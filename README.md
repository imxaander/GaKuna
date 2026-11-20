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

The server stores node and camera registrations in
`RPI_Server/gakuna_nodes.db`, created automatically on first run. The existing
`node_1`, `node_2`, and `cam_1` entries are seeded into the database. Add
devices from the dashboard's **Settings** tab:

- A node ID must match `NODE_ID` in its ESP32 firmware.
- A camera ID must match `CAMERA_ID` in its ESP32-CAM firmware. Leave its URL
  blank for the TCP frame stream, or enter an HTTP snapshot URL.