"""Read Xiaomi ``.mtz`` icon themes."""

from __future__ import annotations

import fnmatch
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from ..errors import SourceFormatError
from ..models import DynamicAsset, SourceTheme, StaticIcon, ThemeMeta
from ..packaging import (
    extract_nested_zip,
    extract_prefix,
    has_entry,
    read_entry_text,
)

logger = logging.getLogger("themeshift.xiaomi")


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class XiaomiSource:
    """Reader for MIUI/Xiaomi ``.mtz`` theme packages."""

    name = "xiaomi"

    def __init__(self, profile: dict) -> None:
        self.profile = profile

    def load(self, package_path: Path, work_dir: Path) -> SourceTheme:
        meta = self._read_meta(package_path)
        icons_dir = work_dir / "source-icons"
        self._extract_icons(package_path, icons_dir)
        return SourceTheme(
            meta=meta,
            static_icons=self._read_static(icons_dir),
            dynamic_assets=self._read_dynamic(icons_dir),
            source_name=self.name,
            source_path=package_path,
        )

    # ------------------------------------------------------------- internals
    def _extract_icons(self, package_path: Path, icons_dir: Path) -> None:
        entry = self.profile["icons_entry"]
        if has_entry(package_path, entry):
            extract_nested_zip(package_path, entry, icons_dir)
            return

        prefix = f"{entry}/"
        logger.debug("%s 没有独立 icons 包，尝试按目录前缀解压", package_path.name)
        extract_prefix(package_path, prefix, icons_dir)

    def _read_meta(self, package_path: Path) -> ThemeMeta:
        entry = self.profile["description_entry"]
        text = read_entry_text(package_path, entry)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise SourceFormatError(
                f"{package_path.name} 的 {entry} 不是合法 XML：{exc}"
            ) from exc

        fields = self.profile["meta_fields"]

        def pick(names: list[str]) -> str:
            for name in names:
                for element in root.iter():
                    if _strip_namespace(element.tag) != name:
                        continue
                    value = (element.text or "").strip()
                    if value:
                        return value
            return ""

        return ThemeMeta(
            title=pick(fields["title"]),
            designer=pick(fields["designer"]),
            version=pick(fields.get("version", [])),
            title_en=pick(fields.get("title_en", [])),
            brief_info=pick(fields.get("brief_info", [])),
            brief_info_en=pick(fields.get("brief_info_en", [])),
        )

    def _read_static(self, icons_dir: Path) -> list[StaticIcon]:
        pattern = self.profile["static_glob"]
        dynamic_dir = self.profile["dynamic_dir"]
        icons: list[StaticIcon] = []
        seen: set[str] = set()

        for path in sorted(icons_dir.rglob("*.png")):
            relative = path.relative_to(icons_dir).as_posix()
            parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
            if parent == dynamic_dir or parent.startswith(f"{dynamic_dir}/"):
                continue
            if not fnmatch.fnmatch(parent, pattern):
                continue
            if path.stem in seen:
                continue
            seen.add(path.stem)
            icons.append(StaticIcon(package=path.stem, path=path))

        if icons:
            return icons

        logger.debug("标准 drawable 目录中没有图标，回退到全目录扫描")
        for path in sorted(icons_dir.rglob("*.png")):
            relative = path.relative_to(icons_dir).as_posix()
            if relative.startswith(f"{dynamic_dir}/"):
                continue
            if path.stem in seen:
                continue
            seen.add(path.stem)
            icons.append(StaticIcon(package=path.stem, path=path))
        return icons

    def _read_dynamic(self, icons_dir: Path) -> list[DynamicAsset]:
        dynamic_root = icons_dir / self.profile["dynamic_dir"]
        if not dynamic_root.is_dir():
            return []
        assets: list[DynamicAsset] = []
        for path in sorted(dynamic_root.rglob("*.png")):
            assets.append(
                DynamicAsset(
                    package=path.parent.name,
                    filename=path.name,
                    path=path,
                )
            )
        return assets
