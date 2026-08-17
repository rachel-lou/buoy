#!/usr/bin/env bash
# Provision a Raspberry Pi 3B+ for the ocean monitoring buoy.
# Run as root.
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo "must run as root" >&2
    exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[setup] installing apt packages"
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    python3-yaml python3-numpy \
    i2c-tools \
    git ca-certificates


echo "[setup] disabling the OS watchdog daemon if present (conflicts with buoy's own watchdog)"
systemctl disable --now watchdog 2>/dev/null || true

echo "[setup] enabling SPI, I2C, UART"
raspi-config nonint do_spi 0 || true
raspi-config nonint do_i2c 0 || true
raspi-config nonint do_serial 2 || true   # serial port enabled, console disabled

if ! grep -q "^dtparam=watchdog=on" /boot/config.txt 2>/dev/null; then
    echo "dtparam=watchdog=on" >> /boot/config.txt
fi
if ! grep -q "^enable_uart=1" /boot/config.txt 2>/dev/null; then
    echo "enable_uart=1" >> /boot/config.txt
fi

echo "[setup] installing python requirements"
pip3 install --break-system-packages -r "$SRC_DIR/requirements.txt"

echo "[setup] deploying application"
install -d -m 0755 /opt/buoy
install -d -m 0755 /opt/buoy/src
install -d -m 0755 /opt/buoy/staging
install -d -m 0755 /etc/buoy
install -d -m 0755 /data
install -d -m 0755 /var/log/buoy

cp -a "$SRC_DIR/src/." /opt/buoy/src/
install -m 0644 "$SRC_DIR/config/config.yaml" /etc/buoy/config.yaml

echo "[setup] installing systemd unit"
install -m 0644 "$SRC_DIR/systemd/buoy.service" /etc/systemd/system/buoy.service
systemctl daemon-reload

echo "[setup] done. Enable the service with: systemctl enable --now buoy.service"
