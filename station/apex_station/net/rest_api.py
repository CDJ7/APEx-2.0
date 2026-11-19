"""REST API for the workstation laptop (FastAPI + uvicorn in a background thread).

Adapted from the old APEX station (archive/old_agv/station_gui/net/rest_api.py): the phone
QR-scan and AGV-command endpoints are gone; job / robot / cell status, receipts, returns,
shortfall decisions and the operator Stop / Reset / Resume are new.

Security (unchanged):

* every endpoint except ``/api/health`` requires the header ``X-API-Key`` (``rest.api_key``,
  compared in constant time);
* optional source-IP allow-list ``rest.allowed_ips``;
* request bodies are validated with pydantic (types, ranges, lengths);
* every request is logged with method, path, status and **source IP**, and every
  state-changing call is written to the audit log as ``rest:<ip>``.

There is no endpoint that writes a PLC tag, a safety value or a KUKA setting.

Interactive docs (Swagger UI): ``http://<station>:8000/docs``.
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from apex_station.config import Config
from apex_station.core.inventory import ValidationError
from apex_station.core.station import StationCore

log = logging.getLogger("station.rest")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# ------------------------------------------------------------------ schemas
class OrderLineIn(BaseModel):
    part_number: str = Field(min_length=1, max_length=40)
    quantity: int = Field(ge=1, le=1000)


class OrderIn(BaseModel):
    requester: str = Field(min_length=1, max_length=60)
    destination: str = Field(min_length=1, max_length=40)
    lines: list[OrderLineIn] = Field(min_length=1, max_length=50)
    notes: str = Field(default="", max_length=500)
    priority: int = Field(default=0, ge=0, le=9)


class ReceiveLineIn(BaseModel):
    line_id: int
    ok: int = Field(ge=0)
    damaged: int = Field(default=0, ge=0)
    missing: int = Field(default=0, ge=0)
    notes: str = Field(default="", max_length=300)


class ReceiveIn(BaseModel):
    lines: list[ReceiveLineIn] = Field(min_length=1)
    notes: str = Field(default="", max_length=500)
    receiver: str = Field(default="laptop", max_length=60)


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=300)


class ReturnIn(BaseModel):
    count: int = Field(ge=0, le=1000)


class ShortfallIn(BaseModel):
    action: Literal["send", "cancel"]


class ResolveIn(BaseModel):
    resolution: str = Field(min_length=1, max_length=300)
    restock: bool = False


OperatorAction = Literal["stop", "reset", "resume", "tray_home"]


# ------------------------------------------------------------------ app
def create_app(core: StationCore, cfg: Config) -> FastAPI:
    api_key = str(cfg.require("rest.api_key"))
    allowed = set(cfg.get("rest.allowed_ips", []) or [])
    app = FastAPI(title="APEx 2.0 Station API", version="2.0",
                  description="Inventory, orders, receipts and job / robot / cell status for APEx 2.0.")

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        ip = request.client.host if request.client else "?"
        if allowed and ip not in allowed:
            log.warning("REST rejected ip=%s path=%s (not in allow list)", ip, request.url.path)
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        t0 = time.perf_counter()
        response = await call_next(request)
        log.info("REST %s %s ip=%s status=%d %.0fms", request.method, request.url.path, ip,
                 response.status_code, (time.perf_counter() - t0) * 1000)
        return response

    def auth(request: Request, key: str | None = Security(api_key_header)) -> str:
        if not key or not hmac.compare_digest(key, api_key):
            ip = request.client.host if request.client else "?"
            log.warning("REST auth failed ip=%s path=%s", ip, request.url.path)
            raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
        return f"rest:{request.client.host if request.client else '?'}"

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def order_or_404(order_id: int) -> dict:
        order = core.orders.get(order_id)
        if order is None:
            raise HTTPException(404, "order not found")
        return order

    # -------------------------------------------------------------- health + status
    @app.get("/api/health")
    def health() -> dict:
        st = core.status()
        return {"status": "ok", "link": core.link.mode, "orchestrator_connected": bool(st.get("connected")),
                "time": time.time()}

    @app.get("/api/kpis")
    def kpis(_src: str = Depends(auth)) -> dict:
        return core.kpis()

    @app.get("/api/status")
    def status(_src: str = Depends(auth)) -> dict:
        """Job, robot, and the READ-ONLY cell / KUKA summary."""
        return core.status()

    @app.get("/api/destinations")
    def destinations(_src: str = Depends(auth)) -> dict:
        return {"destinations": core.destination_names()}

    @app.get("/api/job-events")
    def job_events(order_id: int | None = None, limit: int = Query(100, ge=1, le=1000),
                   _src: str = Depends(auth)) -> list[dict]:
        return core.recent_job_events(limit, order_id)

    # -------------------------------------------------------------- inventory
    @app.get("/api/parts")
    def list_parts(search: str = Query("", max_length=60), low_stock: bool = False,
                   _src: str = Depends(auth)) -> list[dict]:
        return core.inventory.list_parts(search, low_stock)

    @app.get("/api/parts/{part_number}")
    def get_part(part_number: str, _src: str = Depends(auth)) -> dict:
        part = core.inventory.get_by_number(part_number)
        if part is None:
            raise HTTPException(404, "part not found")
        return part

    # -------------------------------------------------------------- orders
    @app.get("/api/orders")
    def list_orders(status: str | None = None, limit: int = Query(100, ge=1, le=1000),
                    _src: str = Depends(auth)) -> list[dict]:
        return core.orders.list(status=status, limit=limit)

    @app.get("/api/orders/{order_id}")
    def get_order(order_id: int, _src: str = Depends(auth)) -> dict:
        return order_or_404(order_id)

    @app.post("/api/orders", status_code=201)
    def create_order(body: OrderIn, src: str = Depends(auth)) -> dict:
        return guard(core.create_order, body.requester, body.destination, [ln.model_dump() for ln in body.lines],
                     body.notes, body.priority, actor=body.requester, source=src)

    @app.post("/api/orders/{order_id}/approve")
    def approve_order(order_id: int, src: str = Depends(auth)) -> dict:
        order_or_404(order_id)
        return guard(core.approve_order, order_id, actor="laptop", source=src)

    @app.post("/api/orders/{order_id}/cancel")
    def cancel_order(order_id: int, body: ReasonIn, src: str = Depends(auth)) -> dict:
        order_or_404(order_id)
        return guard(core.cancel_order, order_id, body.reason, actor="laptop", source=src)

    @app.post("/api/orders/{order_id}/receive")
    def receive_order(order_id: int, body: ReceiveIn, src: str = Depends(auth)) -> dict:
        order_or_404(order_id)
        return guard(core.receive_order, order_id, [ln.model_dump() for ln in body.lines], body.notes,
                     actor=body.receiver, source=src)

    @app.post("/api/orders/{order_id}/return")
    def confirm_return(order_id: int, body: ReturnIn, src: str = Depends(auth)) -> dict:
        order_or_404(order_id)
        return guard(core.confirm_return, order_id, body.count, actor="laptop", source=src)

    @app.post("/api/orders/{order_id}/shortfall")
    def shortfall(order_id: int, body: ShortfallIn, src: str = Depends(auth)) -> dict:
        order_or_404(order_id)
        return guard(core.resolve_shortfall, order_id, body.action, actor="laptop", source=src)

    # -------------------------------------------------------------- discrepancies / quarantine
    @app.get("/api/discrepancies")
    def discrepancies(resolved: bool | None = None, kind: str | None = None, _src: str = Depends(auth)) -> list[dict]:
        return core.orders.discrepancies(resolved, kind)

    @app.get("/api/discrepancies.csv", response_class=PlainTextResponse)
    def discrepancies_csv(_src: str = Depends(auth)) -> str:
        return core.orders.discrepancies_csv()

    @app.post("/api/discrepancies/{disc_id}/resolve")
    def resolve(disc_id: int, body: ResolveIn, src: str = Depends(auth)) -> dict:
        guard(core.orders.resolve_discrepancy, disc_id, body.resolution, body.restock, "laptop", src)
        return {"ok": True}

    # -------------------------------------------------------------- operator
    @app.post("/api/operator/{action}")
    def operator(action: OperatorAction, src: str = Depends(auth)) -> dict:
        """Stop halts the job at once; Reset clears a fault (nothing moves); Resume continues."""
        guard(core.operator_command, action, "laptop", src)
        return {"ok": True, "action": action}

    return app


class RestServer:
    """Runs uvicorn in a daemon thread so the GUI event loop never blocks."""

    def __init__(self, core: StationCore, cfg: Config):
        self.app = create_app(core, cfg)
        self.host = str(cfg.get("rest.host", "127.0.0.1"))
        self.port = int(cfg.get("rest.port", 8000))
        self._server = uvicorn.Server(uvicorn.Config(self.app, host=self.host, port=self.port,
                                                     log_level="warning", access_log=False))
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._server.started)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.run, name="rest-api", daemon=True)
        self._thread.start()
        log.info("REST API on http://%s:%d (docs at /docs)", self.host, self.port)

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
