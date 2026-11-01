"""Translate source package names into target package names.

All package knowledge lives in JSON mapping data; this module only knows how
to apply it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError
from .models import NormalizedTheme, SourceTheme
from .naming import is_safe_component


@dataclass
class PackageMap:
    """A source -> target package translation table."""

    source: str
    target: str
    system_app_map: dict[str, list[str]] = field(default_factory=dict)
    source_system_prefixes: tuple[str, ...] = ()
    source_system_exact: frozenset[str] = frozenset()
    dynamic_assets: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict, *, origin: Path | None = None) -> PackageMap:
        where = str(origin) if origin else "包名映射"
        if not isinstance(data, dict):
            raise ConfigError(f"{where} 的顶层必须是对象。")
        for key in ("source", "target"):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ConfigError(f"{where} 缺少字符串字段 {key!r}。")

        system = data.get("source_system", {})
        if not isinstance(system, dict):
            raise ConfigError(f"{where} 的 source_system 必须是对象。")
        for key in ("prefixes", "exact"):
            values = system.get(key, [])
            if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
                raise ConfigError(f"{where} 的 source_system.{key} 必须是非空字符串的数组。")
        if not isinstance(data.get("dynamic_assets", []), list):
            raise ConfigError(f"{where} 的 dynamic_assets 必须是数组。")

        raw_map = data.get("system_app_map", {})
        if not isinstance(raw_map, dict):
            raise ConfigError(f"{where} 的 system_app_map 必须是对象。")

        system_app_map: dict[str, list[str]] = {}
        for target_package, candidates in raw_map.items():
            if not is_safe_component(target_package):
                raise ConfigError(f"{where} 中存在非法目标包名 {target_package!r}。")
            if not isinstance(candidates, list) or not all(
                isinstance(item, str) for item in candidates
            ):
                raise ConfigError(
                    f"{where} 中 {target_package!r} 的候选列表必须是字符串数组。"
                )
            system_app_map[target_package] = list(candidates)

        dynamic_assets: list[tuple[str, str]] = []
        for entry in data.get("dynamic_assets", []):
            if not isinstance(entry, dict) or not {"source", "target"} <= set(entry):
                raise ConfigError(
                    f"{where} 的 dynamic_assets 项必须包含 source 与 target。"
                )
            source_ref = str(entry["source"])
            target_ref = str(entry["target"])
            for reference in (source_ref, target_ref):
                package, separator, filename = reference.partition("/")
                if not separator or not is_safe_component(package) or not is_safe_component(
                    filename
                ):
                    raise ConfigError(
                        f"{where} 中存在非法动态资源路径 {reference!r}，"
                        "格式应为 '<package>/<filename>'。"
                    )
            dynamic_assets.append((source_ref, target_ref))

        return cls(
            source=data["source"],
            target=data["target"],
            system_app_map=system_app_map,
            source_system_prefixes=tuple(system.get("prefixes", [])),
            source_system_exact=frozenset(system.get("exact", [])),
            dynamic_assets=dynamic_assets,
        )

    @classmethod
    def load(cls, path: Path) -> PackageMap:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConfigError(f"{path} 不是合法的 UTF-8 JSON：{exc}") from exc
        return cls.from_dict(payload, origin=path)

    def is_source_system(self, package: str) -> bool:
        """True if ``package`` belongs to the source OS, not a third-party app."""

        if package in self.source_system_exact:
            return True
        return package.startswith(self.source_system_prefixes)


def apply_package_map(
    theme: SourceTheme,
    mapping: PackageMap,
    target_filter: frozenset[str] | None = None,
    *,
    modules: frozenset[str] = frozenset({"icons", "dynamic_icons"}),
) -> NormalizedTheme:
    """Return a theme whose packages already match the target platform.

    * system apps: resolved through ``system_app_map`` (first existing source wins)
    * third-party apps: copied through unchanged
    * source-only system icons with no target equivalent: dropped
    """

    by_package = {icon.package: icon.path for icon in theme.static_icons} if "icons" in modules else {}
    normalized = NormalizedTheme(meta=theme.meta)

    for target_package, candidates in (mapping.system_app_map.items() if "icons" in modules else ()):
        if target_filter is not None and target_package not in target_filter:
            continue
        for candidate in candidates:
            source_path = by_package.get(candidate)
            if source_path is None:
                continue
            normalized.static_icons[target_package] = source_path
            normalized.stats.mapped += 1
            normalized.note(f"{candidate} -> {target_package}")
            break
        else:
            normalized.stats.skipped += 1
            normalized.note(f"(skip) {target_package}: no matching source icon")

    for package, source_path in by_package.items():
        if mapping.is_source_system(package):
            continue
        if package in normalized.static_icons:
            continue
        if not is_safe_component(package):
            normalized.note(f"(skip) unsafe package name: {package!r}")
            continue
        normalized.static_icons[package] = source_path
        normalized.stats.passthrough += 1

    normalized.note(f"third-party passthrough: {normalized.stats.passthrough}")

    dynamic_index = {
        (asset.package, asset.filename): asset.path for asset in theme.dynamic_assets
    }
    for source_ref, target_ref in (mapping.dynamic_assets if "dynamic_icons" in modules else ()):
        source_package, _, source_name = source_ref.partition("/")
        target_package, _, target_name = target_ref.partition("/")
        if target_filter is not None and target_package not in target_filter:
            continue
        source_path = dynamic_index.get((source_package, source_name))
        if source_path is None:
            continue
        normalized.dynamic_assets.append((target_package, target_name, source_path))
        normalized.stats.dynamic += 1
        normalized.note(f"dynamic {source_ref} -> {target_ref}")

    return normalized
