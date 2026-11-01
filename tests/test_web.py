"""Real ASGI API tests with synthetic themes; optional when .[web] is absent."""

from __future__ import annotations

import io
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tests.support import build_fake_mtz, solid_png_bytes
from themeshift.service import MigrationService

try:
    from fastapi.testclient import TestClient

    from themeshift.web.app import create_app
    from themeshift.web.settings import WebSettings
except ImportError:
    WEB_AVAILABLE = False
else:
    WEB_AVAILABLE = True

MUTATION = {"X-ThemeShift-Request": "1"}


@unittest.skipUnless(WEB_AVAILABLE, 'Install .[web,dev] to run API tests')
class WebApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.service = MigrationService()
        self.payload = build_fake_mtz(self.root / "sample.mtz").read_bytes()
        self.make_client()

    def make_client(self, **options) -> None:
        self.settings = WebSettings(storage_dir=self.root / "web", **options)
        self.app = create_app(self.settings, self.service)
        self.client = self.contexts.enter_context(TestClient(self.app, base_url="http://127.0.0.1"))
        self.manager = self.app.state.jobs

    def upload(self, *, filename="主题.mtz", payload=None, **data):
        return self.client.post(
            "/api/jobs", headers=MUTATION,
            files={"file": (filename, self.payload if payload is None else payload, "application/octet-stream")},
            data={"target": "honor", **data},
        )

    def finish(self, response):
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            poll = self.client.get(f"/api/jobs/{job_id}")
            self.assertEqual(poll.status_code, 200, poll.text)
            job = poll.json()
            if job["status"] in {"succeeded", "failed"}:
                return job
            threading.Event().wait(0.01)
        self.fail("background conversion did not finish")

    def test_health_catalog_static_ui_and_security_headers(self) -> None:
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")
        catalog = self.client.get("/api/platforms").json()
        self.assertEqual(catalog["routes"], [{"source": "xiaomi", "target": "honor", "modules": ["dynamic_icons", "icons"]}])
        self.assertNotIn("implementation", str(catalog))
        self.assertEqual(catalog["limits"]["workers"], 2)
        page = self.client.get("/")
        self.assertIn("/static/app.js", page.text)
        self.assertIn('href="https://www.mtztheme.com/"', page.text)
        self.assertIn('target="_blank"', page.text)
        self.assertIn('rel="noopener noreferrer"', page.text)
        for path in ("/", "/api/health", "/static/app.css", "/static/app.js"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(self.client.get("/api/openapi.json").status_code, 200)

    def test_upload_convert_download_and_delete(self) -> None:
        job = self.finish(self.upload(name="中文 & <主题>", author="自定义作者", version_number="2.1"))
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["progress"], 100)
        result = job["result"]
        self.assertEqual(result["meta"]["title"], "中文 & <主题>")
        self.assertEqual(result["meta"]["designer"], "自定义作者")
        self.assertEqual(result["static_icons"], 3)
        self.assertEqual(result["stats"]["passthrough"], 2)
        self.assertTrue(result["validation"]["ok"])
        artifact = self.client.get(result["artifacts"]["theme"]["url"])
        self.assertEqual(artifact.status_code, 200)
        self.assertIn("attachment", artifact.headers["content-disposition"])
        self.assertIn("filename*=utf-8", artifact.headers["content-disposition"])
        with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
            self.assertIn("icons", archive.namelist())
            self.assertIn("中文 &amp; &lt;主题&gt;", archive.read("description.xml").decode())
        mapping = self.client.get(result["artifacts"]["report"]["url"])
        self.assertIn("com.hihonor.calculator", mapping.text)
        self.assertNotIn(str(self.root), str(job))
        self.assertFalse((self.manager.root / job["id"] / "input").exists())
        self.assertFalse((self.manager.root / job["id"] / "work").exists())
        deletion = self.client.delete(f"/api/jobs/{job['id']}", headers=MUTATION)
        self.assertEqual(deletion.status_code, 204)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}").status_code, 404)
        self.assertEqual(self.client.get(result["artifacts"]["theme"]["url"]).status_code, 404)

    def test_icon_margin_reaches_the_honor_writer(self) -> None:
        job = self.finish(self.upload(icon_margin="72"))
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["result"]["icon_margin"], 72)
        artifact = self.client.get(job["result"]["artifacts"]["theme"]["url"])
        with zipfile.ZipFile(io.BytesIO(artifact.content)) as outer, zipfile.ZipFile(io.BytesIO(outer.read("icons"))) as icons:
            name = next(n for n in icons.namelist() if n.endswith(".png") and not n.startswith("dynamic_icons/"))
            with Image.open(io.BytesIO(icons.read(name))) as image:
                box = image.convert("RGBA").getchannel("A").getbbox()
        self.assertEqual(box, (72, 72, 120, 120))

    def test_out_of_range_icon_margin_is_rejected_before_conversion(self) -> None:
        response = self.upload(icon_margin="73")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.manager._inflight, 0)
        self.assertEqual(list(self.manager.root.iterdir()), [])

    def test_dry_run_has_statistics_without_artifacts_or_validation(self) -> None:
        job = self.finish(self.upload(dry_run="true"))
        self.assertEqual(job["status"], "succeeded")
        self.assertTrue(job["dry_run"])
        self.assertIsNone(job["result"]["validation"])
        self.assertEqual(job["result"]["artifacts"], {})
        self.assertGreater(job["result"]["stats"]["mapped"], 0)
        self.assertTrue(any("尚未" in text for text in job["result"]["warnings"]))
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/artifacts/theme").status_code, 404)

    def test_repeat_inputs_are_isolated_and_deterministic(self) -> None:
        first = self.finish(self.upload())
        second = self.finish(self.upload())
        self.assertNotEqual(first["id"], second["id"])
        files = [self.client.get(job["result"]["artifacts"]["theme"]["url"]).content for job in (first, second)]
        self.assertEqual(files[0], files[1])

    def test_corrupt_archive_and_image_fail_without_downloads(self) -> None:
        bad_image = build_fake_mtz(self.root / "broken.mtz", icons={"com.test.app": b"not png"}).read_bytes()
        for payload in (b"not zip", bad_image):
            with self.subTest(payload=payload[:10]):
                job = self.finish(self.upload(payload=payload))
                self.assertEqual(job["status"], "failed")
                self.assertEqual(job["error"]["code"], "source_format")
                self.assertIsNone(job["result"])
                for root in (str(self.root), str(self.root).replace("\\", "\\\\"), self.root.as_posix()):
                    self.assertNotIn(root, str(job))
                self.assertFalse((self.manager.root / job["id"] / "input").exists())
                self.assertFalse((self.manager.root / job["id"] / "artifacts").exists())
                self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/artifacts/theme").status_code, 404)

    def test_archive_path_attack_is_a_failed_job(self) -> None:
        payload = build_fake_mtz(self.root / "evil.mtz", icons={"../../outside": solid_png_bytes()}).read_bytes()
        job = self.finish(self.upload(payload=payload))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "source_format")
        self.assertFalse((self.root / "outside.png").exists())

    def test_bad_parameters_are_rejected_and_reservations_released(self) -> None:
        cases = [
            {"source": "honor"}, {"source": "../../outside"}, {"target": "xiaomi"},
            {"name": "x" * 201}, {"name": "bad\x00title"}, {"dry_run": "not-bool"},
            {"brief": "x" * 2001}, {"unknown": "field"},
        ]
        for data in cases:
            with self.subTest(data=data):
                response = self.upload(**data)
                self.assertIn(response.status_code, (400, 422), response.text)
                self.assertIn("error", response.json())
                self.assertEqual(self.manager._inflight, 0)
                self.assertEqual(self.manager._jobs, {})
                self.assertEqual(list(self.manager.root.iterdir()), [])

    def test_empty_unsupported_and_unsafe_filenames(self) -> None:
        for filename, payload in (("theme.mtz", b""), ("theme.hnt", b"x"), ("../theme.mtz", b"x"), ("a:b.mtz", b"x"), ("a" * 181 + ".mtz", b"x")):
            with self.subTest(filename=filename):
                response = self.upload(filename=filename, payload=payload)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.manager._inflight, 0)

    def test_one_file_only_and_no_duplicate_fields(self) -> None:
        cases = [
            [("other", ("file.mtz", self.payload))],
            [("file", ("a.mtz", self.payload)), ("file", ("b.mtz", self.payload))],
            [("file", ("a.mtz", self.payload)), ("target", (None, "honor")), ("target", (None, "honor"))],
        ]
        for files in cases:
            response = self.client.post("/api/jobs", headers=MUTATION, files=files)
            self.assertIn(response.status_code, (400, 422), response.text)
            self.assertEqual(self.manager._inflight, 0)
        response = self.client.post("/api/jobs", headers=MUTATION, json={"target": "honor"})
        self.assertEqual(response.status_code, 415)

    def test_host_csrf_origin_and_fetch_site_checks(self) -> None:
        self.assertEqual(self.client.get("/api/health", headers={"Host": "evil.example"}).status_code, 400)
        self.assertEqual(self.client.post("/api/jobs").status_code, 403)
        for headers in ({**MUTATION, "Origin": "https://evil.example"}, {**MUTATION, "Sec-Fetch-Site": "cross-site"}, {**MUTATION, "Origin": "null"}):
            response = self.client.post("/api/jobs", headers=headers)
            self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/jobs", headers={**MUTATION, "Origin": "http://127.0.0.1"}, json={})
        self.assertEqual(response.status_code, 415)  # passed same-origin check
        self.assertEqual(self.manager._jobs, {})

    def test_file_size_and_declared_body_size_limits(self) -> None:
        self.make_client(max_upload_bytes=128)
        self.assertEqual(self.upload(payload=b"x" * 129).status_code, 413)
        self.assertEqual(self.manager._jobs, {})
        response = self.client.post("/api/jobs", headers={**MUTATION, "Content-Length": "70000"}, content=b"x")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.manager._jobs, {})

    def test_streamed_body_limit_does_not_trust_content_length(self) -> None:
        self.make_client(max_upload_bytes=128)
        def parts():
            yield b'--abc\r\nContent-Disposition: form-data; name="file"; filename="a.mtz"\r\n\r\n'
            for _ in range(5):
                yield b"x" * 16000
            yield b"\r\n--abc--\r\n"
        response = self.client.post(
            "/api/jobs", headers={**MUTATION, "Content-Type": "multipart/form-data; boundary=abc"},
            content=parts(),
        )
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.manager._inflight, 0)
        self.assertEqual(list(self.manager.root.iterdir()), [])

    def test_queue_capacity_active_deletion_and_expiry(self) -> None:
        self.make_client(workers=1, max_pending=1)
        started, release = threading.Event(), threading.Event()
        original = self.service.migrate
        def gated(request, progress):
            started.set()
            if not release.wait(5):
                raise AssertionError("test did not release worker")
            return original(request, progress)
        with patch.object(self.service, "migrate", side_effect=gated):
            try:
                response = self.upload()
                self.assertTrue(started.wait(2))
                job_id = response.json()["id"]
                busy = self.upload()
                self.assertEqual(busy.status_code, 429)
                self.assertEqual(busy.headers["retry-after"], "5")
                self.assertEqual(self.client.delete(f"/api/jobs/{job_id}", headers=MUTATION).status_code, 409)
                self.manager.cleanup(time.time() + 100000)
                self.assertEqual(self.client.get(f"/api/jobs/{job_id}").status_code, 200)
                self.assertEqual(self.client.get("/api/health").status_code, 200)
            finally:
                release.set()
            self.assertEqual(self.finish(response)["status"], "succeeded")

    def test_retained_capacity_is_released_by_deleting_job(self) -> None:
        self.make_client(max_jobs=1, max_pending=1)
        job = self.finish(self.upload(dry_run="true"))
        self.assertEqual(self.upload().status_code, 429)
        self.client.delete(f"/api/jobs/{job['id']}", headers=MUTATION)
        self.assertEqual(self.finish(self.upload(dry_run="true"))["status"], "succeeded")

    def test_download_lease_protects_expiring_files(self) -> None:
        job = self.finish(self.upload())
        path = self.manager.acquire_artifact(job["id"], "theme")
        try:
            self.manager.cleanup(time.time() + 100000)
            self.assertTrue(path.exists())
            self.assertEqual(self.client.delete(f"/api/jobs/{job['id']}", headers=MUTATION).status_code, 409)
        finally:
            self.manager.release_artifact(job["id"])
        self.manager.cleanup(time.time() + 100000)
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}").status_code, 404)

    def test_no_global_listing_or_arbitrary_file_download(self) -> None:
        self.assertEqual(self.client.get("/api/jobs").status_code, 405)
        self.assertEqual(self.client.get("/api/jobs/invalid").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/" + "0" * 32).status_code, 404)
        job = self.finish(self.upload())
        for kind in ("input", "work", "description.xml", "unknown"):
            self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/artifacts/{kind}").status_code, 404)
        self.assertEqual(self.client.get("/static/%2E%2E/%2E%2E/pyproject.toml").status_code, 404)

    def test_malformed_and_truncated_multipart_are_client_errors(self) -> None:
        bodies = [
            b"garbage", b'--abc\r\nContent-Disposition: form-data; name="file"; filename="a.mtz"\r\n\r\nunfinished',
            b'--abc\r\nContent-Disposition: form-data; name="file"; filename="' + b"x" * 17000 + b'"\r\n\r\nx\r\n--abc--\r\n',
        ]
        for body in bodies:
            with self.subTest(body=body[:20]):
                response = self.client.post("/api/jobs", headers={**MUTATION, "Content-Type": "multipart/form-data; boundary=abc"}, content=body)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["error"]["code"], "invalid_multipart")
                self.assertEqual(self.manager._inflight, 0)
                self.assertEqual(list(self.manager.root.iterdir()), [])

    def test_abandoned_upload_cleanup_retries_without_leaking_slots(self) -> None:
        job_id, directory = self.manager.reserve()
        (directory / "partial").write_text("partial", encoding="utf-8")
        with patch.object(self.manager, "_remove", side_effect=PermissionError("busy file")), self.assertLogs("themeshift.web.jobs", level="ERROR"):
            self.manager.abandon(job_id)
            self.assertEqual(self.manager._inflight, 0)
            self.assertEqual(self.manager._jobs[job_id].status, "failed")
            self.manager.abandon(job_id)
            self.assertEqual(self.manager._inflight, 0)
            self.manager.cleanup(time.time() + 1)
        self.manager.cleanup(time.time() + 1)
        self.assertFalse(directory.exists())
        self.assertEqual(self.manager._jobs, {})

    def test_close_waits_for_existing_download_lease(self) -> None:
        job = self.finish(self.upload())
        path = self.manager.acquire_artifact(job["id"], "theme")
        finished = threading.Event()
        def close_manager():
            self.manager.close()
            finished.set()
        thread = threading.Thread(target=close_manager)
        thread.start()
        try:
            self.assertFalse(finished.wait(0.1))
            self.assertTrue(path.exists())
        finally:
            self.manager.release_artifact(job["id"])
            thread.join(timeout=5)
        self.assertTrue(finished.is_set())
        self.assertFalse(path.exists())

    def test_shutdown_cleans_only_its_owned_session(self) -> None:
        marker = self.settings.storage_dir / "unrelated.txt"
        marker.write_text("keep", encoding="utf-8")
        session = self.manager.root
        self.finish(self.upload())
        self.contexts.close()
        self.assertFalse(session.exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
