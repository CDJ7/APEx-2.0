"""APEx software stack: (simulated cell) + PLC bridge + orchestrator.

    # with Gazebo (start sim.launch.py first, or use demo.launch.py):
    ros2 launch apex_bringup apex.launch.py
    # without Gazebo/Nav2 - kinematic robot stand-in, checks the ROS wiring anywhere:
    ros2 launch apex_bringup apex.launch.py nav_backend:=kinematic use_sim_time:=false
    # lab router, real FlexBase PLC (never on the school network):
    ros2 launch apex_bringup apex.launch.py network:=lab cell_sim:=false use_sim_time:=false

The bridge refuses ``network:=school`` with a real PLC; the cell simulator is only for
``backend sim``. Safety is never simulated around: apex_cell_sim reports
``Safety_Simulated = true`` and the station shows it.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from apex_bringup.paths import config_file


def _setup(context, *args, **kwargs):
    network = LaunchConfiguration("network").perform(context)
    use_sim_time = LaunchConfiguration("use_sim_time").perform(context) == "true"
    nav_backend = LaunchConfiguration("nav_backend").perform(context)
    obstacles = [o for o in LaunchConfiguration("kinematic_obstacles").perform(context).split(",") if o]
    cell_args = ["--config", str(config_file("cell_sim.yaml")), "--tags", str(config_file("plc_tags.yaml"))]
    if LaunchConfiguration("spawn_parts").perform(context) == "true":
        cell_args.append("--spawn-parts")
    tilt = LaunchConfiguration("tilt").perform(context)
    tilt_nodes = []
    if tilt == "sim":                  # simulated tray on this PC; on the robot run it there (backend:=serial)
        tilt_nodes.append(Node(package="apex_tilt", executable="tilt_platform", name="tilt_platform", output="screen",
                               parameters=[{"backend": "sim", "use_sim_time": use_sim_time,
                                            # the kinematic stand-in publishes no /odom
                                            "require_odom": nav_backend == "nav2"}]))
    tilt_backend = {"sim": "ros", "robot": "ros", "none": "none"}[tilt]
    return tilt_nodes + [
        Node(package="apex_cell_sim", executable="cell_sim", name="apex_cell_sim", output="screen",
             arguments=cell_args, condition=IfCondition(LaunchConfiguration("cell_sim"))),
        Node(package="apex_plc_bridge", executable="plc_bridge", name="apex_plc_bridge", output="screen",
             parameters=[{"network_profile": str(config_file(f"network_{network}.yaml")),
                          "tags_file": str(config_file("plc_tags.yaml")),
                          "backend": LaunchConfiguration("backend").perform(context),
                          "use_sim_time": use_sim_time}]),
        Node(package="apex_orchestrator", executable="orchestrator", name="apex_orchestrator", output="screen",
             parameters=[{"lab_file": str(config_file("lab.yaml")),
                          "job_config": str(config_file("job_timeouts.yaml")),
                          "nav_backend": nav_backend,
                          "robot_namespace": LaunchConfiguration("namespace").perform(context),
                          "kinematic_obstacles": obstacles or [""],
                          "tilt_backend": tilt_backend,
                          "use_sim_time": use_sim_time}]),
    ]


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument("network", default_value="school", choices=["school", "lab"]),
        DeclareLaunchArgument("backend", default_value="", description="'' = profile default | sim | logix"),
        DeclareLaunchArgument("cell_sim", default_value="true", choices=["true", "false"]),
        DeclareLaunchArgument("spawn_parts", default_value="false", choices=["true", "false"]),
        DeclareLaunchArgument("nav_backend", default_value="nav2", choices=["nav2", "kinematic"]),
        DeclareLaunchArgument("kinematic_obstacles", default_value="",
                              description="comma list for nav_backend:=kinematic, e.g. barrier_north"),
        DeclareLaunchArgument("use_sim_time", default_value="true", choices=["true", "false"]),
        DeclareLaunchArgument("namespace", default_value=""),
        DeclareLaunchArgument("tilt", default_value="sim", choices=["sim", "robot", "none"],
                              description="sim = simulated tilt tray on this PC | robot = tilt_platform runs on the "
                                          "TurtleBot | none = manual damaged-part return"),
        OpaqueFunction(function=_setup),
    ])
