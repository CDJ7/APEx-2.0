# APEx 2.0 — Open questions

Everything still needed from the team, grouped by topic. Each item says what
we assumed so work can continue, and **which stage is blocked** if the
assumption turns out wrong. Answer in this file (or in chat) and the item
moves to the *Answered* table at the bottom.

## Simulation Bring-up (Q-S)

| ID | Question | Current assumption |
|---|---|---|
| Q-S1 | **ROS 2 distro on our TurtleBot 4.** Run `echo $ROS_DISTRO` or `cat /etc/os-release` on the robot's Raspberry Pi. | **Jazzy** (Ubuntu 24.04). All TurtleBot repos on the `jazzy` branch. |
| Q-S2 | **Station PC.** Is it a dedicated Ubuntu 24.04 machine (or dual boot) that can run ROS 2 Jazzy + Gazebo? Studio 5000 and WorkVisual are Windows-only, so they would run on a separate Windows laptop. | Station PC = Ubuntu 24.04 + ROS 2 Jazzy; separate Windows laptop for Studio 5000 / WorkVisual. |
| Q-S3 | **Who runs the Gazebo + Nav2 demos?** The build environment can install Gazebo Harmonic but not ROS 2 (its package server is blocked), so the worlds were validated in Gazebo and the logic headless; the full ROS run needs the team's Ubuntu 24.04 + Jazzy machine. Which machine, and who? | Station PC, following `docs/SIMULATION.md`. |
| Q-S4 | **Parts to deliver:** how many part types, size and weight of each (heaviest?), how many parts per order, how they are stored in the cell (trays, fixtures, a parts rack?). Drives gripper choice, pick positions, conveyor pallet nests, bin size and the 9 kg payload budget. | Small parts ≤ 0.5 kg, ≤ 10 per order (placeholder). |
| Q-S5 | ~~Transfer method~~ → answered A-7: **B, gravity chute**. Still open: real chute geometry (conveyor exit height, slope, exit height above the bin, padding). | Sim: top 0.85 m, exit 0.55 m, 0.28 m run, 0.20 m wide. |
| Q-S6 | **Pallet or directly on the belt?** With the gravity chute the part has to leave the conveyor by itself at the end of the run, so it rides **directly on the belt** (or on a pallet that tips at the end). Does our Quickdraw run bare-belt? | **Proposal: bare belt**, stop station holds the part, releasing it lets it run off the end into the chute. **[CONFIRM]** |
| Q-S7 | **Parking accuracy under the chute.** Is AMCL + Nav2 with a 6 cm goal tolerance good enough on the real floor, or do we add a fiducial (AprilTag on the transfer station, OAK-D) and opennav_docking for the last 0.6 m? | Try AMCL first; measure 20 parkings; fiducial if any is off by > 4 cm. |
| Q-S8 | **Real cell timing** (pick, convey, release, drop per part) to replace the placeholders in `cell_sim.yaml`. | 6 s / 4 s / 0.5 s / 1 s. |

## Lab layout (Q-L)

| ID | Question | Current assumption |
|---|---|---|
| Q-L1 | Room dimensions, door, columns. Does a 6.1 m × 4.7 m area fit? | Proposed footprint in `LAB_LAYOUT.md`. |
| Q-L2 | FlexBase orientation and **which end the Quickdraw conveyor exits**, plus the conveyor height at the exit. | Conveyor exits the +x (east) end. Height unknown; it sets the chute slope. |
| Q-L3 | Operator side of the FlexBase; position of the start/stop/reset pushbutton box and the safety-gate **acknowledge button** (must be outside the guarding). | Operator at the east end, near the transfer station. |
| Q-L4 | Where can the charging dock go (wall outlet)? | Outer wall of the east corridor, at (3.10, −1.20). |
| Q-L5 | What do the delivery stations physically look like (table? shelf?), and where does the worker confirm receipt (station PC GUI, the workstation laptop, a tablet at each station)? | Workstation laptop via REST, or the main GUI. |

## FlexBase / PLC (Q-P)

| ID | Question | Current assumption |
|---|---|---|
| Q-P1 | Exact options installed on **our** FlexBase: Quickdraw conveyor (upper/lower), stop stations, lift-and-locate, light curtains vs Lexan doors, interlocked door switches, pushbutton box, E-stop station, SMC valve bank (how many free stations). | Conveyor + stop station + SMC valve bank present (brochure options). |
| Q-P2 | Compact GuardLogix **catalog number and firmware**, and our **Studio 5000 version**. | 5380 series; Studio 5000 v32+. |
| Q-P3 | Can we get the **existing FlexBase PLC program (.ACD)** and its tag naming conventions? Is there an Automation NTH standard program we must keep? | We add our own routines and a handshake UDT; we touch nothing in the safety task. |
| Q-P4 | IO-Link master and stack light addressing (which ports are free for the photoeye?). | Photoeye wired to a 1734 standard input or an IO-Link port. |
| Q-P5 | PLC IP address plan and whether the PLC has a second Ethernet port (for a separate controls network). | See network plan, `ARCHITECTURE.md` §7. |

## KUKA KR 6 R900-2 / KR C4 compact (Q-K)

| ID | Question | Current assumption |
|---|---|---|
| Q-K1 | **KSS version** and **installed options** (smartPAD: *Help → Info → Options*): KUKA.EtherNet/IP? CIP Safety? PROFIsafe? | KUKA.EtherNet/IP present. CIP Safety unknown. |
| Q-K2 | Is the safety interface today **X11 discrete** or **X66 Ethernet safety**? (Never both.) | Recommend CIP Safety if installed, else X11 to GuardLogix safety I/O. |
| Q-K3 | **Gripper:** model, actuation (pneumatic via the SMC valve bank? electric?), and whether it needs a part-present sensor. | Pneumatic gripper on the SMC valve bank; PLC controls it through the KRL handshake. |
| Q-K4 | Where is the arm mounted inside the FlexBase, and can its **danger zone (workspace + stopping distance)** reach the conveyor exit opening? Do we need software workspace limits (SafeOperation) or mechanical limits? | Danger zone stays inside the guarding; the conveyor opening is too small for a person to reach through. **⚠ RA** |
| Q-K5 | Is a smartPAD with a key switch available, and who on the team is trained to operate the KUKA (T1 teaching)? | — |

## TurtleBot 4 (Q-T)

| ID | Question | Current assumption |
|---|---|---|
| Q-T1 | Is the **OAK-D Pro** free to be used for an AprilTag at the transfer station (precise parking)? | Yes, for the fiducial final approach. |
| Q-T2 | Battery policy: dock below what % and refuse jobs below what %? | Dock < 30 %, refuse < 40 % (placeholders). |
| Q-T3 | One TurtleBot or more in the future? | One. The design keeps namespaces possible. |

## Network (Q-N)

| ID | Question | Current assumption |
|---|---|---|
| Q-N1 | School Wi-Fi: WPA2-Enterprise / 802.1X? Client isolation? Can a device be registered by MAC? Is 5 GHz available in the lab? | Assume enterprise and isolated, so ROS traffic goes over our router. |
| Q-N2 | The lab router is a **Netgear DGND3700v2** (A-13). It is a consumer router and is not expected to do **VLANs** or firewall rules between segments. Do we need a VLAN-capable router or managed switch before the PLC joins? | Separate controls network needed before the PLC is connected. |
| Q-N3 | Is the router ever allowed to uplink to the school network for internet? | Only the robot VLAN may, and the controls VLAN never routes to WAN. |

## Process (Q-X)

| ID | Question | Current assumption |
|---|---|---|
| Q-X1 | Who owns the **risk assessment** and who signs off before AUT EXT runs with the TurtleBot (instructor, lab manager)? | Team as system integrator, reviewed by the instructor. |
| Q-X2 | ~~Damaged-part retrieval~~ → deferred (A-9). | Operator-emptied return trip kept in software. |
| Q-X3 | "Not loaded" vs "lost in transfer": if the PLC placed a part on the conveyor but the photoeye did not count it, treat it as a discrepancy for an operator to check (not restocked)? | Yes (`JOB_STATE_MACHINE.md` §5). |
| Q-X4 | Old requirements are void. Is there **any** number from the old midterm (budget, speed, size) that should still apply? | None carried over. |

## Station HMI (Q-H)

| ID | Question | Working assumption |
|---|---|---|
| Q-H1 | Where does the worker confirm receipt at a delivery station: the station PC GUI, the workstation laptop (REST), or a tablet at each station? | Workstation laptop via REST; the main GUI as a fallback. |
| Q-H2 | Maximum parts per trip (bin size, 9 kg payload) - sets `station.max_units_per_order`. | 20 (placeholder). |
| Q-H3 | Real names of the delivery stations (shown to users) and their part numbers / PLC part codes for the parts stored in the FlexBase. | "Station A" / "Station B"; demo parts with codes 10-30. |
| Q-H4 | Is the station PC a touch screen? (Button sizes, on-screen keyboard.) | Mouse + keyboard. |
| Q-H5 | Should orders need an approval step (`station.auto_approve: false`), e.g. by the lab manager? | Auto-approve. |

## Ignition HMI (Q-I)

| ID | Question | Working assumption |
|---|---|---|
| Q-I1 | Ignition **version and licence** (Maker / trial / full), and which computer runs the Ignition gateway (station PC, a separate PC, the Windows laptop)? | Gateway on the Windows laptop on the lab network. |
| Q-I2 | Which request bits does **Ignition** own (e.g. cell start/stop, conveyor jog in MANUAL) and which does the **station** own (`APEX_FromStation`)? Each bit must have exactly one writer. | Station owns `APEX_FromStation`; Ignition owns a separate `APEX_FromHMI` UDT. |
| Q-I3 | Should Ignition also show TurtleBot status (via a small REST/MQTT feed from the station), or is the robot only on the station GUI? | Robot only on the station GUI. |

## Learning-Based Navigation (Q-R)

| ID | Question | Working assumption |
|---|---|---|
| Q-R1 | What should the RL policy control: **local driving** (cmd_vel between waypoints, replacing MPPI), **route choice** (which way round the loop), or both? | Local driving only; routing stays rule-based. |
| Q-R2 | Training hardware: is there a GPU machine (station PC or personal)? | Training on a team member's GPU laptop; CPU is possible but slow. |
| Q-R3 | Success criterion to use the policy on the real robot? | Beats Nav2 MPPI in sim on success rate with zero collisions over 200 runs. |

## Schedule (Q-C)

| ID | Question | Working assumption |
|---|---|---|
| Q-C1 | **Spring break dates** (no work then). | `[TBD]` |

## Answered

| ID | Answer | Date |
|---|---|---|
| A-1 | FlexBase is the **6 ft** model: 72" × 40". | 2026-11-12 |
| A-2 | KUKA gripper picks **individual parts**; no bin handling by the arm. | 2026-11-12 |
| A-3 | KR C4 **X66 (KLI)** on the **lab router** only; PLC and KR C4 never on the school network. | 2026-11-12 |
| A-4 | Handoff via **conveyor + transfer station**; the TurtleBot stays outside the guarding at all times. | 2026-11-12 |
| A-5 | The **C#** folder in Senior-Design is another project and is not part of APEx 2.0. | 2026-11-12 |
| A-6 | apex-2.0 is a **fresh start**; Senior-Design is read-only reference. | 2026-11-12 |
| A-7 | Transfer: **gravity chute** from the FlexBase onto the TurtleBot (`transfer_mode` B). | 2026-11-17 |
| A-8 | **Ignition** is the HMI for the FlexBase. | 2026-11-17 |
| A-9 | **Damaged-part handling is deferred** to a later stage. | 2026-11-17 |
| A-10 | **RL navigation policy is approved** ("RL policy is a go") - new stage *Learning-Based Navigation*. | 2026-11-17 |
| A-11 | The lab is modelled in the **Gazebo simulator** for the TurtleBot's benefit (map + training world). | 2026-11-17 |
| A-12 | **No work during spring break.** | 2026-11-17 |
| A-13 | Lab router is a **Netgear DGND3700v2** on `192.168.0.0/24`, no internet. TurtleBot on 5 GHz Wi-Fi at `192.168.0.3`, station laptop wired at `192.168.0.2` (both reserved). See [`TURTLEBOT_NETWORK.md`](TURTLEBOT_NETWORK.md). | 2026-11-18 |
