#!/usr/bin/env python3
"""Extract per-received-packet connectivity metrics from one or more basestation
(Meshtastic) logs into a CSV.

    parse_basestation_logs.py OUT.csv LOG1 [LOG2 ...]

Multiple logs (a service restart splits one deployment into several files) are
concatenated and de-duplicated by Meshtastic packet id, so an overlapping
restart window does not double-count packets.

Also prints a per-source-node packet count. That count is how you tell which
logs are the real remote deployment: at a remote site (e.g. Caspar Cove) the
basestation should hear essentially ONE node (the buoy). A log full of many
nodes was recorded near a populated mesh and is NOT the deployment.
"""
import re, sys, csv
from collections import Counter

if len(sys.argv) < 3:
    sys.exit(__doc__)

out = sys.argv[1]
logs = sys.argv[2:]

ansi = re.compile(r'\x1b\[[0-9;]*m')
rx = re.compile(r'\[RadioIf\] Lora RX \((?P<body>.*?)\)')

def kv(body, key, cast=str):
    m = re.search(rf'{key}=(?P<v>[-0-9a-fxA-F.]+)', body)
    if not m:
        return ''
    try:
        return cast(m.group('v'), 0) if cast is int else cast(m.group('v'))
    except (ValueError, TypeError):
        return m.group('v')

rows, seen, dupes = [], set(), 0
for log in logs:
    with open(log, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = ansi.sub('', line)
            ts = line.split('\t', 1)[0]
            m = rx.search(line)
            if not m:
                continue
            b = m.group('body')
            pid = re.search(r'id=(\S+)', b).group(1) if re.search(r'id=(\S+)', b) else ''
            if pid and pid in seen:
                dupes += 1
                continue
            if pid:
                seen.add(pid)
            hop_start = kv(b, 'hopStart', int)
            hop_lim = kv(b, 'HopLim', int)
            hops_used = (hop_start - hop_lim) if isinstance(hop_start, int) and isinstance(hop_lim, int) else ''
            rows.append({
                'timestamp': ts,
                'packet_id': pid,
                'from_node': kv(b, 'fr'),
                'to_node': re.search(r'to=(\S+?),', b).group(1) if re.search(r'to=(\S+?),', b) else '',
                'rx_snr': kv(b, 'rxSNR', float),
                'rx_rssi': kv(b, 'rxRSSI', int),
                'hop_start': hop_start,
                'hop_limit': hop_lim,
                'hops_used': hops_used,
                'relay': kv(b, 'relay'),
                'want_ack': kv(b, 'WantAck', int),
                'channel': kv(b, 'Ch'),
                'length': kv(b, 'len', int),
            })

if not rows:
    sys.exit("No '[RadioIf] Lora RX' lines found in the given logs.")

rows.sort(key=lambda r: r['timestamp'])
with open(out, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

print(f"Wrote {len(rows)} received-packet rows to {out} (dropped {dupes} duplicate ids)")
snrs = [r['rx_snr'] for r in rows if isinstance(r['rx_snr'], float)]
rssis = [r['rx_rssi'] for r in rows if isinstance(r['rx_rssi'], int)]
if snrs:
    print(f"SNR  min/mean/max: {min(snrs):.2f} / {sum(snrs)/len(snrs):.2f} / {max(snrs):.2f} dB")
if rssis:
    print(f"RSSI min/mean/max: {min(rssis)} / {sum(rssis)/len(rssis):.1f} / {max(rssis)} dBm")
nodes = Counter(r['from_node'] for r in rows)
print(f"Unique source nodes: {len(nodes)}  (remote deployment should be ~1)")
for n, c in nodes.most_common(10):
    print(f"  {n}: {c} packets")
print(f"Time span (buoy clock, unreliable): {rows[0]['timestamp']} -> {rows[-1]['timestamp']}")
