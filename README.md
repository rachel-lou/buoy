# Ocean Monitoring Buoy

Embedded data acquisition system for an autonomous ocean monitoring buoy running on Raspberry Pi 3B+.

## Hardware

| Sensor | Interface | Address/Pin |
| --- | --- | --- |
| MS5837-02BA depth/temperature | I2C bus 1 | 0x76 |
| ICM-42688-P IMU | SPI bus 0, dev 0 | CS=GPIO 8 |
| MCP3008 ADC (DO + salinity) | SPI bus 0, dev 1 | CS=GPIO 25 |
| DFRobot Gravity DO | MCP3008 ch 0 | analog |
| Conductivity probe | MCP3008 ch 1 | analog |
| Leak sensor | GPIO 17 (pull-up) | active low |
| Meshtastic LoRa node | USB (CP2102, auto-discovered) | 115200 baud |
| INA219 battery monitor | I2C bus 1 | 0x40 |

## SSH into the pi

```bash
ssh kelp@buoy1.local
```
## Install

```bash
sudo bash scripts/setup_pi.sh
sudo systemctl enable --now buoy.service
```

## Run tests (laptop, no hardware required)

```bash
python -m unittest discover tests -v
```

## Configuration

All tunable parameters live in `config/config.yaml`. Send `SIGHUP` to the
daemon to reload without restart.

## Data

Readings are written to SQLite at `/data/buoy.db` with a 500,000 row ring
buffer. Export CSV or JSON with `DataStore.export_csv()` /
`DataStore.export_json()`.

## Radio packets

All machine-to-machine radio I/O uses a JSON framed packet:

```json
{"type": "heartbeat", "payload": {...}, "checksum": "<sha256 hex>"}
```

Supported types: `heartbeat`, `data_request`, `data_response`, `ota`, `ack`,
`nack`. A `data_request` may include `since_timestamp`, `until_timestamp`,
`sensor` and `limit`. Because a real LoRa/Meshtastic frame is far smaller
than a few hours of readings, the buoy splits its `data_response` across
multiple packets sharing one `request_id`, each tagged with `chunk_index` /
`chunk_count`; the requester concatenates the `data` fields in order before
base64-decoding and zlib-decompressing. `scripts/query_client.py` does this
reassembly for you (see below).

## Querying data from a phone, with no WiFi

The buoy's Meshtastic node's UART carries two kinds of traffic: the JSON
packets above, for scripted clients, and plain text lines, for a person
typing a direct message from the standard Meshtastic phone app. Any line
that isn't a valid JSON packet is handed to the same text-command handler,
so no extra app is needed on the phone -- just a Meshtastic node paired on
the buoy's channel.

Command syntax (case-insensitive, `GET`/`QUERY` prefix optional):

```
<sensor|ALL> <2h|30m|1d|YYYY-MM-DD|YYYY-MM-DD..YYYY-MM-DD> [csv]
```

Examples:

* `ALL 2H` -- summary (count/min/max/avg/last) of every sensor over the last 2 hours
* `TEMPERATURE 2026-08-13` -- summary of temperature readings for that UTC day
* `SALINITY 6H CSV` -- individual raw salinity samples from the last 6 hours
* `HELP` -- usage plus the sensor names currently in the store

Replies default to one compact summary line per sensor -- computed with a
single SQL aggregate pass, so it's cheap on the Pi's battery and small over
the air. Raw/`csv` detail is capped at 200 rows, and any reply is capped at 8
radio messages; queries that would exceed that get truncated with a note to
narrow the time range. This keeps radio queries useful for "what's it doing
right now / today" checks without draining the battery on a full historical
dump -- see "Bulk sync" below for that.

### Testing without hardware

```bash
python scripts/query_client.py --demo
```

This wires two `Radio` instances together over an in-memory loopback, seeds
a temporary store with a few hours of synthetic readings, and runs both a
text-command exchange (what the phone sees) and a JSON `data_request` with
chunk reassembly -- no serial port or Meshtastic hardware required.

### Testing with real hardware

Point `--port` at a serial port connected to a second Meshtastic node paired
on the buoy's channel (a laptop's USB LoRa dongle, or a phone running
Termux with a serial bridge):

```bash
python scripts/query_client.py --port COM5 --text "ALL 2H"
python scripts/query_client.py --port COM5 --json --sensor temperature --time-spec 2026-08-13 --out temps.csv
```

### Bulk sync (planned)

The text/radio path above is intentionally bandwidth- and battery-conscious,
not a full export mechanism. Once the buoy is recovered or reachable over
WiFi, the existing `DataStore.export_csv()` / `export_json()` (and the SQLite
file itself) are the intended path for a fast full-history download; a small
HTTP server exposing them is the natural next step but is not implemented
yet.

## Safety

* Leak sensor on GPIO 17 triggers graceful shutdown.
* Battery below 11 V drops to low-power mode (300 s sampling).
