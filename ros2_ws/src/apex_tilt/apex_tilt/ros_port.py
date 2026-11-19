"""TiltPort over ROS 2: the orchestrator's client of the ``tilt_platform`` node on the robot."""

from __future__ import annotations

import time
from typing import Any

from apex_interfaces.msg import TiltState
from apex_interfaces.srv import TiltCommand

STALE_S = 1.0


class RosTiltPort:
    def __init__(self, node: Any):
        self.node = node
        self.msg: TiltState | None = None
        self._rx = 0.0
        self._req = None
        self._req_cycles = 0
        self._active = False
        self._cycles0 = 0
        node.create_subscription(TiltState, "/apex/tilt/state", self._on_state, 10)
        self.cli = node.create_client(TiltCommand, "/apex/tilt/command")

    def _on_state(self, msg: TiltState) -> None:
        self.msg, self._rx = msg, time.monotonic()

    def _fresh(self) -> bool:
        return self.msg is not None and time.monotonic() - self._rx < STALE_S

    def state(self) -> str:
        return self.msg.state if self._fresh() else "NO DATA"

    def fault_text(self) -> str:
        return self.msg.fault_text if self.msg else "no tilt-tray status (is tilt_platform running?)"

    def is_flat(self) -> bool:
        return self._fresh() and self.msg.state == "FLAT" and bool(self.msg.flat_switch)

    def start_dump(self) -> None:
        """Non-blocking: raises until the tray node accepted the dump (the job machine keeps retrying)."""
        if self._req is None:
            if not self.cli.service_is_ready():
                raise RuntimeError("tilt_platform service not available")
            self._req_cycles = self.msg.cycles if self.msg else 0
            self._req = self.cli.call_async(TiltCommand.Request(action="dump"))
            raise RuntimeError("waiting for the tray to accept the dump")
        if not self._req.done():
            raise RuntimeError("waiting for the tray to accept the dump")
        res, self._req = self._req.result(), None
        if res is None or not res.ok:
            raise RuntimeError(res.message if res else "no reply from tilt_platform")
        self._active, self._cycles0 = True, self._req_cycles

    def dump_status(self) -> str:
        if not self._active:
            return "idle"
        if not self._fresh() or self.msg.state == "FAULT":
            self._active = False
            return "failed"
        if self.msg.cycles > self._cycles0 and self.msg.state == "FLAT":
            self._active = False
            return "succeeded"
        return "running"

    def _send(self, action: str) -> None:
        if self.cli.service_is_ready():
            self.cli.call_async(TiltCommand.Request(action=action))
        else:
            raise RuntimeError("tilt_platform service not available")

    def home(self) -> None:
        self._active = False
        self._send("home")

    def stop(self) -> None:
        try:
            self._send("stop")
        except RuntimeError:
            pass
