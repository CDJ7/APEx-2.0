"""REST API for the workstation laptop (FastAPI + uvicorn in a background thread).

Security:
* every endpoint except ``/api/health`` requires the header ``X-API-Key``
  (``security.api_key``, compared in constant time);
* optional source-IP allow list ``security.rest_allowed_ips``;
* request bodies are validated with pydantic (types, ranges, lengths);
* every request is logged with method, path, status and **source IP**, and
  every state-changing call is written to the audit log as ``rest:<ip>``.

Interactive docs (Swagger UI) at ``http://<station>:8000/docs``.

Phone QR scanner (home-station loading)
---------------------------------------
The phone mounted in the flexbase runs a QR scanner app that forwards each
scan to the station. Two forms are accepted so most scanner apps work:

* ``POST /api/scan`` with ``{"code": "APEX:BRG-6204-2RS", "device": "phone1"}``
  and the ``X-API-Key`` header;
* ``GET /api/scan?code=APEX:BRG-6204-2RS&key=<api_key>&device=phone1`` for
  apps that can only open a URL with the scanned text appended. It answers
  with a large, colour-coded page (green = right part, red = wrong) so the
  result is visible on the phone too.
"""

from __future__ import annotations

import hmac
from html import escape
import logging
import threading
import time
from typing import Any, Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.responses import HTMLResponse, JSONResponse
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


class ScanIn(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    device: str = Field(default="phone", max_length=40)


class OverrideIn(BaseModel):
    reason: str = Field(default="", max_length=300)


AgvCommandName = Literal["estop", "reset", "pause", "resume", "return_home", "release", "clear_blocked",
                         "discharge"]

_SCAN_COLOURS = {"ok": "#1b873f", "no_active_load": "#9a6700"}


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

    # -------------------------------------------------------------- loading / scanning
    @app.post("/api/scan")
    def scan(body: ScanIn, src: str = Depends(auth)) -> dict:
        return core.scan_code(body.code, body.device, src)

    @app.get("/api/scan", response_class=HTMLResponse)
    def scan_get(request: Request, code: str = Query(min_length=1, max_length=120),
                 key: str = Query("", max_length=200), device: str = Query("phone", max_length=40)) -> HTMLResponse:
        ip = request.client.host if request.client else "?"
        if not key or not hmac.compare_digest(key, api_key):
            log.warning("REST scan auth failed ip=%s", ip)
            raise HTTPException(status_code=401, detail="invalid or missing key")
        res = core.scan_code(code, device, f"rest:{ip}")
        colour = _SCAN_COLOURS.get(res["result"], "#cf222e")
        title = "OK" if res["result"] == "ok" else res["result"].replace("_", " ").upper()
        page = (f"<!doctype html><meta name=viewport content='width=device-width'><title>{title}</title>"
                f"<body style='margin:0;font-family:sans-serif;background:{colour};color:#fff;text-align:center'>"
                f"<h1 style='font-size:18vw;margin:20vh 0 4vh'>{title}</h1>"
                f"<p style='font-size:6vw'>{escape(res.get('detail', ''))}</p></body>")
        return HTMLResponse(page)

    @app.get("/api/loading")
    def loading(_src: str = Depends(auth)) -> dict:
        return core.loading.snapshot()

    @app.post("/api/loading/start")
    def loading_start(order_id: int | None = None, src: str = Depends(auth)) -> dict:
        return guard(core.start_loading, order_id, actor="laptop", source=src)

    @app.post("/api/loading/permit")
    def loading_permit(src: str = Depends(auth)) -> dict:
        return guard(core.grant_departure, "laptop", src)

    @app.post("/api/loading/override")
    def loading_override(body: OverrideIn, src: str = Depends(auth)) -> dict:
        core.db.audit("laptop", src, "loading.override_reason", "loading", "", body.reason)
        return guard(core.grant_departure, "laptop", src, True)

    @app.post("/api/loading/clear_wrong")
    def loading_clear_wrong(src: str = Depends(auth)) -> dict:
        core.clear_wrong_pick("laptop", src)
        return core.loading.snapshot()

    @app.post("/api/loading/abort")
    def loading_abort(body: OverrideIn, src: str = Depends(auth)) -> dict:
        guard(core.abort_loading, body.reason, actor="laptop", source=src)
        return core.loading.snapshot()

    @app.get("/api/returns")
    def returns(_src: str = Depends(auth)) -> list[dict]:
        return core.orders.pending_returns()

    @app.post("/api/returns/{disc_id}/book")
    def book_return(disc_id: int, src: str = Depends(auth)) -> dict:
        return guard(core.book_return, disc_id, actor="laptop", source=src)

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
