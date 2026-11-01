"""Command-line transport; all migration business logic lives in service.py."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .console import configure
from .errors import ThemeShiftError, UsageError
from .packages import load_package_filter
from .repository import DataRepository
from .service import MetadataOverrides, MigrationRequest, MigrationService

logger = logging.getLogger("themeshift.cli")
COMMANDS = ("migrate", "platforms", "inspect", "serve")


def _normalize_argv(argv: list[str]) -> list[str]:
    if not argv or argv[0] in COMMANDS or argv[0].startswith("-"):
        return argv
    return ["migrate", *argv]


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", type=Path, help="平台数据目录，也可用 THEMESHIFT_DATA")
    parser.add_argument("-q", "--quiet", action="store_true", help="只输出警告和错误")
    parser.add_argument("--debug", action="store_true", help="输出调试信息")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="themeshift", description="跨品牌主题移植工具与本地 Web 工作台（当前支持小米 → 荣耀图标）。",
        epilog="简写：themeshift <源文件> 等价于 themeshift migrate <源文件>。",
    )
    parser.add_argument("--version", action="version", version=f"themeshift {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    migrate = subparsers.add_parser("migrate", help="执行一次主题移植")
    migrate.add_argument("input", type=Path, help="源主题文件，例如 超拟圆.mtz")
    migrate.add_argument("-t", "--to", default="honor", help="目标平台（默认 honor）")
    migrate.add_argument("-f", "--from", dest="source", help="源平台（默认按扩展名识别）")
    migrate.add_argument("--name", help="覆盖主题名称")
    migrate.add_argument("--name-en", help="覆盖英文主题名称")
    migrate.add_argument("--author", help="覆盖设计师名称")
    migrate.add_argument("--version-number", dest="theme_version", help="覆盖版本号")
    migrate.add_argument("--brief", help="覆盖中文简介")
    migrate.add_argument("--brief-en", help="覆盖英文简介")
    migrate.add_argument("--packages", type=Path, help="限定系统应用目标包名（.xlsx 或 .json）")
    migrate.add_argument("--modules", nargs="+", help="要移植的模块，默认协商全部共同能力")
    migrate.add_argument(
        "--icon-margin", type=int, metavar="PX",
        help="调整图标内容区边距（画布内缩像素），范围由目标平台决定",
    )
    migrate.add_argument("-o", "--out", type=Path, help="输出文件路径")
    migrate.add_argument("--workdir", type=Path, help="中间目录的父目录（内部建立独立运行目录并保留）")
    migrate.add_argument("--keep-work", action="store_true", help="保留自动创建的中间目录")
    migrate.add_argument("--dry-run", action="store_true", help="只分析与统计，不生成主题包")
    _add_common(migrate)
    platforms = subparsers.add_parser("platforms", help="列出平台能力与实际可用方向")
    _add_common(platforms)
    inspect = subparsers.add_parser("inspect", help="读取源主题信息")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("-f", "--from", dest="source")
    _add_common(inspect)
    serve = subparsers.add_parser("serve", help="启动本地 Web 工作台（需安装 .[web]）")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--storage-dir", type=Path, default=Path(".themeshift-web"))
    serve.add_argument("--job-workers", type=int, default=2, help="转换工作线程数，不是HTTP进程数")
    serve.add_argument("--max-upload-mb", type=int, default=128)
    serve.add_argument("--max-jobs", type=int, default=32, help="含已完成结果的任务容量")
    serve.add_argument("--max-pending", type=int, default=8, help="含上传/排队/运行的任务容量")
    serve.add_argument("--retention-minutes", type=int, default=60, help="完成后保留结果的分钟数")
    serve.add_argument("--allow-host", action="append", default=[], help="允许的额外Host，不含端口")
    _add_common(serve)
    return parser


def _service(args: argparse.Namespace) -> MigrationService:
    return MigrationService(DataRepository.create(args.data_dir))


def _cmd_platforms(args: argparse.Namespace) -> int:
    service = _service(args)
    catalog = service.catalog()
    for role, label in (("can_read", "源平台"), ("can_write", "目标平台")):
        print(f"{label}：")
        for platform in catalog["platforms"]:
            if platform[role]:
                print(f"  {platform['id']:<10} {platform['display_name']}")
    print("可用移植方向（不会自动反转映射）：")
    for route in catalog["routes"]:
        print(f"  {route['source']} -> {route['target']} [{', '.join(route['modules'])}]")
    print(f"数据目录：{service.repository.root}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    service = _service(args)
    theme = service.inspect(args.input, args.source)
    print(f"源平台：{theme.source}")
    print(f"文件：{args.input}")
    print(f"标题：{theme.meta.title or '（未提供）'}")
    print(f"设计师：{theme.meta.designer or '（未提供）'}")
    print(f"静态图标：{theme.static_icons}")
    print(f"动态资源：{theme.dynamic_assets}")
    for package in theme.packages[:10]:
        print(f"  - {package}")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    result = _service(args).migrate(MigrationRequest(
        input_path=args.input, target=args.to, source=args.source,
        output_path=args.out, work_dir=args.workdir,
        keep_work=args.keep_work or args.workdir is not None, dry_run=args.dry_run,
        metadata=MetadataOverrides(
            title=args.name, title_en=args.name_en, designer=args.author,
            version=args.theme_version, brief_info=args.brief, brief_info_en=args.brief_en,
        ),
        target_packages=load_package_filter(args.packages) if args.packages else None,
        modules=tuple(args.modules) if args.modules is not None else None,
        icon_margin=args.icon_margin,
    ))
    stats = result.stats
    logger.info("系统映射=%d 第三方直通=%d 缺失目标=%d 动态=%d", stats.mapped, stats.passthrough, stats.skipped, stats.dynamic)
    for warning in result.warnings:
        logger.info("提示：%s", warning)
    if result.validation:
        for line in result.validation.summary_lines():
            logger.info("%s", line)
        logger.info("输出：%s", result.output_path)
        logger.info("映射：%s", result.report_path)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from .web.app import create_app
        from .web.settings import WebSettings
    except ImportError as exc:
        raise UsageError('Web 依赖未安装，请运行 python -m pip install ".[web]"。') from exc
    if not 1 <= args.port <= 65535:
        raise UsageError("端口应在 1 到 65535 之间。")
    settings = WebSettings(
        storage_dir=args.storage_dir, workers=args.job_workers,
        max_upload_bytes=args.max_upload_mb * 1024 * 1024,
        max_jobs=args.max_jobs, max_pending=args.max_pending,
        retention_seconds=args.retention_minutes * 60,
        allowed_hosts=tuple(dict.fromkeys(["127.0.0.1", "localhost", "::1", *args.allow_host])),
    )
    app = create_app(settings=settings, service=_service(args))
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        logger.warning("本服务无内置账户鉴权，仅供可信环境；公网部署必须使用认证反向代理和HTTPS。")
    logger.info("打开 http://%s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level="debug" if args.debug else "info")
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    args = _build_parser().parse_args(_normalize_argv(raw))
    configure("debug" if args.debug else ("quiet" if args.quiet else "info"))
    handler = {"migrate": _cmd_migrate, "platforms": _cmd_platforms, "inspect": _cmd_inspect, "serve": _cmd_serve}[args.command]
    try:
        return handler(args)
    except ThemeShiftError as exc:
        logger.error("错误：%s", exc)
        return exc.exit_code
    except KeyboardInterrupt:
        logger.error("已取消。")
        return 130
    except Exception as exc:  # noqa: BLE001 - user-facing last-resort guard
        if args.debug:
            raise
        logger.error("未预期的错误：%s（加 --debug 查看堆栈）", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
