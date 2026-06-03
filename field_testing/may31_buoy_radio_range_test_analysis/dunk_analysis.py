#!/usr/bin/env python3
"""Detect and characterize the dunk test that took place after the buoy reboot
at 11:40:47 PDT.

Parses post-reboot BUOY1 text messages (which carry Temperature/Pressure/Deep
telemetry) and pairs them with their direct LoRa-RX rxSNR/rxRSSI samples.

Writes:
  round2/dunk_telemetry.json  — full per-packet record across the dunk window
  round2/fig_dunk_test.png    — 4-panel time series figure
"""
import re, json, os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ANSI = re.compile(rb'\x1b\[[\d;?]*[A-Za-z]')
with open('buoy1_20260531_1049.txt', 'rb') as f:
    raw = f.read()
text = ANSI.sub(b'', raw)
text = re.sub(rb'\x1b\([A-Z0-9]', b'', text)
text = re.sub(rb'\x1b[=>]', b'', text).decode('utf-8', errors='replace')

re_text = re.compile(
    r'(\d\d):(\d\d):(\d\d)\s+\d+\s+\[Router\]\s+Received text msg from=0x4089c08,\s+'
    r'id=(0x[0-9a-f]+),\s+msg=BUOY(\d+)\s+#(\d+)\s+\+(\d+)m(\d+)s\s+'
    r'Temperature:(-?[\d.]+).*?Pressure:(-?[\d.]+).*?Deep:(-?[\d.]+)'
)
re_lora = re.compile(
    r'Lora RX \(id=(0x[0-9a-f]+)\s+fr=0x04089c08[^)]*?'
    r'rxSNR=(-?[\d.]+)\s+rxRSSI=(-?\d+)[^)]*?relay=0x8\)'
)
lora_by_id = {}
for m in re_lora.finditer(text):
    if m.group(1) not in lora_by_id:
        lora_by_id[m.group(1)] = (float(m.group(2)), int(m.group(3)))

# All text msgs sorted by reception time
msgs = []
for m in re_text.finditer(text):
    t_utc = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3))
    pid = m.group(4)
    snr, rssi = lora_by_id.get(pid, (None, None))
    msgs.append({
        'time': f"{m.group(1)}:{m.group(2)}:{m.group(3)}",
        't_utc': t_utc, 't_pdt': t_utc - 7*3600,
        'id': pid,
        'seq': int(m.group(6)),
        'buoy_up_s': int(m.group(7))*60 + int(m.group(8)),
        'temp': float(m.group(9)),
        'press': float(m.group(10)),
        'deep': float(m.group(11)),
        'snr': snr, 'rssi': rssi,
    })
msgs.sort(key=lambda r: r['t_utc'])

# Find the reboot point — seq + uptime both reset
boot_i = None
for i in range(1, len(msgs)):
    if (msgs[i]['seq'] < 50 and msgs[i]['buoy_up_s'] < 100
        and msgs[i-1]['seq'] > 100 and msgs[i-1]['buoy_up_s'] > 500):
        boot_i = i; break
post = msgs[boot_i:]
print(f"Reboot at idx {boot_i}; post-reboot text msgs: {len(post)}")
print(f"  from {post[0]['time']} UTC ({post[0]['t_pdt']//3600:02d}:"
      f"{(post[0]['t_pdt']%3600)//60:02d}:{post[0]['t_pdt']%60:02d} PDT)")

# Baseline (first 5 msgs after reboot — buoy still dry on dock)
base_deep = sum(r['deep'] for r in post[:5]) / 5
base_press = sum(r['press'] for r in post[:5]) / 5
print(f"Baseline: deep={base_deep:.4f} m  press={base_press:.4f} kPa")

# Tag each packet "submerged" if depth-from-pressure > 2 cm above baseline
for r in post:
    r['delta_deep'] = r['deep'] - base_deep
    r['delta_press'] = r['press'] - base_press
    r['submerged'] = r['delta_deep'] > 0.02

# Find dunk window bounds (first to last submerged packet)
sub_times = [r['t_pdt'] for r in post if r['submerged']]
dunk_start = min(sub_times); dunk_end = max(sub_times)
def fmt(t): return f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"
print(f"Dunk window: {fmt(dunk_start)} → {fmt(dunk_end)} PDT  ({dunk_end - dunk_start} s)")

os.makedirs('round2', exist_ok=True)
json.dump(post, open('round2/dunk_telemetry.json', 'w'), indent=1)

# ---- Figure: 4 stacked panels covering the dunk window ----
t_ref = post[0]['t_pdt']  # x-axis origin = first post-reboot packet
t = np.array([r['t_pdt'] - t_ref for r in post])
temp = np.array([r['temp'] for r in post])
press = np.array([r['press'] for r in post])
deep_m = np.array([r['delta_deep'] for r in post])  # depth above baseline
rssi = np.array([r['rssi'] if r['rssi'] is not None else np.nan for r in post])
snr  = np.array([r['snr']  if r['snr']  is not None else np.nan for r in post])
sub = np.array([r['submerged'] for r in post])

# Show the whole post-reboot story: full dry-on-dock baseline (~3 min)
# → buoy carried to water (~10 s) → 2-min dunk → recovery → drying off.
# Start at reboot, end 60 s after the last submerged packet.
t_min = 0
t_max = (dunk_end - t_ref) + 60
mask = (t >= t_min) & (t <= t_max)

# Define "true dry" baseline as packets before 11:43:30 PDT (well before the
# buoy starts approaching the water at 11:43:54).
DRY_END_PDT = 11*3600 + 43*60 + 30

fig, axes = plt.subplots(4, 1, figsize=(13, 11), sharex=True)

# Panel 1: Temperature
ax = axes[0]
ax.plot(t[mask], temp[mask], '-o', color='C3', markersize=4, lw=1)
ax.fill_between(t, ax.get_ylim()[0], ax.get_ylim()[1],
                where=sub, color='blue', alpha=0.10, label='submerged (Δdepth > 2 cm)')
ax.set_ylabel('Temperature (°C)', color='C3')
ax.tick_params(axis='y', labelcolor='C3')
ax.grid(alpha=0.3)
ax.set_title('Dunk test — telemetry vs time', fontsize=13, fontweight='bold', loc='left')
ax.legend(loc='upper right', fontsize=9)

# Panel 2: Depth (above baseline, in cm)
ax = axes[1]
ax.plot(t[mask], deep_m[mask] * 100, '-o', color='steelblue', markersize=4, lw=1)
ax.fill_between(t[mask], 0, deep_m[mask] * 100,
                where=(deep_m[mask] > 0), color='steelblue', alpha=0.3)
ax.axhline(0, color='gray', linestyle=':', alpha=0.5)
ax.axhline(2, color='red', linestyle=':', alpha=0.5, label='submersion threshold (2 cm)')
ax.set_ylabel('Depth below surface (cm)\n[from Pressure − baseline]', color='steelblue')
ax.tick_params(axis='y', labelcolor='steelblue')
ax.grid(alpha=0.3)
ax.legend(loc='upper right', fontsize=9)
# annotate dunk peaks
peaks = sorted([(r, r['delta_deep']) for r in post if r['submerged']
                and t_min <= r['t_pdt'] - t_ref <= t_max],
               key=lambda x: -x[1])[:5]
for r, _ in peaks:
    ax.annotate(f"{r['delta_deep']*100:.1f} cm",
                xy=(r['t_pdt'] - t_ref, r['delta_deep'] * 100),
                xytext=(0, 12), textcoords='offset points', ha='center', fontsize=8,
                bbox=dict(boxstyle='round,pad=0.2', fc='white', ec='steelblue', alpha=0.8))

# Panel 3: Pressure
ax = axes[2]
ax.plot(t[mask], press[mask], '-o', color='C2', markersize=4, lw=1)
ax.fill_between(t, ax.get_ylim()[0], ax.get_ylim()[1],
                where=sub, color='blue', alpha=0.10)
ax.axhline(base_press, color='gray', linestyle=':', alpha=0.5,
           label=f'dry baseline {base_press:.3f} kPa')
ax.set_ylabel('Pressure (kPa)', color='C2')
ax.tick_params(axis='y', labelcolor='C2')
ax.grid(alpha=0.3)
ax.legend(loc='upper right', fontsize=9)

# Panel 4: RSSI + SNR
ax = axes[3]
ax.plot(t[mask], rssi[mask], '-o', color='C0', markersize=4, lw=1, label='RSSI (dBm)')
ax.set_ylabel('RSSI (dBm)', color='C0')
ax.tick_params(axis='y', labelcolor='C0')
ax.set_ylim(-110, -10)
ax.grid(alpha=0.3)
ax2 = ax.twinx()
ax2.plot(t[mask], snr[mask], '-^', color='C2', markersize=4, lw=1, alpha=0.7, label='SNR (dB)')
ax2.set_ylabel('SNR (dB)', color='C2')
ax2.tick_params(axis='y', labelcolor='C2')
ax2.set_ylim(-5, 10)
ax.fill_between(t, -110, -10, where=sub, color='blue', alpha=0.10)
ax.set_xlabel(f'Seconds since first post-reboot packet ({fmt(t_ref)} PDT)')
# Combined legend
h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, loc='upper right', fontsize=9)

# Vertical guide lines for dunk start / end
for axx in axes:
    axx.axvline(dunk_start - t_ref, color='blue', linestyle='--', lw=0.8, alpha=0.7)
    axx.axvline(dunk_end - t_ref, color='blue', linestyle='--', lw=0.8, alpha=0.7)
axes[0].text(dunk_start - t_ref + 1, axes[0].get_ylim()[1] - 0.5,
             f'dunk start\n{fmt(dunk_start)}', fontsize=8, color='blue', va='top')
axes[0].text(dunk_end - t_ref + 1, axes[0].get_ylim()[1] - 0.5,
             f'dunk end\n{fmt(dunk_end)}', fontsize=8, color='blue', va='top')

axes[0].set_xlim(t_min, t_max)
plt.tight_layout()
plt.savefig('round2/fig_dunk_test.png', dpi=150, bbox_inches='tight')
plt.close()
print('Saved round2/fig_dunk_test.png')

# Stats summary
def stats(arr):
    arr = arr[~np.isnan(arr)]
    if len(arr) == 0: return None, None
    return arr.mean(), arr.std()

post_pdt = np.array([r['t_pdt'] for r in post])
dry_mask = post_pdt <= DRY_END_PDT
wet_mask = sub
dry_rssi_mu, _ = stats(rssi[dry_mask])
wet_rssi_mu, _ = stats(rssi[wet_mask])
dry_snr_mu, _ = stats(snr[dry_mask])
wet_snr_mu, _ = stats(snr[wet_mask])
print(f"\nDry baseline: RSSI={dry_rssi_mu:.1f} dBm  SNR={dry_snr_mu:.2f} dB")
print(f"Submerged:    RSSI={wet_rssi_mu:.1f} dBm  SNR={wet_snr_mu:.2f} dB")
print(f"ΔRSSI = {wet_rssi_mu - dry_rssi_mu:+.1f} dB")
print(f"ΔSNR  = {wet_snr_mu - dry_snr_mu:+.2f} dB")
