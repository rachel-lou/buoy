#!/usr/bin/env bash
# Links the buoy code into the (unmodified) Meshtastic submodule. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

git submodule update --init --recursive meshtastic  # checks out the pinned release tag

ln -sfn ../../../variant/heltec_v3_buoy meshtastic/variants/esp32s3/heltec_v3_buoy
mkdir -p meshtastic/src/modules/optional
ln -sfn ../../../../module/Buoy meshtastic/src/modules/optional/Buoy

# Keep the symlinks out of the submodule's `git status`.
exclude="$(git -C meshtastic rev-parse --path-format=absolute --git-path info/exclude)"
mkdir -p "$(dirname "$exclude")"
for p in /variants/esp32s3/heltec_v3_buoy /src/modules/optional/; do
    grep -qxF "$p" "$exclude" || echo "$p" >> "$exclude"
done

echo "Linked. Build with: cd firmware/meshtastic && pio run -e heltec-v3-buoy"
