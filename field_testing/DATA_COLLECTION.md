# How field-test data is pulled off the hardware

All of this was done on a **Mac** (macOS). The buoy and the basestation each run
on a Raspberry Pi and store data on a **microSD card with a Linux ext4 root
filesystem**. macOS cannot read ext4 natively, so the cards are mounted through
FUSE. This note records the exact process so it's repeatable.

The processing that turns this raw data into the CSVs and plots inside each test
folder is documented in `common_analysis_scripts/README.md`.

## What's on the cards

- **Buoy SD card** — the sensor database: `buoy.db` (SQLite), at
  `data/buoy.db` on the card. Table `readings` with columns:
  `id, timestamp (unix epoch), sensor, value, unit, quality_flag`. Sensors:
  `temperature` (C), `pressure` (mbar), `depth` (m).
- **Basestation SD card** — the Meshtastic logs, at
  `var/log/basestation/basestation_YYYYMMDD_HHMMSS.log`. One deployment can span
  several files (the service restarts). The filename timestamp comes from the
  Pi's unreliable clock — do not trust it (see caveat below).

## Mounting an ext4 SD card on macOS

Prerequisite (one time):

    brew install macfuse
    # ext4fuse is not in a working bottle; build it from source:
    git clone https://github.com/gerard/ext4fuse.git ~/ext4fuse
    cd ~/ext4fuse && make
    # macFUSE also needs its system extension approved once in
    # System Settings > Privacy & Security (a reboot may be required).

Each time you plug a card in:

    diskutil list                      # find the card's disk (e.g. /dev/disk6)
                                       # the ext4 root shows as TYPE "Linux",
                                       # usually partition slice 2 (disk6s2);
                                       # the small FAT "bootfs" slice auto-mounts.
    mkdir -p ~/pi_drive
    sudo ~/ext4fuse/ext4fuse /dev/disk6s2 ~/pi_drive -o allow_other

Notes:
- The device node (`disk6s2`) changes depending on what else is plugged in —
  always re-check with `diskutil list`.
- `ext4fuse` mounts **read-only**, which is what we want (no risk of corrupting
  the card).
- The mount can be slow / occasionally stall on large reads; if a command hangs,
  give it a moment rather than killing it mid-read.

After mounting, the data is at:

    ~/pi_drive/data/buoy.db                 # buoy sensor DB
    ~/pi_drive/var/log/basestation/*.log    # basestation comms logs

When done, unmount before pulling the card:

    sudo umount ~/pi_drive

## Pulling the data

- **Basestation logs:** copy the relevant `.log` files into the test folder
  (e.g. into a `basestation_logs/` subfolder). Which logs are the real
  deployment is decided by node counts — see
  `common_analysis_scripts/README.md` (remote sites should show only the buoy
  node).
- **Buoy DB → CSV:** run `common_analysis_scripts/extract_buoy_db.sh` against
  `~/pi_drive/data/buoy.db`. It uses `sqlite3` (bundled with macOS) and opens the
  DB read-only. The DB accumulates many logging sessions (bench/idle runs plus
  the field trip); pick the in-water window of the field-trip session and pass
  its start epoch so `elapsed_hr` lines up with the deployment.

## Caveat: unreliable clocks

Neither the buoy nor the basestation has a reliable time source, so the absolute
timestamps in `buoy.db` and in the log filenames/lines read a day or two off the
real date (e.g. the Aug 29 2026 Caspar Cove test records as ~Aug 28). **Do not
trust absolute dates from the hardware.** Anchor analysis to the known real
deployment start and use elapsed/relative time.
