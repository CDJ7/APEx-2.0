# APEx 2.0 — TurtleBot payload: lift + tilt tray

**Status:** design draft (decided 2026-11-19). Nothing is bought or built yet.
Numbers marked **[TBD]** must be measured or confirmed before parts are ordered.

## 1. What it does

The TurtleBot 4 carries a two-level module on its top plate:

- **Level 1 (fixed deck):** a 12 V battery pack, fuse, switch, motor driver
  and the base of the lift.
- **Level 2 (moving tray):** a padded tray on a short **linear-actuator lift**,
  hinged on one edge so a **tilt actuator** can tip it.

One tray carries everything on the trip:

1. **Transfer station:** the robot parks under the chute with the tray down,
   raises it to just below the chute outlet, the parts drop in, the tray
   lowers, and the robot drives off.
2. **Delivery station:** the worker takes the **OK** parts and leaves any
   **damaged** parts on the tray.
3. **Return pose (`return_drop`):** the robot **tilts the tray by itself** and
   the damaged parts slide into a **quarantine bin**. The tray tilts back
   level and the robot takes the next job or docks.

The robot never enters the FlexBase guarding.

## 2. Decisions so far

| # | Decision | Date |
|---|---|---|
| B-1 | The **chute starts at the conveyor exit** and slopes down to a fixed outlet height. The lift only makes the last fine adjustment; it does **not** lift parts to conveyor height. | 2026-11-19 |
| B-2 | Parts are **light 3D-printed parts**. | 2026-11-19 |
| B-3 | The robot **dumps damaged parts by itself** by tilting the tray at `return_drop`. This replaces the earlier plan of an operator emptying a separate return bin. | 2026-11-19 |
| B-4 | The actuators get their **own 12 V battery**, not the Pi's USB ports or the TurtleBot's user power pins (see §4). | 2026-11-19 |

## 3. Height budget

```
conveyor exit (H_exit, [TBD measure])
   \  chute, slope ≥ 30–35° so light PLA parts slide
    \
     outlet (h_chute) ── fixed, set by the chute design
        ↕ 20–40 mm clearance (never touch the chute)
     tray top, raised  = h_tray_down + lift
     tray top, down    = h_tray_down  ≈ top plate (~0.35 m) + deck + tray  [TBD]
```

- Pick the lift stroke first (target **50–100 mm**), then put the chute outlet
  at about `h_tray_down + stroke/2 + clearance`, so the lift can correct
  ± half a stroke for parking and floor differences.
- **A long lift is not an option.** Raising the tray high moves the centre of
  gravity up and makes the robot easy to tip when braking or turning.
- Chute length follows from the drop: for example, a 0.40 m drop at 35° needs
  about 0.57 m of horizontal run. Check this against the space outside the
  guarding at the transfer pose (`LAB_LAYOUT.md`).

## 4. Power

From the TurtleBot 4 user manual (*Electrical → Power budget, UI PCBA,
Create 3*):

| Source | Rating | Use for actuators? |
|---|---|---|
| Create 3 output (whole robot) | **28.8 W** total; TurtleBot 4 uses ~15 W, **~13.8 W left** | Not with margin |
| UI board user pin **12 V** | **300 mA** (fuse holds at 350 mA) | ❌ start-up and stall current exceed it |
| UI board user pin **VBAT** (14.4 V) | **300 mA** (fuse holds at 350 mA) | ❌ same |
| UI board **USB-C** ports / Pi USB | 5 V, 3 A **shared**, same supply as the Pi | ❌ wrong voltage; a motor surge can brown out and **reboot the Pi** |

**Design:** a separate **12 V pack** (e.g. 3S Li-ion, 2–3 Ah, protected
cells/BMS) → **inline fuse** (size to the actuators, about 3 A **[TBD]**) →
**main switch** → motor driver(s). **Only the ground** is shared with the Pi for
the control signals. The robot's own electronics stay on Create 3 power.

## 5. Mechanism and parts (candidates, not ordered)

| Function | Candidate | Notes |
|---|---|---|
| Lift | Mini linear actuator **with position feedback**, 12 V, 50–100 mm stroke, e.g. **Actuonix L16-R** (built-in controller, RC-servo signal) or **P16-P** (potentiometer feedback) | Parts are light, so the faster, lower-force gearing (about 50–100 N) is enough. **Check the datasheet** for force, speed and current. |
| Lift guidance | 2 guide rods + linear bushings, or a small rail | The actuator must only push up and down; side loads ruin mini actuators. |
| Tilt | Second mini linear actuator (holds position with power off — **preferred**) **or** a metal-gear servo, about 25–35 kg·cm | Mount the actuator close to the hinge to get a large angle from a short stroke. |
| Tilt angle | **40–45°** | PLA-on-PLA friction is roughly 0.3–0.5, so parts need more than ~27° to slide. A smooth tray surface (HDPE sheet or PTFE tape) helps. |
| Tray | About 220 × 200 mm, inside the robot's Ø 0.34 m outline, 30–40 mm lips; thin foam or rubber pad | Dump side: low lip or a passive flap. **Prototype and test with real parts.** |
| Control | PCA9685 PWM board (I²C) for "-R" actuators and servos, or one small H-bridge per standard actuator, on the UI board's free 2 × 12 GPIO header | The Pi's own software PWM jitters. |
| Sensors | Limit switch **LIFT_DOWN**, limit switch **TILT_HOME** | **Required:** the robot must not drive unless both are made. |
| Do not reuse | VEVOR OK648 from the old AGV | 1000 N, 8 in stroke, 15 mm/s: far too big and heavy for a TurtleBot. |

## 6. Weight budget

| Item | Estimate |
|---|---|
| Deck plate + standoffs | 0.4–0.6 kg |
| Lift actuator + guides + carriage | 0.3–0.5 kg |
| Tilt actuator + hinge | 0.1–0.2 kg |
| Tray + lips + pad | 0.3–0.5 kg |
| Battery, fuse, switch, driver, wiring | 0.3–0.4 kg |
| **Module total** | **≈ 1.5–2.2 kg** |
| 3D-printed parts per trip | small (weigh the heaviest load **[TBD]**) |

The manual's **9 kg** payload limit applies to a low, centred load. With a
raised tray, keep the module and parts to **about 3–4 kg**.

## 7. Software (`apex_payload`, runs on the TurtleBot's Pi)

- Action `SetLift(height_mm)`, action `TiltDump()`, service `Home()`.
- Publishes `PayloadStatus`: lift height, `lift_down`, `tilt_home`, moving,
  fault code.
- **Motion interlock:** the orchestrator sends no Nav2 goal unless
  `lift_down && tilt_home` is true. If either switch opens while driving, the
  orchestrator cancels navigation and goes to `FAULT: PAYLOAD_NOT_STOWED`.
- **Watchdog:** if the node gets no heartbeat from the orchestrator for 1 s,
  or the job goes to `FAULT`, it stops both actuators.
- Every move has a timeout (`T_lift`, `T_tilt`). A missed limit switch is a
  fault, never a retry loop.
- Lift and tilt only move while the robot is **parked**.

## 8. Rules

1. Lift and tilt **only while parked**; drive only with the tray **down and level**.
2. Nothing may block the **LiDAR** or the **OAK-D** view.
3. The tray must stay **inside the robot's outline** in every position. If the
   tilted tray sticks out, keep that clearance free at `return_drop`.
4. **⚠ RA:** cutting the FlexBase glass for the chute changes the guarding.
   See `RISK_ASSESSMENT_POINTS.md` RA-01. **No cutting** before the risk
   assessment and the lab's / Automation NTH's approval.
5. **⚠ RA:** pinch points at the lift, the tilt hinge and between the raised
   tray and the chute; tipping; Li-ion battery. See RA-16 to RA-19.

## 9. Still to decide or measure

| ID | Item |
|---|---|
| Q-B1 | Conveyor exit height `H_exit` above the floor (same as Q-L2). |
| Q-B2 | Largest and heaviest 3D-printed part; most parts per trip. |
| Q-B3 | Quarantine bin at `return_drop`: size, height and which side of the robot it is on (the tilt direction follows from it). |
| Q-B4 | How the dumped count is checked: book the damaged quantity from the receipt (default), or add a break-beam on the quarantine bin inlet to count parts. |
| Q-B5 | Final actuator, servo, driver and battery models, after a quick prototype. |
