#!/usr/bin/env python3
"""Plot Meshtastic connectivity metrics over time for the Caspar Cove remote
deployment. Usage: plot_conn.py CONNECTIVITY_CSV OUTDIR

This was a remote single-link test: essentially only the buoy node
(0x04089c08) was heard, so the plots focus on the quality and continuity of
that one link rather than on network reach.
"""
import sys
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

csv_path = sys.argv[1]
outdir = sys.argv[2]

BUOY = "0x04089c08"

df = pd.read_csv(csv_path)
df["timestamp"] = pd.to_datetime(df["timestamp"])
df = df.sort_values("timestamp").reset_index(drop=True)
# focus on the buoy link (a handful of stray packets from another channel exist)
buoy = df[df["from_node"] == BUOY].copy().reset_index(drop=True)

# real deployment date; the buoy clock in the logs is unreliable (reads Aug 28)
session = "Aug 29, 2026"
tfmt = mdates.DateFormatter("%H:%M")
NOTE = "Buoy clock unreliable — times are relative, not absolute"

def finish(ax, ylabel, title):
    ax.set_xlabel("Time of day (buoy clock)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.xaxis.set_major_formatter(tfmt)
    ax.grid(True, alpha=0.3)

# --- 1. SNR & RSSI over time (twin axes) ---
fig, ax1 = plt.subplots(figsize=(12, 5))
ax1.scatter(buoy["timestamp"], buoy["rx_snr"], s=10, alpha=0.5, color="tab:blue", label="SNR")
r10 = buoy.set_index("timestamp")["rx_snr"].rolling("5min").mean()
ax1.plot(r10.index, r10.values, color="navy", lw=2, label="SNR (5-min avg)")
ax1.set_ylabel("rx SNR (dB)", color="tab:blue")
ax1.tick_params(axis="y", labelcolor="tab:blue")

ax2 = ax1.twinx()
ax2.scatter(buoy["timestamp"], buoy["rx_rssi"], s=10, alpha=0.35, color="tab:red", label="RSSI")
rr = buoy.set_index("timestamp")["rx_rssi"].rolling("5min").mean()
ax2.plot(rr.index, rr.values, color="darkred", lw=2, label="RSSI (5-min avg)")
ax2.set_ylabel("rx RSSI (dBm)", color="tab:red")
ax2.tick_params(axis="y", labelcolor="tab:red")

ax1.set_title(f"Buoy link signal quality over time — Caspar Cove ({session})")
ax1.set_xlabel(f"Time of day (buoy clock)\n{NOTE}")
ax1.xaxis.set_major_formatter(tfmt)
ax1.grid(True, alpha=0.3)
l1, lb1 = ax1.get_legend_handles_labels()
l2, lb2 = ax2.get_legend_handles_labels()
ax1.legend(l1 + l2, lb1 + lb2, loc="lower left", fontsize=8)
fig.tight_layout()
fig.savefig(f"{outdir}/plot_signal_quality.png", dpi=130)
plt.close(fig)

# --- 2. Received-packet rate over time (packets per minute) ---
rate = buoy.set_index("timestamp").assign(n=1)["n"].resample("1min").sum()
fig, ax = plt.subplots(figsize=(12, 4.5))
ax.plot(rate.index, rate.values, color="tab:green", lw=1.2)
ax.fill_between(rate.index, rate.values, alpha=0.3, color="tab:green")
finish(ax, "Packets received / min", f"Buoy packet reception rate ({session})")
ax.set_xlabel(f"Time of day (buoy clock)\n{NOTE}")
fig.tight_layout()
fig.savefig(f"{outdir}/plot_packet_rate.png", dpi=130)
plt.close(fig)

# --- 3. Hops used over time (a direct link should be 0 hops) ---
fig, ax = plt.subplots(figsize=(12, 4.5))
sc = ax.scatter(buoy["timestamp"], buoy["hops_used"], c=buoy["rx_snr"],
                cmap="viridis", s=14, alpha=0.7)
finish(ax, "Hops to reach basestation", f"Path length over time ({session})")
hv = sorted(buoy["hops_used"].dropna().unique())
if hv:
    ax.set_yticks(hv)
ax.set_xlabel(f"Time of day (buoy clock)\n{NOTE}")
cb = fig.colorbar(sc, ax=ax)
cb.set_label("rx SNR (dB)")
fig.tight_layout()
fig.savefig(f"{outdir}/plot_hops.png", dpi=130)
plt.close(fig)

# --- 4. Reception gaps over time (link continuity / dropouts) ---
gap = buoy["timestamp"].diff().dt.total_seconds()
fig, ax = plt.subplots(figsize=(12, 4.5))
ax.plot(buoy["timestamp"], gap, color="tab:orange", lw=0.8)
ax.scatter(buoy["timestamp"], gap, s=8, color="tab:orange", alpha=0.4)
med = np.nanmedian(gap)
ax.axhline(med, color="gray", ls="--", lw=1,
           label=f"median gap {med:.0f} s")
finish(ax, "Seconds since previous packet", f"Link continuity — gaps between received packets ({session})")
ax.set_xlabel(f"Time of day (buoy clock)\n{NOTE}")
ax.legend(loc="upper left", fontsize=8)
fig.tight_layout()
fig.savefig(f"{outdir}/plot_reception_gaps.png", dpi=130)
plt.close(fig)

# text summary
dur_h = (buoy["timestamp"].iloc[-1] - buoy["timestamp"].iloc[0]).total_seconds() / 3600
print(f"buoy packets: {len(buoy)} over {dur_h:.2f} h")
print(f"SNR  min/mean/max: {buoy['rx_snr'].min():.2f} / {buoy['rx_snr'].mean():.2f} / {buoy['rx_snr'].max():.2f} dB")
print(f"RSSI min/mean/max: {buoy['rx_rssi'].min()} / {buoy['rx_rssi'].mean():.1f} / {buoy['rx_rssi'].max()} dBm")
print(f"hops_used values: {dict(buoy['hops_used'].value_counts())}")
print(f"reception gap median/max: {med:.0f} s / {np.nanmax(gap):.0f} s")
print("Wrote: plot_signal_quality.png, plot_packet_rate.png, plot_hops.png, plot_reception_gaps.png")
