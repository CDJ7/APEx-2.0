"""Real TurtleBot 4 on the lab router: localization + Nav2 (on the station laptop) + the APEx stack.

    # 1. once: record a map of the real lab (station GUI Drive & Map, or apex_mapping/tools/map_lab.sh)
    # 2. once: align it with lab.yaml (writes room101_lab.yaml in the lab frame)
    ros2 run apex_bringup align_map --map ~/apex_maps/room101.yaml --pairs pairs.yaml
    # 3. every day
    ros2 launch apex_bringup robot.launch.py map:=$HOME/apex_maps/room101_lab.yaml keepout:=true
    #    (unaligned map: map:=room101.yaml keepout:=true keepout_mask:=room101_keepout.yaml)
    #    ... cell:=sim to use the cell simulator until the PLC is connected (default),
    #        cell:=plc for the real FlexBase PLC (network_lab.yaml address), tilt:=none (default)
    #        while the tilt tray is being built - damaged parts use the manual return - and
    #        tilt:=robot once tilt_platform runs on the robot.

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
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

from apex_bringup.nav2_params import merged_params


def _setup(context, *args, **kwargs):
    share = get_package_share_directory("apex_bringup")
    tb4_nav = get_package_share_directory("turtlebot4_navigation")
    ns = LaunchConfiguration("namespace")
    cell = LaunchConfiguration("cell").perform(context)
    keepout = LaunchConfiguration("keepout").perform(context) == "true"
    overrides = [os.path.join(share, "config", "nav2_overrides.yaml")]
    if keepout:
        overrides.append(os.path.join(share, "config", "nav2_keepout.yaml"))
    nav2_params = merged_params(os.path.join(tb4_nav, "config", "nav2.yaml"), overrides)
    keepout_servers = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(share, "launch", "keepout.launch.py")),
        launch_arguments=[("use_sim_time", "false"), ("mask", LaunchConfiguration("keepout_mask"))],
        condition=IfCondition(LaunchConfiguration("keepout")))
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
    return [localization, TimerAction(period=4.0, actions=[keepout_servers]), TimerAction(period=5.0, actions=[nav2]),
            TimerAction(period=10.0, actions=[apex])]


def generate_launch_description() -> LaunchDescription:
    share = get_package_share_directory("apex_bringup")
    return LaunchDescription([
        DeclareLaunchArgument("map", default_value=os.path.join(share, "maps", "apex_lab.yaml"),
                              description="map yaml - use the SLAM map of the real lab"),
        DeclareLaunchArgument("cell", default_value="sim", choices=["sim", "plc"],
                              description="sim = apex_cell_sim until the PLC is connected; plc = real FlexBase"),
        DeclareLaunchArgument("tilt", default_value="none", choices=["sim", "robot", "none"],
                              description="none = no tilt tray yet (manual damaged-part return) | robot = "
                                          "tilt_platform runs on the TurtleBot | sim = simulated tray on this PC"),
        DeclareLaunchArgument("keepout", default_value="false", choices=["true", "false"],
                              description="Nav2 keepout zone - only with a map aligned to lab.yaml (align_map), "
                                          "or with keepout_mask:= the <map>_keepout.yaml align_map wrote"),
        DeclareLaunchArgument("keepout_mask", default_value=os.path.join(share, "maps", "apex_lab_keepout.yaml"),
                              description="keepout mask in the same frame as map:="),
        DeclareLaunchArgument("namespace", default_value=""),
        OpaqueFunction(function=_setup),
    ])
