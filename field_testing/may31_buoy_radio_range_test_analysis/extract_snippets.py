#!/usr/bin/env python3
"""Extract the specific log snippets that appear in round2/FINDINGS.md §5.
Writes them to round2/log_snippets.txt."""
import re

ANSI = re.compile(rb'\x1b\[[\d;?]*[A-Za-z]')
with open('buoy1_20260531_1049.txt', 'rb') as f:
    raw = f.read()
text = ANSI.sub(b'', raw)
text = re.sub(rb'\x1b\([A-Z0-9]', b'', text)
text = re.sub(rb'\x1b[=>]', b'', text).decode('utf-8', errors='replace')
lines = text.split('\n')

def time_of(line):
    m = re.search(r'(\d\d):(\d\d):(\d\d)\s+\d+\s+\[', line)
    if not m: return None
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])

def in_range(line, t0, t1):
    t = time_of(line)
    return t is not None and t0 <= t <= t1

out = []

def hdr(title):
    out.append('=' * 80)
    out.append(title)
    out.append('=' * 80)

# §5.1
hdr('SNIPPET 1: Healthy direct reception (mid-test, ~11:00 PDT, 18:00 UTC)')
t0, t1 = 18 * 3600, 18 * 3600 + 20
for ln in lines:
    if in_range(ln, t0, t1) and '4089c08' in ln:
        if any(k in ln for k in ('Lora RX', 'Received text', 'enqueue for send', 'decoded message')):
            out.append(ln[:200])

# §5.2
out.append('')
hdr('SNIPPET 2: Last DIRECT reception of buoy (11:13:04 PDT = 18:13:04 UTC)')
t0, t1 = 18 * 3600 + 13 * 60, 18 * 3600 + 13 * 60 + 10
for ln in lines:
    if in_range(ln, t0, t1):
        if '4089c08' in ln and any(k in ln for k in ('Lora RX', 'Received text')):
            out.append(ln[:220])

# §5.3
out.append('')
hdr('SNIPPET 3: Receiver TX queue draining OLD packets after direct link failed\n'
    '(seqs out of order — these are queued retransmits, not new receptions)')
t0, t1 = 18 * 3600 + 13 * 60 + 10, 18 * 3600 + 15 * 60 + 50
shown = 0
for ln in lines:
    if in_range(ln, t0, t1) and 'Started Tx' in ln and '4089c08' in ln:
        out.append(ln[:220])
        shown += 1
        if shown >= 8:
            out.append('  ... (more queued retransmits omitted)')
            break

# §5.4
out.append('')
hdr('SNIPPET 4: RELAYED 9c08 packets received through neighbor mesh nodes\n'
    '(relay=0x3c, 0x27, etc. — buoy was still alive, heard via mesh)')
t0, t1 = 18 * 3600 + 13 * 60 + 5, 18 * 3600 + 16 * 60
shown = 0
for ln in lines:
    if in_range(ln, t0, t1) and 'Lora RX' in ln and '4089c08' in ln:
        m = re.search(r'relay=(0x[0-9a-f]+)', ln)
        if m and m[1] != '0x8':
            out.append(ln[:230])
            shown += 1
            if shown >= 10:
                out.append('  ... (more relayed packets omitted)')
                break

# §5.5
out.append('')
hdr('SNIPPET 5: Final reception, then silence (~11:15:49 PDT = 18:15:49 UTC)')
t0, t1 = 18 * 3600 + 15 * 60 + 30, 18 * 3600 + 16 * 60 + 30
for ln in lines:
    if in_range(ln, t0, t1):
        if '4089c08' in ln and any(k in ln for k in ('Lora RX', 'Started Tx',
                                                     'Ignore received', 'Received text')):
            out.append(ln[:220])

# §5.6
out.append('')
hdr('SNIPPET 6: Gap of silence and buoy reboot (11:40:47 PDT = 18:40:47 UTC)')
last_pre = None
for ln in lines:
    t = time_of(ln)
    if t is not None and t < 18 * 3600 + 16 * 60 and '4089c08' in ln and 'Lora RX' in ln:
        last_pre = ln
out.append('Last 9c08 sighting before silence:')
if last_pre:
    out.append(f'  {last_pre[:220]}')
out.append('\n  ... ~25 minutes of silence ...\n')
for ln in lines:
    t = time_of(ln)
    if t is not None and t >= 18 * 3600 + 40 * 60 and '4089c08' in ln and 'Lora RX' in ln:
        out.append('First reception after silence (uptime field shows BOOT):')
        out.append(f'  {ln[:220]}')
        break

# §5.7
out.append('')
hdr('SNIPPET 7: Inventory of mesh nodes that relayed 9c08 packets')
counts = {}
for ln in lines:
    if 'Lora RX' in ln and '4089c08' in ln:
        m = re.search(r'relay=(0x[0-9a-f]+)', ln)
        if m: counts[m[1]] = counts.get(m[1], 0) + 1
out.append('relay= byte → packet count (entire log):')
for r, c in sorted(counts.items(), key=lambda x: -x[1]):
    label = 'direct from 9c08 (originator)' if r == '0x8' else 'via mesh relay'
    out.append(f'  relay={r}: {c:4d} packets  ({label})')

txt = '\n'.join(out) + '\n'
print(txt)
open('round2/log_snippets.txt', 'w').write(txt)
print('Wrote round2/log_snippets.txt')
