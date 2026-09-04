Caspar Cove field test #1
=========================
Field test date: Aug 29, 2026 (started ~10:00 PDT, ~12 h in water).
Buoy sensor data extracted from buoy.db (readings table) on 2026-09-01.
Basestation comms logs pulled from the basestation SD card on 2026-09-03.

NOTE ON THE DATE: earlier drafts of this analysis labeled the test Aug 30. That
was wrong; the field test was Aug 29. Both the buoy sensor clock and the
basestation (Meshtastic) clock are unreliable and read ~Aug 28 in the raw data.
Absolute timestamps in the logs/CSVs are NOT ground truth. Use relative/elapsed
time as the trustworthy axis.

WHAT'S IN THIS FOLDER
  caspar_cove_readings_long.csv  - raw buoy readings, one row per (timestamp, sensor). 4491 rows.
  caspar_cove_wide.csv           - one row per 30 s reading cycle, sensors in columns. 1497 rows.
  ndbc_46014_wtmp.csv            - NDBC buoy 46014 water temp, Aug 29 window, as ocean reference.
  connectivity_metrics.csv       - one row per received Meshtastic packet (1141 rows). See COMMS.
  basestation_logs/              - the raw basestation logs this analysis is built from.
  comms_analysis/                - Meshtastic connectivity plots + the script that makes them.
  temp_analysis/                 - temperature plots + the script that makes them.


COMMS ANALYSIS (Meshtastic connectivity)
----------------------------------------
WHICH LOG IS THE REAL TEST
  The SD card holds five basestation logs. Caspar Cove is a remote site with NO
  other Meshtastic nodes nearby, so the deployment log should show essentially
  only the buoy's node (0x04089c08, "Meshtastic 9c08"). Counting unique source
  nodes per log settles which files belong to the test:

    log (buoy-clock span)          RX pkts   unique nodes   verdict
    105806  10:58-11:30               170      dozens        near town - NOT the test
    112718  11:27-11:57                41       1 (buoy)     Caspar Cove  <-- included
    115740  11:57-20:34              1191       1 (buoy)     Caspar Cove  <-- included
    203202  20:32-03:54(+1d)         1005      dozens        back near town - NOT the test

  A previous draft used 203202, which is full of other nodes - i.e. the
  basestation after it had returned near town, NOT the remote deployment. This
  version uses the two contiguous buoy-only logs (112718 + 115740), which is the
  actual in-water Caspar Cove period.

  basestation_logs/ contains those two logs. connectivity_metrics.csv is parsed
  from both, de-duplicated by Meshtastic packet id across the restart overlap
  (89 duplicates dropped). 1141 packets total: 1138 from the buoy, 3 stray
  packets from one far node on a different channel (0x1f vs the buoy's 0x6f) -
  negligible, effectively a single-link test.

HEADLINE RESULTS
  Received packets : 1141 over ~9.1 h of basestation logging
  Link path        : 100% direct (hops_used = 0 for every buoy packet; no relays)
  Signal (buoy)    : SNR min/mean/max = -15.5 / 9.2 / 12.3 dB
                     RSSI min/mean/max = -126 / -46 / -6 dBm
  Reliability      : median gap between packets = 30 s (== the buoy's send
                     cadence, so nearly every scheduled packet was received);
                     largest single dropout ~22 min.
  The signal-quality plot shows two clear excursions (~13:00 and ~18:00 buoy
  clock) where SNR/RSSI dropped hard (RSSI to ~-120 dBm) - consistent with
  range-testing / the buoy drifting far - with strong signal the rest of the time.

COMMS PLOTS (comms_analysis/)
  plot_signal_quality.png  - rx SNR & RSSI over time (points + 5-min average).
  plot_packet_rate.png     - packets received per minute over time.
  plot_hops.png            - hop count over time (all 0 = direct link).
  plot_reception_gaps.png  - seconds between consecutive packets (link continuity).
  Regenerate:  python3 comms_analysis/plot_conn.py connectivity_metrics.csv comms_analysis


BUOY SENSOR DATA (temperature / depth)
--------------------------------------
DEPLOYMENT WINDOW (the in-water portion only, buoy-clock times - unreliable)
  Buoy-clock time : 2026-08-28 21:05:03  ->  2026-08-29 09:25:52  (~12.35 h)
  Sample cadence  : 30 s
  Sensors present : temperature (C), pressure (mbar), depth (m)
  Quality flags   : 1488 good (flag 0) + 9 flagged (flag 2) per sensor

TIME COLUMNS
  raw_epoch          - Unix epoch seconds exactly as stored in the DB.
  buoy_datetime_utc  - raw_epoch decoded. NOTE: the buoy clock is UNRELIABLE. It
                       reads ~Aug 28-29 but the field test was Aug 29; treat
                       absolute dates as offset. Do not trust them as ground truth.
  elapsed_s / elapsed_hr - seconds / hours since deployment start (21:05:03). This
                       is reliable relative time and is the recommended x-axis.

HOW THIS WAS SEPARATED FROM OTHER TESTS
  buoy.db holds ~14 days of data across 8 distinct logging sessions. Seven of them
  (Aug 13-26) are bench/idle runs: 10 s cadence, dead-flat depth (variance ~1e-5),
  not in water. They are NOT included here.
  The 8th session (30 s cadence) is this field trip. Its first ~15 h was the buoy
  powered on but out of water (flat depth, warm temp) - staging/transport - and is
  excluded. Only the in-water window above is included.

TEMP PLOTS (temp_analysis/)
  plot_temp_vs_ndbc.png    - buoy temp vs NDBC 46014 over the full deployment.
  plot_temp_early_zoom.png - first 2.5 h, where the buoy still reads ocean-like.
  t=0 is anchored to Aug 29, 2026 10:00 PDT (== 17:00 UTC == NDBC elapsed_hr 0).
  Regenerate:  python3 temp_analysis/plot_temp.py

NOTES / CAVEATS
  - Cadence is 30 s here (bench sessions were 10 s).
  - Temperature looks unreliable: it starts ocean-like (~12-16 C, matching nearby
    NDBC buoy 46014 at ~12-13 C) but then climbs to 24-29 C over the deployment,
    which real seawater does not do. Likely air-exposed / self-heating /
    uncalibrated. Depth's wave motion looks physically real.
  - Depth sits near the surface (mean ~ -0.03 m) with dips to -0.5 m from wave action.
