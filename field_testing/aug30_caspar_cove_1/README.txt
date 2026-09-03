Caspar Cove field test #1
=========================
Extracted from buoy.db (readings table) on 2026-09-01.

WHAT'S IN THIS FOLDER
  caspar_cove_readings_long.csv  - raw readings, one row per (timestamp, sensor). 4491 rows.
  caspar_cove_wide.csv           - one row per 30 s reading cycle, sensors in columns. 1497 rows.

DEPLOYMENT WINDOW (the in-water portion only)
  Buoy-clock time : 2026-08-28 21:05:03  ->  2026-08-29 09:25:52  (~12.35 h)
  Sample cadence  : 30 s
  Sensors present : temperature (C), pressure (mbar), depth (m)
  Quality flags   : 1488 good (flag 0) + 9 flagged (flag 2) per sensor

TIME COLUMNS
  raw_epoch          - Unix epoch seconds exactly as stored in the DB.
  buoy_datetime_utc  - raw_epoch decoded. NOTE: the buoy clock is UNRELIABLE. It reads
                       ~Aug 28-29 but the field test was Aug 30; treat absolute dates as
                       offset by a day or two. Do not trust them as ground truth.
  elapsed_s / elapsed_hr - seconds / hours since deployment start (21:05:03). This is
                       reliable relative time and is the recommended x-axis.

HOW THIS WAS SEPARATED FROM OTHER TESTS
  buoy.db holds ~14 days of data across 8 distinct logging sessions. Seven of them
  (Aug 13-26) are bench/idle runs: 10 s cadence, dead-flat depth (variance ~1e-5),
  not in water. They are NOT included here.
  The 8th session (30 s cadence) is this field trip. Its first ~15 h (Aug 28 ~06:00-21:00)
  was the buoy powered on but out of water (flat depth, warm temp) - staging/transport -
  and is excluded. Only the in-water window above is included.

NOTES / CAVEATS
  - Cadence is 30 s here (bench sessions were 10 s).
  - Temperature looks unreliable: it starts ocean-like (~13-16 C, matching nearby NDBC
    buoy 46014 at 12-15 C) but then climbs to 24-28 C over the deployment, which real
    seawater does not do. Likely air-exposed / self-heating / uncalibrated. Depth's
    wave motion looks physically real.
  - Depth sits near the surface (mean ~ -0.03 m) with dips to -0.5 m from wave action.
