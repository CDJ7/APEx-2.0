"""Station configuration (``station/config/station.yaml``) with dotted-key access."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "station.yaml"


class Config:
    def __init__(self, data: dict[str, Any], base_dir: Path):
        self.data = data
        self.base_dir = base_dir

    @classmethod
    def load(cls, path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> "Config":
        p = Path(path or DEFAULT_CONFIG)
        cfg = cls(yaml.safe_load(p.read_text()) or {}, p.resolve().parent)
        for k, v in (overrides or {}).items():
            cfg.set(k, v)
        return cfg

    def get(self, key: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def require(self, key: str) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(f"missing config key {key}")
        return val

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        cur = self.data
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value

    def path_of(self, key: str) -> Path:
        raw = str(self.require(key))
        if raw == ":memory:":
            return Path(raw)
        p = Path(raw)
        return p if p.is_absolute() else (self.base_dir / p).resolve()

    @property
    def simulation(self) -> bool:
        return self.get("link.mode", "sim") == "sim"
