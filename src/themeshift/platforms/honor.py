"""Write HONOR ``.hnt`` icon themes."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image

from ..errors import UsageError
from ..imaging import normalize_icon, render_grid
from ..models import BuildReport, NormalizedTheme, ThemeMeta
from ..naming import is_safe_component
from ..packaging import has_dos_attributes, write_text_lf, write_zip


class HonorTarget:
    """Writer for HONOR icon theme packages (``.hnt``)."""

    name = "honor"

    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self.layout = profile["layout"]
        self.canvas = int(profile["icon"]["canvas"])
        self.content = int(profile["icon"]["content"])
        self.background = tuple(profile["icon"].get("background", [0, 0, 0, 0]))
        # Pillow's resize/encode releases the GIL, so icon rendering scales well
        # across threads. Cap it so a multi-job web server is not oversubscribed.
        configured = profile["icon"].get("workers")
        self.workers = max(1, int(configured)) if configured else min(8, os.cpu_count() or 1)

    # ---------------------------------------------------------------- build
    def build(
        self,
        theme: NormalizedTheme,
        out_path: Path,
        work_dir: Path,
        options: Mapping[str, object] | None = None,
    ) -> BuildReport:
        if not theme.static_icons:
            raise UsageError("没有可输出的图标，已中止打包。")

        content = self._content_size(options)
        stage = work_dir / "honor-stage"
        icon_dir = stage / "icon-files"
        dynamic_dir = stage / "dynamic-icons"

        icon_entries: list[tuple[str, Path]] = []
        icon_jobs: list[tuple[Path, Path]] = []
        for package in sorted(theme.static_icons):
            self._require_safe(package, "应用包名")
            destination = icon_dir / f"{package}.png"
            icon_jobs.append((theme.static_icons[package], destination))
            icon_entries.append((f"{package}.png", destination))

        for package, filename, source in theme.dynamic_assets:
            self._require_safe(package, "动态图标包名")
            self._require_safe(filename, "动态图标文件名")
            destination = dynamic_dir / package / filename
            icon_jobs.append((source, destination))
            icon_entries.append(
                (f"dynamic_icons/{package}/{filename}", destination)
            )

        self._normalize_icons(icon_jobs, content)

        icons_archive = stage / self.layout["icons"]
        write_zip(icons_archive, icon_entries)

        self._write_description(stage, theme.meta)
        preview_dir = stage / self.layout["preview_dir"]
        self._render_previews(icon_dir, preview_dir)

        outer_files: list[tuple[str, Path]] = [
            (self.layout["description"], stage / self.layout["description"]),
            (self.layout["icons"], icons_archive),
        ]
        for preview in sorted(preview_dir.glob("*.jpg")):
            outer_files.append((f"{self.layout['preview_dir']}/{preview.name}", preview))

        write_zip(out_path, outer_files)
        return self._verify(out_path, content)

    # ------------------------------------------------------------ internals
    def _normalize_icons(self, jobs: list[tuple[Path, Path]], content: int) -> None:
        """Render icons concurrently; each job owns a distinct destination file."""

        def render(job: tuple[Path, Path]) -> None:
            source, destination = job
            normalize_icon(source, destination, self.canvas, content, self.background)

        if len(jobs) < 2 or self.workers < 2:
            for job in jobs:
                render(job)
            return
        with ThreadPoolExecutor(max_workers=min(self.workers, len(jobs))) as pool:
            # list() surfaces the first worker exception instead of silently
            # leaving a partially rendered icon directory.
            list(pool.map(render, jobs))

    def _content_size(self, options: Mapping[str, object] | None) -> int:
        """Resolve the content box from an optional, explicitly validated margin."""

        raw = None if options is None else options.get("icon_margin", None)
        if raw is None:
            return self.content
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise UsageError("图标边距必须是整数像素。")
        minimum_content = max(32, self.canvas // 4)
        if raw < 0 or self.canvas - 2 * raw < minimum_content:
            raise UsageError(
                f"图标边距 {raw}px 超出画布 {self.canvas}px 的可用范围，"
                f"内容区至少保留 {minimum_content}px。"
            )
        return self.canvas - 2 * raw

    def _require_safe(self, value: str, label: str) -> None:
        if not is_safe_component(value):
            raise UsageError(f"非法的{label}：{value!r}")

    def _write_description(self, stage: Path, meta: ThemeMeta) -> bytes:
        defaults = self.profile["description"]
        title = meta.title or "Icon"
        title_en = meta.title_en or meta.title or "Icon"
        brief_zh = meta.brief_info or f"{title}荣耀图标主题。"
        brief_en = meta.brief_info_en or defaults.get("brief_info_en", "")
        version = meta.version or defaults.get("version", "1.0.0")

        extra = {
            "extraContentFlag": defaults.get("extra_content_flag", "5"),
            "zh": {"title": title, "briefInfo": brief_zh},
            "en": {"title": title_en, "briefInfo": brief_en},
        }
        extra_json = json.dumps(extra, ensure_ascii=False, indent=2)

        text = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<HnTheme>\n"
            f"  <title>{escape(title)}</title>\n"
            f"  <designer>{escape(meta.designer)}</designer>\n"
            f"  <version>{escape(version)}</version>\n"
            f"  <briefInfo>{escape(brief_zh)}</briefInfo>\n"
            "  <extra>\n"
            f"{escape(extra_json)}\n"
            "  </extra>\n"
            "</HnTheme>\n"
        )
        return write_text_lf(stage / self.layout["description"], text)

    def _render_previews(self, icon_dir: Path, preview_dir: Path) -> None:
        specs = self.profile.get("preview", [])
        if not specs:
            return
        icons = sorted(icon_dir.glob("*.png"))
        background = self.profile.get("preview_background", "#FFFFFF")
        for spec in specs:
            render_grid(
                icon_paths=icons,
                destination=preview_dir / spec["filename"],
                width=int(spec["width"]),
                height=int(spec["height"]),
                columns=int(spec["columns"]),
                rows=int(spec["rows"]),
                icon_size=int(spec["icon_size"]),
                start=int(spec.get("start", 0)),
                background=background,
            )

    def _verify(self, out_path: Path, content: int) -> BuildReport:
        margin = (self.canvas - content) // 2
        low, high = margin, self.canvas - margin
        entries_without_dos_attributes = 0
        with zipfile.ZipFile(out_path) as outer:
            names = outer.namelist()
            entries_without_dos_attributes += sum(
                1 for info in outer.infolist() if not has_dos_attributes(info)
            )
            with zipfile.ZipFile(BytesIO(outer.read(self.layout["icons"]))) as icons:
                entries_without_dos_attributes += sum(
                    1 for info in icons.infolist() if not has_dos_attributes(info)
                )
                png_names = [n for n in icons.namelist() if n.endswith(".png")]
                wrong_canvas = 0
                outside_content = 0
                for name in png_names:
                    with Image.open(BytesIO(icons.read(name))) as image:
                        if image.size != (self.canvas, self.canvas):
                            wrong_canvas += 1
                            continue
                        if image.mode == "RGBA":
                            box = image.getchannel("A").getbbox()
                        else:
                            with image.convert("RGBA") as rgba:
                                box = rgba.getbbox()
                    if box and (box[0] < low or box[1] < low or box[2] > high or box[3] > high):
                        outside_content += 1
            preview_sizes: dict[str, tuple[int, int]] = {}
            for name in names:
                if name.endswith("/") or not name.startswith(f"{self.layout['preview_dir']}/"):
                    continue
                with Image.open(BytesIO(outer.read(name))) as image:
                    preview_sizes[name] = image.size
            xml_payload = outer.read(self.layout["description"])

        expected_previews = {
            f"{self.layout['preview_dir']}/{spec['filename']}": (
                int(spec["width"]),
                int(spec["height"]),
            )
            for spec in self.profile.get("preview", [])
        }
        return BuildReport(
            output=out_path,
            static_icons=len(
                [n for n in png_names if not n.startswith("dynamic_icons/")]
            ),
            dynamic_assets=len(
                [n for n in png_names if n.startswith("dynamic_icons/")]
            ),
            canvas_size=self.canvas,
            wrong_canvas=wrong_canvas,
            content_size=content,
            icons_outside_content=outside_content,
            entries_without_dos_attributes=entries_without_dos_attributes,
            preview_sizes=preview_sizes,
            expected_previews=expected_previews,
            description_lf=b"\r\n" not in xml_payload,
            description_bom=xml_payload.startswith(b"\xef\xbb\xbf"),
            extra_content_flag=_read_extra_flag(xml_payload),
        )


def _read_extra_flag(xml_payload: bytes) -> str | None:
    root = ET.fromstring(xml_payload)
    raw = root.findtext("extra")
    if not raw:
        return None
    try:
        return json.loads(raw.strip()).get("extraContentFlag")
    except json.JSONDecodeError:
        return None
