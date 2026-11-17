"""Inventory business logic: parts CRUD, search, stock levels, quarantine, CSV import/export.

Adapted from the old APEX station (archive/old_agv/station_gui/core/inventory.py). New in
APEx 2.0: every orderable part has a **part code** - the number the PLC uses to select the
KRL pick program / shelf slot in the FlexBase (``Job_PartCodes`` in plc_tags.yaml). Damaged
parts are held in **quarantine** (``damaged_qty``) and never restocked automatically.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apex_station.core.database import Database, now_iso
from apex_station.core.states import PartStatus

log = logging.getLogger("station.inventory")

CSV_FIELDS = ["part_number", "name", "description", "location", "part_code", "quantity", "reorder_threshold", "unit",
              "status"]
MAX_PART_CODE = 9999          # [TBD] matches the PLC / KRL program numbering
PART_NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,39}$")


class ValidationError(ValueError):
    """Invalid user/remote input (message is safe to show to the user)."""


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    errors: list[str] | None = None


def _clean_part(data: dict[str, Any], partial: bool = False) -> dict[str, Any]:
    """Validate/normalise part fields. Raises :class:`ValidationError`."""
    out: dict[str, Any] = {}
    if "part_number" in data or not partial:
        pn = str(data.get("part_number", "")).strip()
        if not PART_NUMBER_RE.match(pn):
            raise ValidationError("Part number must be 1-40 characters: letters, digits, . _ / -")
        out["part_number"] = pn
    if "name" in data or not partial:
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 120:
            raise ValidationError("Name is required (max 120 characters)")
        out["name"] = name
    for key, maxlen in (("description", 500), ("location", 60), ("unit", 12)):
        if key in data or not partial:
            val = str(data.get(key, "") or ("ea" if key == "unit" else "")).strip()
            if len(val) > maxlen:
                raise ValidationError(f"{key} too long (max {maxlen})")
            out[key] = val
    for key in ("quantity", "reorder_threshold"):
        if key in data or not partial:
            try:
                val = int(str(data.get(key, 0)).strip() or 0)
            except ValueError as exc:
                raise ValidationError(f"{key} must be a whole number") from exc
            if val < 0 or val > 10_000_000:
                raise ValidationError(f"{key} must be between 0 and 10,000,000")
            out[key] = val
    if "part_code" in data or not partial:
        try:
            code = int(str(data.get("part_code", 0)).strip() or 0)
        except ValueError as exc:
            raise ValidationError("part_code must be a whole number") from exc
        if not 0 <= code <= MAX_PART_CODE:
            raise ValidationError(f"part_code must be 0..{MAX_PART_CODE} (0 = not orderable through the cell)")
        out["part_code"] = code
    if "status" in data or not partial:
        status = str(data.get("status", PartStatus.ACTIVE.value) or PartStatus.ACTIVE.value).strip().title()
        if status not in {s.value for s in PartStatus}:
            raise ValidationError(f"status must be one of {[s.value for s in PartStatus]}")
        out["status"] = status
    return out


class InventoryService:
    def __init__(self, db: Database, bus=None):
        self.db = db
        self.bus = bus

    def _changed(self) -> None:
        if self.bus:
            self.bus.publish("inventory")

    # ------------------------------------------------------------------ read
    def list_parts(self, search: str = "", low_stock_only: bool = False, status: str | None = None) -> list[dict]:
        sql = "SELECT *, (quantity <= reorder_threshold) AS low_stock FROM parts WHERE 1=1"
        params: list[Any] = []
        if search:
            like = f"%{search.strip()}%"
            sql += " AND (part_number LIKE ? OR name LIKE ? OR description LIKE ? OR location LIKE ?)"
            params += [like] * 4
        if low_stock_only:
            sql += " AND quantity <= reorder_threshold"
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY part_number"
        return self.db.query(sql, tuple(params))

    def get(self, part_id: int) -> dict | None:
        return self.db.query_one("SELECT *, (quantity <= reorder_threshold) AS low_stock FROM parts WHERE id=?",
                                 (part_id,))

    def get_by_number(self, part_number: str) -> dict | None:
        return self.db.query_one(
            "SELECT *, (quantity <= reorder_threshold) AS low_stock FROM parts WHERE part_number=?",
            (part_number,))

    def stats(self) -> dict[str, int]:
        row = self.db.query_one(
            "SELECT COUNT(*) AS skus, COALESCE(SUM(quantity),0) AS units, "
            "COALESCE(SUM(CASE WHEN quantity <= reorder_threshold AND status='Active' THEN 1 ELSE 0 END),0) AS low, "
            "COALESCE(SUM(CASE WHEN quantity = 0 AND status='Active' THEN 1 ELSE 0 END),0) AS out_of_stock, "
            "COALESCE(SUM(damaged_qty),0) AS damaged "
            "FROM parts")
        return {k: int(v) for k, v in (row or {}).items()}

    # ------------------------------------------------------------------ write
    def create(self, data: dict[str, Any], actor: str = "operator", source: str = "gui") -> dict:
        part = _clean_part(data)
        with self.db.transaction() as c:
            if c.execute("SELECT 1 FROM parts WHERE part_number=?", (part["part_number"],)).fetchone():
                raise ValidationError(f"Part number {part['part_number']} already exists")
            if part["part_code"] and c.execute("SELECT 1 FROM parts WHERE part_code=?", (part["part_code"],)).fetchone():
                raise ValidationError(f"Part code {part['part_code']} is already used by another part")
            cur = c.execute(
                "INSERT INTO parts(part_number,name,description,location,part_code,quantity,reorder_threshold,unit,status,"
                "updated_at) VALUES (:part_number,:name,:description,:location,:part_code,:quantity,:reorder_threshold,"
                ":unit,:status,:ts)", {**part, "ts": now_iso()})
            pid = cur.lastrowid
            self.db.audit(actor, source, "part.create", "part", pid, part, conn=c)
        self._changed()
        return self.get(pid)  # type: ignore[return-value]

    def update(self, part_id: int, data: dict[str, Any], actor: str = "operator", source: str = "gui") -> dict:
        fields = _clean_part(data, partial=True)
        if not fields:
            raise ValidationError("nothing to update")
        with self.db.transaction() as c:
            old = c.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone()
            if old is None:
                raise ValidationError(f"Part {part_id} not found")
            if "part_number" in fields and fields["part_number"] != old["part_number"]:
                if c.execute("SELECT 1 FROM parts WHERE part_number=? AND id<>?",
                             (fields["part_number"], part_id)).fetchone():
                    raise ValidationError(f"Part number {fields['part_number']} already exists")
            if fields.get("part_code") and c.execute("SELECT 1 FROM parts WHERE part_code=? AND id<>?",
                                                     (fields["part_code"], part_id)).fetchone():
                raise ValidationError(f"Part code {fields['part_code']} is already used by another part")
            sets = ", ".join(f"{k}=:{k}" for k in fields)
            c.execute(f"UPDATE parts SET {sets}, updated_at=:ts WHERE id=:id", {**fields, "ts": now_iso(), "id": part_id})
            changes = {k: [old[k], v] for k, v in fields.items() if old[k] != v}
            self.db.audit(actor, source, "part.update", "part", part_id, changes, conn=c)
        self._changed()
        return self.get(part_id)  # type: ignore[return-value]

    def adjust(self, part_id: int, delta: int, reason: str, actor: str = "operator", source: str = "gui") -> dict:
        """Stock correction / receipt from supplier (+) or write-off (-)."""
        with self.db.transaction() as c:
            row = c.execute("SELECT quantity FROM parts WHERE id=?", (part_id,)).fetchone()
            if row is None:
                raise ValidationError("Part not found")
            new = row["quantity"] + int(delta)
            if new < 0:
                raise ValidationError(f"Adjustment would make stock negative ({new})")
            c.execute("UPDATE parts SET quantity=?, updated_at=? WHERE id=?", (new, now_iso(), part_id))
            self.db.audit(actor, source, "part.adjust", "part", part_id,
                          {"delta": delta, "new_quantity": new, "reason": reason}, conn=c)
        self._changed()
        return self.get(part_id)  # type: ignore[return-value]

    def dispose_damaged(self, part_id: int, qty: int, reason: str, actor: str = "operator",
                        source: str = "gui") -> dict:
        """Remove units from quarantine (scrapped / returned to the supplier)."""
        with self.db.transaction() as c:
            row = c.execute("SELECT damaged_qty FROM parts WHERE id=?", (part_id,)).fetchone()
            if row is None:
                raise ValidationError("Part not found")
            if not 0 < qty <= row["damaged_qty"]:
                raise ValidationError(f"Quantity must be 1..{row['damaged_qty']}")
            c.execute("UPDATE parts SET damaged_qty = damaged_qty - ?, updated_at=? WHERE id=?", (qty, now_iso(), part_id))
            self.db.audit(actor, source, "part.dispose_damaged", "part", part_id, {"qty": qty, "reason": reason}, conn=c)
        self._changed()
        return self.get(part_id)  # type: ignore[return-value]

    def delete(self, part_id: int, actor: str = "operator", source: str = "gui") -> None:
        with self.db.transaction() as c:
            row = c.execute("SELECT part_number FROM parts WHERE id=?", (part_id,)).fetchone()
            if row is None:
                raise ValidationError("Part not found")
            open_use = c.execute(
                "SELECT COUNT(*) FROM order_lines l JOIN orders o ON o.id=l.order_id "
                "WHERE l.part_id=? AND o.status NOT IN ('Closed','Cancelled')",
                (part_id,)).fetchone()[0]
            if open_use:
                raise ValidationError(f"{row['part_number']} is on {open_use} open order line(s); "
                                      "set it Inactive instead or close those orders first")
            c.execute("DELETE FROM parts WHERE id=?", (part_id,))
            self.db.audit(actor, source, "part.delete", "part", part_id, {"part_number": row["part_number"]}, conn=c)
        self._changed()

    # ------------------------------------------------------------------ CSV
    def export_csv(self, path: str | Path | None = None) -> str:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for p in self.list_parts():
            w.writerow(p)
        text = buf.getvalue()
        if path:
            Path(path).write_text(text, encoding="utf-8", newline="")
        return text

    def import_csv(self, path: str | Path, actor: str = "operator", source: str = "gui") -> ImportResult:
        """Create or update parts from a CSV file (see :meth:`import_csv_text`)."""
        return self.import_csv_text(Path(path).read_text(encoding="utf-8-sig"), actor, source)

    def import_csv_text(self, text: str, actor: str = "operator", source: str = "gui") -> ImportResult:
        """Create or update parts from CSV text (matched on ``part_number``).

        Required columns: ``part_number, name``. Others are optional. Each row is
        validated; bad rows are reported and skipped, good rows are applied.
        """
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or "part_number" not in reader.fieldnames or "name" not in reader.fieldnames:
            raise ValidationError("CSV must have at least the columns: part_number, name")
        res = ImportResult(errors=[])
        for lineno, row in enumerate(reader, start=2):
            row = {k: (v if v is not None else "") for k, v in row.items() if k in CSV_FIELDS}
            try:
                existing = self.get_by_number(str(row.get("part_number", "")).strip())
                if existing:
                    self.update(existing["id"], {k: v for k, v in row.items() if v != "" or k == "description"},
                                actor, source)
                    res.updated += 1
                else:
                    self.create(row, actor, source)
                    res.created += 1
            except ValidationError as exc:
                res.errors.append(f"line {lineno}: {exc}")
        self.db.audit(actor, source, "inventory.import", "inventory", "",
                      {"created": res.created, "updated": res.updated, "errors": len(res.errors)})
        return res

    def seed_demo(self) -> None:
        """Populate an empty database with realistic demo parts."""
        if self.db.scalar("SELECT COUNT(*) FROM parts"):
            return
        demo = [   # part_number, name, description, shelf slot, part code, qty, reorder threshold
            ("BRG-6204-2RS", "Ball bearing 6204-2RS", "20x47x14 mm sealed deep groove", "S1-A", 10, 40, 10),
            ("BLT-M8x25", "Hex bolt M8x25 8.8", "DIN 933 zinc plated", "S1-B", 11, 120, 30),
            ("NUT-M8", "Hex nut M8", "DIN 934 zinc plated", "S1-C", 12, 150, 30),
            ("WSH-M8", "Flat washer M8", "DIN 125", "S1-D", 13, 25, 30),
            ("SNS-PROX-M12", "Inductive sensor M12 PNP", "Sn 4 mm, NO, 3-wire", "S2-A", 20, 18, 5),
            ("RLY-24VDC", "Relay 24 VDC 2CO", "Plug-in with socket", "S2-B", 21, 12, 6),
            ("FUS-5A-5x20", "Fuse 5 A 5x20 mm", "Slow blow", "S2-C", 22, 64, 25),
            ("CBL-M12-1M", "M12 cable 4-pin 1 m", "PUR, straight female", "S2-D", 23, 7, 10),
            ("GRP-PAD-01", "Gripper finger pad", "Spare pad for the KUKA gripper", "S3-A", 30, 4, 2),
            ("FLT-AIR-01", "Air filter cartridge", "Not stored in the cell - manual pick", "shelf B", 0, 3, 3),
        ]
        for pn, name, desc, loc, code, qty, thr in demo:
            self.create({"part_number": pn, "name": name, "description": desc, "location": loc, "part_code": code,
                         "quantity": qty, "reorder_threshold": thr}, "system", "seed")
        log.info("seeded %d demo parts", len(demo))
