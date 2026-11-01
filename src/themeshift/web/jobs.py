"""Bounded in-process jobs, isolated files and expiring download capabilities.

This is deliberately a single-process local queue, not a durable distributed
scheduler. Job ids are unguessable download capabilities; there is no global
listing endpoint. Replace this transport-side manager if durable workers are
needed later; MigrationService does not depend on it.
"""

from __future__ import annotations

import copy
import logging
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from ..errors import BuildValidationError, ConfigError, SourceFormatError, ThemeShiftError
from ..service import MigrationRequest, MigrationResult, MigrationService
from .settings import WebSettings

logger = logging.getLogger("themeshift.web.jobs")
_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
_TERMINAL = {"succeeded", "failed"}


class JobError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _iso(timestamp: float | None) -> str | None:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None


@dataclass
class _Job:
    id: str
    directory: Path
    created_at: float
    status: str = "uploading"
    progress: int = 0
    stage: str = "等待上传"
    filename: str = ""
    source: str = ""
    target: str = ""
    dry_run: bool = False
    expires_at: float | None = None
    result: dict | None = None
    error: dict | None = None
    downloads: int = 0
    artifacts: dict[str, Path] | None = None

    def snapshot(self) -> dict:
        return {
            "id": self.id, "status": self.status, "progress": self.progress,
            "stage": self.stage, "filename": self.filename, "source": self.source,
            "target": self.target, "dry_run": self.dry_run,
            "created_at": _iso(self.created_at), "expires_at": _iso(self.expires_at),
            "result": copy.deepcopy(self.result), "error": copy.deepcopy(self.error),
        }


class JobManager:
    def __init__(self, settings: WebSettings, service: MigrationService) -> None:
        self.settings = settings
        self.service = service
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(prefix="session-", dir=settings.storage_dir)
        self.root = Path(self._temporary.name).resolve()
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()
        self._downloads_finished = threading.Condition(self._lock)
        self._inflight = 0
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="themeshift")
        self._stop = threading.Event()
        self._janitor = threading.Thread(target=self._reap, name="themeshift-expiry", daemon=True)
        self._janitor.start()

    def _reap(self) -> None:
        while not self._stop.wait(min(30, self.settings.retention_seconds)):
            try:
                self.cleanup()
            except OSError:
                logger.exception("清理过期任务失败，将于下一轮重试")

    def _directory(self, job_id: str) -> Path:
        # All removal paths are derived only from this manager's own root + UUID.
        if not _JOB_ID.fullmatch(job_id):
            raise JobError(404, "not_found", "任务不存在或已过期。")
        path = self.root / job_id
        if path.resolve().parent != self.root:
            raise JobError(404, "not_found", "任务不存在或已过期。")
        return path

    def _remove(self, job_id: str) -> None:
        path = self._directory(job_id)
        if path.is_dir():
            shutil.rmtree(path)
        self._jobs.pop(job_id, None)

    def cleanup(self, now: float | None = None) -> None:
        with self._lock:
            current = time.time() if now is None else now
            expired = [
                job.id for job in self._jobs.values()
                if job.status in _TERMINAL and not job.downloads
                and job.expires_at is not None and job.expires_at <= current
            ]
            for job_id in expired:
                try:
                    self._remove(job_id)
                except OSError:
                    logger.exception("过期任务 %s 清理失败，将再次尝试", job_id)

    def reserve(self) -> tuple[str, Path]:
        """Reserve BEFORE parsing multipart, bounding simultaneous upload storage."""
        with self._lock:
            self.cleanup()
            if self._closed:
                raise JobError(503, "shutting_down", "服务正在关闭，请稍后重试。")
            if self._inflight >= self.settings.max_pending or len(self._jobs) >= self.settings.max_jobs:
                raise JobError(429, "capacity", "任务容量已满，请等待运行任务结束，或清理已完成的任务。")
            job_id = uuid.uuid4().hex
            directory = self._directory(job_id)
            directory.mkdir()
            self._jobs[job_id] = _Job(job_id, directory, time.time())
            self._inflight += 1
            return job_id, directory

    def abandon(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job.status == "uploading":
                job.status = "failed"
                job.stage = "上传未完成"
                job.error = {"code": "upload_abandoned", "message": "上传未完成，任务已取消。"}
                job.expires_at = time.time()
                self._inflight -= 1
                try:
                    self._remove(job_id)
                except OSError:
                    logger.exception("未完成上传 %s 清理失败，保留为过期任务以便重试", job_id)

    def submit(self, job_id: str, filename: str, request: MigrationRequest) -> dict:
        with self._lock:
            if self._closed:
                raise JobError(503, "shutting_down", "服务正在关闭。")
            job = self._jobs[job_id]
            job.filename = filename
            job.source = request.source or "auto"
            job.target = request.target
            job.dry_run = request.dry_run
            job.stage = "等待转换线程"
            job.status = "queued"
            request = replace(
                request, output_path=None, output_dir=job.directory / "artifacts",
                work_dir=job.directory / "work", keep_work=False,
            )
            try:
                self._executor.submit(self._run, job_id, request)
            except Exception:
                job.status = "uploading"
                raise
            return job.snapshot()

    def _run(self, job_id: str, request: MigrationRequest) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.stage = "准备读取主题"

        def progress(value: int, stage: str) -> None:
            with self._lock:
                job.progress = value
                job.stage = stage

        payload = None
        artifacts: dict[str, Path] = {}
        error = None
        try:
            result = self.service.migrate(request, progress)
            payload, artifacts = self._result(job_id, result)
        except ThemeShiftError as exc:
            if isinstance(exc, ConfigError):
                logger.exception("任务 %s 平台配置错误", job_id)
                error = {"code": "configuration", "message": "服务端平台配置错误，请查看服务日志。"}
            else:
                code = "source_format" if isinstance(exc, SourceFormatError) else (
                    "validation_failed" if isinstance(exc, BuildValidationError) else "invalid_request"
                )
                message = str(exc)
                roots = {str(self.root), self.root.as_posix(), str(self.root).replace("\\", "\\\\")}
                for root in sorted(roots, key=len, reverse=True):
                    message = message.replace(root, "<任务目录>")
                error = {"code": code, "message": message[:1500]}
        except Exception:
            logger.exception("任务 %s 转换失败", job_id)
            error = {"code": "internal_error", "message": "转换失败，请检查主题格式或查看服务端日志。"}
        finally:
            # The raw upload and scratch space are never download endpoints.
            for name in ("input", "work"):
                path = self._directory(job_id) / name
                if path.is_dir():
                    try:
                        shutil.rmtree(path)
                    except OSError:
                        logger.exception("任务 %s 中间文件清理失败", job_id)
            if error is not None:
                path = self._directory(job_id) / "artifacts"
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
            with self._lock:
                job.result = payload
                job.artifacts = artifacts
                job.error = error
                job.status = "failed" if error else "succeeded"
                job.stage = "转换失败" if error else ("分析完成（未生成主题包）" if request.dry_run else "转换完成")
                if not error:
                    job.progress = 100
                job.expires_at = time.time() + self.settings.retention_seconds
                self._inflight -= 1

    @staticmethod
    def _result(job_id: str, result: MigrationResult) -> tuple[dict, dict[str, Path]]:
        paths = {}
        artifacts = {}
        for kind, path in (("theme", result.output_path), ("report", result.report_path)):
            if path is not None:
                paths[kind] = path
                artifacts[kind] = {
                    "filename": path.name, "size": path.stat().st_size,
                    "url": f"/api/jobs/{job_id}/artifacts/{kind}",
                }
        return {
            "meta": asdict(result.meta), "stats": asdict(result.stats),
            "static_icons": result.static_icons, "dynamic_assets": result.dynamic_assets,
            "warnings": result.warnings, "modules": list(result.plan.modules),
            "icon_margin": result.icon_margin,
            "validation": {"ok": result.validation.ok, "summary": result.validation.summary_lines()} if result.validation else None,
            "artifacts": artifacts,
        }, paths

    def _get(self, job_id: str) -> _Job:
        self._directory(job_id)
        self.cleanup()
        job = self._jobs.get(job_id)
        if job is None or job.status == "uploading":
            raise JobError(404, "not_found", "任务不存在或已过期。")
        return job

    def snapshot(self, job_id: str) -> dict:
        with self._lock:
            return self._get(job_id).snapshot()

    def delete(self, job_id: str) -> None:
        with self._lock:
            job = self._get(job_id)
            if job.status not in _TERMINAL or job.downloads:
                raise JobError(409, "job_busy", "运行中或正在下载的任务不能清理。")
            self._remove(job_id)

    def acquire_artifact(self, job_id: str, kind: str) -> Path:
        with self._lock:
            if self._closed:
                raise JobError(503, "shutting_down", "服务正在关闭。")
            job = self._get(job_id)
            path = (job.artifacts or {}).get(kind)
            if job.status != "succeeded" or path is None or not path.is_file():
                raise JobError(404, "artifact_not_found", "此任务尚无可下载产物。")
            if not path.resolve().is_relative_to(job.directory):
                raise JobError(404, "artifact_not_found", "产物不存在。")
            job.downloads += 1
            return path

    def release_artifact(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.downloads = max(0, job.downloads - 1)
                self._downloads_finished.notify_all()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stop.set()
        self._janitor.join()
        # Running conversions cannot be force-killed safely in Python threads.
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._downloads_finished:
            self._downloads_finished.wait_for(lambda: not any(job.downloads for job in self._jobs.values()))
        self._temporary.cleanup()
        with self._lock:
            self._jobs.clear()
            self._inflight = 0
