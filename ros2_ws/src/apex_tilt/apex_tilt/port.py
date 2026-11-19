"""The orchestrator's view of the tilt tray (``TiltPort``) and the in-process implementation.

The job machine only needs: is the tray flat (motion interlock), start a dump, how did the dump
end, and home the tray after a fault. On the real robot the controller runs in the
``tilt_platform`` node on the TurtleBot and the orchestrator uses ``RosTiltPort``
(apex_orchestrator/orchestrator_node.py); in simulations and tests the controller runs in the
same process through :class:`InProcessTilt`.
"""

from __future__ import annotations

from typing import Protocol

from apex_tilt.tilt_core import SimTiltBackend, TiltConfig, TiltController, TiltRefused, TiltState


class TiltPort(Protocol):
    def state(self) -> str: ...
    def fault_text(self) -> str: ...
    def is_flat(self) -> bool: ...
    def start_dump(self) -> None: ...          # raises TiltRefused / RuntimeError when refused
    def dump_status(self) -> str: ...          # idle | running | succeeded | failed
    def home(self) -> None: ...
    def stop(self) -> None: ...


class InProcessTilt:
    """TiltPort around a :class:`TiltController`; whoever runs the simulation calls :meth:`update`."""

    def __init__(self, controller: TiltController | None = None):
        self.ctrl = controller or TiltController(SimTiltBackend(), TiltConfig())
        self._active = False
        self._cycles0 = 0

    def update(self, now: float, moving: bool) -> None:
        self.ctrl.tick(now, moving)

    def state(self) -> str:
        return self.ctrl.state.value

    def fault_text(self) -> str:
        return self.ctrl.fault_text

    def is_flat(self) -> bool:
        return self.ctrl.is_flat()

    def start_dump(self) -> None:
        self._cycles0 = self.ctrl.cycles
        self.ctrl.command("dump")
        self._active = True

    def dump_status(self) -> str:
        if not self._active:
            return "idle"
        if self.ctrl.state is TiltState.FAULT:
            self._active = False
            return "failed"
        if self.ctrl.cycles > self._cycles0 and self.ctrl.state is TiltState.FLAT:
            self._active = False
            return "succeeded"
        return "running"

    def home(self) -> None:
        self._active = False
        self.ctrl.command("home")

    def stop(self) -> None:
        self.ctrl.command("stop")


__all__ = ["InProcessTilt", "TiltPort", "TiltRefused"]
