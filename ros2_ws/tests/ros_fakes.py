"""Minimal in-process stand-ins for rclpy and the generated message modules.

Lets the ROS node wrappers (apex_plc_bridge.bridge_node, apex_orchestrator.orchestrator_node,
demo_client) run under pytest on a machine without ROS 2. Message classes are generated
from the real ``apex_interfaces`` .msg/.srv files with ``__slots__``, so a node that sets a
field that does not exist in the interface fails here exactly as it would in ROS.

This is NOT a ROS replacement: no DDS, no QoS, no executors - callbacks are called
synchronously and the test drives timers by hand.
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from typing import Any, Callable

IFACE = Path(__file__).resolve().parents[1] / "src" / "apex_interfaces"
_DEFAULTS = {"bool": False, "string": "", "float32": 0.0, "float64": 0.0}


class _Msg:
    __slots__: tuple[str, ...] = ()
    _types: dict[str, str] = {}

    def __init__(self, **kw: Any) -> None:
        for f, t in self._types.items():
            setattr(self, f, _default(t))
        for k, v in kw.items():
            setattr(self, k, v)            # unknown field -> AttributeError (slots)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({', '.join(f'{f}={getattr(self, f)!r}' for f in self.__slots__)})"


class Time(_Msg):
    __slots__ = ("sec", "nanosec")
    _types = {"sec": "int32", "nanosec": "uint32"}


def _default(t: str) -> Any:
    if t.endswith("[]"):
        return []
    if t in _DEFAULTS:
        return _DEFAULTS[t]
    if "int" in t:
        return 0
    if t == "builtin_interfaces/Time":
        return Time()
    return None


def _parse(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            t, name = line.split()[:2]
            fields[name] = t
    return fields


def _make(name: str, fields: dict[str, str]) -> type:
    return type(name, (_Msg,), {"__slots__": tuple(fields), "_types": fields})


def build_interfaces() -> tuple[types.ModuleType, types.ModuleType]:
    msg_mod, srv_mod = types.ModuleType("apex_interfaces.msg"), types.ModuleType("apex_interfaces.srv")
    for f in sorted((IFACE / "msg").glob("*.msg")):
        setattr(msg_mod, f.stem, _make(f.stem, _parse(f.read_text())))
    for f in sorted((IFACE / "srv").glob("*.srv")):
        req, res = f.read_text().split("---")
        cls = type(f.stem, (), {"Request": _make(f.stem + "_Request", _parse(req)),
                                "Response": _make(f.stem + "_Response", _parse(res))})
        setattr(srv_mod, f.stem, cls)
    return msg_mod, srv_mod


# ------------------------------------------------------------------ rclpy stand-in
class Bus:
    """Topics and services shared by all fake nodes in a test."""

    def __init__(self) -> None:
        self.subs: dict[str, list[Callable]] = {}
        self.services: dict[str, Callable] = {}
        self.published: dict[str, list[Any]] = {}
        self.clock: Callable[[], float] = lambda: 0.0


BUS = Bus()


class Future:
    def __init__(self) -> None:
        self._result, self._exc, self._cbs = None, None, []

    def set_result(self, r: Any) -> None:
        self._result = r
        for cb in self._cbs:
            cb(self)

    def result(self) -> Any:
        return self._result

    def exception(self) -> Any:
        return self._exc

    def done(self) -> bool:
        return True

    def add_done_callback(self, cb: Callable) -> None:
        self._cbs.append(cb)
        cb(self)


class _Param:
    def __init__(self, v: Any) -> None:
        self.value = v


class _Logger:
    def __init__(self, name: str) -> None:
        self.name, self.lines = name, []

    def _log(self, lvl: str, msg: str, **_: Any) -> None:
        self.lines.append((lvl, msg))

    def info(self, m: str, **k: Any) -> None: self._log("info", m)
    def warning(self, m: str, **k: Any) -> None: self._log("warning", m)
    def error(self, m: str, **k: Any) -> None: self._log("error", m)


class _Now:
    def __init__(self, t: float) -> None:
        self.nanoseconds = int(t * 1e9)

    def to_msg(self) -> Time:
        return Time(sec=self.nanoseconds // 10**9, nanosec=self.nanoseconds % 10**9)


class _Clock:
    def now(self) -> _Now:
        return _Now(BUS.clock())


class _Client:
    def __init__(self, srv_name: str) -> None:
        self.srv_name = srv_name

    def service_is_ready(self) -> bool:
        return self.srv_name in BUS.services

    def wait_for_service(self, timeout_sec: float = 0.0) -> bool:
        return self.service_is_ready()

    def call_async(self, req: Any) -> Future:
        handler, res_cls = BUS.services[self.srv_name]
        fut = Future()
        fut.set_result(handler(req, res_cls()))
        return fut


class _Publisher:
    def __init__(self, topic: str) -> None:
        self.topic = topic

    def publish(self, msg: Any) -> None:
        BUS.published.setdefault(self.topic, []).append(msg)
        for cb in BUS.subs.get(self.topic, []):
            cb(msg)


class Node:
    PARAMS: dict[str, dict[str, Any]] = {}     # node name -> parameter overrides

    def __init__(self, name: str) -> None:
        self.name = name
        self.timers: list[tuple[float, Callable]] = []
        self._logger = _Logger(name)

    def declare_parameter(self, name: str, default: Any) -> _Param:
        return _Param(self.PARAMS.get(self.name, {}).get(name, default))

    def create_publisher(self, _t: Any, topic: str, _q: Any) -> _Publisher:
        return _Publisher(topic)

    def create_subscription(self, _t: Any, topic: str, cb: Callable, _q: Any) -> None:
        BUS.subs.setdefault(topic, []).append(cb)

    def create_service(self, srv: Any, name: str, cb: Callable) -> None:
        BUS.services[name] = (cb, srv.Response)

    def create_client(self, _srv: Any, name: str) -> _Client:
        return _Client(name)

    def create_timer(self, period: float, cb: Callable) -> None:
        self.timers.append((period, cb))

    def get_logger(self) -> _Logger:
        return self._logger

    def get_clock(self) -> _Clock:
        return _Clock()

    def destroy_node(self) -> None: ...


def install() -> Bus:
    """Put the stand-ins into sys.modules (only when real rclpy is absent). Returns the bus."""
    try:
        import rclpy  # noqa: F401
        raise RuntimeError("real rclpy present - use launch tests instead of fakes")
    except ImportError:
        pass
    global BUS
    BUS = Bus()
    rclpy = types.ModuleType("rclpy")
    rclpy.init = lambda args=None: None
    rclpy.shutdown = lambda: None
    rclpy.ok = lambda: True
    rclpy.spin_once = lambda node, timeout_sec=0.0: time.sleep(min(timeout_sec or 0.0, 0.05))   # blocks like rclpy
    rclpy.spin_until_future_complete = lambda node, fut, timeout_sec=0.0: None
    node_mod = types.ModuleType("rclpy.node")
    node_mod.Node = Node
    std = types.ModuleType("std_msgs.msg")
    std.String = _make("String", {"data": "string"})
    msg_mod, srv_mod = build_interfaces()
    nav = types.ModuleType("nav_msgs.msg")
    nav.Odometry = type("Odometry", (), {})
    pkg = types.ModuleType("apex_interfaces")
    pkg.msg, pkg.srv = msg_mod, srv_mod
    sys.modules.update({"nav_msgs": types.ModuleType("nav_msgs"), "nav_msgs.msg": nav, "rclpy": rclpy, "rclpy.node": node_mod, "std_msgs": types.ModuleType("std_msgs"),
                        "std_msgs.msg": std, "apex_interfaces": pkg, "apex_interfaces.msg": msg_mod,
                        "apex_interfaces.srv": srv_mod})
    return BUS


def uninstall() -> None:
    for m in ["rclpy", "rclpy.node", "std_msgs", "std_msgs.msg", "apex_interfaces", "apex_interfaces.msg",
              "apex_interfaces.srv", "apex_plc_bridge.bridge_node", "apex_orchestrator.orchestrator_node",
              "apex_tilt.tilt_node", "apex_tilt.ros_port", "nav_msgs", "nav_msgs.msg",
              "apex_orchestrator.demo_client"]:
        sys.modules.pop(m, None)
