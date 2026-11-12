#!/usr/bin/env bash
# Clone the TurtleBot reference repositories into third_party/ (git-ignored),
# on the branch matching our ROS 2 distro. Usage:
#   tools/fetch_third_party.sh            # Jazzy (default)
#   ROS_DISTRO=humble tools/fetch_third_party.sh
set -euo pipefail
DISTRO="${ROS_DISTRO:-jazzy}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)/third_party"
mkdir -p "$ROOT"
clone() {  # name branch
  if [ -d "$ROOT/$1/.git" ]; then echo "exists: $1"; return; fi
  git clone --depth 1 -b "$2" "https://github.com/turtlebot/$1" "$ROOT/$1"
}
clone turtlebot4            "$DISTRO"   # navigation launch files, TurtleBot4Navigator, msgs
clone turtlebot4_simulator  "$DISTRO"   # Gazebo simulation
clone turtlebot4_robot      "$DISTRO"   # on-robot software
clone turtlebot4_tutorials  "$DISTRO"   # waypoint / navigation examples
clone turtlebot4-hardware   master      # top plate drawings (no distro branches)
clone turtlebot4-user-manual gh-pages   # authoritative manual (all distros, tabbed)
# turtlebot/turtlebot_simulator is NOT used: it is the legacy TurtleBot 1/2 ROS 1
# simulator (branches groovy ... melodic only).
echo "third_party ready for ROS 2 $DISTRO in $ROOT"
