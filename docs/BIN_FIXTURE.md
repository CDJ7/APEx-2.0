# APEx 2.0 — TurtleBot payload: tray, tilt (and optional lift)

**Status:** design draft (updated 2026-11-19). Nothing is bought or built yet.
Numbers marked **[TBD]** must be measured or confirmed before parts are ordered.

## 1. What it does

Loading needs no conveyor. Each part goes straight from the arm, down a chute,
into the robot's tray:

```
 inside the guarding                      │ outside the guarding
                                          │
 parts shelf ──► KUKA KR 6 picks a part   │
                 and places it at the     │
                 chute inlet ─────────────┼──► chute (through an opening in the
                                          │    FlexBase glass) ──► photoeye
                                          │    counts it ──► TurtleBot tray
```

The TurtleBot carries a module on its top plate:

- **Level 1 (fixed deck):** a 12 V battery pack, fuse, switch, motor driver.
- **Level 2 (tray):** a padded tray, hinged on one edge so a **tilt
  actuator** can tip it. An **optional lift** under the tray is described in
  §3.

One tray carries everything on the trip:

1. **Transfer pose:** the robot parks with its tray under the chute outlet.
   The KUKA places the parts one by one on the chute; each one slides into the
   tray and is counted by the photoeye.
2. **Delivery station:** the worker takes the **OK** parts and leaves any
   **damaged** parts on the tray.
3. **Return pose (`return_drop`):** the robot **tilts the tray by itself** and
   the damaged parts slide into a **quarantine bin**. The tray tilts back
   level and the robot takes the next job or docks.

The robot never enters the FlexBase guarding.

## 2. Decisions so far

| # | Decision | Date |
|---|---|---|
| B-1 | **No conveyor in the transfer.** The KUKA picks each part from a **shelf inside the FlexBase** and places it on the **chute**, which passes through an opening cut in the FlexBase glass and ends over the TurtleBot's tray. | 2026-11-19 |
| B-2 | Parts are **light 3D-printed parts**, about **3 kg per trip** (rough estimate). | 2026-11-19 |
| B-3 | The robot **dumps damaged parts by itself** by tilting the tray at `return_drop`. This replaces the earlier plan of an operator emptying a separate return bin. | 2026-11-19 |
| B-4 | The actuators get their **own 12 V battery**, not the Pi's USB ports or the TurtleBot's user power pins (see §5). | 2026-11-19 |
| B-5 | **Proposed, [CONFIRM]:** a **fixed-height tray, no lift**. The chute outlet is designed to the tray height (§3). | — |

## 3. Height: why a lift is probably not needed

The arm can place a part at any height within its reach (KR 6 R900-2: 901 mm),
and the team designs the chute. So the chute outlet can simply be built at
the tray's height:

```
 chute inlet (where the arm places parts, inside the guarding)
    \   slope ≥ 30–35° so light PLA parts slide
     \  passes through the opening in the glass
      outlet (h_chute) = h_tray + 20–40 mm clearance
      tray top (h_tray) ≈ top plate (~0.35 m) + deck + tray   [TBD from CAD]
```

- **Without a lift** the module is lighter (about −0.4 kg), has fewer parts
  to fail, and keeps the centre of gravity low. With 3 kg of parts on board,
  that matters.
- **Keep a lift only if** something fixes the chute outlet height that the
  team cannot change, for example where the glass can be cut. In that case
  it must be sized for the full load (§6) and run on guide rods.
- The software keeps the lift steps behind a setting
  (`payload.lift_fitted`, default `false`), so the decision can wait.

## 4. Tray size: check the volume

3 kg of 3D-printed parts can take up a lot of space. Infill makes printed
parts much lighter than solid plastic, and loose parts pack with gaps.

- A tray of about 220 × 200 mm with 40 mm walls holds about **1.8 L**.
- 3 kg of printed parts may need **several litres** **[TBD: weigh and measure
  one real load]**.
- If it doesn't fit: make the tray walls deeper (keep the weight low and
  centred), or split large orders over two trips. The orchestrator can do the
  splitting.
- The tray must stay **inside the robot's Ø 0.34 m outline**, and must not
  block the LiDAR or the OAK-D camera.

## 5. Power

From the TurtleBot 4 user manual (*Electrical → Power budget, UI PCBA,
Create 3*):

| Source | Rating | Use for actuators? |
|---|---|---|
| Create 3 output (whole robot) | **28.8 W** total; TurtleBot 4 uses ~15 W, **~13.8 W left** | Not with margin |
| UI board user pin **12 V** | **300 mA** (fuse holds at 350 mA) | ❌ start-up and stall current exceed it |
| UI board user pin **VBAT** (14.4 V) | **300 mA** (fuse holds at 350 mA) | ❌ same |
| UI board **USB-C** ports / Pi USB | 5 V, 3 A **shared**, same supply as the Pi | ❌ wrong voltage; a motor surge can brown out and **reboot the Pi** |

**Design:** a separate **12 V pack** (e.g. 3S Li-ion, 2–3 Ah, protected
cells/BMS) → **inline fuse** (size to the actuator, about 3 A **[TBD]**) →
**main switch** → motor driver. **Only the ground** is shared with the Pi for
the control signals. The robot's own electronics stay on Create 3 power.

## 6. Mechanism and parts (candidates, not ordered)

**Design load:** tray + 3 kg of parts ≈ **3.5–4 kg**, if every part comes
back damaged. Normally only a few parts are dumped, but the tilt must cope
with the worst case.

| Function | Candidate | Notes |
|---|---|---|
| Tilt | Mini **linear actuator** with position feedback, 12 V, about **150–200 N**, e.g. **Actuonix L16 / P16** class with the high-force gearing | ~40 N of load, offset from the hinge, plus a 2–3× margin. Holds position with the power off. A servo is **not** recommended at this load (about 35 kg·cm needed). Check force, speed and current in the datasheet. |
| Tilt angle | **40–45°** | PLA-on-PLA friction is roughly 0.3–0.5, so parts need more than ~27° to slide. A smooth tray floor (HDPE sheet or PTFE tape) helps. |
| Tilt direction | Toward the quarantine bin; **tip over the front or back** if possible | Sideways, 3 kg sliding to one edge moves the centre of gravity outward. Check stability with the CAD mass properties (`025620-OS-GEO_4.STEP` + module) before building. |
| Tray | About 220 × 200 mm, inside the Ø 0.34 m outline, padded floor where parts land | Dump side: low lip or a passive flap. **Prototype and test with real parts.** |
| Lift (optional, §3) | 12 V actuator with feedback, ≥ **150 N**, 50–100 mm stroke, on 2 guide rods | Only if B-5 is rejected. |
| Control | One small H-bridge per actuator, or a PCA9685 PWM board (I²C) for "-R" type actuators, on the UI board's free 2 × 12 GPIO header | The Pi's own software PWM jitters. |
| Sensors | Limit switch **TILT_HOME** (and **LIFT_DOWN** if a lift is fitted) | **Required:** the robot must not drive unless they are made. |
| Do not reuse | VEVOR OK648 from the old AGV | 1000 N, 8 in stroke: far too big and heavy for a TurtleBot. |

## 7. Weight budget

| Item | Without lift | With lift |
|---|---|---|
| Deck plate + standoffs | 0.4–0.6 kg | 0.4–0.6 kg |
| Tray + pad + hinge | 0.4–0.6 kg | 0.4–0.6 kg |
| Tilt actuator | 0.1–0.2 kg | 0.1–0.2 kg |
| Lift actuator + guides + carriage | — | 0.3–0.5 kg |
| Battery, fuse, switch, driver, wiring | 0.3–0.4 kg | 0.3–0.4 kg |
| **Module** | **1.2–1.8 kg** | **1.5–2.3 kg** |
| **Parts** | **≈ 3 kg** | **≈ 3 kg** |
| **Total on the robot** | **≈ 4.2–4.8 kg** | **≈ 4.5–5.3 kg** |

This is within the manual's **9 kg** maximum payload, which assumes a low,
centred load. Keep heavy items (battery, parts) low and over the middle of
the robot, and **weigh the finished module**.

## 8. Software (`apex_payload`, runs on the TurtleBot's Pi)

- Action `TiltDump()`, service `Home()`; action `SetLift(height_mm)` only if a
  lift is fitted.
- Publishes `PayloadStatus`: `tilt_home`, `lift_down` (always true without a
  lift), moving, fault code.
- **Motion interlock:** the orchestrator sends no Nav2 goal unless the tray
  is stowed (`tilt_home`, and `lift_down` if fitted). If a switch opens while
  driving, the orchestrator cancels navigation and goes to
  `FAULT: PAYLOAD_NOT_STOWED`.
- **Watchdog:** if the node gets no heartbeat from the orchestrator for 1 s,
  or the job goes to `FAULT`, it stops the actuators.
- Every move has a timeout (`T_tilt`, `T_lift`). A missed limit switch is a
  fault, never a retry loop.
- Tilt (and lift) only move while the robot is **parked**.

## 9. Rules

1. Tilt (and lift) **only while parked**; drive only with the tray **stowed**.
2. Nothing may block the **LiDAR** or the **OAK-D** view.
3. The tray must stay **inside the robot's outline** while driving. If the
   tilted tray sticks out, keep that clearance free at `return_drop`.
4. **⚠ RA-01:** the chute opening in the FlexBase glass sits **right next to
   where the arm places parts**, so the arm's danger zone reaches the opening.
   The opening must stop a hand or arm reaching the arm (for example a long,
   narrow chute tunnel sized to ISO 13857 reach distances). **No cutting**
   before the risk assessment and approval by the lab and Automation NTH.
5. **⚠ RA:** pinch at the tilt hinge (and lift); tipping; Li-ion battery;
   parts dropping from the chute. See RA-08 and RA-16 to RA-19.

## 10. Still to decide or measure

| ID | Item |
|---|---|
| Q-B1 | Where the chute passes through the guarding, and the **chute inlet height** where the arm places parts. |
| Q-B2 | Largest 3D-printed part, number of parts per trip, and the **volume of a 3 kg load** (§4). |
| Q-B3 | Quarantine bin at `return_drop`: size, height and position relative to the robot (sets the tilt direction). |
| Q-B4 | How the dumped count is checked: book the damaged quantity from the receipt (default), or add a break-beam on the quarantine bin inlet. |
| Q-B5 | Fixed-height tray (no lift, proposed) or keep the lift (B-5). |
| Q-B6 | Final actuator, driver and battery models, after a quick prototype. |
| Q-B7 | Shelf layout inside the FlexBase: how many part types, where each sits (these become taught pick positions). |
