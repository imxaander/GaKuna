# GaKuna

## Raspberry Pi server

The server is Python-based and uses SQLite; no separate database service or
Node.js build environment is required. On the Raspberry Pi, create a virtual
environment and install its Python packages:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r RPI_Server/requirements.txt
python RPI_Server/GAKUNA_SERVER_V3.py
```

The server stores node and camera registrations and dashboard thresholds in
`RPI_Server/gakuna_nodes.db`, created automatically on first run. The five map
nodes (`node_11`, `node_12`, `node_21`, `node_22`, and `node_23`) and `cam_1`
are seeded on first run. Existing default `node_1`/`node_2` records are migrated
to `node_11`/`node_12` when their original seeded names are unchanged. Add
additional devices from the dashboard's **Settings** tab:

- A node ID must match `NODE_ID` in its ESP32 firmware.
- A camera ID must match `CAMERA_ID` in its ESP32-CAM firmware. Leave its URL
  blank for the TCP frame stream, or enter an HTTP snapshot URL.

The **Overview** tab shows the supplied Floor 1 and Floor 2 schematics. Select
a node marker to view its IP, firmware version, last-seen time, DHT22 readings,
and either its MLX90640 thermal feed or assigned ESP32-CAM feed. The ESP32 node
firmware sends DHT22 humidity and firmware version along with its other sensor
data; set the node's ID in the sketch to the matching registry ID before
flashing each physical node. Before compiling, copy
`ESP_Node/GAKUNA_ESP_NODE/wifi_secrets.example.h` to `wifi_secrets.h` in the
same folder and add the local Wi-Fi credentials. The real `wifi_secrets.h` is
git-ignored and must not be committed.

Configure ambient and thermal warning/critical temperature pairs under
**Settings** after validating values for the site. These thresholds classify
displayed readings only. Existing automatic sign behavior is marked as
prototype behavior and does not compute a verified evacuation route.
