#!/usr/bin/env python3
"""Temperature charts for a buoy deployment, with an NDBC reference overlay.

    plot_temp.py WIDE_CSV NDBC_CSV OUTDIR --start "Aug 29, 2026 10:00 PDT"

WIDE_CSV  is the wide buoy CSV from extract_buoy_db.sh (needs temp_c, temp_qf,
          elapsed_hr).
NDBC_CSV  is from fetch_ndbc.py (elapsed_hr, wtmp_c), anchored to the same t=0.

X-axis is elapsed_hr since deployment start (the buoy clock is unreliable;
elapsed time is the trustworthy axis). Writes plot_temp_vs_ndbc.png and
plot_temp_early_zoom.png into OUTDIR.
"""
import argparse
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("wide_csv")
ap.add_argument("ndbc_csv")
ap.add_argument("outdir")
ap.add_argument("--start", default="deployment start", help='t=0 label, e.g. "Aug 29, 2026 10:00 PDT"')
args = ap.parse_args()

buoy = pd.read_csv(args.wide_csv)
ndbc = pd.read_csv(args.ndbc_csv)
good = buoy[buoy["temp_qf"] == 0].copy()  # qf 2 rows are zero-filled
START = args.start

# Chart 1: full deployment
fig, ax = plt.subplots(figsize=(12, 5.5))
ax.plot(good["elapsed_hr"], good["temp_c"], color="tab:red", lw=1.2,
        label="Buoy temp sensor")
ax.plot(ndbc["elapsed_hr"], ndbc["wtmp_c"], color="tab:blue", lw=2, marker="o", ms=4,
        label="NDBC water temp (reference)")
ax.set_xlabel(f"Hours since deployment  (t=0 : {START})")
ax.set_ylabel("Temperature (°C)")
ax.set_title("Buoy temperature vs NDBC reference")
ax.grid(True, alpha=0.3)
ax.legend(loc="upper left")
fig.tight_layout(); fig.savefig(f"{args.outdir}/plot_temp_vs_ndbc.png", dpi=130); plt.close(fig)

# Chart 2: first 2.5 h zoom
early = good[good["elapsed_hr"] <= 2.5]
ndbc_early = ndbc[ndbc["elapsed_hr"] <= 2.5]
fig, ax = plt.subplots(figsize=(11, 5))
ax.plot(early["elapsed_hr"], early["temp_c"], color="tab:red", lw=1.2, label="Buoy temp sensor")
ax.plot(ndbc_early["elapsed_hr"], ndbc_early["wtmp_c"], color="tab:blue", lw=2, marker="o", ms=5,
        label="NDBC reference")
ax.set_xlabel(f"Hours since deployment  (t=0 : {START})")
ax.set_ylabel("Temperature (°C)")
ax.set_title("First 2.5 h")
ax.grid(True, alpha=0.3)
ax.legend(loc="upper left")
fig.tight_layout(); fig.savefig(f"{args.outdir}/plot_temp_early_zoom.png", dpi=130); plt.close(fig)

print("Wrote plot_temp_vs_ndbc.png, plot_temp_early_zoom.png")
print(f"buoy good samples: {len(good)} | temp range {good['temp_c'].min():.1f}-{good['temp_c'].max():.1f} °C")
print(f"NDBC range: {ndbc['wtmp_c'].min()}-{ndbc['wtmp_c'].max()} °C")
