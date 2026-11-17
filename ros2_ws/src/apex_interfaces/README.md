# apex_interfaces

ROS 2 messages and services shared by every APEx 2.0 node. Built with `rosidl` (ament_cmake);
everything else in the workspace is pure Python (ament_python).

## Messages

| Message | Topic | Publisher | What it carries |
|---|---|---|---|
| `OrderLine` | (inside `SubmitJob`) | station GUI / demo client | `part_number`, `part_code` (selects the KRL pick program / shelf slot on the PLC), `quantity` |
| `LineReceipt` | (inside `ConfirmReceipt`) | station GUI | Worker's count for one line: `ok + damaged + missing` **must equal** the quantity loaded for that line |
| `JobStatus` | `/apex/job_status` (2 Hz) | apex_orchestrator | Current job, state-machine step (`docs/JOB_STATE_MACHINE.md`), seconds in step, destination, route direction, parts expected/counted, queue, fault code/text, and the robot as the orchestrator sees it (pose, battery, docked, current route and remembered blockages - same for Nav2 and the kinematic stand-in) |
| `JobEvent` | `/apex/job_events` | apex_orchestrator | One message per state-machine event (`job_queued`, `plc_ack`, `part_counted`, `load_complete`, `direction_blocked`, `rerouting`, `receipt_confirmed`, `fault`, `docked`, ...). Details are JSON in `data_json`. The station GUI books inventory movements from these. |
| `CellStatus` | `/apex/cell_status` | apex_plc_bridge | **Read-only** view of the FlexBase PLC: cell mode, transfer mode, chute part count, load complete, job fault + text, stack lights, `safety_ok` and `safety_simulated` (display only). |
| `ArmStatus` | `/apex/arm_status` | apex_plc_bridge | KUKA KR C4 status **as reported by the PLC**: mode (T1/T2/AUT/AUT EXT), ready, busy, fault, program number. The station never talks to the KR C4 directly. |

## Services

| Service | Server | Request → Response |
|---|---|---|
| `/apex/submit_job` (`SubmitJob`) | orchestrator | `order_id`, `destination` (pose name from `lab.yaml`, e.g. `station_a`), `lines[]` → `accepted`, `job_id`, `message` |
| `/apex/confirm_receipt` (`ConfirmReceipt`) | orchestrator | `job_id`, one `LineReceipt` per order line → `ok`, `message` (refused if the numbers do not add up) |
| `/apex/confirm_return` (`ConfirmReturn`) | orchestrator | `job_id`, `count` of damaged parts taken out of the return bin |
| `/apex/resolve_shortfall` (`ResolveShortfall`) | orchestrator | `job_id`, `action` = `send` (deliver what reached the bin) or `cancel` |
| `/apex/operator` (`Operator`) | orchestrator | `action` = `stop` / `reset` / `resume` / `cancel`, `operator_name`, `job_id` (for cancel). Reset never restarts motion; Resume does. |
| `/apex/plc/write_tag` (`WriteTag`) | apex_plc_bridge | `name` (APEX_FromStation member), `value_json` → `ok`, `message`. Anything outside the allow-list returns `REFUSED`. |

## Design notes

* Job/receipt data are typed messages; free-form event details stay JSON so new event
  kinds do not need an interface change (and a rebuild of every package).
* There is deliberately **no** message or service that can set a safety value, a KUKA
  mode or an override. `WriteTag` exists, but apex_plc_bridge refuses every name outside the
  `APEX_FromStation` allow-list (see `apex_plc_bridge/README.md`).
