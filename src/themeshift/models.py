"""Platform-neutral data structures shared by the whole pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ThemeMeta:
    """Descriptive metadata for a theme."""

    title: str
    designer: str = ""
    version: str = ""
    title_en: str = ""
    brief_info: str = ""
    brief_info_en: str = ""


@dataclass(frozen=True)
class StaticIcon:
    """A launcher icon addressed by its application package name."""

    package: str
    path: Path


@dataclass(frozen=True)
class DynamicAsset:
    """A single file belonging to a dynamic (multi-state) icon group."""

    package: str
    filename: str
    path: Path


@dataclass(frozen=True)
class ThemeResource:
    """A neutral non-icon asset; vendor-specific paths stay inside adapters.

    Module transformers must interpret ``key`` and ``attributes`` explicitly.
    Unknown modules are never copied into a target package automatically.
    """

    key: str
    path: Path
    media_type: str = "application/octet-stream"
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class SourceTheme:
    """Versioned neutral IR read from a source platform, before translation."""

    meta: ThemeMeta
    static_icons: list[StaticIcon] = field(default_factory=list)
    dynamic_assets: list[DynamicAsset] = field(default_factory=list)
    source_name: str = ""
    source_path: Path | None = None
    schema_version: int = 1
    modules: dict[str, list[ThemeResource]] = field(default_factory=dict)


@dataclass
class MappingStats:
    """Counters describing how source packages were translated."""

    mapped: int = 0
    skipped: int = 0
    passthrough: int = 0
    dynamic: int = 0


@dataclass
class NormalizedTheme:
    """A theme whose package names already match the target platform."""

    meta: ThemeMeta
    static_icons: dict[str, Path] = field(default_factory=dict)
    dynamic_assets: list[tuple[str, str, Path]] = field(default_factory=list)
    report: list[str] = field(default_factory=list)
    stats: MappingStats = field(default_factory=MappingStats)
    schema_version: int = 1
    modules: dict[str, list[ThemeResource]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def note(self, line: str) -> None:
        self.report.append(line)


@dataclass
class BuildReport:
    """Outcome of writing a target theme, including self-verification."""

    output: Path
    static_icons: int = 0
    dynamic_assets: int = 0
    canvas_size: int = 0
    wrong_canvas: int = 0
    preview_sizes: dict[str, tuple[int, int]] = field(default_factory=dict)
    expected_previews: dict[str, tuple[int, int]] = field(default_factory=dict)
    description_lf: bool = False
    description_bom: bool = False
    extra_content_flag: str | None = None
    content_size: int = 0
    icons_outside_content: int = 0
    entries_without_dos_attributes: int = 0
    # New adapters supply named checks; the older HONOR fields remain compatible.
    checks: dict[str, bool] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    @property
    def preview_ok(self) -> bool:
        return self.preview_sizes == self.expected_previews

    @property
    def ok(self) -> bool:
        if self.issues:
            return False
        if self.checks:
            return all(value is True for value in self.checks.values())
        return (
            self.wrong_canvas == 0
            and self.icons_outside_content == 0
            and self.entries_without_dos_attributes == 0
            and self.preview_ok
            and self.description_lf
            and not self.description_bom
            and self.extra_content_flag is not None
        )

    def summary_lines(self) -> list[str]:
        if self.checks:
            return [f"{name}: {'通过' if passed else '失败'}" for name, passed in self.checks.items()] + self.issues
        return [
            f"静态图标={self.static_icons} 动态资源={self.dynamic_assets} "
            f"画布异常={self.wrong_canvas} 内容区={self.content_size}px 越界={self.icons_outside_content}",
            f"预览图匹配={self.preview_ok} 描述LF换行={self.description_lf} "
            f"无BOM={not self.description_bom} extraContentFlag={self.extra_content_flag}",
            f"DOS属性异常={self.entries_without_dos_attributes}",
        ]
