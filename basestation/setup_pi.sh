#!/usr/bin/env bash
# Provision a Raspberry Pi as a Meshtastic basestation logger.
# Run as root, from this repo's root, after the Pi is booted and this repo is
# on it:  sudo bash basestation/setup_pi.sh
#
# Mirrors scripts/setup_pi.sh but far simpler: the basestation only needs
# Python + pyserial and a systemd unit. The Meshtastic node is on USB
# (/dev/ttyUSB0 or /dev/ttyACM0), so no SPI/I2C/UART/watchdog config is needed.
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo "must run as root" >&2
    exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[setup] installing apt packages"
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-serial \
    git ca-certificates

echo "[setup] deploying application"
install -d -m 0755 /opt/basestation
install -d -m 0755 /var/log/basestation
install -m 0755 "$SRC_DIR/basestation.py" /opt/basestation/basestation.py

echo "[setup] installing systemd unit"
install -m 0644 "$SRC_DIR/basestation.service" /etc/systemd/system/basestation.service
systemctl daemon-reload

echo "[setup] done. Enable the service with: systemctl enable --now basestation.service"
echo "[setup] tip: confirm your node's serial device with: ls /dev/ttyUSB* /dev/ttyACM*"
