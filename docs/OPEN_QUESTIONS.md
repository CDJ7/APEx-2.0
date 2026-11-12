# APEx 2.0 — Open questions

Everything still needed from the team, grouped by topic. Each item says what
we assumed so work can continue, and **which stage is blocked** if the
assumption turns out wrong. Answer in this file (or in chat) and the item
moves to the *Answered* table at the bottom.

## Blocking the Simulation Bring-up stage

| ID | Question | Current assumption |
|---|---|---|
| Q-S1 | **ROS 2 distro on our TurtleBot 4.** Run `echo $ROS_DISTRO` or `cat /etc/os-release` on the robot's Raspberry Pi. | **Jazzy** (Ubuntu 24.04). All TurtleBot repos on the `jazzy` branch. |
| Q-S2 | **Station PC.** Is it a dedicated Ubuntu 24.04 machine (or dual boot) that can run ROS 2 Jazzy + Gazebo? Studio 5000 and WorkVisual are Windows-only, so they would run on a separate Windows laptop. | Station PC = Ubuntu 24.04 + ROS 2 Jazzy; separate Windows laptop for Studio 5000 / WorkVisual. |
| Q-S3 | **Simulation in the cloud session.** To build and run the Gazebo demos here, the session needs `packages.ros.org` and `packages.osrfoundation.org` allowed (currently blocked). OK to request that network change? | Otherwise code and world files are written here, and the demos are run on the team's Ubuntu machine. |
| Q-S4 | **Parts to deliver:** how many part types, size and weight of each (heaviest?), how many parts per order, how they are stored in the cell (trays, fixtures, a parts rack?). Drives gripper choice, pick positions, conveyor pallet nests, bin size and the 9 kg payload budget. | Small parts ≤ 0.5 kg, ≤ 10 per order (placeholder). |
| Q-S5 | **Transfer method** A / B / C (`docs/ARCHITECTURE.md` §3.6). | **A**: stop station + SMC pusher + padded chute (default). `transfer_mode` stays configurable. |
| Q-S6 | **Pallet or directly on the belt?** Quickdraw is normally a pallet conveyor. Pallets give a repeatable part position for the pusher and the photoeye; bare belt is simpler but parts can roll or rotate. | **Proposal: pallets with a simple nest**, with the pusher pushing the part off the pallet into the chute. **[CONFIRM]** once parts are known. |

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
| Q-N2 | Router model for the lab network; can it do **VLANs** and firewall rules between them? | VLAN-capable router. |
| Q-N3 | Is the router ever allowed to uplink to the school network for internet? | Only the robot VLAN may, and the controls VLAN never routes to WAN. |

## Process (Q-X)

| ID | Question | Current assumption |
|---|---|---|
| Q-X1 | Who owns the **risk assessment** and who signs off before AUT EXT runs with the TurtleBot (instructor, lab manager)? | Team as system integrator, reviewed by the instructor. |
| Q-X2 | Damaged-part retrieval: the updated brief says an **operator** empties the return bin and confirms the count. Confirm there is **no** arm handling of the return bin in v1. | Operator only. |
| Q-X3 | "Not loaded" vs "lost in transfer": if the PLC placed a part on the conveyor but the photoeye did not count it, treat it as a discrepancy for an operator to check (not restocked)? | Yes (`JOB_STATE_MACHINE.md` §5). |
| Q-X4 | Old requirements are void. Is there **any** number from the old midterm (budget, speed, size) that should still apply? | None carried over. |

## Answered

| ID | Answer | Date |
|---|---|---|
| A-1 | FlexBase is the **6 ft** model: 72" × 40". | 2026-11-12 |
| A-2 | KUKA gripper picks **individual parts**; no bin handling by the arm. | 2026-11-12 |
| A-3 | KR C4 **X66 (KLI)** on the **lab router** only; PLC and KR C4 never on the school network. | 2026-11-12 |
| A-4 | Handoff via **conveyor + transfer station**; the TurtleBot stays outside the guarding at all times. | 2026-11-12 |
| A-5 | The **C#** folder in Senior-Design is another project and is not part of APEx 2.0. | 2026-11-12 |
| A-6 | apex-2.0 is a **fresh start**; Senior-Design is read-only reference. | 2026-11-12 |
