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
| RA-01 | **Arm danger zone vs the chute's opening in the guarding** | The guarding must contain workspace + stopping distance (KUKA §5.3). The chute passes through the east guard panel: the opening must not let a hand reach the danger zone (ISO 13857 opening size vs distance; hood / tunnel). | ARCHITECTURE §3.6, TRANSFER_AND_RETURN §1.2 |
| RA-02 | **Stopping distances** of the KR 6 with gripper and part | Measure with safety stop 0 and 1 at operating temperature (KUKA §5.3.1). Input to RA-01. | KUKA_INTEGRATION (later stage) |
| RA-03 | **Operator-safety acknowledge** | Must be a manual button **outside** the guarding, with no auto-acknowledge on gate close (KUKA §5.5.4, §5.6.7). | ARCHITECTURE §3.4 |
| RA-04 | **Ethernet safety and controller switch-off** | With CIP Safety, switching the KR C4 off does not E-STOP the overall system; the smartPAD E-STOP stops working when the controller is off (KUKA §5.5.6). | ARCHITECTURE §3.4 |
| RA-05 | **External E-STOP coverage** | Every operator station able to start motion needs an E-STOP; at least one external E-STOP (KUKA §5.5.7). Includes the transfer station and the delivery stations? | PLC_INTEGRATION (later stage) |
| RA-06 | **Single point of control** | PLC and remote tools must not write safety-relevant signals or `$OV_PRO` in T1/T2; WorkVisual must not modify programs in test modes (KUKA §5.8.9). Our bridge has a write allow-list. | ARCHITECTURE §3.3, §3.4 |
| RA-07 | ~~Transfer station: pusher~~ | No pusher in transfer mode D (2026-11-19). Re-open if mode A is ever used. | - |
| RA-08 | **Chute and funnel: falling parts** | Parts sliding down the chute and dropping through the funnel onto the tray; pinch points between funnel, chute support and robot; a hand under the funnel. | TRANSFER_AND_RETURN §1, §3 |
| RA-09 | **TurtleBot near people** | Mobile robot in corridors shared with people (0.31 m/s, Create 3 bump/cliff). ISO 3691-4 / R15.08 considerations. | ARCHITECTURE §3.5 |
| RA-10 | **Reaching into bins** | Worker at a delivery station and operator at the return pose reach into bins; the robot must be stopped and held (no motion command) during those steps. | JOB_STATE_MACHINE §6 |
| RA-11 | **Keepout zone is not a safety function** | Nav2 keepout zones only guide planning; the safety argument must not rely on them. | ARCHITECTURE §3.5 |
| RA-12 | **Network separation** | Not a safety function, but exposing the PLC or KR C4 to the school network could allow unintended writes. | ARCHITECTURE §7.3 |
| RA-13 | **Fault recovery and restart** | Reset must never restart motion by itself; Resume needs an operator; after a safety stop, a physical acknowledge outside the guarding. | JOB_STATE_MACHINE §3 |
| RA-14 | **Future PC motion control (EKI / RSI)** | Would make a PC a motion source and change the control architecture; a new assessment is needed before use. | ARCHITECTURE §3.4 |
| RA-15 | **Mode selector access** | If the smartPAD mode switch has no key, an additional device must restrict who can change modes (KUKA §5.5.3). | OPEN_QUESTIONS Q-K5 |
| RA-16 | **Learned (RL) navigation policy** | A learned policy can behave unexpectedly outside its training conditions. It must run behind Nav2's collision monitor and the Create 3 hazard safety, with a hard speed limit, and be trialled on an empty loop with a person ready to stop the robot. | ROADMAP "Learning-Based Navigation" |
| RA-17 | **Two HMIs on one cell (Ignition + station GUI)** | Two screens that can request cell actions could conflict. Each request bit has exactly one owner, safety resets stay on the hardwired acknowledge button, and the station GUI shows only a read-only cell summary. | ARCHITECTURE §3.1a |
| RA-18 | **Gravity chute drop** | Free fall of parts into the bin: drop height, part damage, parts bouncing out of the bin, and a hand in the bin while the stop station could release. The PLC must only release with the robot confirmed in position and no operator request active. | ARCHITECTURE §3.6, `lab.yaml` chute |
| RA-19 | **Software STOP mistaken for an emergency stop** | The station's STOP button (and F12) faults the job and halts the robot through software. It is not a safety function and is labelled so; the hardware E-stops remain the emergency means. Training and signage needed. | station/README §4 |
| RA-20 | **Chute opening in the guarding** | Same as RA-01, listed separately because it is new with transfer mode D: the KUKA must release parts only inside the guarding, and nothing may reach in through the chute. | TRANSFER_AND_RETURN §1.2 |
| RA-21 | **Tilting tray on the robot** | Pinch points at the hinge and the bin rim; the robot driving with the tray tilted; the tray tilting while someone's hand is on it at a station. Interlocks both ways in software (tray only tilts with the robot still, robot only drives with the tray flat), plus the microcontroller's own limit-switch stop and watchdog. | TRANSFER_AND_RETURN §2, apex_tilt README |
| RA-22 | **Tray motor power on the robot** | A motor on the TurtleBot's battery: fuse, wiring, stall current above the 300-500 mA user rails, battery protection. | TRANSFER_AND_RETURN §2.3 |

