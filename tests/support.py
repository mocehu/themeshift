"""Helpers for building tiny, synthetic source themes in tests."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from PIL import Image

DEFAULT_ICONS = {
    "com.miui.calculator": "calc",
    "com.tencent.mm": "wechat",
    "com.taobao.taobao": "taobao",
}


def solid_png_bytes(
    size: tuple[int, int] = (240, 240),
    color: tuple[int, int, int, int] = (220, 60, 60, 255),
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def build_fake_mtz(
    destination: Path,
    *,
    title: str = "测试主题",
    designer: str = "测试作者",
    icons: dict[str, bytes] | None = None,
    dynamic: dict[str, bytes] | None = None,
) -> Path:
    """Create a minimal Xiaomi-shaped ``.mtz`` archive."""

    icons = icons if icons is not None else {
        package: solid_png_bytes() for package in DEFAULT_ICONS
    }
    dynamic = dynamic or {}

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as archive:
        for package, payload in icons.items():
            archive.writestr(f"res/drawable-xxhdpi/{package}.png", payload)
        for relative, payload in dynamic.items():
            archive.writestr(f"dynamic_icons/{relative}", payload)

    description = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<theme>\n"
        f"  <title>{title}</title>\n"
        f"  <author>{designer}</author>\n"
        "</theme>\n"
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("description.xml", description)
        archive.writestr("icons", inner.getvalue())
    return destination
