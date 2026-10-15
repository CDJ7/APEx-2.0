"""REST API for the workstation laptop (FastAPI + uvicorn in a background thread).

Security:
* every endpoint except ``/api/health`` requires the header ``X-API-Key``
  (``security.api_key``, compared in constant time);
* optional source-IP allow list ``security.rest_allowed_ips``;
* request bodies are validated with pydantic (types, ranges, lengths);
* every request is logged with method, path, status and **source IP**, and
  every state-changing call is written to the audit log as ``rest:<ip>``.

Interactive docs (Swagger UI) at ``http://<station>:8000/docs``.
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
from typing import Any, Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from common.config import Config
from station_gui.core.inventory import ValidationError
from station_gui.core.station import StationCore

log = logging.getLogger("station.rest")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# ------------------------------------------------------------------ schemas
class OrderLineIn(BaseModel):
    part_number: str = Field(min_length=1, max_length=40)
    quantity: int = Field(ge=1, le=10_000)


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


class CancelIn(BaseModel):
    reason: str = Field(default="", max_length=300)


class AgvCommandIn(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)


AgvCommandName = Literal["estop", "reset", "pause", "resume", "return_home", "release", "clear_blocked"]


# ------------------------------------------------------------------ app
def create_app(core: StationCore, cfg: Config) -> FastAPI:
    api_key = str(cfg.require("security.api_key"))
    allowed = set(cfg.get("security.rest_allowed_ips", []) or [])
    app = FastAPI(title="APEX Station API", version="1.0",
                  description="Inventory, orders and AGV status for the APEX AGV system.")

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

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "agv_online": core.agv.online, "time": time.time()}

    @app.get("/api/kpis")
    def kpis(_src: str = Depends(auth)) -> dict:
        return core.kpis()

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

    @app.get("/api/stations")
    def stations(_src: str = Depends(auth)) -> dict:
        return {"home": core.home_station, "destinations": core.destination_names(), "stations": core.stations}

    @app.get("/api/orders")
    def list_orders(status: str | None = None, limit: int = Query(100, ge=1, le=1000),
                    _src: str = Depends(auth)) -> list[dict]:
        return core.orders.list(status=status, limit=limit)

    @app.get("/api/orders/{order_id}")
    def get_order(order_id: int, _src: str = Depends(auth)) -> dict:
        order = core.orders.get(order_id)
        if order is None:
            raise HTTPException(404, "order not found")
        return order

    @app.post("/api/orders", status_code=201)
    def create_order(body: OrderIn, src: str = Depends(auth)) -> dict:
        return guard(core.create_order, body.requester, body.destination,
                     [ln.model_dump() for ln in body.lines], body.notes, body.priority,
                     actor=body.requester, source=src)

    @app.post("/api/orders/{order_id}/cancel")
    def cancel_order(order_id: int, body: CancelIn, src: str = Depends(auth)) -> dict:
        return guard(core.cancel_order, order_id, body.reason, actor="laptop", source=src)

    @app.post("/api/orders/{order_id}/approve")
    def approve_order(order_id: int, src: str = Depends(auth)) -> dict:
        return guard(core.approve_order, order_id, actor="laptop", source=src)

    @app.post("/api/orders/{order_id}/receive")
    def receive_order(order_id: int, body: ReceiveIn, src: str = Depends(auth)) -> dict:
        return guard(core.receive_order, order_id, [ln.model_dump() for ln in body.lines], body.notes,
                     actor=body.receiver, source=src)

    @app.get("/api/discrepancies")
    def discrepancies(resolved: bool | None = None, _src: str = Depends(auth)) -> list[dict]:
        return core.orders.discrepancies(resolved)

    @app.get("/api/agv")
    def agv(_src: str = Depends(auth)) -> dict:
        return core.agv_status()

    @app.post("/api/agv/{command}")
    def agv_command(command: AgvCommandName, body: AgvCommandIn | None = None, src: str = Depends(auth)) -> dict:
        ack = guard(core.agv_command, command, (body.data if body else {}), actor="laptop", source=src)
        if not ack.get("ok"):
            raise HTTPException(409, ack.get("error", "AGV rejected the command"))
        return ack

    return app


class RestServer:
    """Runs uvicorn in a daemon thread so the GUI event loop never blocks."""

    def __init__(self, core: StationCore, cfg: Config):
        self.app = create_app(core, cfg)
        self.host = cfg.bind_address("station")
        self.port = int(cfg.require("network.station.rest_port"))
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
