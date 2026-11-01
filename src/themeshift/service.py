"""Transport-independent application service shared by CLI, Web and embedders.

Adapters read/write the neutral IR; module transformers translate it. This layer
owns direction/capability negotiation, isolation and verified artifact publishing.
It never imports FastAPI, argparse or a concrete vendor implementation.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from . import registry
from .errors import BuildValidationError, ConfigError, SourceFormatError, UsageError
from .mapping import PackageMap, apply_package_map
from .models import BuildReport, MappingStats, NormalizedTheme, ThemeMeta
from .naming import slugify
from .platforms.base import ModuleTransformer
from .profiles import (
    ICON_MODULES,
    adapter_config,
    adapter_modules,
    icon_margin_bounds,
    require_id,
)
from .repository import DataRepository

logger = logging.getLogger("themeshift.service")
ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True)
class MetadataOverrides:
    title: str | None = None
    title_en: str | None = None
    designer: str | None = None
    version: str | None = None
    brief_info: str | None = None
    brief_info_en: str | None = None

    def apply(self, meta: ThemeMeta) -> ThemeMeta:
        values = {item.name: getattr(self, item.name) for item in fields(self)}
        result = replace(meta, **{key: value for key, value in values.items() if value})
        for key, value in asdict(result).items():
            maximum = 2000 if key.startswith("brief") else 200
            if not isinstance(value, str) or len(value) > maximum:
                raise UsageError(f"元数据 {key} 必须是不超过 {maximum} 个字符的文本。")
            if any(
                not (char in "\t\n\r" or "\x20" <= char <= "\ud7ff" or "\ue000" <= char <= "\ufffd" or "\U00010000" <= char <= "\U0010ffff")
                for char in value
            ):
                raise UsageError(f"元数据 {key} 含有 XML 不允许的控制字符。")
        return result


@dataclass(frozen=True)
class MigrationRequest:
    input_path: Path
    target: str = "honor"
    source: str | None = None
    output_path: Path | None = None
    output_dir: Path | None = None
    work_dir: Path | None = None
    keep_work: bool = False
    dry_run: bool = False
    metadata: MetadataOverrides = field(default_factory=MetadataOverrides)
    target_packages: frozenset[str] | None = None
    modules: tuple[str, ...] | None = None
    # Optional rendering option for icon targets; None keeps the profile default.
    icon_margin: int | None = None


@dataclass(frozen=True)
class MigrationPlan:
    source: str
    target: str
    modules: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass
class MigrationResult:
    plan: MigrationPlan
    meta: ThemeMeta
    stats: MappingStats
    static_icons: int
    dynamic_assets: int
    warnings: list[str]
    output_path: Path | None = None
    report_path: Path | None = None
    validation: BuildReport | None = None
    work_dir: Path | None = None
    icon_margin: int | None = None


@dataclass(frozen=True)
class InspectionResult:
    source: str
    meta: ThemeMeta
    static_icons: int
    dynamic_assets: int
    packages: tuple[str, ...]


class MigrationService:
    def __init__(
        self,
        repository: DataRepository | None = None,
        *,
        transformers: Mapping[str, ModuleTransformer] | None = None,
    ) -> None:
        self.repository = repository or DataRepository.create()
        self.transformers = dict(transformers or {})
        for name in self.transformers:
            require_id(name)
            if name in ICON_MODULES:
                raise ConfigError(f"内置模块 {name} 不能由外部转换器覆盖。")
        self.supported_modules = frozenset(ICON_MODULES) | self.transformers.keys()

    def detect_source(self, path: Path, requested: str | None = None) -> str:
        sources = self.repository.available("source")
        if requested and requested != "auto":
            if requested not in sources:
                raise UsageError(f"平台 {requested!r} 不支持读取主题。")
            return requested
        matches = [
            name for name in sources
            if path.suffix.lower() in {
                ext.lower() for ext in self.repository.load_profile(name)["input_extensions"]
            }
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise UsageError("多个读取器支持此扩展名，请明确指定源平台。")
        raise UsageError(f"无法识别扩展名 {path.suffix!r}，请指定已支持的源平台。")

    def plan(self, request: MigrationRequest) -> MigrationPlan:
        source = self.detect_source(request.input_path, request.source)
        if request.target not in self.repository.available("target"):
            raise UsageError(f"平台 {request.target!r} 不支持写出主题。")
        source_profile = self.repository.load_profile(source)
        target_profile = self.repository.load_profile(request.target)
        self._icon_margin(target_profile, request.target, request.icon_margin)
        readable = set(adapter_modules(source_profile, "source"))
        writable = set(adapter_modules(target_profile, "target"))
        common = readable & writable & self.supported_modules
        modules = tuple(sorted(common)) if request.modules is None else request.modules
        if not modules or len(modules) != len(set(modules)):
            raise UsageError("至少需要一个可转换模块，且模块不能重复。")
        unsupported = set(modules) - common
        if unsupported:
            raise UsageError(f"此方向尚不支持模块：{', '.join(sorted(unsupported))}。")
        # Explicit direction only: many-to-one app and dynamic-role maps are lossy.
        self.repository.mapping_path(source, request.target)
        warnings = ["仅转换已协商的模块；主题内其他内容不会自动复制。"]
        omitted = readable - set(modules)
        if omitted:
            warnings.append(f"未迁移的源模块：{', '.join(sorted(omitted))}。")
        return MigrationPlan(source, request.target, modules, tuple(warnings))

    def _icon_margin(self, profile: dict, target: str, margin: int | None) -> int | None:
        """Validate an optional icon margin against this target's real limits."""

        if margin is None:
            return None
        bounds = icon_margin_bounds(profile)
        if bounds is None or not registry.accepts_options(registry.create_target(profile)):
            raise UsageError(f"目标平台 {target!r} 不支持调整图标边距。")
        low, high, _default = bounds
        if isinstance(margin, bool) or not isinstance(margin, int) or not low <= margin <= high:
            raise UsageError(f"图标边距必须是 {low}–{high} 之间的整数像素。")
        return margin

    def catalog(self) -> dict:
        platforms = []
        profiles = {}
        for name in self.repository.profile_names():
            profile = self.repository.load_profile(name)
            profiles[name] = profile
            read = adapter_config(profile, "source") is not None
            write = adapter_config(profile, "target") is not None
            # Fail early for broken server-side plugin installations.
            if read:
                registry.create_source(profile)
            writer = registry.create_target(profile) if write else None
            margin = None
            bounds = icon_margin_bounds(profile) if writer else None
            if bounds and registry.accepts_options(writer):
                margin = {
                    "min": bounds[0], "max": bounds[1], "default": bounds[2],
                    "canvas": int(profile["icon"]["canvas"]), "unit": "px",
                }
            platforms.append({
                "id": name, "display_name": profile["display_name"],
                "can_read": read, "can_write": write,
                "input_extensions": profile.get("input_extensions", []),
                "output_extension": profile.get("output_extension"),
                "read_modules": list(adapter_modules(profile, "source")),
                "write_modules": list(adapter_modules(profile, "target")),
                "icon_margin": margin,
            })
        routes = []
        for source in self.repository.available("source"):
            for target in self.repository.available("target"):
                if f"{source}_to_{target}" not in self.repository.mapping_names():
                    continue
                path = self.repository.mapping_path(source, target)
                PackageMap.load(path)
                modules = set(adapter_modules(profiles[source], "source")) & set(
                    adapter_modules(profiles[target], "target")
                ) & self.supported_modules
                if modules:
                    routes.append({"source": source, "target": target, "modules": sorted(modules)})
        return {"platforms": platforms, "routes": routes}

    def inspect(self, path: Path, source: str | None = None) -> InspectionResult:
        if not path.is_file():
            raise UsageError(f"找不到源主题文件：{path}")
        name = self.detect_source(path, source)
        reader = registry.create_source(self.repository.load_profile(name))
        with tempfile.TemporaryDirectory(prefix="themeshift-inspect-") as temporary:
            theme = reader.load(path, Path(temporary))
            if theme.schema_version != 1:
                raise SourceFormatError("读取器返回了不支持的中间模型版本。")
            return InspectionResult(
                name, theme.meta, len(theme.static_icons), len(theme.dynamic_assets),
                tuple(sorted(icon.package for icon in theme.static_icons)),
            )

    def migrate(
        self, request: MigrationRequest, progress: ProgressCallback | None = None
    ) -> MigrationResult:
        if not request.input_path.is_file():
            raise UsageError(f"找不到源主题文件：{request.input_path}")
        plan = self.plan(request)
        source_profile = self.repository.load_profile(plan.source)
        target_profile = self.repository.load_profile(plan.target)
        reader = registry.create_source(source_profile)
        writer = registry.create_target(target_profile)
        mapping = PackageMap.load(self.repository.mapping_path(plan.source, plan.target))

        def emit(percent: int, message: str) -> None:
            logger.info("[%d%%] %s", percent, message)
            if progress:
                progress(percent, message)

        # Even explicit --workdir is a parent, never a directory we clear wholesale.
        if request.work_dir:
            request.work_dir.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="themeshift-", dir=request.work_dir))
        preserve = request.keep_work
        try:
            emit(10, f"读取源主题 ({plan.source})")
            theme = reader.load(request.input_path, work / "source")
            if theme.schema_version != 1:
                raise SourceFormatError("读取器返回了不支持的中间模型版本。")
            theme.meta = request.metadata.apply(theme.meta)
            emit(35, "转换应用包名与模块资源")
            normalized = apply_package_map(
                theme, mapping, request.target_packages, modules=frozenset(plan.modules)
            )
            for module in plan.modules:
                if module in self.transformers:
                    self.transformers[module].transform(theme, normalized, mapping)
            warnings = [*plan.warnings, *normalized.warnings]
            if len(theme.dynamic_assets) > normalized.stats.dynamic:
                warnings.append("部分动态图标未匹配到目标角色，未复制；动态行为不保证跨厂商等价。")
            if not normalized.static_icons and not normalized.dynamic_assets and not any(normalized.modules.values()):
                raise UsageError("没有可输出的资源，请检查主题内容、映射与所选模块。")
            result = MigrationResult(
                plan, theme.meta, normalized.stats, len(normalized.static_icons),
                len(normalized.dynamic_assets), warnings, work_dir=work if preserve else None,
                icon_margin=request.icon_margin,
            )
            if request.dry_run:
                warnings.append("分析模式不构建目标包，尚未执行目标图像归一化与输出自检。")
                emit(100, "分析完成（未生成主题包）")
                return result

            filename = f"{slugify(theme.meta.title)[:80]}{target_profile.get('output_suffix', '')}{target_profile['output_extension']}"
            output = request.output_path or ((request.output_dir or (Path.cwd() / "outputs")) / filename)
            report_path = output.with_name(f"{output.stem}{target_profile.get('report_suffix', '_package_map')}.txt")
            if output.resolve() == report_path.resolve():
                raise UsageError("目标主题与映射报告路径冲突，请检查 output_extension/report_suffix。")
            if request.input_path.resolve() in {output.resolve(), report_path.resolve()}:
                raise UsageError("输出路径不能覆盖输入主题。")
            emit(55, f"生成目标主题 ({plan.target})")
            staged_output = work / f"result{target_profile['output_extension']}"
            options = {"icon_margin": request.icon_margin} if request.icon_margin is not None else None
            validation = self._build(writer, normalized, staged_output, work, options)
            emit(90, "验证目标主题")
            if not validation.ok or not staged_output.is_file():
                raise BuildValidationError("目标主题自检失败，未发布输出。" + "；".join(validation.summary_lines()))
            staged_report = work / "mapping.txt"
            staged_report.write_bytes(("\n".join([*normalized.report, *warnings]) + "\n").encode("utf-8"))
            output.parent.mkdir(parents=True, exist_ok=True)
            # A sibling temporary file keeps os.replace atomic across filesystems.
            self._publish(staged_output, output)
            self._publish(staged_report, report_path)
            validation.output = output
            result.output_path = output
            result.report_path = report_path
            result.validation = validation
            emit(100, "转换完成")
            return result
        finally:
            if not preserve:
                shutil.rmtree(work)
            else:
                logger.info("保留中间目录：%s", work)

    @staticmethod
    def _build(
        writer,
        theme: NormalizedTheme,
        output: Path,
        work: Path,
        options: dict[str, object] | None,
    ) -> BuildReport:
        if options is None:
            return writer.build(theme, output, work)
        return writer.build(theme, output, work, options=options)

    @staticmethod
    def _publish(source: Path, destination: Path) -> None:
        handle, name = tempfile.mkstemp(prefix=".themeshift-", suffix=".tmp", dir=destination.parent)
        temporary = Path(name)
        try:
            with os.fdopen(handle, "wb") as target, source.open("rb") as incoming:
                shutil.copyfileobj(incoming, target, length=1024 * 1024)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
