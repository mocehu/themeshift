"""Optional real-browser smoke test; start ``themeshift serve`` separately.

Install Playwright for this developer check only (not a Web runtime dependency).
Use --browser msedge on Windows or install Chromium with playwright install.
All theme artwork is generated in memory by the existing synthetic test helper.
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    from playwright.sync_api import expect, sync_playwright

    from tests.support import build_fake_mtz

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--browser", default="msedge", help="Installed browser channel, or chromium")
    parser.add_argument("--out", type=Path, default=ROOT / ".artifacts")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    fixture = build_fake_mtz(args.out / "browser-fixture.mtz")
    title = "浏览器测试 <img src=x onerror=alert(1)>"
    with sync_playwright() as playwright:
        options = {} if args.browser == "chromium" else {"channel": args.browser}
        browser = playwright.chromium.launch(headless=True, **options)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda entry: errors.append(entry.text) if entry.type == "error" else None)
        page.goto(args.url, wait_until="networkidle")
        expect(page.locator("#task-fields")).to_be_enabled()
        nav_sizes = page.eval_on_selector_all(
            ".side-nav .nav-link", "els => els.map(el => getComputedStyle(el).fontSize)"
        )
        assert len(set(nav_sizes)) == 1, f"sidebar nav font sizes differ: {nav_sizes}"
        flat = page.eval_on_selector(".form-card", """el => {
            const s = getComputedStyle(el);
            return {
                blur: s.backdropFilter || s.webkitBackdropFilter || "none",
                gradient: s.backgroundImage,
                radius: parseFloat(s.borderTopLeftRadius),
                shadow: s.boxShadow,
            };
        }""")
        assert flat["blur"] == "none", f"iOS style should not use glass blur: {flat}"
        assert flat["gradient"] == "none", f"iOS style should not use gradients: {flat}"
        assert 10 <= flat["radius"] <= 20, f"iOS-style corner radius out of range: {flat}"
        assert flat["shadow"] in ("none", ""), f"iOS style keeps cards flat, found shadow: {flat}"
        body_background = page.evaluate("getComputedStyle(document.body).backgroundImage")
        assert body_background == "none", f"body background should be a flat colour: {body_background}"
        source_link = page.locator(".source-link a")
        expect(source_link).to_be_visible()
        expect(source_link).to_have_attribute("href", "https://www.mtztheme.com/")
        expect(source_link).to_have_attribute("target", "_blank")
        assert "noopener" in (source_link.get_attribute("rel") or ""), "external link must not leak the opener"
        expect(page.locator("#target")).to_have_value("honor")
        expect(page.locator("#submit-job")).to_be_disabled()
        expect(page.locator("#step-next")).to_be_visible()
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.classList.contains('skip-link')")
        page.screenshot(path=str(args.out / "web-desktop.png"), full_page=True)
        for width in (375, 768, 1024, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"horizontal overflow at {width}"
        page.locator("#open-capabilities").click()
        expect(page.locator("#capabilities-dialog")).to_be_visible()
        expect(page.locator("#route-list li")).to_have_count(1)
        page.keyboard.press("Escape")
        expect(page.locator("#capabilities-dialog")).to_be_hidden()
        step_one_height = page.evaluate("document.querySelector('.form-card').getBoundingClientRect().height")
        assert step_one_height < 900, f"step 1 should stay compact, got {step_one_height}px"
        page.locator("#file-input").set_input_files(str(fixture))
        expect(page.locator("#step-next")).to_be_enabled()
        page.locator("#step-next").click()
        expect(page.locator("#submit-job")).to_be_visible()
        expect(page.locator("#step-prev")).to_be_visible()
        page.locator("#step-prev").click()
        expect(page.locator("#step-next")).to_be_visible()
        page.locator("#step-next").click()
        expect(page.locator("#submit-job")).to_be_visible()
        page.locator("#metadata-details > summary").click()
        page.locator("#name").fill(title)
        page.locator("#author").fill("合成测试作者")
        page.locator("#icon-margin").fill("72")
        expect(page.locator("#margin-value")).to_have_text("72 px")
        page.locator("#submit-job").click()
        expect(page.locator("#task-badge")).to_have_attribute("data-state", "succeeded", timeout=20000)
        expect(page.locator("#job-result")).to_be_visible()
        expect(page.locator("#job-facts")).to_be_visible()
        facts_tops = page.eval_on_selector_all(
            "#job-facts > div", "els => els.map(el => Math.round(el.getBoundingClientRect().top))"
        )
        assert len(set(facts_tops)) == 1, f"job facts should share one row: {facts_tops}"
        download_tops = page.eval_on_selector_all(
            ".artifact-list .artifact-link", "els => els.map(el => Math.round(el.getBoundingClientRect().top))"
        )
        assert len(download_tops) == 2 and len(set(download_tops)) == 1, f"downloads should sit side by side: {download_tops}"
        assert page.locator("#job-result img").count() == 0
        job_id = page.locator("#job-id").inner_text()
        with page.expect_download() as download_info:
            page.locator('#job-result a[href$="/artifacts/theme"]').click()
        download = download_info.value
        downloaded = args.out / "browser-result.hnt"
        download.save_as(str(downloaded))
        with zipfile.ZipFile(downloaded) as archive:
            assert "icons" in archive.namelist()
            assert "&lt;img src=x onerror=alert(1)&gt;" in archive.read("description.xml").decode()
            with zipfile.ZipFile(io.BytesIO(archive.read("icons"))) as icons:
                from PIL import Image

                name = next(n for n in icons.namelist() if n.endswith(".png") and not n.startswith("dynamic_icons/"))
                with Image.open(io.BytesIO(icons.read(name))) as image:
                    box = image.convert("RGBA").getchannel("A").getbbox()
            assert box == (72, 72, 120, 120), f"icon margin was not honoured: {box}"
        expect(page.locator("#job-result")).to_contain_text("图标边距 72 px")
        with page.expect_download() as report_info:
            page.locator('#job-result a[href$="/artifacts/report"]').click()
        report_info.value.save_as(str(args.out / "browser-mapping.txt"))
        page.screenshot(path=str(args.out / "web-result.png"), full_page=True)
        page.reload(wait_until="networkidle")
        expect(page.locator("#task-badge")).to_have_attribute("data-state", "succeeded")
        expect(page.locator("#job-id")).to_have_text(job_id)
        page.locator("#delete-job").click()
        page.locator("#confirm-task-action").click()
        expect(page.locator("#task-badge")).to_have_attribute("data-state", "idle")
        page.locator("#new-job").click()
        expect(page.locator("#file-input")).to_be_visible()
        page.locator("#file-input").set_input_files(str(fixture))
        page.locator("#step-next").click()
        page.locator("#dry-run").check()
        page.locator("#submit-job").click()
        expect(page.locator("#task-badge")).to_have_attribute("data-state", "succeeded", timeout=20000)
        assert page.locator('#job-result a[href*="/artifacts/"]').count() == 0
        page.locator("#delete-job").click()
        page.locator("#confirm-task-action").click()
        expect(page.locator("#task-badge")).to_have_attribute("data-state", "idle")
        page.locator("#new-job").click()
        page.set_viewport_size({"width": 375, "height": 900})
        page.locator("#file-input").set_input_files({"name": "unsupported.txt", "mimeType": "text/plain", "buffer": b"not a theme"})
        expect(page.locator("#file-error")).to_be_visible()
        expect(page.locator("#step-next")).to_be_disabled()
        expect(page.locator("#submit-job")).to_be_disabled()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(args.out / "web-mobile.png"), full_page=True)
        assert not errors, "Browser errors: " + " | ".join(errors)
        context.close()
        browser.close()
    print("Browser smoke passed: responsive 375/768/1024/1440, upload, conversion, both downloads, XSS-safe text, restore, cleanup, dry-run and client validation.")


if __name__ == "__main__":
    main()
