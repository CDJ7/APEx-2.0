"""Network profiles: which PLC backend a computer is allowed to use.

``network_school.yaml`` forbids the real PLC outright (the PLC and the KR C4 are never on the
school network). ``network_lab.yaml`` allows it on the isolated lab router. The bridge calls
:func:`choose_backend` before it opens any connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class NetworkPolicyError(RuntimeError):
    """The requested PLC backend is not allowed by the active network profile."""


@dataclass(frozen=True)
class BackendChoice:
    kind: str                 # "sim" | "logix"
    host: str
    port: int = 44818
    timeout_s: float = 2.0


def load_profile(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


def choose_backend(profile: dict[str, Any], requested: str = "") -> BackendChoice:
    """Resolve the backend from the profile (``requested`` overrides only within what the profile allows)."""
    plc = profile.get("plc", {})
    kind = requested or plc.get("backend", "sim")
    if kind == "logix":
        if not plc.get("allowed", False):
            raise NetworkPolicyError(
                f"profile {profile.get('profile')!r} does not allow the real PLC - "
                "the PLC is never on the school network; use network_lab.yaml on the lab router")
        if not plc.get("address"):
            raise NetworkPolicyError("lab profile has no plc.address")
        return BackendChoice("logix", str(plc["address"]), timeout_s=float(plc.get("timeout_s", 2.0)))
    if kind == "sim":
        return BackendChoice("sim", str(plc.get("sim_host", "127.0.0.1")), int(plc.get("sim_port", 44818)))
    raise NetworkPolicyError(f"unknown PLC backend {kind!r}")
