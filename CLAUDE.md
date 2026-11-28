# APEx 2.0 - notes for Claude Code

Read the developer notes first - working rules, safety rules, where things are, how to check a
change, current state and open items:

@docs/DEV_NOTES.md

Short version of the rules that must never be broken:

- Push only to `CDJ7/apex-2.0`. Never change `CDJ7/Senior-Design` or `aidanjmartin/tourbot` (read only).
- Leave the C# code alone (another project).
- Stages are named, never numbered; READMEs stay very detailed and are updated with the code.
- Software only reads safety state - never write, bypass, jumper or simulate around safety
  tags / I/O / CIP Safety / the PLd circuit. Arm and robot never share space. Every step has a
  timeout; faults stop everything and need an operator reset. Flag hazards as `⚠ RA`.
- No AI model names in commits or files. No pull requests unless asked.
- `lab.yaml` is the single source of lab geometry: after editing it run `generate_lab.py`,
  `tools/draw_lab_layout.py` and the tests (`generate_lab.py --check` must pass).
- When something important is decided or learned, update `docs/DEV_NOTES.md`.
