# GaKuna

## Raspberry Pi node registry

The server stores node registrations in `RPI_Server/gakuna_nodes.db`, created
automatically on first run. The existing `node_1` and `node_2` entries are
seeded into the database. Add new nodes from the **Nodes** tab; each ID must
match the `NODE_ID` configured in that node's ESP32 firmware.