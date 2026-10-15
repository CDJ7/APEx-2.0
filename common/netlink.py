"""Newline-delimited JSON (NDJSON) transport helpers.

* :func:`read_message` / :func:`write_message` - asyncio stream helpers (servers)
* :class:`JsonLineClient` - a thread-based, auto-reconnecting client used by the
  station (to talk to the AGV) and by the test tools. It never blocks the
  caller: messages arrive on a callback from a background thread.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from typing import Any, Callable

from common.protocol.messages import (
    MAX_MESSAGE_BYTES,
    MessageError,
    MsgType,
    decode_message,
    encode_message,
    make_message,
)

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ asyncio
async def read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one message; returns ``None`` on clean EOF. Raises :class:`MessageError`."""
    try:
        line = await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError:
        return None
    except asyncio.LimitOverrunError as exc:
        raise MessageError("line exceeds limit") from exc
    if not line.strip():
        return await read_message(reader)
    return decode_message(line)


async def write_message(writer: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
    writer.write(encode_message(msg))
    await writer.drain()


STREAM_LIMIT = MAX_MESSAGE_BYTES + 1024


# ------------------------------------------------------------- thread client
class JsonLineClient:
    """Auto-reconnecting NDJSON client running in a daemon thread.

    Args:
        host, port: server address.
        token: shared secret sent in the ``hello`` message.
        client_name: identity sent in ``hello`` (e.g. ``"station"``).
        on_message: called (from the client thread) for every received message.
        on_connection: called with ``True``/``False`` on connect/disconnect.
        reconnect_s: delay between reconnection attempts.
    """

    def __init__(self, host: str, port: int, token: str, client_name: str,
                 on_message: Callable[[dict[str, Any]], None] | None = None,
                 on_connection: Callable[[bool], None] | None = None,
                 reconnect_s: float = 2.0, connect_timeout_s: float = 3.0):
        self.host, self.port = host, port
        self._token = token
        self._name = client_name
        self._on_message = on_message or (lambda m: None)
        self._on_connection = on_connection or (lambda c: None)
        self._reconnect_s = reconnect_s
        self._timeout = connect_timeout_s
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False

    # -------------------------------------------------------------- control
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"ndjson-{self._name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close()
        if self._thread:
            self._thread.join(timeout=2.0)

    def send(self, msg_type: MsgType | str, data: dict[str, Any] | None = None) -> str | None:
        """Send a message; returns its id, or ``None`` if not connected."""
        msg = make_message(msg_type, data, src=self._name)
        return msg["id"] if self.send_raw(msg) else None

    def send_raw(self, msg: dict[str, Any]) -> bool:
        with self._send_lock:
            sock = self._sock
            if sock is None or not self.connected:
                return False
            try:
                sock.sendall(encode_message(msg))
                return True
            except OSError as exc:
                log.warning("send failed host=%s err=%s", self.host, exc)
                self._close()
                return False

    # ------------------------------------------------------------- internals
    def _close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self.connected:
            self.connected = False
            try:
                self._on_connection(False)
            except Exception:  # noqa: BLE001 - never let a callback kill the thread
                log.exception("on_connection callback failed")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sock = socket.create_connection((self.host, self.port), timeout=self._timeout)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                sock.settimeout(1.0)
                self._sock = sock
                hello = make_message(MsgType.HELLO, {"token": self._token, "client": self._name}, src=self._name)
                sock.sendall(encode_message(hello))
                self.connected = True
                log.info("connected host=%s port=%s", self.host, self.port)
                self._on_connection(True)
                self._read_loop(sock)
            except (OSError, MessageError) as exc:
                if not self._stop.is_set():
                    log.debug("connection error host=%s port=%s err=%s", self.host, self.port, exc)
            finally:
                self._close()
            self._stop.wait(self._reconnect_s)

    def _read_loop(self, sock: socket.socket) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise OSError("server closed connection")
            buf.extend(chunk)
            if len(buf) > MAX_MESSAGE_BYTES:
                raise MessageError("receive buffer overflow")
            while True:
                idx = buf.find(b"\n")
                if idx < 0:
                    break
                line, buf = bytes(buf[:idx]), buf[idx + 1:]
                if not line.strip():
                    continue
                try:
                    msg = decode_message(line)
                except MessageError as exc:
                    log.warning("bad message from %s: %s", self.host, exc)
                    continue
                try:
                    self._on_message(msg)
                except Exception:  # noqa: BLE001
                    log.exception("on_message callback failed")


def wait_for(predicate: Callable[[], bool], timeout_s: float, poll_s: float = 0.05) -> bool:
    """Poll ``predicate`` until true or timeout (helper for tests/tools)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return predicate()
