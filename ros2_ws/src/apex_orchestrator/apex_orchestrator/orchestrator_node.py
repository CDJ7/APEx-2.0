"""ROS 2 node ``apex_orchestrator``: runs the job state machine at 10 Hz.

Inputs / outputs
    /apex/plc/tags          (sub, std_msgs/String JSON)    PLC tags from apex_plc_bridge
    /apex/plc/write_tag     (client, apex_interfaces/WriteTag)  the ONLY way this node writes the PLC
    /apex/job_status        (pub, JobStatus, 2 Hz)
    /apex/job_events        (pub, JobEvent)                 every state-machine event
    /apex/submit_job, /apex/confirm_receipt, /apex/confirm_return,
    /apex/resolve_shortfall, /apex/operator                 (services, apex_interfaces)

Navigation backends (parameter ``nav_backend``)
    nav2       TurtleBot 4 + Nav2 (Gazebo or the real robot) through :class:`Nav2Adapter`
    kinematic  the headless stand-in (no Gazebo, no Nav2) - checks the ROS wiring anywhere

Job IDs start above the PLC's last ``Job_ID``/``Job_Ack`` so a restarted orchestrator can
never be "acknowledged" by a stale Job_Ack from before the restart.
"""

from __future__ import annotations

import json
import time
from typing import Any

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from apex_interfaces.msg import JobEvent, JobStatus
from apex_interfaces.srv import ConfirmReceipt, ConfirmReturn, Operator, ResolveShortfall, SubmitJob, WriteTag
from apex_bringup.lab_model import LabModel
from apex_bringup.paths import config_file
from apex_orchestrator.config import load_job_config
from apex_orchestrator.job_machine import Job, JobMachine, OrderLine
from apex_orchestrator.router import LoopRouter
from apex_orchestrator.sim_nav import KinematicNav, barriers_from_lab


class RosPlcPort:
    """PlcPort over the bridge: cached tag reads, asynchronous allow-listed writes."""

    def __init__(self, node: Node, stale_s: float = 1.5):
        self.node = node
        self.stale_s = stale_s
        self.tags: dict[str, Any] = {}
        self.connected = False
        self.last_rx = 0.0
        node.create_subscription(String, "/apex/plc/tags", self._on_tags, 10)
        self.cli = node.create_client(WriteTag, "/apex/plc/write_tag")

    def _on_tags(self, msg: String) -> None:
        data = json.loads(msg.data)
        self.connected = bool(data.get("connected"))
        if self.connected:
            self.tags = data.get("tags", {})
            self.last_rx = time.monotonic()

    def have_tags(self) -> bool:
        return bool(self.tags)

    def read_all(self) -> dict[str, Any]:
        if not self.connected or time.monotonic() - self.last_rx > self.stale_s:
            raise ConnectionError("no fresh PLC data from apex_plc_bridge")
        return dict(self.tags)

    def write(self, name: str, value: Any) -> None:
        if not self.cli.service_is_ready():
            raise ConnectionError("apex_plc_bridge write service not available")
        req = WriteTag.Request(name=name, value_json=json.dumps(value))
        fut = self.cli.call_async(req)
        fut.add_done_callback(lambda f, n=name: self._done(n, f))

    def _done(self, name: str, fut) -> None:
        res = fut.result()
        if res is None or not res.ok:
            self.node.get_logger().error(f"PLC write {name} failed: {res.message if res else fut.exception()}")


class OrchestratorNode(Node):
    def __init__(self) -> None:
        super().__init__("apex_orchestrator")
        lab_file = self.declare_parameter("lab_file", str(config_file("lab.yaml"))).value
        job_file = self.declare_parameter("job_config", str(config_file("job_timeouts.yaml"))).value
        nav_backend = self.declare_parameter("nav_backend", "nav2").value
        namespace = self.declare_parameter("robot_namespace", "").value
        obstacles = list(self.declare_parameter("kinematic_obstacles", [""]).value)

        self.lab = LabModel.load(lab_file)
        timeouts, policy, router_cfg = load_job_config(job_file)
        poses = {n: (p.x, p.y, p.yaw) for n, p in self.lab.poses.items()}
        dock = poses["dock"]
        if nav_backend == "nav2":
            from apex_orchestrator.nav2_adapter import Nav2Adapter   # needs the TurtleBot 4 stack
            self.nav = Nav2Adapter(namespace, initial_pose=dock, logger=self.get_logger())
        elif nav_backend == "kinematic":
            self.nav = KinematicNav(*dock, barriers=barriers_from_lab(
                self.lab, [o for o in obstacles if o], self.lab.raw["corridor_width"]))
        else:
            raise ValueError(f"nav_backend must be nav2 or kinematic, not {nav_backend!r}")
        self.nav_backend = nav_backend
        self.plc = RosPlcPort(self)
        router = LoopRouter(list(self.lab.corners.values()), **router_cfg)
        self.machine = JobMachine(self.nav, self.plc, router, poses, timeouts, policy, on_event=self._on_event)
        self._next_id = 0
        self._started = False

        self.pub_status = self.create_publisher(JobStatus, "/apex/job_status", 10)
        self.pub_events = self.create_publisher(JobEvent, "/apex/job_events", 50)
        self.create_service(SubmitJob, "/apex/submit_job", self._srv_submit)
        self.create_service(ConfirmReceipt, "/apex/confirm_receipt", self._srv_receipt)
        self.create_service(ConfirmReturn, "/apex/confirm_return", self._srv_return)
        self.create_service(ResolveShortfall, "/apex/resolve_shortfall", self._srv_shortfall)
        self.create_service(Operator, "/apex/operator", self._srv_operator)
        self.create_timer(0.1, self._tick)
        self.create_timer(0.5, self._publish_status)
        self.get_logger().info(f"orchestrator up: nav_backend={nav_backend}, poses={sorted(poses)}")

    # ------------------------------------------------------------------ loop
    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _tick(self) -> None:
        now = self._now()
        if not self._started:
            # wait for the first PLC data (and Nav2) before the machine may judge heartbeats
            nav_ready = getattr(self.nav, "ready", lambda: True)()
            if not (self.plc.have_tags() and nav_ready):
                self.get_logger().info("waiting for PLC tags and Nav2...", throttle_duration_sec=5.0)
                return
            t = self.plc.tags
            self._next_id = max(int(t.get("Job_ID", 0)), int(t.get("Job_Ack", 0))) + 1
            self._started = True
            self.get_logger().info(f"ready; first job id {self._next_id}")
        if isinstance(self.nav, KinematicNav):
            self.nav.update(now)
        self.machine.tick(now)

    def _on_event(self, kind: str, job: Job | None, data: dict[str, Any]) -> None:
        msg = JobEvent(kind=kind, job_id=job.job_id if job else 0, data_json=json.dumps(data, default=str))
        msg.stamp = self.get_clock().now().to_msg()
        self.pub_events.publish(msg)

    def _publish_status(self) -> None:
        s = self.machine.snapshot()
        m = JobStatus()
        m.stamp = self.get_clock().now().to_msg()
        m.job_id = int(s["job_id"])
        m.step = s["step"] if self._started else "STARTING"
        m.step_age_s = float(s["since_s"])
        m.destination = s["destination"]
        m.route_direction = s["route"]
        m.parts_expected = int(s["expected"])
        m.parts_counted = int(s["counted"])
        m.queue = [int(q) for q in s["queue"]]
        m.fault_code, m.fault_text = s["fault_code"], s["fault_text"]
        x, y, yaw = self.nav.pose()
        m.robot_x, m.robot_y, m.robot_yaw = float(x), float(y), float(yaw)
        m.battery_pct = float(self.nav.battery_pct())
        m.docked = bool(self.nav.is_docked())
        m.route_xy = [float(v) for p in s["route_xy"] for v in p]
        m.blocked_xy = [float(v) for p in s["blocked_xy"] for v in p]
        self.pub_status.publish(m)

    # ------------------------------------------------------------------ services
    def _guard(self, res: Any, fn) -> Any:
        try:
            fn()
            res.ok, res.message = True, "ok"
        except (ValueError, RuntimeError, KeyError) as exc:
            res.ok, res.message = False, str(exc)
        return res

    def _srv_submit(self, req: SubmitJob.Request, res: SubmitJob.Response) -> SubmitJob.Response:
        if not self._started:
            res.accepted, res.message = False, "orchestrator not ready (waiting for PLC/Nav2)"
            return res
        job = Job(self._next_id, int(req.order_id), req.destination,
                  [OrderLine(ln.part_number, int(ln.part_code), int(ln.quantity)) for ln in req.lines])
        try:
            self.machine.submit(job)
            self._next_id += 1
            res.accepted, res.job_id, res.message = True, job.job_id, "queued"
        except ValueError as exc:
            res.accepted, res.message = False, str(exc)
        return res

    def _srv_receipt(self, req: ConfirmReceipt.Request, res: ConfirmReceipt.Response) -> ConfirmReceipt.Response:
        lines = [{"ok": r.ok, "damaged": r.damaged, "missing": r.missing} for r in req.lines]
        return self._guard(res, lambda: self.machine.confirm_receipt(int(req.job_id), lines))

    def _srv_return(self, req: ConfirmReturn.Request, res: ConfirmReturn.Response) -> ConfirmReturn.Response:
        return self._guard(res, lambda: self.machine.confirm_return(int(req.job_id), int(req.count)))

    def _srv_shortfall(self, req: ResolveShortfall.Request, res: ResolveShortfall.Response):
        return self._guard(res, lambda: self.machine.resolve_shortfall(int(req.job_id), req.action))

    def _srv_operator(self, req: Operator.Request, res: Operator.Response) -> Operator.Response:
        who = req.operator_name or "operator"
        actions = {
            "stop": lambda: self.machine.stop(who),
            "reset": lambda: self.machine.reset_fault(who),
            "resume": lambda: self.machine.resume(who),
            "cancel": lambda: self.machine.cancel(int(req.job_id)),
        }
        if req.action not in actions:
            res.ok, res.message = False, f"action must be one of {sorted(actions)}"
            return res
        return self._guard(res, actions[req.action])


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = OrchestratorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.machine.nav.cancel()
        if hasattr(node.nav, "shutdown"):
            node.nav.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
