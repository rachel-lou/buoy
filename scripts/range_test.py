import serial, time, threading

SENSOR   = "/dev/ttyUSB0"
RADIO    = "/dev/ttyUSB1"
BAUD     = 115200
INTERVAL = 5.0

latest = {"line": None}

def reader(s):
    while True:
        try:
            line = s.readline().decode("utf-8", errors="replace").strip()
            if line.startswith("Temperature:"):
                latest["line"] = line
        except Exception:
            pass

sensor = serial.Serial(SENSOR, BAUD, timeout=1)
radio  = serial.Serial(RADIO,  BAUD, timeout=1)
threading.Thread(target=reader, args=(sensor,), daemon=True).start()

seq = 0
start = time.monotonic()
print(f"Sending every {INTERVAL}s -- Ctrl-C to stop")
try:
    while True:
        time.sleep(INTERVAL)
        if latest["line"]:
            seq += 1
            elapsed = int(time.monotonic() - start)
            ts  = f"+{elapsed//60}m{elapsed%60}s"
            msg = f"BUOY1 #{seq} {ts} {latest['line']}\n"
            radio.write(msg.encode())
            radio.flush()
            print(f"TX: {msg.strip()}")
        else:
            print("waiting for sensor...")
except KeyboardInterrupt:
    print("Stopped.")
finally:
    sensor.close()
    radio.close()
