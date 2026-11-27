#!/usr/bin/env bash
# Build the map of a mapping run again ON THE LAPTOP, from the run's raw bag. Run on the laptop.
#
#   ros2_ws/src/apex_mapping/tools/rebuild_map.sh <run_dir> [slam.yaml]
#
# Why: on the robot, SLAM shares the Raspberry Pi with the camera and the base, falls behind and
# drops lidar scans ("queue is full" in slam.log). The map drifts and the loop closure at the end
# of the run then yanks it. The laptop replays the bag with slam_toolbox in sync mode, which uses
# every scan, so the same drive gives straight, single walls.
# (APEx; not from TourBot. map_lab.sh runs this automatically after copying a run.)
#
# Writes into <run_dir>: rebuilt.pgm + rebuilt.yaml (the map to use) and rebuilt.posegraph +
# rebuilt.data (the SLAM session). The robot's own map.pgm/.yaml stay as they were.
# Runs on a laptop-only ROS domain, so nothing reaches the robot.
#
# Settings: APEX_REBUILD_RATE (bag playback speed, default 2).
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
RUN="${1:?usage: rebuild_map.sh <run_dir> [slam.yaml]}"
RUN="$(cd "$RUN" && pwd)"
PARAMS="${2:-$HERE/../config/slam.yaml}"
RATE="${APEX_REBUILD_RATE:-2}"
[ -d "$RUN/bag" ] || { echo "[FAIL] No bag/ in $RUN"; exit 1; }

source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=72 ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
WORK="$(mktemp -d)"
cleanup() { [ -n "${slam:-}" ] && kill -INT "$slam" 2>/dev/null; sleep 1; kill -9 "${slam:-0}" 2>/dev/null; rm -rf "$WORK"; }
trap cleanup EXIT

echo "=== Rebuilding the map on the laptop from $RUN/bag ==="
python3 "$HERE/strip_slam_tf.py" "$RUN/bag" "$WORK/bag" || { echo "[FAIL] Could not read the bag"; exit 1; }

ros2 launch slam_toolbox online_sync_launch.py slam_params_file:="$PARAMS" use_sim_time:=true \
  > "$RUN/rebuild_slam.log" 2>&1 &
slam=$!
for _ in $(seq 30); do grep -q "Activating" "$RUN/rebuild_slam.log" && break; sleep 0.5; done
sleep 2

dur="$(python3 -c "import yaml,sys; print(round(yaml.safe_load(open(sys.argv[1]))['rosbag2_bagfile_information']['duration']['nanoseconds']/1e9/$RATE))" "$WORK/bag/metadata.yaml" 2>/dev/null)"
echo "Replaying the drive at ${RATE}x (about ${dur:-?} s)..."
ros2 bag play "$WORK/bag" --clock 50 --rate "$RATE" --disable-keyboard-controls \
  --topics /scan /tf /tf_static /odom > "$RUN/rebuild_play.log" 2>&1
sleep 10   # let SLAM finish the last scans and the final loop closure

rm -f "$RUN"/rebuilt.{pgm,yaml,posegraph,data}
ros2 run nav2_map_server map_saver_cli -f "$RUN/rebuilt" \
  --ros-args -p map_subscribe_transient_local:=true > "$RUN/rebuild_save.log" 2>&1
ros2 service call /slam_toolbox/serialize_map slam_toolbox/srv/SerializePoseGraph \
  "{filename: '$RUN/rebuilt'}" > /dev/null 2>&1

drops=$(grep -c "queue is full" "$RUN/rebuild_slam.log")
if [ ! -f "$RUN/rebuilt.yaml" ]; then
  echo "[FAIL] No map was produced (see $RUN/rebuild_*.log). The robot's map.pgm is still there."
  exit 1
fi
size="$(python3 -c "from PIL import Image; import sys; w,h=Image.open(sys.argv[1]).size; print(f'{w*0.05:.1f} x {h*0.05:.1f} m')" "$RUN/rebuilt.pgm" 2>/dev/null)"
echo "[OK]   $RUN/rebuilt.pgm + rebuilt.yaml (${size:-?}; scans dropped: $drops)"
[ -f "$RUN/rebuilt.posegraph" ] && echo "[OK]   SLAM session: rebuilt.posegraph + rebuilt.data"
[ "$drops" -gt 0 ] && echo "[WARN] Some scans were dropped; try APEX_REBUILD_RATE=1."
exit 0
