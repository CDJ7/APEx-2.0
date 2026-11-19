"""'Action required' panel: everything the job is waiting for from a person, with the buttons.

Shown on the Dashboard and the Robot & Cell page. Items come from the live job status:

* **Load shortfall** - fewer parts reached the bin than ordered: *Send what is in the bin* or
  *Cancel order*.
* **Receipt** - the robot is parked at a station: opens the Receiving page.
* **Return bin** - the robot is at the return pose with damaged parts: enter the count.
* **Fault** - Reset (clears the fault, nothing moves), then Resume (motion continues).

Every request runs in the background (a ROS service call can take a moment) and errors are
shown to the operator.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import QInputDialog, QLabel, QVBoxLayout, QWidget

from apex_station.core.states import OrderStatus
from apex_station.ui import theme
from apex_station.ui.bridge import run_in_background
from apex_station.ui.pages.base import AppContext
from apex_station.ui.widgets import Card, button, confirm, error, hbox, muted


class ActionPanel(Card):
    def __init__(self, ctx: AppContext, open_receiving: Callable[[], None] | None = None):
        super().__init__("Action required")
        self.ctx = ctx
        self.open_receiving = open_receiving
        self.items = QVBoxLayout()
        self.items.setSpacing(8)
        self.holder = QWidget()
        self.holder.setLayout(self.items)
        self.holder.setStyleSheet("background: transparent;")
        self.body.addWidget(self.holder)
        self.empty = muted("Nothing waiting - the system is running on its own.")
        self.body.addWidget(self.empty)
        self._key: tuple = ()
        ctx.bridge.job_status.connect(self.update_from)
        ctx.bridge.orders_changed.connect(lambda: self.update_from(ctx.core.status(), force=True))

    def _clear(self) -> None:
        while self.items.count():
            it = self.items.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

    def _row(self, colour: str, text: str, *buttons) -> None:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {colour}; font-weight: 600;")
        w = QWidget()
        w.setLayout(hbox(lbl, None, *buttons, stretch_at=0))
        self.items.addWidget(w)

    def update_from(self, st: dict[str, Any], force: bool = False) -> None:
        job = st.get("job", {})
        step, jid = job.get("step", ""), job.get("job_id", 0)
        delivered = [o["id"] for o in self.ctx.core.orders.list(status=OrderStatus.DELIVERED.value)]
        key = (step, jid, tuple(delivered), job.get("fault_code"))
        if key == self._key and not force:
            return
        self._key = key
        self._clear()
        order = self.ctx.core.orders.by_job(jid) if jid else None
        oid = order["id"] if order else None
        if step == "LOAD_SHORTFALL" and oid:
            self._row(theme.BAD, f"Order #{oid}: only {job.get('counted')} of {job.get('expected')} parts reached "
                                 "the bin.",
                      button("Send what is in the bin", "primary", lambda: self._shortfall(oid, "send")),
                      button("Cancel order", "danger", lambda: self._shortfall(oid, "cancel")))
        for d in delivered:
            o = self.ctx.core.orders.get(d)
            self._row(theme.OK, f"Order #{d}: robot waiting at {o['destination']} for the receipt.",
                      button("Open receiving", "primary", self.open_receiving or (lambda: None)))
        if step == "AWAIT_RETURN_CONFIRM" and oid:
            self._row(theme.WARN, f"Order #{oid}: robot at the return pose - empty the return bin and count the "
                                  "damaged parts.", button("Enter count…", "primary", lambda: self._return(oid)))
        if step == "FAULT" and job.get("fault_code") in ("TRAY_NOT_FLAT", "TILT_FAILED", "TILT_TIMEOUT"):
            self._row(theme.BAD, f"FAULT {job.get('fault_code')}: {job.get('fault_text')}. Clear the tray, keep "
                                 "hands away, home it flat, then Reset.",
                      button("Tray flat", slot=lambda: self._operator("tray_home")),
                      button("Reset", "primary", lambda: self._operator("reset")))
        elif step == "FAULT":
            self._row(theme.BAD, f"FAULT {job.get('fault_code')}: {job.get('fault_text')}. Fix the cause, then Reset.",
                      button("Reset", "primary", lambda: self._operator("reset")))
        if step == "RESUME_WAIT":
            self._row(theme.WARN, "Fault cleared. Make sure the path and the cell are clear, then Resume.",
                      button("Resume", "primary", lambda: self._operator("resume")))
        self.empty.setVisible(self.items.count() == 0)
        self.holder.setVisible(self.items.count() > 0)

    # ------------------------------------------------------------------ actions
    def _bg(self, fn, ok_text: str) -> None:
        run_in_background(fn, lambda _r: self.ctx.toast("success", ok_text),
                          lambda msg: error(self, "Not accepted", msg))

    def _shortfall(self, oid: int, action: str) -> None:
        text = ("Send the robot with the parts that are in the bin? The missing parts are logged as NOT LOADED "
                "for a check of the conveyor and chute." if action == "send" else
                "Cancel the order? Unload the bin by hand - counted parts go back to stock, missing ones are "
                "logged as NOT LOADED.")
        if confirm(self, "Load shortfall", text, danger=action == "cancel"):
            self._bg(lambda: self.ctx.core.resolve_shortfall(oid, action, self.ctx.operator),
                     "Shortfall: " + ("robot sent" if action == "send" else "order cancelled"))

    def _return(self, oid: int) -> None:
        expected = sum(d["quantity"] for d in self.ctx.core.orders.pending_returns(oid))
        n, ok = QInputDialog.getInt(self, "Return bin", f"Damaged parts reported: {expected}\n"
                                    "How many did you take out of the return bin?", expected, 0, expected)
        if ok:
            self._bg(lambda: self.ctx.core.confirm_return(oid, n, self.ctx.operator),
                     f"{n} damaged part(s) booked into quarantine")

    def _operator(self, action: str) -> None:
        self._bg(lambda: self.ctx.core.operator_command(action, self.ctx.operator), f"{action.title()} sent")
