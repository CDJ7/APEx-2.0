"""Enumerations shared by every component.

Each enum member carries a stable integer ``code`` that is used on the PLC
Modbus interface (registers only hold integers). Never renumber existing codes.
"""

from __future__ import annotations

from enum import Enum


class _CodedEnum(str, Enum):
    """A string enum whose members also have a stable integer code."""

    code: int

    def __new__(cls, value: str, code: int) -> "_CodedEnum":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.code = code
        return obj

    @classmethod
    def from_code(cls, code: int) -> "_CodedEnum":
        for member in cls:
            if member.code == code:
                return member
        raise ValueError(f"{cls.__name__}: unknown code {code}")

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class AgvState(_CodedEnum):
    """AGV master state machine (see ``agv_pi/controller/state_machine.py``)."""

    IDLE = ("IDLE", 0)
    RECEIVING_TASK = ("RECEIVING_TASK", 1)
    NAVIGATING = ("NAVIGATING", 2)
    OBSTACLE_WAIT = ("OBSTACLE_WAIT", 3)
    REROUTING = ("REROUTING", 4)
    ARRIVED = ("ARRIVED", 5)
    RETURNING = ("RETURNING", 6)
    FAULT = ("FAULT", 7)
    E_STOP = ("E_STOP", 8)
    MANUAL = ("MANUAL", 9)          # teleop / mapping
    OFFLINE = ("OFFLINE", 15)       # used by the station when no telemetry arrives

    @property
    def is_moving_state(self) -> bool:
        return self in (AgvState.NAVIGATING, AgvState.RETURNING, AgvState.REROUTING)

    @property
    def is_busy(self) -> bool:
        """True when the AGV cannot accept a new delivery."""
        return self not in (AgvState.IDLE,)


class OrderStatus(_CodedEnum):
    """Order lifecycle: Pending -> Approved -> Dispatched -> Delivered -> Closed."""

    PENDING = ("Pending", 0)
    APPROVED = ("Approved", 1)
    DISPATCHED = ("Dispatched", 2)     # sent to the AGV / in transit
    DELIVERED = ("Delivered", 3)       # AGV arrived at destination
    CLOSED = ("Closed", 4)             # receiving report completed
    CANCELLED = ("Cancelled", 5)

    @property
    def is_open(self) -> bool:
        return self in (OrderStatus.PENDING, OrderStatus.APPROVED, OrderStatus.DISPATCHED, OrderStatus.DELIVERED)


#: Allowed order status transitions (enforced by the station order service).
ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.APPROVED, OrderStatus.CANCELLED},
    OrderStatus.APPROVED: {OrderStatus.DISPATCHED, OrderStatus.CANCELLED},
    OrderStatus.DISPATCHED: {OrderStatus.DELIVERED, OrderStatus.APPROVED, OrderStatus.CANCELLED},
    OrderStatus.DELIVERED: {OrderStatus.CLOSED},
    OrderStatus.CLOSED: set(),
    OrderStatus.CANCELLED: set(),
}


class PartStatus(str, Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    DISCONTINUED = "Discontinued"


class LineCondition(str, Enum):
    """Receiving condition for an order line."""

    OK = "Received OK"
    DAMAGED = "Damaged"
    MISSING = "Missing"


class FaultCode(_CodedEnum):
    """AGV fault codes (bit positions in the Modbus fault word = code)."""

    NONE = ("NONE", 0)
    ARDUINO_LINK = ("ARDUINO_LINK", 1)      # serial link lost / STAT timeout
    ARDUINO_WATCHDOG = ("ARDUINO_WATCHDOG", 2)
    LIDAR = ("LIDAR", 3)                    # lidar stale / disconnected
    CAMERA = ("CAMERA", 4)
    LOCALIZATION = ("LOCALIZATION", 5)
    NO_ROUTE = ("NO_ROUTE", 6)
    BUMPER = ("BUMPER", 7)
    LOW_BATTERY = ("LOW_BATTERY", 8)
    HW_ESTOP = ("HW_ESTOP", 9)
    INTERNAL = ("INTERNAL", 10)

    @property
    def bit(self) -> int:
        return 0 if self is FaultCode.NONE else (1 << (self.code - 1))


def faults_to_word(faults: "set[FaultCode] | list[FaultCode]") -> int:
    """Pack a set of fault codes into a 16-bit word (for Modbus)."""
    word = 0
    for f in faults:
        word |= f.bit
    return word & 0xFFFF
