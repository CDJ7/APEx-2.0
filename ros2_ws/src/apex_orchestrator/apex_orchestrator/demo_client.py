"""ROS 2 demo client: drives the three Simulation Bring-up demos through the real services.

    ros2 run apex_orchestrator demo_client --ros-args -p scenario:=full      # (a) full order
    ros2 run apex_orchestrator demo_client --ros-args -p scenario:=reroute   # (b) blocked corridor
    ros2 run apex_orchestrator demo_client --ros-args -p scenario:=return    # (c) damaged-part return

It plays the station worker: submits an order, confirms the receipt when the robot arrives
(reporting damaged parts in the ``return`` scenario), confirms the return, and exits with
code 0 when the robot is back on the dock, 1 on a fault or timeout. For ``reroute`` launch
the blocked world (``apex_bringup sim.launch.py world:=apex_lab_blocked_north``).
"""

from __future__ import annotations

import json
import time

import rclpy
from rclpy.node import Node

from apex_interfaces.msg import JobEvent, JobStatus, LineReceipt, OrderLine
from apex_interfaces.srv import ConfirmReceipt, ConfirmReturn, SubmitJob

SCENARIOS = {
    "full": {"destination": "station_a", "lines": [("P-1001", 10, 2), ("P-1002", 11, 1)], "damaged": 0},
    "reroute": {"destination": "station_a", "lines": [("P-1001", 10, 1)], "damaged": 0},
    "return": {"destination": "station_b", "lines": [("P-1003", 12, 3)], "damaged": 1},
}


class DemoClient(Node):
    def __init__(self) -> None:
        super().__init__("apex_demo_client")
        self.scenario = self.declare_parameter("scenario", "full").value
        self.timeout_s = float(self.declare_parameter("timeout_s", 1200.0).value)
        self.cfg = SCENARIOS[self.scenario]
        self.status: JobStatus | None = None
        self.events: list[str] = []
        self.job_id = 0
        self.done_flag = False
        self.result = 1
        self.create_subscription(JobStatus, "/apex/job_status", self._on_status, 10)
        self.create_subscription(JobEvent, "/apex/job_events", self._on_event, 50)
        self.submit = self.create_client(SubmitJob, "/apex/submit_job")
        self.receipt = self.create_client(ConfirmReceipt, "/apex/confirm_receipt")
        self.ret = self.create_client(ConfirmReturn, "/apex/confirm_return")
        self._answered: set[str] = set()

    def _on_status(self, msg: JobStatus) -> None:
        self.status = msg

    def _on_event(self, msg: JobEvent) -> None:
        line = f"{msg.kind:<20} job={msg.job_id} {msg.data_json}"
        self.events.append(line)
        self.get_logger().info(line)
        if msg.kind == "fault":
            self.done_flag, self.result = True, 1
        if msg.kind == "docked" and self.job_id and any(e.startswith("job_done") for e in self.events):
            self.done_flag, self.result = True, 0

    def call(self, client, req, wait_s: float = 10.0):
        if not client.wait_for_service(timeout_sec=wait_s):
            raise RuntimeError(f"service {client.srv_name} not available")
        fut = client.call_async(req)
        rclpy.spin_until_future_complete(self, fut, timeout_sec=wait_s)
        return fut.result()

    def run(self) -> int:
        t_end = time.monotonic() + self.timeout_s
        while rclpy.ok() and (self.status is None or self.status.step == "STARTING"):
            rclpy.spin_once(self, timeout_sec=0.5)
            if time.monotonic() > t_end:
                return 1
        req = SubmitJob.Request(order_id=1, destination=self.cfg["destination"],
                                lines=[OrderLine(part_number=p, part_code=c, quantity=q)
                                       for p, c, q in self.cfg["lines"]])
        res = self.call(self.submit, req)
        if not res or not res.accepted:
            self.get_logger().error(f"job rejected: {res.message if res else 'no reply'}")
            return 1
        self.job_id = res.job_id
        self.get_logger().info(f"scenario {self.scenario}: job {self.job_id} submitted")
        while rclpy.ok() and not self.done_flag and time.monotonic() < t_end:
            rclpy.spin_once(self, timeout_sec=0.2)
            self._worker()
        self.get_logger().info(f"scenario {self.scenario}: {'PASS' if self.result == 0 else 'FAIL'}")
        return self.result

    def _worker(self) -> None:
        st = self.status
        if st is None or st.job_id != self.job_id:
            return
        if st.step == "AWAIT_RECEIPT" and "receipt" not in self._answered:
            self._answered.add("receipt")
            dmg = self.cfg["damaged"]
            lines = []
            for i, (_, _, q) in enumerate(self.cfg["lines"]):
                d = min(dmg, q) if i == 0 else 0
                lines.append(LineReceipt(ok=q - d, damaged=d, missing=0))
            r = self.call(self.receipt, ConfirmReceipt.Request(job_id=self.job_id, lines=lines))
            self.get_logger().info(f"receipt -> {r.message if r else 'no reply'}")
        elif st.step == "AWAIT_RETURN_CONFIRM" and "return" not in self._answered:
            self._answered.add("return")
            r = self.call(self.ret, ConfirmReturn.Request(job_id=self.job_id, count=self.cfg["damaged"]))
            self.get_logger().info(f"return -> {r.message if r else 'no reply'}")


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = DemoClient()
    code = node.run()
    node.destroy_node()
    rclpy.shutdown()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
