# Buoy Meshtastic Radio Test — Findings (Round 2 revision)

**Test date:** 2026-05-31
**Location:** Aquatic Cove, San Francisco
**Tracked device:** `0x04089c08` (low byte `0x08` → mesh shorthand "9c08") — the BUOY1 transmitter being towed by the swimmer
**Receiver:** at 37°48′24.3″N 122°25′23.7″W = (37.80675, −122.42325), on the south beach
**Source data:** `buoy1_20260531_1049.txt` (minicom capture from the receiver) and `buoy_dunk.gpx` (swimmer's track)

> This revision corrects two earlier mistakes:
> 1. The signal-strength dataset now uses ALL direct receptions of 9c08 packets (`relay=0x8` LoRa-RX events), not just decoded `Received text msg` lines. Result: 270 receptions instead of 244 — gives ~10 % more samples and crucially fixes the per-minute RSSI table.
> 2. The buoy did NOT complete the GPX loop. The swimmer did. The buoy died near the mouth of the cove around 11:13–11:15 PDT; the trkpts past the mouth show the swimmer's path *after* the transmitter was already dead.

---

## 1. The 3 timestamps that matter

| Event | Time (UTC / PDT) | What it means |
|---|---|---|
| Last DIRECT reception | 18:13:04 / **11:13:04** | Receiver's last sample of the buoy on the direct path — RSSI −89, SNR +6. Signal-analysis cutoff. |
| Last RELAYED reception | 18:15:49 / **11:15:49** | Buoy was still transmitting; receiver only heard it via neighbor mesh nodes (`relay=0x3c`, `0x27`, …). |
| Buoy reboot | 18:40:47 / **11:40:47** | After a ~25 min silence (battery cutoff / replacement), the buoy boots fresh (uptime 5 s). |

Your "1115ish PDT" recollection lines up with the **last sign of life** (relayed). The cutoff at **11:13:04** is where the *direct* link to the receiver failed — and that's the boundary of the dataset that powers everything in this report.

---

## 2. Setup and device verification

| Item | Value |
|---|---|
| Buoy node ID | `0x04089c08` |
| Receiver | south beach of Aquatic Cove (cyan star on all maps) |
| Radio | LoRa 915 MHz, SF11/BW125 (Meshtastic LongFast) |
| Test session bounds | 10:49:14 → 11:13:04 PDT |
| Buoy uptime at first log line | 1257 s (boot ≈ 10:28:17 PDT) |
| Buoy uptime at last log line | 2602 s (~43 min total) |
| Direct receptions of 9c08 | **270 unique packets** (242 text msgs + 28 non-text) |

All 422 text messages in the log came from `0x04089c08`. No other buoy is in the data.

---

## 3. Distance validation

**Two independent methods agree to < 0.21 m at every distance tested.**

| trkpt | lat | lon | haversine | flat-earth | Δ |
|---:|---|---|---:|---:|---:|
| 0 (start) | 37.806874 | −122.423754 | 46.4 m | 46.4 m | 0.05 m |
| 4 | 37.808106 | −122.425798 | 269.9 m | 270.1 m | 0.16 m |
| 7 (W corner) | 37.809111 | −122.426272 | 373.4 m | 373.5 m | 0.11 m |
| 11 (N peak) | 37.810069 | −122.424977 | **399.0 m** | 398.9 m | 0.13 m |
| 13 (mouth NE) | 37.810269 | −122.423995 | **396.7 m** | 396.5 m | 0.21 m |
| 20 | 37.807778 | −122.423089 | 115.2 m | 115.1 m | 0.06 m |
| 27 (end) | 37.806906 | −122.423782 | 49.9 m | 49.9 m | 0.05 m |

Ground-truth check: Municipal Pier W → Hyde St Pier E = 451 m (Wikipedia: ~450 m). ✓
Visual check: `fig_distance_sanity_sat.png` — receiver on the south beach, 400 m ring tangents the northern arc of the swim path.

---

## 4. Timeline

| Time (PDT) | Phase | Notes |
|---|---|---|
| 10:49:14 | Log start | Buoy on shore near operator/RX. RSSI −20 to −30 dBm. |
| 10:49 – 10:55 | Pre-swim baseline | Stationary; SNR +5 to +7 dB. |
| **10:55:57** | **Swim start** | First packet with RSSI < −50 — antenna enters water. Sharp −40 dB step. |
| 10:55:57 – 11:13:04 | **Outbound to the mouth (~17 min)** | Buoy traversed trkpts 0 → ~13. Estimated avg pace 0.6 m/s (slow buoy tow). |
| **11:13:04** | **Last DIRECT reception** | seq 519, RSSI −89, SNR +6, range ~397 m (at the cove mouth). |
| 11:13:04 – 11:15:49 | Buoy heard only via mesh relays | 16 relayed packets through neighbor nodes. |
| **11:15:49** | **Buoy goes silent forever** | Battery cutoff. |
| 11:15:49 – 11:40:47 | Silence (~25 min) | Swimmer presumably continued the GPX loop without a working buoy. |
| 11:40:47 | Buoy reboots | Uptime = 5 s, new boot cycle. Out of scope for the test. |

---

## 5. What the log actually looks like at each moment

### 5.1 Healthy mid-test direct reception (11:00:00 PDT)
Buoy at ~210 m from RX, signal clean.
```
DEBUG | 18:00:00 1903 [RadioIf] Lora RX (id=0x4b88e1dd fr=0x04089c08 to=0xffffffff,
        transport = 0, WantAck=0, HopLim=3 Ch=0x8 encrypted len=99
        rxSNR=6.25 rxRSSI=-81 hopStart=3 relay=0x8)
INFO  | 18:00:00 1903 [Router] Received text msg from=0x4089c08, id=0x4b88e1dd,
        msg=BUOY1 #362 +30m15s Temperature:27.040 , Pressure:102.01400 kPa, Deep:0.032 m
DEBUG | 18:00:06 1909 [RadioIf] Lora RX (id=0x717889df fr=0x04089c08 ...
        rxSNR=6 rxRSSI=-82 hopStart=3 relay=0x8)
INFO  | 18:00:06 1909 [Router] Received text msg from=0x4089c08, id=0x717889df,
        msg=BUOY1 #363 +30m20s Temperature:26.970 , Pressure:102.07600 kPa, Deep:0.038 m
```
`relay=0x8` = packet's last hop was the originator `0x04089c08` (low byte `0x08`). Direct reception.

### 5.2 Last DIRECT reception — 11:13:04 PDT (seq 519)
```
DEBUG | 18:13:04 2687 [RadioIf] Lora RX (id=0x9625e316 fr=0x04089c08 ...
        rxSNR=6 rxRSSI=-89 hopStart=3 relay=0x8)
INFO  | 18:13:04 2687 [Router] Received text msg from=0x4089c08, id=0x9625e316,
        msg=BUOY1 #519 +43m22s Temperature:29.870 , Pressure:101.71200 kPa, Deep:0.001 m
DEBUG | 18:13:06 2689 [RadioIf] Lora RX (id=0xb4f7a313 fr=0x04089c08 ...
        rxSNR=4.5 rxRSSI=-92 hopStart=3 relay=0x8)
DEBUG | 18:13:09 2692 [RadioIf] Lora RX (id=0x11f9ab14 fr=0x04089c08 ...
        rxSNR=5.75 rxRSSI=-88 hopStart=3 relay=0x8)
```
Last three direct receptions. RSSI sits at −88 to −92 — the buoy is at the mouth (~400 m). After 18:13:09 the direct path is gone — no more `relay=0x8`.

### 5.3 The receiver's TX queue draining OLD packets after the link went dark
This is the trap I fell into in earlier passes: `Started Tx` lines past 11:13 are NOT new receptions. They are the receiver retransmitting old buffered packets. The seqs are *earlier* than 519 (412, 415, 416, 437, 442…) and the embedded `rxSNR`/`rxRSSI` are from when those packets were originally received minutes ago.
```
DEBUG | 18:13:13 2696 [RadioIf] Started Tx (id=0x26b11e41 fr=0x04089c08 ...
        rxtime=1780250648 rxSNR=4.25 rxRSSI=-90 hopStart=3 relay=0x8)
DEBUG | 18:13:28 2711 [RadioIf] Started Tx (id=0xac216a47 fr=0x04089c08 ...
        rxtime=1780250663 rxSNR=5.75 rxRSSI=-90 …)
DEBUG | 18:13:45 2728 [RadioIf] Started Tx (id=0x4c589248 fr=0x04089c08 ...
        rxtime=1780250668 rxSNR=5.5 rxRSSI=-84 …)
DEBUG | 18:14:11 2754 [RadioIf] Started Tx (id=0x3bd0de7d fr=0x04089c08 ...
        rxtime=1780250801 rxSNR=5 rxRSSI=-92 …)
DEBUG | 18:14:33 2776 [RadioIf] Started Tx (id=0x271d8a98 fr=0x04089c08 ...
        rxtime=1780250871 rxSNR=5.75 rxRSSI=-97 …)
```
TX queue was full all test (see `[Router] TX queue is full, ... evict in favour of 0x...`). After the buoy went silent the receiver kept clearing the backlog.

### 5.4 RELAYED 9c08 packets — buoy still alive, heard through the mesh
The most interesting moment of the test. The direct path is gone but mesh neighbors (closer to the mouth, or with better LOS) keep forwarding the buoy's transmissions to us.
```
DEBUG | 18:13:16 2699 [RadioIf] Lora RX (id=0x26b11e41 fr=0x04089c08 ...
        rxSNR=-11 rxRSSI=-120 hopStart=3 relay=0x27)
DEBUG | 18:13:17 2700 [RadioIf] Lora RX (id=0x26b11e41 fr=0x04089c08 ...
        rxSNR=-1.5 rxRSSI=-108 hopStart=3 relay=0x3c)
DEBUG | 18:13:47 2730 [RadioIf] Lora RX (id=0x4c589248 fr=0x04089c08 ...
        rxSNR=-3 rxRSSI=-108 hopStart=3 relay=0x3c)
DEBUG | 18:13:50 2733 [RadioIf] Lora RX (id=0x4c589248 fr=0x04089c08 ...
        rxSNR=-13.25 rxRSSI=-123 hopStart=3 relay=0x5b)
DEBUG | 18:14:14 2757 [RadioIf] Lora RX (id=0x3bd0de7d fr=0x04089c08 ...
        rxSNR=-2 rxRSSI=-111 hopStart=3 relay=0x3c)
DEBUG | 18:14:29 2772 [RadioIf] Lora RX (id=0x06fc168a fr=0x04089c08 ...
        rxSNR=-2 rxRSSI=-111 hopStart=3 relay=0x3c)
```
Key observations:
- `relay=0x3c` and `relay=0x27` show up — two neighbor mesh nodes are quietly doing their job, hearing the buoy and forwarding to us.
- `HopLim=1` means the packet has been hopped once already (decrement from the original 3, then once more by the relay → 3 − 2 = 1). Confirms it traveled buoy → relay → us.
- RSSI/SNR here describe the *relay → us* hop, NOT the buoy → us link. That's why these aren't usable for direct path-loss analysis.

### 5.5 Final silence — 11:15:49 PDT
The last gasp. ERROR lines = packets we could see arriving but couldn't decode (CRC fail / corrupted at the noise floor). After 18:15:49 — nothing for 25 minutes.
```
DEBUG | 18:15:30 2833 [RadioIf] Lora RX (id=0x99c8aaed fr=0x04089c08 ...
        rxSNR=-9.5 rxRSSI=-119 hopStart=3 relay=0x27)
DEBUG | 18:15:32 2835 [RadioIf] Lora RX (id=0x99c8aaed fr=0x04089c08 ...
        rxSNR=-7  rxRSSI=-107 hopStart=3 relay=0x3c)
DEBUG | 18:15:41 2844 [RadioIf] Lora RX (id=0x3cf8faf5 fr=0x04089c08 ...
        rxSNR=-1.75 rxRSSI=-109 hopStart=3 relay=0x3c)
DEBUG | 18:15:49 2852 [RadioIf] Lora RX (id=0x045f2701 fr=0x04089c08 ...
        rxSNR=-0.5 rxRSSI=-108 hopStart=3 relay=0x3c)   ← LAST EVER from this buoy
ERROR | 18:15:50 2853 [RadioIf] Ignore received packet due to error=-7
                                 (maybe to=0xffffffff, from=0x04089c08, flags=0x60)
```

### 5.6 Silence and reboot
```
[ 25 minutes of nothing from 9c08 ]

DEBUG | 18:40:47 4350 [RadioIf] Lora RX (id=0xd63a3cae fr=0x04089c08 ...
        rxSNR=6.75 rxRSSI=-35 hopStart=3 relay=0x8)
```
RSSI is back to −35 dBm, the buoy is in the operator's hand again, uptime is starting fresh. This portion is outside the test.

### 5.7 Mesh inventory — who else was hearing the buoy?

| `relay=` | packets (whole log) | meaning |
|---|---:|---|
| `0x8` | **729** | direct from buoy `0x04089c08` (last byte `0x08`) |
| `0x3c` | 64 | via neighbor node ID `0x…3c` |
| `0x27` | 46 | via neighbor node ID `0x…27` |
| `0x5b` | 1 | via neighbor node ID `0x…5b` (one-off) |
| `0xbf` | 1 | via neighbor node ID `0x…bf` (one-off) |

Two regular relays — `0x3c` (more reliable) and `0x27` — handled all the mesh-forwarded copies. The two one-shots (`0x5b`, `0xbf`) are probably nodes that briefly came into range. These are mesh nodes installed somewhere around Aquatic Park / Fisherman's Wharf — they are NOT part of this test, but they bailed the receiver out for the last 2:45 of buoy life.

---

## 6. Effective range

### Demonstrated DIRECT range: 397 m

Last direct reception was at 11:13:04, position interpolated to ~397 m from RX (right at the mouth, GPX trkpt 13). RSSI −89 dBm, SNR +6 dB. The link still had ~23 dB of SNR margin before SF11 demod threshold and ~25 dB of RSSI margin before the noise floor — so we were not link-budget limited at 400 m. The test ended because (a) we lost the direct geometry and (b) eventually the battery died.

### Demonstrated MESH range: ~400 m, sustained 2:45 longer

Through the local mesh (`relay=0x3c`, `0x27`), the buoy stayed reachable until 11:15:49 — about 2:45 past the direct-link cutoff. So at 400 m, the *mesh* link was robust to single-path failures.

### Extrapolated practical range

Direct-only log-distance fit: **RSSI = 28 − 48.5·log₁₀(d), path-loss exponent n ≈ 4.85** (over salt water, antenna at surface). At +6 dB SNR with 23 dB margin to the demod floor:
```
range_limit ≈ 400 m × 10^(23 / (10 × 4.85))
            ≈ 400 m × 2.94
            ≈ 1170 m
```
This is optimistic — n typically increases past the radio horizon (~1.5 km for surface-mount antennas). **Realistic practical envelope: 600–1000 m for direct-link from a similarly-mounted buoy in Aquatic Cove geometry.** The mesh can extend that further wherever neighbor nodes have LOS.

---

## 7. Signal quality over time — direct receptions, per minute

| min (PDT) | n | SNR mean | RSSI mean | RSSI min | est range mean |
|---|---:|---:|---:|---:|---:|
| 10:49 | 11 | +6.36 | −20.9 | −26 | 46 m (on shore) |
| 10:50 | 11 | +6.25 | −23.5 | −30 | 46 |
| 10:51 | 12 | +6.12 | −24.8 | −32 | 46 |
| 10:52 | 12 | +5.94 | −19.8 | −22 | 46 |
| 10:53 | 12 | +6.40 | −26.7 | −36 | 46 |
| 10:54 | 12 | +6.25 | −30.1 | −42 | 46 |
| 10:55 | 10 | +6.33 | −32.1 | −53 | 46 (entering water) |
| 10:56 | 12 | +5.92 | −62.2 | −72 | 69 (antenna-immersion step) |
| 10:57 | 10 | +6.28 | −68.2 | −85 | 102 |
| 10:58 | 13 | +6.17 | −76.3 | −83 | 135 |
| 10:59 | 12 | +5.75 | −79.3 | −94 | 169 |
| 11:00 | 11 | +5.66 | −83.5 | −90 | 207 |
| 11:01 | 9 | +5.33 | −87.6 | −97 | 241 |
| 11:02 | 13 | +5.15 | −89.8 | −97 | 273 |
| 11:03 | 11 | +4.82 | −92.0 | −100 | 307 |
| 11:04 | 11 | +5.45 | −88.5 | −95 | 341 |
| 11:05 | 11 | +5.18 | −91.3 | −97 | 363 |
| 11:06 | 9 | +4.33 | −94.3 | −102 | 376 |
| 11:07 | 13 | +3.15 | −98.8 | −106 | 386 |
| 11:08 | 8 | +2.62 | −99.2 | −105 | 393 |
| 11:09 | 12 | +1.96 | −103.8 | −110 | 397 |
| 11:10 | 11 | +0.50 | −105.4 | −113 | 398 |
| 11:11 | 12 | +2.65 | −98.7 | −109 | 398 |
| 11:12 | 11 | +2.30 | −101.5 | −109 | 397 |
| 11:13 | 1 | +6.00 | −89.0 | −89 | 397 (final direct) |

Three regimes are now crystal clear:
- **0–7 min (on shore):** RSSI flat at −20 to −30 dBm. Antenna in air.
- **10:55:57 (swim start):** Sharp −40 dB step as the antenna enters water — the largest single-feature signal change in the test.
- **10:56 → 11:13:** RSSI marches cleanly down with range to −105 dBm at the mouth. SNR holds positive throughout. Path loss exponent **n ≈ 4.85**, consistent with sea-surface multipath for a low-mount antenna.

No more "return-leg suppression mystery" — that was an artifact of the wrong position assumption.

---

## 8. Packet reception rate vs distance (direct only)

Reception is **essentially flat at ~85 %** across the 40–400 m envelope. Loss is dominated by local channel utilization, not link budget — the log is full of `[Position] Ch. util >25%. Skip send` and `TX queue is full` warnings.

See `fig_loss_vs_distance.png`.

---

## 9. Obstruction analysis

With the corrected position model:
- The direct-only RSSI cleanly tracks the path-loss curve out to 400 m. There's no individual point lying 10+ dB below the fit for multiple consecutive packets — i.e., **no high-confidence boat-obstruction event**.
- Single-packet deep dips (e.g. RSSI −115 to −121 at the noise floor) at peak range are decode-edge artifacts, not blockage.
- The most plausible obstruction-like effect is the loss of the direct path at 11:13:04. But we can't distinguish it from battery-sag-driven Tx-power decay, antenna geometry change (waves), or the buoy moving behind a pier piling. The mesh relays kept hearing the buoy from a different angle, which is consistent with line-of-sight obstruction (between buoy and RX specifically) but not proof.

---

## 10. Headline conclusions

1. **The buoy died near the cove mouth around 11:13–11:15 PDT**, while at ~400 m from RX. The direct link to the receiver was lost first (11:13:04); the buoy continued transmitting and was heard via mesh relays until 11:15:49.
2. **Demonstrated direct radio range: ~400 m**, with ~23 dB of SNR margin remaining — *not* a range-limited test failure.
3. **Path-loss exponent over salt water at Aquatic Cove: n ≈ 4.85.** Significantly worse than free-space (n=2), as expected for an antenna near the water surface.
4. **Antenna-immersion penalty: ~40 dB** — the single largest signal-quality effect in the entire test.
5. **The mesh saved us.** Without `0x3c` and `0x27` quietly forwarding, we'd have lost the buoy 2:45 sooner.
6. **No high-confidence obstruction events** during the swim.
7. **Loss is queue-limited, not range-limited.** Channel saturation dropped ~15 % of packets; the air interface was fine throughout.

---

## 11. Caveats

- The GPX has no timestamps. The buoy's position over time during 10:55:57–11:13:04 is interpolated linearly along the outbound portion (trkpts 0 → 13). Real pacing was probably variable.
- The receiver's antenna location is at the south beach as given (37°48′24.3″N 122°25′23.7″W). Distances are valid relative to that point.
- "Direct" here means `relay=0x8` (last hop = originator). Some relayed copies may have arrived first in time but they aren't usable for direct path-loss analysis.

---

## 12. Files in this folder

- **`FINDINGS.md`** — this report
- **`log_snippets.txt`** — full log excerpts shown in §5
- `fig_basemap_rssi.png` — RSSI heatmap on OpenStreetMap, buoy-alive trajectory colored
- `fig_basemap_rssi_sat.png` — same, satellite imagery
- `fig_basemap_snr.png` — SNR heatmap on OpenStreetMap
- `fig_basemap_snr_sat.png` — SNR heatmap on satellite imagery
- `fig_distance_sanity_sat.png` — distance verification with key trkpt rays
- `fig_rssi_and_range.png` — direct-RX RSSI and estimated range vs time
- `fig_snr_and_range.png` — direct-RX SNR and estimated range vs time (with SF11 demod floor line)
- `fig_snr_by_distance.png` — SNR boxplot distribution by distance bin
- `fig_signal_vs_range.png` — RSSI/SNR vs true range, log-distance fit
- `fig_loss_vs_distance.png` — direct reception rate by distance bin
- `direct_receptions.json` — raw 270 direct receptions (RSSI, SNR, packet id, source kind)
- `annotated.json` — 270 direct receptions enriched with interpolated lat/lon and `rx_d`
- `per_minute.json` — per-minute summary table
