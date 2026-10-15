# Hardware Bring-up, Calibration and "Verify on Real Hardware" List

Everything below was **simulated** during development (the Arduino firmware
is mirrored by `agv_pi/sim/sim_arduino.py`, the LiDAR/camera by ray-casting
a simulated world). These are the items that can only be confirmed on the
real AGV. Work through them in order, wheels off the ground until §4.

## 1. Arduino / drive train (`agv_arduino/agv_arduino.ino`)

- [ ] Cytron **MDD20A** wired in PWM/DIR mode per `docs/WIRING.md`; 10 kΩ pull-downs on PWM1/PWM2.
- [ ] Encoder A/B levels: 3.3 V encoders read reliably on the 5 V R4 (level shifter
      or confirmed 5 V encoder supply, see `docs/WIRING.md` §3). Every edge counts
      when a wheel is turned slowly by hand.
- [ ] `CMD,80,80` turns **both wheels forward**; otherwise flip `INVERT_MOTOR_L/R`.
- [ ] Forward motion gives **positive** `enc_left`/`enc_right`; otherwise flip `INVERT_ENC_L/R`.
- [ ] **Ticks per wheel revolution**: `RST`, rotate a wheel exactly 10 turns by hand,
      read the count, divide by 10 → `agv.robot.encoder_ticks_per_rev`.
      Expected ≈ **1948** for the 5303-2402-0139 (report's 8,440 pulses/s at
      130 rpm ÷ 2 for ×2 decoding) — **VERIFY**.
- [ ] **Dead band**: increase `CMD,n,n` from 0 until the wheels just turn → `agv.robot.min_pwm`.
- [ ] **Max wheel speed**: at `CMD,255,255` read `speed_left` (ticks/s) →
      `max_wheel_speed_mps = speed × π × wheel_diameter / ticks_per_rev`.
- [ ] **Hardwired E-stop** drops K1 (motion power) with the Pi still commanding
      motion; Pi, Arduino and LiDAR stay powered; A3 reports `estop=1`.
- [ ] Releasing the E-stop and pressing the manual reset does **not** restart
      motion: the AGV stays paused until an explicit *Resume* (report p. 14).
- [ ] **Stopping distance** from 0.30 m/s, both by PWM-zero (braking) and by K1
      opening (coasting): **[TBD: measured stopping distance]**.
- [ ] Pulling the USB cable stops the motors within 0.5 s (watchdog).
- [ ] (Only if bumpers are fitted, `BUMPERS_FITTED 1`) bumpers trip and latch;
      `CLR` only clears after release.
- [ ] Battery reading matches a multimeter at the OSA170 terminals (adjust
      `VBAT_DIVIDER`, 56k/10k = 6.6); A2 never exceeds 5 V. Set
      `agv.battery.full_mv` / `empty_mv` for the 24 V SLA **[TBD from a discharge test]**.
- [ ] Driver PWM frequency acceptable (R4 `analogWrite` ≈ 490 Hz; whine or poor
      low-speed torque → use the Renesas `PwmOut` class at ~20 kHz, MDD20A max 20 kHz).

## 1a. Dock sensor, actuator, manual RC

- [ ] **Hall dock sensor**: `dock=1` in `STAT` only when the AGV sits in the
      charge / wait position, within the dock tolerance **[TBD: dock position tolerance]**. Returning
      AGV reaches DOCKING → IDLE with `docked: true`; with the magnet removed it
      raises `dock_failed` after `agv.dock.max_approach_s`.
- [ ] **Return-bin actuator**: `ACT,1,<ms>` extends, `ACT,-1,<ms>` retracts, never
      both relays at once; time the full stroke and set `agv.actuator.extend_ms` /
      `retract_ms`. *Empty return bin* in the GUI tips and retracts while docked.
      E-stop during a stroke stops the actuator.
- [ ] **Flysky FS-i6 / FS-iA6B**: receiver bound, i-BUS on D0; SwA (CH5) on →
      `rc=2`, Pi shows MANUAL and the sticks drive the AGV slowly
      (`RC_MAX_PWM`); SwA off → IDLE. Switching the transmitter **off** must stop
      the motors (receiver failsafe set so CH5 drops to off).

## 2. Geometry / odometry (`config.yaml → agv.robot`)

- [ ] Measure `wheel_diameter_m` (or roll 10 revolutions and measure the distance / 10π).
- [ ] Measure `wheel_base_m` between the tyre contact centres.
- [ ] **Straight-line test**: drive 3 m with the station/console, compare the
      odometry `x` to a tape measure; correct `wheel_diameter_m` by the ratio.
- [ ] **Rotation test**: turn in place 10 full turns; if odometry reports more/less
      than 3600°, scale `wheel_base_m` by `reported / actual`.
- [ ] `wheel_base_m` (track width) is a placeholder 0.50 m **[TBD: measured track width]**.
- [ ] `width_m` / `length_m` = outer footprint (frame 700 × 500 mm plus wheels:
      `width_m` 0.62 is **[TBD: measured overall width]**), used for collision checks.

## 3. LiDAR (`agv.lidar`)

- [ ] `python -m agv_pi.tools.lidar_check`: health `Good`, 5.5–10 Hz.
- [ ] Object placed on the robot's **left** appears in the *left* sector. If it
      shows on the right, set `clockwise` to the opposite value; if rotated,
      set `angle_offset_deg` (CCW positive) so the *front* sector sees the front.
- [ ] `mount_x_m` / `mount_y_m` = LiDAR centre relative to the robot centre.
- [ ] No chassis parts in the scan (min range `min_range_m` or raise the LiDAR);
      self-hits inside the footprint are ignored, but posts/cables in view are not.
- [ ] Glass, black or shiny surfaces in the area: check they appear in the map.

## 4. Mapping & localisation

- [ ] Map the area at ≤ 0.25 m/s with `mapping_tool`; the PNG should show straight walls.
- [ ] Route editor: every station has a `plc_id`; `v` (validate) reports no edges
      closer than 0.3 m to walls.
- [ ] AGV started at MAIN with the configured heading localises (`localized: true`
      in telemetry) and the station map shows it in the right place.
- [ ] Drive a full loop: the pose must not jump (> 10 cm) or drift; if ICP
      rejects matches often, increase `localization.icp_max_correspondence_m`
      slightly or lower `min_inlier_ratio`.

## 5. Navigation tuning (`agv.navigation`, `agv.wheel_pid`)

- [ ] Start with `max_speed_mps: 0.2`; raise once stable.
- [ ] Wheel PI: the robot tracks speed without oscillation (raise `kp` until it
      oscillates, then halve; `ki` removes steady-state error).
- [ ] Pure pursuit: weaving → increase `lookahead_m`; cutting corners → decrease it.
- [ ] `goal_tolerance_m` and station `heading_deg` give repeatable docking.

## 6. Safety sensors (`agv.safety`, `agv.ir`, `agv.vision`)

- [ ] Walk in front of the moving AGV: it stops at ≥ `stop_distance_m` and resumes
      ≥ `obstacle_clear_s` after you leave (**transient** — no reroute).
- [ ] Place a box on a route: the AGV waits `permanent_obstacle_timeout_s`,
      marks the segment blocked (red dashed on the station map), reroutes,
      and the station shows the event.
- [ ] IR sensors (in front of the drive wheels) stop the AGV for a low object the
      camera and LiDAR miss (e.g. a pallet fork or a box below LiDAR height).
      **[TBD: IR sensor model]**
- [ ] `stop_distance_cm` for the IR matches the real braking distance at max speed.
- [ ] Camera: `camera_check` ≥ 15 FPS; ROI (yellow trapezoid in the snapshot)
      covers the floor in front of the AGV; tune `zone_near_y` / `zone_mid_y` so
      that "near" ≈ 0.6 m and "mid" ≈ 1.2 m on your floor.
- [ ] Lighting: test under the real shop lighting (flicker from LED/fluorescent
      lights can cause motion false positives; if so raise the difference
      threshold in `motion_detector.py` or `min_blob_area`).
- [ ] Optional person detector: `download_models.sh`, enable, check FPS stays ≥ 15.

## 7. Network & station

- [ ] All four devices have their static IPs; station ↔ AGV latency < 50 ms (`ping`).
- [ ] Wi-Fi coverage along every route (walk the route with `ping -i 0.2`).
      If no station message arrives for `safety.station_link_timeout_s` (3 s)
      while moving, the AGV stops and holds (`link_lost`), then continues when
      the link is back (`station_link_auto_resume`). Test by unplugging the
      station's network cable mid-route.
- [ ] Router: NETGEAR DGND3700v2 — confirm LAN subnet/gateway and set DHCP
      reservations for the station, laptop, PLC, AGV and phone **[TBD]**.
- [ ] **Phone QR scanner**: scanning a label `APEX:<part>` with the app shows
      "OK"/"WRONG PART" on the phone and the Orders → Loading tab updates.
- [ ] Secrets changed (`APEX_API_KEY`, `APEX_AGV_TOKEN`, `APEX_PLC_TOKEN`) and
      allow-lists set (`security.*_allowed_ips`).
- [ ] REST from the workstation laptop: `python -m tests.fake_laptop_client parts`.

## 8. PLC

- [ ] Confirm the CompactLogix **controller** catalog number (1769-ECR is the end
      cap) and install Rockwell's Modbus TCP Client AOI (see `docs/PLC_INTEGRATION.md`).
- [ ] Station Modbus port (502) matches the AOI's target port.
- [ ] `PLC_LINK_OK` = 1 on the station status block (heartbeat seen).
- [ ] CREATE_ORDER / ESTOP / RESET round-trips succeed with `CMD_ACK_SEQ` handshake.
- [ ] PLC alarms on `STATION_HEARTBEAT` stall and on `AGV_ESTOP` / `AGV_STATE = 7/8`.
- [ ] Loading: `LOAD_SEQ` change starts the KUKA program; `LOAD_STATE = 4`
      (wrong pick) holds the arm; `DEPART_PERMIT` only after the arm is clear;
      `DEPART_PERMIT` before the scan is complete returns `REJECTED`.
