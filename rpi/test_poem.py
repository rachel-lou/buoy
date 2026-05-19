#!/usr/bin/env python3
"""
Loops through Sea Fever by John Masefield, sending each line to the ESP32
over serial so it gets relayed out over the Meshtastic mesh.

Usage:
    python test_poem.py [--port /dev/ttyUSB0] [--baud 115200] [--delay 5]
"""

import argparse
import logging
import time

from serial_driver import MeshtasticSerial

POEM = [
    "I must go down to the seas again,",
    "to the lonely sea and the sky,",
    "And all I ask is a tall ship",
    "and a star to steer her by;",
    "And the wheel's kick and the wind's song",
    "and the white sail's shaking,",
    "And a grey mist on the sea's face,",
    "and a grey dawn breaking.",
    "I must go down to the seas again,",
    "for the call of the running tide",
    "Is a wild call and a clear call",
    "that may not be denied;",
    "And all I ask is a windy day",
    "with the white clouds flying,",
    "And the flung spray and the blown spume,",
    "and the sea-gulls crying.",
    "I must go down to the seas again,",
    "to the vagrant gypsy life,",
    "To the gull's way and the whale's way",
    "where the wind's like a whetted knife;",
    "And all I ask is a merry yarn",
    "from a laughing fellow-rover,",
    "And quiet sleep and a sweet dream",
    "when the long trick's over.",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Send Sea Fever over Meshtastic serial")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="Serial port")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="Seconds between lines (default: 5)",
    )
    parser.add_argument("--loops", type=int, default=0, help="Times to loop (0 = forever)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger(__name__)

    loop_count = 0
    with MeshtasticSerial(args.port, baud_rate=args.baud) as mesh:
        while True:
            loop_count += 1
            log.info("--- Loop %d ---", loop_count)
            for line in POEM:
                log.info("Sending: %r", line)
                mesh.send(line)
                time.sleep(args.delay)

            if args.loops and loop_count >= args.loops:
                break


if __name__ == "__main__":
    main()
