"""Same-origin FastAPI API and zero-build static UI for the local workspace."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from python_multipart.exceptions import MultipartParseError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from .. import __version__
from ..errors import ConfigError, ThemeShiftError, UsageError
from ..models import ThemeMeta
from ..service import MetadataOverrides, MigrationRequest, MigrationService
from .jobs import JobError, JobManager
from .settings import WebSettings

logger = logging.getLogger("themeshift.web")
STATIC_DIR = Path(__file__).parent / "static"


def _error(status: int, code: str, message: str) -> JSONResponse:
    headers = {"Retry-After": "5"} if status == 429 else None
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def _origin(value: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(value)
    return parsed.scheme.lower(), parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


class LocalRequestMiddleware:
    """Host/CSRF checks and a byte counter run before multipart spooling."""

    def __init__(self, app, settings: WebSettings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value.decode("latin-1") for key, value in scope["headers"]}
        host = headers.get(b"host", "")

        async def secure_send(message):
            if message["type"] == "http.response.start":
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"cache-control", b"no-store"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                ]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        try:
            hostname = urlsplit(f"http://{host}").hostname
        except ValueError:
            hostname = None
        if hostname not in self.settings.allowed_hosts or sum(key.lower() == b"host" for key, _ in scope["headers"]) != 1:
            await _error(400, "invalid_host", "此 Host 未获允许，请使用本机地址或配置 --allow-host。")(scope, receive, secure_send)
            return
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = headers.get(b"origin")
            try:
                same_origin = not origin or _origin(origin) == _origin(f"{scope['scheme']}://{host}")
            except ValueError:
                same_origin = False
            if not same_origin or headers.get(b"sec-fetch-site") == "cross-site" or headers.get(b"x-themeshift-request") != "1":
                await _error(403, "origin_rejected", "仅接受同源请求，并需要 X-ThemeShift-Request: 1 请求头。")(scope, receive, secure_send)
                return

        budget = self.settings.max_upload_bytes + 64 * 1024
        if scope["method"] == "POST":
            try:
                declared = int(headers.get(b"content-length", "0"))
                if declared < 0:
                    raise ValueError
            except ValueError:
                await _error(400, "invalid_length", "无效的 Content-Length。")(scope, receive, secure_send)
                return
            if declared > budget:
                await _error(413, "upload_too_large", "上传请求超过大小限制。")(scope, receive, secure_send)
                return
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > budget:
                    raise HTTPException(413, "上传请求超过大小限制。")
            return message

        await self.app(scope, bounded_receive, secure_send)


class ClosingMultiPartParser(MultiPartParser):
    """Close owned spools even when a receive error/cancellation interrupts parse.

    Starlette's normal error path only covers MultiPartException. These internals
    are covered by dedicated regression tests against our declared dependency range.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._ended = False
        self._header_bytes = 0

    def on_part_begin(self) -> None:
        self._header_bytes = 0
        super().on_part_begin()

    def _header_budget(self, size: int) -> None:
        self._header_bytes += size
        if self._header_bytes > 16 * 1024:
            raise MultiPartException("单个 multipart 部分的头部超过 16 KiB。")

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_budget(end - start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_budget(end - start)
        super().on_header_value(data, start, end)

    def on_header_end(self) -> None:
        if len(self._current_part.item_headers) >= 32:
            raise MultiPartException("单个 multipart 部分的头部数量过多。")
        super().on_header_end()

    def on_end(self) -> None:
        self._ended = True
        super().on_end()

    async def parse(self):
        try:
            form = await super().parse()
            if not self._ended:
                raise MultiPartException("上传请求不完整，缺少结束边界。")
            return form
        except BaseException:
            for file in self._files_to_close_on_error:
                file.close()
            raise


class JobOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(default="auto", pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    target: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(default="", max_length=200)
    name_en: str = Field(default="", max_length=200)
    author: str = Field(default="", max_length=200)
    version_number: str = Field(default="", max_length=200)
    brief: str = Field(default="", max_length=2000)
    brief_en: str = Field(default="", max_length=2000)
    dry_run: bool = False
    icon_margin: int | None = Field(default=None, ge=0, le=4096)

    def metadata(self) -> MetadataOverrides:
        return MetadataOverrides(
            title=self.name, title_en=self.name_en, designer=self.author,
            version=self.version_number, brief_info=self.brief, brief_info_en=self.brief_en,
        )


def _save_upload(file: UploadFile, destination: Path, maximum: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with destination.open("xb") as output:
        while chunk := file.file.read(1024 * 1024):
            count += len(chunk)
            if count > maximum:
                raise JobError(413, "upload_too_large", "主题文件超过上传大小限制。")
            output.write(chunk)
    if not count:
        raise JobError(400, "empty_file", "请选择非空的主题文件。")


class ArtifactResponse(FileResponse):
    def __init__(self, path: Path, manager: JobManager, job_id: str, kind: str) -> None:
        super().__init__(
            path, filename=path.name,
            media_type="text/plain; charset=utf-8" if kind == "report" else "application/octet-stream",
            content_disposition_type="attachment",
        )
        self.manager = manager
        self.job_id = job_id

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.manager.release_artifact(self.job_id)


def create_app(
    settings: WebSettings | None = None, service: MigrationService | None = None
) -> FastAPI:
    settings = settings or WebSettings()
    service = service or MigrationService()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.catalog = service.catalog()
        manager = JobManager(settings, service)
        app.state.jobs = manager
        try:
            yield
        finally:
            await run_in_threadpool(manager.close)

    app = FastAPI(
        title="ThemeShift API", version=__version__, lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url="/api/openapi.json",
        description="Local, single-process theme conversion. Mutations require X-ThemeShift-Request: 1.",
    )
    app.add_middleware(LocalRequestMiddleware, settings=settings)

    @app.exception_handler(JobError)
    async def job_error(_request: Request, exc: JobError):
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(ThemeShiftError)
    async def domain_error(_request: Request, exc: ThemeShiftError):
        if isinstance(exc, ConfigError):
            logger.error("平台配置错误：%s", exc)
            return _error(500, "configuration", "服务端平台配置错误，请查看服务日志。")
        return _error(400, "invalid_request", str(exc))

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        code = "upload_too_large" if exc.status_code == 413 else "http_error"
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def request_error(_request: Request, _exc: RequestValidationError):
        return _error(422, "invalid_request", "请求参数不符合接口要求。")

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, exc: Exception):
        logger.error("Web 请求失败", exc_info=(type(exc), exc, exc.__traceback__))
        return _error(500, "internal_error", "服务端处理失败，请查看日志。")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/platforms")
    def platforms():
        return {**app.state.catalog, "version": __version__, "limits": settings.public_limits()}

    @app.post("/api/jobs", status_code=202, openapi_extra={
        "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
            "type": "object", "required": ["file", "target"],
            "properties": {
                "file": {"type": "string", "format": "binary"},
                **JobOptions.model_json_schema()["properties"],
            },
        }}}},
    })
    async def submit(request: Request):
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            raise JobError(415, "content_type", "请使用 multipart/form-data 上传主题。")
        manager: JobManager = app.state.jobs
        job_id, directory = await run_in_threadpool(manager.reserve)
        form = None
        submitted = False
        try:
            parser = ClosingMultiPartParser(
                request.headers, request.stream(), max_files=1, max_fields=10, max_part_size=16 * 1024,
            )
            form = await asyncio.wait_for(parser.parse(), timeout=settings.upload_timeout_seconds)
            if any(len(form.getlist(key)) != 1 for key in form):
                raise JobError(422, "duplicate_field", "请求中不能重复提交同名字段。")
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise JobError(422, "missing_file", "请上传一个名为 file 的主题文件。")
            filename = file.filename or ""
            if not filename or len(filename) > 180 or any(char in filename for char in '/\\:') or any(ord(char) < 32 for char in filename):
                raise JobError(400, "invalid_filename", "主题文件名无效，请使用不含路径的普通文件名。")
            try:
                options = JobOptions.model_validate({key: value for key, value in form.items() if key != "file"})
            except ValidationError as exc:
                fields = ", ".join(str(item["loc"][0]) for item in exc.errors())
                raise JobError(422, "invalid_request", f"参数格式或长度无效：{fields}。") from exc
            metadata = options.metadata()
            metadata.apply(ThemeMeta(title=""))
            source = service.detect_source(Path(filename), options.source)
            extensions = service.repository.load_profile(source)["input_extensions"]
            if Path(filename).suffix.lower() not in {ext.lower() for ext in extensions}:
                raise UsageError("文件扩展名与所选源平台不匹配。")
            input_path = directory / "input" / f"theme{Path(filename).suffix.lower()}"
            migration = MigrationRequest(
                input_path=input_path, source=source, target=options.target,
                metadata=metadata, dry_run=options.dry_run,
                icon_margin=options.icon_margin,
            )
            await run_in_threadpool(service.plan, migration)
            await run_in_threadpool(_save_upload, file, input_path, settings.max_upload_bytes)
            result = manager.submit(job_id, filename, migration)
            submitted = True
            return result
        except asyncio.TimeoutError as exc:
            raise JobError(408, "upload_timeout", "上传超时，请重试或减小文件大小。") from exc
        except MultiPartException as exc:
            raise JobError(400, "invalid_multipart", exc.message) from exc
        except MultipartParseError as exc:
            raise JobError(400, "invalid_multipart", "上传请求的 multipart 格式无效。") from exc
        except ClientDisconnect as exc:
            raise JobError(400, "client_disconnected", "上传连接已断开。") from exc
        finally:
            if not submitted:
                await run_in_threadpool(manager.abandon, job_id)
            if form is not None:
                await form.close()

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        return app.state.jobs.snapshot(job_id)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str):
        app.state.jobs.delete(job_id)
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}/artifacts/{kind}")
    def artifact(job_id: str, kind: str):
        manager: JobManager = app.state.jobs
        path = manager.acquire_artifact(job_id, kind)
        return ArtifactResponse(path, manager, job_id, kind)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
