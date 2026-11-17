"""ROS 2 node ``apex_plc_bridge``: the ONLY process that talks to the FlexBase PLC.

* Polls every handshake tag (``APEX_ToStation`` + ``APEX_FromStation``) at ``poll_hz``.
* Publishes ``/apex/plc/tags`` (std_msgs/String, JSON of member -> value), ``/apex/cell_status``
  and ``/apex/arm_status`` (read-only views; safety state is *displayed*, never acted on here).
* Serves ``/apex/plc/write_tag`` (apex_interfaces/WriteTag). Every write goes through
  :class:`~apex_plc_bridge.client.PlcClient`, which refuses anything outside the
  ``APEX_FromStation`` allow-list and every safety-looking name - no exceptions, no override.
* Chooses the backend from the network profile; the school profile can never reach the real PLC.

Parameters:
    tags_file (str)         plc_tags.yaml (default: apex_bringup/config/plc_tags.yaml)
    network_profile (str)   network_school.yaml | network_lab.yaml (default: school)
    backend (str)           "" = profile default, "sim" or "logix" (logix only if the profile allows)
    poll_hz (float)         default 10
"""

from __future__ import annotations

import json
import time
from typing import Any

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from apex_interfaces.msg import ArmStatus, CellStatus
from apex_interfaces.srv import WriteTag
from apex_bringup.paths import config_file
from apex_plc_bridge.client import LogixBackend, PlcClient, SimBackend
from apex_plc_bridge.network import NetworkPolicyError, choose_backend, load_profile
from apex_plc_bridge.tags import TagSpec, TagWriteRefused

KUKA_MODES = {1: "T1", 2: "T2", 3: "AUT", 4: "AUT EXT"}


class PlcBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("apex_plc_bridge")
        tags_file = self.declare_parameter("tags_file", str(config_file("plc_tags.yaml"))).value
        profile_file = self.declare_parameter("network_profile", str(config_file("network_school.yaml"))).value
        requested = self.declare_parameter("backend", "").value
        poll_hz = float(self.declare_parameter("poll_hz", 10.0).value)

        self.spec = TagSpec.load(tags_file)
        profile = load_profile(profile_file)
        self.choice = choose_backend(profile, requested)          # raises NetworkPolicyError
        backend = (LogixBackend(self.choice.host, self.choice.timeout_s) if self.choice.kind == "logix"
                   else SimBackend(self.choice.host, self.choice.port, self.choice.timeout_s))
        self.client = PlcClient(self.spec, backend)
        self.get_logger().info(
            f"profile={profile.get('profile')} backend={self.choice.kind} host={self.choice.host} "
            f"writable={[t.name for t in self.spec.writable()]}")

        self.pub_tags = self.create_publisher(String, "/apex/plc/tags", 10)
        self.pub_cell = self.create_publisher(CellStatus, "/apex/cell_status", 10)
        self.pub_arm = self.create_publisher(ArmStatus, "/apex/arm_status", 10)
        self.create_service(WriteTag, "/apex/plc/write_tag", self._on_write)
        self._next_connect = 0.0
        self.create_timer(1.0 / poll_hz, self._poll)

    # ------------------------------------------------------------------ connection
    def _ensure_connected(self) -> bool:
        if self.client.connected:
            return True
        if time.monotonic() < self._next_connect:
            return False
        try:
            self.client.connect()
            self.get_logger().info("connected to PLC backend")
            return True
        except (OSError, ConnectionError) as exc:
            self._next_connect = time.monotonic() + 2.0
            self.get_logger().warning(f"PLC connect failed ({exc}); retrying in 2 s",
                                      throttle_duration_sec=10.0)
            return False

    # ------------------------------------------------------------------ polling
    def _poll(self) -> None:
        tags: dict[str, Any] = {}
        if self._ensure_connected():
            try:
                tags = self.client.read_all()
            except (OSError, ConnectionError) as exc:
                self.get_logger().error(f"PLC read failed: {exc}")
                self.client.connected = False
        self.pub_tags.publish(String(data=json.dumps({"connected": bool(tags), "tags": tags})))
        self._publish_status(tags)

    def _publish_status(self, t: dict[str, Any]) -> None:
        stamp = self.get_clock().now().to_msg()
        c = CellStatus()
        c.stamp = stamp
        c.connected = bool(t)
        c.backend = self.choice.kind
        if t:
            c.cell_mode = int(t.get("Cell_Mode", 0))
            c.safety_ok = bool(t.get("Safety_OK", False))
            c.safety_simulated = bool(t.get("Safety_Simulated", False))
            c.transfer_mode = int(t.get("Transfer_Mode", 0))
            c.chute_part_count = int(t.get("Chute_PartCount", 0))
            c.parts_placed = int(t.get("Parts_Placed", 0))
            c.load_complete = bool(t.get("Load_Complete", False))
            c.job_fault = bool(t.get("Job_Fault", False))
            c.fault_code = int(t.get("Fault_Code", 0))
            c.fault_text = self.spec.fault_text(c.fault_code)
            c.stack_red = bool(t.get("StackLight_Red", False))
            c.stack_amber = bool(t.get("StackLight_Amber", False))
            c.stack_green = bool(t.get("StackLight_Green", False))
        self.pub_cell.publish(c)
        a = ArmStatus()
        a.stamp = stamp
        if t:
            a.mode = int(t.get("KUKA_Mode", 0))
            a.mode_name = KUKA_MODES.get(a.mode, "UNKNOWN")
            a.ready = bool(t.get("KUKA_Ready", False))
            a.busy = bool(t.get("KUKA_Busy", False))
            a.fault = bool(t.get("KUKA_Fault", False))
            a.program_no = int(t.get("KUKA_ProgramNo", 0))
        self.pub_arm.publish(a)

    # ------------------------------------------------------------------ writes
    def _on_write(self, req: WriteTag.Request, res: WriteTag.Response) -> WriteTag.Response:
        try:
            value = json.loads(req.value_json)
            if not self._ensure_connected():
                raise ConnectionError("PLC not connected")
            self.client.write(req.name, value)
            res.ok, res.message = True, "written"
        except TagWriteRefused as exc:
            res.ok, res.message = False, f"REFUSED: {exc}"
        except (ValueError, OSError, ConnectionError) as exc:
            res.ok, res.message = False, str(exc)
            if isinstance(exc, (OSError, ConnectionError)):
                self.client.connected = False
        if not res.ok:
            self.get_logger().error(f"write {req.name}={req.value_json} failed: {res.message}")
        return res


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    try:
        node = PlcBridgeNode()
    except NetworkPolicyError as exc:
        print(f"apex_plc_bridge: {exc}")
        rclpy.shutdown()
        raise SystemExit(2)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.client.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
