#!/usr/bin/env bash
# Extract long + wide CSVs from a buoy SQLite database (readings table).
#
# Usage:
#   ./extract_buoy_db.sh DB_PATH OUT_DIR [START_EPOCH] [PREFIX]
#
#   DB_PATH      path to buoy.db (e.g. ~/pi_drive/data/buoy.db while the SD card
#                is mounted, or a local copy)
#   OUT_DIR      where the CSVs are written
#   START_EPOCH  unix epoch of deployment start = elapsed_hr 0. Optional; if
#                omitted, the earliest real (>1e9) timestamp in the DB is used.
#                For a real field test set this to the in-water start so
#                elapsed_hr lines up with the deployment (see DATA_COLLECTION.md
#                for how the window was found).
#   PREFIX       output filename prefix (default: "readings")
#
# Writes:  OUT_DIR/PREFIX_long.csv   one row per (timestamp, sensor)
#          OUT_DIR/PREFIX_wide.csv   one row per reading cycle, sensors in columns
#
# The DB is opened read-only. Run on macOS (see DATA_COLLECTION.md) or Linux;
# only sqlite3 is required.
set -euo pipefail

DB_PATH=${1:?need DB_PATH}
OUT_DIR=${2:?need OUT_DIR}
START_EPOCH=${3:-}
PREFIX=${4:-readings}

command -v sqlite3 >/dev/null || { echo "sqlite3 not found (macOS ships it; else: brew install sqlite)"; exit 1; }
mkdir -p "$OUT_DIR"
DB="file:${DB_PATH}?mode=ro"

if [[ -z "$START_EPOCH" ]]; then
  START_EPOCH=$(sqlite3 "$DB" "SELECT CAST(MIN(timestamp) AS INT) FROM readings WHERE timestamp>1000000000;")
  echo "START_EPOCH not given; using earliest DB timestamp: $START_EPOCH"
fi

LONG="$OUT_DIR/${PREFIX}_long.csv"
WIDE="$OUT_DIR/${PREFIX}_wide.csv"

# 1) Long format (raw slice, as stored)
sqlite3 -header -csv "$DB" "
SELECT id,
       timestamp AS raw_epoch,
       datetime(timestamp,'unixepoch') AS buoy_datetime_utc,
       ROUND(timestamp-$START_EPOCH,2) AS elapsed_s,
       ROUND((timestamp-$START_EPOCH)/3600.0,4) AS elapsed_hr,
       sensor, value, unit, quality_flag
FROM readings WHERE timestamp>=$START_EPOCH ORDER BY id;" > "$LONG"

# 2) Wide format (one row per reading cycle; pivot the buoy's three sensors)
sqlite3 -header -csv "$DB" "
SELECT raw_epoch,
       datetime(raw_epoch,'unixepoch') AS buoy_datetime_utc,
       ROUND(raw_epoch-$START_EPOCH,2) AS elapsed_s,
       ROUND((raw_epoch-$START_EPOCH)/3600.0,4) AS elapsed_hr,
       temp_c, temp_qf, pressure_mbar, pressure_qf, depth_m, depth_qf
FROM (
  SELECT timestamp AS raw_epoch,
    MAX(CASE WHEN sensor='temperature' THEN value END) temp_c,
    MAX(CASE WHEN sensor='temperature' THEN quality_flag END) temp_qf,
    MAX(CASE WHEN sensor='pressure' THEN value END) pressure_mbar,
    MAX(CASE WHEN sensor='pressure' THEN quality_flag END) pressure_qf,
    MAX(CASE WHEN sensor='depth' THEN value END) depth_m,
    MAX(CASE WHEN sensor='depth' THEN quality_flag END) depth_qf
  FROM readings WHERE timestamp>=$START_EPOCH GROUP BY timestamp
) ORDER BY raw_epoch;" > "$WIDE"

echo "Wrote:"
echo "  $LONG  ($(($(wc -l < "$LONG")-1)) rows)"
echo "  $WIDE  ($(($(wc -l < "$WIDE")-1)) rows)"
echo "elapsed_hr is anchored to START_EPOCH=$START_EPOCH ($(date -r "$START_EPOCH" -u '+%Y-%m-%d %H:%M UTC' 2>/dev/null || echo epoch))"
echo "NOTE: the buoy clock is unreliable; trust elapsed_hr, not buoy_datetime_utc."
