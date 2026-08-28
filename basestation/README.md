# Basestation

Dead-simple logger for a Raspberry Pi wired to a Meshtastic node over serial.

It reads whatever text the node emits (the buoy sends newline-terminated JSON
packet lines at 115200 baud — see `src/comms/`) and appends each received line,
verbatim with a receive timestamp, to a log file. A **fresh log file is created
each time the service starts** (i.e. on every boot).

That's the whole program. No parsing, no database, no transmitting.

## Files

- `basestation.py` — the logger.
- `basestation.service` — systemd unit so it runs on boot.
- `setup_pi.sh` — one-command provisioning script (installs deps, copies code, installs the service).

## Provision a Raspberry Pi from scratch (SD card → running logger)

### 1. Image the SD card

Use the [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your laptop:

1. Insert the SD card.
2. Choose OS: **Raspberry Pi OS Lite (64-bit)** — no desktop needed.
3. Click the gear / **Edit Settings** before writing and set:
   - **Hostname** (e.g. `basestation.local`)
   - **Enable SSH** → use password or your public key
   - **Username / password** (e.g. `kelp`)
   - **Wi-Fi** SSID + password (so you can SSH in), and locale/timezone
4. Write the image, then put the SD card in the Pi and power it on.

### 2. SSH in and get the code onto the Pi

```bash
ssh kelp@basestation.local
git clone <this-repo-url> buoy
cd buoy
```

(If the Pi has no network at the buoy site, clone on your laptop and copy it
over with `scp -r buoy kelp@basestation.local:~/`.)

### 3. Run the setup script

```bash
sudo bash basestation/setup_pi.sh
sudo systemctl enable --now basestation.service
```

That installs `pyserial`, copies `basestation.py` to `/opt/basestation`,
installs the systemd unit, and starts it — so it now runs automatically on
every boot.

### 4. Plug in the Meshtastic node and verify

Plug the node into USB, then:

```bash
ls /dev/ttyUSB* /dev/ttyACM*     # find the device
journalctl -u basestation -f     # watch it log received packets
```

If your node is `/dev/ttyACM0` (not the default `/dev/ttyUSB0`), edit
`BASESTATION_DEVICE` in `/etc/systemd/system/basestation.service` and run
`sudo systemctl daemon-reload && sudo systemctl restart basestation`.

## Run it by hand

```bash
python3 basestation.py --device /dev/ttyUSB0 --baud 115200 --log-dir /var/log/basestation
```

All three flags have defaults and can also be set via the `BASESTATION_DEVICE`,
`BASESTATION_BAUD`, and `BASESTATION_LOG_DIR` environment variables.

Find your Meshtastic node's serial device with `ls /dev/ttyUSB* /dev/ttyACM*`.
A USB-connected node is usually `/dev/ttyUSB0` or `/dev/ttyACM0`.

## Install as a boot service

```bash
sudo mkdir -p /opt/basestation
sudo cp basestation.py /opt/basestation/
sudo cp basestation.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now basestation
```

Check it: `journalctl -u basestation -f`

Logs land in `/var/log/basestation/basestation_YYYYMMDD_HHMMSS.log`.

## Requirements

Python 3 with `pyserial` (`pip install pyserial`, or `sudo apt install python3-serial`).
