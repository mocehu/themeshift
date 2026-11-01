"""Load an optional allow-list of target package names."""

from __future__ import annotations

import json
from pathlib import Path

from .errors import ConfigError, UsageError

PACKAGE_HEADERS = {"包名", "package", "package name", "package_name"}


def load_package_filter(path: Path) -> frozenset[str]:
    """Read package names from a ``.json`` or ``.xlsx`` file."""

    if not path.is_file():
        raise UsageError(f"找不到包名列表文件：{path}")

    suffix = path.suffix.lower()
    if suffix == ".json":
        return _from_json(path)
    if suffix in {".xlsx", ".xlsm"}:
        return _from_xlsx(path)
    raise UsageError(f"不支持的包名列表格式：{suffix}（仅支持 .json/.xlsx）")


def _from_json(path: Path) -> frozenset[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法的 JSON：{exc}") from exc
    if isinstance(data, dict):
        data = data.get("packages", [])
    if not isinstance(data, list):
        raise ConfigError(f"{path} 必须是字符串数组或包含 packages 数组的对象。")
    return frozenset(str(item).strip() for item in data if str(item).strip())


def _from_xlsx(path: Path) -> frozenset[str]:
    try:
        import openpyxl
    except ImportError as exc:
        raise ConfigError(
            "读取 .xlsx 需要 openpyxl，请执行：pip install \"themeshift[xlsx]\""
        ) from exc

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    packages: set[str] = set()
    for worksheet in workbook.worksheets:
        rows = worksheet.iter_rows(values_only=True)
        header = next(rows, None) or ()
        column = _package_column(header)
        for row in rows:
            if column < len(row) and isinstance(row[column], str):
                packages.update(
                    value.strip() for value in row[column].splitlines() if value.strip()
                )
    return frozenset(packages)


def _package_column(header: tuple) -> int:
    for index, cell in enumerate(header):
        if isinstance(cell, str) and cell.strip().lower() in PACKAGE_HEADERS:
            return index
    # Fall back to the third column, the layout used by HONOR's template sheet.
    return 2
