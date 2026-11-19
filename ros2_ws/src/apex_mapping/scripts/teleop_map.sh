#!/bin/bash
# Runs teleop_map.py on the robot with the robot's ROS environment.
# The laptop's tools/map_floor.sh starts this inside tmux; you can also run it directly:
#   ~/tourbot/ros2_ws/src/tourbot_navigation/scripts/teleop_map.sh [--floor N]
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
source /etc/turtlebot4/setup.bash
WS_SETUP="$HERE/../../../install/setup.bash"   # ros2_ws/install, when run from ros2_ws/src
[ -f "$WS_SETUP" ] && source "$WS_SETUP"

# The tour stack (tourbot_bringup's tourbot.service: AMCL + Nav2 + app bridge) and SLAM
# both publish /map, and together they'd overload the Pi. Stop it while mapping and
# start it again afterwards.
restart_tour_stack=
if systemctl --user is-active --quiet tourbot.service 2>/dev/null; then
  echo "Stopping the tour stack while mapping (it starts again when you're done)..."
  systemctl --user stop tourbot.service && restart_tour_stack=1
fi

python3 "$HERE/teleop_map.py" "$@"
rc=$?

if [ -n "$restart_tour_stack" ]; then
  echo "Starting the tour stack again (dock the robot so it localizes correctly)."
  systemctl --user start tourbot.service
fi
# Inside tmux the window closes when this exits; keep the last messages readable.
if [ -n "$TMUX" ]; then
  read -r -t 15 -p "Press Enter to close (closes by itself in 15 s)... " || true
  echo
fi
exit $rc
