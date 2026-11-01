"""Versioned profiles, role registration and explicit mapping directions."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from themeshift import registry
from themeshift.errors import ConfigError
from themeshift.models import BuildReport
from themeshift.profiles import (
    ICON_MODULES,
    adapter_config,
    adapter_modules,
    require_id,
    validate_profile,
)
from themeshift.repository import DataRepository


class ProfileReader:
    """A read-only toy plugin; it deliberately has no build() method."""

    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self.name = profile["name"]

    def load(self, package_path: Path, work_dir: Path) -> str:
        return f"read:{self.name}"


class ProfileWriter:
    """An independent write-only plugin, not a subclass of the reader."""

    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self.name = profile["name"]

    def build(self, theme, out_path: Path, work_dir: Path) -> BuildReport:
        out_path.write_text(f"write:{self.name}", encoding="utf-8")
        return BuildReport(output=out_path, checks={"toy-written": True})


class EmptyAdapter:
    def __init__(self, profile: dict) -> None:
        self.profile = profile


def legacy_profile(name: str = "legacy", role: str = "source") -> dict:
    result = {
        "name": name,
        "display_name": f"Legacy {name}",
        "kind": role,
        "implementation": f"tests.test_profiles:Profile{'Reader' if role == 'source' else 'Writer'}",
    }
    if role == "source":
        result["input_extensions"] = [f".{name}"]
    else:
        result["output_extension"] = f".{name}"
    return result


def dual_profile(name: str = "dual") -> dict:
    return {
        "schema_version": 2,
        "name": name,
        "display_name": f"Dual {name}",
        "adapters": {
            "source": {
                "implementation": "tests.test_profiles:ProfileReader",
                "modules": ["icons", "dynamic_icons", "wallpapers"],
            },
            "target": {
                "implementation": "tests.test_profiles:ProfileWriter",
                "modules": ["icons", "wallpapers"],
            },
        },
        "input_extensions": [f".{name}", ".THEME"],
        "output_extension": f".{name}",
    }


class ProfileValidationTest(unittest.TestCase):
    def validate(self, profile: dict) -> None:
        validate_profile(profile, profile["name"], Path(f"{profile['name']}.json"))

    def test_unversioned_and_explicit_v1_profiles_remain_compatible(self) -> None:
        for role in ("source", "target"):
            for explicit_version in (False, True):
                with self.subTest(role=role, explicit_version=explicit_version):
                    profile = legacy_profile(role=role)
                    if explicit_version:
                        profile["schema_version"] = 1
                    before = copy.deepcopy(profile)
                    self.validate(profile)
                    self.assertEqual(adapter_modules(profile, role), ICON_MODULES)
                    opposite = "target" if role == "source" else "source"
                    self.assertIsNone(adapter_config(profile, opposite))
                    self.assertEqual(adapter_modules(profile, opposite), ())
                    self.assertEqual(profile, before)

    def test_v2_supports_both_independent_roles_for_one_brand(self) -> None:
        profile = dual_profile()
        self.validate(profile)
        reader = registry.create_source(profile)
        writer = registry.create_target(profile)
        self.assertIsInstance(reader, ProfileReader)
        self.assertIsInstance(writer, ProfileWriter)
        self.assertEqual(reader.name, writer.name)
        self.assertIs(reader.profile, profile)
        self.assertIs(writer.profile, profile)
        self.assertFalse(hasattr(reader, "build"))
        self.assertFalse(hasattr(writer, "load"))
        self.assertEqual(adapter_modules(profile, "source"), ("icons", "dynamic_icons", "wallpapers"))
        self.assertEqual(adapter_modules(profile, "target"), ("icons", "wallpapers"))

    def test_v2_allows_read_only_or_write_only_platforms(self) -> None:
        for role in ("source", "target"):
            with self.subTest(role=role):
                profile = dual_profile()
                profile["adapters"] = {role: profile["adapters"][role]}
                self.validate(profile)
                opposite = "target" if role == "source" else "source"
                self.assertIsNone(adapter_config(profile, opposite))

    def test_invalid_schema_versions_are_config_errors(self) -> None:
        for version in (0, 3, -1, "2", 2.0, True, None, [], {}):
            with self.subTest(version=version):
                profile = dual_profile()
                profile["schema_version"] = version
                with self.assertRaises(ConfigError):
                    self.validate(profile)

    def test_invalid_ids_are_rejected_before_filesystem_resolution(self) -> None:
        for identifier in ("", "Demo", "2demo", "../demo", "a/b", "a\\b", "a:b", ".", "a" * 65, None, True, []):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ConfigError):
                    require_id(identifier)
                with self.assertRaises(ConfigError):
                    DataRepository(Path("unused-repository")).load_profile(identifier)
        for identifier in ("a", "toy_brand-2", "a" * 64):
            require_id(identifier)

    def test_name_must_match_requested_profile(self) -> None:
        with self.assertRaises(ConfigError):
            validate_profile(dual_profile(), "other", Path("other.json"))

    def test_display_name_must_be_nonempty_text(self) -> None:
        for display_name in (None, "", "  \t", 3, [], {}):
            with self.subTest(display_name=display_name):
                profile = dual_profile()
                profile["display_name"] = display_name
                with self.assertRaises(ConfigError):
                    self.validate(profile)

    def test_invalid_v1_kind_uses_config_error_even_for_unhashable_json(self) -> None:
        for kind in (None, "reader", "both", 1, False, ["source"], {"source": True}):
            with self.subTest(kind=kind):
                profile = legacy_profile()
                profile["kind"] = kind
                with self.assertRaises(ConfigError):
                    self.validate(profile)

    def test_invalid_adapter_shapes_including_null_roles_are_rejected(self) -> None:
        for adapters in (None, {}, [], "source", {"reader": {}}, {"source": None}, {"target": None}, {"source": []}):
            with self.subTest(adapters=adapters):
                profile = dual_profile()
                profile["adapters"] = adapters
                with self.assertRaises(ConfigError):
                    self.validate(profile)
        profile = dual_profile()
        profile["adapters"]["source"] = None
        with self.assertRaises(ConfigError):
            self.validate(profile)

    def test_v1_and_v2_declarations_cannot_be_mixed(self) -> None:
        for key, value in (("kind", "source"), ("implementation", "tests.test_profiles:ProfileReader")):
            with self.subTest(key=key):
                profile = dual_profile()
                profile[key] = value
                with self.assertRaises(ConfigError):
                    self.validate(profile)

    def test_implementation_references_have_a_restricted_shape(self) -> None:
        for spec in (None, 1, [], "", "no_separator", ":Reader", "module:", "../module:Reader", "module:Outer.Reader", "module:Reader()"):
            with self.subTest(spec=spec):
                profile = dual_profile()
                profile["adapters"]["source"]["implementation"] = spec
                with self.assertRaises(ConfigError):
                    self.validate(profile)
        profile = legacy_profile()
        del profile["implementation"]
        with self.assertRaises(ConfigError):
            self.validate(profile)

    def test_modules_must_be_nonempty_unique_valid_ids(self) -> None:
        for modules in (None, [], "icons", ["icons", "icons"], ["Icons"], ["../wallpapers"], [1], [True], [None], [{}]):
            with self.subTest(modules=modules):
                profile = dual_profile()
                profile["adapters"]["source"]["modules"] = modules
                with self.assertRaises(ConfigError):
                    self.validate(profile)

    def test_invalid_input_extensions_are_config_errors(self) -> None:
        for extensions in (None, [], ".toy", [None], [1], ["toy"], ["."], ["..toy"], [".toy/evil"], [".toy\\evil"], [".toy:ads"], [".a b"], [".toolong123456789012345"]):
            with self.subTest(extensions=extensions):
                profile = dual_profile()
                profile["input_extensions"] = extensions
                with self.assertRaises(ConfigError):
                    self.validate(profile)

    def test_invalid_output_extensions_are_config_errors(self) -> None:
        for extension in (None, [], 3, "", "toy", ".", "../toy", ".toy/evil", ".toy\\evil", ".toy:ads", ".a b", ".a\0", ".toolong123456789012345"):
            with self.subTest(extension=extension):
                profile = dual_profile()
                profile["output_extension"] = extension
                with self.assertRaises(ConfigError):
                    self.validate(profile)

    def test_output_suffixes_cannot_escape_directories(self) -> None:
        for key in ("output_suffix", "report_suffix"):
            for value in (None, 4, "../evil", "\\evil", "bad:name", "bad\0name"):
                with self.subTest(key=key, value=value):
                    profile = dual_profile()
                    profile[key] = value
                    with self.assertRaises(ConfigError):
                        self.validate(profile)

    def test_registry_rejects_missing_or_nonclass_implementations(self) -> None:
        for spec in (
            "themeshift_nonexistent_adapter:Reader",
            "tests.test_profiles:NoSuchAdapter",
            "tests.test_profiles:dual_profile",
        ):
            with self.subTest(spec=spec), self.assertRaises(ConfigError):
                registry.load_implementation(spec)

    def test_registry_checks_declared_role_methods(self) -> None:
        profile = dual_profile()
        profile["adapters"]["source"]["implementation"] = "tests.test_profiles:EmptyAdapter"
        with self.assertRaises(ConfigError):
            registry.create_source(profile)
        profile["adapters"]["target"]["implementation"] = "tests.test_profiles:ProfileReader"
        with self.assertRaises(ConfigError):
            registry.create_target(profile)
        with self.assertRaises(ConfigError):
            registry.create_target(legacy_profile())

    def test_unknown_adapter_role_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            adapter_config(dual_profile(), "reader")


class ProfileRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "profiles").mkdir()
        (self.root / "mappings").mkdir()
        self.repository = DataRepository.create(self.root)

    def write_profile(self, profile: dict) -> Path:
        path = self.root / "profiles" / f"{profile['name']}.json"
        path.write_text(json.dumps(profile), encoding="utf-8")
        return path

    def write_mapping(self, source: str, target: str, payload: dict) -> Path:
        path = self.root / "mappings" / f"{source}_to_{target}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_available_roles_mix_v1_and_v2_without_duplicate_brand_ids(self) -> None:
        self.write_profile(legacy_profile("oldreader", "source"))
        self.write_profile(legacy_profile("oldwriter", "target"))
        self.write_profile(dual_profile("dual"))
        self.assertEqual(self.repository.profile_names(), ["dual", "oldreader", "oldwriter"])
        self.assertEqual(self.repository.available("source"), ["dual", "oldreader"])
        self.assertEqual(self.repository.available("target"), ["dual", "oldwriter"])
        self.assertEqual(self.repository.load_profile("dual")["schema_version"], 2)

    def test_repository_rejects_nonobject_malformed_and_non_utf8_profiles(self) -> None:
        path = self.root / "profiles" / "bad.json"
        for payload in (b"[]", b"null", b"42", b"{", b"\xff"):
            with self.subTest(payload=payload):
                path.write_bytes(payload)
                with self.assertRaises(ConfigError):
                    self.repository.load_profile("bad")

    def test_reverse_mapping_is_never_inferred_from_forward_file(self) -> None:
        forward = self.write_mapping("alpha", "beta", {"source": "alpha", "target": "beta"})
        self.assertEqual(self.repository.mapping_path("alpha", "beta").resolve(), forward.resolve())
        with self.assertRaises(ConfigError):
            self.repository.mapping_path("beta", "alpha")
        reverse = self.write_mapping("beta", "alpha", {"source": "beta", "target": "alpha"})
        self.assertEqual(self.repository.mapping_path("beta", "alpha").resolve(), reverse.resolve())
        self.assertNotEqual(forward, reverse)

    def test_mapping_direction_fields_must_match_filename_and_request(self) -> None:
        for payload in (
            {"source": "beta", "target": "alpha"},
            {"source": "other", "target": "beta"},
            {"source": "alpha", "target": "other"},
            {"source": "alpha"},
            {"target": "beta"},
            {"source": ["alpha"], "target": "beta"},
        ):
            with self.subTest(payload=payload):
                self.write_mapping("alpha", "beta", payload)
                with self.assertRaises(ConfigError):
                    self.repository.mapping_path("alpha", "beta")

    def test_mapping_ids_cannot_traverse_the_repository(self) -> None:
        for source, target in (("../alpha", "beta"), ("alpha", "../beta"), ("alpha", "b\\eta")):
            with self.subTest(source=source, target=target), self.assertRaises(ConfigError):
                self.repository.mapping_path(source, target)


if __name__ == "__main__":
    unittest.main()
