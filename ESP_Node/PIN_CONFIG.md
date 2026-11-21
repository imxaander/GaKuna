# GaKuna ESP32 Node — Pin Configuration

Board: ESP32 dev board (GaKuna sensor node)
Sketch: `GAKUNA_ESP_NODE.ino`

## Pin Map

| Component | Signal | GPIO | Notes |
|-----------|--------|------|-------|
| DHT22 | Data | 4 | 4.7–10k pull-up to 3.3V; VCC 3.3V, GND |
| Green LED #1 | Left arrow | 18 | Via transistor |
| Green LED #2 | Right arrow | 19 | Via transistor |
| Red LED | Stop / cross | 23 | Via transistor |
| DFPlayer Mini | DF TX → node RX | 16 | `SoftwareSerial` RX |
| DFPlayer Mini | node TX → DF RX | 17 | `SoftwareSerial` TX, add 1kΩ series (DF is 5V logic) |
| MLX90640 | SDA | 21 | I2C, 400 kHz |
| MLX90640 | SCL | 22 | I2C |
| ESP32-CAM | — | none | Separate board, WiFi only (power + GND) |

## Power

| Rail | Source | Feeds |
|------|--------|-------|
| 5V | Buck #1 | ESP32 VIN |
| 5V | Buck #2 | DFPlayer VCC, ESP32-CAM 5V |
| 3.3V | ESP32 regulator | DHT22, MLX90640 |
| GND | Single star point | All grounds common |

## Reserved / Avoid

| Pins | Reason |
|------|--------|
| GPIO 6–11 | Connected to SPI flash — unusable |
| GPIO 34–39 | Input-only |
| GPIO 1 / 3 | UART0 — keep clear for USB serial |

## Network

| Setting | Value |
|---------|-------|
| SSID | `gakunapi_wifi` |
| Password | `gakunapass` |
| Pi IP | `192.168.4.1` |
| Pi port | `5001` (must match `ESP32_SERVER_PORT` in `GAKUNA_SERVER_V3.py`) |

## Telemetry Payload

Sent as newline-delimited JSON every `SEND_INTERVAL` (1000 ms):

```json
{"node_id":"node_11","firmware_version":"1.1.0","temp":24.5,"humidity":48.0,"thermal":41.2,"seismic":0.0,"blocked":false}
```

| Field | Source | Notes |
|-------|--------|-------|
| `node_id` | Firmware setting | Must match a node ID registered on the Pi; set the value for each board before flashing |
| `firmware_version` | Firmware setting | Displayed in the dashboard |
| `temp` | DHT22 | Ambient temp in °C; `-127.0` on read failure |
| `humidity` | DHT22 | Relative humidity in %; `-127.0` on read failure |
| `thermal` | MLX90640 | Hottest valid pixel in °C; `-127.0` on failure |
| `thermal_frame` | MLX90640 | 32 × 24 pixel values used by the Pi to render the live thermal view |
| `seismic` | — | Placeholder for MPU6050 |
| `blocked` | — | Placeholder for HC-SR04 (server also gets blockage from the CAM) |

## Libraries

- `DHT sensor library` (Adafruit)
- `Adafruit MLX90640`
- `ArduinoJson`
- `DFRobotDFPlayerMini`

## DFPlayer sound files

Place these numbered files in the SD card's `/mp3/` folder:

| File | Cue |
|------|-----|
| `0001.mp3` | Go left |
| `0002.mp3` | Go right |
| `0003.mp3` | Do not proceed here |
| `0004.mp3` | Go down |
| `0005.mp3` | Go up |
| `0006.mp3` | Go to the other side |

Use the Pi dashboard's per-node sound controls, or send
`POST /api/sound` with `{"node_id": "node_1", "track": 4}`. Set `node_id` to
`"all"` to broadcast a cue. Playing a sound does not change the node's sign
override state.
