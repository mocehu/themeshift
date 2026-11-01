"""Tests for archive helpers: determinism, decoding and zip-slip safety."""

from __future__ import annotations

import io
import os
import stat
import struct
import tempfile
import unittest
import warnings
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.support import solid_png_bytes
from themeshift.errors import SourceFormatError
from themeshift.packaging import (
    DEFAULT_ARCHIVE_LIMITS,
    FIXED_TIMESTAMP,
    ArchiveLimits,
    extract_nested_zip,
    extract_prefix,
    has_dos_attributes,
    has_entry,
    read_entry_bytes,
    read_entry_text,
    safe_extract,
    write_text_lf,
    write_zip,
)


def zip_bytes(
    members: list[tuple[str | zipfile.ZipInfo, bytes]],
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # Deliberate duplicate names.
        with zipfile.ZipFile(output, "w", compression) as archive:
            for name, payload in members:
                archive.writestr(name, payload)
    return output.getvalue()


class PackagingTest(unittest.TestCase):
    def test_write_text_lf_forces_unix_endings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "description.xml"
            payload = write_text_lf(path, "a\r\nb\rc\n")
            self.assertEqual(payload, b"a\nb\nc\n")
            self.assertNotIn(b"\r", path.read_bytes())

    def test_write_zip_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "icon.png"
            source.write_bytes(b"payload")
            first = root / "a.zip"
            second = root / "b.zip"
            write_zip(first, [("icon.png", source)])
            write_zip(second, [("icon.png", source)])
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_read_entry_text_handles_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "theme.mtz"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("description.xml", "\ufeff<title>标题</title>")
            self.assertEqual(
                read_entry_text(archive, "description.xml"),
                "<title>标题</title>",
            )

    def test_nested_zip_rejects_path_traversal(self) -> None:
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as handle:
            handle.writestr("../evil.png", b"x")

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "theme.mtz"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("icons", inner.getvalue())
            with self.assertRaises(SourceFormatError):
                extract_nested_zip(archive, "icons", Path(tmp) / "out")

    def test_missing_entry_reports_format_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "theme.mtz"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("other", b"")
            with self.assertRaises(SourceFormatError):
                read_entry_text(archive, "description.xml")


class SafeExtractionTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write_archive(self, payload: bytes) -> Path:
        path = self.root / "theme.mtz"
        path.write_bytes(payload)
        return path

    def extract(self, payload: bytes, **kwargs) -> Path:
        destination = self.root / "out"
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            safe_extract(archive, destination, **kwargs)
        return destination

    def assert_rejected_before_writing(self, payload: bytes, **kwargs) -> None:
        with self.assertRaises(SourceFormatError):
            self.extract(payload, **kwargs)
        self.assertFalse((self.root / "out").exists())

    def test_rejects_unsafe_portable_paths(self) -> None:
        names = (
            "../evil.png", "res/../../evil.png", "res\\..\\evil.png",
            "/absolute.png", "\\absolute.png", "C:/evil.png", "C:evil.png",
            "//server/share/evil.png", "\\\\server\\share\\evil.png",
            "\\\\?\\C:\\evil.png", "\\\\.\\NUL",
            "res//icon.png", "res/./icon.png", "res/../icon.png",
            "res/icon.png:stream", "res/icon.png.", "res /icon.png",
            "res/CON", "res/nul.png", "res/COM1.png", "res/LPT9.png",
            "res/COM¹.png", "res/CON .png", "res/CONOUT$", "AUX/icon.png",
            "LONGFI~1.PNG", "LONGDI~1/icon.png",
            "res/ques?tion.png", "res/a*b.png", "res/a<b.png", "res/a|b.png",
            "res/a\nb.png", "res/\x01icon.png", "./", "../", "/",
            "/".join(["nested"] * 33) + "/icon.png",
            "a" * 256 + ".png",
        )
        sentinel = self.root / "evil.png"
        sentinel.write_bytes(b"untouched")
        for name in names:
            with self.subTest(name=name):
                self.assert_rejected_before_writing(zip_bytes([("good.png", b"ok"), (name, b"x")]))
                self.assertEqual(sentinel.read_bytes(), b"untouched")

    def test_rejects_nul_in_original_zip_filename(self) -> None:
        payload = zip_bytes([("badXname.png", b"bad")]).replace(b"badXname.png", b"bad\x00name.png")
        self.assert_rejected_before_writing(payload)

    def test_rejects_duplicate_normalized_paths_and_directory_aliases(self) -> None:
        pairs = (
            ("icon.png", "icon.png"),
            ("res/icon.png", "res\\icon.png"),
            ("Icon.png", "icon.png"),
            ("Icon.png", "\u0131con.png"),
            ("caf\u00e9.png", "cafe\u0301.png"),
            ("Res/a.png", "res/b.png"),
            ("folder/", "folder/"),
        )
        for first, second in pairs:
            with self.subTest(first=first, second=second):
                members = [(name, b"" if name.endswith("/") else b"x") for name in (first, second)]
                self.assert_rejected_before_writing(zip_bytes(members))

    def test_rejects_file_directory_collisions_in_both_orders(self) -> None:
        for names in (("res", "res/a.png"), ("res/a.png", "res"), ("res/", "res"), ("res", "res/")):
            with self.subTest(names=names):
                members = [(name, b"" if name.endswith("/") else b"x") for name in names]
                self.assert_rejected_before_writing(zip_bytes(members))

    def test_rejects_links_special_files_and_reparse_attributes(self) -> None:
        for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK):
            with self.subTest(mode=mode):
                member = zipfile.ZipInfo("link")
                member.create_system = 3
                member.external_attr = (mode | 0o777) << 16
                self.assert_rejected_before_writing(zip_bytes([(member, b"../outside")]))
        member = zipfile.ZipInfo("junction")
        member.create_system = 0
        member.external_attr = 0x400
        self.assert_rejected_before_writing(zip_bytes([(member, b"target")]))

    def test_rejects_directory_payload(self) -> None:
        self.assert_rejected_before_writing(zip_bytes([("res/", b"hidden payload")]))

    def test_normal_directories_unicode_windows_separators_and_empty_files(self) -> None:
        payload = zip_bytes([
            ("res\\drawable\\图标.png", b"png"),
            ("res/", b""),  # Explicit directory may follow its implicit creation.
            ("empty", b""),
        ])
        destination = self.extract(payload)
        self.assertEqual((destination / "res" / "drawable" / "图标.png").read_bytes(), b"png")
        self.assertEqual((destination / "empty").read_bytes(), b"")

    def test_normal_stored_archive_comment_and_prepended_stub(self) -> None:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("icon.png", b"png")
            archive.comment = "主题注释".encode()
        destination = self.extract(b"self-extracting-stub" + output.getvalue())
        self.assertEqual((destination / "icon.png").read_bytes(), b"png")

    def test_normal_zip64_local_header(self) -> None:
        output = io.BytesIO()
        with (
            zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive,
            archive.open("icon.png", "w", force_zip64=True) as member,
        ):
            member.write(b"png")
        destination = self.extract(output.getvalue())
        self.assertEqual((destination / "icon.png").read_bytes(), b"png")

    def test_normal_zip64_end_records(self) -> None:
        payload = bytearray(zip_bytes([("icon.png", b"png")]))
        end = payload.rfind(b"PK\x05\x06")
        _, _, _, count, _, directory_size, directory_offset, _ = struct.unpack_from("<4s4H2LH", payload, end)
        zip64 = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, directory_size, directory_offset)
        locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, end, 1)
        struct.pack_into("<2H2L", payload, end + 8, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
        destination = self.extract(payload[:end] + zip64 + locator + payload[end:])
        self.assertEqual((destination / "icon.png").read_bytes(), b"png")

    def test_prefix_keeps_directory_boundary_and_output_layout(self) -> None:
        path = self.write_archive(zip_bytes([
            ("icons/res/a.png", b"one"), ("icons\\res\\b.png", b"two"),
            ("icons-other/x.png", b"skip"), ("description.xml", b"xml"),
        ]))
        destination = self.root / "out"
        extract_prefix(path, "icons", destination)
        self.assertEqual((destination / "icons" / "res" / "a.png").read_bytes(), b"one")
        self.assertEqual((destination / "icons" / "res" / "b.png").read_bytes(), b"two")
        self.assertFalse((destination / "icons-other").exists())
        self.assertFalse((destination / "description.xml").exists())
        with self.assertRaises(SourceFormatError):
            extract_prefix(path, "absent/", self.root / "missing")
        self.assertFalse((self.root / "missing").exists())

    def test_prefix_does_not_hide_unsafe_unselected_entries(self) -> None:
        path = self.write_archive(zip_bytes([("icons/a.png", b"ok"), ("../evil", b"bad")]))
        with self.assertRaises(SourceFormatError):
            extract_prefix(path, "icons/", self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_exact_member_and_total_byte_limits_are_allowed(self) -> None:
        limits = replace(DEFAULT_ARCHIVE_LIMITS, max_member_bytes=4, max_total_bytes=8)
        destination = self.extract(zip_bytes([("a", b"1234"), ("b", b"5678")]), limits=limits)
        self.assertEqual((destination / "a").read_bytes(), b"1234")
        self.assertEqual((destination / "b").read_bytes(), b"5678")

    def test_oversized_member_is_rejected_before_any_writes(self) -> None:
        limits = replace(DEFAULT_ARCHIVE_LIMITS, max_member_bytes=4)
        self.assert_rejected_before_writing(zip_bytes([("good", b"x"), ("bad", b"12345")]), limits=limits)

    def test_oversized_total_is_rejected_before_any_writes(self) -> None:
        limits = replace(DEFAULT_ARCHIVE_LIMITS, max_total_bytes=7)
        self.assert_rejected_before_writing(zip_bytes([("a", b"1234"), ("b", b"5678")]), limits=limits)

    def test_rejects_real_high_ratio_deflate_payload(self) -> None:
        self.assert_rejected_before_writing(zip_bytes([("bomb", b"\0" * (2 * 1024 * 1024))]))

    def test_small_high_ratio_content_has_a_grace_budget(self) -> None:
        payload = b"\0" * 16384
        destination = self.extract(zip_bytes([("small", payload)]), limits=replace(DEFAULT_ARCHIVE_LIMITS, max_compression_ratio=1))
        self.assertEqual((destination / "small").read_bytes(), payload)

    def test_read_text_has_a_separate_smaller_budget(self) -> None:
        path = self.write_archive(zip_bytes([("description.xml", b"12345")]))
        limits = replace(DEFAULT_ARCHIVE_LIMITS, max_text_bytes=4)
        with self.assertRaises(SourceFormatError):
            read_entry_text(path, "description.xml", limits=limits)
        self.assertEqual(read_entry_bytes(path, "description.xml", limits=limits), b"12345")

    def test_archive_size_and_directory_budget_precede_zipfile_construction(self) -> None:
        payload = zip_bytes([("icon.png", b"png")])
        path = self.write_archive(payload)
        for limits in (
            replace(DEFAULT_ARCHIVE_LIMITS, max_archive_bytes=len(payload) - 1),
            replace(DEFAULT_ARCHIVE_LIMITS, max_directory_bytes=1),
        ):
            with (
                self.subTest(limits=limits),
                patch("themeshift.packaging.zipfile.ZipFile", side_effect=AssertionError("must not parse ZipInfo")),
                self.assertRaises(SourceFormatError),
            ):
                has_entry(path, "icon.png", limits=limits)

    def test_member_count_precedes_zipfile_construction_even_if_eocd_lies(self) -> None:
        limits = replace(DEFAULT_ARCHIVE_LIMITS, max_members=3)
        payload = bytearray(zip_bytes([(str(index), b"") for index in range(4)]))
        for claimed_count in (4, 1):
            with self.subTest(claimed_count=claimed_count):
                end = payload.rfind(b"PK\x05\x06")
                struct.pack_into("<2H", payload, end + 8, claimed_count, claimed_count)
                path = self.write_archive(payload)
                with (
                    patch("themeshift.packaging.zipfile.ZipFile", side_effect=AssertionError("must not parse ZipInfo")),
                    self.assertRaises(SourceFormatError),
                ):
                    has_entry(path, "0", limits=limits)

    def test_implicit_directory_growth_is_bounded(self) -> None:
        limits = replace(DEFAULT_ARCHIVE_LIMITS, max_members=3)
        self.assert_rejected_before_writing(
            zip_bytes([("a/b/c/1.png", b""), ("d/e/f/2.png", b"")]),
            limits=limits,
        )

    def test_nested_container_has_its_own_larger_bounded_budget(self) -> None:
        # The nested ZIP is larger than the single extracted icon allowance.
        inner = zip_bytes([("res/a.png", b"1234")])
        path = self.write_archive(zip_bytes([("icons", inner)]))
        limits = replace(DEFAULT_ARCHIVE_LIMITS, max_member_bytes=4)
        extract_nested_zip(path, "icons", self.root / "out", limits=limits)
        self.assertEqual((self.root / "out" / "res" / "a.png").read_bytes(), b"1234")
        large_inner = zip_bytes([(f"res/{index}.png", b"x") for index in range(100)])
        outer = zip_bytes([("icons", large_inner)])
        self.assertLess(len(outer), len(large_inner))
        path = self.write_archive(outer)
        with self.assertRaises(SourceFormatError):
            extract_nested_zip(path, "icons", self.root / "too-big", limits=replace(DEFAULT_ARCHIVE_LIMITS, max_archive_bytes=len(outer)))
        self.assertFalse((self.root / "too-big").exists())

    def test_read_and_nested_extraction_do_not_use_unbounded_archive_read(self) -> None:
        inner = zip_bytes([("icon.png", b"png")])
        path = self.write_archive(zip_bytes([("description.xml", b"xml"), ("icons", inner)]))
        with patch.object(zipfile.ZipFile, "read", side_effect=AssertionError("unbounded read")):
            self.assertEqual(read_entry_text(path, "description.xml"), "xml")
            extract_nested_zip(path, "icons", self.root / "out")
        self.assertEqual((self.root / "out" / "icon.png").read_bytes(), b"png")

    def test_streaming_checks_actual_size_and_removes_partial_file(self) -> None:
        for actual in (b"12345", b"123"):
            with self.subTest(actual=actual):
                destination = self.root / ("over" if len(actual) > 4 else "under")
                with (
                    zipfile.ZipFile(io.BytesIO(zip_bytes([("icon.png", b"1234")]))) as archive,
                    patch.object(archive, "open", return_value=io.BytesIO(actual)),
                    self.assertRaises(SourceFormatError),
                ):
                    safe_extract(archive, destination)
                self.assertEqual(list(destination.iterdir()), [])

    def test_streaming_reads_in_bounded_chunks(self) -> None:
        sizes = []

        class BoundedReader(io.BytesIO):
            def read(self, size=-1):
                sizes.append(size)
                if size < 0 or size > 64 * 1024:
                    raise AssertionError("unbounded read")
                return super().read(size)

        payload = b"x" * (3 * 64 * 1024 + 17)
        with (
            zipfile.ZipFile(io.BytesIO(zip_bytes([("icon.png", payload)]))) as archive,
            patch.object(archive, "open", return_value=BoundedReader(payload)),
        ):
            safe_extract(archive, self.root / "out")
        self.assertGreater(len(sizes), 3)
        self.assertEqual((self.root / "out" / "icon.png").read_bytes(), payload)

    def test_corrupt_crc_does_not_overwrite_an_existing_file(self) -> None:
        payload = bytearray(zip_bytes([("icon.png", b"replacement")], zipfile.ZIP_STORED))
        payload[30 + len("icon.png")] ^= 0xFF
        path = self.write_archive(payload)
        destination = self.root / "out"
        destination.mkdir()
        target = destination / "icon.png"
        target.write_bytes(b"original")
        with self.assertRaises(SourceFormatError):
            self.extract(payload)
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(list(destination.iterdir()), [target])
        with self.assertRaises(SourceFormatError):
            read_entry_bytes(path, "icon.png")

    def test_bad_deflate_stream_is_a_format_error(self) -> None:
        payload = bytearray(zip_bytes([("icon.png", b"replacement")]))
        start = 30 + len("icon.png")
        payload[start:start + 3] = b"\xff\xff\xff"
        with self.assertRaises(SourceFormatError):
            self.extract(payload)
        self.assertFalse((self.root / "out" / "icon.png").exists())

    def test_bad_archives_use_the_existing_format_error(self) -> None:
        path = self.write_archive(b"not a ZIP")
        for operation in (
            lambda: has_entry(path, "icons"),
            lambda: read_entry_text(path, "description.xml"),
            lambda: extract_prefix(path, "icons/", self.root / "out"),
            lambda: extract_nested_zip(path, "icons", self.root / "out"),
        ):
            with self.subTest(operation=operation), self.assertRaises(SourceFormatError):
                operation()
        path = self.write_archive(zip_bytes([("icons", b"not a ZIP either")]))
        with self.assertRaises(SourceFormatError):
            extract_nested_zip(path, "icons", self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_invalid_central_directory_and_count_are_format_errors(self) -> None:
        original = zip_bytes([("icon.png", b"png")])
        broken_directory = original.replace(b"PK\x01\x02", b"NOPE", 1)
        broken_count = bytearray(original)
        end = broken_count.rfind(b"PK\x05\x06")
        struct.pack_into("<2H", broken_count, end + 8, 0, 0)
        for payload in (broken_directory, broken_count, original[:-5]):
            with self.subTest(payload=payload):
                path = self.write_archive(payload)
                with self.assertRaises(SourceFormatError):
                    has_entry(path, "icon.png")

    def test_encryption_and_unbounded_dictionary_codecs_are_rejected(self) -> None:
        for encrypted, method in ((True, zipfile.ZIP_STORED), (False, zipfile.ZIP_LZMA), (False, 99)):
            with self.subTest(encrypted=encrypted, method=method):
                payload = bytearray(zip_bytes([("icon.png", b"png")], zipfile.ZIP_STORED))
                central = payload.find(b"PK\x01\x02")
                struct.pack_into("<H", payload, 6, int(encrypted))
                struct.pack_into("<H", payload, central + 8, int(encrypted))
                struct.pack_into("<H", payload, 8, method)
                struct.pack_into("<H", payload, central + 10, method)
                self.assert_rejected_before_writing(payload)

    def test_existing_symlink_is_rejected_without_writing_outside(self) -> None:
        destination = self.root / "out"
        outside = self.root / "outside"
        destination.mkdir()
        outside.mkdir()
        try:
            (destination / "res").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation not available: {exc}")
        with self.assertRaises(SourceFormatError):
            self.extract(zip_bytes([("good.png", b"ok"), ("res/evil.png", b"bad")]))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((destination / "good.png").exists())

    def test_existing_hardlink_is_not_overwritten(self) -> None:
        destination = self.root / "out"
        destination.mkdir()
        original = self.root / "original.png"
        original.write_bytes(b"original")
        target = destination / "icon.png"
        try:
            os.link(original, target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hardlink creation not available: {exc}")
        with self.assertRaises(SourceFormatError):
            self.extract(zip_bytes([("icon.png", b"replacement")]))
        self.assertEqual(original.read_bytes(), b"original")
        self.assertEqual(target.read_bytes(), b"original")

    def test_default_budgets_accept_a_thousand_real_png_icons(self) -> None:
        png = solid_png_bytes((48, 48))
        inner = zip_bytes([(f"res/drawable-xxhdpi/com.example.app{index}.png", png) for index in range(1000)])
        path = self.write_archive(zip_bytes([("description.xml", b"<theme/>"), ("icons", inner)]))
        destination = self.root / "out"
        extract_nested_zip(path, "icons", destination)
        icons = list(destination.rglob("*.png"))
        self.assertEqual(len(icons), 1000)
        self.assertTrue(all(icon.read_bytes() == png for icon in icons))

    def test_deterministic_writer_keeps_fixed_metadata(self) -> None:
        first = self.root / "first.png"
        second = self.root / "second.png"
        first.write_bytes(b"one")
        second.write_bytes(b"two")
        archive_path = self.root / "result.zip"
        write_zip(archive_path, [("b.png", first), ("a.png", second)])
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(archive.namelist(), ["b.png", "a.png"])
            for info in archive.infolist():
                self.assertEqual(info.date_time, FIXED_TIMESTAMP)
                self.assertEqual(info.create_system, 0)
                self.assertEqual(info.external_attr & 0xFF, 0x20)
                self.assertTrue(has_dos_attributes(info))
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
            safe_extract(archive, self.root / "roundtrip")
        self.assertEqual((self.root / "roundtrip" / "b.png").read_bytes(), b"one")

    def test_dos_attribute_detection_rejects_unix_entries(self) -> None:
        info = zipfile.ZipInfo("a.png")
        info.create_system = 3
        info.external_attr = 0o644 << 16
        self.assertFalse(has_dos_attributes(info))
        info.create_system = 0
        info.external_attr = 0
        self.assertFalse(has_dos_attributes(info))
        info.external_attr = 0x20
        self.assertTrue(has_dos_attributes(info))
        info.filename = "preview/"
        info.external_attr = 0x10
        self.assertTrue(has_dos_attributes(info))

    def test_directories_are_explicit_and_marked_as_folders(self) -> None:
        first = self.root / "a.jpg"
        second = self.root / "b.png"
        first.write_bytes(b"jpeg")
        second.write_bytes(b"png")
        archive_path = self.root / "dirs.zip"
        write_zip(
            archive_path,
            [("preview/a.jpg", first), ("dynamic_icons/com.example/b.png", second)],
        )
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            self.assertEqual(
                names,
                [
                    "preview/",
                    "preview/a.jpg",
                    "dynamic_icons/",
                    "dynamic_icons/com.example/",
                    "dynamic_icons/com.example/b.png",
                ],
            )
            for info in archive.infolist():
                self.assertEqual(info.create_system, 0)
                if info.is_dir():
                    self.assertEqual(info.external_attr & 0xFF, 0x10)
                else:
                    self.assertEqual(info.external_attr & 0xFF, 0x20)
                self.assertTrue(has_dos_attributes(info), info.filename)

    def test_explicit_directory_entries_are_not_duplicated(self) -> None:
        payload = self.root / "x.png"
        payload.write_bytes(b"png")
        archive_path = self.root / "explicit.zip"
        write_zip(archive_path, [("preview/", payload), ("preview/x.png", payload)])
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(archive.namelist(), ["preview/", "preview/x.png"])

    def test_limits_must_be_positive_integers(self) -> None:
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ArchiveLimits(max_members=value)


if __name__ == "__main__":
    unittest.main()
