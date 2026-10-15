"""PLC <-> Main Station Modbus TCP register map.

Roles
-----
* The **station** runs a Modbus TCP *server* (slave) on ``network.station.plc_modbus_port``.
* The **PLC** is the Modbus *client* (master) - consistent with the PLC being the
  top-level master of the system. It periodically reads the status block and
  writes the command block.

All data are **holding registers** (function codes 03 read, 06/16 write).
Addresses below are 0-based protocol addresses. In Allen-Bradley Micro800
``MSG_MODBUS``/``MSG_MODBUS2`` blocks and in most PLC software they appear as
``400001 + address`` (e.g. ``CMD_CODE`` = address 1 = ``400002``).

Command handshake (edge-triggered, idempotent)
----------------------------------------------
1. PLC writes the argument registers (``ARG_*``) and ``CMD_CODE``.
2. PLC increments ``CMD_SEQ`` (any change triggers execution; write it LAST, or
   write registers 1..7 in a single FC16 request).
3. Station executes once, then writes ``CMD_RESULT``/``CMD_RESULT_VALUE`` and
   finally copies ``CMD_SEQ`` into ``CMD_ACK_SEQ``.
4. PLC waits until ``CMD_ACK_SEQ == CMD_SEQ`` and reads the result.

Signed values (pose) are int16 two's complement.
"""

from __future__ import annotations

from enum import IntEnum


class Reg(IntEnum):
    # ---------------- written by the PLC (command block) -----------------
    PLC_HEARTBEAT = 0      # PLC increments ~1 Hz; station flags the PLC offline if it stalls
    CMD_CODE = 1           # see Cmd
    CMD_SEQ = 2            # change to execute CMD_CODE
    ARG_ORDER_ID = 3       # order id for DISPATCH_ORDER / CANCEL_ORDER / APPROVE_ORDER
    ARG_PART_ID = 4        # part database id for CREATE_ORDER
    ARG_QTY = 5            # quantity for CREATE_ORDER
    ARG_STATION_ID = 6     # destination station id (routes yaml 'plc_id') for CREATE_ORDER
    ARG_RESERVED = 7

    # ---------------- written by the station (status block) --------------
    CMD_ACK_SEQ = 20       # = CMD_SEQ once the command has been processed
    CMD_RESULT = 21        # see Result
    CMD_RESULT_VALUE = 22  # e.g. the new order id for CREATE_ORDER
    STATION_HEARTBEAT = 23 # station increments every second
    AGV_CONNECTED = 24     # 1 = telemetry is fresh
    AGV_STATE = 25         # AgvState.code (common/protocol/states.py)
    AGV_ORDER_ID = 26      # order currently being delivered (0 = none)
    AGV_BATTERY_PCT = 27   # 0..100 (0xFFFF = unknown)
    AGV_POSE_X_CM = 28     # int16, map frame
    AGV_POSE_Y_CM = 29     # int16, map frame
    AGV_HEADING_DDEG = 30  # 0..3599 deci-degrees
    AGV_FAULT_WORD = 31    # bitfield, bit (code-1) of FaultCode
    AGV_ESTOP = 32         # 1 = E-stop active (hardware or software)
    AGV_OBSTACLE = 33      # 0 none, 1 waiting for transient obstacle, 2 path blocked / rerouting
    OPEN_ORDERS = 34       # orders not yet Closed/Cancelled
    PENDING_DISPATCH = 35  # approved orders waiting for the AGV
    LOW_STOCK_COUNT = 36   # parts at/below reorder threshold
    LAST_EVENT_CODE = 37   # see EventCode
    LAST_EVENT_COUNTER = 38  # increments on every new event
    PLC_LINK_OK = 39       # 1 = station sees PLC heartbeat changing
    AGV_DEST_STATION_ID = 40


#: Total number of holding registers exposed.
REGISTER_COUNT = 50
#: Registers the PLC is allowed to write (writes elsewhere are rejected with exception 02).
PLC_WRITABLE = range(Reg.PLC_HEARTBEAT, Reg.ARG_RESERVED + 1)


class Cmd(IntEnum):
    NONE = 0
    DISPATCH_NEXT = 1      # send the oldest approved order to the AGV
    DISPATCH_ORDER = 2     # ARG_ORDER_ID
    CANCEL_ORDER = 3       # ARG_ORDER_ID
    ESTOP = 4              # software E-stop of the AGV
    RESET = 5              # clear software E-stop / faults on the AGV
    RETURN_HOME = 6
    PAUSE = 7
    RESUME = 8
    CREATE_ORDER = 9       # ARG_PART_ID, ARG_QTY, ARG_STATION_ID -> RESULT_VALUE = order id
    RELEASE = 10           # AGV leaves the destination after unloading
    CLEAR_BLOCKED = 11     # clear all blocked route segments
    APPROVE_ORDER = 12     # ARG_ORDER_ID


class Result(IntEnum):
    OK = 0
    UNKNOWN_COMMAND = 1
    INVALID_ARGS = 2
    REJECTED = 3           # e.g. AGV busy, order in wrong state, insufficient stock
    AGV_OFFLINE = 4
    INTERNAL_ERROR = 5
    BUSY = 6               # command still executing (transient)


class EventCode(IntEnum):
    NONE = 0
    ORDER_CREATED = 1
    ORDER_DISPATCHED = 2
    AGV_ARRIVED = 3
    AGV_RETURNED_HOME = 4
    OBSTACLE = 5
    SEGMENT_BLOCKED = 6
    REROUTING = 7
    FAULT = 8
    ESTOP = 9
    ORDER_CLOSED = 10
    ORDER_CANCELLED = 11
    LOW_STOCK = 12


def to_int16(value: int) -> int:
    """Encode a signed integer as an unsigned 16-bit register value (clamped)."""
    value = max(-32768, min(32767, int(value)))
    return value & 0xFFFF


def from_int16(reg: int) -> int:
    """Decode an unsigned 16-bit register into a signed integer."""
    reg &= 0xFFFF
    return reg - 0x10000 if reg >= 0x8000 else reg


def to_uint16(value: int | None, unknown: int = 0xFFFF) -> int:
    if value is None:
        return unknown
    return max(0, min(0xFFFF, int(value)))
