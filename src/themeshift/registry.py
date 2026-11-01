"""Resolve trusted ``module:Class`` adapter references from server profiles."""

from __future__ import annotations

import importlib
import inspect
from functools import lru_cache
from typing import Any

from .errors import ConfigError
from .profiles import adapter_config


@lru_cache(maxsize=128)
def load_implementation(spec: str) -> type:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ConfigError(f"非法的 implementation 引用：{spec!r}")
    try:
        implementation = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(f"无法装载平台实现 {spec!r}，请确认模块已安装且位于 PYTHONPATH。") from exc
    if not inspect.isclass(implementation):
        raise ConfigError(f"平台实现 {spec!r} 必须是一个类。")
    return implementation


def _create(profile: dict, role: str, method: str) -> Any:
    adapter = adapter_config(profile, role)
    if adapter is None:
        raise ConfigError(f"平台 {profile['name']!r} 没有声明 {role} 适配器。")
    instance = load_implementation(adapter["implementation"])(profile)
    if not callable(getattr(instance, method, None)):
        raise ConfigError(f"平台 {profile['name']!r} 的 {role} 适配器缺少 {method} 方法。")
    return instance


def create_source(profile: dict) -> Any:
    return _create(profile, "source", "load")


def create_target(profile: dict) -> Any:
    return _create(profile, "target", "build")


def accepts_options(instance: Any) -> bool:
    """True when a target adapter opts into the generic ``options`` channel.

    Adapters written against the three-argument protocol keep working; only
    writers that declare an ``options`` parameter receive rendering options.
    """

    try:
        parameters = inspect.signature(instance.build).parameters
    except (TypeError, ValueError):
        return False
    return "options" in parameters
