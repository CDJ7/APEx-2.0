"""Shared fixtures: a StationCore on an in-memory database with the built-in simulation link,
stepped deterministically (no background threads, no real time)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apex_station.paths import ensure_apex_packages  # noqa: E402

ensure_apex_packages()

from apex_cell_sim.cell_model import CellTiming  # noqa: E402

from apex_station.config import Config  # noqa: E402
from apex_station.core.database import Database  # noqa: E402
from apex_station.core.station import StationCore  # noqa: E402
from apex_station.links.local_link import LocalJobLink  # noqa: E402

DT = 0.1


class SimStation:
    """StationCore + LocalJobLink driven by hand."""

    def __init__(self, seed: bool = True):
        cfg = Config.load(overrides={"station.db_path": ":memory:", "station.seed_demo_data": seed,
                                     "link.mode": "sim"})
        self.link = LocalJobLink(timing=CellTiming(pick=1.0, convey=0.5))
        self.core = StationCore(cfg, Database(":memory:"), self.link)
        if seed:
            self.core.inventory.seed_demo()
        self.link._on_event = self.core._on_job_event       # what link.start() would wire up
        self.link._on_status = self.core._on_status
        self.now = 0.0
        self.notes: list[tuple[str, str]] = []
        self.core.bus.subscribe(lambda t, p: self.notes.append((p["level"], p["text"])) if t == "notify" else None)

    def step(self) -> None:
        self.now = round(self.now + DT, 3)
        self.link.step(self.now)
        if int(self.now * 10) % 10 == 0:
            self.core.submit_pending()

    def run_until(self, cond, limit: float = 2000.0) -> bool:
        end = self.now + limit
        while self.now < end:
            self.step()
            if cond():
                return True
        return False

    def order_status(self, oid: int) -> str:
        return self.core.orders.get(oid)["status"]

    def job_step(self) -> str:
        return self.core.job_status().get("step", "")

    def idle_docked(self) -> bool:
        st = self.core.status()
        return st["job"]["step"] == "IDLE" and not st["job"]["queue"] and st["robot"]["docked"] \
            and st["job"]["job_id"] == 0


@pytest.fixture()
def sim() -> SimStation:
    return SimStation()
