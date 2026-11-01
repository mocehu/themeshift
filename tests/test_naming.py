"""Tests for slug and path-component safety helpers."""

from __future__ import annotations

import unittest

from themeshift.naming import is_safe_component, slugify


class SlugifyTest(unittest.TestCase):
    def test_keeps_cjk(self) -> None:
        self.assertEqual(slugify("超拟圆"), "超拟圆")

    def test_replaces_path_separators(self) -> None:
        self.assertEqual(slugify("a/b:c"), "a_b_c")

    def test_falls_back_when_empty(self) -> None:
        self.assertEqual(slugify("   ", fallback="theme"), "theme")


class SafeComponentTest(unittest.TestCase):
    def test_accepts_package_names(self) -> None:
        self.assertTrue(is_safe_component("com.tencent.mm"))

    def test_portable_windows_names(self) -> None:
        for value in ("CON", "con.png", "NUL", "LPT1", "x:stream", "x.", "x ", "x?", "x\n", None):
            with self.subTest(value=value):
                self.assertFalse(is_safe_component(value))
        self.assertEqual(slugify("CON"), "_CON")
        self.assertTrue(is_safe_component("天气.png"))

    def test_rejects_traversal(self) -> None:
        for value in ("..", "../evil", "a/b", "a\\b", "", "..hidden"):
            with self.subTest(value=value):
                self.assertFalse(is_safe_component(value))


if __name__ == "__main__":
    unittest.main()
