"""Order and part states for the APEx 2.0 station.

Order lifecycle (one order = one orchestrator job = one TurtleBot trip)::

    Pending -> Approved -> Queued -> Loading -> InTransit -> Delivered -> Closed
       └─────────┴──────────┴──────────┴──> Cancelled

* ``Queued``    - submitted to apex_orchestrator, waiting for the robot / the cell.
* ``Loading``   - the PLC acknowledged the job; the robot is going to / parked at the
                  transfer station; the KUKA picks parts onto the conveyor and down the chute.
* ``InTransit`` - the bin is loaded (all parts, or the operator sent it with a shortfall).
* ``Delivered`` - the robot is parked at the destination waiting for the receipt.
* ``Closed``    - the worker confirmed OK / damaged / missing for every line.
"""

from __future__ import annotations

from enum import Enum


class OrderStatus(str, Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    QUEUED = "Queued"
    LOADING = "Loading"
    IN_TRANSIT = "InTransit"
    DELIVERED = "Delivered"
    CLOSED = "Closed"
    CANCELLED = "Cancelled"

    @property
    def is_open(self) -> bool:
        return self not in (OrderStatus.CLOSED, OrderStatus.CANCELLED)

    @property
    def label(self) -> str:
        return "In transit" if self is OrderStatus.IN_TRANSIT else self.value


ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.APPROVED, OrderStatus.CANCELLED},
    OrderStatus.APPROVED: {OrderStatus.QUEUED, OrderStatus.CANCELLED},
    OrderStatus.QUEUED: {OrderStatus.LOADING, OrderStatus.APPROVED, OrderStatus.CANCELLED},
    OrderStatus.LOADING: {OrderStatus.IN_TRANSIT, OrderStatus.CANCELLED},
    OrderStatus.IN_TRANSIT: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: {OrderStatus.CLOSED},
    OrderStatus.CLOSED: set(),
    OrderStatus.CANCELLED: set(),
}


class PartStatus(str, Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    DISCONTINUED = "Discontinued"


class DiscrepancyKind(str, Enum):
    DAMAGED = "Damaged"              # reported damaged at receipt (goes to the return bin)
    MISSING = "Missing"              # loaded (counted into the bin) but not found at delivery
    NOT_LOADED = "NotLoaded"         # never counted into the bin (chute photoeye short count)
    RETURN_MISMATCH = "ReturnMismatch"   # operator emptied fewer damaged parts than reported
