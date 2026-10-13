"""Doc log ESP32 ma khong chu dong bat DTR/RTS."""

from __future__ import annotations

import argparse
import time

import serial


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM11")
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    device = serial.Serial()
    device.port = args.port
    device.baudrate = 115200
    device.timeout = 0.2
    device.dtr = False
    device.rts = False
    device.open()
    if args.reset:
        device.rts = True
        time.sleep(0.15)
        device.rts = False
        time.sleep(0.2)
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            line = device.readline()
            if line:
                text = line.decode("utf-8", "backslashreplace").rstrip()
                print(text.encode("ascii", "backslashreplace").decode(), flush=True)
    finally:
        device.close()


if __name__ == "__main__":
    main()
