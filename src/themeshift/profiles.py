"""Versioned adapter declarations, independent of CLI and HTTP transports.

Version 1's single ``kind`` / ``implementation`` pair is still accepted. Version
2 lets one vendor provide a reader, a writer, or both without duplicating its id.
Directions are never inferred by reversing another vendor's lossy mapping.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import ConfigError

ICON_MODULES = ("icons", "dynamic_icons")
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_EXTENSION = re.compile(r"\.[a-zA-Z0-9]{1,16}\Z")
_SPEC = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*\Z")


def require_id(value: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ConfigError(f"非法的平台或模块标识：{value!r}。请使用小写字母、数字、下划线或连字符。")


def adapter_config(profile: dict[str, Any], role: str) -> dict[str, Any] | None:
    if role not in {"source", "target"}:
        raise ConfigError(f"未知适配器角色：{role!r}")
    if profile.get("schema_version", 1) == 1:
        if profile.get("kind") != role:
            return None
        return {"implementation": profile["implementation"], "modules": list(ICON_MODULES)}
    return profile.get("adapters", {}).get(role)


def adapter_modules(profile: dict[str, Any], role: str) -> tuple[str, ...]:
    adapter = adapter_config(profile, role)
    return tuple(adapter["modules"]) if adapter else ()


def icon_margin_bounds(profile: dict[str, Any]) -> tuple[int, int, int] | None:
    """Return ``(minimum, maximum, default)`` icon margin in canvas pixels.

    The margin is the gap between the canvas edge and the content box, so the
    default follows the profile's declared canvas/content pair. A quarter of the
    canvas (at least 32 px) must stay available for artwork; otherwise the
    option would ship unusable icons. Profiles without icon geometry, or with
    an inconsistent one, simply do not offer this option.
    """

    icon = profile.get("icon")
    if not isinstance(icon, dict):
        return None
    try:
        canvas = int(icon["canvas"])
        content = int(icon["content"])
    except (KeyError, TypeError, ValueError):
        return None
    if canvas <= 0 or content <= 0 or content > canvas:
        return None
    minimum_content = max(32, canvas // 4)
    maximum = (canvas - minimum_content) // 2
    if maximum < 0:
        return None
    return 0, maximum, min((canvas - content) // 2, maximum)


def validate_profile(data: dict[str, Any], expected_name: str, path: Path) -> None:
    require_id(expected_name)
    if data.get("name") != expected_name:
        raise ConfigError(f"{path} 的 name 必须与文件名 {expected_name!r} 一致。")
    if not isinstance(data.get("display_name"), str) or not data["display_name"].strip():
        raise ConfigError(f"{path} 缺少非空的 display_name。")
    version = data.get("schema_version", 1)
    if type(version) is not int or version not in {1, 2}:
        raise ConfigError(f"{path} 的 schema_version 仅支持 1 或 2。")
    if version == 1:
        if not isinstance(data.get("kind"), str) or data["kind"] not in {"source", "target"}:
            raise ConfigError(f"{path} 的 kind 必须是 'source' 或 'target'。")
        if "implementation" not in data:
            raise ConfigError(f"{path} 缺少 implementation。")
    else:
        adapters = data.get("adapters")
        if not isinstance(adapters, dict) or not adapters or not set(adapters) <= {"source", "target"}:
            raise ConfigError(f"{path} 的 adapters 必须声明 source、target 或两者。")
        if any(not isinstance(adapter, dict) for adapter in adapters.values()):
            raise ConfigError(f"{path} 中每个已声明的适配器必须是对象，不能为 null。")
        if "kind" in data or "implementation" in data:
            raise ConfigError(f"{path} 请勿混用 v1 的 kind/implementation 与 v2 的 adapters。")

    for role in ("source", "target"):
        adapter = adapter_config(data, role)
        if adapter is None:
            continue
        if not isinstance(adapter, dict):
            raise ConfigError(f"{path} 的 {role} 适配器必须是对象。")
        spec = adapter.get("implementation")
        if not isinstance(spec, str) or not _SPEC.fullmatch(spec):
            raise ConfigError(f"{path} 的 {role}.implementation 必须是 'module:Class' 形式。")
        modules = adapter.get("modules")
        if not isinstance(modules, list) or not modules:
            raise ConfigError(f"{path} 的 {role}.modules 必须是非空字符串数组。")
        for module in modules:
            require_id(module)
        if len(modules) != len(set(modules)):
            raise ConfigError(f"{path} 的 {role}.modules 含有重复模块。")
        if role == "source":
            extensions = data.get("input_extensions")
            if not isinstance(extensions, list) or not extensions or not all(
                isinstance(ext, str) and _EXTENSION.fullmatch(ext) for ext in extensions
            ):
                raise ConfigError(f"{path} 的 input_extensions 必须是扩展名数组，例如 ['.mtz']。")
        else:
            extension = data.get("output_extension")
            if not isinstance(extension, str) or not _EXTENSION.fullmatch(extension):
                raise ConfigError(f"{path} 的 output_extension 必须是安全扩展名，例如 '.hnt'。")
            for key in ("output_suffix", "report_suffix"):
                value = data.get(key, "")
                if not isinstance(value, str) or any(char in value for char in '/\\:\x00'):
                    raise ConfigError(f"{path} 的 {key} 不能包含路径分隔符。")
