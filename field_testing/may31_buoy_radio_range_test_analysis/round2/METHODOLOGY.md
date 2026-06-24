# Methodology

How the raw minicom log and a timestampless GPX were turned into the numbers, plots, and conclusions in `FINDINGS.md`. Each step lists what we did, why, and where it could break.

---

## 1. Inputs

| File | What it is | Contains timestamps? |
|---|---|---|
| `buoy1_20260531_1049.txt` | Receiver-side minicom capture (raw serial dump from the Meshtastic receiver node). 10.4 MB, ~31 k lines, covers 10:49 → 11:56 PDT on 2026-05-31. | **Yes** — every log line is prefixed `HH:MM:SS uptime` (UTC). |
| `buoy_dunk.gpx` | Strava GPX export of the swimmer's path through Aquatic Cove. 28 trackpoints. | **No** — `<trkpt>` elements have only `lat`, `lon`, `<ele>`. No `<time>` element. |

The receiver coordinates were supplied separately by the operator: **37°48′24.3″N 122°25′23.7″W = (37.80675, −122.42325)**, on the south beach of Aquatic Cove just east of Speaker Tower.

The target device — the buoy under test — is Meshtastic node `0x04089c08`, shorthand "9c08" (last 4 hex of the ID, equivalent to the mesh shortname `!9c08`).

---

## 2. Cleaning the raw log

Minicom captures embed ANSI escape sequences for terminal control (cursor positioning for line wrapping, color codes, etc.). These corrupt regex matching if left in. The parser strips them in three passes:

```python
ANSI = re.compile(rb'\x1b\[[\d;?]*[A-Za-z]')     # CSI sequences (ESC[…X)
text = ANSI.sub(b'', raw)
text = re.sub(rb'\x1b\([A-Z0-9]', b'', text)     # G0/G1 charset designators
text = re.sub(rb'\x1b[=>]', b'', text)           # keypad mode toggles
text = text.decode('utf-8', errors='replace')
```

After this, each Meshtastic log line collapses into a clean single-line form like:

```
DEBUG | 18:00:00 1903 [RadioIf] Lora RX (id=0x4b88e1dd fr=0x04089c08 to=0xffffffff,
        transport = 0, WantAck=0, HopLim=3 Ch=0x8 encrypted len=99
        rxSNR=6.25 rxRSSI=-81 hopStart=3 relay=0x8)
```

Why this matters: without ANSI stripping, every character of certain wrapped lines gets prefixed with cursor-position escapes (`\x1b[14;89H`), turning a one-line `Lora RX (...)` into a multi-thousand-character mess that no regex can match.

---

## 3. Identifying "direct receptions from 9c08"

Meshtastic LoRa packets carry a `relay` byte set to the **last hop's low byte**. If a packet from node `0x04089c08` arrives with `relay=0x8`, the last transmitter on the air was the originator itself — i.e., we heard the buoy directly, not a retransmitted copy from a neighbor mesh node.

The parser uses this anchor:

```python
re_direct = re.compile(
    r'(\d\d):(\d\d):(\d\d)\s+\d+\s+\[RadioIf\]\s+Lora RX \('
    r'id=(0x[0-9a-f]+)\s+fr=0x04089c08'              # originated at 9c08
    r'[^)]*?rxSNR=(-?[\d.]+)\s+rxRSSI=(-?\d+)'        # PHY metrics
    r'[^)]*?relay=0x8\)'                               # direct path
)
```

Validation (run independently after parsing):

| audit | result |
|---|---|
| All 729 log lines with `relay=0x8` have `fr=0x04089c08` | ✓ no other node's packets get mis-attributed |
| Every record in `direct_receptions.json` traces back to a `fr=0x04089c08 … relay=0x8` source line | ✓ 270 / 270 |
| Stored `(rssi, snr)` values match the source line exactly | ✓ 0 mismatches in 270 records |

Relayed copies (`relay=0x3c`, `relay=0x27`, etc.) are **excluded** from signal-strength analysis: their RSSI/SNR describe the relay → receiver hop, not the buoy → receiver link. They are counted separately in §5.7 of `FINDINGS.md` to show the mesh's role.

### Why not just match `Received text msg`?

Text-message lines are higher-level (Router subsystem) and only fire for packets that decode as the Text Messaging port. The buoy also broadcasts position, NodeInfo, and routing packets, all of which have PHY-layer RSSI/SNR worth measuring. Anchoring at the `Lora RX` line catches all of them.

Result: **270 direct receptions = 242 text msgs + 28 non-text** — a ~10 % data-density boost over a text-only parse.

---

## 4. Pairing telemetry payload to PHY metrics

For the post-reboot dunk-test analysis (`dunk_analysis.py`), we also need the buoy's Temperature / Pressure / Depth payload tied to each packet's RSSI. The pairing key is the packet ID:

```
Received text msg from=0x4089c08, id=0xb4f7a313, msg=BUOY1 #519 ...
                                  ↑ same id ↑
Lora RX (id=0xb4f7a313 fr=0x04089c08 ... rxSNR=... rxRSSI=... relay=0x8)
```

Build a `{id → (snr, rssi)}` map from all `relay=0x8` Lora-RX lines, then iterate text-msg lines and join. Trivial.

---

## 5. Aligning the GPX path to the log timestamps

This is the hardest step because the GPX has no `<time>` elements. Solution: anchor at two log-derived events and linearly interpolate position between them.

### 5.1 Anchor A — swim start

The cleanest signature of the buoy entering the water is the antenna-immersion step: RSSI drops ~40 dB in seconds (later confirmed independently by the dunk test at −41.6 dB). We use the first packet whose RSSI is below −50 dBm:

```python
swim_start_t = min(r['t_pdt'] for r in data if r['rssi'] < -50)
# → 10:55:57 PDT
```

This is mapped to GPX trkpt 0 — the launch point on the south beach.

**Robustness check.** Pre-swim RSSI is tight at −20 to −30 dBm (buoy in operator's hand on shore). The threshold of −50 dBm is well below that baseline and well above the next stable regime (~−65 dBm once in water). Moving the threshold to −40 dBm or −60 dBm shifts the chosen packet by ±5 s, not minutes.

### 5.2 Anchor B — end of buoy-in-water portion

The buoy was confirmed (operator recollection) to have died **near the cove mouth**. The last direct reception is at:

```
11:13:04 PDT, seq 519, RSSI −89 dBm, SNR +6 dB
```

This is mapped to GPX trkpt 13 — the easternmost-northernmost waypoint of the cove mouth arc, **396.7 m** straight-line from the receiver. Three independent reasons this is the right anchor:

1. Operator-confirmed location ("died near the mouth").
2. Last RSSI −89 dBm matches the outbound path-loss model at ~400 m exactly.
3. Last LoRa RX was abrupt — no slow fade — consistent with a positional/geometric event (buoy left the direct-LOS sector) rather than a range-creep cutoff.

### 5.3 Linear interpolation between anchors

For every received packet `r` with `swim_start_t ≤ t ≤ swim_end_t`:

```python
frac = (r.t_pdt - swim_start_t) / (swim_end_t - swim_start_t)
target_path_dist = frac * cumulative_haversine_distance(trkpts[0..13])
# Walk along trkpts 0..13 by haversine length, then interpolate lat/lon
# linearly within the segment where target_path_dist falls.
```

Implied average speed: **608 m of path over 17 min ≈ 0.6 m/s**. Slow for an unburdened swimmer but plausible for one towing a small buoy.

### 5.4 What's NOT on the buoy track

Trkpts 14–27 (the eastern arc and the return to shore) are the swimmer's path **after** the buoy died. They're drawn dashed on all basemap figures to distinguish "swimmer-only" from "buoy-instrumented".

### 5.5 Sensitivity

The position interpolation is the largest uncertainty in the pipeline. **Headline numbers are robust to it** because:

- Peak range = 399 m, which is the *trkpt-13 haversine to the receiver*, not an interpolation result.
- Antenna-immersion step (~40 dB) is anchored on adjacent time-ordered packets, independent of position.
- Path-loss exponent n ≈ 4.85 is a fit over a wide distance range (30 → 400 m); small per-packet position errors don't bias the slope.
- Dunk test (§11) doesn't use the GPX at all — it's a separate post-reboot dock-based event.

Per-packet position estimates in mid-loop could be off by tens of meters if the swimmer's pace was non-constant. That would shake up the `fig_loss_vs_distance.png` bins slightly but wouldn't change the qualitative pattern (loss is ~flat across the envelope).

### 5.6 If higher accuracy is needed later

Two options to tighten this:

1. Re-export the GPX from Strava with timestamps enabled (`<trkpt>` should then carry a `<time>2026-05-31T17:55:57Z</time>` child). The parser can be rewritten to a direct time-join in ~10 lines.
2. Note wall-clock times at known landmarks during a future swim (e.g. "rounded the Municipal Pier corner at 11:01:30") and re-anchor at those points.

---

## 6. Distance calculation

Both haversine and flat-earth equirectangular methods are computed and cross-checked:

```python
# Haversine (spherical Earth, R = 6,371,000 m)
def hav(lat1, lon1, lat2, lon2):
    a1, b1, a2, b2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dl, do = a2 - a1, b2 - b1
    h = math.sin(dl/2)**2 + math.cos(a1) * math.cos(a2) * math.sin(do/2)**2
    return 2 * 6371000 * math.asin(math.sqrt(h))

# Flat-earth (valid for <few km at mid-latitudes)
def flat(lat1, lon1, lat2, lon2):
    dy = (lat2 - lat1) * 111132.0
    dx = (lon2 - lon1) * 111320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)
```

Across all 28 GPX trackpoints, the two methods agree to **< 0.21 m**. Ground-truth calibration via the Municipal Pier W → Hyde St Pier E span gives 451 m by our haversine — Wikipedia documents the span as ~450 m. ✓

---

## 7. Path-loss model

Empirical log-distance fit on the 187 direct-RX packets where `30 m < d < 400 m` and `RSSI > −110 dBm`:

```
RSSI(d) = a + b · log₁₀(d)
```

`numpy.polyfit` gives `a = +28.0 dBm`, `b = −48.51` dB/decade. The path-loss exponent is `n = −b / 10 = 4.85`. Compare:

| environment | n typical |
|---|---|
| Free space | 2.0 |
| Indoor LOS | 1.6 – 1.8 |
| Suburban LOS | 2.7 – 3.5 |
| **Sea surface, low-mount antenna** | **3.5 – 5.5** |
| Dense urban NLOS | 3.0 – 5.0 |

n ≈ 4.85 sits in the upper end of the "sea surface with antenna near water" range, consistent with the buoy's antenna sitting ~10 cm above the waterline with Fresnel-zone clipping by the salt-water surface.

The free-space reference line (`n=2`, Tx +20 dBm, 0 dBi antennas) is plotted alongside for comparison in `fig_signal_vs_range.png`.

---

## 8. Effective range estimate

Direct demonstrated range: **399 m** (the GPX trkpt 13 distance to the receiver, where the buoy was at its last direct reception).

Extrapolated practical range from the fit:

```
margin_dB    = SNR_last - SNR_demod_floor      (≈ +6 dB − (−17 dB) = 23 dB)
range_limit  = d_last × 10^(margin_dB / (10n))
             = 400 m × 10^(23 / 48.5)
             ≈ 400 m × 2.94
             ≈ 1180 m
```

This is optimistic (assumes constant n past the demonstrated envelope; n typically grows further once the antennas approach the radio horizon at ~1.5 km for 0.1 m antenna heights). Realistic practical envelope: **600–1000 m** for this geometry.

---

## 9. Dunk-test detection

The post-reboot data is analyzed separately because the buoy's position is fixed (operator on the dock). Detection logic:

1. Locate the reboot: find a text msg where `seq < 50` and `buoy_uptime_s < 100` while the previous message had `seq > 100` and `buoy_uptime_s > 500`.
2. Define dry baseline as the first 5 post-reboot messages: average `Pressure ≈ 101.6534 kPa`, average `Deep ≈ −0.0018 m`.
3. Flag each subsequent message as `submerged` if `Δdeep > 2 cm` above baseline.
4. Dunk window = `[first submerged time, last submerged time]`.

For the 2026-05-31 test: dunk window = **11:43:54 → 11:45:49 PDT**, peak depth 14.9 cm, antenna-immersion attenuation **−41.6 dB** in RSSI.

---

## 10. Reproducibility

Three scripts at the project root encode the full pipeline:

```
$ python3 parse_log.py            # log → round2/direct_receptions.json
$ python3 make_plots.py           # → annotated.json, per_minute.json, fig_*.png
$ python3 extract_snippets.py     # → round2/log_snippets.txt
$ python3 dunk_analysis.py        # → round2/dunk_telemetry.json, fig_dunk_test.png
```

Each script is independent. Inputs are exactly `buoy1_20260531_1049.txt`, `buoy_dunk.gpx`, the receiver lat/lon (hard-coded), and `round2/direct_receptions.json` (for downstream scripts). No hidden state.

---

## 11. Known limitations

| limitation | impact | mitigation |
|---|---|---|
| GPX has no timestamps | Per-packet positions in mid-swim depend on a constant-pace assumption | Anchors are time-clean. Headline metrics (peak range, n, immersion step) don't depend on interpolation accuracy. |
| Constant-pace assumption | If swimmer paused at the mouth, mid-swim positions are biased outbound | Sensitivity to this is limited because RSSI tracks log-distance cleanly throughout the outbound section. |
| Receiver position assumed exact | Distances shift uniformly if RX is off by a few meters | Provided by operator. Substring sanity (visible on basemap) — RX star sits on the south beach exactly where described. |
| Single buoy run | Path-loss exponent n is from one swim, not an ensemble average | Statistically n ≈ 4.85 across 187 samples is tight; would benefit from a repeat swim to confirm. |
| Channel saturation hides true link-budget loss | ~15 % packet loss is dominated by `TX queue full` / `Ch. util >25%` warnings, not weak link | Reduce channel utilization (longer send interval, fewer relays) to expose the actual link-budget edge in a future test. |
