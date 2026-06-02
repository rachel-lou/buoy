#!/usr/bin/env python3
"""Generate every figure in round2/ from the parsed direct-reception data.

Inputs:
  round2/direct_receptions.json   (produced by parse_log.py)
  buoy_dunk.gpx
Outputs into round2/:
  annotated.json                  receptions enriched with lat/lon + rx_d
  per_minute.json                 per-minute summary table (list of rows)
  fig_basemap_rssi.png            RSSI heatmap on OSM
  fig_basemap_rssi_sat.png        RSSI heatmap on satellite
  fig_basemap_snr.png             SNR heatmap on OSM
  fig_basemap_snr_sat.png         SNR heatmap on satellite
  fig_distance_sanity_sat.png     distance verification
  fig_rssi_and_range.png          RSSI + range time series
  fig_snr_and_range.png           SNR + range time series
  fig_signal_vs_range.png         RSSI+SNR vs distance, log-distance fit
  fig_snr_by_distance.png         SNR boxplot by distance bin
  fig_loss_vs_distance.png        direct reception rate by distance bin
"""
import json, math, re, os
from collections import defaultdict
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize, LinearSegmentedColormap
import contextily as cx

# ---- inputs ----
RX_LAT = 37 + 48 / 60 + 24.3 / 3600          # 37°48'24.3"N
RX_LON = -(122 + 25 / 60 + 23.7 / 3600)      # 122°25'23.7"W
MOUTH_IDX = 13  # last GPX trkpt the buoy reached (near the cove mouth)

data = json.load(open('round2/direct_receptions.json'))

gpx_text = open('buoy_dunk.gpx').read()
trkpts = [{'lat': float(m[1]), 'lon': float(m[2])}
          for m in re.finditer(r'<trkpt lat="([\-\d.]+)" lon="([\-\d.]+)">\s*<ele>([\-\d.]+)</ele>',
                               gpx_text)]

# ---- geodesy ----
R_EARTH = 6371000.0
def hav(a, b, c, d):
    a1, b1, a2, b2 = map(math.radians, [a, b, c, d])
    dl, do = a2 - a1, b2 - b1
    h = math.sin(dl / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin(do / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))

def to_merc(lat, lon):
    x = lon * 20037508.34 / 180.0
    y = math.log(math.tan((90 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    return x, y * 20037508.34 / 180.0

# ---- position model: linear time interp from trkpt 0 (swim start) to trkpt
# MOUTH_IDX (last direct reception). trkpts past MOUTH_IDX were swum without
# a working buoy. ----
cum = [0.0]
for i in range(1, len(trkpts)):
    cum.append(cum[-1] + hav(trkpts[i - 1]['lat'], trkpts[i - 1]['lon'],
                              trkpts[i]['lat'], trkpts[i]['lon']))
mouth_cum = cum[MOUTH_IDX]

swim_start_t = min(r['t_pdt'] for r in data if r['rssi'] < -50)
swim_end_t = data[-1]['t_pdt']

def pos_at_time(t):
    if t <= swim_start_t:
        return trkpts[0]['lat'], trkpts[0]['lon']
    if t >= swim_end_t:
        return trkpts[MOUTH_IDX]['lat'], trkpts[MOUTH_IDX]['lon']
    frac = (t - swim_start_t) / (swim_end_t - swim_start_t)
    target = frac * mouth_cum
    for i in range(1, MOUTH_IDX + 1):
        if cum[i] >= target:
            seg = cum[i] - cum[i - 1]
            sf = (target - cum[i - 1]) / seg if seg > 0 else 0
            lat = trkpts[i - 1]['lat'] + sf * (trkpts[i]['lat'] - trkpts[i - 1]['lat'])
            lon = trkpts[i - 1]['lon'] + sf * (trkpts[i]['lon'] - trkpts[i - 1]['lon'])
            return lat, lon
    return trkpts[MOUTH_IDX]['lat'], trkpts[MOUTH_IDX]['lon']

for r in data:
    lat, lon = pos_at_time(r['t_pdt'])
    r['lat'] = lat; r['lon'] = lon
    r['rx_d'] = hav(RX_LAT, RX_LON, lat, lon)
json.dump(data, open('round2/annotated.json', 'w'), indent=1)

# ---- per-minute summary ----
buckets = defaultdict(list)
for r in data:
    buckets[r['t_pdt'] // 60].append(r)
table_rows = []
for mp in sorted(buckets):
    rs = buckets[mp]
    h, mm = divmod(mp, 60)
    snrs = [r['snr'] for r in rs]; rssis = [r['rssi'] for r in rs]; ds = [r['rx_d'] for r in rs]
    table_rows.append((h, mm, len(rs), sum(snrs) / len(snrs),
                       sum(rssis) / len(rssis), min(rssis),
                       sum(ds) / len(ds), max(ds)))
json.dump(table_rows, open('round2/per_minute.json', 'w'), indent=1)

# ---- plotting setup ----
gpx_xy = [to_merc(p['lat'], p['lon']) for p in trkpts]
gpx_x = np.array([p[0] for p in gpx_xy]); gpx_y = np.array([p[1] for p in gpx_xy])
rx_x, rx_y = to_merc(RX_LAT, RX_LON)
swim = sorted([r for r in data if r['t_pdt'] >= swim_start_t], key=lambda r: r['t_pdt'])
sx = np.array([to_merc(r['lat'], r['lon'])[0] for r in swim])
sy = np.array([to_merc(r['lat'], r['lon'])[1] for r in swim])
srssi = np.array([r['rssi'] for r in swim])
ssnr = np.array([r['snr'] for r in swim])
xmin = min(gpx_x.min(), rx_x) - 100
xmax = max(gpx_x.max(), rx_x) + 100
ymin = min(gpx_y.min(), rx_y) - 100
ymax = max(gpx_y.max(), rx_y) + 100
sf = 1.0 / math.cos(math.radians(RX_LAT))
CMAP = LinearSegmentedColormap.from_list('signal',
    ['#4a0000', '#c0392b', '#e67e22', '#f1c40f', '#2ecc71', '#0a3d0a'])

def add_basemap(ax, satellite=False):
    src = cx.providers.Esri.WorldImagery if satellite else cx.providers.OpenStreetMap.Mapnik
    try:
        cx.add_basemap(ax, source=src, crs='EPSG:3857', attribution_size=7)
    except Exception:
        cx.add_basemap(ax, source=cx.providers.CartoDB.Positron, crs='EPSG:3857', attribution_size=7)

def draw_heatmap(ax, vals, vmin, vmax, label, unit, satellite=False):
    """Render the buoy-alive trajectory as a colored line + rings + markers."""
    ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax)
    line = 'white' if satellite else 'gray'
    ring = 'white' if satellite else '#666'
    bg = 'black' if satellite else 'white'
    # Full GPX (faint), buoy-live portion (bold), swimmer-only (dashed)
    ax.plot(gpx_x, gpx_y, '-', color=line, lw=1.2, alpha=0.5, zorder=1,
            label='swimmer GPX route (full)')
    ax.plot(gpx_x[:MOUTH_IDX + 1], gpx_y[:MOUTH_IDX + 1], '-',
            color=line, lw=1.6, alpha=0.85, zorder=2,
            label=f'buoy-alive portion (trkpts 0–{MOUTH_IDX})')
    ax.plot(gpx_x[MOUTH_IDX:], gpx_y[MOUTH_IDX:], '--',
            color=line, lw=1.3, alpha=0.75, zorder=2,
            label='swimmer continued after buoy died')
    # Colored line
    pts = np.array([sx, sy]).T.reshape(-1, 1, 2)
    seg = np.concatenate([pts[:-1], pts[1:]], axis=1)
    mid = (vals[:-1] + vals[1:]) / 2.0
    lc = LineCollection(seg, cmap=CMAP, norm=Normalize(vmin=vmin, vmax=vmax),
                        linewidth=7, alpha=0.95, zorder=3, capstyle='round')
    lc.set_array(mid)
    ax.add_collection(lc)
    cb = plt.colorbar(lc, ax=ax, shrink=0.7)
    cb.set_label(f'{label} ({unit}) — direct receptions only')
    # Reception dots
    ax.scatter(sx, sy, s=14, c=('white' if satellite else 'black'), alpha=0.55, zorder=4)
    # Range rings
    for r_m in [100, 200, 300, 400, 500]:
        th = np.linspace(0, 2 * np.pi, 200)
        ax.plot(rx_x + r_m * sf * np.cos(th), rx_y + r_m * sf * np.sin(th),
                ':' if satellite else '--', color=ring, lw=1, alpha=0.65, zorder=5)
        ax.text(rx_x, rx_y + r_m * sf, f'{r_m} m', fontsize=9, fontweight='bold',
                color=ring, ha='center', va='bottom', zorder=5,
                bbox=dict(boxstyle='round,pad=0.2', fc=bg, ec=ring, alpha=0.8))
    # RX + markers
    ax.scatter([rx_x], [rx_y], s=600, marker='*', color='cyan',
               edgecolor='black', linewidth=2, zorder=10,
               label='Receiver (37°48′24.3″N 122°25′23.7″W)')
    start_xy = to_merc(swim[0]['lat'], swim[0]['lon'])
    mouth_xy = to_merc(trkpts[MOUTH_IDX]['lat'], trkpts[MOUTH_IDX]['lon'])
    ax.scatter([start_xy[0]], [start_xy[1]], s=180, marker='o', color='lime',
               edgecolor='black', linewidth=1.5, zorder=9,
               label='Swim start (10:55:57 PDT)')
    ax.scatter([mouth_xy[0]], [mouth_xy[1]], s=260, marker='X', color='red',
               edgecolor='black', linewidth=1.5, zorder=11,
               label='Last DIRECT reception (11:13:04, ~mouth)')
    # 2-min time labels
    for r in swim:
        h = r['t_pdt'] // 3600; m = (r['t_pdt'] % 3600) // 60; s = r['t_pdt'] % 60
        if s < 5 and m % 2 == 0:
            x, y = to_merc(r['lat'], r['lon'])
            ax.annotate(f"{h:02d}:{m:02d}", (x, y), xytext=(7, 7),
                        textcoords='offset points', fontsize=10, fontweight='bold',
                        bbox=dict(boxstyle='round,pad=0.2', fc='white', ec='black', alpha=0.9),
                        zorder=8)
    ax.set_xticks([]); ax.set_yticks([])
    ax.legend(loc='upper right', fontsize=9, framealpha=0.92)

# ---- RSSI basemap (street + satellite) ----
for sat, fname in [(False, 'fig_basemap_rssi.png'), (True, 'fig_basemap_rssi_sat.png')]:
    fig, ax = plt.subplots(figsize=(13, 12))
    draw_heatmap(ax, srssi, -115, -50, 'RSSI', 'dBm', satellite=sat)
    ax.set_title(('BUOY1 — RSSI along buoy-live path' + ('  (satellite)' if sat else '\n270 direct receptions; buoy died near cove mouth ~11:13–11:15 PDT')),
                 fontsize=13, fontweight='bold')
    add_basemap(ax, satellite=sat)
    plt.tight_layout()
    plt.savefig(f'round2/{fname}', dpi=160, bbox_inches='tight')
    plt.close()
    print(f'Saved round2/{fname}')

# ---- SNR basemap (street + satellite) ----
for sat, fname in [(False, 'fig_basemap_snr.png'), (True, 'fig_basemap_snr_sat.png')]:
    fig, ax = plt.subplots(figsize=(13, 12))
    draw_heatmap(ax, ssnr, -10, 8, 'SNR', 'dB', satellite=sat)
    ax.set_title(('BUOY1 — SNR along buoy-live path' + ('  (satellite)' if sat else '\n270 direct receptions; SF11 demod floor at −17 dB')),
                 fontsize=13, fontweight='bold')
    add_basemap(ax, satellite=sat)
    plt.tight_layout()
    plt.savefig(f'round2/{fname}', dpi=160, bbox_inches='tight')
    plt.close()
    print(f'Saved round2/{fname}')

# ---- RSSI + range time series ----
t0 = data[0]['t_pdt']
t_rel = np.array([r['t_pdt'] - t0 for r in data]) / 60.0
rssi_all = np.array([r['rssi'] for r in data])
snr_all = np.array([r['snr'] for r in data])
rxd_all = np.array([r['rx_d'] for r in data])

fig, ax1 = plt.subplots(figsize=(13, 5.5))
ax1.set_xlabel('Minutes since log start (10:49:14 PDT)')
ax1.set_ylabel('RSSI (dBm)', color='C0')
ax1.plot(t_rel, rssi_all, '-', color='C0', lw=0.7, alpha=0.5)
ax1.scatter(t_rel, rssi_all, s=10, c='C0', alpha=0.85)
ax1.tick_params(axis='y', labelcolor='C0'); ax1.set_ylim(-130, -10); ax1.grid(alpha=0.3)
ax2 = ax1.twinx()
ax2.set_ylabel('Estimated range from RX (m)', color='C3')
ax2.plot(t_rel, rxd_all, '-', color='C3', lw=1.6, alpha=0.85)
ax2.fill_between(t_rel, 0, rxd_all, color='C3', alpha=0.15)
ax2.tick_params(axis='y', labelcolor='C3'); ax2.set_ylim(0, 450)
for label, t in [('swim start (10:55:57)', swim_start_t - t0),
                 ('last DIRECT (11:13:04)', swim_end_t - t0)]:
    ax1.axvline(t / 60.0, color='black', linestyle='--', lw=0.8, alpha=0.6)
    ax1.text(t / 60.0 + 0.1, -15, label, fontsize=9)
plt.title('Direct-RX RSSI & estimated range vs time (270 packets)',
          fontsize=13, fontweight='bold')
plt.tight_layout(); plt.savefig('round2/fig_rssi_and_range.png', dpi=140); plt.close()
print('Saved round2/fig_rssi_and_range.png')

# ---- SNR + range time series ----
fig, ax1 = plt.subplots(figsize=(13, 5.5))
ax1.set_xlabel('Minutes since log start (10:49:14 PDT)')
ax1.set_ylabel('SNR (dB)', color='C2')
ax1.plot(t_rel, snr_all, '-', color='C2', lw=0.7, alpha=0.5)
ax1.scatter(t_rel, snr_all, s=10, c='C2', alpha=0.85)
ax1.tick_params(axis='y', labelcolor='C2'); ax1.set_ylim(-20, 10); ax1.grid(alpha=0.3)
ax1.axhline(0, color='gray', linestyle=':', alpha=0.5)
ax1.axhline(-17, color='red', linestyle=':', alpha=0.6)
ax1.text(0.5, -16, 'SF11 demod floor', fontsize=8, color='red')
ax2 = ax1.twinx()
ax2.set_ylabel('Estimated range from RX (m)', color='C3')
ax2.plot(t_rel, rxd_all, '-', color='C3', lw=1.6, alpha=0.6)
ax2.fill_between(t_rel, 0, rxd_all, color='C3', alpha=0.1)
ax2.tick_params(axis='y', labelcolor='C3'); ax2.set_ylim(0, 450)
for label, t in [('swim start (10:55:57)', swim_start_t - t0),
                 ('last DIRECT (11:13:04)', swim_end_t - t0)]:
    ax1.axvline(t / 60.0, color='black', linestyle='--', lw=0.8, alpha=0.6)
    ax1.text(t / 60.0 + 0.1, 9, label, fontsize=9)
plt.title('Direct-RX SNR & estimated range vs time (270 packets)',
          fontsize=13, fontweight='bold')
plt.tight_layout(); plt.savefig('round2/fig_snr_and_range.png', dpi=140); plt.close()
print('Saved round2/fig_snr_and_range.png')

# ---- RSSI/SNR vs range with path-loss fit ----
in_swim = np.array([r['t_pdt'] >= swim_start_t for r in data])
mask = in_swim & (rxd_all > 30) & (rssi_all > -110)
b, a = np.polyfit(np.log10(rxd_all[mask]), rssi_all[mask], 1)
n_pl = -b / 10.0
print(f"Path-loss fit: RSSI = {a:.1f} {b:+.2f}·log10(d)   n ≈ {n_pl:.2f}  ({mask.sum()} samples)")
fig, axes = plt.subplots(1, 2, figsize=(15, 6))
ax = axes[0]
ax.scatter(rxd_all[mask], rssi_all[mask], s=24, c='steelblue', alpha=0.7,
           edgecolor='black', linewidth=0.3)
d_grid = np.logspace(math.log10(30), math.log10(420), 100)
ax.plot(d_grid, a + b * np.log10(d_grid), '--', color='red', lw=2,
        label=f'fit: RSSI = {a:.0f} {b:+.1f}·log₁₀(d)  (n ≈ {n_pl:.2f})')
ax.plot(d_grid, 20 - (20 * np.log10(d_grid) + 20 * math.log10(915) - 27.55),
        ':', color='green', lw=2, label='free-space (n=2.0)')
ax.set_xscale('log'); ax.set_xlabel('Distance from RX (m)  [log scale]')
ax.set_ylabel('RSSI (dBm)'); ax.set_title('RSSI vs range — DIRECT receptions only')
ax.grid(True, which='both', alpha=0.3); ax.legend(loc='lower left', fontsize=9)
ax.axhline(-130, color='gray', linestyle=':', alpha=0.5); ax.set_ylim(-130, -10)
ax = axes[1]
ax.scatter(rxd_all[in_swim], snr_all[in_swim], s=24, c='seagreen', alpha=0.7,
           edgecolor='black', linewidth=0.3)
ax.set_xscale('log'); ax.set_xlabel('Distance from RX (m)  [log scale]')
ax.set_ylabel('SNR (dB)'); ax.set_title('SNR vs range — DIRECT receptions only')
ax.grid(True, which='both', alpha=0.3)
ax.axhline(0, color='gray', linestyle=':', alpha=0.5)
ax.axhline(-17, color='red', linestyle=':', alpha=0.5)
ax.text(35, -16, 'SF11 demod floor', fontsize=8, color='red')
plt.suptitle(f'Direct-RX path-loss model: n ≈ {n_pl:.2f}  ({mask.sum()} samples)',
             fontsize=13, fontweight='bold')
plt.tight_layout(); plt.savefig('round2/fig_signal_vs_range.png', dpi=140); plt.close()
print('Saved round2/fig_signal_vs_range.png')

# ---- SNR boxplot by distance ----
bins = [(40, 75), (75, 125), (125, 175), (175, 225),
        (225, 275), (275, 325), (325, 400)]
swim_d = rxd_all[in_swim]; swim_snr = snr_all[in_swim]
bp_data = [swim_snr[(swim_d >= lo) & (swim_d < hi)] for lo, hi in bins]
labels = [f'{lo}–{hi}\n(n={len(arr)})' for (lo, hi), arr in zip(bins, bp_data)]
fig, ax = plt.subplots(figsize=(11, 5.5))
ax.boxplot(bp_data, positions=list(range(len(bins))), widths=0.6, patch_artist=True,
           medianprops=dict(color='black', lw=2),
           boxprops=dict(facecolor='#2ecc71', alpha=0.7))
ax.set_xticks(list(range(len(bins)))); ax.set_xticklabels(labels)
ax.set_xlabel('TRUE distance from RX (m)'); ax.set_ylabel('SNR (dB)')
ax.set_title('SNR distribution by distance bin — direct receptions during swim')
ax.axhline(0, color='gray', linestyle=':', alpha=0.6)
ax.axhline(-17, color='red', linestyle=':', alpha=0.6, label='SF11 demod floor')
ax.legend(loc='lower left', fontsize=9); ax.grid(True, axis='y', alpha=0.3)
plt.tight_layout(); plt.savefig('round2/fig_snr_by_distance.png', dpi=140); plt.close()
print('Saved round2/fig_snr_by_distance.png')

# ---- distance sanity check (satellite) ----
fig, ax = plt.subplots(figsize=(12, 11))
ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax)
for r_m in [50, 100, 200, 300, 400, 500]:
    th = np.linspace(0, 2 * np.pi, 300)
    ax.plot(rx_x + r_m * sf * np.cos(th), rx_y + r_m * sf * np.sin(th),
            '--', color='white', lw=1.0, alpha=0.75, zorder=4)
    ax.text(rx_x, rx_y + r_m * sf, f'{r_m} m', fontsize=10, fontweight='bold',
            color='white', ha='center', va='bottom', zorder=5,
            bbox=dict(boxstyle='round,pad=0.2', fc='black', ec='white', alpha=0.7))
ax.plot(gpx_x, gpx_y, '-', color='yellow', lw=3, alpha=0.95, zorder=3)
ax.scatter(gpx_x, gpx_y, s=40, c='yellow', edgecolor='black', linewidth=0.8, zorder=4)
key_idx = [0, 4, 7, 11, 13, 17, 20, 27]
for i in key_idx:
    d = hav(RX_LAT, RX_LON, trkpts[i]['lat'], trkpts[i]['lon'])
    x, y = gpx_xy[i]
    ax.plot([rx_x, x], [rx_y, y], '-', color='red', lw=1.2, alpha=0.85, zorder=4)
    mx, my = (rx_x + x) / 2, (rx_y + y) / 2
    ax.text(mx, my, f"  trkpt {i}: {d:.0f} m",
            fontsize=10, fontweight='bold', color='black',
            bbox=dict(boxstyle='round,pad=0.25', fc='yellow', ec='black', alpha=0.95),
            zorder=6)
ax.scatter([rx_x], [rx_y], s=700, marker='*', color='cyan',
           edgecolor='black', linewidth=2, zorder=10, label='Receiver')
ax.set_xticks([]); ax.set_yticks([])
ax.set_title('Distance sanity-check (satellite underlay)', fontsize=12, fontweight='bold')
ax.legend(loc='upper right', fontsize=10, framealpha=0.95)
add_basemap(ax, satellite=True)
plt.tight_layout(); plt.savefig('round2/fig_distance_sanity_sat.png', dpi=160, bbox_inches='tight')
plt.close()
print('Saved round2/fig_distance_sanity_sat.png')

# ---- packet loss vs distance ----
recv_st = sorted([(r['seq'], r['t_pdt']) for r in data if 'seq' in r])
seq_to_t = dict(recv_st)
def t_for_seq(s):
    if s in seq_to_t: return seq_to_t[s]
    lo = hi = None
    for ss, tt in recv_st:
        if ss < s: lo = (ss, tt)
        if ss > s:
            hi = (ss, tt); break
    if lo and hi:
        f = (s - lo[0]) / (hi[0] - lo[0])
        return lo[1] + f * (hi[1] - lo[1])
    return (lo or hi)[1]
recv_td = sorted([(r['t_pdt'], r['rx_d']) for r in data])
def rxd_t(t):
    if t <= recv_td[0][0]: return recv_td[0][1]
    if t >= recv_td[-1][0]: return recv_td[-1][1]
    for i in range(1, len(recv_td)):
        if recv_td[i][0] >= t:
            t0_, d0_ = recv_td[i - 1]; t1_, d1_ = recv_td[i]
            f = (t - t0_) / (t1_ - t0_) if t1_ > t0_ else 0
            return d0_ + f * (d1_ - d0_)
    return recv_td[-1][1]
bins2 = [40, 75, 125, 175, 225, 275, 325, 400]
counts = [0] * (len(bins2) - 1); recv = [0] * (len(bins2) - 1)
for s in range(234, 520):
    t = t_for_seq(s); d = rxd_t(t)
    if d < 40: continue
    for i in range(len(bins2) - 1):
        if bins2[i] <= d < bins2[i + 1]:
            counts[i] += 1
            if s in seq_to_t: recv[i] += 1
            break
fig, ax = plt.subplots(figsize=(11, 5.5))
labels = [f'{bins2[i]}–{bins2[i + 1]}' for i in range(len(bins2) - 1)]
x = np.arange(len(labels))
rates = [100.0 * recv[i] / counts[i] if counts[i] else 0 for i in range(len(labels))]
ax.bar(x, rates,
       color=['#2ecc71' if r > 80 else '#f39c12' if r > 50 else '#e74c3c' for r in rates],
       edgecolor='black')
for i, (r, c, n) in enumerate(zip(rates, counts, recv)):
    ax.text(i, r + 2, f'{r:.0f}%\n({n}/{c})', ha='center', fontsize=9)
ax.set_xticks(x); ax.set_xticklabels(labels)
ax.set_xlabel('TRUE distance from RX (m)'); ax.set_ylabel('Direct reception rate (%)')
ax.set_ylim(0, 110); ax.set_title('Direct reception rate vs distance')
ax.grid(True, axis='y', alpha=0.3)
plt.tight_layout(); plt.savefig('round2/fig_loss_vs_distance.png', dpi=140); plt.close()
print('Saved round2/fig_loss_vs_distance.png')

print('\nAll figures rebuilt.')
