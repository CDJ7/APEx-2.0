# APEx 2.0 — Risk-assessment register (points to assess)

**This is not a risk assessment.** It lists every place found so far where
the team, **as system integrator**, must carry out a real risk assessment
before the system runs. The KUKA KR C4 compact specification puts this
responsibility on the system integrator, for example in §5.1.1 (liability),
§5.3.1 (stopping distances), §5.5.4 (operator-safety acknowledgement),
§5.5.6 (Ethernet safety interface and switch-off), §5.5.7 (external
E-STOPs), §5.6.7 (external safeguards) and §5.8.9 (single point of control).

Applicable references to use during the assessment (design-review
references, **no compliance claimed**): ISO 10218-1/-2 (industrial robots),
ISO 13849-1 (safety-related control systems, PL), ISO 13850 (E-stop),
ISO 14120 (guards), ISO 3691-4 (driverless trucks) and ANSI/A3 R15.08
(industrial mobile robots).

| ID | Hazard area | Why it needs assessment | Where it is referenced |
|---|---|---|---|
| RA-01 | **Arm danger zone vs conveyor exit opening** | The guarding must contain workspace + stopping distance (KUKA §5.3). The conveyor leaves the cell through an opening; it must not let a hand or arm reach the danger zone. **The plan is to cut the FlexBase glass (Lexan) for the chute: this modifies the guarding and must not be done before this assessment and approval by the lab and Automation NTH** (ISO 13857 reach distances, ISO 14120). | ARCHITECTURE §3.4, BIN_FIXTURE §8, OPEN_QUESTIONS Q-K4 |
| RA-02 | **Stopping distances** of the KR 6 with gripper and part | Measure with safety stop 0 and 1 at operating temperature (KUKA §5.3.1). Input to RA-01. | KUKA_INTEGRATION (later stage) |
| RA-03 | **Operator-safety acknowledge** | Must be a manual button **outside** the guarding, with no auto-acknowledge on gate close (KUKA §5.5.4, §5.6.7). | ARCHITECTURE §3.4 |
| RA-04 | **Ethernet safety and controller switch-off** | With CIP Safety, switching the KR C4 off does not E-STOP the overall system; the smartPAD E-STOP stops working when the controller is off (KUKA §5.5.6). | ARCHITECTURE §3.4 |
| RA-05 | **External E-STOP coverage** | Every operator station able to start motion needs an E-STOP; at least one external E-STOP (KUKA §5.5.7). Includes the transfer station and the delivery stations? | PLC_INTEGRATION (later stage) |
| RA-06 | **Single point of control** | PLC and remote tools must not write safety-relevant signals or `$OV_PRO` in T1/T2; WorkVisual must not modify programs in test modes (KUKA §5.8.9). Our bridge has a write allow-list. | ARCHITECTURE §3.3, §3.4 |
| RA-07 | **Transfer station: pusher** | Pneumatic pusher outside the guarding: pinch / impact hazard to the operator's hands. | ARCHITECTURE §3.6, TRANSFER_STATION (later stage) |
| RA-08 | **Transfer station: chute and falling parts** | Parts falling from conveyor height; pinch points between chute, bin and robot. | ARCHITECTURE §3.6, JOB_STATE_MACHINE §6 |
| RA-09 | **TurtleBot near people** | Mobile robot in corridors shared with people (0.31 m/s, Create 3 bump/cliff). ISO 3691-4 / R15.08 considerations. | ARCHITECTURE §3.5 |
| RA-10 | **Reaching into the tray** | A worker at a delivery station reaches into the tray; the robot must be stopped and held (no motion and no lift/tilt command) during `AWAIT_RECEIPT`. | JOB_STATE_MACHINE §6 |
| RA-11 | **Keepout zone is not a safety function** | Nav2 keepout zones only guide planning; the safety argument must not rely on them. | ARCHITECTURE §3.5 |
| RA-12 | **Network separation** | Not a safety function, but exposing the PLC or KR C4 to the school network could allow unintended writes. | ARCHITECTURE §7.3 |
| RA-13 | **Fault recovery and restart** | Reset must never restart motion by itself; Resume needs an operator; after a safety stop, a physical acknowledge outside the guarding. | JOB_STATE_MACHINE §3 |
| RA-14 | **Future PC motion control (EKI / RSI)** | Would make a PC a motion source and change the control architecture; a new assessment is needed before use. | ARCHITECTURE §3.4 |
| RA-15 | **Mode selector access** | If the smartPAD mode switch has no key, an additional device must restrict who can change modes (KUKA §5.5.3). | OPEN_QUESTIONS Q-K5 |
| RA-16 | **Robot lift and tilt tray: pinch points** | Pinch between the raised tray and the chute outlet, at the lift guides, and at the tilt hinge. Low force, but fingers are near during loading and receipt. | BIN_FIXTURE §8, JOB_STATE_MACHINE §6 |
| RA-17 | **Robot stability** | A raised or tilted tray raises the centre of gravity; driving that way risks tipping. Interlock: no Nav2 goal unless `lift_down && tilt_home`; keep the module + parts to about 3–4 kg. | BIN_FIXTURE §3, §6, §7 |
| RA-18 | **Li-ion battery on the robot** | Separate 12 V pack for the actuators: protected cells/BMS, inline fuse, main switch, charging procedure, storage. | BIN_FIXTURE §4 |
| RA-19 | **Automatic dump at `return_drop`** | Parts tipped into the quarantine bin; the tilted tray may stick out past the robot's outline; nobody may reach in while it tilts. | BIN_FIXTURE §1, §8 |
