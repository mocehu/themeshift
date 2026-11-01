"""Icon canvas normalization and preview-grid rendering."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .errors import SourceFormatError, UsageError

RGBA = tuple[int, int, int, int]

# A 4096 x 4096 source already needs 64 MiB after RGBA conversion. Icons are
# normally much smaller; check dimensions BEFORE decoding/converting rather
# than relying on Pillow's much larger, warning-only default pixel threshold.
MAX_SOURCE_PIXELS = 16 * 1024 * 1024
MAX_SOURCE_DIMENSION = 8192

# Lanczos keeps small reductions crisp, but its negative lobes ring along hard
# edges and alias once an icon shrinks several times. Beyond this ratio an exact
# area average gives smoother curves at a slightly softer edge.
_AREA_FILTER_RATIO = 3.5


def _load_rgba(source: Path) -> Image.Image:
    """Decode one bounded source frame, translating Pillow failures for callers.

    Do not mutate Pillow's process-global MAX_IMAGE_PIXELS or truncated-image
    settings: concurrent web jobs must not weaken one another's image checks.
    """

    source = Path(source)
    try:
        with Image.open(source) as image:
            width, height = image.size
            if (
                min(width, height) <= 0
                or max(width, height) > MAX_SOURCE_DIMENSION
                or width * height > MAX_SOURCE_PIXELS
            ):
                raise SourceFormatError(
                    f"图像 {source.name!r} 的尺寸 {width}×{height} 超过安全限制。"
                )
            return image.convert("RGBA")
    except (
        OSError, ValueError, SyntaxError, EOFError,
        Image.DecompressionBombError, Image.DecompressionBombWarning,
    ) as exc:
        raise SourceFormatError(f"图像 {source.name!r} 损坏或无法安全解码：{exc}") from exc


def _fit_icon(icon: Image.Image, content_size: int) -> Image.Image:
    """Scale an icon down to fit ``content_size`` without ever enlarging it.

    Pillow resamples RGBA with premultiplied alpha, so edge colours are not
    darkened by transparent pixels. The filter is chosen by reduction ratio:
    Lanczos for mild reductions, exact area averaging when an icon is shrunk
    several times, where Lanczos would ring and alias curved outlines.
    """

    longest = max(icon.width, icon.height)
    if longest <= content_size:
        return icon
    scale = content_size / longest
    size = (max(1, round(icon.width * scale)), max(1, round(icon.height * scale)))
    resample = (
        Image.Resampling.BOX
        if longest / content_size >= _AREA_FILTER_RATIO
        else Image.Resampling.LANCZOS
    )
    return icon.resize(size, resample)


def normalize_icon(
    source: Path,
    destination: Path,
    canvas_size: int,
    content_size: int,
    background: RGBA = (0, 0, 0, 0),
) -> None:
    """Fit ``source`` onto a transparent square canvas.

    Artwork is scaled down proportionally so it never exceeds
    ``content_size`` and is centred on a ``canvas_size`` canvas, leaving a
    symmetric safety margin (the theme "bleed line").

    Icons smaller than the content box are *not* enlarged: upscaling loses
    fidelity, and platform launchers scale them anyway.
    """

    if canvas_size <= 0 or content_size <= 0 or content_size > canvas_size:
        raise UsageError(
            f"非法画布配置：canvas={canvas_size}, content={content_size}"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with _load_rgba(source) as icon:
        icon = _fit_icon(icon, content_size)
        canvas = Image.new("RGBA", (canvas_size, canvas_size), background)
        offset = (
            (canvas_size - icon.width) // 2,
            (canvas_size - icon.height) // 2,
        )
        # Using the icon as a paste mask would multiply its alpha a second time,
        # collapsing the antialiased edge into a hard, jagged outline. Proper
        # source-over compositing keeps the original coverage exactly.
        canvas.alpha_composite(icon, offset)
        # optimize=True re-encodes with several filter strategies: ~40% slower
        # for roughly 2-4% smaller files, which is a poor trade for icon packs.
        canvas.save(destination, format="PNG", compress_level=6)


def render_grid(
    icon_paths: list[Path],
    destination: Path,
    width: int,
    height: int,
    columns: int,
    rows: int,
    icon_size: int,
    start: int = 0,
    background: str = "#FFFFFF",
) -> None:
    """Render a preview JPEG containing ``columns`` x ``rows`` icons."""

    if min(width, height, columns, rows, icon_size) <= 0:
        raise UsageError("预览图配置必须为正数。")

    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (width, height), background)
    cell_width = width / columns
    cell_height = height / rows
    window = icon_paths[start : start + columns * rows]
    for offset, icon_path in enumerate(window):
        with _load_rgba(icon_path) as icon:
            icon = _fit_icon(icon, icon_size)
            column = offset % columns
            row = offset // columns
            x = round(column * cell_width + (cell_width - icon.width) / 2)
            y = round(row * cell_height + (cell_height - icon.height) / 2)
            canvas.paste(icon, (x, y), icon)
    canvas.save(destination, quality=94, subsampling=0)
