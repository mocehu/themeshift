"""Tests for canvas normalization and preview rendering."""

from __future__ import annotations

import io
import struct
import tempfile
import unittest
import warnings
import zlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from tests.support import solid_png_bytes
from themeshift import imaging
from themeshift.errors import SourceFormatError
from themeshift.imaging import normalize_icon, render_grid


class NormalizeIconTest(unittest.TestCase):
    def test_full_bleed_icon_gets_ten_pixel_margin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src.png"
            target = Path(tmp) / "out.png"
            source.write_bytes(solid_png_bytes((240, 240)))
            normalize_icon(source, target, canvas_size=192, content_size=172)
            with Image.open(target) as image:
                self.assertEqual(image.size, (192, 192))
                self.assertEqual(image.convert("RGBA").getchannel("A").getbbox(), (10, 10, 182, 182))

    def test_small_icon_is_not_upscaled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src.png"
            target = Path(tmp) / "out.png"
            source.write_bytes(solid_png_bytes((40, 40)))
            normalize_icon(source, target, canvas_size=192, content_size=172)
            with Image.open(target) as image:
                self.assertEqual(image.size, (192, 192))
                self.assertEqual(image.convert("RGBA").getchannel("A").getbbox(), (76, 76, 116, 116))

    def test_antialiased_alpha_is_not_multiplied_twice(self) -> None:
        # Regression: pasting with the icon as mask squared the alpha, turning a
        # 50% edge into 25% and producing hard, jagged curves after resizing.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src.png"
            target = Path(tmp) / "out.png"
            image = Image.new("RGBA", (172, 172), (255, 255, 255, 0))
            ImageDraw.Draw(image).rectangle((0, 0, 171, 171), fill=(255, 255, 255, 128))
            image.save(source)
            normalize_icon(source, target, canvas_size=192, content_size=172)
            with Image.open(target) as result:
                alpha = result.convert("RGBA").getchannel("A")
                self.assertEqual(alpha.getpixel((96, 96)), 128)

    def test_filter_is_chosen_by_reduction_ratio(self) -> None:
        icon = Image.new("RGBA", (512, 512), (200, 40, 40, 255))
        calls = []

        def fake_resize(self, size, resample=None, **kwargs):
            calls.append(resample)
            return Image.new("RGBA", size, (200, 40, 40, 255))

        with patch.object(Image.Image, "resize", fake_resize):
            imaging._fit_icon(icon, 172)
            imaging._fit_icon(icon, 96)
            imaging._fit_icon(Image.new("RGBA", (64, 64)), 172)
        self.assertEqual(calls, [Image.Resampling.LANCZOS, Image.Resampling.BOX])
        self.assertEqual(icon.size, (512, 512), "the source image must not be mutated")

    def test_strong_reduction_matches_area_averaged_edges(self) -> None:
        # An 8x supersampled circle is the exact coverage reference; a single
        # Lanczos pass overshoots and rings here, the area filter does not.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "circle.png"
            target = root / "icon.png"
            truth_size = 32
            supersample = 8
            big = truth_size * supersample
            canvas = Image.new("L", (big, big), 0)
            ImageDraw.Draw(canvas).ellipse(
                (4 * supersample, 4 * supersample, (truth_size - 4) * supersample, (truth_size - 4) * supersample),
                fill=255,
            )
            truth = canvas.resize((truth_size, truth_size), Image.Resampling.BOX)
            artwork = Image.new("RGBA", (big, big), (0, 0, 0, 0))
            ImageDraw.Draw(artwork).ellipse(
                (4 * supersample, 4 * supersample, (truth_size - 4) * supersample, (truth_size - 4) * supersample),
                fill=(240, 240, 240, 255),
            )
            artwork.save(source)
            normalize_icon(source, target, canvas_size=64, content_size=truth_size)
            with Image.open(target) as image:
                alpha = image.convert("RGBA").getchannel("A").crop((16, 16, 48, 48))
                differences = [
                    abs(a - b) for a, b in zip(alpha.tobytes(), truth.tobytes(), strict=True)
                ]
            self.assertLess(sum(differences) / len(differences), 0.35)


class RenderGridTest(unittest.TestCase):
    def test_output_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            icons = []
            for index in range(6):
                path = root / f"icon-{index}.png"
                path.write_bytes(solid_png_bytes((192, 192)))
                icons.append(path)
            target = root / "preview.jpg"
            render_grid(
                icon_paths=icons,
                destination=target,
                width=510,
                height=345,
                columns=3,
                rows=2,
                icon_size=100,
            )
            with Image.open(target) as image:
                self.assertEqual(image.size, (510, 345))


class ImageSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source.png"
        self.target = self.root / "output.png"

    def normalize(self) -> None:
        normalize_icon(self.source, self.target, canvas_size=192, content_size=172)

    def test_unidentified_and_truncated_images_are_format_errors(self) -> None:
        png = solid_png_bytes((32, 32))
        idat = png.index(b"IDAT") + 4
        for payload in (b"not an image", png[:20], png[:idat + 2]):
            with self.subTest(payload=payload):
                self.source.write_bytes(payload)
                with self.assertRaises(SourceFormatError):
                    self.normalize()
                self.assertFalse(self.target.exists())

    def test_invalid_png_raster_is_a_format_error(self) -> None:
        png = bytearray(solid_png_bytes((32, 32)))
        idat = png.index(b"IDAT") + 4
        png[idat:idat + 3] = b"\xff\xff\xff"
        self.source.write_bytes(png)
        with self.assertRaises(SourceFormatError):
            self.normalize()
        self.assertFalse(self.target.exists())

    def test_pixel_and_dimension_bombs_are_rejected_before_convert(self) -> None:
        # Change only IHDR dimensions and its CRC; never allocate a large image.
        for width, height in ((4096, 4097), (8193, 1), (1, 8193)):
            with self.subTest(size=(width, height)):
                png = bytearray(solid_png_bytes((1, 1)))
                struct.pack_into(">2I", png, 16, width, height)
                struct.pack_into(">I", png, 29, zlib.crc32(png[12:29]))
                self.source.write_bytes(png)
                with (
                    patch.object(Image.Image, "convert", side_effect=AssertionError("must not decode")),
                    self.assertRaises(SourceFormatError),
                ):
                    self.normalize()
                self.assertFalse(self.target.exists())

    def test_pillow_own_bomb_error_is_translated_without_changing_global_limit(self) -> None:
        self.source.write_bytes(solid_png_bytes((4, 4)))
        with patch.object(Image, "MAX_IMAGE_PIXELS", 1):
            with self.assertRaises(SourceFormatError):
                self.normalize()
            self.assertEqual(Image.MAX_IMAGE_PIXELS, 1)
        self.assertFalse(self.target.exists())

    def test_pillow_warning_as_error_is_also_translated(self) -> None:
        self.source.write_bytes(solid_png_bytes((4, 4)))
        with warnings.catch_warnings(), patch.object(Image, "MAX_IMAGE_PIXELS", 8):
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with self.assertRaises(SourceFormatError):
                self.normalize()
        self.assertFalse(self.target.exists())

    def test_exact_pixel_budget_is_allowed(self) -> None:
        self.source.write_bytes(solid_png_bytes((4, 4)))
        original_pillow_limit = Image.MAX_IMAGE_PIXELS
        with patch.object(imaging, "MAX_SOURCE_PIXELS", 16), patch.object(imaging, "MAX_SOURCE_DIMENSION", 4):
            self.normalize()
        self.assertEqual(Image.MAX_IMAGE_PIXELS, original_pillow_limit)
        with Image.open(self.target) as image:
            self.assertEqual(image.size, (192, 192))
            self.assertEqual(image.getchannel("A").getbbox(), (94, 94, 98, 98))

    def test_grayscale_rgb_and_palette_pngs_still_normalize(self) -> None:
        for mode in ("L", "RGB", "P", "RGBA"):
            with self.subTest(mode=mode):
                buffer = io.BytesIO()
                with Image.new(mode, (16, 16)) as image:
                    image.save(buffer, format="PNG")
                self.source.write_bytes(buffer.getvalue())
                self.normalize()
                with Image.open(self.target) as image:
                    self.assertEqual(image.mode, "RGBA")
                    self.assertEqual(image.size, (192, 192))

    def test_preview_grid_rejects_corrupt_and_oversized_sources(self) -> None:
        payloads = [b"not an image"]
        png = bytearray(solid_png_bytes((1, 1)))
        struct.pack_into(">2I", png, 16, 8193, 1)
        struct.pack_into(">I", png, 29, zlib.crc32(png[12:29]))
        payloads.append(bytes(png))
        for payload in payloads:
            with self.subTest(payload=payload):
                self.source.write_bytes(payload)
                with self.assertRaises(SourceFormatError):
                    render_grid([self.source], self.target, 200, 100, 1, 1, 64)
                self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
