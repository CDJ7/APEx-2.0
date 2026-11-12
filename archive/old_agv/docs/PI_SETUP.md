# Raspberry Pi 5 Setup — from a blank SD card to a running AGV

Target: **Raspberry Pi 5**, **Raspberry Pi OS (64-bit) Bookworm**, user `apex`,
hostname `apex-agv`, static IP **192.168.1.40**. Commands are run on the Pi
over SSH unless noted otherwise.

> Bookworm/Pi 5 gotchas this guide avoids:
> * the legacy `picamera` library and `raspistill` do **not** exist — use
>   **Picamera2** and the `rpicam-*` apps;
> * `RPi.GPIO` does **not** work on the Pi 5 — use `gpiozero` with the `lgpio` backend;
> * `pip install` into the system Python is blocked (PEP 668) — use a venv,
>   created with `--system-site-packages` so the apt-installed Picamera2 is visible;
> * networking is managed by **NetworkManager** (`nmcli`), not `dhcpcd.conf`.

---

## 1. Flash the SD card (on your laptop)

1. Install **Raspberry Pi Imager** (raspberrypi.com/software).
2. *Device*: Raspberry Pi 5 · *OS*: Raspberry Pi OS (64-bit) · *Storage*: your card (≥ 32 GB, A2 class).
3. Click **Next → Edit settings** (OS customisation):
   * **General**: hostname `apex-agv`; username `apex` + a strong password;
     Wi-Fi SSID/password of the team router (NETGEAR N600 DGND3700v2; use its
     5 GHz network if coverage allows); **Wireless LAN country** (e.g. US); locale/timezone.
   * **Services**: ✅ Enable SSH (password, or better: paste your public key).
4. Write, insert the card, power the Pi with a **5 V / 5 A USB-C supply**.

```bash
# from the laptop (same network)
ssh apex@apex-agv.local
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

## 2. Static IP (NetworkManager)

```bash
nmcli connection show                      # the Imager Wi-Fi profile is usually "preconfigured"
CON="preconfigured"
sudo nmcli connection modify "$CON" \
    ipv4.method manual \
    ipv4.addresses 192.168.1.40/24 \
    ipv4.gateway 192.168.1.1 \
    ipv4.dns "192.168.1.1" \
    802-11-wireless.powersave 2            # disable Wi-Fi power saving (lower command latency)
sudo nmcli connection up "$CON"            # your SSH session will drop; reconnect to the new IP
ssh apex@192.168.1.40
ip -4 addr show wlan0                      # verify 192.168.1.40
ping -c3 192.168.1.10                      # main station
```

Use the router's gateway address if it is not `192.168.1.1` **[TBD: confirm in
the DGND3700v2 admin page]**. Reserve 192.168.1.10/.20/.30/.40 (and .50 for the
flexbase phone) in the router's DHCP settings so nothing else takes them.

## 3. Camera Module 3

Connect with the **15-to-22-pin Standard-Mini camera cable** (Pi 5 CSI is the
22-pin "mini" connector) to `CAM/DISP 0`, contacts
facing the right way, with the Pi powered off. Bookworm auto-detects the
camera (`camera_auto_detect=1` in `/boot/firmware/config.txt`).

```bash
rpicam-hello --list-cameras                # should list "imx708" (Camera Module 3)
rpicam-hello -t 5000                       # 5 s preview (needs a display; over SSH it just runs)
rpicam-still -o ~/test.jpg                 # copy to the laptop with scp to check focus/exposure
```

## 4. System packages

```bash
sudo apt install -y git python3-venv python3-pip \
    python3-picamera2 python3-libcamera python3-opencv python3-numpy python3-scipy \
    python3-yaml python3-serial python3-lgpio python3-gpiozero
# optional: build/flash the Arduino from the Pi
# curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
```

## 5. Code + Python virtual environment

```bash
cd ~
git clone https://github.com/CDJ7/Senior-Design.git
cd ~/Senior-Design/apex-agv
python3 -m venv --system-site-packages .venv      # sees apt's picamera2/opencv/numpy/scipy
source .venv/bin/activate
pip install -r agv_pi/requirements.txt
python -c "import picamera2, cv2, numpy, scipy, serial, yaml, gpiozero; print('imports OK')"
```

## 6. Permissions and udev rules

```bash
sudo usermod -aG dialout,video,gpio apex          # serial ports, camera, GPIO (log out/in afterwards)
sudo cp agv_pi/udev/99-apex-agv.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# plug in the RPLIDAR (CP2102 adapter) and the Arduino, then:
ls -l /dev/rplidar /dev/arduino
```

If a symlink is missing, check the IDs with
`udevadm info -a -n /dev/ttyUSB0 | grep -E "idVendor|idProduct"` (and
`/dev/ttyACM0` for the Arduino) and adjust the rule.

## 7. Configure

Edit `~/Senior-Design/apex-agv/config.yaml`:

* `network.*.host` — the four static IPs;
* `agv.robot.*` — measured wheel diameter, wheel base, encoder ticks/rev (see `docs/BRINGUP.md`);
* `agv.map_yaml` / `agv.routes_yaml` — after mapping (step 10);
* secrets: do **not** commit real ones — put them in `/etc/default/apex-agv`:

```bash
sudo tee /etc/default/apex-agv >/dev/null <<'EOF'
APEX_AGV_TOKEN=change-me-long-random-string
EOF
sudo chmod 600 /etc/default/apex-agv
```

(The station must use the same `APEX_AGV_TOKEN`.)

## 8. Flash the Arduino

From the Arduino IDE on a laptop (see `agv_arduino/README.md`) or on the Pi:

```bash
arduino-cli core install arduino:renesas_uno
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi agv_arduino
arduino-cli upload  --fqbn arduino:renesas_uno:unor4wifi -p /dev/arduino agv_arduino
```

## 9. Smoke tests (wheels off the ground!)

Activate the venv first (`source ~/Senior-Design/apex-agv/.venv/bin/activate`, `cd ~/Senior-Design/apex-agv`).

| Subsystem | Command | Pass criteria |
|---|---|---|
| Arduino link | `python -m agv_pi.tools.arduino_console` → `PING`, `stat`, `hb on`, `CMD,80,80`, `STOP` | `PONG`; STAT streams at 20 Hz; wheels turn **forward**; `enc_left/right` increase |
| Watchdog | in the console: `hb off`, `CMD,80,80` | motors stop ≤ 0.5 s, `fault` = 1; `CLR` clears it |
| E-stop | press the button while `CMD,80,80` | motors stop instantly, `estop`=1, fault bit 2; release + `CLR` |
| IR sensors | console `stat`, move a hand 10–80 cm in front | `ir1`/`ir2` rise as the hand gets closer (analog) |
| LiDAR | `python -m agv_pi.tools.lidar_check` | health `Good`, 5.5–10 Hz, an object on the robot's **left** shows in the *left* sector |
| Camera / vision | `python -m agv_pi.tools.camera_check --save roi.jpg` | ≥ 15 FPS, motion detected when waving; ROI in `roi.jpg` covers the floor ahead |
| Full controller | `python -m agv_pi.main` (foreground) | log shows `AGV server listening`, no repeated faults |
| From the laptop | `python -m tests.fake_laptop_client agv` (station running) | AGV `online`, state `IDLE` |

## 10. Map the area and define routes

```bash
sudo systemctl stop apex-agv 2>/dev/null
python -m agv_pi.lidar.mapping_tool --name shop_floor     # drive slowly with w/a/s/d, q = save
# copy agv_pi/maps/shop_floor.{png,yaml} to a laptop with a display (or use VNC), then:
python -m agv_pi.lidar.route_editor --map agv_pi/maps/shop_floor.yaml
# place MAIN (home, plc_id 1) and the workstations (plc_id 2, 3, ...), waypoints, edges; 's' to save
```

Copy `shop_floor_routes.yaml` back to the Pi, set `agv.map_yaml` /
`agv.routes_yaml` in `config.yaml` on **both** the Pi and the station PC
(the station reads station names from it), park the AGV at `MAIN` facing the
configured heading, and start the service.

## 11. Run at boot (systemd)

```bash
sudo cp agv_pi/systemd/apex-agv.service /etc/systemd/system/
# adjust User / WorkingDirectory / ExecStart in the unit if your paths differ
sudo systemctl daemon-reload
sudo systemctl enable --now apex-agv
systemctl status apex-agv
journalctl -u apex-agv -f                          # live log
tail -f ~/Senior-Design/apex-agv/logs/agv_pi.log   # structured log file (rotated, 5 x 5 MB)
```

The service restarts automatically on failure; on `systemctl stop` the
controller sends `STOP` to the Arduino before exiting (and the Arduino's
watchdog stops the motors anyway within 0.5 s if the Pi dies).

## 12. Final checklist

- [ ] Static IP 192.168.1.40, Wi-Fi power save off, station pingable
- [ ] `/dev/rplidar` and `/dev/arduino` exist after a reboot
- [ ] Camera listed by `rpicam-hello --list-cameras`
- [ ] All smoke tests in §9 pass
- [ ] Map + routes saved, paths set in `config.yaml` (Pi **and** station)
- [ ] Secrets changed from the `apex-dev-*` defaults on Pi and station
- [ ] `systemctl is-enabled apex-agv` → enabled; survives a reboot
- [ ] AGV parked at MAIN facing the home heading before power-up
