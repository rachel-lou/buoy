#!/usr/bin/env python3
"""Fetch NDBC water temperature for a time window, as an ocean-truth reference.

    fetch_ndbc.py OUT.csv --anchor "2026-08-29 17:00" [--station 46014] [--hours 13]

--anchor is the UTC time that becomes elapsed_hr 0 (align it with the buoy
deployment start; e.g. 10:00 PDT == 17:00 UTC). Output columns:
utc_datetime, elapsed_hr, wtmp_c.

Uses NDBC's realtime2 feed, which holds ~45 days of recent data, so run this
within a few weeks of the test. Station 46014 is the reference used for Caspar
Cove; pick the nearest station to your site.
"""
import argparse, sys, urllib.request
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--anchor", required=True, help='UTC time for elapsed_hr 0, e.g. "2026-08-29 17:00"')
ap.add_argument("--station", default="46014")
ap.add_argument("--hours", type=float, default=13.0)
args = ap.parse_args()

URL = f"https://www.ndbc.noaa.gov/data/realtime2/{args.station}.txt"
ANCHOR = pd.Timestamp(args.anchor, tz="UTC")

req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
raw = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")

lines = raw.splitlines()
cols = lines[0].lstrip("#").split()          # line 0: column names (leading #)
data = [l.split() for l in lines[2:] if l.strip() and not l.startswith("#")]  # line 1: units
df = pd.DataFrame(data, columns=cols)

df["dt"] = pd.to_datetime(
    df["YY"] + "-" + df["MM"] + "-" + df["DD"] + " " + df["hh"] + ":" + df["mm"],
    format="%Y-%m-%d %H:%M", utc=True, errors="coerce")
df["WTMP"] = pd.to_numeric(df["WTMP"], errors="coerce")
df = df[(df["WTMP"] < 99) & df["WTMP"].notna()]

end = ANCHOR + pd.Timedelta(hours=args.hours)
win = df[(df["dt"] >= ANCHOR) & (df["dt"] <= end)].sort_values("dt")
if win.empty:
    print(f"NO DATA in window. Feed covers {df['dt'].min()} -> {df['dt'].max()} "
          f"(realtime2 keeps ~45 days).", file=sys.stderr)
    sys.exit(1)

out = pd.DataFrame({
    "utc_datetime": win["dt"].dt.strftime("%Y-%m-%d %H:%M"),
    "elapsed_hr": ((win["dt"] - ANCHOR).dt.total_seconds() / 3600).round(2),
    "wtmp_c": win["WTMP"].round(1),
})
out.to_csv(args.out, index=False)
print(f"Wrote {len(out)} rows to {args.out}")
print(f"station {args.station}  window {ANCHOR} -> {end}")
print(f"WTMP range {out['wtmp_c'].min()}-{out['wtmp_c'].max()} C")
