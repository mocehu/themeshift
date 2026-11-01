"""Portable naming helpers shared by readers, writers and transports."""

from __future__ import annotations

import re

_UNSAFE_SLUG = re.compile(r"[^\w.\-]+", re.UNICODE)
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.IGNORECASE)


def slugify(text: str, fallback: str = "theme") -> str:
    """Filesystem-safe name retaining CJK, including Windows device safety."""
    cleaned = _UNSAFE_SLUG.sub("_", (text or "").strip()).strip("._-") or fallback
    return f"_{cleaned}" if _RESERVED.match(cleaned) else cleaned


def is_safe_component(name: str) -> bool:
    """Reject traversal, Windows streams/device names and cross-OS aliases."""
    if not isinstance(name, str) or not name or name.startswith("..") or len(name) > 240:
        return False
    if name in {".", ".."} or name[-1] in ". " or _RESERVED.match(name):
        return False
    return not any(char in '/\\:\x00<>"|?*' or ord(char) < 32 for char in name)
