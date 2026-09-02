#!/usr/bin/env bash
# Redeploy this checked-out repo to the running buoy service.
#
# Run this ON THE PI, from the repo root, after `git pull`. Unlike
# setup_pi.sh (full provisioning: apt packages, raspi-config, systemd unit),
# this only syncs src/ (and optionally config.yaml) into the locations the
# service actually runs from, then restarts it. `systemctl restart` alone
# does NOT pick up a git pull -- /opt/buoy/src is a copy, not a symlink.
#
# Usage:
#   sudo bash scripts/deploy_pi.sh                # code only
#   sudo bash scripts/deploy_pi.sh --with-config  # also overwrite /etc/buoy/config.yaml
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo "must run as root" >&2
    exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[deploy] copying src/ to /opt/buoy/src"
cp -a "$SRC_DIR/src/." /opt/buoy/src/

if [[ "${1:-}" == "--with-config" ]]; then
    echo "[deploy] copying config/config.yaml to /etc/buoy/config.yaml (overwrites any live edits there)"
    cp "$SRC_DIR/config/config.yaml" /etc/buoy/config.yaml
fi

echo "[deploy] restarting buoy.service"
systemctl restart buoy.service

echo "[deploy] done. Tailing logs (Ctrl-C stops watching; the service keeps running):"
journalctl -u buoy -f
