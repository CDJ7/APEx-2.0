#!/usr/bin/env python3
"""Copy a mapping bag without the robot's own SLAM output (map -> odom on /tf).

    strip_slam_tf.py <bag_in> <bag_out>

A run's bag records /tf including the map -> odom correction that SLAM published on the robot.
To build the map again on the laptop, that transform has to go, or it would fight the new SLAM.
Everything else (/scan, /odom, odom -> base_link, /tf_static, /imu) is copied unchanged.
Used by rebuild_map.sh (APEx; not from TourBot).
"""
import sys

import rosbag2_py
from rclpy.serialization import deserialize_message, serialize_message
from tf2_msgs.msg import TFMessage


def strip(src: str, dst: str, storage: str = "mcap") -> int:
    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=src, storage_id=storage),
                rosbag2_py.ConverterOptions("cdr", "cdr"))
    writer = rosbag2_py.SequentialWriter()
    writer.open(rosbag2_py.StorageOptions(uri=dst, storage_id=storage),
                rosbag2_py.ConverterOptions("cdr", "cdr"))
    for i, t in enumerate(reader.get_all_topics_and_types()):
        writer.create_topic(rosbag2_py.TopicMetadata(
            id=i, name=t.name, type=t.type, serialization_format="cdr",
            offered_qos_profiles=t.offered_qos_profiles))
    dropped = 0
    while reader.has_next():
        topic, data, stamp = reader.read_next()
        if topic == "/tf":
            msg = deserialize_message(data, TFMessage)
            keep = [tf for tf in msg.transforms if tf.child_frame_id != "odom"]
            if len(keep) != len(msg.transforms):
                dropped += 1
                if not keep:
                    continue
                msg.transforms = keep
                data = serialize_message(msg)
        writer.write(topic, data, stamp)
    return dropped


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(f"removed the robot's map -> odom from {strip(sys.argv[1], sys.argv[2])} /tf messages")
