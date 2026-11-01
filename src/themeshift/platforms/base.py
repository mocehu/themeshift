"""Interfaces every source/target platform implementation must satisfy."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..mapping import PackageMap
from ..models import BuildReport, NormalizedTheme, SourceTheme


class SourcePlatform(Protocol):
    """Reads a platform package into a neutral :class:`SourceTheme`."""

    name: str

    def load(self, package_path: Path, work_dir: Path) -> SourceTheme: ...


class TargetPlatform(Protocol):
    """Writes a neutral :class:`NormalizedTheme` in a platform format."""

    name: str

    def build(
        self,
        theme: NormalizedTheme,
        out_path: Path,
        work_dir: Path,
    ) -> BuildReport: ...


class OptionsAwareTarget(TargetPlatform, Protocol):
    """Target that also accepts validated rendering options.

    The service detects the extra parameter at runtime, so existing writers keep
    the three-argument contract. Unknown option names must be ignored safely;
    values are validated by the service before this call and again here.
    """

    def build(
        self,
        theme: NormalizedTheme,
        out_path: Path,
        work_dir: Path,
        options: dict[str, object] | None = None,
    ) -> BuildReport: ...


class ModuleTransformer(Protocol):
    """Translate one explicitly negotiated module into the neutral target IR."""

    def transform(
        self, source: SourceTheme, target: NormalizedTheme, mapping: PackageMap
    ) -> None: ...
