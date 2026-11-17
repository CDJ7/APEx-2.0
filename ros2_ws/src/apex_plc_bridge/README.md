# apex_plc_bridge

The **only** software connection to the FlexBase PLC (Compact GuardLogix 5380). Every other
node reads the PLC through this bridge's topics and writes only through its `WriteTag` service.

## What it does

* Polls every handshake tag at 10 Hz and publishes:
  * `/apex/plc/tags` - `std_msgs/String` JSON `{"connected": bool, "tags": {member: value}}`
  * `/apex/cell_status` - `apex_interfaces/CellStatus`
  * `/apex/arm_status` - `apex_interfaces/ArmStatus`
* Serves `/apex/plc/write_tag` (`apex_interfaces/WriteTag`).
* Reconnects every 2 s after a lost connection; the orchestrator sees stale data and faults
  (`PLC_LINK_LOST`) - nothing keeps moving on old data.

## Safety rules enforced in code (`tags.py`, `client.py`)

These are **not** configuration. They hold even if someone edits the YAML:

1. Writes go only to members of `APEX_FromStation.*` that are listed in `plc_tags.yaml`
   (`TagSpec.check_write`). The write prefix itself is checked when the spec loads.
2. Any name that looks like safety is refused, listed or not: `safety`, `safe`, `estop`,
   `guard`, `lightcurtain`, `muting`, `S:` status, `$OV_PRO`, operating mode, `KUKA_Mode`,
   and every KR C4 Ethernet-safety bit name (NHE, BS, QBS, SHS1, SHS2, US2, SBH, SPA, AF, FF, ...).
3. A refused write is logged at ERROR and returns `REFUSED` - nothing is sent to the PLC.
4. Safety state (`Safety_OK`) is **read and displayed only**. No code path uses it to enable
   motion; the PLd safety circuit in the GuardLogix safety task is the only thing that does.
5. The PLC side should mirror this: the station's tags are standard-task tags only, and the
   PLC program decides what to do with a request (docs/RISK_ASSESSMENT_POINTS.md).

`tests/test_plc_tags.py` and `tests/test_ros_nodes.py` prove all of the above.

## Backends and network profiles (`network.py`)

| Profile | `plc.allowed` | Default backend | Use |
|---|---|---|---|
| `network_school.yaml` | **false** | `sim` (apex_cell_sim on 127.0.0.1) | simulation, classroom, campus Wi-Fi |
| `network_lab.yaml` | true | `logix` (pycomm3 → PLC address) | isolated lab router only |

Asking for `backend:=logix` with the school profile stops the node with
`NetworkPolicyError` before any socket is opened. The PLC and the KR C4 are never on the
school network.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `tags_file` | `apex_bringup/config/plc_tags.yaml` | handshake definition |
| `network_profile` | `apex_bringup/config/network_school.yaml` | which network this computer is on |
| `backend` | `""` (profile default) | `sim` or `logix` (logix only if the profile allows it) |
| `poll_hz` | 10 | read rate |

## Run

```bash
ros2 run apex_plc_bridge plc_bridge                                  # school profile -> simulator
ros2 run apex_plc_bridge plc_bridge --ros-args \
    -p network_profile:=$(ros2 pkg prefix apex_bringup)/share/apex_bringup/config/network_lab.yaml
ros2 service call /apex/plc/write_tag apex_interfaces/srv/WriteTag "{name: Safety_OK, value_json: 'true'}"
#   -> ok: false, message: 'REFUSED: ...'
```

## Needs hardware testing

* pycomm3 array syntax `APEX_FromStation.Job_PartCodes{10}` against the real controller.
* Read time of all tags in one request (target < 50 ms) and the 10 Hz rate on the lab router.
* Behaviour when the PLC goes to Program mode or is power-cycled.
