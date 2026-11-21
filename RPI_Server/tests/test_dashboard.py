import os
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path


DATABASE_DIRECTORY = tempfile.TemporaryDirectory(prefix="gakuna-dashboard-tests-")
os.environ["GAKUNA_DATABASE_PATH"] = str(Path(DATABASE_DIRECTORY.name) / "dashboard.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import GAKUNA_SERVER_V3 as server


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def tearDown(self):
        with closing(server.sqlite3.connect(server.DATABASE_PATH)) as database:
            with database:
                database.executemany(
                    "UPDATE dashboard_settings SET value = NULL WHERE key = ?",
                    [(key,) for key in server.ALERT_THRESHOLD_KEYS],
                )
        server.ALERT_THRESHOLDS = {
            key: None for key in server.ALERT_THRESHOLD_KEYS
        }

    def test_map_nodes_expose_new_telemetry_fields(self):
        response = self.client.get("/api/nodes")
        self.assertEqual(response.status_code, 200)

        nodes = {node["id"]: node for node in response.get_json()}
        mapped_ids = {marker["id"] for marker in server.FLOOR_MAP_NODES}
        self.assertTrue(mapped_ids <= set(nodes))
        for node_id in mapped_ids:
            self.assertIn("humidity", nodes[node_id])
            self.assertIn("firmware_version", nodes[node_id])
            self.assertIn("last_seen_epoch", nodes[node_id])
            self.assertIn("last_seen_age_seconds", nodes[node_id])

        cameras = self.client.get("/api/cameras")
        self.assertEqual(cameras.status_code, 200)
        self.assertTrue(cameras.get_json()[0]["stale"])

    def test_overview_renders_floor_plans_and_prototype_warning(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Prototype guidance", response.data)
        self.assertIn(b'id="building-model-canvas"', response.data)
        self.assertIn(b"function renderBuildingModel()", response.data)
        self.assertIn(b"function shortestBuildingRoute(startId)", response.data)
        self.assertIn(b"id=\"building-model-cutaway\"", response.data)
        self.assertIn(b"id=\"building-model-path\"", response.data)
        self.assertIn(b"id=\"building-path-status\"", response.data)
        self.assertIn(b"View original 2D floor plans", response.data)
        self.assertIn(b"Shift+drag to pan", response.data)
        for marker in server.FLOOR_MAP_NODES:
            self.assertIn(
                f'data-model-node-id="{marker["id"]}"'.encode(),
                response.data,
            )
        self.assertIn(b"edge-left", response.data)
        self.assertIn(b"edge-right", response.data)
        floor_one = self.client.get("/static/maps/floor-1.png")
        floor_two = self.client.get("/static/maps/floor-2.png")
        self.assertEqual(floor_one.status_code, 200)
        self.assertEqual(floor_two.status_code, 200)
        floor_one.close()
        floor_two.close()

    def test_threshold_pairs_must_be_complete_and_ordered(self):
        incomplete = self.client.post(
            "/api/settings/thresholds",
            json={"ambient_warning_c": 30},
        )
        self.assertEqual(incomplete.status_code, 400)

        reversed_pair = self.client.post(
            "/api/settings/thresholds",
            json={"thermal_warning_c": 60, "thermal_critical_c": 55},
        )
        self.assertEqual(reversed_pair.status_code, 400)

        valid = self.client.post(
            "/api/settings/thresholds",
            json={
                "ambient_warning_c": 30,
                "ambient_critical_c": 40,
                "thermal_warning_c": 60,
                "thermal_critical_c": 80,
            },
        )
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(
            self.client.get("/api/settings/thresholds").get_json(),
            valid.get_json(),
        )
        out_of_range = self.client.post(
            "/api/settings/thresholds",
            json={"ambient_warning_c": 101, "ambient_critical_c": 110},
        )
        self.assertEqual(out_of_range.status_code, 400)

    def test_invalid_sensor_sentinel_is_not_reported_as_temperature(self):
        self.assertIsNone(server.valid_sensor_reading(-127.0))
        self.assertIsNone(server.valid_sensor_reading(float("nan")))
        self.assertIsNone(server.valid_sensor_reading(101, minimum=0, maximum=100))
        self.assertEqual(server.valid_sensor_reading(42.36), 42.4)

    def test_connected_node_with_old_telemetry_is_stale(self):
        node = server.NodeState({
            "id": "node_test",
            "name": "Test node",
            "ip": "DHCP",
            "port": server.ESP32_SERVER_PORT,
        })
        node.connected = True
        node.last_seen_epoch = time.time() - 6
        self.assertTrue(node.to_dict()["stale"])

    def test_seeded_node_and_camera_ids_migrate(self):
        original_database_path = server.DATABASE_PATH
        with tempfile.TemporaryDirectory(prefix="gakuna-legacy-test-") as directory:
            server.DATABASE_PATH = str(Path(directory) / "legacy.db")
            try:
                with closing(server.sqlite3.connect(server.DATABASE_PATH)) as database:
                    with database:
                        database.execute("""
                            CREATE TABLE node_registry (
                                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                                ip TEXT NOT NULL, port INTEGER NOT NULL
                            )
                        """)
                        database.execute(
                            "INSERT INTO node_registry VALUES (?, ?, ?, ?)",
                            ("node_1", "Node 11 - Floor 1 Left", "DHCP", 5001),
                        )
                        database.execute("""
                            CREATE TABLE camera_registry (
                                id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT,
                                node_id TEXT NOT NULL, enabled INTEGER NOT NULL
                            )
                        """)
                        database.execute(
                            "INSERT INTO camera_registry VALUES (?, ?, ?, ?, ?)",
                            ("cam_1", "Camera - Node 11", None, "node_1", 1),
                        )

                nodes = server.load_node_configs()
                cameras = server.load_camera_configs()
                self.assertIn("node_11", {node["id"] for node in nodes})
                self.assertNotIn("node_1", {node["id"] for node in nodes})
                self.assertEqual(cameras[0]["node_id"], "node_11")
            finally:
                server.DATABASE_PATH = original_database_path

    def test_legacy_node_slots_merge_and_camera_bindings_normalize(self):
        original_database_path = server.DATABASE_PATH
        with tempfile.TemporaryDirectory(prefix="gakuna-legacy-slots-") as directory:
            server.DATABASE_PATH = str(Path(directory) / "legacy.db")
            try:
                with closing(server.sqlite3.connect(server.DATABASE_PATH)) as database:
                    with database:
                        database.execute("""
                            CREATE TABLE node_registry (
                                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                                ip TEXT NOT NULL, port INTEGER NOT NULL
                            )
                        """)
                        database.executemany(
                            "INSERT INTO node_registry VALUES (?, ?, ?, ?)",
                            [
                                ("node_3", "Node 13 - Floor 2", "192.168.1.30", 5030),
                                ("node_21", "Old Node 21", "DHCP", 5001),
                                ("node_4", "Node 14 - Floor 2", "192.168.1.40", 5040),
                                ("node_22", "Old Node 22", "192.168.1.22", 5001),
                            ],
                        )
                        database.execute("""
                            CREATE TABLE camera_registry (
                                id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT,
                                node_id TEXT NOT NULL, enabled INTEGER NOT NULL
                            )
                        """)
                        database.executemany(
                            "INSERT INTO camera_registry VALUES (?, ?, ?, ?, ?)",
                            [
                                ("cam_1", "Cam 1", None, "node_1", 1),
                                ("cam_2", "Cam 2", None, "node_2", 1),
                                ("cam_3", "Cam 3", None, "node_1", 1),
                                ("cam_4", "Cam 4", None, "node_1", 1),
                            ],
                        )

                nodes = server.load_node_configs()
                cameras = server.load_camera_configs()
                nodes_by_id = {node["id"]: node for node in nodes}
                cameras_by_id = {camera["id"]: camera for camera in cameras}

                self.assertNotIn("node_3", nodes_by_id)
                self.assertNotIn("node_4", nodes_by_id)
                self.assertEqual(nodes_by_id["node_21"]["name"], "Node 21 - Floor 2 left end")
                self.assertEqual(nodes_by_id["node_21"]["ip"], "192.168.1.30")
                self.assertEqual(nodes_by_id["node_21"]["port"], 5030)
                self.assertEqual(nodes_by_id["node_22"]["name"], "Node 22 - Floor 2 middle")
                self.assertEqual(nodes_by_id["node_22"]["ip"], "192.168.1.22")
                self.assertIn("node_23", nodes_by_id)
                self.assertEqual(
                    {camera_id: camera["node_id"] for camera_id, camera in cameras_by_id.items()},
                    {
                        "cam_1": "node_11",
                        "cam_2": "node_12",
                        "cam_3": "node_21",
                        "cam_4": "node_22",
                        "cam_5": "node_23",
                    },
                )

                with closing(server.sqlite3.connect(server.DATABASE_PATH)) as database:
                    with database:
                        database.execute(
                            "UPDATE camera_registry SET node_id = ? WHERE id = ?",
                            ("node_12", "cam_3"),
                        )
                cameras = server.load_camera_configs()
                self.assertEqual(
                    {camera["id"]: camera["node_id"] for camera in cameras}["cam_3"],
                    "node_12",
                )
            finally:
                server.DATABASE_PATH = original_database_path

    def test_legacy_node_ids_resolve_to_canonical_slots(self):
        for legacy_id, canonical_id in server.LEGACY_NODE_ID_MAP.items():
            with self.subTest(legacy_id=legacy_id):
                self.assertIs(server.get_node(legacy_id), server.get_node(canonical_id))

        duplicate = self.client.post(
            "/api/nodes",
            json={"id": "node_5", "name": "Legacy fifth node"},
        )
        self.assertEqual(duplicate.status_code, 409)

        invalid_camera = self.client.post(
            "/api/cameras",
            json={"id": ["invalid"], "name": "Invalid camera", "node_id": "node_1"},
        )
        self.assertEqual(invalid_camera.status_code, 400)


if __name__ == "__main__":
    unittest.main()
