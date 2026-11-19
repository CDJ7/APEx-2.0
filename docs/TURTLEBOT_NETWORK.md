# TurtleBot 4 on the lab router

How the real TurtleBot 4 talks to the station laptop over Wi-Fi, how its clocks
are kept right, and what to do when it drops off the network. Set up and tested
2026-11-18 (robot image v2.0.2, ROS 2 Jazzy).

## Addresses

| Device | Address | How it connects |
|---|---|---|
| Router: Netgear DGND3700v2 | `192.168.0.1` | Admin page `http://192.168.0.1`. No internet uplink. |
| Station laptop | `192.168.0.2` (reserved) | USB-Ethernet adapter into a router **LAN** port. Internet stays on the school Wi-Fi. |
| TurtleBot 4 Raspberry Pi | `192.168.0.3` (reserved for `D8:3A:DD:08:5B:AC`) | Wi-Fi `NETGEAR61-5G` (5 GHz, ch 36). Fallback `NETGEAR61` (2.4 GHz). |
| Pi, wired port (recovery only) | `192.168.185.3` | Cable straight from the laptop into the robot. |
| Create 3 base | `192.168.186.2` | Internal USB link to the Pi only (`192.168.186.3`). |

The Wi-Fi password is kept on the robot and with the team, not in this repository.

## Daily use

1. Plug the laptop's adapter into a router LAN port **before** powering the robot on
   (the robot gets its time from the laptop at boot).
2. Power the robot on and wait for the chime. The top line of its screen shows `192.168.0.3`.
3. On the laptop:

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
ros2 topic list                     # /scan, /odom, /imu, /tf, /battery_state, ...
ssh ubuntu@192.168.0.3              # key login, no password
rsync -av my_pkg/ ubuntu@192.168.0.3:~/ros2_ws/src/my_pkg/
```

The lidar is **off while the robot is docked**, so `/scan` only publishes after undocking.

## Time sync

The robot has no internet and no battery-backed clock, so every cold boot starts at
the image's date (2024-10-28). ROS rejects data whose timestamps disagree, so this
breaks RViz, SLAM and Nav2 until fixed. The chain:

```
internet -> laptop (chrony, serves 192.168.0.0/24) -> Pi (chrony) -> Create 3 (ntpd)
```

| Where | Setting |
|---|---|
| Laptop | `/etc/chrony/conf.d/turtlebot.conf`: `allow 192.168.0.0/24`, `local stratum 8`. UDP 123 open. |
| Pi | `/etc/chrony/chrony.conf`: `server 192.168.0.2 iburst prefer` |
| Create 3 | ntp.conf (web UI on the Pi side, `http://192.168.186.2/beta-ntp-conf`) starts with `tinker panic 0`, so it accepts the two-year jump |
| Pi boot job | `ubuntu` crontab `@reboot ~/create3-timesync.sh`: waits for the Pi to sync from the laptop, then restarts the Create 3's ntpd so the base corrects at once |

Check that timestamps agree (difference should be 0-2 s):

```bash
ros2 topic echo /odom --once --field header.stamp.sec; date -u +%s
```

## Robot network settings

- `/etc/netplan/50-wifis.yaml`: `NETGEAR61-5G` (NetworkManager autoconnect priority 10)
  and `NETGEAR61` (priority 5). Backups of earlier versions are in `~ubuntu/*.bak*`.
- `/boot/firmware/cmdline.txt` has `cfg80211.ieee80211_regdom=US`. Without it the Pi boots
  with Wi-Fi country `00` and **cannot join 5 GHz at power-on**.
- A polkit rule (`/etc/polkit-1/rules.d/50-ubuntu-networkmanager.rules`) lets the `ubuntu`
  user run `nmcli` without sudo.
- ROS: `ROS_DOMAIN_ID=0`, `rmw_fastrtps_cpp`, simple discovery (`/etc/turtlebot4/setup.bash`).

## When the robot is not on Wi-Fi

The screen's top line shows `unknown` instead of `192.168.0.3`.

1. **Power-cycle the router** (off 10 s). After many failed attempts the DGND3700v2 stops
   answering the Pi on **both** bands; the Pi's log shows
   `CTRL-EVENT-ASSOC-REJECT ... status_code=16`. A router restart clears it.
2. Still nothing: plug the laptop's adapter **into the robot** and use
   `ssh ubuntu@192.168.185.3`. The laptop's wired profile works on both the router and
   the robot without changes. Then:

```bash
nmcli dev                                       # wlan0 state
journalctl -b -u wpa_supplicant | tail -20      # why it was rejected
nmcli con up netplan-wlan0-NETGEAR61-5G         # retry (no sudo needed)
```

## Full power-cycle

`sudo reboot` on the Pi does **not** restart the Create 3 base. To restart both:
lift the robot off the dock, hold the power button about 10 s until the light ring
goes out, then put it back on the dock.
