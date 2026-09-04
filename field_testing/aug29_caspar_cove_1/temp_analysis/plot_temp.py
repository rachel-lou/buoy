#!/usr/bin/env python3
"""
Temperature charts for Caspar Cove field test #1, with NDBC 46014 reference.

X-axis is elapsed_hr since deployment start (README: the buoy clock is
unreliable; elapsed time is the trustworthy axis). Deployment start is anchored
to 10:00 AM PDT on Aug 29, 2026 == 17:00 UTC, which is also elapsed_hr=0 for the
NDBC series (see ndbc_46014_wtmp.csv).

Run:  python3 plot_temp.py
"""
import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
buoy = pd.read_csv(os.path.join(ROOT, "caspar_cove_wide.csv"))
ndbc = pd.read_csv(os.path.join(ROOT, "ndbc_46014_wtmp.csv"))

# keep only good-quality temperature samples (qf 0); qf 2 rows are zero-filled
good = buoy[buoy["temp_qf"] == 0].copy()

START_LABEL = "Aug 29, 2026 10:00 PDT"

# ---- Chart 1: buoy temp vs NDBC 46014, full deployment ----
fig, ax = plt.subplots(figsize=(12, 5.5))
ax.plot(good["elapsed_hr"], good["temp_c"], color="tab:red", lw=1.2,
        label="Buoy temp sensor (Caspar Cove)")
ax.plot(ndbc["elapsed_hr"], ndbc["wtmp_c"], color="tab:blue", lw=2,
        marker="o", ms=4, label="NDBC 46014 water temp (reference)")
ax.set_xlabel(f"Hours since deployment  (t=0 : {START_LABEL})")
ax.set_ylabel("Temperature (°C)")
ax.set_title("Caspar Cove field test #1 — buoy temperature vs NDBC 46014")
ax.grid(True, alpha=0.3)
ax.legend(loc="upper left")
ax.annotate("NDBC ground truth stays ~12–13 °C;\nbuoy sensor drifts upward "
            "(likely air-exposed / self-heating — see README)",
            xy=(0.98, 0.04), xycoords="axes fraction", ha="right", va="bottom",
            fontsize=8, color="dimgray",
            bbox=dict(boxstyle="round", fc="white", ec="lightgray", alpha=0.8))
fig.tight_layout()
fig.savefig(os.path.join(HERE, "plot_temp_vs_ndbc.png"), dpi=130)
plt.close(fig)

# ---- Chart 2: zoom on the first 2.5 h where the buoy still reads ocean-like ----
early = good[good["elapsed_hr"] <= 2.5]
ndbc_early = ndbc[ndbc["elapsed_hr"] <= 2.5]
fig, ax = plt.subplots(figsize=(11, 5))
ax.plot(early["elapsed_hr"], early["temp_c"], color="tab:red", lw=1.2,
        label="Buoy temp sensor")
ax.plot(ndbc_early["elapsed_hr"], ndbc_early["wtmp_c"], color="tab:blue", lw=2,
        marker="o", ms=5, label="NDBC 46014")
ax.set_xlabel(f"Hours since deployment  (t=0 : {START_LABEL})")
ax.set_ylabel("Temperature (°C)")
ax.set_title("First 2.5 h — buoy reads ocean-like before drifting")
ax.grid(True, alpha=0.3)
ax.legend(loc="upper left")
fig.tight_layout()
fig.savefig(os.path.join(HERE, "plot_temp_early_zoom.png"), dpi=130)
plt.close(fig)

print("Wrote:")
print("  plot_temp_vs_ndbc.png")
print("  plot_temp_early_zoom.png")
print(f"buoy good samples: {len(good)} | temp range {good['temp_c'].min():.1f}"
      f"–{good['temp_c'].max():.1f} °C")
print(f"NDBC 46014 range: {ndbc['wtmp_c'].min()}–{ndbc['wtmp_c'].max()} °C")
