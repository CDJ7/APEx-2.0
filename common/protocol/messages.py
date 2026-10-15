"""Network message schema: station / PLC gateway / tools  <->  AGV (Raspberry Pi).

Transport: TCP, one JSON object per line (NDJSON, UTF-8, ``\\n`` terminated,
maximum :data:`MAX_MESSAGE_BYTES` per line). The AGV is the TCP server
(``network.agv.port``); clients connect and must send ``hello`` with the shared
token first.

Envelope (every message)::

    {"v": 1, "type": "dispatch", "id": "6f1c2a9e", "ts": 1739620000.12,
     "src": "station", "data": {...}}

* ``v``     protocol version (:data:`PROTOCOL_VERSION`)
* ``type``  one of :class:`MsgType`
* ``id``    sender-chosen message id; replies reference it in ``ack.ref``
* ``ts``    sender UNIX time (seconds, float)
* ``src``   free-text sender identity (``station``, ``plc``, ``agv``, ...)
* ``data``  type-specific payload, validated by :func:`validate_message`

See ``docs/PROTOCOL.md`` for examples of every message.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 2_000_000  # a "map" reply carries a base64 PNG


class MsgType(str, Enum):
    # client -> AGV
    HELLO = "hello"
    DISPATCH = "dispatch"
    CANCEL = "cancel"
    RELEASE = "release"            # leave the destination and return home
    RETURN_HOME = "return_home"
    ESTOP = "estop"
    RESET = "reset"                # clear software E-stop / faults
    PAUSE = "pause"
    RESUME = "resume"
    CLEAR_BLOCKED = "clear_blocked"
    GET_MAP = "get_map"
    SET_POSE = "set_pose"
    SIM = "sim"                    # simulation-only scenario control
    LOAD = "load"                  # station: KUKA arm loading order <order_id> (AGV must stay docked)
    LOAD_ABORT = "load_abort"      # station: loading abandoned
    DISCHARGE = "discharge"        # station: tip the return bin (linear actuator) while docked
    PING = "ping"
    # AGV -> client
    WELCOME = "welcome"
    ACK = "ack"
    TELEMETRY = "telemetry"
    EVENT = "event"
    MAP = "map"
    PONG = "pong"


class EventKind(str, Enum):
    TASK_ACCEPTED = "task_accepted"
    TASK_REJECTED = "task_rejected"
    TASK_CANCELLED = "task_cancelled"
    ARRIVED = "arrived"
    RETURNED_HOME = "returned_home"
    OBSTACLE_DETECTED = "obstacle_detected"
    OBSTACLE_CLEARED = "obstacle_cleared"
    SEGMENT_BLOCKED = "segment_blocked"
    SEGMENT_CLEARED = "segment_cleared"
    REROUTING = "rerouting"
    REROUTE_FAILED = "reroute_failed"
    FAULT = "fault"
    FAULT_CLEARED = "fault_cleared"
    ESTOP = "estop"
    STATE_CHANGE = "state_change"
    DOCKED = "docked"
    DOCK_FAILED = "dock_failed"
    DISCHARGE_DONE = "discharge_done"
    MANUAL_MODE = "manual_mode"
    LINK_LOST = "link_lost"
    LINK_RESTORED = "link_restored"
    TASK_FAILED = "task_failed"


#: Required ``data`` keys and their accepted python types, per message type.
_SCHEMAS: dict[MsgType, dict[str, tuple[type, ...]]] = {
    MsgType.HELLO: {"token": (str,), "client": (str,)},
    MsgType.DISPATCH: {"order_id": (int,), "destination": (str,)},
    MsgType.CANCEL: {},
    MsgType.RELEASE: {},
    MsgType.RETURN_HOME: {},
    MsgType.ESTOP: {},
    MsgType.RESET: {},
    MsgType.PAUSE: {},
    MsgType.RESUME: {},
    MsgType.CLEAR_BLOCKED: {},
    MsgType.GET_MAP: {},
    MsgType.SET_POSE: {},
    MsgType.SIM: {"action": (str,)},
    MsgType.LOAD: {"order_id": (int,)},
    MsgType.LOAD_ABORT: {},
    MsgType.DISCHARGE: {},
    MsgType.PING: {},
    MsgType.WELCOME: {"agv_id": (str,)},
    MsgType.ACK: {"ref": (str,), "ok": (bool,)},
    MsgType.TELEMETRY: {"state": (str,)},
    MsgType.EVENT: {"kind": (str,)},
    MsgType.MAP: {"resolution": (int, float), "origin": (list,)},
    MsgType.PONG: {},
}


class MessageError(ValueError):
    """Raised for malformed or invalid messages."""


@dataclass
class Pose2D:
    """Planar pose in the map frame: metres and radians (CCW positive)."""

    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {"x": round(self.x, 4), "y": round(self.y, 4), "theta": round(self.theta, 4)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Pose2D":
        return cls(float(d.get("x", 0.0)), float(d.get("y", 0.0)), float(d.get("theta", 0.0)))

    def distance_to(self, other: "Pose2D") -> float:
        return math.hypot(other.x - self.x, other.y - self.y)


@dataclass
class Telemetry:
    """Payload of a ``telemetry`` message (AGV -> clients, ``agv.telemetry_hz``)."""

    state: str
    agv_id: str = "agv1"
    order_id: int | None = None
    destination: str | None = None
    pose: dict[str, float] = field(default_factory=lambda: Pose2D().as_dict())
    localized: bool = False
    speed_mps: float = 0.0
    battery_pct: float | None = None
    battery_mv: int | None = None
    faults: list[str] = field(default_factory=list)
    estop: bool = False
    paused: bool = False
    obstacle: dict[str, Any] | None = None        # {"source","distance","zone","moving"}
    path: list[list[float]] = field(default_factory=list)  # remaining planned polyline
    route_nodes: list[str] = field(default_factory=list)
    blocked_edges: list[list[str]] = field(default_factory=list)
    sensors: dict[str, Any] = field(default_factory=dict)
    docked: bool = False                      # Hall home-dock sensor (charging / waiting position)
    manual: bool = False                      # Flysky RC override active
    actuator: int = 0                         # return-bin actuator 1 extending / -1 retracting / 0 idle
    link_hold: bool = False                   # motion held because the station link went stale
    loading_order_id: int | None = None
    simulation: bool = False
    uptime_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------- build/parse
def new_id() -> str:
    return uuid.uuid4().hex[:12]


def make_message(msg_type: MsgType | str, data: dict[str, Any] | None = None,
                 src: str = "", msg_id: str | None = None) -> dict[str, Any]:
    """Create an envelope dict for ``msg_type`` with payload ``data``."""
    mtype = MsgType(msg_type)
    return {
        "v": PROTOCOL_VERSION,
        "type": mtype.value,
        "id": msg_id or new_id(),
        "ts": round(time.time(), 3),
        "src": src,
        "data": data or {},
    }


def make_ack(ref: str, ok: bool = True, error: str | None = None, src: str = "agv",
             **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"ref": ref, "ok": ok}
    if error:
        data["error"] = error
    data.update(extra)
    return make_message(MsgType.ACK, data, src=src)


def make_event(kind: EventKind | str, message: str = "", src: str = "agv", **data: Any) -> dict[str, Any]:
    payload = {"kind": EventKind(kind).value, "message": message}
    payload.update(data)
    return make_message(MsgType.EVENT, payload, src=src)


def encode_message(msg: dict[str, Any]) -> bytes:
    """Serialise to one NDJSON line."""
    raw = json.dumps(msg, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    if len(raw) > MAX_MESSAGE_BYTES:
        raise MessageError(f"message too large ({len(raw)} bytes)")
    return raw


def validate_message(obj: Any) -> dict[str, Any]:
    """Validate an envelope + payload; returns it with ``type`` normalised."""
    if not isinstance(obj, dict):
        raise MessageError("message must be a JSON object")
    if obj.get("v") != PROTOCOL_VERSION:
        raise MessageError(f"unsupported protocol version {obj.get('v')!r}")
    try:
        mtype = MsgType(obj.get("type"))
    except ValueError as exc:
        raise MessageError(f"unknown message type {obj.get('type')!r}") from exc
    data = obj.get("data", {})
    if not isinstance(data, dict):
        raise MessageError("'data' must be an object")
    if not isinstance(obj.get("id", ""), str):
        raise MessageError("'id' must be a string")
    for key, types in _SCHEMAS[mtype].items():
        if key not in data:
            raise MessageError(f"{mtype.value}: missing field '{key}'")
        # bool is an int subclass - reject it where an int is expected
        if not isinstance(data[key], types) or (bool not in types and isinstance(data[key], bool)):
            raise MessageError(f"{mtype.value}: field '{key}' has wrong type")
    obj.setdefault("id", new_id())
    obj["data"] = data
    return obj


def decode_message(raw: bytes | str) -> dict[str, Any]:
    """Parse and validate one NDJSON line."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_MESSAGE_BYTES:
            raise MessageError("message too large")
        raw = raw.decode("utf-8", errors="strict")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MessageError(f"invalid JSON: {exc}") from exc
    return validate_message(obj)
