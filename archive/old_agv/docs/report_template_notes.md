# Midterm report template notes (from the course example)

Source: `docs/reference/Example_Midterm_Report.pdf` ("MediMonitor Midterm
Report", ENGR 4580, 49 pages).

## Formatting conventions seen in the example

* Title page: project name + "Midterm Report", course, team names, logo.
* Front matter numbered with roman numerals (Abstract ii, Table of Contents iii); body restarts at 1.
* Times New Roman, double-spaced body, first-line indents; H1 bold large
  ("Introduction", "Design" …), H2 bold-italic ("Background", "Design Criteria").
  Sub-subsections as "o" bullets (e.g. "o Carousel", "o Pseudocode", "o Wiring Diagrams").
* Captions **below** figures/tables: bold-italic "*Figure N:*" / "*Table N:*" + plain title.
* Calculations follow a fixed pattern per actuator: *Assumptions → Free body
  diagram and coordinate frame → Formula and equation derivation → Solution and reality check*.
* Budget and BOM are separate tables in the appendix; references numbered with links.

Our report uses a different (also clean) house style: Calibri, numbered
headings ("1 Introduction"), light-blue table header rows, small
"Figure N …" captions below figures, footer "APEx Systems ENGR 4580" + page
number. **Decision for v2:** keep our house style (the task says keep ours
unless the example clearly expects something different) but adopt the
example's **section structure**.

## Section structure (order) and expected content

| # | Section | Expected content |
|---|---|---|
| — | Title page | Project name, "Midterm Report", course, team, (logo) |
| — | **Abstract** (own page) | Problem, goal, key capabilities, size/weight/safety constraints, what the report covers |
| — | **Table of Contents** | Two levels |
| 1 | **Introduction** | |
| 1.1 | Background | Problem/need, goal of the project |
| 1.2 | Design Criteria | Lettered list (A, B, C …) of measurable requirements: size, mass, capacity, interfaces, safety, cost |
| 1.3 | Prior Work Research | Numbered commercial/prior products with figure, pro/con bullets per subsystem; then how the team's idea differs |
| 1.4 | System Flow Diagram | Main system flowchart (with legend) + sub-sequence flowcharts |
| 2 | **Design** | |
| 2.1 | Design | Brainstormed concepts → weighted **decision matrix** (Table) → chosen concept; concept sketches (top/side view) |
| 2.1.x | Subsystems | One "o" sub-heading per subsystem with CAD images + text; **Pseudocode**; **Wiring Diagrams** (signal and power distribution) |
| 2.2 | Simulations and Calculations | Per actuator/subsystem: assumptions, FBD, equations, solution + reality check; **power calculations** (component currents, converter sizing, distribution tree) |
| 2.3 | Lessons Learned | What the analysis taught the team |
| 3 | **Prototyping** | |
| 3.1 | Construction | Status of the build with photos |
| 3.2 | Testing & analysis | What was/was not tested yet, honestly stated |
| 3.3 | Lessons learned | Build issues found |
| 4 | **Future Vision** | |
| 4.1 | Testing Agenda | Safety precautions list + table (Test / Procedure with quantitative pass criteria and trial counts) |
| 4.2 | Anticipated design and prototyping updates | Bulleted list |
| 4.3 | Timeline | Dated milestones |
| 5 | **Appendices** | Drawings (figures A1…), **Budget** table (item, qty, cost, supplier, part no., who paid), **Bill of Materials** table (part, manufacturer, part #, qty, unit, total, supplier) |
| — | **References** | Numbered list with links |

## Mapping our current report onto this structure

| Example section | Our report today | Action for v2 |
|---|---|---|
| Abstract (own page) | Abstract on title page | Move to its own page |
| Background / Design criteria / Prior work | §1 has all three | Keep; prior work is brief (table) — keep but it is lighter than the example |
| System Flow Diagram | Figure 1 (in §2) and Figure 5 (in §6) | Move into Introduction as "System Flow Diagram" |
| Design (decision matrix, concept) | §2 + §3 | Merge under "Design" |
| Subsystems incl. pseudocode, wiring | §3, §4, §5, §6 separate | Merge as Design subsections; **add software/firmware subsystem** |
| Simulations and Calculations | §7, §8, §9, §10 | Merge under one heading |
| Lessons learned (design) | inside §11 | Keep as Design → Lessons Learned |
| Prototyping: construction / testing / lessons | §11 | Split into the three sub-headings |
| Future vision: testing agenda / updates / timeline | §12 | Split; add explicit safety-precaution list and "anticipated updates" list |
| Appendices: drawings, budget, BOM | A, B, C | Keep; split budget summary vs BOM table |
| References | References | Keep |
