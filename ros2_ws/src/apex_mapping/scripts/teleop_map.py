#!/usr/bin/env python3
"""Map an AEB floor on the real TurtleBot 4 by driving it with the TurtleBot 4 controller.

Runs on the robot. Usually started from the laptop with tools/map_floor.sh, which runs it
inside tmux on the robot (so a Wi-Fi drop doesn't end the run) and copies the result back.

  1. asks which floor (or --floor N)
  2. checks the battery, the controller, the dock and the lidar, and starts the lidar
     motor (the TurtleBot's power saver stops it on the dock)
  3. starts SLAM Toolbox and records a raw bag of /scan /odom /tf /tf_static /imu
  4. you drive with the controller: hold L1 and use the left stick (R1 for faster)
  5. hold OPTIONS for 2 s (or press Ctrl-C) to stop: the map, SLAM pose graph and bag are
     saved to ~/maps/level<N>/<YYYYmmdd_HHMM>/ and the controller rumbles and the light
     ring flashes green

No Nav2 runs, so the Raspberry Pi only carries its own nodes, SLAM and the recorder. If
SLAM fails, the bag is still recorded; build the map from it later on the laptop.
"""

import argparse
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque

import numpy as np
import rclpy
from irobot_create_msgs.msg import DockStatus, LedColor, LightringLeds
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, QoSProfile, ReliabilityPolicy,
                       qos_profile_sensor_data)
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import BatteryState, Joy, JoyFeedback, JoyFeedbackArray, LaserScan
from slam_toolbox.srv import SerializePoseGraph
from std_msgs.msg import String
from std_srvs.srv import Empty

FLOORS = ('1', '2', '3')
BAG_TOPICS = ['/scan', '/odom', '/tf', '/tf_static', '/imu']
JOY_DEVICE = '/dev/input/js0'
# The TurtleBot 4 controller (DualShock 4 layout) on joy_linux: 4 = L1 (drive), 5 = R1
# (turbo), 8 = SHARE, 9 = OPTIONS
DEFAULT_SAVE_BUTTON = 9
MIN_SCAN_HZ = 5.0       # the RPLIDAR A1 runs at about 7.6 Hz
LOW_BATTERY = 0.20


def find_slam_params():
    """The team's SLAM settings from tourbot_sim, or None for the TurtleBot 4 defaults."""
    try:
        from ament_index_python.packages import get_package_share_directory
        path = os.path.join(get_package_share_directory('tourbot_sim'), 'config', 'slam.yaml')
        if os.path.exists(path):
            return path
    except Exception:
        pass
    here = os.path.dirname(os.path.realpath(__file__))
    path = os.path.join(here, '..', '..', 'tourbot_sim', 'config', 'slam.yaml')
    return os.path.realpath(path) if os.path.exists(path) else None


def save_map_files(grid, path):
    """Write an OccupancyGrid as <path>.pgm + <path>.yaml, the same format as map_saver_cli
    (trinary, thresholds 0.65 / 0.25), so Nav2's map_server and RViz load it directly."""
    info = grid.info
    data = np.array(grid.data, dtype=np.int16).reshape(info.height, info.width)
    img = np.full(data.shape, 205, dtype=np.uint8)       # unknown
    img[(data >= 0) & (data <= 25)] = 254                # free
    img[data >= 65] = 0                                  # occupied
    img = np.flipud(img)                                 # image row 0 is the map's top (max y)
    with open(path + '.pgm', 'wb') as f:
        f.write(f'P5\n{info.width} {info.height}\n255\n'.encode())
        f.write(img.tobytes())
    q = info.origin.orientation
    yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
    with open(path + '.yaml', 'w') as f:
        f.write(f'image: {os.path.basename(path)}.pgm\n'
                f'mode: trinary\n'
                f'resolution: {info.resolution:.3f}\n'
                f'origin: [{info.origin.position.x:.3f}, {info.origin.position.y:.3f}, '
                f'{yaw:.3f}]\n'
                f'negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n')


def dir_size_mb(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / 1e6


class Abort(Exception):
    """Ctrl-C before recording started: nothing to save."""


class TeleopMapper(Node):

    def __init__(self, save_button):
        super().__init__('teleop_map')
        self.save_button = save_button
        self.lock = threading.Lock()
        self.battery = None
        self.docked = None
        self.scan_times = deque(maxlen=50)
        self.scan_count = 0
        self.map = None
        self.joy_buttons = []
        self.last_joy = 0.0
        self.last_odom = 0.0

        # /battery_state and /map are published rarely but latched (transient local), so
        # ask for the last value instead of waiting for the next one.
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(BatteryState, '/battery_state', self.on_battery, latched)
        self.create_subscription(DockStatus, '/dock_status', self.on_dock,
                                 qos_profile_sensor_data)
        self.create_subscription(LaserScan, '/scan', self.on_scan, qos_profile_sensor_data)
        self.create_subscription(Joy, '/joy', self.on_joy, 10)
        self.create_subscription(Odometry, '/odom', self.on_odom, qos_profile_sensor_data)
        self.create_subscription(OccupancyGrid, '/map', self.on_map, latched)

        self.lightring = self.create_publisher(LightringLeds, '/cmd_lightring', 10)
        self.display = self.create_publisher(String, '/hmi/display/message', 10)
        self.rumble_pub = self.create_publisher(JoyFeedbackArray, '/joy/set_feedback', 10)
        self.start_motor_cli = self.create_client(Empty, '/start_motor')
        self.serialize_cli = self.create_client(SerializePoseGraph,
                                                '/slam_toolbox/serialize_map')

    # Callbacks run on the executor thread
    def on_battery(self, msg):
        self.battery = msg.percentage

    def on_dock(self, msg):
        self.docked = msg.is_docked

    def on_scan(self, msg):
        with self.lock:
            self.scan_times.append(time.monotonic())
            self.scan_count += 1

    def on_joy(self, msg):
        self.joy_buttons = list(msg.buttons)
        self.last_joy = time.monotonic()

    def on_odom(self, msg):
        self.last_odom = time.monotonic()

    def on_map(self, msg):
        self.map = msg

    # Queries from the main thread
    def scan_hz(self, window=3.0):
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.scan_times if now - t <= window]
        return len(recent) / window

    def save_held(self):
        b = self.joy_buttons
        return len(b) > self.save_button and b[self.save_button] == 1

    def call(self, client, request, timeout):
        if not client.wait_for_service(timeout_sec=min(timeout, 5.0)):
            return None
        future = client.call_async(request)
        end = time.monotonic() + timeout
        while not future.done() and time.monotonic() < end:
            time.sleep(0.05)
        return future.result() if future.done() else None

    def start_motor(self):
        return self.call(self.start_motor_cli, Empty.Request(), 5.0) is not None

    def show(self, text):
        self.display.publish(String(data=text))

    def rumble(self, seconds=0.6):
        on = JoyFeedbackArray(array=[JoyFeedback(type=JoyFeedback.TYPE_RUMBLE, id=0,
                                                 intensity=1.0)])
        off = JoyFeedbackArray(array=[JoyFeedback(type=JoyFeedback.TYPE_RUMBLE, id=0,
                                                  intensity=0.0)])
        self.rumble_pub.publish(on)
        time.sleep(seconds)
        self.rumble_pub.publish(off)

    def flash(self, r, g, b, seconds=2.0):
        msg = LightringLeds(override_system=True,
                            leds=[LedColor(red=r, green=g, blue=b) for _ in range(6)])
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            msg.header.stamp = self.get_clock().now().to_msg()
            self.lightring.publish(msg)
            time.sleep(0.1)
        self.lightring.publish(LightringLeds(override_system=False))


class Run:

    def __init__(self, args):
        self.args = args
        self.stop_reason = None
        self.log_file = None
        self.slam = None
        self.bag = None
        self.recording = False
        self.status_shown = False

    # Output: events go to the terminal and run.log; the live status line only to the terminal
    def log(self, text):
        if self.status_shown:
            sys.stdout.write('\r\x1b[K')
            self.status_shown = False
        print(text, flush=True)
        if self.log_file:
            self.log_file.write(f'{time.strftime("%H:%M:%S")} {text}\n')
            self.log_file.flush()

    def status(self, text):
        sys.stdout.write('\r\x1b[K' + text)
        sys.stdout.flush()
        self.status_shown = True

    def on_signal(self, signum, frame):
        if signum == signal.SIGHUP:
            return   # the SSH session dropped; keep mapping
        if self.stop_reason is None:
            self.stop_reason = 'Ctrl-C'
        else:
            self.log('Ctrl-C again: stopping without waiting for the save to finish')
            self.kill_children(signal.SIGKILL)
            os._exit(130)

    def wait(self, seconds):
        """Sleep that wakes up on Ctrl-C; raises Abort before recording starts."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.stop_reason and not self.recording:
                raise Abort()
            time.sleep(0.1)

    def spawn(self, cmd, log_name):
        # Own session, so the terminal's Ctrl-C reaches only this program, which then
        # saves the map before stopping SLAM and the recorder itself.
        out = open(os.path.join(self.run_dir, log_name), 'w')
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)

    @staticmethod
    def stop_process(proc, timeout):
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGINT)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def kill_children(self, sig):
        for proc in (self.bag, self.slam):
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    pass

    def choose_floor(self):
        floor = self.args.floor
        while floor not in FLOORS:
            try:
                floor = input('Which floor are you mapping? [1/2/3]: ').strip()
            except EOFError:
                raise Abort()
        return floor

    def make_run_dir(self, floor):
        base = os.path.join(os.path.expanduser(self.args.maps_dir), f'level{floor}')
        stamp = time.strftime('%Y%m%d_%H%M')
        run_dir, n = os.path.join(base, stamp), 2
        while os.path.exists(run_dir):
            run_dir, n = os.path.join(base, f'{stamp}_{n}'), n + 1
        os.makedirs(run_dir)
        return run_dir

    def preflight(self, node):
        self.log('=== Checking robot ===')
        self.wait(3.0)   # let the first messages arrive

        # /odom streams at 20 Hz whenever the base is up. If it's silent, nothing from the
        # robot's own nodes is getting through, which is what happens when the network
        # changes after boot (e.g. switch_to_bluewifi.sh): ROS still talks on the old
        # addresses until it restarts.
        if time.monotonic() - node.last_odom > 2.0:
            self.wait(5.0)
        if time.monotonic() - node.last_odom > 2.0:
            raise RuntimeError('No data from the robot base (/odom is silent). If the Wi-Fi '
                               'or Ethernet changed since the robot booted, restart ROS with: '
                               'sudo systemctl restart turtlebot4  (then wait ~1 min)')
        self.log('[OK]   Robot base: /odom streaming')

        if node.battery is None:
            self.log('[WARN] Battery: no data yet')
        else:
            pct = round(node.battery * 100)
            tag = '[WARN]' if node.battery < LOW_BATTERY else '[OK]  '
            self.log(f'{tag} Battery: {pct}%' + (' (low, charge soon)' if tag == '[WARN]' else ''))

        if not os.path.exists(JOY_DEVICE):
            self.log('[WAIT] Controller: not connected. Press the PS button on the controller.')
            while not os.path.exists(JOY_DEVICE):
                self.wait(0.5)
        self.log('[OK]   Controller: connected')

        if node.docked:
            self.log('[WAIT] Robot is on the dock. Hold the controller HOME button for 3 s '
                     'to undock (or lift it off the dock).')
            while node.docked:
                self.wait(0.5)
            self.wait(2.0)
        self.log('[OK]   Off the dock')

        for attempt in range(3):
            node.start_motor()
            end = time.monotonic() + 10.0
            while time.monotonic() < end and node.scan_hz() < MIN_SCAN_HZ:
                self.wait(0.5)
            if node.scan_hz() >= MIN_SCAN_HZ:
                break
            self.log(f'[WAIT] Lidar: no scans yet (try {attempt + 1}/3), restarting motor')
        hz = node.scan_hz()
        if hz < MIN_SCAN_HZ:
            raise RuntimeError(f'Lidar is not publishing /scan ({hz:.1f} Hz). Check the '
                               'lidar cable, or reboot the robot, and try again.')
        self.log(f'[OK]   Lidar: spinning, /scan {hz:.1f} Hz')

    def start_slam(self, node):
        cmd = ['ros2', 'launch', 'turtlebot4_navigation', 'slam.launch.py', 'sync:=false']
        params = find_slam_params()
        if params:
            cmd.append(f'params:={params}')
        self.log(f'Starting SLAM ({"tourbot_sim/config/slam.yaml" if params else "defaults"})'
                 ' - keep the robot still...')
        self.slam = self.spawn(cmd, 'slam.log')
        end = time.monotonic() + 60.0
        while node.map is None and time.monotonic() < end:
            if self.slam.poll() is not None:
                break
            self.wait(0.5)
        if node.map is None:
            self.log('[WARN] SLAM did not publish a map (see slam.log). Recording the raw '
                     'bag anyway; build the map from it on the laptop.')
            return False
        self.log('[OK]   SLAM: running')
        return True

    def start_bag(self):
        bag_dir = os.path.join(self.run_dir, 'bag')
        self.bag = self.spawn(['ros2', 'bag', 'record', '--disable-keyboard-controls',
                               '-o', bag_dir] + BAG_TOPICS, 'bag.log')
        time.sleep(3.0)
        if self.bag.poll() is not None:
            raise RuntimeError('ros2 bag record exited right away (see bag.log)')
        self.recording = True

    def drive(self, node, floor):
        self.log('')
        self.log('=== RECORDING ===  Drive: hold L1 + left stick (R1 = faster).')
        self.log(f'Stop and save: hold OPTIONS for {self.args.hold:.0f} s (or Ctrl-C).')
        node.show(f'Mapping L{floor}: REC')
        threading.Thread(target=node.rumble, args=(0.3,), daemon=True).start()
        t0 = time.monotonic()
        held_since = None
        last_motor_kick = 0.0
        slam_warned = False
        bag_dir = os.path.join(self.run_dir, 'bag')
        while self.stop_reason is None:
            now = time.monotonic()
            if node.save_held():
                held_since = held_since or now
                if now - held_since >= self.args.hold:
                    self.stop_reason = 'OPTIONS button'
                    break
            else:
                held_since = None

            if self.bag.poll() is not None:
                self.log('[FAIL] The recorder stopped on its own (see bag.log). Saving what '
                         'there is.')
                self.stop_reason = 'recorder stopped'
                break
            if self.slam and self.slam.poll() is not None and not slam_warned:
                self.log('[WARN] SLAM stopped (see slam.log). Still recording the raw bag.')
                slam_warned = True

            hz = node.scan_hz()
            if hz < MIN_SCAN_HZ and now - t0 > 5.0 and now - last_motor_kick > 10.0:
                self.log(f'[WARN] Lidar dropped to {hz:.1f} Hz, restarting the motor')
                node.start_motor()
                last_motor_kick = now

            elapsed = int(now - t0)
            parts = [f'REC {elapsed // 60:02d}:{elapsed % 60:02d}', f'lidar {hz:.1f} Hz']
            if node.battery is not None:
                parts.append(f'battery {round(node.battery * 100)}%')
            if node.map is not None:
                i = node.map.info
                parts.append(f'map {i.width * i.resolution:.0f}x{i.height * i.resolution:.0f} m')
            parts.append(f'bag {dir_size_mb(bag_dir):.0f} MB')
            if held_since:
                parts.append(f'SAVING in {self.args.hold - (now - held_since):.1f}s, keep holding')
            else:
                parts.append('hold OPTIONS to save')
            self.status(' | '.join(parts))
            time.sleep(0.2)
        return time.monotonic() - t0

    def save(self, node, floor, duration, slam_ok):
        self.log(f'Stopping ({self.stop_reason}). Saving...')
        node.show('Saving map...')
        map_path = os.path.join(self.run_dir, 'map')
        map_saved = graph_saved = False
        if node.map is not None:
            try:
                save_map_files(node.map, map_path)
                map_saved = True
                self.log('[OK]   Map: map.pgm + map.yaml')
            except Exception as e:
                self.log(f'[FAIL] Map: {e}')
            if self.slam and self.slam.poll() is None:
                if node.call(node.serialize_cli,
                             SerializePoseGraph.Request(filename=map_path), 30.0) is not None:
                    graph_saved = True
                    self.log('[OK]   Pose graph: map.posegraph + map.data')
                else:
                    self.log('[WARN] Pose graph: not saved')
        else:
            self.log('[WARN] No SLAM map to save; the raw bag has everything to build one')

        self.stop_process(self.bag, 20)
        self.recording = False
        self.stop_process(self.slam, 15)
        bag_ok = os.path.exists(os.path.join(self.run_dir, 'bag', 'metadata.yaml'))
        self.log(f'[{"OK]  " if bag_ok else "FAIL]"} Bag: bag/ ({dir_size_mb(os.path.join(self.run_dir, "bag")):.0f} MB)')

        info = node.map.info if node.map is not None else None
        with open(os.path.join(self.run_dir, 'run.yaml'), 'w') as f:
            f.write(f'floor: {floor}\n'
                    f'started: "{self.started}"\n'
                    f'duration_s: {duration:.0f}\n'
                    f'stopped_by: "{self.stop_reason}"\n'
                    f'scans: {node.scan_count}\n'
                    f'slam_ok: {str(slam_ok).lower()}\n'
                    f'map_saved: {str(map_saved).lower()}\n'
                    f'posegraph_saved: {str(graph_saved).lower()}\n'
                    f'bag_ok: {str(bag_ok).lower()}\n')
            if info:
                f.write(f'map_size_m: [{info.width * info.resolution:.1f}, '
                        f'{info.height * info.resolution:.1f}]\n')
        with open(os.path.join(os.path.expanduser(self.args.maps_dir), '.last_run'), 'w') as f:
            f.write(self.run_dir + '\n')

        node.show('Map saved')
        threading.Thread(target=node.rumble, args=(0.8,), daemon=True).start()
        node.flash(0, 255, 0)
        self.log(f'=== Saved run to {self.run_dir} ({duration / 60:.1f} min) ===')
        return map_saved or bag_ok

    def main(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, self.on_signal)
        if shutil.which('ros2') is None:
            print('ros2 not found: source /etc/turtlebot4/setup.bash first', file=sys.stderr)
            return 1

        try:
            floor = self.choose_floor()
        except (Abort, KeyboardInterrupt):
            return 130

        rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        node = TeleopMapper(self.args.save_button)
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        threading.Thread(target=executor.spin, daemon=True).start()

        self.run_dir = self.make_run_dir(floor)
        self.log_file = open(os.path.join(self.run_dir, 'run.log'), 'w')
        self.started = time.strftime('%Y-%m-%d %H:%M:%S')
        self.log(f'TourBot teleop mapping: AEB Level {floor} -> {self.run_dir}')
        ok = False
        try:
            self.preflight(node)
            slam_ok = self.start_slam(node)
            self.start_bag()
            duration = self.drive(node, floor)
            ok = self.save(node, floor, duration, slam_ok)
        except Abort:
            self.log('Cancelled before recording started; nothing saved.')
            self.kill_children(signal.SIGINT)
            self.log_file.close()
            self.log_file = None
            shutil.rmtree(self.run_dir, ignore_errors=True)
        except Exception as e:
            self.log(f'[FAIL] {e}')
            if self.recording:
                self.stop_reason = self.stop_reason or 'error'
                self.save(node, floor, 0.0, False)
            self.kill_children(signal.SIGINT)
            if not self.recording and not os.path.exists(
                    os.path.join(self.run_dir, 'run.yaml')):
                self.log_file.close()   # nothing recorded: don't leave an empty run behind
                self.log_file = None
                shutil.rmtree(self.run_dir, ignore_errors=True)
        finally:
            executor.shutdown(timeout_sec=1.0)
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
        return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--floor', choices=FLOORS, help='AEB floor (asks if left out)')
    parser.add_argument('--maps-dir', default='~/maps',
                        help='Runs are saved to <maps-dir>/level<N>/<date_time>/')
    parser.add_argument('--save-button', type=int, default=DEFAULT_SAVE_BUTTON,
                        help='Joy button index that stops and saves (9 = OPTIONS)')
    parser.add_argument('--hold', type=float, default=2.0,
                        help='Seconds to hold the save button')
    return Run(parser.parse_args()).main()


if __name__ == '__main__':
    sys.exit(main())
