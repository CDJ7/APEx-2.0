#!/usr/bin/env bash
# Map one AEB floor by driving the TurtleBot 4 with its controller. Run this on the laptop.
#
#   ros2_ws/src/tourbot_navigation/tools/map_floor.sh [floor]
#
# 1. asks which floor (1-3)
# 2. SSHes to the robot and runs scripts/teleop_map.sh there inside tmux, so a Wi-Fi drop
#    doesn't end the run (run this again to reattach)
# 3. after you hold OPTIONS on the controller to save, copies the run to
#    <TourbotWS>/maps/level<N>/<date_time>/ and opens the map in RViz
#
# Settings (environment variables):
#   TOURBOT_ROBOT      SSH target            (default ubuntu@10.80.91.57, the robot on BlueWiFi)
#   TOURBOT_MAPS_DIR   where runs are copied (default: the maps/ folder next to the repo)
#   TOURBOT_NO_RVIZ=1  copy only, don't open RViz

ROBOT="${TOURBOT_ROBOT:-ubuntu@10.80.91.57}"
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
MAPS_DIR="${TOURBOT_MAPS_DIR:-$(dirname "$REPO")/maps}"
SESSION=teleop_map
LAUNCHER='~/tourbot/ros2_ws/src/tourbot_navigation/scripts/teleop_map.sh'
SSH_OPTS=(-o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=3)

rsh() { ssh "${SSH_OPTS[@]}" "$ROBOT" "$@" 2> >(grep -v setlocale >&2); }

echo "=== TourBot floor mapping ==="
if ! rsh true; then
  echo "[FAIL] Can't reach the robot at $ROBOT. Is it on and on the same Wi-Fi?"
  echo "       Set TOURBOT_ROBOT=ubuntu@<address> if its address changed."
  exit 1
fi
echo "[OK]   Robot reachable at $ROBOT"

last_before="$(rsh 'cat ~/maps/.last_run 2>/dev/null')"

if rsh tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "A mapping run is already going on the robot; reattaching to it."
  ssh -t "${SSH_OPTS[@]}" "$ROBOT" tmux attach -t "$SESSION"
else
  floor="${1:-}"
  while [[ ! "$floor" =~ ^[123]$ ]]; do
    read -rp "Which floor are you mapping? [1/2/3]: " floor
  done
  ssh -t "${SSH_OPTS[@]}" "$ROBOT" tmux new-session -s "$SESSION" "$LAUNCHER --floor $floor"
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

run_dir="$(rsh 'cat ~/maps/.last_run 2>/dev/null')"
if [ -z "$run_dir" ] || [ "$run_dir" = "$last_before" ]; then
  echo "No new run was saved."
  exit 1
fi

level="$(basename "$(dirname "$run_dir")")"   # level<N>
dest="$MAPS_DIR/$level/$(basename "$run_dir")"
mkdir -p "$MAPS_DIR/$level"
echo "Copying $run_dir to $dest ..."
if ! scp -r -q "${SSH_OPTS[@]}" "$ROBOT:$run_dir" "$MAPS_DIR/$level/" 2> >(grep -v setlocale >&2); then
  echo "[FAIL] Copy failed. The run is still on the robot at $run_dir. Retry with:"
  echo "       scp -r $ROBOT:$run_dir $MAPS_DIR/$level/"
  exit 1
fi
echo "[OK]   Copied ($(du -sh "$dest" | cut -f1))"
sed 's/^/       /' "$dest/run.yaml" 2>/dev/null

if [ ! -f "$dest/map.yaml" ]; then
  echo "No SLAM map in this run (only the raw bag), so there is nothing to open in RViz."
  exit 0
fi
[ -n "${TOURBOT_NO_RVIZ:-}" ] && exit 0

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
