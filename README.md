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
| Meshtastic LoRa node | UART /dev/ttyS0 | 115200 baud |
| INA219 battery monitor | I2C bus 1 | 0x40 |

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

All radio I/O uses a JSON framed packet:

```json
{"type": "heartbeat", "payload": {...}, "checksum": "<sha256 hex>"}
```

Supported types: `heartbeat`, `data_request`, `data_response`, `ota`, `ack`,
`nack`.

## Safety

* Hardware watchdog at `/dev/watchdog`, kicked every 30 s.
* Leak sensor on GPIO 17 triggers graceful shutdown.
* Battery below 11 V drops to low-power mode (300 s sampling).
