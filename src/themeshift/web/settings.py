"""Explicit, bounded settings for the single-process local Web service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..errors import UsageError


@dataclass(frozen=True)
class WebSettings:
    storage_dir: Path = Path(".themeshift-web")
    workers: int = 2
    max_upload_bytes: int = 128 * 1024 * 1024
    max_jobs: int = 32
    max_pending: int = 8
    retention_seconds: int = 3600
    upload_timeout_seconds: int = 120
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")

    def __post_init__(self) -> None:
        for name in ("workers", "max_upload_bytes", "max_jobs", "max_pending", "retention_seconds", "upload_timeout_seconds"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise UsageError(f"Web 配置 {name} 必须是正整数。")
        if self.workers > 16:
            raise UsageError("轻量服务最多允许 16 个转换线程。")
        if self.max_pending > self.max_jobs:
            raise UsageError("max_pending 不能大于 max_jobs。")
        if not self.allowed_hosts or any(not host or "/" in host or "*" in host for host in self.allowed_hosts):
            raise UsageError("必须显式配置允许的 Host 主机名，不支持通配符。")

    def public_limits(self) -> dict[str, int]:
        return {
            "max_upload_bytes": self.max_upload_bytes,
            "retention_seconds": self.retention_seconds,
            "max_jobs": self.max_jobs,
            "max_pending": self.max_pending,
            "workers": self.workers,
        }
