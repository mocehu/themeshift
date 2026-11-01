"""End-to-end migration test against a synthetic Xiaomi theme."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tests.support import build_fake_mtz, solid_png_bytes
from themeshift.cli import main
from themeshift.service import MigrationRequest, MigrationService


class EndToEndTest(unittest.TestCase):
    def test_migrate_produces_valid_honor_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mtz = build_fake_mtz(root / "demo.mtz", title="合成主题", designer="测试作者")
            out = root / "demo.hnt"

            code = main(
                [
                    "migrate",
                    str(mtz),
                    "-o",
                    str(out),
                    "--workdir",
                    str(root / "work"),
                    "--quiet",
                ]
            )

            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())

            with zipfile.ZipFile(out) as outer:
                names = outer.namelist()
                self.assertIn("description.xml", names)
                self.assertIn("icons", names)
                self.assertIn("preview/icon_small.jpg", names)
                for info in outer.infolist():
                    self.assertEqual(info.create_system, 0, info.filename)
                    expected = 0x10 if info.is_dir() else 0x20
                    self.assertEqual(info.external_attr & 0xFF, expected, info.filename)
                self.assertIn("preview/", names)

                with zipfile.ZipFile(io.BytesIO(outer.read("icons"))) as icons:
                    icon_names = set(icons.namelist())
                    self.assertIn("com.hihonor.calculator.png", icon_names)
                    self.assertIn("com.tencent.mm.png", icon_names)
                    self.assertIn("com.taobao.taobao.png", icon_names)
                    for name in icon_names:
                        with Image.open(io.BytesIO(icons.read(name))) as image:
                            self.assertEqual(image.size, (192, 192))
                            bbox = image.convert("RGBA").getchannel("A").getbbox()
                            self.assertEqual(bbox, (10, 10, 182, 182))
                    for info in icons.infolist():
                        self.assertEqual(info.create_system, 0, info.filename)
                        expected = 0x10 if info.is_dir() else 0x20
                        self.assertEqual(info.external_attr & 0xFF, expected, info.filename)

                previews = {
                    name: Image.open(io.BytesIO(outer.read(name))).size
                    for name in names
                    if name.startswith("preview/") and not name.endswith("/")
                }
                self.assertEqual(previews["preview/icon_small.jpg"], (510, 345))
                self.assertEqual(previews["preview/icon_small_1.jpg"], (640, 640))
                self.assertEqual(previews["preview/preview_icons_0.jpg"], (1080, 2160))
                self.assertEqual(previews["preview/preview_icons_1.jpg"], (1080, 2160))

                payload = outer.read("description.xml")

            self.assertNotIn(b"\r\n", payload)
            self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
            text = payload.decode("utf-8")
            self.assertIn("<title>合成主题</title>", text)
            self.assertIn("<designer>测试作者</designer>", text)
            self.assertIn('"extraContentFlag": "5"', text)
            self.assertIn('"en"', text)

    def test_icon_margin_option_resizes_the_safe_content_area(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mtz = build_fake_mtz(root / "demo.mtz")
            out = root / "demo.hnt"
            code = main(["migrate", str(mtz), "-o", str(out), "--icon-margin", "72", "--quiet"])
            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())
            with zipfile.ZipFile(out) as outer, zipfile.ZipFile(io.BytesIO(outer.read("icons"))) as icons:
                name = next(n for n in icons.namelist() if n.endswith(".png") and not n.startswith("dynamic_icons/"))
                with Image.open(io.BytesIO(icons.read(name))) as image:
                    self.assertEqual(image.size, (192, 192))
                    box = image.convert("RGBA").getchannel("A").getbbox()
            self.assertEqual(box, (72, 72, 120, 120))

    def test_out_of_range_icon_margin_is_a_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mtz = build_fake_mtz(root / "demo.mtz")
            out = root / "demo.hnt"
            code = main(["migrate", str(mtz), "-o", str(out), "--icon-margin", "73", "--quiet"])
            self.assertEqual(code, 2)
            self.assertFalse(out.exists())

    def test_parallel_rendering_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            icons = {f"com.example.app{index}": solid_png_bytes((256, 256)) for index in range(24)}
            mtz = build_fake_mtz(root / "demo.mtz", icons=icons)
            digests = []
            for run in range(2):
                out = root / f"run{run}.hnt"
                result = MigrationService().migrate(MigrationRequest(input_path=mtz, target="honor", output_path=out))
                digests.append(result.output_path.read_bytes())
                with zipfile.ZipFile(out) as outer, zipfile.ZipFile(io.BytesIO(outer.read("icons"))) as inner:
                    self.assertEqual(len(inner.namelist()), 24)
            self.assertEqual(digests[0], digests[1])

    def test_honor_browser_is_mapped_and_mi_home_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mtz = build_fake_mtz(
                root / "demo.mtz",
                icons={
                    "com.miui.browser": solid_png_bytes(),
                    "com.android.browser": solid_png_bytes(),
                    "com.xiaomi.smarthome": solid_png_bytes(),
                },
            )
            out = root / "demo.hnt"
            code = main(["migrate", str(mtz), "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            with zipfile.ZipFile(out) as outer, zipfile.ZipFile(io.BytesIO(outer.read("icons"))) as icons:
                names = set(icons.namelist())
            self.assertIn("com.honor.browser.png", names)
            self.assertNotIn("com.hihonor.magichome.png", names)
            self.assertNotIn("com.xiaomi.smarthome.png", names)

    def test_cli_switches_non_utf8_streams_to_utf8(self) -> None:
        # Windows consoles default to a legacy codec; Chinese output must not crash.
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")
        with patch.object(sys, "stdout", stream), patch.object(sys, "stderr", stream):
            self.assertEqual(main(["platforms", "--quiet"]), 0)
            self.assertEqual(stream.encoding.lower(), "utf-8")

    def test_package_filter_limits_system_icons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mtz = build_fake_mtz(root / "demo.mtz")
            allow = root / "allow.json"
            allow.write_text(json.dumps(["com.hihonor.calculator"]), encoding="utf-8")
            out = root / "filtered.hnt"

            code = main(
                [
                    "migrate",
                    str(mtz),
                    "-o",
                    str(out),
                    "--workdir",
                    str(root / "work"),
                    "--packages",
                    str(allow),
                    "--quiet",
                ]
            )

            self.assertEqual(code, 0)
            with zipfile.ZipFile(out) as outer, zipfile.ZipFile(io.BytesIO(outer.read("icons"))) as icons:
                names = set(icons.namelist())
            self.assertIn("com.hihonor.calculator.png", names)
            self.assertIn("com.tencent.mm.png", names)
            self.assertNotIn("com.hihonor.contacts.png", names)

    def test_dry_run_does_not_write_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mtz = build_fake_mtz(root / "demo.mtz")
            out = root / "should-not-exist.hnt"
            code = main(
                [
                    "migrate",
                    str(mtz),
                    "-o",
                    str(out),
                    "--workdir",
                    str(root / "work"),
                    "--dry-run",
                    "--quiet",
                ]
            )
            self.assertEqual(code, 0)
            self.assertFalse(out.exists())

    def test_shorthand_equals_migrate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mtz = build_fake_mtz(root / "demo.mtz")
            code = main(
                [
                    str(mtz),
                    "--workdir",
                    str(root / "work"),
                    "--dry-run",
                    "--quiet",
                ]
            )
            self.assertEqual(code, 0)

    def test_missing_input_returns_usage_exit_code(self) -> None:
        code = main(["migrate", "does-not-exist.mtz", "--quiet"])
        self.assertEqual(code, 2)


class CommandTest(unittest.TestCase):
    def test_platforms_lists_both(self) -> None:
        self.assertEqual(main(["platforms", "--quiet"]), 0)

    def test_inspection_preserves_optional_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mtz = build_fake_mtz(Path(tmp) / "demo.mtz")
            with zipfile.ZipFile(mtz) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            entries["description.xml"] = entries["description.xml"].replace(
                b"</theme>",
                b"<version>3.2</version><title-en>English title</title-en>"
                b"<description>Source brief</description><description-en>English brief</description-en></theme>",
            )
            with zipfile.ZipFile(mtz, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, payload in entries.items():
                    archive.writestr(name, payload)
            meta = MigrationService().inspect(mtz).meta
            self.assertEqual(meta.version, "3.2")
            self.assertEqual(meta.title_en, "English title")
            self.assertEqual(meta.brief_info, "Source brief")
            self.assertEqual(meta.brief_info_en, "English brief")

    def test_inspect_reads_fake_theme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mtz = build_fake_mtz(Path(tmp) / "demo.mtz")
            self.assertEqual(main(["inspect", str(mtz), "--quiet"]), 0)


if __name__ == "__main__":
    unittest.main()
