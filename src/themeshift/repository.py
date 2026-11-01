"""Access platform profiles and package mappings.

Data lives either inside the installed package (``themeshift/data``) or in a
user-supplied directory, which is how new platforms can be added without
forking the project.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError
from .profiles import adapter_config, require_id, validate_profile

ENV_DATA_DIR = "THEMESHIFT_DATA"
PACKAGED_DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class DataRepository:
    """Resolves and validates profile/mapping JSON files."""

    root: Path

    @classmethod
    def create(cls, override: Path | None = None) -> DataRepository:
        if override is not None:
            root = override.expanduser().resolve()
        elif os.environ.get(ENV_DATA_DIR):
            root = Path(os.environ[ENV_DATA_DIR]).expanduser().resolve()
        else:
            root = PACKAGED_DATA_DIR

        if not (root / "profiles").is_dir():
            raise ConfigError(
                f"数据目录 {root} 缺少 profiles/ 子目录。"
                f"可用 --data-dir 或环境变量 {ENV_DATA_DIR} 指定。"
            )
        return cls(root=root)

    @property
    def profiles_dir(self) -> Path:
        return self.root / "profiles"

    @property
    def mappings_dir(self) -> Path:
        return self.root / "mappings"

    def profile_names(self) -> list[str]:
        return sorted(path.stem for path in self.profiles_dir.glob("*.json"))

    def available(self, kind: str | None = None) -> list[str]:
        if kind is None:
            return self.profile_names()
        return [
            name
            for name in self.profile_names()
            if adapter_config(self.load_profile(name), kind) is not None
        ]

    def load_profile(self, name: str) -> dict:
        require_id(name)
        path = self.profiles_dir / f"{name}.json"
        if not path.is_file():
            raise ConfigError(f"未知平台 {name!r}：找不到 {path}")
        data = _read_json(path)
        validate_profile(data, expected_name=name, path=path)
        return data

    def mapping_path(self, source: str, target: str) -> Path:
        require_id(source)
        require_id(target)
        path = self.mappings_dir / f"{source}_to_{target}.json"
        if not path.is_file():
            raise ConfigError(
                f"缺少 {source} -> {target} 的包名映射文件：{path}\n"
                f"可用映射：{', '.join(self.mapping_names()) or '（无）'}"
            )
        data = _read_json(path)
        if data.get("source") != source or data.get("target") != target:
            raise ConfigError(f"{path} 声明的 source/target 与请求方向不一致。")
        return path

    def mapping_names(self) -> list[str]:
        if not self.mappings_dir.is_dir():
            return []
        return sorted(path.stem for path in self.mappings_dir.glob("*.json"))


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path} 不是 UTF-8 编码。") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法的 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} 的顶层结构必须是 JSON 对象。")
    return payload
