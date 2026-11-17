"""Gazebo Harmonic + TurtleBot 4 + localization + Nav2 in the APEx lab world.

    ros2 launch apex_bringup sim.launch.py                         # empty lab
    ros2 launch apex_bringup sim.launch.py world:=apex_lab_blocked_north
    ros2 launch apex_bringup sim.launch.py world:=apex_lab_obstacles rviz:=true

Why not ``turtlebot4_gz_bringup turtlebot4_gz.launch.py world:=...``? Its sim.launch.py
*overwrites* GZ_SIM_RESOURCE_PATH (so it cannot find our worlds) and the ROS-Gazebo bridge
uses the ``world`` argument as the Gazebo world NAME. So we start Gazebo ourselves with the
same resource/GUI paths plus ours, and include ``turtlebot4_spawn.launch.py`` with
``world:=apex_lab`` (the name inside every generated SDF). The robot starts docked at the
``dock`` pose from lab.yaml. Localization uses our generated map; Nav2 uses TurtleBot 4's
nav2.yaml with config/nav2_overrides.yaml merged on top.
"""

import math
import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, GroupAction, IncludeLaunchDescription, OpaqueFunction,
                            SetEnvironmentVariable, TimerAction)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

from apex_bringup.lab_model import LabModel
from apex_bringup.nav2_params import merged_params

ARGS = [
    DeclareLaunchArgument("world", default_value="apex_lab",
                          description="apex_lab | apex_lab_obstacles | apex_lab_blocked_north | apex_lab_blocked_south"),
    DeclareLaunchArgument("rviz", default_value="false", choices=["true", "false"]),
    DeclareLaunchArgument("nav2", default_value="true", choices=["true", "false"]),
    DeclareLaunchArgument("localization", default_value="true", choices=["true", "false"]),
    DeclareLaunchArgument("model", default_value="standard", choices=["standard", "lite"]),
    DeclareLaunchArgument("namespace", default_value=""),
    DeclareLaunchArgument("headless", default_value="false", choices=["true", "false"],
                          description="true = no Gazebo GUI (server only)"),
]


def _setup(context, *args, **kwargs):
    share = Path(get_package_share_directory("apex_bringup"))
    tb4_gz = get_package_share_directory("turtlebot4_gz_bringup")
    tb4_nav = get_package_share_directory("turtlebot4_navigation")
    create_gz = get_package_share_directory("irobot_create_gz_bringup")
    tb4_desc = get_package_share_directory("turtlebot4_description")
    create_desc = get_package_share_directory("irobot_create_description")
    tb4_gui = get_package_share_directory("turtlebot4_gz_gui_plugins")
    create_plugins = get_package_share_directory("irobot_create_gz_plugins")
    ros_gz_sim = get_package_share_directory("ros_gz_sim")

    lab = LabModel.load(share / "config" / "lab.yaml")
    world_file = share / "worlds" / f"{LaunchConfiguration('world').perform(context)}.sdf"
    if not world_file.exists():
        raise FileNotFoundError(f"{world_file} - run `ros2 run apex_bringup generate_lab` and rebuild")
    model = LaunchConfiguration("model").perform(context)
    headless = LaunchConfiguration("headless").perform(context) == "true"
    dock = lab.poses["dock"]

    env = [
        SetEnvironmentVariable("GZ_SIM_RESOURCE_PATH", ":".join([
            str(share / "worlds"), os.path.join(tb4_gz, "worlds"), os.path.join(create_gz, "worlds"),
            str(Path(tb4_desc).parent.resolve()), str(Path(create_desc).parent.resolve())])),
        SetEnvironmentVariable("GZ_GUI_PLUGIN_PATH", ":".join([
            os.path.join(tb4_gui, "lib"), os.path.join(create_plugins, "lib")])),
    ]
    gz_args = f"{world_file} -r -v 3"
    gz_args += " -s" if headless else f" --gui-config {os.path.join(tb4_gz, 'gui', model, 'gui.config')}"
    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(ros_gz_sim, "launch", "gz_sim.launch.py")),
        launch_arguments=[("gz_args", gz_args)])

    spawn = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(tb4_gz, "launch", "turtlebot4_spawn.launch.py")),
        launch_arguments=[("world", lab.world_name), ("namespace", LaunchConfiguration("namespace")),
                          ("rviz", LaunchConfiguration("rviz")), ("model", model),
                          ("x", str(dock.x)), ("y", str(dock.y)), ("z", "0.0"),
                          ("yaw", str(dock.yaw)),
                          ("localization", "false"), ("slam", "false"), ("nav2", "false")])

    localization = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(tb4_nav, "launch", "localization.launch.py")),
        launch_arguments=[("use_sim_time", "true"), ("namespace", LaunchConfiguration("namespace")),
                          ("map", str(share / "maps" / "apex_lab.yaml"))],
        condition=IfCondition(LaunchConfiguration("localization")))

    nav2_params = merged_params(os.path.join(tb4_nav, "config", "nav2.yaml"), share / "config" / "nav2_overrides.yaml")
    nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(tb4_nav, "launch", "nav2.launch.py")),
        launch_arguments=[("use_sim_time", "true"), ("namespace", LaunchConfiguration("namespace")),
                          ("params_file", str(nav2_params))],
        condition=IfCondition(LaunchConfiguration("nav2")))

    # localization + Nav2 after the robot exists (same idea as turtlebot4_spawn's own delays)
    return env + [gazebo, spawn, TimerAction(period=8.0, actions=[GroupAction([localization])]),
                  TimerAction(period=12.0, actions=[GroupAction([nav2])])]


def generate_launch_description() -> LaunchDescription:
    ld = LaunchDescription(ARGS)
    ld.add_action(OpaqueFunction(function=_setup))
    return ld
