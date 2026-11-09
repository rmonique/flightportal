"""Log the MatrixPortal's serial output to daily files, with timestamps.

Survives board reboots and unplugging. Also writes a warning line if the
board goes quiet for too long, since a hang prints nothing on its own.

Usage: uv run --with pyserial python tools/serial_logger.py <log_dir>
"""
import glob
import os
import sys
import time

import serial

LOG_DIR = sys.argv[1] if len(sys.argv) > 1 else "logs"
# The board prints at least every 30s normally, or every <=10 min while backing off
SILENCE_WARNING_SECONDS = 15 * 60

os.makedirs(LOG_DIR, exist_ok=True)


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def write(line):
    path = os.path.join(LOG_DIR, time.strftime("flightportal-%Y-%m-%d.log"))
    with open(path, "a") as f:
        f.write(f"{stamp()} {line}\n")


write("[logger] started")
while True:
    ports = glob.glob("/dev/cu.usbmodem*")
    if not ports:
        time.sleep(5)
        continue
    try:
        s = serial.Serial(ports[0], 115200, timeout=1)
        write(f"[logger] connected to {ports[0]}")
        buf = ""
        last_output = time.monotonic()
        warned = False
        while True:
            d = s.read(1024)
            if d:
                last_output = time.monotonic()
                if warned:
                    write("[logger] board output resumed")
                    warned = False
                buf += d.decode("utf-8", "replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    write(line.rstrip())
            elif not warned and time.monotonic() - last_output > SILENCE_WARNING_SECONDS:
                write(f"[logger] WARNING: no output from board for {SILENCE_WARNING_SECONDS // 60} minutes")
                warned = True
    except (serial.SerialException, OSError) as e:
        write(f"[logger] disconnected: {e}")
        time.sleep(5)
