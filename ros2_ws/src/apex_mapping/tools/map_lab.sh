#!/usr/bin/env bash
# Map the APEx lab with SLAM running ON THE ROBOT (survives Wi-Fi drops). Run this on the laptop.
#
#   ros2_ws/src/apex_mapping/tools/map_lab.sh [name] [--no-controller]
#
# Adapted from aidanjmartin/tourbot (tourbot_navigation/tools/map_floor.sh, commit d4ba4ad),
# borrowed with the TourBot team's knowledge. APEx changes: the run name replaces the floor
# number, the robot address and folders are APEx's, and the scripts are copied to the robot
# first (the APEx repository is not on the robot).
#
# 1. copies scripts/ and config/slam.yaml to ~/apex_mapping/ on the robot and checks that the
#    robot has tmux, slam_toolbox and ros2 bag
# 2. SSHes to the robot and runs scripts/teleop_map.sh there inside tmux, so a Wi-Fi drop
#    doesn't end the run (run this again to reattach)
# 3. after you stop it (OPTIONS on the controller, or Ctrl-C), copies the run to
#    ~/apex_maps/runs/<name>/<date_time>/ and opens the map in RViz
#
# Drive with the TurtleBot 4 controller, or pass --no-controller and drive from the station
# GUI's Drive & Map tab (W A S D) - but don't press "Start mapping" there: SLAM runs on the robot.
#
# Settings (environment variables):
#   APEX_ROBOT       SSH target            (default ubuntu@192.168.0.3, the robot on the lab router)
#   APEX_MAPS_DIR    where runs are copied (default ~/apex_maps/runs)
#   APEX_NO_RVIZ=1   copy only, don't open RViz

ROBOT="${APEX_ROBOT:-ubuntu@192.168.0.3}"
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PKG="$(cd "$HERE/.." && pwd)"
MAPS_DIR="${APEX_MAPS_DIR:-$HOME/apex_maps/runs}"
SESSION=apex_map
REMOTE_DIR='apex_mapping'                       # ~/apex_mapping on the robot
LAUNCHER="~/$REMOTE_DIR/scripts/teleop_map.sh"
SSH_OPTS=(-o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=3)

name=lab
extra=()
for arg in "$@"; do
  case "$arg" in
    --no-controller) extra+=(--no-controller) ;;
    -*) echo "unknown option $arg"; exit 2 ;;
    *) name="$arg" ;;
  esac
done
name="$(echo "$name" | tr -c 'A-Za-z0-9_\n-' '_')"

rsh() { ssh "${SSH_OPTS[@]}" "$ROBOT" "$@" 2> >(grep -v setlocale >&2); }

echo "=== APEx lab mapping (SLAM on the robot) ==="
if ! rsh true; then
  echo "[FAIL] Can't reach the robot at $ROBOT. Is it on, and is the laptop cabled to the lab router?"
  echo "       Set APEX_ROBOT=ubuntu@<address> if its address changed (see its screen)."
  exit 1
fi
echo "[OK]   Robot reachable at $ROBOT"

missing="$(rsh 'source /etc/turtlebot4/setup.bash >/dev/null 2>&1
  command -v tmux >/dev/null || echo -n "tmux "
  ros2 pkg prefix slam_toolbox >/dev/null 2>&1 || echo -n "ros-jazzy-slam-toolbox "
  ros2 pkg prefix rosbag2_storage_mcap >/dev/null 2>&1 || echo -n "ros-jazzy-rosbag2-storage-mcap "
  python3 -c "import numpy" 2>/dev/null || echo -n "python3-numpy "')"
if [ -n "$missing" ]; then
  echo "[FAIL] The robot is missing: $missing"
  echo "       Install them on the robot (needs internet on the robot, e.g. temporarily on a phone"
  echo "       hotspot):  sudo apt install $missing"
  exit 1
fi
echo "[OK]   Robot has tmux, slam_toolbox, ros2 bag and numpy"

last_before="$(rsh "cat ~/apex_maps/runs/.last_run 2>/dev/null")"

if rsh tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "A mapping run is already going on the robot; reattaching to it."
  ssh -t "${SSH_OPTS[@]}" "$ROBOT" tmux attach -t "$SESSION"
else
  # copy the current scripts + SLAM settings to the robot (nothing else is installed there)
  if ! rsh "mkdir -p ~/$REMOTE_DIR" || \
     ! scp -q -r "${SSH_OPTS[@]}" "$PKG/scripts" "$PKG/config" "$ROBOT:$REMOTE_DIR/" 2> >(grep -v setlocale >&2); then
    echo "[FAIL] Could not copy the mapping scripts to the robot."
    exit 1
  fi
  rsh "chmod +x ~/$REMOTE_DIR/scripts/teleop_map.sh ~/$REMOTE_DIR/scripts/teleop_map.py"
  echo "[OK]   Mapping scripts copied to ~/$REMOTE_DIR on the robot"
  ssh -t "${SSH_OPTS[@]}" "$ROBOT" tmux new-session -s "$SESSION" "$LAUNCHER --name $name ${extra[*]}"
fi

# The SSH session ended. If the run is still going (Wi-Fi dropped, or tmux detached with
# Ctrl-B d), wait for it to finish on the robot.
while true; do
  rsh tmux has-session -t "$SESSION" 2>/dev/null
  rc=$?
  [ $rc -eq 1 ] && break   # session gone: the run finished
  echo "Mapping is still running on the robot (connection lost or detached)."
  echo "Waiting for it to finish... Ctrl-C to stop waiting; run this again to reattach."
  sleep 10
done

run_dir="$(rsh "cat ~/apex_maps/runs/.last_run 2>/dev/null")"
if [ -z "$run_dir" ] || [ "$run_dir" = "$last_before" ]; then
  echo "No new run was saved."
  exit 1
fi

run_name="$(basename "$(dirname "$run_dir")")"
dest="$MAPS_DIR/$run_name/$(basename "$run_dir")"
mkdir -p "$MAPS_DIR/$run_name"
echo "Copying $run_dir to $dest ..."
if ! scp -r -q "${SSH_OPTS[@]}" "$ROBOT:$run_dir" "$MAPS_DIR/$run_name/" 2> >(grep -v setlocale >&2); then
  echo "[FAIL] Copy failed. The run is still on the robot at $run_dir. Retry with:"
  echo "       scp -r $ROBOT:$run_dir $MAPS_DIR/$run_name/"
  exit 1
fi
echo "[OK]   Copied ($(du -sh "$dest" | cut -f1))"
sed 's/^/       /' "$dest/run.yaml" 2>/dev/null

if [ ! -f "$dest/map.yaml" ]; then
  echo "No SLAM map in this run (only the raw bag), so there is nothing to open in RViz."
  exit 0
fi
echo
echo "To use this map for navigation (robot.launch.py expects ~/apex_maps/lab_real.yaml):"
echo "  cp $dest/map.pgm ~/apex_maps/lab_real.pgm"
echo "  sed 's/^image: .*/image: lab_real.pgm/' $dest/map.yaml > ~/apex_maps/lab_real.yaml"
[ -n "${APEX_NO_RVIZ:-}" ] && exit 0

# Show the map in RViz on a laptop-only ROS domain, so nothing reaches the robot.
echo "Opening the map in RViz. Close the RViz window when you're done."
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=73 ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
ros2 run nav2_map_server map_server --ros-args -p yaml_filename:="$dest/map.yaml" \
  > /dev/null 2>&1 &
map_server=$!
trap 'kill $map_server 2>/dev/null' EXIT
sleep 2
ros2 lifecycle set /map_server configure > /dev/null && ros2 lifecycle set /map_server activate > /dev/null
rviz2 -d "$HERE/map_view.rviz" > /dev/null 2>&1
