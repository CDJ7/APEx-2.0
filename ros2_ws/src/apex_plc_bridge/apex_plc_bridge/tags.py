"""PLC tag specification and the station's write allow-list.

The allow-list is the software guarantee behind the project rule *our code never
writes safety tags*: a write is only sent if

1. the member is listed under ``from_station`` in ``plc_tags.yaml``, AND
2. its full path starts with the hard-coded prefix ``APEX_FromStation.``, AND
3. the name matches none of the deny patterns (safety / KUKA safety bits / system tags).

Rule 2 and 3 are in code, so editing the YAML cannot open a write path to a safety tag.
Pure Python (PyYAML only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

WRITE_PREFIX = "APEX_FromStation."
READ_PREFIX = "APEX_ToStation."

# Never writable, whatever the YAML says (case-insensitive substring / exact-bit matches).
_DENY_SUBSTRINGS = ("safety", "safe", "estop", "e_stop", "guard", "lightcurtain", "muting", "s:", "$ov_pro",
                    "ov_pro", "operating_mode", "kuka_mode")
_KUKA_SAFETY_BITS = {"NHE", "BS", "QBS", "SHS1", "SHS2", "US2", "SBH", "SPA", "NHL", "AF", "FF", "ZS", "PE",
                     "AUT", "T1", "T2", "BSQ", "PSA", "SP", "RES"}


class TagWriteRefused(PermissionError):
    """Raised when a write is not on the allow-list. Always logged by the caller."""


@dataclass(frozen=True)
class TagDef:
    name: str          # member name, e.g. "Job_Request"
    path: str          # full Logix path, e.g. "APEX_FromStation.Job_Request"
    type: str          # "BOOL", "DINT", "DINT[10]", ...
    writable: bool
    doc: str = ""

    @property
    def is_array(self) -> bool:
        return "[" in self.type

    @property
    def length(self) -> int:
        m = re.search(r"\[(\d+)\]", self.type)
        return int(m.group(1)) if m else 1

    def default(self) -> Any:
        base = self.type.split("[")[0]
        v: Any = False if base == "BOOL" else ("" if base == "STRING" else 0)
        return [v] * self.length if self.is_array else v


def is_denied(name: str) -> bool:
    """True if the (member or full) tag name looks like anything safety-related."""
    low = name.lower()
    if any(s in low for s in _DENY_SUBSTRINGS):
        return True
    leaf = name.split(".")[-1].split("[")[0]
    return leaf.upper() in _KUKA_SAFETY_BITS


class TagSpec:
    """Loaded ``plc_tags.yaml``."""

    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        prefix = raw.get("write_prefix", WRITE_PREFIX)
        if prefix != WRITE_PREFIX:
            raise ValueError(f"write_prefix must be {WRITE_PREFIX!r} (hard-coded for safety), got {prefix!r}")
        self.max_lines = int(raw.get("max_lines", 10))
        self.fault_codes: dict[int, str] = {int(k): v for k, v in raw.get("fault_codes", {}).items()}
        self.tags: dict[str, TagDef] = {}
        for name, d in (raw.get("from_station") or {}).items():
            if is_denied(name):
                raise ValueError(f"from_station tag {name!r} matches the safety deny list - refusing to load")
            self.tags[name] = TagDef(name, WRITE_PREFIX + name, d["type"], True, d.get("doc", ""))
        for name, d in (raw.get("to_station") or {}).items():
            self.tags[name] = TagDef(name, READ_PREFIX + name, d["type"], False, d.get("doc", ""))

    @classmethod
    def load(cls, path: str | Path) -> "TagSpec":
        return cls(yaml.safe_load(Path(path).read_text()))

    # --------------------------------------------------------------- queries
    def writable(self) -> list[TagDef]:
        return [t for t in self.tags.values() if t.writable]

    def readable(self) -> list[TagDef]:
        return [t for t in self.tags.values() if not t.writable]

    def check_write(self, name: str) -> TagDef:
        """Return the TagDef if ``name`` may be written, else raise TagWriteRefused."""
        t = self.tags.get(name)
        if t is None:
            raise TagWriteRefused(f"{name!r} is not in plc_tags.yaml")
        if not t.writable:
            raise TagWriteRefused(f"{name!r} is a PLC->station (read-only) tag")
        if not t.path.startswith(WRITE_PREFIX) or is_denied(t.path):
            raise TagWriteRefused(f"{t.path!r} is outside the station write allow-list")
        return t

    def fault_text(self, code: int) -> str:
        return self.fault_codes.get(int(code), f"UNKNOWN_{code}")
