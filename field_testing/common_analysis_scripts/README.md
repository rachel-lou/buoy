# Common field-test analysis scripts

Reusable scripts for processing buoy field tests. Each new test gets its own
folder under `field_testing/` (e.g. `aug29_caspar_cove_1/`); these scripts turn
the raw data pulled off the hardware into the CSVs and plots that live in that
folder. See `../DATA_COLLECTION.md` for how the raw data is pulled off the
buoy SD card / basestation SD card first (all done on a Mac).

There are two independent data streams per test:

- **Buoy sensor data** (temperature / pressure / depth) — comes from `buoy.db`
  (SQLite) on the buoy's SD card.
- **Comms data** (Meshtastic link quality) — comes from the basestation logs on
  the basestation's SD card.

## One-time setup

    python3 -m venv venv
    ./venv/bin/pip install pandas matplotlib requests
    # then call scripts with ./venv/bin/python (examples below assume it on PATH)

`extract_buoy_db.sh` needs only `sqlite3`, which ships with macOS.

## Buoy sensor pipeline

1. **Extract CSVs from the DB** (`sqlite3`):

       ./extract_buoy_db.sh ~/pi_drive/data/buoy.db OUT_DIR START_EPOCH caspar_cove

   Produces `caspar_cove_long.csv` and `caspar_cove_wide.csv`. `START_EPOCH` is
   the deployment (in-water) start; it becomes `elapsed_hr = 0`. Find it the way
   `../DATA_COLLECTION.md` describes (the DB holds many sessions; you want the
   in-water window of the field-trip session). Omit it to anchor at the first
   row instead.

2. **Fetch an ocean-truth reference** from the nearest NDBC station:

       python3 fetch_ndbc.py OUT_DIR/ndbc_46014_wtmp.csv \
           --anchor "2026-08-29 17:00" --station 46014 --hours 13

   `--anchor` is the UTC time matching `elapsed_hr 0` (10:00 PDT == 17:00 UTC).
   Run within ~45 days of the test (NDBC realtime feed window).

3. **Plot temperature vs the reference:**

       python3 plot_temp.py OUT_DIR/caspar_cove_wide.csv \
           OUT_DIR/ndbc_46014_wtmp.csv OUT_DIR/temp_analysis \
           --start "Aug 29, 2026 10:00 PDT"

## Comms pipeline

1. **Pick the right logs and parse them.** Copy the basestation logs off the SD
   card first. Then:

       python3 parse_basestation_logs.py OUT_DIR/connectivity_metrics.csv LOG1 LOG2 ...

   It prints a per-source-node packet count. **This is how you identify the real
   deployment log:** a remote site (e.g. Caspar Cove) has no other nodes nearby,
   so the correct log shows essentially ONE source node (the buoy). A log full of
   many nodes was recorded near a populated mesh and is NOT the deployment —
   discard it. Pass all the contiguous buoy-only logs at once; duplicates across
   a service-restart overlap are de-duped by packet id.

2. **Plot connectivity:**

       python3 plot_comms.py OUT_DIR/connectivity_metrics.csv OUT_DIR/comms_analysis \
           --date "Aug 29, 2026"

   The dominant node is auto-detected (override with `--node`). Pass `--date`
   because the basestation clock in the logs is unreliable.

## Important caveats (apply to every test)

- **Both device clocks are unreliable.** The buoy and the basestation both lack
  a good time source; their absolute timestamps read a day or two off. Trust
  **elapsed/relative time**, not the absolute dates in the logs or CSVs. Anchor
  `elapsed_hr = 0` to the known real deployment start.
- **The buoy temperature sensor drifts** (reads ocean-like at first, then climbs
  well above real seawater — likely air-exposed / self-heating). The NDBC overlay
  is there to show this. Depth's wave motion looks physically real.

## Files

| Script | Language | Purpose |
|--------|----------|---------|
| `extract_buoy_db.sh` | bash + sqlite3 | buoy.db → long + wide CSVs |
| `fetch_ndbc.py` | python | NDBC water-temp reference CSV |
| `plot_temp.py` | python | temperature vs NDBC plots |
| `parse_basestation_logs.py` | python | basestation logs → per-packet CSV (+ node counts) |
| `plot_comms.py` | python | signal quality / packet rate / hops / gaps plots |
