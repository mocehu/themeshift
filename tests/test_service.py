"""Transport-neutral service tests using small, plain-text toy adapters."""

from __future__ import annotations

import builtins
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from themeshift import service as service_module
from themeshift.errors import BuildValidationError, ConfigError, SourceFormatError, UsageError
from themeshift.mapping import PackageMap
from themeshift.models import (
    BuildReport,
    DynamicAsset,
    NormalizedTheme,
    SourceTheme,
    StaticIcon,
    ThemeMeta,
    ThemeResource,
)
from themeshift.repository import DataRepository
from themeshift.service import MetadataOverrides, MigrationRequest, MigrationService


class ToyReader:
    """Read plain text into the neutral IR; no image, ZIP or vendor dependency."""

    on_load = None

    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self.name = profile["name"]

    def load(self, package_path: Path, work_dir: Path) -> SourceTheme:
        work_dir.mkdir(parents=True, exist_ok=True)
        asset = work_dir / "resource.txt"
        asset.write_text(package_path.read_text(encoding="utf-8"), encoding="utf-8")
        callback = type(self).on_load
        if callback:
            callback(self, package_path, work_dir)
        if self.profile.get("test_fail_load"):
            raise SourceFormatError("toy reader failure")
        return SourceTheme(
            meta=ThemeMeta(title=self.profile.get("test_title", "Shared theme"), designer="Toy author"),
            static_icons=[] if self.profile.get("test_no_icons") else [StaticIcon(f"com.{self.name}.system", asset)],
            dynamic_assets=[DynamicAsset(f"com.{self.name}.clock", "frame.png", asset)] if self.profile.get("test_dynamic") else [],
            source_name=self.name,
            source_path=package_path,
            schema_version=self.profile.get("test_schema_version", 1),
            modules={
                module: [ThemeResource("main", asset, "text/plain", {"source": self.name})]
                for module in self.profile.get("test_modules", [])
            },
        )


class ToyWriter:
    """Write text and named checks: service validation must not assume HONOR."""

    on_build = None

    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self.name = profile["name"]

    def build(self, theme: NormalizedTheme, out_path: Path, work_dir: Path) -> BuildReport:
        callback = type(self).on_build
        if callback:
            callback(self, theme, out_path, work_dir)
        payload = {
            "writer": self.name,
            "title": theme.meta.title,
            "icons": {package: path.read_text(encoding="utf-8") for package, path in theme.static_icons.items()},
            "dynamic": [[package, filename] for package, filename, _ in theme.dynamic_assets],
            "modules": {
                module: [
                    {"key": asset.key, "text": asset.path.read_text(encoding="utf-8"), "attributes": asset.attributes}
                    for asset in resources
                ]
                for module, resources in theme.modules.items()
            },
        }
        if not self.profile.get("test_no_output"):
            out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        if self.profile.get("test_fail_build"):
            raise RuntimeError("toy writer failure after staging")
        return BuildReport(
            output=out_path,
            static_icons=len(theme.static_icons),
            dynamic_assets=len(theme.dynamic_assets),
            checks={"toy-format": self.profile.get("test_valid", True)},
        )


class WallpaperTransformer:
    """Interpret an explicitly opted-in module, never copy unknown IR blindly."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def transform(self, source: SourceTheme, target: NormalizedTheme, mapping: PackageMap) -> None:
        self.calls.append((mapping.source, mapping.target))
        target.modules["wallpapers"] = [
            ThemeResource(
                key=f"{mapping.target}.{asset.key}",
                path=asset.path,
                media_type=asset.media_type,
                attributes={**asset.attributes, "route": f"{mapping.source}->{mapping.target}"},
            )
            for asset in source.modules.get("wallpapers", [])
        ]
        target.note("wallpapers: explicitly transformed")


def toy_profile(name: str) -> dict:
    return {
        "schema_version": 2,
        "name": name,
        "display_name": f"Toy {name}",
        "adapters": {
            "source": {"implementation": "tests.test_service:ToyReader", "modules": ["icons"]},
            "target": {"implementation": "tests.test_service:ToyWriter", "modules": ["icons"]},
        },
        "input_extensions": [f".{name}"],
        "output_extension": f".{name}",
        "output_suffix": "",
        "report_suffix": "_map",
    }


class MetadataRulesTest(unittest.TestCase):
    def test_overrides_copy_metadata_and_preserve_unset_fields(self) -> None:
        original = ThemeMeta("Original", "Designer", version="1")
        result = MetadataOverrides(title="新标题 & <art>", title_en="New", designer="").apply(original)
        self.assertEqual(result.title, "新标题 & <art>")
        self.assertEqual(result.title_en, "New")
        self.assertEqual(result.designer, "Designer")
        self.assertEqual(result.version, "1")
        self.assertEqual(original.title, "Original")
        self.assertIsNot(result, original)

    def test_xml_forbidden_control_characters_are_rejected_in_overrides(self) -> None:
        for character in ("\0", "\x01", "\x08", "\x0b", "\x1f", "\ud800", "\udfff", "\ufffe", "\uffff"):
            with self.subTest(character=repr(character)), self.assertRaises(UsageError):
                MetadataOverrides(title=f"bad{character}title").apply(ThemeMeta("Good"))

    def test_source_metadata_is_validated_even_without_overrides(self) -> None:
        with self.assertRaises(UsageError):
            MetadataOverrides().apply(ThemeMeta("bad\0title"))

    def test_xml_whitespace_and_supplementary_unicode_remain_valid(self) -> None:
        text = "标题\tline\nnext\r😀"
        self.assertEqual(MetadataOverrides(title=text).apply(ThemeMeta("Old")).title, text)

    def test_metadata_field_lengths_are_bounded(self) -> None:
        for field, maximum in (("title", 200), ("designer", 200), ("version", 200), ("brief_info", 2000), ("brief_info_en", 2000)):
            with self.subTest(field=field):
                result = MetadataOverrides(**{field: "x" * maximum}).apply(ThemeMeta("Old"))
                self.assertEqual(len(getattr(result, field)), maximum)
                with self.assertRaises(UsageError):
                    MetadataOverrides(**{field: "x" * (maximum + 1)}).apply(ThemeMeta("Old"))

    def test_generic_build_checks_do_not_require_honor_fields(self) -> None:
        report = BuildReport(Path("toy.output"), checks={"plain-text-format": True})
        self.assertTrue(report.ok)
        self.assertFalse(report.description_lf)
        self.assertIsNone(report.extra_content_flag)
        self.assertTrue(report.summary_lines())
        self.assertFalse(BuildReport(Path("toy.output"), checks={"not-a-boolean": 1}).ok)
        self.assertFalse(BuildReport(Path("toy.output"), checks={"format": True}, issues=["bad content"]).ok)


class MarginToyWriter(ToyWriter):
    """Opt-in writer proving that generic build options reach the adapter."""

    margins: list[int | None] = []

    def build(self, theme, out_path, work_dir, options=None) -> BuildReport:
        type(self).margins.append(None if options is None else options.get("icon_margin"))
        return super().build(theme, out_path, work_dir)


class MigrationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "profiles").mkdir()
        (self.root / "mappings").mkdir()
        self.alpha = toy_profile("alpha")
        self.beta = toy_profile("beta")
        self.write_profile(self.alpha)
        self.write_profile(self.beta)
        self.write_mapping("alpha", "beta")
        self.repository = DataRepository.create(self.root)
        self.service = MigrationService(self.repository)
        self.input = self.root / "theme.alpha"
        self.input.write_text("input-alpha", encoding="utf-8")
        self.work = self.root / "work"
        self.output = self.root / "outputs"

    def write_profile(self, profile: dict) -> None:
        path = self.root / "profiles" / f"{profile['name']}.json"
        path.write_text(json.dumps(profile), encoding="utf-8")

    def write_mapping(self, source: str, target: str, target_package: str | None = None) -> None:
        data = {
            "source": source,
            "target": target,
            "source_system": {"prefixes": [f"com.{source}."], "exact": []},
            "system_app_map": {target_package or f"com.{target}.translated": [f"com.{source}.system"]},
            "dynamic_assets": [{"source": f"com.{source}.clock/frame.png", "target": f"com.{target}.clock/clock.png"}],
        }
        path = self.root / "mappings" / f"{source}_to_{target}.json"
        path.write_text(json.dumps(data), encoding="utf-8")

    def request(self, **changes) -> MigrationRequest:
        values = {
            "input_path": self.input,
            "source": "alpha",
            "target": "beta",
            "work_dir": self.work,
            "output_dir": self.output,
        }
        values.update(changes)
        return MigrationRequest(**values)

    def read_output(self, result) -> dict:
        self.assertIsNotNone(result.output_path)
        return json.loads(result.output_path.read_text(encoding="utf-8"))

    def enable_wallpapers(self) -> None:
        self.alpha["adapters"]["source"]["modules"] = ["icons", "wallpapers"]
        self.alpha["test_modules"] = ["wallpapers"]
        self.beta["adapters"]["target"]["modules"] = ["icons", "wallpapers"]
        self.write_profile(self.alpha)
        self.write_profile(self.beta)

    def test_icon_margin_is_offered_only_by_opt_in_icon_targets(self) -> None:
        self.beta["icon"] = {"canvas": 192, "content": 172}
        self.beta["adapters"]["target"]["implementation"] = "tests.test_service:MarginToyWriter"
        self.write_profile(self.beta)
        platform = next(
            item for item in MigrationService(self.repository).catalog()["platforms"]
            if item["id"] == "beta"
        )
        self.assertEqual(
            platform["icon_margin"],
            {"min": 0, "max": 72, "default": 10, "canvas": 192, "unit": "px"},
        )
        MarginToyWriter.margins.clear()
        result = MigrationService(self.repository).migrate(self.request(icon_margin=4))
        self.assertEqual(result.icon_margin, 4)
        self.assertEqual(MarginToyWriter.margins, [4])
        self.assertTrue(result.validation.ok)

    def test_icon_margin_requires_opt_in_writer_and_profile_bounds(self) -> None:
        self.beta["icon"] = {"canvas": 192, "content": 172}
        self.write_profile(self.beta)
        with self.assertRaises(UsageError):
            MigrationService(self.repository).migrate(self.request(icon_margin=4))
        self.assertFalse(self.output.exists())

        self.beta["adapters"]["target"]["implementation"] = "tests.test_service:MarginToyWriter"
        self.write_profile(self.beta)
        service = MigrationService(self.repository)
        for value in (-1, 73, True, 4.5):
            with self.subTest(value=value), self.assertRaises(UsageError):
                service.migrate(self.request(icon_margin=value))
        self.assertEqual(list(self.work.iterdir()) if self.work.exists() else [], [])
        self.assertFalse(self.output.exists())

    def test_service_builds_a_generic_verified_artifact_without_vendor_assumptions(self) -> None:
        progress = []
        result = self.service.migrate(self.request(), progress=lambda percent, message: progress.append(percent))
        payload = self.read_output(result)
        self.assertEqual(payload["writer"], "beta")
        self.assertEqual(payload["icons"], {"com.beta.translated": "input-alpha"})
        self.assertEqual(result.plan.modules, ("icons",))
        self.assertEqual(result.static_icons, 1)
        self.assertEqual(result.stats.mapped, 1)
        self.assertTrue(result.validation.ok)
        self.assertFalse(result.validation.description_lf)
        self.assertIsNone(result.validation.extra_content_flag)
        self.assertEqual(result.validation.output, result.output_path)
        self.assertTrue(result.report_path.is_file())
        self.assertIn("com.alpha.system -> com.beta.translated", result.report_path.read_text(encoding="utf-8"))
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(progress[-1], 100)
        self.assertIsNone(result.work_dir)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(self.input.read_text(encoding="utf-8"), "input-alpha")

    def test_service_remains_compatible_with_old_single_role_v1_profiles(self) -> None:
        for profile, role, implementation in (
            (self.alpha, "source", "tests.test_service:ToyReader"),
            (self.beta, "target", "tests.test_service:ToyWriter"),
        ):
            del profile["schema_version"]
            del profile["adapters"]
            profile.update(kind=role, implementation=implementation)
            self.write_profile(profile)
        result = self.service.migrate(self.request())
        self.assertEqual(set(result.plan.modules), {"icons", "dynamic_icons"})
        self.assertEqual(self.read_output(result)["icons"], {"com.beta.translated": "input-alpha"})

    def test_catalog_exposes_dual_roles_but_only_explicit_routes(self) -> None:
        catalog = self.service.catalog()
        self.assertEqual({platform["id"] for platform in catalog["platforms"]}, {"alpha", "beta"})
        self.assertTrue(all(platform["can_read"] and platform["can_write"] for platform in catalog["platforms"]))
        self.assertEqual(catalog["routes"], [{"source": "alpha", "target": "beta", "modules": ["icons"]}])
        with self.assertRaises(ConfigError):
            self.service.plan(self.request(source="beta", target="alpha"))
        self.assertFalse(self.output.exists())
        self.assertFalse(self.work.exists())

    def test_forward_and_reverse_use_distinct_explicit_mapping_data(self) -> None:
        self.write_mapping("beta", "alpha", "com.alpha.reverse_specific")
        reverse_input = self.root / "theme.beta"
        reverse_input.write_text("input-beta", encoding="utf-8")
        forward = self.service.migrate(self.request(output_dir=self.root / "forward"))
        reverse = self.service.migrate(self.request(input_path=reverse_input, source="beta", target="alpha", output_dir=self.root / "reverse"))
        self.assertEqual(self.read_output(forward)["icons"], {"com.beta.translated": "input-alpha"})
        self.assertEqual(self.read_output(reverse)["icons"], {"com.alpha.reverse_specific": "input-beta"})
        self.assertEqual({(route["source"], route["target"]) for route in self.service.catalog()["routes"]}, {("alpha", "beta"), ("beta", "alpha")})

    def test_same_brand_can_use_separate_reader_and_writer_with_an_explicit_mapping(self) -> None:
        self.write_mapping("alpha", "alpha", "com.alpha.same_brand")
        result = self.service.migrate(self.request(target="alpha"))
        self.assertEqual(self.read_output(result)["writer"], "alpha")
        self.assertEqual(self.read_output(result)["icons"], {"com.alpha.same_brand": "input-alpha"})

    def test_direction_header_mismatch_is_rejected_before_work_is_created(self) -> None:
        path = self.root / "mappings" / "alpha_to_beta.json"
        path.write_text(json.dumps({"source": "beta", "target": "alpha"}), encoding="utf-8")
        with self.assertRaises(ConfigError):
            self.service.migrate(self.request())
        self.assertFalse(self.work.exists())
        self.assertFalse(self.output.exists())

    def test_extension_detection_is_case_insensitive_and_ambiguity_requires_a_choice(self) -> None:
        self.assertEqual(self.service.detect_source(Path("theme.ALPHA")), "alpha")
        self.beta["input_extensions"] = [".ALPHA"]
        self.write_profile(self.beta)
        with self.assertRaises(UsageError):
            self.service.detect_source(self.input)
        self.assertEqual(self.service.detect_source(self.input, "alpha"), "alpha")
        self.assertEqual(self.service.detect_source(self.input, "beta"), "beta")
        with self.assertRaises(UsageError):
            self.service.detect_source(Path("theme.unknown"))
        with self.assertRaises(UsageError):
            self.service.detect_source(self.input, "missing")

    def test_requested_roles_must_exist(self) -> None:
        del self.beta["adapters"]["target"]
        self.write_profile(self.beta)
        with self.assertRaises(UsageError):
            self.service.plan(self.request())
        self.write_profile(toy_profile("beta"))
        del self.alpha["adapters"]["source"]
        self.write_profile(self.alpha)
        with self.assertRaises(UsageError):
            self.service.plan(self.request())

    def test_missing_duplicate_and_unsupported_modules_are_rejected(self) -> None:
        for modules in ((), ("icons", "icons"), ("dynamic_icons",), ("unknown",)):
            with self.subTest(modules=modules), self.assertRaises(UsageError):
                self.service.plan(self.request(modules=modules))
        self.assertFalse(self.work.exists())

    def test_unknown_modules_are_not_automatically_copied(self) -> None:
        self.enable_wallpapers()
        self.alpha["test_modules"].append("undeclared_payload")
        self.write_profile(self.alpha)
        result = self.service.migrate(self.request())
        self.assertEqual(result.plan.modules, ("icons",))
        self.assertEqual(self.read_output(result)["modules"], {})
        self.assertTrue(any("wallpapers" in warning for warning in result.warnings))
        with self.assertRaises(UsageError):
            self.service.plan(self.request(modules=("wallpapers",)))

    def test_custom_module_transformer_can_produce_non_icon_output(self) -> None:
        self.enable_wallpapers()
        self.alpha["test_no_icons"] = True
        self.write_profile(self.alpha)
        transformer = WallpaperTransformer()
        service = MigrationService(self.repository, transformers={"wallpapers": transformer})
        result = service.migrate(self.request(modules=("wallpapers",)))
        payload = self.read_output(result)
        self.assertEqual(transformer.calls, [("alpha", "beta")])
        self.assertEqual(result.plan.modules, ("wallpapers",))
        self.assertEqual(payload["icons"], {})
        self.assertEqual(payload["modules"]["wallpapers"], [{"key": "beta.main", "text": "input-alpha", "attributes": {"source": "alpha", "route": "alpha->beta"}}])
        self.assertEqual(result.static_icons, 0)
        self.assertTrue(result.validation.ok)
        self.assertIn("explicitly transformed", result.report_path.read_text(encoding="utf-8"))

    def test_transformer_names_are_checked_and_builtin_modules_cannot_be_replaced(self) -> None:
        for name in ("icons", "dynamic_icons", "../wallpapers", "Wallpaper"):
            with self.subTest(name=name), self.assertRaises(ConfigError):
                MigrationService(self.repository, transformers={name: WallpaperTransformer()})

    def test_selected_icon_modules_do_not_silently_include_dynamic_assets(self) -> None:
        self.alpha["adapters"]["source"]["modules"].append("dynamic_icons")
        self.beta["adapters"]["target"]["modules"].append("dynamic_icons")
        self.alpha["test_dynamic"] = True
        self.write_profile(self.alpha)
        self.write_profile(self.beta)
        result = self.service.migrate(self.request(modules=("icons",)))
        self.assertEqual(self.read_output(result)["dynamic"], [])
        self.assertEqual(result.dynamic_assets, 0)
        self.assertTrue(any("dynamic_icons" in warning for warning in result.warnings))

    def test_dry_run_does_not_build_or_publish_artifacts(self) -> None:
        self.work.mkdir()
        sentinel = self.work / "user-file.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with patch.object(ToyWriter, "build", side_effect=AssertionError("dry run must not build")):
            result = self.service.migrate(self.request(dry_run=True))
        self.assertIsNone(result.output_path)
        self.assertIsNone(result.report_path)
        self.assertIsNone(result.validation)
        self.assertIsNone(result.work_dir)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.work.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertTrue(any("自检" in warning for warning in result.warnings))

    def test_keep_work_retains_only_the_new_job_directory(self) -> None:
        result = self.service.migrate(self.request(dry_run=True, keep_work=True))
        self.assertIsNotNone(result.work_dir)
        self.assertEqual(result.work_dir.parent, self.work)
        self.assertTrue((result.work_dir / "source" / "resource.txt").is_file())
        self.assertFalse(self.output.exists())

    def test_reader_and_writer_failures_clean_only_their_job_directory(self) -> None:
        self.work.mkdir()
        sentinel = self.work / "user-file.txt"
        sentinel.write_text("keep", encoding="utf-8")
        unrelated = self.work / "unrelated-job"
        unrelated.mkdir()
        (unrelated / "asset.txt").write_text("another job", encoding="utf-8")
        observed = []
        for stage, expected in (("read", SourceFormatError), ("write", RuntimeError)):
            with self.subTest(stage=stage):
                self.alpha["test_fail_load"] = stage == "read"
                self.beta["test_fail_build"] = stage == "write"
                self.write_profile(self.alpha)
                self.write_profile(self.beta)
                with (
                    patch.object(ToyReader, "on_load", lambda reader, path, work: observed.append(work.parent)),
                    self.assertRaises(expected),
                ):
                    self.service.migrate(self.request())
                self.assertEqual({path.name for path in self.work.iterdir()}, {sentinel.name, unrelated.name})
                self.assertTrue(all(not path.exists() for path in observed))
                self.assertEqual((unrelated / "asset.txt").read_text(encoding="utf-8"), "another job")
                self.assertFalse(self.output.exists())

    def test_transformer_failure_also_cleans_job_work(self) -> None:
        self.enable_wallpapers()
        transformer = WallpaperTransformer()
        service = MigrationService(self.repository, transformers={"wallpapers": transformer})
        with (
            patch.object(transformer, "transform", side_effect=RuntimeError("transform failed")),
            self.assertRaises(RuntimeError),
        ):
            service.migrate(self.request(modules=("wallpapers",)))
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertFalse(self.output.exists())

    def test_concurrent_same_named_jobs_do_not_remove_each_others_work(self) -> None:
        first_input = self.root / "first" / "same.alpha"
        second_input = self.root / "second" / "same.alpha"
        for path, text in ((first_input, "one"), (second_input, "two")):
            path.parent.mkdir()
            path.write_text(text, encoding="utf-8")
        self.work.mkdir()
        sentinel = self.work / "user-file.txt"
        sentinel.write_text("keep", encoding="utf-8")
        barrier = threading.Barrier(2, timeout=10)
        first_finished = threading.Event()
        observed = []
        lock = threading.Lock()

        def loaded(reader, package_path, work_dir):
            with lock:
                observed.append(work_dir.parent)
            barrier.wait()

        def building(writer, theme, out_path, work_dir):
            asset = next(iter(theme.static_icons.values()))
            if asset.read_text(encoding="utf-8") == "two":
                if not first_finished.wait(timeout=10):
                    raise AssertionError("first job did not finish")
                self.assertTrue(asset.is_file(), "one job deleted the other job's resources")
                self.assertTrue(sentinel.is_file())

        def run_first():
            try:
                return self.service.migrate(self.request(input_path=first_input, output_dir=self.root / "result-one"))
            finally:
                first_finished.set()

        with (
            patch.object(ToyReader, "on_load", loaded),
            patch.object(ToyWriter, "on_build", building),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first_future = executor.submit(run_first)
            second_future = executor.submit(self.service.migrate, self.request(input_path=second_input, output_dir=self.root / "result-two"))
            first = first_future.result(timeout=20)
            second = second_future.result(timeout=20)
        self.assertEqual(len(set(observed)), 2)
        self.assertTrue(all(not path.exists() for path in observed))
        self.assertEqual(first.output_path.name, second.output_path.name)
        self.assertNotEqual(first.output_path, second.output_path)
        self.assertEqual(self.read_output(first)["icons"], {"com.beta.translated": "one"})
        self.assertEqual(self.read_output(second)["icons"], {"com.beta.translated": "two"})
        self.assertEqual(list(self.work.iterdir()), [sentinel])

    def test_failed_validation_or_missing_staged_file_preserves_existing_output(self) -> None:
        self.output.mkdir()
        existing = self.output / "protected.beta"
        report = self.output / "protected_map.txt"
        existing.write_bytes(b"original artifact")
        report.write_bytes(b"original report")
        for invalid_checks, missing_file in ((True, False), (False, True)):
            with self.subTest(invalid_checks=invalid_checks, missing_file=missing_file):
                self.beta["test_valid"] = not invalid_checks
                self.beta["test_no_output"] = missing_file
                self.write_profile(self.beta)
                with self.assertRaises(BuildValidationError):
                    self.service.migrate(self.request(output_path=existing))
                self.assertEqual(existing.read_bytes(), b"original artifact")
                self.assertEqual(report.read_bytes(), b"original report")
                self.assertEqual({path.name for path in self.output.iterdir()}, {existing.name, report.name})
                self.assertEqual(list(self.work.iterdir()), [])

    def test_artifact_and_report_cannot_publish_to_the_same_path(self) -> None:
        self.beta["output_extension"] = ".txt"
        self.beta["report_suffix"] = ""
        self.write_profile(self.beta)
        existing = self.root / "protected.txt"
        existing.write_bytes(b"original artifact")
        with self.assertRaises(UsageError):
            self.service.migrate(self.request(output_path=existing))
        self.assertEqual(existing.read_bytes(), b"original artifact")
        self.assertEqual(list(self.work.iterdir()), [])

    def test_output_cannot_overwrite_source_input(self) -> None:
        with self.assertRaises(UsageError):
            self.service.migrate(self.request(output_path=self.input))
        self.assertEqual(self.input.read_text(encoding="utf-8"), "input-alpha")
        self.assertEqual(list(self.work.iterdir()), [])

    def test_invalid_source_ir_version_is_rejected_and_cleaned(self) -> None:
        self.alpha["test_schema_version"] = 2
        self.write_profile(self.alpha)
        with self.assertRaises(SourceFormatError):
            self.service.migrate(self.request())
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertFalse(self.output.exists())
        with self.assertRaises(SourceFormatError):
            self.service.inspect(self.input, "alpha")

    def test_source_metadata_control_characters_are_rejected_before_writing(self) -> None:
        self.alpha["test_title"] = "bad\0title"
        self.write_profile(self.alpha)
        with (
            patch.object(ToyWriter, "build", side_effect=AssertionError("invalid metadata must not reach writer")),
            self.assertRaises(UsageError),
        ):
            self.service.migrate(self.request())
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertFalse(self.output.exists())

    def test_no_convertible_resources_is_an_explicit_error(self) -> None:
        self.alpha["test_no_icons"] = True
        self.write_profile(self.alpha)
        with self.assertRaises(UsageError):
            self.service.migrate(self.request())
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertFalse(self.output.exists())

    def test_missing_input_fails_before_creating_work_or_output(self) -> None:
        with self.assertRaises(UsageError):
            self.service.migrate(self.request(input_path=self.root / "missing.alpha"))
        self.assertFalse(self.work.exists())
        self.assertFalse(self.output.exists())

    def test_inspection_is_a_read_only_service_operation(self) -> None:
        observed = []
        with patch.object(ToyReader, "on_load", lambda reader, path, work: observed.append(work)):
            inspection = self.service.inspect(self.input)
        self.assertEqual(inspection.source, "alpha")
        self.assertEqual(inspection.packages, ("com.alpha.system",))
        self.assertEqual(inspection.static_icons, 1)
        self.assertTrue(all(not path.exists() for path in observed))
        self.assertFalse(self.output.exists())
        self.assertFalse(self.work.exists())

    def test_service_module_imports_and_runs_without_cli_or_web_transports(self) -> None:
        real_import = builtins.__import__

        def import_without_transports(name, globals=None, locals=None, fromlist=(), level=0):
            root = name.split(".", 1)[0]
            if root in {"argparse", "fastapi", "starlette", "uvicorn"} or name in {"themeshift.cli", "themeshift.web", "cli", "web"}:
                raise AssertionError(f"service unexpectedly imported transport: {name}")
            return real_import(name, globals, locals, fromlist, level)

        module_name = "themeshift._service_transport_test"
        spec = importlib.util.spec_from_file_location(module_name, service_module.__file__)
        isolated = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {module_name: isolated}), patch("builtins.__import__", import_without_transports):
            spec.loader.exec_module(isolated)
            result = isolated.MigrationService(self.repository).migrate(self.request(dry_run=True))
        self.assertEqual(result.plan.modules, ("icons",))
        self.assertIsNone(result.output_path)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
