"""Keepout zone servers for Nav2's KeepoutFilter (task M4): the filter mask + its filter-info message.

    ros2 launch apex_bringup keepout.launch.py                          # generated lab-frame mask
    ros2 launch apex_bringup keepout.launch.py mask:=<room>_keepout.yaml   # mask from align_map

Normally included by sim.launch.py / robot.launch.py with ``keepout:=true``, which also merge
config/nav2_keepout.yaml into the Nav2 parameters so both costmaps use the filter.

Nodes (nav2_map_server, Nav2 jazzy parameter names):
  filter_mask_server          map_server: yaml_filename, topic_name /keepout_filter_mask, frame_id map
  costmap_filter_info_server  type 0 (keepout), filter_info_topic /costmap_filter_info,
                              mask_topic /keepout_filter_mask, base 0.0, multiplier 1.0
  lifecycle_manager_keepout   autostart both
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.descriptions import ParameterValue

MASK_TOPIC = "/keepout_filter_mask"
INFO_TOPIC = "/costmap_filter_info"


def generate_launch_description() -> LaunchDescription:
    share = get_package_share_directory("apex_bringup")
    use_sim_time = ParameterValue(LaunchConfiguration("use_sim_time"), value_type=bool)
    return LaunchDescription([
        DeclareLaunchArgument("mask", default_value=os.path.join(share, "maps", "apex_lab_keepout.yaml"),
                              description="keepout mask yaml, in the SAME frame as Nav2's map"),
        DeclareLaunchArgument("use_sim_time", default_value="false", choices=["true", "false"]),
        Node(package="nav2_map_server", executable="map_server", name="filter_mask_server", output="screen",
             parameters=[{"yaml_filename": LaunchConfiguration("mask"), "topic_name": MASK_TOPIC,
                          "frame_id": "map", "use_sim_time": use_sim_time}]),
        Node(package="nav2_map_server", executable="costmap_filter_info_server", name="costmap_filter_info_server",
             output="screen",
             parameters=[{"type": 0, "filter_info_topic": INFO_TOPIC, "mask_topic": MASK_TOPIC,
                          "base": 0.0, "multiplier": 1.0, "use_sim_time": use_sim_time}]),
        Node(package="nav2_lifecycle_manager", executable="lifecycle_manager", name="lifecycle_manager_keepout",
             output="screen",
             parameters=[{"autostart": True, "node_names": ["filter_mask_server", "costmap_filter_info_server"],
                          "use_sim_time": use_sim_time}]),
    ])
