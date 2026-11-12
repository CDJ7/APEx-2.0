"""Front IR sensors covering the camera/LiDAR dead zone directly ahead of the bumper.

Design decision (see docs/WIRING.md): the IR sensors are wired to the
**Arduino**, not the Pi, because

* analog sensors (Sharp GP2Y0A21) need an ADC and the Pi 5 has none;
* most IR modules are 5 V parts, Pi GPIO is 3.3 V-only (the R4 is 5 V tolerant);
* all low-level I/O then arrives in one timestamped ``STAT`` frame.

For 3.3 V digital modules wired directly to the Pi there is
:class:`GpioIRSource` (``agv.ir.source: pi_gpio``) which uses ``gpiozero`` with
the ``lgpio`` pin factory - ``RPi.GPIO`` does **not** work on the Pi 5.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from common.config import Config
from common.protocol.serial_protocol import StatusReport

log = logging.getLogger("agv.ir")

# Sharp GP2Y0A21YK0F empirical fit: distance_cm = A * V^B (valid ~10..80 cm)
SHARP_A = 29.988
SHARP_B = -1.173
SHARP_MIN_CM = 10.0
SHARP_MAX_CM = 80.0
SHARP_MIN_V = (SHARP_MAX_CM / SHARP_A) ** (1.0 / SHARP_B)   # ~0.43 V at 80 cm


def sharp_volts_to_cm(volts: float) -> float:
    """Convert a GP2Y0A21 output voltage to distance; ``inf`` when out of range."""
    if volts < SHARP_MIN_V:
        return math.inf
    return max(SHARP_MIN_CM, SHARP_A * volts ** SHARP_B)


def sharp_cm_to_volts(cm: float) -> float:
    """Inverse model (used by the simulator)."""
    if not math.isfinite(cm) or cm > SHARP_MAX_CM:
        return 0.3                           # sensor floor reading beyond range
    cm = max(SHARP_MIN_CM, cm)
    return (cm / SHARP_A) ** (1.0 / SHARP_B)


@dataclass
class IRReading:
    """Fused reading of all front IR sensors."""

    distances_cm: list[float] = field(default_factory=list)   # inf = nothing seen
    triggered: list[bool] = field(default_factory=list)       # True = obstacle inside stop distance
    ok: bool = True

    @property
    def any_triggered(self) -> bool:
        return any(self.triggered)

    @property
    def min_distance_m(self) -> float:
        finite = [d for d in self.distances_cm if math.isfinite(d)]
        return min(finite) / 100.0 if finite else math.inf


class IRProcessor:
    """Converts raw IR values from the Arduino ``STAT`` frame into an :class:`IRReading`."""

    def __init__(self, cfg: Config):
        ir = cfg.section("agv.ir")
        self.type = str(ir.get("type", "sharp_gp2y0a21"))
        self.count = int(ir.get("count", 2))
        self.stop_cm = float(ir.get("stop_distance_cm", 25.0))
        self.adc_max = (1 << int(ir.get("adc_bits", 10))) - 1
        self.vref = float(ir.get("adc_vref", 5.0))

    def from_status(self, st: StatusReport) -> IRReading:
        raws = [st.ir1, st.ir2][: self.count]
        if self.type == "digital":
            trig = [bool(r) for r in raws]
            return IRReading([self.stop_cm if t else math.inf for t in trig], trig)
        dists = [sharp_volts_to_cm(r / self.adc_max * self.vref) for r in raws]
        return IRReading(dists, [d <= self.stop_cm for d in dists])


class GpioIRSource:
    """Digital IR modules on Pi GPIO (only for 3.3 V-output modules!)."""

    def __init__(self, cfg: Config):
        from gpiozero import DigitalInputDevice  # noqa: WPS433 - Pi-only dependency
        from gpiozero.pins.lgpio import LGPIOFactory

        factory = LGPIOFactory()
        pins = [int(p) for p in cfg.get("agv.ir.gpio_pins", [17, 27])]
        # Typical modules pull OUT low when an obstacle is seen. With pull_up=True
        # gpiozero treats LOW as "active", so value == 1 means obstacle.
        self._devices = [DigitalInputDevice(p, pull_up=True, pin_factory=factory) for p in pins]
        self.stop_cm = float(cfg.get("agv.ir.stop_distance_cm", 25.0))
        log.info("IR on Pi GPIO pins=%s", pins)

    def read(self) -> IRReading:
        trig = [dev.value == 1 for dev in self._devices]
        return IRReading([self.stop_cm if t else math.inf for t in trig], trig)

    def close(self) -> None:
        for dev in self._devices:
            dev.close()
