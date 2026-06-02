#!/usr/bin/env python3
"""Parse the receiver's Meshtastic log and extract all DIRECT receptions of
buoy 0x04089c08 (=9c08).

Writes round2/direct_receptions.json — list of records each with:
  time      "HH:MM:SS"  UTC
  t_utc     int (seconds of day, UTC)
  t_pdt     int (seconds of day, PDT = UTC-7)
  id        packet id (hex)
  snr       float (dB)
  rssi      int (dBm)
  src       "lora_rx" — origin tag (only one kind kept here)
  seq, buoy_up_s, temp, press, deep — included where the packet was decoded
                                       as a BUOY1 text message

"direct" = the receiver's LoRa-RX line shows `relay=0x8` (last hop = the
originator 0x04089c08, whose low byte is 0x08). Relayed copies through other
mesh nodes (relay=0x3c, 0x27, etc.) are not included because their RSSI/SNR
describe the relay→RX hop, not the buoy→RX link.
"""
import re, json, os

ANSI = re.compile(rb'\x1b\[[\d;?]*[A-Za-z]')

LOG_PATH = 'buoy1_20260531_1049.txt'
OUT_PATH = 'round2/direct_receptions.json'

with open(LOG_PATH, 'rb') as f:
    raw = f.read()
text = ANSI.sub(b'', raw)
text = re.sub(rb'\x1b\([A-Z0-9]', b'', text)
text = re.sub(rb'\x1b[=>]', b'', text).decode('utf-8', errors='replace')

# Permissive matcher: tolerates line wraps in the minicom capture
re_direct = re.compile(
    r'(\d\d):(\d\d):(\d\d)\s+\d+\s+\[RadioIf\]\s+Lora RX \('
    r'id=(0x[0-9a-f]+)\s+fr=0x04089c08'
    r'[^)]*?rxSNR=(-?[\d.]+)\s+rxRSSI=(-?\d+)'
    r'[^)]*?relay=0x8\)'
)

re_text = re.compile(
    r'Received text msg from=0x4089c08,\s+id=(0x[0-9a-f]+),\s+'
    r'msg=BUOY(\d+)\s+#(\d+)\s+\+(\d+)m(\d+)s'
    r'.*?Temperature:(-?[\d.]+).*?Pressure:(-?[\d.]+).*?Deep:(-?[\d.]+)'
)

text_meta = {m[1]: dict(seq=int(m[3]),
                        buoy_up_s=int(m[4]) * 60 + int(m[5]),
                        temp=float(m[6]), press=float(m[7]), deep=float(m[8]))
             for m in re_text.finditer(text)}

direct = []
seen = set()
for m in re_direct.finditer(text):
    pid = m[4]
    if pid in seen:
        continue
    seen.add(pid)
    t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
    rec = dict(time=f"{m[1]}:{m[2]}:{m[3]}", t_utc=t, t_pdt=t - 7 * 3600,
               id=pid, snr=float(m[5]), rssi=int(m[6]), src='lora_rx')
    if pid in text_meta:
        rec.update(text_meta[pid])
    direct.append(rec)

direct.sort(key=lambda r: r['t_utc'])

# Cut off at the largest gap (the battery cutoff at ~11:13:04)
cutoff_t = None
for i in range(1, len(direct)):
    if direct[i]['t_utc'] - direct[i - 1]['t_utc'] > 300:
        cutoff_t = direct[i - 1]['t_utc']
        break
if cutoff_t:
    direct = [r for r in direct if r['t_utc'] <= cutoff_t]

print(f"Direct receptions of 9c08 in test session: {len(direct)}")
print(f"  first: {direct[0]['time']} UTC")
print(f"  last:  {direct[-1]['time']} UTC")
print(f"  with text-msg payload: {sum(1 for r in direct if 'seq' in r)}")
print(f"  non-text (position/nodeinfo/etc.): {sum(1 for r in direct if 'seq' not in r)}")

os.makedirs('round2', exist_ok=True)
json.dump(direct, open(OUT_PATH, 'w'), indent=1)
print(f"Wrote {OUT_PATH}")
