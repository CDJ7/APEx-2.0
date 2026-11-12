"""Slamtec RPLIDAR A1M8 driver - direct implementation of the serial protocol.

Implemented directly (instead of depending on ``rplidar-roboticia``) so that
error handling and reconnection are fully under our control and there is no
dependency on an unmaintained package. Protocol reference: Slamtec
"RPLIDAR Interface Protocol and Application Notes" (LR001).

* USB via the CP2102 adapter, 115200 8N1 -> ``/dev/rplidar`` (udev rule).
* On the A1 the scan motor is switched by the adapter's **DTR** line:
  DTR low = motor on, DTR high = motor off.
* Requests:  ``A5 <cmd>``. Responses start with the 7-byte descriptor
  ``A5 5A <len:30 bits | mode:2 bits> <type>``.
* Scan nodes are 5 bytes::

      byte0: quality[7:2] | !S[1] | S[0]     (S = start of a new revolution)
      byte1: angle_q6[6:0] << 1 | C(=1)
      byte2: angle_q6[14:7]
      byte3-4: distance_q2 (little endian)   distance_mm = q2 / 4, angle = q6 / 64
"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass
from typing import Iterator

from agv_pi.lidar.scan import RawMeasurement

log = logging.getLogger("agv.lidar.driver")

SYNC_A, SYNC_B = 0xA5, 0x5A
CMD_STOP, CMD_RESET, CMD_SCAN = 0x25, 0x40, 0x20
CMD_GET_INFO, CMD_GET_HEALTH = 0x50, 0x52
TYPE_INFO, TYPE_HEALTH, TYPE_SCAN = 0x04, 0x06, 0x81
HEALTH_STATUS = {0: "Good", 1: "Warning", 2: "Error"}


class RPLidarError(RuntimeError):
    """Any protocol / descriptor / health error. The service reconnects on it."""


@dataclass
class DeviceInfo:
    model: int
    firmware: str
    hardware: int
    serial: str


def parse_node(node: bytes) -> RawMeasurement | None:
    """Decode one 5-byte scan node; ``None`` if the check bits are invalid."""
    b0, b1 = node[0], node[1]
    start = b0 & 0x1
    inv_start = (b0 >> 1) & 0x1
    if start == inv_start or not (b1 & 0x1):
        return None
    quality = b0 >> 2
    angle = ((b1 >> 1) | (node[2] << 7)) / 64.0
    dist = (node[3] | (node[4] << 8)) / 4.0
    return quality, angle, dist


def node_is_start(node: bytes) -> bool:
    return bool(node[0] & 0x1)


class RPLidarA1:
    """Blocking driver - run it in its own thread (see ``lidar_service.py``)."""

    def __init__(self, port: str = "/dev/rplidar", baud: int = 115200, timeout: float = 1.0):
        self.port, self.baud, self.timeout = port, baud, timeout
        self._ser = None
        self._scanning = False

    # ------------------------------------------------------------- connection
    def connect(self) -> None:
        import serial  # pyserial

        self.disconnect()
        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=self.timeout,
                                      parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE)
        except serial.SerialException as exc:
            raise RPLidarError(f"cannot open {self.port}: {exc}") from exc
        self.stop_motor()
        log.info("RPLIDAR connected port=%s", self.port)

    def disconnect(self) -> None:
        if self._ser is None:
            return
        try:
            if self._scanning:
                self.stop()
            self.stop_motor()
            self._ser.close()
        except Exception:  # noqa: BLE001 - best effort on a dying port
            pass
        self._ser = None
        self._scanning = False

    def start_motor(self) -> None:
        self._require().dtr = False

    def stop_motor(self) -> None:
        self._require().dtr = True

    # ------------------------------------------------------------- requests
    def _require(self):
        if self._ser is None:
            raise RPLidarError("not connected")
        return self._ser

    def _send(self, cmd: int) -> None:
        self._require().write(bytes([SYNC_A, cmd]))

    def _read_descriptor(self) -> tuple[int, bool, int]:
        desc = self._require().read(7)
        if len(desc) != 7:
            raise RPLidarError("descriptor timeout")
        if desc[0] != SYNC_A or desc[1] != SYNC_B:
            raise RPLidarError(f"bad descriptor sync {desc.hex()}")
        size_mode = struct.unpack("<I", desc[2:6])[0]
        size = size_mode & 0x3FFFFFFF
        continuous = (size_mode >> 30) == 1
        return size, continuous, desc[6]

    def _read_exact(self, n: int) -> bytes:
        data = self._require().read(n)
        if len(data) != n:
            raise RPLidarError(f"read timeout ({len(data)}/{n} bytes)")
        return data

    def reset(self) -> None:
        """Soft reset the core (recovers from the 'Error' health state)."""
        self._send(CMD_RESET)
        time.sleep(0.05)
        self._require().reset_input_buffer()

    def stop(self) -> None:
        self._send(CMD_STOP)
        self._scanning = False
        time.sleep(0.01)
        self._require().reset_input_buffer()

    def get_info(self) -> DeviceInfo:
        self._send(CMD_GET_INFO)
        size, _, dtype = self._read_descriptor()
        if dtype != TYPE_INFO or size != 20:
            raise RPLidarError("bad info descriptor")
        raw = self._read_exact(20)
        return DeviceInfo(model=raw[0], firmware=f"{raw[2]}.{raw[1]:02d}", hardware=raw[3], serial=raw[4:].hex().upper())

    def get_health(self) -> tuple[str, int]:
        self._send(CMD_GET_HEALTH)
        size, _, dtype = self._read_descriptor()
        if dtype != TYPE_HEALTH or size != 3:
            raise RPLidarError("bad health descriptor")
        raw = self._read_exact(3)
        return HEALTH_STATUS.get(raw[0], "Unknown"), raw[1] | (raw[2] << 8)

    # ------------------------------------------------------------- scanning
    def iter_scans(self, max_buffer_bytes: int = 3000) -> Iterator[list[RawMeasurement]]:
        """Start the motor + scan and yield one list of measurements per revolution.

        Health is checked first; an 'Error' state triggers a reset (as the
        datasheet recommends). Bad nodes are skipped by sliding one byte to
        resynchronise. If the host falls behind (input buffer above
        ``max_buffer_bytes``) the backlog is flushed so the data stays real-time.
        """
        ser = self._require()
        status, code = self.get_health()
        log.info("RPLIDAR health=%s code=%d", status, code)
        if status == "Error":
            self.reset()
            raise RPLidarError(f"lidar health error code {code} (reset issued)")
        self.start_motor()
        time.sleep(0.5)                         # let the motor spin up
        ser.reset_input_buffer()
        self._send(CMD_SCAN)
        size, continuous, dtype = self._read_descriptor()
        if size != 5 or not continuous or dtype != TYPE_SCAN:
            raise RPLidarError("bad scan descriptor")
        self._scanning = True

        buf = bytearray()
        scan: list[RawMeasurement] = []
        bad = 0
        while True:
            if ser.in_waiting > max_buffer_bytes:
                ser.reset_input_buffer()        # we are too slow: drop the backlog
                buf.clear()
                scan = []
            chunk = ser.read(max(5, min(ser.in_waiting, 1000)))
            if not chunk:
                raise RPLidarError("scan data timeout (motor stalled / cable?)")
            buf.extend(chunk)
            while len(buf) >= 5:
                node = bytes(buf[:5])
                meas = parse_node(node)
                if meas is None:
                    del buf[0]                  # resync
                    bad += 1
                    if bad > 2000:
                        raise RPLidarError("too many corrupt scan nodes")
                    continue
                del buf[:5]
                if node_is_start(node) and len(scan) > 30:
                    yield scan
                    scan = []
                    bad = 0
                if meas[2] > 0:
                    scan.append(meas)
