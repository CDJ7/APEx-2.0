"""Load ``job_timeouts.yaml`` into the job machine's dataclasses."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from apex_orchestrator.job_machine import Policy, Timeouts


def _fill(cls: type, data: dict[str, Any]) -> Any:
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown {cls.__name__} keys: {sorted(unknown)}")
    return cls(**data)


def load_job_config(path: str | Path) -> tuple[Timeouts, Policy, dict[str, Any]]:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return (_fill(Timeouts, raw.get("timeouts", {})), _fill(Policy, raw.get("policy", {})),
            raw.get("router", {}))
