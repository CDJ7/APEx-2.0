# APEx 2.0 — TurtleBot payload: two-section tray

**Status:** design draft (updated 2026-11-19). Nothing is bought or built yet.
Numbers marked **[TBD]** must be measured or confirmed before parts are ordered.
All fixture and plate parts are **3D-printed** (§8).

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
                                          │    counts it ──► GOOD section of the tray
```

The tray on top of the TurtleBot is **one plate with two sections**:

```
          top view                         side view (bad section tilting)
 ┌──────────────────┬─────────┐
 │                  │         │              good section    bad section
 │   GOOD section   │   BAD   │            ┌──────────────┐  ╱
 │  (chute fills    │ section │            │              │ ╱  ← servo lifts the inner
 │   this one,      │ (tilts) │            │              │╱      edge; it tips outward
 │   never tilts)   │         │            └──────────────┘● hinge on the OUTER edge
 └──────────────────┴─────────┘                                ↓ parts slide into the quarantine bin
        divider wall between them
```

1. **Transfer pose:** the robot parks with the **good section** under the
   chute outlet. The KUKA places the parts one by one on the chute; each one
   slides into the good section and is counted by the photoeye.
2. **Delivery station:** the worker takes the **OK** parts, **puts any damaged
   parts in the bad section**, and confirms the receipt in the GUI.
3. **Return pose (`return_drop`):** a **servo tilts only the bad section**; the
   damaged parts slide into the **quarantine bin**. The section tilts back
   onto its rest, the level switch is checked, and the robot takes the next
   job or docks. **The good section never tilts.**

The robot never enters the FlexBase guarding.

## 2. Decisions so far

| # | Decision | Date |
|---|---|---|
| B-1 | **No conveyor in the transfer.** The KUKA picks each part from a **shelf inside the FlexBase** and places it on the **chute**, which passes through an opening cut in the FlexBase glass and ends over the tray. | 2026-11-19 |
| B-2 | Parts are **light 3D-printed parts**, about **3 kg per trip** (rough estimate). | 2026-11-19 |
| B-3 | The robot **dumps damaged parts by itself** at `return_drop`. | 2026-11-19 |
| B-4 | **Two-section tray:** a fixed **good** section and a servo-tilted **bad** section, on one plate. Only the bad section tilts. | 2026-11-19 |
| B-5 | **Lift: optional.** Build with the chute outlet at the fixed tray height first; add a linear-actuator lift under the plate only if testing shows the heights need adjusting (§4). | 2026-11-19 |
| B-6 | **Arduino** controls the servo, the actuator and the switches; the Pi sends it commands over USB serial. | 2026-11-19 |
| B-7 | Motor power from a **TurtleBot UI-board USB-C port (5 V)**, within the limits in §5. A separate battery is the fallback if testing shows problems. | 2026-11-19 |
| B-8 | Fixture and plates are **3D-printed** (§8). | 2026-11-19 |

## 3. Tray sizing

- **Split about 2/3 good, 1/3 bad.** The good section is the chute's target,
  so it must be large enough to absorb parking error (about ±2 cm).
- Whole tray about 220 × 200 mm, **inside the robot's Ø 0.34 m outline**, not
  blocking the LiDAR or the OAK-D.
- **Check the volume:** 3 kg of printed parts can take up several litres, and
  a 2/3 section of that tray with 40 mm walls holds only about **1.2 L**
  **[TBD: weigh and measure one real load]**. If it doesn't fit, make the
  walls deeper (keep it low and centred) or split big orders over two trips.
- **Bad section:** hinge on its **outer edge**, divider wall on the inner side.
  When level it **rests on a hard stop**, so gravity keeps it shut and the servo
  only works while lifting. Tilt **40–45°** (PLA-on-PLA friction is roughly
  0.3–0.5, so parts need more than ~27° to slide); line the floor with HDPE
  sheet or PTFE tape.
- Put the bad section on the side that faces the quarantine bin at
  `return_drop`. The bin rim must be lower than the tray edge and close to
  the robot.

## 4. Height and the optional lift

The arm can place a part at any height in its reach (KR 6 R900-2: 901 mm), and
the team designs the chute, so the chute outlet can be built to the tray
height:

```
 chute inlet (where the arm places parts, inside the guarding)
    \   slope ≥ 30–35° so light PLA parts slide
     \  passes through the opening in the glass
      outlet (h_chute) = h_tray + 20–40 mm clearance
      tray top (h_tray) ≈ top plate (~0.35 m) + deck + tray   [TBD from CAD]
```

**If a lift is added later**, it lifts **everything**: plate, servo, both
sections and up to 3 kg of parts (≈ 4 kg). Then:

- actuator force **≥ 150 N** (40 N load × margin), stroke 50–100 mm;
- **two steel guide rods** with linear bearings carry the plate; the
  actuator only pushes up and down;
- the lift moves only while parked, never at the same time as the servo, and
  a **LIFT_DOWN** switch must be made before the robot drives.

The software keeps the lift steps behind `payload.lift_fitted` (default
`false`).

## 5. Power from a TurtleBot USB-C port

From the TurtleBot 4 user manual (*Electrical → UI PCBA, Power budget*):

- The UI board has **4 USB-C ports, 5 V only**, sharing **3 A in total**. On
  a **REV 2** board only **port 4** gives 3 A; the others give about 2.6 A.
  The revision and port numbers are printed under the board.
- All of it comes out of the robot's **28.8 W** budget, of which the
  TurtleBot 4 itself uses about **15 W**, leaving **~13.8 W**. That pool is
  shared with the Pi's own supply, so an overload can affect the whole robot.
- The OAK-D camera may also be plugged into this hub.

**What fits:** 5 V × ~2.5 A ≈ 12.5 W, just inside the spare power, **only if**:

1. **Everything runs on 5–6 V parts.** No 12 V actuator.
2. **The servo and the actuator never move at the same time.** The Arduino
   enforces this.
3. Motors take power from **one dedicated port** (port 4 on a REV 2 board)
   through a **USB-C power breakout**, a **3 A fuse** (polyfuse) and a switch.
4. A **1000–2200 µF capacitor** sits across the servo supply to absorb its
   current spikes.
5. **Measure** the current during a tilt with a USB-C power meter. If the
   camera or LiDAR drops out, or the robot logs brown-outs, switch to the
   fallback.

**The Arduino never powers a motor from its pins.** Its pins give only about
20–40 mA. It sends signals only; motor current flows straight from the 5 V
rail.

**Fallback:** a small separate pack (e.g. 2S Li-ion with a 6 V buck converter,
or a 5 V USB power bank rated ≥ 3 A) with a fuse and a switch, sharing only
ground with the Arduino.

## 6. Electronics (candidates, not ordered)

| Function | Candidate | Notes |
|---|---|---|
| Controller | **Arduino Nano** (small) or the **Uno R4** from the old AGV | Plugged into a Pi USB port for **serial commands and its own power**. |
| Bad-section tilt | **Metal-gear servo, 20–25 kg·cm, rated 4.8–6.8 V** (DS3225 class) | ≈ 1 kg of damaged parts + the section at ~6 cm from the hinge ≈ 7 kg·cm; 2–3× margin. Use a **metal servo horn**. Stall current can be 2–3 A: check the datasheet. |
| Lift (optional) | **Actuonix L16-R, 6 V version**, high-force gearing (about 150–175 N), 50–100 mm stroke | The "-R" version has its own controller and takes the **same signal as a servo**, so the Arduino `Servo` library drives it. Check force, current and whether it runs on 5 V. **No 12 V actuators** on USB power. |
| Switches | **TILT_HOME** (and **LIFT_DOWN** if a lift is fitted), microswitches to Arduino inputs with pull-ups | **Required:** the robot must not drive unless they are made. |
| Power | USB-C power breakout (5 V), 3 A polyfuse, toggle switch, 1000–2200 µF capacitor | All grounds tied together: Arduino, servo, actuator. |
| Do not reuse | VEVOR OK648 from the old AGV | 12 V, 1000 N, 8 in stroke: far too big for a TurtleBot. |

```
UI USB-C port 4 ─► USB-C breakout ─► 3 A fuse ─► switch ─► 5 V rail ─┬─► servo V+  (+ capacitor)
                                                                    └─► L16-R V+  (optional lift)
Pi USB ──► Arduino (serial commands + its power)
Arduino D9 ──► servo signal      Arduino D10 ──► L16-R signal (optional)
Arduino D2 ◄── TILT_HOME switch  Arduino D3  ◄── LIFT_DOWN switch (optional)
GND: rail, Arduino, servo, actuator all common
```

## 7. Weight budget

| Item | Without lift | With lift |
|---|---|---|
| Printed deck + standoffs | 0.3–0.5 kg | 0.3–0.5 kg |
| Printed two-section tray + hinge + pad | 0.3–0.5 kg | 0.3–0.5 kg |
| Servo + horn | ~0.07 kg | ~0.07 kg |
| Lift actuator + rods + bearings + carriage | — | 0.3–0.5 kg |
| Arduino, breakout, fuse, wiring | ~0.1 kg | ~0.1 kg |
| **Module** | **≈ 0.8–1.2 kg** | **≈ 1.1–1.7 kg** |
| **Parts** | **≈ 3 kg** | **≈ 3 kg** |
| **Total on the robot** | **≈ 3.8–4.2 kg** | **≈ 4.1–4.7 kg** |

Within the manual's **9 kg** maximum payload, which assumes a low, centred
load. Keep the parts low and over the middle, and **weigh the finished
module**.

## 8. 3D-printing guidelines

- **Material:** **PETG** (or ASA) for the deck, tray, hinge and mounts. PLA
  creeps under constant load and softens in a warm robot.
- **Strength where it matters:** 4–6 perimeters and 40 %+ infill on the hinge
  lugs, servo mount and the parts that bolt to the TurtleBot.
- **Print orientation:** hinge lugs printed so the layer lines are **not**
  across the load. Layers split apart much more easily than they bend.
- **Metal for pins and threads:** a **steel bolt (M3/M4) as the hinge pin**,
  not a printed pin; **heat-set brass inserts** for every screw that is
  undone more than once.
- **Lift (if fitted):** printed carriage, but **steel guide rods + LM8UU
  bearings**; printed rails wear and jam.
- **Mounting:** bolt the deck to the TurtleBot top plate's existing **M4
  holes** (check them against `025394` in the TurtleBot CAD).
- **Tray floor:** padded where parts land from the chute; HDPE or PTFE tape
  on the bad section so parts slide off.
- **Design in CAD around** `025620-OS-GEO_4.STEP` (full TurtleBot 4 assembly)
  to check the LiDAR and camera views and the robot's outline.

## 9. Software

- **Arduino firmware:** a simple serial protocol from the Pi:
  `TILT`, `HOME`, `LIFT <mm>` (if fitted), `STATUS`. It **refuses to move the
  servo and the actuator at the same time**, stops everything if it gets no
  command or heartbeat for 1 s, and limits each move with a timeout.
- **`apex_payload`** (ROS 2 node on the Pi) talks to the Arduino and offers
  the action `TiltDump()`, the service `Home()` and, if fitted,
  `SetLift(height_mm)`. It publishes `PayloadStatus`: `tilt_home`,
  `lift_down` (always true without a lift), moving, fault code.
- **Motion interlock:** the orchestrator sends no Nav2 goal unless the tray
  is stowed. If a switch opens while driving, it cancels navigation and goes
  to `FAULT: PAYLOAD_NOT_STOWED`.

## 10. Rules

1. Tilt (and lift) **only while parked**; drive only with the tray **stowed**.
2. Nothing may block the **LiDAR** or the **OAK-D** view.
3. The tray stays **inside the robot's outline** while driving. Keep room for
   the tilted bad section at `return_drop`.
4. **⚠ RA-01:** the chute opening in the FlexBase glass sits **right next to
   where the arm places parts**. The opening must stop a hand or arm reaching
   the arm (for example a long, narrow chute tunnel sized to ISO 13857 reach
   distances). **No cutting** before the risk assessment and approval by the
   lab and Automation NTH.
5. **⚠ RA:** pinch at the hinge (and lift); tipping; parts dropping from the
   chute; overloading the TurtleBot's power. See RA-08 and RA-16 to RA-19.

## 11. Still to decide or measure

| ID | Item |
|---|---|
| Q-B1 | Where the chute passes through the guarding, and the chute inlet height where the arm places parts. |
| Q-B2 | Largest part, parts per trip, and the **volume** of a 3 kg load (§3). |
| Q-B3 | Quarantine bin at `return_drop`: size, height and which side of the robot (sets which side the bad section is on). |
| Q-B4 | How the dumped count is checked: book the damaged quantity from the receipt (default), or add a break-beam on the quarantine bin inlet. |
| Q-B5 | Lift needed? Decide after testing the fixed-height chute (B-5). |
| Q-B6 | Final servo, actuator and Arduino models; UI board revision (REV 2 → use port 4). |
| Q-B7 | Shelf layout inside the FlexBase: part types and positions (taught pick positions). |
