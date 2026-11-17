"""One-command Simulation Bring-up demos in Gazebo.

    ros2 launch apex_bringup demo.launch.py scenario:=full       # (a) full order end to end
    ros2 launch apex_bringup demo.launch.py scenario:=reroute    # (b) north corridor blocked
    ros2 launch apex_bringup demo.launch.py scenario:=return     # (c) damaged-part return trip
    ros2 launch apex_bringup demo.launch.py scenario:=full gazebo:=false   # no Gazebo/Nav2 (kinematic)

Starts sim.launch.py (unless gazebo:=false), apex.launch.py and, after ``client_delay`` seconds,
the demo client that plays the station worker. Watch /apex/job_events or the client's log.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

WORLDS = {"full": "apex_lab", "reroute": "apex_lab_blocked_north", "return": "apex_lab"}
KINEMATIC_OBSTACLES = {"reroute": "barrier_north"}


def _setup(context, *args, **kwargs):
    launch_dir = os.path.join(get_package_share_directory("apex_bringup"), "launch")
    scenario = LaunchConfiguration("scenario").perform(context)
    gazebo = LaunchConfiguration("gazebo").perform(context) == "true"
    delay = float(LaunchConfiguration("client_delay").perform(context)) if gazebo else 3.0
    actions = []
    if gazebo:
        actions.append(IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(launch_dir, "sim.launch.py")),
            launch_arguments=[("world", WORLDS[scenario]), ("rviz", LaunchConfiguration("rviz"))]))
    actions.append(IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(launch_dir, "apex.launch.py")),
        launch_arguments=[("nav_backend", "nav2" if gazebo else "kinematic"),
                          ("use_sim_time", "true" if gazebo else "false"),
                          ("spawn_parts", "true" if gazebo else "false"),
                          ("kinematic_obstacles", "" if gazebo else KINEMATIC_OBSTACLES.get(scenario, ""))]))
    actions.append(TimerAction(period=delay, actions=[
        Node(package="apex_orchestrator", executable="demo_client", name="apex_demo_client", output="screen",
             parameters=[{"scenario": scenario, "use_sim_time": False}])]))
    return actions


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument("scenario", default_value="full", choices=["full", "reroute", "return"]),
        DeclareLaunchArgument("gazebo", default_value="true", choices=["true", "false"]),
        DeclareLaunchArgument("rviz", default_value="false", choices=["true", "false"]),
        DeclareLaunchArgument("client_delay", default_value="25.0",
                              description="seconds to let Gazebo, localization and Nav2 come up"),
        OpaqueFunction(function=_setup),
    ])
