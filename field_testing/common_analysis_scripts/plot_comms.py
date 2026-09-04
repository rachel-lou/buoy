#!/usr/bin/env python3
"""Plot Meshtastic connectivity metrics for a remote single-link deployment.

    plot_comms.py CONNECTIVITY_CSV OUTDIR [--node 0x04089c08] [--date "Aug 29, 2026"]

CONNECTIVITY_CSV is produced by parse_basestation_logs.py. By default the
dominant source node (the buoy, at a remote site) is auto-detected and the plots
focus on that one link. Pass --date with the real deployment date because the
buoy clock in the logs is unreliable.

Writes four PNGs into OUTDIR:
  plot_signal_quality.png   rx SNR & RSSI over time (+ 5-min average)
  plot_packet_rate.png      packets received per minute
  plot_hops.png             hop count over time (0 = direct link)
  plot_reception_gaps.png   seconds between consecutive packets (link continuity)
"""
import argparse
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

ap = argparse.ArgumentParser()
ap.add_argument("csv")
ap.add_argument("outdir")
ap.add_argument("--node", help="source node id to focus on (default: most common)")
ap.add_argument("--date", default=None, help='real deployment date label, e.g. "Aug 29, 2026"')
args = ap.parse_args()

df = pd.read_csv(args.csv)
df["timestamp"] = pd.to_datetime(df["timestamp"])
df = df.sort_values("timestamp").reset_index(drop=True)

node = args.node or df["from_node"].value_counts().idxmax()
buoy = df[df["from_node"] == node].copy().reset_index(drop=True)
if buoy.empty:
    raise SystemExit(f"no packets from node {node}")

session = args.date or "date unknown"
tfmt = mdates.DateFormatter("%H:%M")
NOTE = "Buoy clock unreliable — times are relative, not absolute"
XLABEL = f"Time of day (buoy clock)\n{NOTE}"

def finish(ax, ylabel, title):
    ax.set_xlabel(XLABEL)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.xaxis.set_major_formatter(tfmt)
    ax.grid(True, alpha=0.3)

# 1. SNR & RSSI over time
fig, ax1 = plt.subplots(figsize=(12, 5))
ax1.scatter(buoy["timestamp"], buoy["rx_snr"], s=10, alpha=0.5, color="tab:blue", label="SNR")
r10 = buoy.set_index("timestamp")["rx_snr"].rolling("5min").mean()
ax1.plot(r10.index, r10.values, color="navy", lw=2, label="SNR (5-min avg)")
ax1.set_ylabel("rx SNR (dB)", color="tab:blue"); ax1.tick_params(axis="y", labelcolor="tab:blue")
ax2 = ax1.twinx()
ax2.scatter(buoy["timestamp"], buoy["rx_rssi"], s=10, alpha=0.35, color="tab:red", label="RSSI")
rr = buoy.set_index("timestamp")["rx_rssi"].rolling("5min").mean()
ax2.plot(rr.index, rr.values, color="darkred", lw=2, label="RSSI (5-min avg)")
ax2.set_ylabel("rx RSSI (dBm)", color="tab:red"); ax2.tick_params(axis="y", labelcolor="tab:red")
ax1.set_title(f"Buoy link signal quality over time — {session}")
ax1.set_xlabel(XLABEL); ax1.xaxis.set_major_formatter(tfmt); ax1.grid(True, alpha=0.3)
l1, lb1 = ax1.get_legend_handles_labels(); l2, lb2 = ax2.get_legend_handles_labels()
ax1.legend(l1 + l2, lb1 + lb2, loc="lower left", fontsize=8)
fig.tight_layout(); fig.savefig(f"{args.outdir}/plot_signal_quality.png", dpi=130); plt.close(fig)

# 2. Received-packet rate
rate = buoy.set_index("timestamp").assign(n=1)["n"].resample("1min").sum()
fig, ax = plt.subplots(figsize=(12, 4.5))
ax.plot(rate.index, rate.values, color="tab:green", lw=1.2)
ax.fill_between(rate.index, rate.values, alpha=0.3, color="tab:green")
finish(ax, "Packets received / min", f"Buoy packet reception rate — {session}")
fig.tight_layout(); fig.savefig(f"{args.outdir}/plot_packet_rate.png", dpi=130); plt.close(fig)

# 3. Hops used
fig, ax = plt.subplots(figsize=(12, 4.5))
sc = ax.scatter(buoy["timestamp"], buoy["hops_used"], c=buoy["rx_snr"], cmap="viridis", s=14, alpha=0.7)
finish(ax, "Hops to reach basestation", f"Path length over time — {session}")
hv = sorted(buoy["hops_used"].dropna().unique())
if hv:
    ax.set_yticks(hv)
cb = fig.colorbar(sc, ax=ax); cb.set_label("rx SNR (dB)")
fig.tight_layout(); fig.savefig(f"{args.outdir}/plot_hops.png", dpi=130); plt.close(fig)

# 4. Reception gaps (link continuity)
gap = buoy["timestamp"].diff().dt.total_seconds()
med = np.nanmedian(gap)
fig, ax = plt.subplots(figsize=(12, 4.5))
ax.plot(buoy["timestamp"], gap, color="tab:orange", lw=0.8)
ax.scatter(buoy["timestamp"], gap, s=8, color="tab:orange", alpha=0.4)
ax.axhline(med, color="gray", ls="--", lw=1, label=f"median gap {med:.0f} s")
finish(ax, "Seconds since previous packet", f"Link continuity — gaps between packets — {session}")
ax.legend(loc="upper left", fontsize=8)
fig.tight_layout(); fig.savefig(f"{args.outdir}/plot_reception_gaps.png", dpi=130); plt.close(fig)

dur_h = (buoy["timestamp"].iloc[-1] - buoy["timestamp"].iloc[0]).total_seconds() / 3600
print(f"node {node}: {len(buoy)} packets over {dur_h:.2f} h")
print(f"SNR  min/mean/max: {buoy['rx_snr'].min():.2f} / {buoy['rx_snr'].mean():.2f} / {buoy['rx_snr'].max():.2f} dB")
print(f"RSSI min/mean/max: {buoy['rx_rssi'].min()} / {buoy['rx_rssi'].mean():.1f} / {buoy['rx_rssi'].max()} dBm")
print(f"hops_used: {dict(buoy['hops_used'].value_counts())}")
print(f"reception gap median/max: {med:.0f} s / {np.nanmax(gap):.0f} s")
print("Wrote plot_signal_quality.png, plot_packet_rate.png, plot_hops.png, plot_reception_gaps.png")
