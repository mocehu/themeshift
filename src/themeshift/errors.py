"""Exception hierarchy with stable CLI exit codes."""

from __future__ import annotations


class ThemeShiftError(Exception):
    """Base class for every error raised intentionally by themeshift."""

    exit_code = 1


class UsageError(ThemeShiftError):
    """The caller passed invalid arguments or an unusable path."""

    exit_code = 2


class SourceFormatError(ThemeShiftError):
    """The source theme package is missing entries or is malformed."""

    exit_code = 3


class ConfigError(ThemeShiftError):
    """A platform profile or mapping file is missing or invalid."""

    exit_code = 4


class BuildValidationError(ThemeShiftError):
    """A target adapter produced an artifact that failed verification."""

    exit_code = 5
