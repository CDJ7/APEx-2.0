"""Real TurtleBot 4 on the lab router: localization + Nav2 (on the station laptop) + the APEx stack.

    # 1. once: record a map of the real lab (drive with station/tools/teleop_wasd.py)
    ros2 launch turtlebot4_navigation slam.launch.py
    ros2 run nav2_map_server map_saver_cli -f ~/apex_maps/lab_real
    # 2. every day
    ros2 launch apex_bringup robot.launch.py map:=$HOME/apex_maps/lab_real.yaml
    #    ... cell:=sim to use the cell simulator until the PLC is connected (default),
    #        cell:=plc for the real FlexBase PLC (network_lab.yaml address), tilt:=robot when
    #        tilt_platform runs on the robot (default) or tilt:=none without the tray.

**UNTESTED on the real robot** - written from the TurtleBot 4 Jazzy launch files; see
docs/TURTLEBOT_STATUS.md for the bring-up checklist. Uses ``use_sim_time:=false``, the
orchestrator's Nav2 adapter, and sets the initial pose at the ``dock`` pose from lab.yaml - the
robot must start on its dock, and the real map must be aligned to the lab.yaml frame (origin at
the FlexBase centre) or the poses re-measured.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

from apex_bringup.nav2_params import merged_params


def _setup(context, *args, **kwargs):
    share = get_package_share_directory("apex_bringup")
    tb4_nav = get_package_share_directory("turtlebot4_navigation")
    ns = LaunchConfiguration("namespace")
    cell = LaunchConfiguration("cell").perform(context)
    nav2_params = merged_params(os.path.join(tb4_nav, "config", "nav2.yaml"),
                                os.path.join(share, "config", "nav2_overrides.yaml"))
    localization = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(tb4_nav, "launch", "localization.launch.py")),
        launch_arguments=[("use_sim_time", "false"), ("namespace", ns), ("map", LaunchConfiguration("map"))])
    nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(tb4_nav, "launch", "nav2.launch.py")),
        launch_arguments=[("use_sim_time", "false"), ("namespace", ns), ("params_file", str(nav2_params))])
    apex = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(share, "launch", "apex.launch.py")),
        launch_arguments=[("network", "lab"), ("use_sim_time", "false"), ("nav_backend", "nav2"),
                          ("cell_sim", "true" if cell == "sim" else "false"),
                          ("backend", "sim" if cell == "sim" else "logix"),
                          ("tilt", LaunchConfiguration("tilt")), ("namespace", ns)])
    return [localization, TimerAction(period=5.0, actions=[nav2]), TimerAction(period=10.0, actions=[apex])]


def generate_launch_description() -> LaunchDescription:
    share = get_package_share_directory("apex_bringup")
    return LaunchDescription([
        DeclareLaunchArgument("map", default_value=os.path.join(share, "maps", "apex_lab.yaml"),
                              description="map yaml - use the SLAM map of the real lab"),
        DeclareLaunchArgument("cell", default_value="sim", choices=["sim", "plc"],
                              description="sim = apex_cell_sim until the PLC is connected; plc = real FlexBase"),
        DeclareLaunchArgument("tilt", default_value="robot", choices=["sim", "robot", "none"]),
        DeclareLaunchArgument("namespace", default_value=""),
        OpaqueFunction(function=_setup),
    ])
