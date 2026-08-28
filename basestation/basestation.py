#!/usr/bin/env python3
"""Dead-simple Meshtastic basestation logger.

Reads whatever text the Meshtastic node emits over serial (newline-terminated
JSON packet lines, per the buoy radio protocol) and appends each line to a log
file. A fresh log file is created every time the process starts (on boot).

Nothing is parsed or interpreted -- each received line is written verbatim with
a receive timestamp so the raw stream is preserved exactly as it came in.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

import serial  # pyserial


def open_log(log_dir: str) -> str:
    """Create a fresh, uniquely named log file for this boot/run."""
    os.makedirs(log_dir, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(log_dir, f"basestation_{stamp}.log")
    # Touch it so the file exists even before the first packet arrives.
    with open(path, "a", encoding="utf-8"):
        pass
    return path


def run(device: str, baud: float, log_dir: str) -> None:
    log_path = open_log(log_dir)
    print(f"[basestation] logging to {log_path}", flush=True)

    ser = None
    while True:
        # (Re)open the serial port if needed -- survives the node being
        # unplugged/replugged without crashing the logger.
        if ser is None:
            try:
                ser = serial.Serial(port=device, baudrate=baud, timeout=1.0)
                print(f"[basestation] opened {device} @ {baud}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[basestation] cannot open {device}: {exc}", flush=True)
                time.sleep(2.0)
                continue

        try:
            raw = ser.readline()
        except Exception as exc:  # noqa: BLE001
            print(f"[basestation] read error: {exc}", flush=True)
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass
            ser = None
            time.sleep(2.0)
            continue

        if not raw:
            continue  # read timeout, nothing received

        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not text:
            continue

        stamp = dt.datetime.now().isoformat(timespec="seconds")
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}\t{text}\n")
            fh.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Meshtastic basestation serial logger")
    parser.add_argument(
        "--device",
        default=os.environ.get("BASESTATION_DEVICE", "/dev/ttyUSB0"),
        help="Serial device the Meshtastic node is on (default: /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=int(os.environ.get("BASESTATION_BAUD", "115200")),
        help="Serial baud rate (default: 115200)",
    )
    parser.add_argument(
        "--log-dir",
        default=os.environ.get("BASESTATION_LOG_DIR", "/var/log/basestation"),
        help="Directory to write log files into (default: /var/log/basestation)",
    )
    args = parser.parse_args(argv)

    try:
        run(args.device, args.baud, args.log_dir)
    except KeyboardInterrupt:
        print("\n[basestation] stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
