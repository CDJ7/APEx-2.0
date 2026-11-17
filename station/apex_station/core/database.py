"""SQLite persistence for the main station.

One database file (``station.db_path``) holds inventory, orders, receiving
results, discrepancies (damaged / missing / not loaded), the audit log and the
orchestrator's job events. Access goes through a single connection guarded by
a re-entrant lock, so the GUI thread, the REST API threads and the job-link
thread can all use it safely. WAL mode keeps reads from blocking writes.

StationCore is the **only writer**: the orchestrator reports job events and
StationCore books them (docs/JOB_STATE_MACHINE.md section 5).

Adapted from the old APEX station (archive/old_agv/station_gui/core/database.py).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("station.db")

SCHEMA_VERSION = 1          # APEx 2.0 schema (fresh database; not compatible with the old AGV station)

SCHEMA = """
CREATE TABLE IF NOT EXISTS parts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    part_number       TEXT    NOT NULL UNIQUE,
    name              TEXT    NOT NULL,
    description       TEXT    NOT NULL DEFAULT '',
    location          TEXT    NOT NULL DEFAULT '',     -- shelf slot inside the FlexBase
    part_code         INTEGER NOT NULL DEFAULT 0,      -- PLC part code: selects the KRL pick program (0 = not orderable)
    quantity          INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    damaged_qty       INTEGER NOT NULL DEFAULT 0 CHECK (damaged_qty >= 0),   -- quarantine, never restocked
    reorder_threshold INTEGER NOT NULL DEFAULT 0 CHECK (reorder_threshold >= 0),
    unit              TEXT    NOT NULL DEFAULT 'ea',
    status            TEXT    NOT NULL DEFAULT 'Active',
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    requester     TEXT NOT NULL,
    destination   TEXT NOT NULL,                 -- display name, e.g. "Station A"
    status        TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 0,
    notes         TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'gui',
    job_id        INTEGER,                       -- orchestrator job id
    approved_at   TEXT,
    queued_at     TEXT,
    loading_at    TEXT,
    in_transit_at TEXT,
    delivered_at  TEXT,
    closed_at     TEXT,
    cancelled_at  TEXT,
    receive_notes TEXT NOT NULL DEFAULT '',
    result        TEXT NOT NULL DEFAULT '',
    last_error    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_job ON orders(job_id);

CREATE TABLE IF NOT EXISTS order_lines (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id       INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    line_no        INTEGER NOT NULL,             -- 1.. in job order (= PLC array index + 1)
    part_id        INTEGER REFERENCES parts(id) ON DELETE SET NULL,
    part_number    TEXT    NOT NULL,             -- snapshot (survives part edits/deletes)
    part_name      TEXT    NOT NULL,
    part_code      INTEGER NOT NULL,
    qty_ordered    INTEGER NOT NULL CHECK (qty_ordered > 0),
    qty_not_loaded INTEGER NOT NULL DEFAULT 0,   -- chute count shortfall attributed to this line
    qty_ok         INTEGER,
    qty_damaged    INTEGER,
    qty_missing    INTEGER,
    notes          TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_lines_order ON order_lines(order_id);

CREATE TABLE IF NOT EXISTS discrepancies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    order_id      INTEGER NOT NULL REFERENCES orders(id),
    line_id       INTEGER REFERENCES order_lines(id),
    part_number   TEXT    NOT NULL,
    part_name     TEXT    NOT NULL,
    kind          TEXT    NOT NULL CHECK (kind IN ('Damaged', 'Missing', 'NotLoaded', 'ReturnMismatch')),
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    notes         TEXT    NOT NULL DEFAULT '',
    reported_by   TEXT    NOT NULL DEFAULT '',
    return_status TEXT    NOT NULL DEFAULT '',  -- Damaged: '' | pending | returned
    returned_at   TEXT,
    resolved      INTEGER NOT NULL DEFAULT 0,
    resolved_at   TEXT,
    resolution    TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    source    TEXT NOT NULL,          -- gui | rest:<ip> | orchestrator | system
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL DEFAULT '',
    details   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS job_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    kind     TEXT NOT NULL,
    job_id   INTEGER,
    order_id INTEGER,
    data     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_job_events_order ON job_events(order_id);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now_iso() -> str:
    """UTC timestamp in ISO-8601 (seconds precision) used for every stored time."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    """Thread-safe wrapper around one SQLite connection."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)
            self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                               (str(SCHEMA_VERSION),))
        log.info("database ready path=%s", self.path)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """``with db.transaction() as c:`` - atomic block (rolled back on exception)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return row[0] if row else None

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        """Run a single statement outside an explicit transaction; returns lastrowid."""
        with self._lock:
            return int(self._conn.execute(sql, params).lastrowid or 0)

    # ---------------------------------------------------------------- audit
    def audit(self, actor: str, source: str, action: str, entity: str = "", entity_id: Any = "",
              details: Any = "", conn: sqlite3.Connection | None = None) -> None:
        """Append to the audit log (inside the caller's transaction if ``conn`` is given)."""
        text = details if isinstance(details, str) else json.dumps(details, default=str)
        sql = ("INSERT INTO audit_log(ts, actor, source, action, entity, entity_id, details) "
               "VALUES (?,?,?,?,?,?,?)")
        params = (now_iso(), actor, source, action, entity, str(entity_id), text)
        if conn is not None:
            conn.execute(sql, params)
        else:
            self.execute(sql, params)

    def backup(self, dst: str | Path) -> None:
        """Online, consistent copy of the database (safe while the station is running)."""
        with self._lock, sqlite3.connect(str(dst)) as out:
            self._conn.backup(out)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
