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
nodes (`node_11`, `node_12`, `node_21`, `node_22`, and `node_23`) and cameras
`cam_1` through `cam_5` are seeded on first run. Legacy node IDs are accepted
without changing firmware and resolve to these floor-plan positions:

| Firmware ID | Dashboard node | Area | Camera |
| --- | --- | --- | --- |
| `node_1` | `node_11` | Floor 1, left end | `cam_1` |
| `node_2` | `node_12` | Floor 1, right end | `cam_2` |
| `node_3` | `node_21` | Floor 2, left end | `cam_3` |
| `node_4` | `node_22` | Floor 2, middle | `cam_4` |
| `node_5` | `node_23` | Floor 2, right end | `cam_5` |

On startup, old registry rows are merged into these canonical nodes; saved
network settings are retained where possible. Legacy camera-to-node references
are migrated by camera number, while existing camera assignments already using
canonical node IDs are left unchanged. Add additional devices from the
dashboard's **Settings** tab:

- A node ID should match `NODE_ID` in its ESP32 firmware; both the canonical IDs
  and legacy `node_1` through `node_5` are supported.
- A camera ID must match `CAMERA_ID` in its ESP32-CAM firmware. Leave its URL
  blank for the TCP frame stream, or enter an HTTP snapshot URL.

The **Overview** tab includes an interactive isometric 3D schematic based on
the supplied floor plans. Drag to orbit, scroll to zoom, and use Shift-drag to
pan; switch between floors, separate them, or enable the upper-floor cutaway
to inspect both levels. Selecting a model node generates a prototype path along
the configured hallway/stair graph to the Floor 1 exit. The stair transition is
drawn as a vertical connector with an UP/DOWN badge. A reported blockage or
stale/offline path-node telemetry marks the path status unknown; the prototype
does not automatically reroute and is not approved evacuation guidance. Select
a model node to view its IP, firmware version, last-seen time, DHT22 readings,
and either its MLX90640 thermal feed or assigned ESP32-CAM feed. The original
2D plans remain available below the model. The 3D room and wall heights are
illustrative because the plans do not specify dimensions. The ESP32 node
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
