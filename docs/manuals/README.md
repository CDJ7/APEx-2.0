# Manuals (kept locally, not committed)

Manufacturer manuals are copyrighted. The KUKA specification states that it
*"may not be reproduced or disclosed to third parties without the express
permission of KUKA Deutschland GmbH"*, and this repository may be public. So
the PDFs are **git-ignored**: put your copies in this folder under the
names below and every document in `docs/` refers to them by these names.

| File name (place in this folder) | Document | Used for |
|---|---|---|
| `KUKA_KR_C4_compact_Specification_V10.pdf` | KUKA *KR C4 compact — Specification*, Spez KR C4 compact V10, issued 20.04.2018 (93 pages) | Safety functions (§5), operating modes (§5.5.3), operator safety (§5.5.4), single point of control (§5.8.9), X11 (§6.5), Ethernet safety bits (§6.6), X66 KLI (§6.6.3), X69 KSI (§6.9) |
| `KUKA_KR_6_R900-2_datasheet.pdf` | KUKA KR 6 R900-2 data sheet (0000-290-001 / V10.1) | Reach 901 mm, payload 6.5 kg max / 3 kg rated, ±0.02 mm, axis ranges, controller KR C4 compact |
| `AutomationNTH_FLEXBASE_brochure.pdf` | Automation NTH *FLEXBASE* 4-page brochure | 4 ft / 6 ft models (40" deep, 48" or 72" wide), controls (Compact GuardLogix 5380, 1734 POINT I/O, PLd circuit, managed switch, IO-Link master, SMC EtherNet/IP valve bank, stack light), Quickdraw conveyor options |

**TurtleBot 4:** the authoritative manual is the public
[turtlebot4-user-manual](https://github.com/turtlebot/turtlebot4-user-manual)
(web version: <https://turtlebot.github.io/turtlebot4-user-manual/>). It is
cloned into `third_party/` by `tools/fetch_third_party.sh`.

**Still wanted** (see `docs/OPEN_QUESTIONS.md`): KUKA System Software (KSS)
*Operating and Programming Instructions for System Integrators* for our KSS
version (Automatic External interface), KUKA.EtherNet/IP option manual,
FlexBase electrical drawings and PLC program, gripper data sheet.
