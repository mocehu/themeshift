"""Tests for data-directory resolution and profile validation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from themeshift.errors import ConfigError
from themeshift.repository import DataRepository


class PackagedDataTest(unittest.TestCase):
    def test_default_repository_exposes_both_platforms(self) -> None:
        repo = DataRepository.create()
        self.assertIn("xiaomi", repo.available("source"))
        self.assertIn("honor", repo.available("target"))
        self.assertIn("xiaomi_to_honor", repo.mapping_names())


class ExternalDataTest(unittest.TestCase):
    def test_name_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "profiles").mkdir()
            (root / "profiles" / "demo.json").write_text(
                json.dumps(
                    {
                        "name": "other",
                        "display_name": "Demo",
                        "kind": "source",
                        "implementation": "demo:Demo",
                        "input_extensions": [".demo"],
                    }
                ),
                encoding="utf-8",
            )
            repo = DataRepository.create(root)
            with self.assertRaises(ConfigError):
                repo.load_profile("demo")

    def test_missing_mapping_lists_alternatives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "profiles").mkdir()
            (root / "mappings").mkdir()
            repo = DataRepository.create(root)
            with self.assertRaises(ConfigError):
                repo.mapping_path("xiaomi", "huawei")


if __name__ == "__main__":
    unittest.main()
