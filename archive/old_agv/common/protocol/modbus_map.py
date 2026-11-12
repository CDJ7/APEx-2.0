"""PLC <-> Main Station Modbus TCP register map.

Roles
-----
* The **station** runs a Modbus TCP *server* (slave) on ``network.station.plc_modbus_port``.
* The **PLC** is the Modbus *client* (master) - consistent with the PLC being the
  top-level master of the system. It periodically reads the status block and
  writes the command block.

All data are **holding registers** (function codes 03 read, 06/16 write).
Addresses below are 0-based protocol addresses. In most PLC software they
appear as ``400001 + address`` (e.g. ``CMD_CODE`` = address 1 = ``400002``).

The team's PLC is an Allen-Bradley CompactLogix 1769 system (the 1769-ECR in
the report is the right end cap/terminator of the I/O bus; the controller
catalog number is still TBD). CompactLogix has no native Modbus instruction:
use Rockwell's *Modbus TCP Client* Add-On Instruction (socket based) on the
controller's Ethernet port. See docs/PLC_INTEGRATION.md.

Home-station loading (registers 41..68)
---------------------------------------
While an order is loaded the PLC reads ``LOAD_STATE`` and the bin table
(part id + quantity per AGV bin) and drives the KUKA arm. The phone in the
flexbase scans each part's QR code; results appear in ``LAST_SCAN_RESULT`` /
``SCAN_COUNTER`` and the per-bin ``SCANNED`` counts. When the arm is clear the
PLC sends ``Cmd.DEPART_PERMIT``; the station dispatches the AGV once the load
is verified (``LOAD_STATE`` 3 -> 5).

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
    # ---------------- home-station loading (station -> PLC) ---------------
    LOAD_STATE = 41        # 0 none, 1 waiting for arm, 2 scanning, 3 verified, 4 wrong pick, 5 departing
    LOAD_ORDER_ID = 42     # order being loaded (0 = none)
    LOAD_SEQ = 43          # increments for every new load request
    LOAD_LINE_COUNT = 44   # number of bins used (1..6)
    LAST_SCAN_RESULT = 45  # 0 none, 1 ok, 2 wrong part, 3 over quantity, 4 unknown code, 5 no active load
    SCAN_COUNTER = 46      # increments on every phone scan
    AGV_DOCKED = 47        # 1 = Hall sensor: AGV in the charge/wait position
    AGV_MANUAL = 48        # 1 = Flysky RC override active
    PENDING_RETURNS = 49   # damaged returns reported, not yet back at the home station
    DAMAGED_UNITS = 50     # total units in the damaged inventory
    LOAD_BIN_BASE = 51     # bin b (1..6): 51+3(b-1) part id, +1 qty ordered, +2 qty scanned  (51..68)


#: Total number of holding registers exposed.
REGISTER_COUNT = 80
#: Number of AGV bins in the loading table (= station.max_lines_per_order).
LOAD_BINS = 6
LOAD_BIN_STRIDE = 3
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
    DEPART_PERMIT = 13     # KUKA arm clear: the loaded AGV may leave (once the QR check passed)
    LOAD_ABORT = 14        # abandon the current load (order back to Approved)
    START_LOADING = 15     # ARG_ORDER_ID (0 = next approved order); AGV must be docked + idle
    DISCHARGE = 16         # tip the AGV's return bin (linear actuator) while docked


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
    LOADING_STARTED = 13
    LOAD_VERIFIED = 14
    WRONG_PICK = 15
    MANUAL_MODE = 16
    DOCKED = 17
    TASK_FAILED = 18
    RETURNS_DISCHARGED = 19


def bin_reg(bin_no: int, field: int) -> int:
    """Register of ``field`` (0 part id, 1 qty, 2 scanned) for AGV bin ``bin_no`` (1-based)."""
    if not 1 <= bin_no <= LOAD_BINS or not 0 <= field < LOAD_BIN_STRIDE:
        raise ValueError("bin 1..6, field 0..2")
    return Reg.LOAD_BIN_BASE + LOAD_BIN_STRIDE * (bin_no - 1) + field


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
