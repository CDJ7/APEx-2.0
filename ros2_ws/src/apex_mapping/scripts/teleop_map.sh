#!/bin/bash
# Runs teleop_map.py on the robot with the robot's ROS environment.
# Adapted from aidanjmartin/tourbot (tourbot_navigation/scripts/teleop_map.sh, commit d4ba4ad);
# APEx: the TourBot tour-stack service handling is removed.
# The laptop's tools/map_lab.sh copies this to ~/apex_mapping/ on the robot and starts it inside
# tmux; you can also run it directly on the robot:
#   ~/apex_mapping/scripts/teleop_map.sh [--name lab] [--no-controller]
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
source /etc/turtlebot4/setup.bash

python3 "$HERE/teleop_map.py" "$@"
rc=$?

# Inside tmux the window closes when this exits; keep the last messages readable.
if [ -n "$TMUX" ]; then
  read -r -t 15 -p "Press Enter to close (closes by itself in 15 s)... " || true
  echo
fi
exit $rc
