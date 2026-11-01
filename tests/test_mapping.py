"""Tests for package translation semantics."""

from __future__ import annotations

import unittest
from pathlib import Path

from themeshift.errors import ConfigError
from themeshift.mapping import PackageMap, apply_package_map
from themeshift.models import SourceTheme, StaticIcon, ThemeMeta


def _theme() -> SourceTheme:
    return SourceTheme(
        meta=ThemeMeta(title="Demo"),
        static_icons=[
            StaticIcon("com.miui.calculator", Path("calc.png")),
            StaticIcon("com.android.contacts", Path("contacts.png")),
            StaticIcon("com.miui.notes", Path("notes.png")),
            StaticIcon("com.tencent.mm", Path("wechat.png")),
        ],
    )


def _mapping() -> PackageMap:
    return PackageMap.from_dict(
        {
            "source": "xiaomi",
            "target": "honor",
            "source_system": {
                "prefixes": ["com.miui.", "com.android."],
                "exact": [],
            },
            "system_app_map": {
                "com.hihonor.calculator": ["com.miui.calculator"],
                "com.hihonor.contacts": ["com.android.contacts"],
                "com.hihonor.notepad": ["com.miui.notes"],
                "com.hihonor.missing": ["com.miui.absent"],
            },
        }
    )


class ApplyPackageMapTest(unittest.TestCase):
    def test_maps_passes_through_and_skips(self) -> None:
        normalized = apply_package_map(_theme(), _mapping())
        self.assertEqual(
            set(normalized.static_icons),
            {
                "com.hihonor.calculator",
                "com.hihonor.contacts",
                "com.hihonor.notepad",
                "com.tencent.mm",
            },
        )
        self.assertEqual(normalized.stats.mapped, 3)
        self.assertEqual(normalized.stats.passthrough, 1)
        self.assertEqual(normalized.stats.skipped, 1)

    def test_target_filter_restricts_system_mapping(self) -> None:
        normalized = apply_package_map(
            _theme(), _mapping(), frozenset({"com.hihonor.calculator"})
        )
        self.assertIn("com.hihonor.calculator", normalized.static_icons)
        self.assertNotIn("com.hihonor.contacts", normalized.static_icons)
        self.assertIn("com.tencent.mm", normalized.static_icons)


class ShippedMappingTest(unittest.TestCase):
    """Guard the data actually used for Xiaomi -> HONOR conversions."""

    @staticmethod
    def mapping() -> PackageMap:
        path = Path(__file__).resolve().parents[1] / "src/themeshift/data/mappings/xiaomi_to_honor.json"
        return PackageMap.load(path)

    def test_honor_browser_accepts_both_xiaomi_browser_packages(self) -> None:
        self.assertEqual(
            self.mapping().system_app_map["com.honor.browser"],
            ["com.android.browser", "com.miui.browser"],
        )

    def test_mi_home_is_not_alias_for_magic_home(self) -> None:
        mapping = self.mapping()
        self.assertNotIn("com.hihonor.magichome", mapping.system_app_map)
        sources = {candidate for candidates in mapping.system_app_map.values() for candidate in candidates}
        self.assertNotIn("com.xiaomi.smarthome", sources)


class PackageMapValidationTest(unittest.TestCase):
    def test_rejects_unsafe_target_package(self) -> None:
        with self.assertRaises(ConfigError):
            PackageMap.from_dict(
                {
                    "source": "a",
                    "target": "b",
                    "system_app_map": {"../evil": ["x"]},
                }
            )

    def test_rejects_bad_dynamic_reference(self) -> None:
        with self.assertRaises(ConfigError):
            PackageMap.from_dict(
                {
                    "source": "a",
                    "target": "b",
                    "dynamic_assets": [{"source": "nofilename", "target": "b/c.png"}],
                }
            )


if __name__ == "__main__":
    unittest.main()
