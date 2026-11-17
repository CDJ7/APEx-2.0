"""PLC client: one API, two backends.

* :class:`LogixBackend` - the real Compact GuardLogix over EtherNet/IP (``pycomm3.LogixDriver``).
* :class:`SimBackend`   - ``apex_cell_sim``'s tag server (newline-delimited JSON over TCP).

Both expose the *same* tag names (``plc_tags.yaml``), so switching from the simulator to
the real cell is the config value ``plc.backend: logix|sim``. Every write goes through
:meth:`PlcClient.write`, which enforces the allow-list in :mod:`apex_plc_bridge.tags`.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from typing import Any, Protocol

from apex_plc_bridge.tags import TagSpec, TagWriteRefused

log = logging.getLogger("apex.plc")


class Backend(Protocol):
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def read(self, paths: list[str]) -> dict[str, Any]: ...
    def write(self, path: str, value: Any) -> None: ...


class LogixBackend:
    """Real PLC via pycomm3 (EtherNet/IP CIP explicit messaging, TCP 44818)."""

    def __init__(self, address: str, timeout_s: float = 2.0):
        self.address, self.timeout_s = address, timeout_s
        self._plc = None

    def connect(self) -> None:
        from pycomm3 import LogixDriver   # imported lazily: not needed for the simulator

        self._plc = LogixDriver(self.address, init_tags=True)
        self._plc.socket_timeout = self.timeout_s
        self._plc.open()
        log.info("connected to Logix PLC at %s (%s)", self.address, self._plc.info.get("product_name", "?"))

    def close(self) -> None:
        if self._plc:
            self._plc.close()
            self._plc = None

    def read(self, paths: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for res in self._plc.read(*paths):
            if res.error:
                raise IOError(f"read {res.tag}: {res.error}")
            out[res.tag] = res.value
        return out

    def write(self, path: str, value: Any) -> None:
        res = self._plc.write((path, value))
        if res.error:
            raise IOError(f"write {path}: {res.error}")


class SimBackend:
    """TCP JSON client for apex_cell_sim's tag server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 44818, timeout_s: float = 2.0):
        self.host, self.port, self.timeout_s = host, port, timeout_s
        self._sock: socket.socket | None = None
        self._file = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout_s)
        self._file = self._sock.makefile("rwb")
        log.info("connected to apex_cell_sim at %s:%d", self.host, self.port)

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def _call(self, req: dict) -> dict:
        with self._lock:
            self._file.write(json.dumps(req).encode() + b"\n")
            self._file.flush()
            line = self._file.readline()
        if not line:
            raise ConnectionError("cell sim closed the connection")
        rsp = json.loads(line)
        if not rsp.get("ok"):
            raise IOError(rsp.get("error", "sim error"))
        return rsp

    def read(self, paths: list[str]) -> dict[str, Any]:
        return self._call({"op": "read", "tags": paths})["values"]

    def write(self, path: str, value: Any) -> None:
        self._call({"op": "write", "tag": path, "value": value})


class PlcClient:
    """Allow-listed access to the handshake tags, by member name (e.g. ``"Job_Request"``)."""

    def __init__(self, spec: TagSpec, backend: Backend):
        self.spec, self.backend = spec, backend
        self.connected = False

    def connect(self) -> None:
        self.backend.connect()
        self.connected = True

    def close(self) -> None:
        self.backend.close()
        self.connected = False

    def read_all(self) -> dict[str, Any]:
        """Read every tag in the spec; keys are member names."""
        defs = list(self.spec.tags.values())
        paths = {self._io_path(d): d for d in defs}
        try:
            values = self.backend.read(list(paths))
        except (OSError, ConnectionError):
            self.connected = False
            raise
        return {d.name: values.get(p, d.default()) for p, d in paths.items()}

    @staticmethod
    def _io_path(tag) -> str:
        """pycomm3 array syntax: ``Tag.Member{n}`` reads/writes n elements."""
        return f"{tag.path}{{{tag.length}}}" if tag.is_array else tag.path

    def write(self, name: str, value: Any) -> None:
        """Write one allow-listed station->PLC member. Refusals are logged and raised."""
        try:
            tag = self.spec.check_write(name)
        except TagWriteRefused:
            log.error("REFUSED write to %r (not on the station allow-list)", name)
            raise
        if tag.is_array and (not isinstance(value, list) or len(value) > tag.length):
            raise ValueError(f"{name} expects a list of at most {tag.length} values")
        if tag.is_array:
            value = list(value) + [0] * (tag.length - len(value))
        self.backend.write(self._io_path(tag), value)


class InProcessBackend:
    """Backend for an object with ``station_read`` / ``station_write`` (e.g. a CellModel) in the same
    process - deterministic tests and single-process simulations; no sockets."""

    def __init__(self, target: Any):
        self.target = target

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def read(self, paths: list[str]) -> dict[str, Any]:
        return self.target.station_read(paths)

    def write(self, path: str, value: Any) -> None:
        self.target.station_write(path, value)
