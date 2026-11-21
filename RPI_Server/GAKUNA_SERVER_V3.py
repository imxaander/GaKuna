"""
GaKuna Server
-------------
Hosts a web dashboard AND manages TCP connections to ESP32 nodes.
Integrates OpenCV-based blockage detection from ESP32-CAM.
Run with: python3 server.py
Access dashboard at: http://<pi-ip>:8080
"""

import json
import math
import os
import re
import socket
import sqlite3
import struct
import subprocess
import threading
import time
import urllib.request
from contextlib import closing
from urllib.parse import urlparse

import cv2
import numpy as np
from flask import Flask, render_template_string, jsonify, request, Response
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'gakuna2025'
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    manage_session=False,
)

# ─────────────────────────────────────────────
# NODE CONFIGURATION
# ─────────────────────────────────────────────
DEFAULT_NODE_CONFIG = [
    {"id": "node_11", "name": "Node 11 - Floor 1 left end", "ip": "DHCP", "port": 5001},
    {"id": "node_12", "name": "Node 12 - Floor 1 right end", "ip": "DHCP", "port": 5001},
    {"id": "node_21", "name": "Node 21 - Floor 2 left end", "ip": "DHCP", "port": 5001},
    {"id": "node_22", "name": "Node 22 - Floor 2 middle", "ip": "DHCP", "port": 5001},
    {"id": "node_23", "name": "Node 23 - Floor 2 right end", "ip": "DHCP", "port": 5001},
]
DEFAULT_NODE_CONFIG_BY_ID = {node["id"]: node for node in DEFAULT_NODE_CONFIG}
LEGACY_NODE_ID_MAP = {
    "node_1": "node_11",
    "node_2": "node_12",
    "node_3": "node_21",
    "node_4": "node_22",
    "node_5": "node_23",
}
CAMERA_NODE_ASSIGNMENTS = {
    "cam_1": "node_11",
    "cam_2": "node_12",
    "cam_3": "node_21",
    "cam_4": "node_22",
    "cam_5": "node_23",
}


def canonical_node_id(node_id):
    if not isinstance(node_id, str):
        return node_id
    return LEGACY_NODE_ID_MAP.get(node_id, node_id)


def canonical_camera_node_id(camera_id, node_id):
    if isinstance(node_id, str) and node_id in LEGACY_NODE_ID_MAP:
        if isinstance(camera_id, str) and camera_id in CAMERA_NODE_ASSIGNMENTS:
            return CAMERA_NODE_ASSIGNMENTS[camera_id]
        return canonical_node_id(node_id)
    return node_id


def migrate_node_registry(database):
    for old_id, new_id in LEGACY_NODE_ID_MAP.items():
        old_node = database.execute(
            "SELECT ip, port FROM node_registry WHERE id = ?",
            (old_id,),
        ).fetchone()
        if not old_node:
            continue

        canonical = DEFAULT_NODE_CONFIG_BY_ID[new_id]
        existing_node = database.execute(
            "SELECT ip FROM node_registry WHERE id = ?",
            (new_id,),
        ).fetchone()
        if existing_node:
            if existing_node[0] == "DHCP" and old_node[0] != "DHCP":
                database.execute(
                    "UPDATE node_registry SET ip = ?, port = ? WHERE id = ?",
                    (old_node[0], old_node[1], new_id),
                )
            database.execute(
                "UPDATE node_registry SET name = ? WHERE id = ?",
                (canonical["name"], new_id),
            )
            database.execute("DELETE FROM node_registry WHERE id = ?", (old_id,))
        else:
            database.execute(
                "UPDATE node_registry SET id = ?, name = ? WHERE id = ?",
                (new_id, canonical["name"], old_id),
            )
DATABASE_PATH = os.environ.get(
    "GAKUNA_DATABASE_PATH",
    os.environ.get(
        "GAKUNA_NODE_DB_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "gakuna_nodes.db"),
    ),
)

def load_node_configs():
    with closing(sqlite3.connect(DATABASE_PATH)) as database:
        with database:
            database.execute("""
                CREATE TABLE IF NOT EXISTS node_registry (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    port INTEGER NOT NULL
                )
            """)
            migrate_node_registry(database)
            database.executemany(
                "INSERT OR IGNORE INTO node_registry (id, name, ip, port) VALUES (?, ?, ?, ?)",
                [(node["id"], node["name"], node["ip"], node["port"]) for node in DEFAULT_NODE_CONFIG],
            )
            database.row_factory = sqlite3.Row
            rows = database.execute(
                "SELECT id, name, ip, port FROM node_registry ORDER BY rowid"
            ).fetchall()
    return [dict(row) for row in rows]

NODE_CONFIG = load_node_configs()

FLOOR_MAP_NODES = [
    {"id": "node_11", "label": "11", "area": "Floor 1 · Left end", "floor": 1, "left": 6.7, "top": 55.0, "edge": "left"},
    {"id": "node_12", "label": "12", "area": "Floor 1 · Right end", "floor": 1, "left": 95.0, "top": 55.0, "edge": "right"},
    {"id": "node_21", "label": "21", "area": "Floor 2 · Left end", "floor": 2, "left": 5.9, "top": 60.0, "edge": "left"},
    {"id": "node_22", "label": "22", "area": "Floor 2 · Middle", "floor": 2, "left": 50.0, "top": 66.0},
    {"id": "node_23", "label": "23", "area": "Floor 2 · Right end", "floor": 2, "left": 94.0, "top": 60.0, "edge": "right"},
]

ALERT_THRESHOLD_KEYS = (
    "ambient_warning_c",
    "ambient_critical_c",
    "thermal_warning_c",
    "thermal_critical_c",
)


def load_alert_thresholds():
    with closing(sqlite3.connect(DATABASE_PATH)) as database:
        with database:
            database.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_settings (
                    key TEXT PRIMARY KEY,
                    value REAL
                )
            """)
            database.executemany(
                "INSERT OR IGNORE INTO dashboard_settings (key, value) VALUES (?, NULL)",
                [(key,) for key in ALERT_THRESHOLD_KEYS],
            )
            rows = database.execute(
                "SELECT key, value FROM dashboard_settings"
            ).fetchall()
    return {key: value for key, value in rows}


ALERT_THRESHOLDS = load_alert_thresholds()

# Listen on all interfaces; ESP32s connect to the Pi's router-assigned address.
PI_HOST = '0.0.0.0'
ESP32_SERVER_PORT = 5001
CAMERA_STREAM_PORT = 8081
VALID_COMMANDS    = ["LEFT", "RIGHT", "STOP", "OFF", "DOWN", "UP", "OTHER_SIDE"]
SOUND_TRACKS      = {
    1: "Go left",
    2: "Go right",
    3: "Do not proceed here",
    4: "Go down",
    5: "Go up",
    6: "Go to the other side",
}

# ─────────────────────────────────────────────
# CAMERA CONFIGURATION
# Each camera maps to a node it monitors
# ─────────────────────────────────────────────
DEFAULT_CAMERA_CONFIG = [
    {
        "id":         "cam_1",
        "name":       "Camera - Node 11",
        "url":        None,  # Uses the ESP32-CAM TCP stream on CAMERA_STREAM_PORT
        "node_id":    "node_11",
        "enabled":    True,
    },
    {
        "id":         "cam_2",
        "name":       "Camera - Node 12",
        "url":        None,
        "node_id":    "node_12",
        "enabled":    True,
    },
    {
        "id":         "cam_3",
        "name":       "Camera - Node 21",
        "url":        None,
        "node_id":    "node_21",
        "enabled":    True,
    },
    {
        "id":         "cam_4",
        "name":       "Camera - Node 22",
        "url":        None,
        "node_id":    "node_22",
        "enabled":    True,
    },
    {
        "id":         "cam_5",
        "name":       "Camera - Node 23",
        "url":        None,
        "node_id":    "node_23",
        "enabled":    True,
    },
]

def load_camera_configs():
    with closing(sqlite3.connect(DATABASE_PATH)) as database:
        with database:
            database.execute("""
                CREATE TABLE IF NOT EXISTS camera_registry (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT,
                    node_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL
                )
            """)
            database.executemany(
                "INSERT OR IGNORE INTO camera_registry (id, name, url, node_id, enabled) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        camera["id"],
                        camera["name"],
                        camera["url"],
                        camera["node_id"],
                        int(camera["enabled"]),
                    )
                    for camera in DEFAULT_CAMERA_CONFIG
                ],
            )
            for camera_id, node_id in database.execute(
                "SELECT id, node_id FROM camera_registry"
            ).fetchall():
                if node_id in LEGACY_NODE_ID_MAP:
                    database.execute(
                        "UPDATE camera_registry SET node_id = ? WHERE id = ?",
                        (canonical_camera_node_id(camera_id, node_id), camera_id),
                    )
            database.row_factory = sqlite3.Row
            rows = database.execute(
                "SELECT id, name, url, node_id, enabled FROM camera_registry ORDER BY rowid"
            ).fetchall()
    return [
        {**dict(row), "enabled": bool(row["enabled"])}
        for row in rows
    ]

CAMERA_CONFIG = load_camera_configs()

# ─────────────────────────────────────────────
# OPENCV CALIBRATION SETTINGS
# Follow these steps to calibrate:
# 1. Get an object exactly KNOWN_WIDTH cm wide
# 2. Hold it exactly KNOWN_DISTANCE cm from camera
# 3. Run script, note the pixel width of that object
# 4. Set CALIBRATION_PIXEL_WIDTH to that number
# ─────────────────────────────────────────────
KNOWN_DISTANCE          = 30.0   # cm
KNOWN_WIDTH             = 10.0   # cm
CALIBRATION_PIXEL_WIDTH = 120    # pixels — measure and update this

FOCAL_LENGTH = (CALIBRATION_PIXEL_WIDTH * KNOWN_DISTANCE) / KNOWN_WIDTH
print(f"[CAM] Focal length: {FOCAL_LENGTH:.2f}px")

# Blockage detection thresholds
BLOCKAGE_MIN_AREA      = 500    # minimum contour area in pixels
BLOCKAGE_THRESHOLD     = 60     # darkness threshold (0-255), lower = darker
BLOCKAGE_DISTANCE_WARN = 50.0   # cm — closer than this = blocked warning

# ─────────────────────────────────────────────
# CAMERA STATE
# ─────────────────────────────────────────────
class CameraState:
    def __init__(self, config):
        self.id       = config["id"]
        self.name     = config["name"]
        self.url      = config["url"]
        self.ip       = None
        self.node_id  = config["node_id"]
        self.enabled  = config["enabled"]
        self.blocked  = False
        self.debris_count   = 0
        self.closest_debris = None   # cm
        self.last_seen      = None
        self.last_seen_epoch = None
        self.error          = None
        self.latest_frame   = None   # JPEG bytes for live view
        self.tcp_frame      = None   # Raw JPEG received from ESP32-CAM
        self.tcp_seen       = 0.0
        self.tcp_sequence   = 0
        self.tcp_processed  = 0
        self.lock           = threading.Lock()

    def to_dict(self):
        last_seen_age = (
            max(0, time.time() - self.last_seen_epoch)
            if self.last_seen_epoch is not None
            else None
        )
        return {
            "id":             self.id,
            "name":           self.name,
            "ip":             self.ip,
            "node_id":        self.node_id,
            "enabled":        self.enabled,
            "blocked":        self.blocked,
            "debris_count":   self.debris_count,
            "closest_debris": self.closest_debris,
            "last_seen":      self.last_seen,
            "last_seen_age_seconds": last_seen_age,
            "stale":          self.enabled and (last_seen_age is None or last_seen_age > 5),
            "error":          self.error,
        }

cameras = {cfg["id"]: CameraState(cfg) for cfg in CAMERA_CONFIG}
cameras_lock = threading.RLock()

def camera_snapshot():
    with cameras_lock:
        return list(cameras.values())

def get_camera(camera_id):
    if not isinstance(camera_id, str):
        return None
    with cameras_lock:
        return cameras.get(camera_id)

def camera_matches_ip(cam, ip):
    with cam.lock:
        camera_ip = cam.ip
    url_host = urlparse(cam.url).hostname if cam.url else None
    return camera_ip == ip or url_host == ip

# ─────────────────────────────────────────────
# NODE STATE MANAGER
# ─────────────────────────────────────────────
class NodeState:
    def __init__(self, config):
        self.id               = config["id"]
        self.name             = config["name"]
        self.ip               = config["ip"]
        self.port             = config["port"]
        self.temperature      = None
        self.humidity         = None
        self.firmware_version = None
        self.seismic          = None
        self.blocked          = False
        self.thermal          = None    # MLX90640 hotspot (°C)
        self.thermal_min      = None
        self.thermal_max      = None
        self.thermal_img      = None    # rendered thermal JPeg bytes
        self.thermal_last     = None
        self.current_sign     = "OFF"
        self.override         = False
        self.override_command = "OFF"
        self.connected        = False
        self.last_seen        = None
        self.last_seen_epoch  = None
        self.conn             = None
        self.lock             = threading.Lock()

    def to_dict(self):
        last_seen_age = (
            max(0, time.time() - self.last_seen_epoch)
            if self.last_seen_epoch is not None
            else None
        )
        return {
            "id":               self.id,
            "name":             self.name,
            "ip":               self.ip,
            "port":             self.port,
            "temperature":      self.temperature,
            "humidity":         self.humidity,
            "firmware_version": self.firmware_version,
            "seismic":          self.seismic,
            "blocked":          self.blocked,
            "thermal":          self.thermal,
            "thermal_min":      self.thermal_min,
            "thermal_max":      self.thermal_max,
            "thermal_last":     self.thermal_last,
            "has_thermal":      self.thermal_img is not None,
            "current_sign":     self.current_sign,
            "override":         self.override,
            "override_command": self.override_command,
            "connected":        self.connected,
            "last_seen":        self.last_seen,
            "last_seen_epoch":  self.last_seen_epoch,
            "last_seen_age_seconds": last_seen_age,
            "stale":            self.connected and (last_seen_age is None or last_seen_age > 5),
        }

    def send_command(self, command):
        if command not in VALID_COMMANDS:
            return False
        with self.lock:
            if self.conn:
                try:
                    self.conn.send((command + '\n').encode('utf-8'))
                    self.current_sign = command
                    return True
                except Exception as e:
                    print(f"[{self.name}] Send error: {e}")
                    self.connected = False
                    self.conn      = None
        return False

    def send_sound(self, track):
        if type(track) is not int or track not in SOUND_TRACKS:
            return False
        with self.lock:
            if self.conn:
                try:
                    self.conn.send((f"PLAY:{track}\n").encode('utf-8'))
                    return True
                except Exception as e:
                    print(f"[{self.name}] Sound send error: {e}")
                    self.connected = False
                    self.conn      = None
        return False

nodes = {cfg["id"]: NodeState(cfg) for cfg in NODE_CONFIG}
nodes_lock = threading.RLock()

def valid_sensor_reading(value, minimum=-300, maximum=300):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        reading = float(value)
    except OverflowError:
        return None
    if not math.isfinite(reading) or reading == -127 or not minimum <= reading <= maximum:
        return None
    return round(reading, 1)

def node_snapshot():
    with nodes_lock:
        return list(nodes.values())

def get_node(node_id):
    if not isinstance(node_id, str):
        return None
    node_id = canonical_node_id(node_id)
    with nodes_lock:
        return nodes.get(node_id)

def valid_registry_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,32}', value) is not None

# ─────────────────────────────────────────────
# OPENCV DETECTION FUNCTIONS
# ─────────────────────────────────────────────
def estimate_distance(pixel_width):
    """Estimate distance in cm using pinhole camera model."""
    if pixel_width == 0:
        return None
    return (KNOWN_WIDTH * FOCAL_LENGTH) / pixel_width

def fetch_frame(url, timeout=3):
    """Fetch a JPEG frame from ESP32-CAM snapshot URL."""
    try:
        resp   = urllib.request.urlopen(url, timeout=timeout)
        data   = np.array(bytearray(resp.read()), dtype=np.uint8)
        frame  = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return frame
    except Exception as e:
        return None

def fetch_tcp_frame(cam):
    """Decode the newest JPEG received through the ESP32-CAM TCP stream."""
    with cam.lock:
        data = cam.tcp_frame
        seen = cam.tcp_seen
        sequence = cam.tcp_sequence
        if data is None or sequence == cam.tcp_processed or time.time() - seen > 5:
            return None
        cam.tcp_processed = sequence
    encoded = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR)

def read_exact(conn, length):
    chunks = bytearray()
    while len(chunks) < length:
        chunk = conn.recv(length - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)

def handle_camera_stream(conn, addr):
    """Receive a camera ID followed by length-prefixed JPEG frames."""
    target = None
    try:
        conn.settimeout(15)
        id_length_data = read_exact(conn, 1)
        if id_length_data is None:
            return
        id_length = id_length_data[0]
        if id_length == 0 or id_length > 32:
            raise ValueError(f"Invalid camera ID length: {id_length}")

        id_data = read_exact(conn, id_length)
        if id_data is None:
            return
        camera_id = id_data.decode('ascii')
        target = get_camera(camera_id)
        if target is None:
            raise ValueError(f"Unknown camera ID {camera_id!r}")

        print(f"[CAM] {target.name} stream connected from {addr[0]}:{addr[1]}")
        with target.lock:
            target.ip = addr[0]

        while True:
            header = read_exact(conn, 4)
            if header is None:
                break
            length = struct.unpack('<I', header)[0]
            if length == 0 or length > 200_000:
                raise ValueError(f"Invalid JPEG frame size: {length}")
            jpeg = read_exact(conn, length)
            if jpeg is None:
                break
            with target.lock:
                target.tcp_frame = jpeg
                target.tcp_seen = time.time()
                target.tcp_sequence += 1
                target.error = None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"[CAM] Stream error from {addr[0]}: {exc}")
    finally:
        conn.close()
        if target:
            print(f"[CAM] {target.name} stream disconnected")
        else:
            print(f"[CAM] Unregistered stream disconnected: {addr[0]}")

def start_camera_stream_server():
    """Accept ESP32-CAM streams on port 8081."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', CAMERA_STREAM_PORT))
    server.listen(5)
    print(f"[CAM] TCP frame stream listening on 0.0.0.0:{CAMERA_STREAM_PORT}")

    while True:
        conn, addr = server.accept()
        stream_thread = threading.Thread(
            target=handle_camera_stream, args=(conn, addr), daemon=True
        )
        stream_thread.start()

def detect_blockage(frame):
    """
    Run OpenCV blockage detection on a frame.
    Returns: (blocked, debris_count, closest_distance, annotated_frame)
    """
    if frame is None:
        return False, 0, None, None

    # Processing pipeline
    gray    = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(
        blurred, BLOCKAGE_THRESHOLD, 255, cv2.THRESH_BINARY_INV
    )

    contours, _ = cv2.findContours(
        thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    debris_count    = 0
    closest_dist    = None
    annotated       = frame.copy()

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < BLOCKAGE_MIN_AREA:
            continue

        debris_count += 1
        x, y, w, h   = cv2.boundingRect(cnt)
        distance      = estimate_distance(w)

        # Track closest object
        if distance is not None:
            if closest_dist is None or distance < closest_dist:
                closest_dist = distance

        # Color by distance
        if distance is not None and distance < 20:
            color = (0, 0, 255)      # Red — very close
        elif distance is not None and distance < 50:
            color = (0, 165, 255)    # Orange — medium
        else:
            color = (0, 255, 0)      # Green — far

        # Draw bounding box
        cv2.rectangle(annotated, (x, y), (x+w, y+h), color, 2)

        # Labels
        dist_str = f"{distance:.1f}cm" if distance else "?cm"
        cv2.putText(annotated, f"Debris #{debris_count}",
                    (x, y-25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        cv2.putText(annotated, f"Dist: {dist_str}",
                    (x, y-8),  cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    # HUD overlay
    blocked = (closest_dist is not None and
               closest_dist < BLOCKAGE_DISTANCE_WARN) or debris_count >= 2

    status_color = (0, 0, 255) if blocked else (0, 255, 0)
    status_text  = "BLOCKED" if blocked else "CLEAR"

    cv2.putText(annotated, f"Status: {status_text}",  (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
    cv2.putText(annotated, f"Debris: {debris_count}", (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    if closest_dist:
        cv2.putText(annotated, f"Closest: {closest_dist:.1f}cm", (10, 85),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    cv2.putText(annotated, f"FL: {FOCAL_LENGTH:.0f}px", (10, 110),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1)

    return blocked, debris_count, closest_dist, annotated

# ─────────────────────────────────────────────
# MLX90640 THERMAL FRAME RENDERING
# ─────────────────────────────────────────────
THERMAL_W        = 32     # MLX90640 native width
THERMAL_H        = 24     # MLX90640 native height
THERMAL_MIN_C    = 10.0   # colour-scale floor (°C)
THERMAL_MAX_C    = 60.0   # colour-scale ceiling (°C)
THERMAL_SCALE    = 10     # upscale factor for the preview image

def render_thermal(frame_values):
    """
    Convert a flat 32x24 MLX90640 frame (list of °C) into a JPEG heatmap.
    Returns (jpeg_bytes, min_c, max_c) or (None, None, None) on failure.
    """
    try:
        arr = np.array(frame_values, dtype=np.float32).reshape((THERMAL_H, THERMAL_W))
    except (ValueError, TypeError):
        return None, None, None

    arr[arr < -40.0] = np.nan                      # drop failed / invalid pixels
    valid = arr[~np.isnan(arr)]
    if valid.size == 0:
        return None, None, None

    vmin = min(float(valid.min()), THERMAL_MIN_C)
    vmax = max(float(valid.max()), THERMAL_MAX_C)
    if vmax - vmin < 1e-3:
        vmax = vmin + 1.0

    norm = np.nan_to_num((arr - vmin) / (vmax - vmin), nan=0.0)
    norm = np.clip(norm * 255.0, 0, 255).astype(np.uint8)

    heat = cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO)
    heat = cv2.resize(heat, (THERMAL_W * THERMAL_SCALE, THERMAL_H * THERMAL_SCALE),
                      interpolation=cv2.INTER_LINEAR)

    ok, jpeg = cv2.imencode('.jpg', heat, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        return None, None, None

    return jpeg.tobytes(), float(valid.min()), float(valid.max())

def camera_loop(cam):
    """Background thread — runs detection continuously for one camera."""
    print(f"[CAM] {cam.name} detection started")

    while True:
        if not cam.enabled:
            time.sleep(2)
            continue

        frame = fetch_tcp_frame(cam)
        if frame is None and cam.url:
            frame = fetch_frame(cam.url)

        if frame is None:
            with cam.lock:
                stream_recent = cam.tcp_frame is not None and time.time() - cam.tcp_seen <= 5
                if not stream_recent:
                    cam.error = "Cannot reach camera"
            time.sleep(0.05 if stream_recent else 2)
            continue

        blocked, debris_count, closest, annotated = detect_blockage(frame)

        # Encode annotated frame as JPEG for live view
        encoded, jpeg = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not encoded:
            with cam.lock:
                cam.error = "Could not encode preview frame"
            time.sleep(0.1)
            continue

        with cam.lock:
            cam.blocked        = blocked
            cam.debris_count   = debris_count
            cam.closest_debris = round(closest, 1) if closest else None
            cam.last_seen      = time.strftime("%H:%M:%S")
            cam.last_seen_epoch = time.time()
            cam.error          = None
            cam.latest_frame   = jpeg.tobytes()

        # Update the node this camera monitors
        node = get_node(cam.node_id)
        if node and not node.override:
            # Camera blockage overrides sensor blockage
            if blocked and not node.blocked:
                node.blocked = True
                cmd = auto_determine_sign(node)
                node.send_command(cmd)
                socketio.emit('node_update', node.to_dict())
                print(f"[CAM] {cam.name} -> BLOCKED -> Node {cam.node_id} -> {cmd}")
            elif not blocked and node.blocked:
                node.blocked = False
                cmd = auto_determine_sign(node)
                node.send_command(cmd)
                socketio.emit('node_update', node.to_dict())
                print(f"[CAM] {cam.name} -> CLEAR  -> Node {cam.node_id} -> {cmd}")

        # Emit camera update to dashboard
        socketio.emit('camera_update', cam.to_dict())

        time.sleep(0.1)

def start_camera_worker(cam):
    threading.Thread(
        target=camera_loop,
        args=(cam,),
        name=f"camera-{cam.id}",
        daemon=True,
    ).start()

# ─────────────────────────────────────────────
# CONNECTED DEVICES SCANNER
# ─────────────────────────────────────────────
device_cache      = []
device_cache_lock = threading.Lock()

def get_connected_devices():
    devices = []
    try:
        import glob
        for lease_file in glob.glob('/var/lib/NetworkManager/*.lease') + \
                          glob.glob('/var/lib/misc/dnsmasq.leases'):
            try:
                with open(lease_file, 'r') as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 4:
                            mac      = parts[1]
                            ip       = parts[2]
                            hostname = parts[3] if parts[3] != '*' else 'Unknown'
                            if ip.endswith('.1') or ip.endswith('.255'):
                                continue
                            node_name   = 'Unknown Device'
                            device_type = 'unknown'
                            for node in node_snapshot():
                                if node.ip == ip:
                                    node_name   = node.name
                                    device_type = 'esp32'
                                    break
                            for cam in camera_snapshot():
                                if camera_matches_ip(cam, ip):
                                    node_name   = cam.name
                                    device_type = 'camera'
                                    break
                            alive = ping_device(ip)
                            devices.append({
                                'ip': ip, 'mac': mac,
                                'hostname': hostname,
                                'node_name': node_name,
                                'type': device_type,
                                'alive': alive,
                                'last_seen': time.strftime("%H:%M:%S") if alive else 'Inactive'
                            })
            except Exception:
                continue
    except Exception:
        pass

    if devices:
        return devices

    # ARP fallback
    try:
        result = subprocess.run(['arp', '-n'], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split('\n'):
            match = re.search(
                r'(\d+\.\d+\.\d+\.\d+)\s+\S+\s+([0-9a-fA-F:]{17})', line
            )
            if match:
                ip  = match.group(1)
                mac = match.group(2)
                if ip.endswith('.1') or ip.endswith('.255'):
                    continue
                node_name   = 'Unknown Device'
                device_type = 'unknown'
                for node in node_snapshot():
                    if node.ip == ip:
                        node_name   = node.name
                        device_type = 'esp32'
                        break
                for cam in camera_snapshot():
                    if camera_matches_ip(cam, ip):
                        node_name   = cam.name
                        device_type = 'camera'
                        break
                alive = ping_device(ip)
                devices.append({
                    'ip': ip, 'mac': mac, 'hostname': 'N/A',
                    'node_name': node_name, 'type': device_type,
                    'alive': alive,
                    'last_seen': time.strftime("%H:%M:%S") if alive else 'Inactive'
                })
    except Exception as e:
        print(f"[ARP] Error: {e}")

    return devices

def ping_device(ip):
    try:
        import platform
        param  = '-n' if platform.system().lower() == 'windows' else '-c'
        result = subprocess.run(
            ['ping', param, '1', '-W', '1', ip],
            capture_output=True, timeout=2
        )
        return result.returncode == 0
    except Exception:
        return False

def scan_devices_loop():
    global device_cache
    while True:
        try:
            devices = get_connected_devices()
            with device_cache_lock:
                device_cache = devices
            socketio.emit('devices_update', devices)
        except Exception as e:
            print(f"[SCAN] Error: {e}")
        time.sleep(10)

# ─────────────────────────────────────────────
# TCP SERVER
# ─────────────────────────────────────────────
def find_node_by_ip(ip):
    for node in node_snapshot():
        if node.ip == ip:
            return node
    return None

def handle_esp32_connection(conn, addr):
    client_ip = addr[0]
    node      = find_node_by_ip(client_ip)
    registered = False

    try:
        buffer = ""
        while True:
            data = conn.recv(1024)
            if not data:
                break

            buffer += data.decode('utf-8')
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                line = line.strip()
                if not line:
                    continue

                try:
                    sensor_data = json.loads(line)
                    if not isinstance(sensor_data, dict):
                        raise ValueError("Sensor payload must be a JSON object")

                    reported_id = sensor_data.get("node_id")
                    if reported_id:
                        reported_node = get_node(reported_id)
                        if not reported_node:
                            print(f"[!] Unknown node ID {reported_id!r} from {client_ip}")
                            return
                        if registered and node is not reported_node:
                            print(f"[!] Node ID changed on connection from {client_ip}")
                            return
                        node = reported_node

                    if not node:
                        print(f"[!] Unknown ESP32 from {client_ip}; send a configured node_id")
                        return

                    if not registered:
                        with node.lock:
                            node.ip        = client_ip
                            node.conn      = conn
                            node.connected = True
                            node.last_seen_epoch = time.time()
                        registered = True
                        print(f"[+] {node.name} connected from {client_ip}")
                        socketio.emit('node_update', node.to_dict())

                    # Render the thermal frame outside the lock (it is CPU-bound)
                    frame    = sensor_data.get("thermal_frame")
                    thermal_img = thermal_min = thermal_max = None
                    if isinstance(frame, list) and len(frame) == THERMAL_W * THERMAL_H:
                        thermal_img, thermal_min, thermal_max = render_thermal(frame)

                    with node.lock:
                        if "temp" in sensor_data:
                            node.temperature = valid_sensor_reading(
                                sensor_data["temp"], minimum=-40, maximum=80
                            )
                        if "humidity" in sensor_data:
                            node.humidity = valid_sensor_reading(
                                sensor_data["humidity"], minimum=0, maximum=100
                            )
                        if "firmware_version" in sensor_data:
                            version = sensor_data["firmware_version"]
                            node.firmware_version = (
                                version.strip()[:40]
                                if isinstance(version, str) and version.strip()
                                else None
                            )
                        if "seismic" in sensor_data:
                            node.seismic = valid_sensor_reading(sensor_data["seismic"])
                        if "thermal" in sensor_data:
                            node.thermal = valid_sensor_reading(
                                sensor_data["thermal"], minimum=-40, maximum=300
                            )
                        if thermal_img:
                            node.thermal_img  = thermal_img
                            node.thermal_min  = round(thermal_min, 1)
                            node.thermal_max  = round(thermal_max, 1)
                            node.thermal_last = time.strftime("%H:%M:%S")
                        # Only update blocked from sensor if no camera is watching this node
                        cam_watching = any(c.node_id == node.id for c in camera_snapshot() if c.enabled)
                        if not cam_watching:
                            node.blocked = sensor_data.get("blocked", node.blocked)
                        node.last_seen = time.strftime("%H:%M:%S")
                        node.last_seen_epoch = time.time()

                    socketio.emit('node_update', node.to_dict())
                    cmd = node.override_command if node.override else auto_determine_sign(node)
                    node.send_command(cmd)

                except (json.JSONDecodeError, ValueError):
                    try:
                        temp = float(line)
                        if not node:
                            print(f"[!] Unknown ESP32 from {client_ip}; send JSON with node_id")
                            return
                        if not registered:
                            with node.lock:
                                node.ip        = client_ip
                                node.conn      = conn
                                node.connected = True
                                node.last_seen_epoch = time.time()
                            registered = True
                            print(f"[+] {node.name} connected from {client_ip}")
                            socketio.emit('node_update', node.to_dict())
                        with node.lock:
                            node.temperature = valid_sensor_reading(
                                temp, minimum=-40, maximum=80
                            )
                            node.last_seen   = time.strftime("%H:%M:%S")
                            node.last_seen_epoch = time.time()
                        socketio.emit('node_update', node.to_dict())
                        cmd = node.override_command if node.override else auto_determine_sign(node)
                        node.send_command(cmd)
                    except ValueError:
                        print(f"[{node.name}] Unparseable: {line}")

    except Exception as e:
        name = node.name if node else client_ip
        print(f"[{name}] Error: {e}")
    finally:
        if registered and node:
            print(f"[-] {node.name} disconnected")
            with node.lock:
                if node.conn is conn:
                    node.connected = False
                    node.conn      = None
            socketio.emit('node_update', node.to_dict())
        conn.close()

def auto_determine_sign(node):
    if node.blocked:
        return "STOP"
    if node.temperature and node.temperature > 50:
        return "STOP"
    if node.temperature and node.temperature > 35:
        return "RIGHT"
    return "LEFT"

def start_tcp_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((PI_HOST, ESP32_SERVER_PORT))
    server.listen(20)
    print(f"[TCP] Listening on {PI_HOST}:{ESP32_SERVER_PORT}")
    while True:
        try:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_esp32_connection, args=(conn, addr))
            t.daemon = True
            t.start()
        except Exception as e:
            print(f"[TCP] Error: {e}")

# ─────────────────────────────────────────────
# FLASK ROUTES
# ─────────────────────────────────────────────
@app.route('/')
def index():
    return render_template_string(
        HTML_TEMPLATE,
        nodes=node_snapshot(),
        sound_tracks=SOUND_TRACKS,
        floor_map_nodes=FLOOR_MAP_NODES,
        alert_thresholds=ALERT_THRESHOLDS,
    )

@app.route('/api/nodes')
def api_nodes():
    return jsonify([n.to_dict() for n in node_snapshot()])

@app.route('/api/settings/thresholds', methods=['GET', 'POST'])
def api_threshold_settings():
    global ALERT_THRESHOLDS

    if request.method == 'GET':
        return jsonify(ALERT_THRESHOLDS)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400

    thresholds = {}
    for key in ALERT_THRESHOLD_KEYS:
        value = data.get(key)
        if value is None or value == '':
            thresholds[key] = None
            continue
        if isinstance(value, bool):
            return jsonify({"error": f"{key} must be a number or blank"}), 400
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return jsonify({"error": f"{key} must be a number or blank"}), 400
        minimum = -40 if key.startswith("ambient_") else -40
        maximum = 100 if key.startswith("ambient_") else 300
        if not math.isfinite(number) or not minimum <= number <= maximum:
            return jsonify({
                "error": f"{key} must be between {minimum} and {maximum} °C"
            }), 400
        thresholds[key] = number

    for kind in ("ambient", "thermal"):
        warning = thresholds[f"{kind}_warning_c"]
        critical = thresholds[f"{kind}_critical_c"]
        if (warning is None) != (critical is None):
            return jsonify({
                "error": f"Set both {kind} warning and critical thresholds, or leave both blank"
            }), 400
        if warning is not None and warning >= critical:
            return jsonify({
                "error": f"{kind.capitalize()} critical threshold must be higher than warning"
            }), 400

    with closing(sqlite3.connect(DATABASE_PATH)) as database:
        with database:
            database.executemany(
                "UPDATE dashboard_settings SET value = ? WHERE key = ?",
                [(thresholds[key], key) for key in ALERT_THRESHOLD_KEYS],
            )
    ALERT_THRESHOLDS = thresholds
    socketio.emit('thresholds_update', ALERT_THRESHOLDS)
    return jsonify(ALERT_THRESHOLDS)

@app.route('/api/nodes', methods=['POST'])
def api_add_node():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400

    node_id = canonical_node_id(data.get('id'))
    name = data.get('name')
    if not valid_registry_id(node_id):
        return jsonify({"error": "Node ID must be 1-32 letters, numbers, underscores, or hyphens"}), 400
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        return jsonify({"error": "Node name must be between 1 and 80 characters"}), 400

    config = {
        "id": node_id,
        "name": name.strip(),
        "ip": "DHCP",
        "port": ESP32_SERVER_PORT,
    }
    node = NodeState(config)
    try:
        with nodes_lock:
            with closing(sqlite3.connect(DATABASE_PATH)) as database:
                with database:
                    database.execute(
                        "INSERT INTO node_registry (id, name, ip, port) VALUES (?, ?, ?, ?)",
                        (config["id"], config["name"], config["ip"], config["port"]),
                    )
            nodes[node_id] = node
    except sqlite3.IntegrityError:
        return jsonify({"error": "A node with that ID already exists"}), 409

    socketio.emit('nodes_changed')
    return jsonify({"status": "created", "node": node.to_dict()}), 201

@app.route('/api/cameras')
def api_cameras():
    return jsonify([c.to_dict() for c in camera_snapshot()])

@app.route('/api/cameras', methods=['POST'])
def api_add_camera():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400

    camera_id = data.get('id')
    name = data.get('name')
    node_id = canonical_camera_node_id(camera_id, data.get('node_id'))
    url = data.get('url', '')
    if not valid_registry_id(camera_id):
        return jsonify({"error": "Camera ID must be 1-32 letters, numbers, underscores, or hyphens"}), 400
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        return jsonify({"error": "Camera name must be between 1 and 80 characters"}), 400
    if not isinstance(node_id, str) or not get_node(node_id):
        return jsonify({"error": "Select an existing node for this camera"}), 400
    if url is None:
        url = ''
    if not isinstance(url, str):
        return jsonify({"error": "Camera URL must be a string"}), 400
    url = url.strip() or None
    if url:
        try:
            parsed_url = urlparse(url)
        except ValueError:
            return jsonify({"error": "Camera URL must be a valid HTTP or HTTPS URL"}), 400
        if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
            return jsonify({"error": "Camera URL must be a valid HTTP or HTTPS URL"}), 400
        if parsed_url.username is not None or parsed_url.password is not None:
            return jsonify({"error": "Camera URLs must not contain embedded credentials"}), 400

    config = {
        "id": camera_id,
        "name": name.strip(),
        "url": url,
        "node_id": node_id,
        "enabled": True,
    }
    camera = CameraState(config)
    try:
        with cameras_lock:
            with closing(sqlite3.connect(DATABASE_PATH)) as database:
                with database:
                    database.execute(
                        "INSERT INTO camera_registry (id, name, url, node_id, enabled) VALUES (?, ?, ?, ?, ?)",
                        (config["id"], config["name"], config["url"], config["node_id"], 1),
                    )
            cameras[camera_id] = camera
    except sqlite3.IntegrityError:
        return jsonify({"error": "A camera with that ID already exists"}), 409

    start_camera_worker(camera)
    socketio.emit('cameras_changed')
    return jsonify({"status": "created", "camera": camera.to_dict()}), 201

def toggle_camera_enabled(cam):
    with cam.lock:
        enabled = not cam.enabled
        with closing(sqlite3.connect(DATABASE_PATH)) as database:
            with database:
                result = database.execute(
                    "UPDATE camera_registry SET enabled = ? WHERE id = ?",
                    (int(enabled), cam.id),
                )
                if result.rowcount != 1:
                    raise RuntimeError(f"Camera {cam.id!r} is missing from the registry database")
        cam.enabled = enabled
    return enabled

@app.route('/api/camera/<cam_id>/feed')
def camera_feed(cam_id):
    """Live MJPEG feed for a specific camera — view in browser."""
    cam = get_camera(cam_id)
    if not cam:
        return "Camera not found", 404

    def generate():
        while True:
            with cam.lock:
                frame = cam.latest_frame
            if frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.1)

    return Response(generate(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/node/<node_id>/thermal')
def node_thermal(node_id):
    """Single latest thermal JPEG for a node."""
    node = get_node(node_id)
    if not node:
        return "Node not found", 404
    with node.lock:
        img = node.thermal_img
    if not img:
        return "No thermal data", 404
    return Response(img, mimetype='image/jpeg')

@app.route('/api/node/<node_id>/thermal/feed')
def node_thermal_feed(node_id):
    """Live MJPEG thermal preview for a node."""
    node = get_node(node_id)
    if not node:
        return "Node not found", 404

    def generate():
        while True:
            with node.lock:
                img = node.thermal_img
            if img:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + img + b'\r\n')
            time.sleep(0.2)

    return Response(generate(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/camera/<cam_id>/toggle', methods=['POST'])
def toggle_camera(cam_id):
    cam = get_camera(cam_id)
    if not cam:
        return jsonify({"error": "Camera not found"}), 404
    enabled = toggle_camera_enabled(cam)
    socketio.emit('camera_update', cam.to_dict())
    return jsonify({"status": "ok", "enabled": enabled})

@app.route('/api/devices')
def api_devices():
    with device_cache_lock:
        return jsonify(device_cache)

@app.route('/api/devices/scan', methods=['POST'])
def api_scan():
    devices = get_connected_devices()
    with device_cache_lock:
        global device_cache
        device_cache = devices
    socketio.emit('devices_update', devices)
    return jsonify({"status": "ok", "count": len(devices)})

@app.route('/api/command', methods=['POST'])
def api_command():
    data    = request.json
    node_id = data.get('node_id')
    command = data.get('command', '').upper()

    if command not in VALID_COMMANDS:
        return jsonify({"error": f"Invalid command. Use: {VALID_COMMANDS}"}), 400

    if node_id == 'all':
        results = {}
        for node in node_snapshot():
            node.override         = True
            node.override_command = command
            ok                    = node.send_command(command)
            results[node.id]      = ok
            socketio.emit('node_update', node.to_dict())
        return jsonify({"status": "broadcast", "results": results})

    else:
        node = get_node(node_id)
        if not node:
            return jsonify({"error": "Node not found"}), 404
        node.override         = True
        node.override_command = command
        ok                    = node.send_command(command)
        socketio.emit('node_update', node.to_dict())
        return jsonify({"status": "ok", "sent": ok})

@app.route('/api/sound', methods=['POST'])
def api_sound():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    node_id = data.get('node_id')
    track   = data.get('track')

    if type(track) is not int or track not in SOUND_TRACKS:
        return jsonify({"error": f"Invalid track. Use one of: {list(SOUND_TRACKS)}"}), 400

    if node_id != 'all' and not isinstance(node_id, str):
        return jsonify({"error": "Node not found"}), 404

    if node_id == 'all':
        results = {node.id: node.send_sound(track) for node in node_snapshot()}
        return jsonify({"status": "broadcast", "track": track, "results": results})

    node = get_node(node_id)
    if not node:
        return jsonify({"error": "Node not found"}), 404
    sent = node.send_sound(track)
    return jsonify({"status": "ok", "track": track, "sent": sent})

@app.route('/api/override/clear', methods=['POST'])
def clear_override():
    data    = request.json
    node_id = data.get('node_id')

    if node_id == 'all':
        for node in node_snapshot():
            node.override = False
            socketio.emit('node_update', node.to_dict())
        return jsonify({"status": "all overrides cleared"})

    node = get_node(node_id)
    if not node:
        return jsonify({"error": "Node not found"}), 404
    node.override = False
    socketio.emit('node_update', node.to_dict())
    return jsonify({"status": "override cleared"})

# ─────────────────────────────────────────────
# SOCKETIO EVENTS
# ─────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    for node in node_snapshot():
        emit('node_update', node.to_dict())
    for cam in camera_snapshot():
        emit('camera_update', cam.to_dict())
    with device_cache_lock:
        emit('devices_update', device_cache)

@socketio.on('send_command')
def on_send_command(data):
    node_id = data.get('node_id')
    command = data.get('command', '').upper()
    if command not in VALID_COMMANDS:
        return
    target_nodes = node_snapshot() if node_id == 'all' else [get_node(node_id)]
    target_nodes = [n for n in target_nodes if n]
    for node in target_nodes:
        node.override         = True
        node.override_command = command
        node.send_command(command)
        socketio.emit('node_update', node.to_dict())

@socketio.on('play_sound')
def on_play_sound(data):
    if not isinstance(data, dict):
        return
    node_id = data.get('node_id')
    track   = data.get('track')
    if type(track) is not int or track not in SOUND_TRACKS:
        return
    if node_id != 'all' and not isinstance(node_id, str):
        return
    target_nodes = node_snapshot() if node_id == 'all' else [get_node(node_id)]
    for node in (n for n in target_nodes if n):
        node.send_sound(track)

@socketio.on('clear_override')
def on_clear_override(data):
    node_id      = data.get('node_id')
    target_nodes = node_snapshot() if node_id == 'all' else [get_node(node_id)]
    target_nodes = [n for n in target_nodes if n]
    for node in target_nodes:
        node.override = False
        socketio.emit('node_update', node.to_dict())

@socketio.on('toggle_camera')
def on_toggle_camera(data):
    cam_id = data.get('cam_id')
    cam    = get_camera(cam_id)
    if cam:
        toggle_camera_enabled(cam)
        socketio.emit('camera_update', cam.to_dict())

# ─────────────────────────────────────────────
# HTML DASHBOARD
# ─────────────────────────────────────────────
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GaKuna Control</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="/static/socketio.min.js"></script>
<style>
  :root {
    --bg: #080c10; --surface: #0d1117; --surface2: #161b22;
    --border: #21262d; --accent: #00ff88; --accent2: #00aaff;
    --warn: #ffaa00; --danger: #ff4455; --text: #e6edf3; --muted: #7d8590;
    --left-color: #00aaff; --right-color: #aa00ff;
    --stop-color: #ff4455; --off-color: #7d8590;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Exo 2', sans-serif; min-height: 100vh; overflow-x: hidden; }
  body::before {
    content: ''; position: fixed; inset: 0;
    background-image: linear-gradient(rgba(0,255,136,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(0,255,136,0.03) 1px, transparent 1px);
    background-size: 40px 40px; pointer-events: none; z-index: 0;
  }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px 20px; position: relative; z-index: 1; }
  header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; padding-bottom: 20px; border-bottom: 1px solid var(--border); }
  .logo { display: flex; align-items: center; gap: 16px; }
  .logo-icon { width: 48px; height: 48px; background: linear-gradient(135deg, var(--accent), var(--accent2)); border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 24px; box-shadow: 0 0 20px rgba(0,255,136,0.3); }
  .logo-text h1 { font-size: 28px; font-weight: 800; letter-spacing: 2px; background: linear-gradient(90deg, var(--accent), var(--accent2)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
  .logo-text p { font-size: 11px; color: var(--muted); letter-spacing: 3px; text-transform: uppercase; font-family: 'Share Tech Mono', monospace; }
  .status-bar { display: flex; align-items: center; gap: 8px; font-family: 'Share Tech Mono', monospace; font-size: 12px; color: var(--muted); }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

  /* TABS */
  .tabs { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 28px; border-bottom: 1px solid var(--border); }
  .tab-btn { padding: 10px 20px; background: none; border: none; border-bottom: 2px solid transparent; color: var(--muted); font-family: 'Exo 2', sans-serif; font-size: 13px; font-weight: 600; letter-spacing: 1px; text-transform: uppercase; cursor: pointer; transition: all 0.2s; margin-bottom: -1px; }
  .tab-btn:hover { color: var(--text); }
  .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }

  /* BROADCAST */
  .broadcast-bar { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 20px 24px; margin-bottom: 32px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .broadcast-label { font-size: 11px; letter-spacing: 3px; text-transform: uppercase; color: var(--muted); font-family: 'Share Tech Mono', monospace; white-space: nowrap; }
  .btn-group { display: flex; gap: 8px; flex-wrap: wrap; flex: 1; }
  .btn { padding: 10px 20px; border: none; border-radius: 8px; font-family: 'Exo 2', sans-serif; font-weight: 600; font-size: 13px; letter-spacing: 1px; cursor: pointer; transition: all 0.2s; text-transform: uppercase; }
  .btn:active { transform: scale(0.96); }
  .btn-left  { background: rgba(0,170,255,0.15); color: var(--left-color);  border: 1px solid rgba(0,170,255,0.3); }
  .btn-right { background: rgba(170,0,255,0.15); color: var(--right-color); border: 1px solid rgba(170,0,255,0.3); }
  .btn-stop  { background: rgba(255,68,85,0.15);  color: var(--stop-color);  border: 1px solid rgba(255,68,85,0.3); }
  .btn-off   { background: rgba(125,133,144,0.15); color: var(--off-color); border: 1px solid rgba(125,133,144,0.3); }
  .btn-clear { background: rgba(0,255,136,0.1); color: var(--accent); border: 1px solid rgba(0,255,136,0.2); }
  .btn-scan  { background: rgba(0,170,255,0.1); color: var(--accent2); border: 1px solid rgba(0,170,255,0.2); }
  .btn-left:hover  { background: rgba(0,170,255,0.3); box-shadow: 0 0 12px rgba(0,170,255,0.3); }
  .btn-right:hover { background: rgba(170,0,255,0.3); box-shadow: 0 0 12px rgba(170,0,255,0.3); }
  .btn-stop:hover  { background: rgba(255,68,85,0.3);  box-shadow: 0 0 12px rgba(255,68,85,0.3); }
  .btn-off:hover   { background: rgba(125,133,144,0.3); }
  .btn-clear:hover { background: rgba(0,255,136,0.2); box-shadow: 0 0 12px rgba(0,255,136,0.2); }
  .btn-scan:hover  { background: rgba(0,170,255,0.2); box-shadow: 0 0 12px rgba(0,170,255,0.2); }

  .settings-intro { margin-bottom: 18px; }
  .settings-intro h2 { font-size: 18px; }
  .settings-intro p { margin-top: 5px; color: var(--muted); font-size: 12px; }
  .settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; }
  .settings-card { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 20px; }
  .settings-card h3 { font-size: 14px; margin-bottom: 5px; }
  .settings-help { color: var(--muted); font-size: 11px; line-height: 1.5; margin-bottom: 16px; }
  .settings-form { display: grid; gap: 12px; }
  .settings-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
  .settings-field { display: flex; flex-direction: column; gap: 5px; font-size: 11px; color: var(--muted); }
  .settings-field input, .settings-field select { width: 100%; padding: 9px 11px; background: var(--surface2); color: var(--text); border: 1px solid var(--border); border-radius: 7px; font: inherit; }
  .settings-field input:focus, .settings-field select:focus { outline: none; border-color: var(--accent); }
  .settings-status { min-height: 16px; color: var(--danger); font-size: 11px; }

  /* NODE CARDS */
  .nodes-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 20px; }
  .node-card { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; transition: border-color 0.3s, box-shadow 0.3s; }
  .node-card.connected { border-color: rgba(0,255,136,0.2); box-shadow: 0 0 20px rgba(0,255,136,0.05); }
  .node-card.override-active { border-color: rgba(255,170,0,0.4); box-shadow: 0 0 20px rgba(255,170,0,0.1); }
  .node-header { padding: 16px 20px; background: var(--surface2); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
  .node-name { font-weight: 600; font-size: 15px; }
  .node-ip { font-family: 'Share Tech Mono', monospace; font-size: 11px; color: var(--muted); margin-top: 2px; }
  .node-status { display: flex; align-items: center; gap: 6px; font-size: 11px; font-family: 'Share Tech Mono', monospace; }
  .conn-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); transition: all 0.3s; }
  .conn-dot.online { background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: pulse 2s infinite; }
  .sign-display { padding: 24px 20px 16px; display: flex; align-items: center; gap: 20px; }
  .sign-icon { width: 72px; height: 72px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 32px; flex-shrink: 0; transition: all 0.3s; border: 2px solid transparent; }
  .sign-icon.LEFT  { background: rgba(0,170,255,0.15);  border-color: rgba(0,170,255,0.4);  box-shadow: 0 0 16px rgba(0,170,255,0.2); }
  .sign-icon.RIGHT { background: rgba(170,0,255,0.15);  border-color: rgba(170,0,255,0.4);  box-shadow: 0 0 16px rgba(170,0,255,0.2); }
  .sign-icon.STOP  { background: rgba(255,68,85,0.15);  border-color: rgba(255,68,85,0.4);  box-shadow: 0 0 16px rgba(255,68,85,0.2); }
  .sign-icon.OFF   { background: rgba(125,133,144,0.1); border-color: rgba(125,133,144,0.2); }
  .sign-info { flex: 1; }
  .sign-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 2px; font-family: 'Share Tech Mono', monospace; }
  .sign-state { font-size: 26px; font-weight: 800; letter-spacing: 2px; margin: 2px 0 4px; }
  .sign-state.LEFT  { color: var(--left-color); }
  .sign-state.RIGHT { color: var(--right-color); }
  .sign-state.STOP  { color: var(--stop-color); }
  .sign-state.OFF   { color: var(--off-color); }
  .override-badge { display: inline-flex; align-items: center; gap: 4px; background: rgba(255,170,0,0.15); border: 1px solid rgba(255,170,0,0.3); color: var(--warn); font-size: 10px; padding: 2px 8px; border-radius: 20px; letter-spacing: 1px; text-transform: uppercase; font-family: 'Share Tech Mono', monospace; }
  .auto-badge { display: inline-flex; align-items: center; gap: 4px; background: rgba(0,255,136,0.1); border: 1px solid rgba(0,255,136,0.2); color: var(--accent); font-size: 10px; padding: 2px 8px; border-radius: 20px; letter-spacing: 1px; text-transform: uppercase; font-family: 'Share Tech Mono', monospace; }
  .sensor-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: var(--border); border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
  .sensor-item { background: var(--surface); padding: 12px 16px; text-align: center; }
  .sensor-value { font-family: 'Share Tech Mono', monospace; font-size: 18px; color: var(--text); }
  .sensor-value.danger { color: var(--danger); }
  .sensor-value.warn   { color: var(--warn); }
  .sensor-value.normal { color: var(--accent); }
  .sensor-unit { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-top: 2px; font-family: 'Share Tech Mono', monospace; }
  .node-controls { padding: 14px 20px; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .btn-sm { padding: 7px 14px; font-size: 11px; border-radius: 6px; }
  .last-seen { margin-left: auto; font-family: 'Share Tech Mono', monospace; font-size: 10px; color: var(--muted); }

  /* CAMERA CARDS */
  .cameras-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 20px; margin-bottom: 32px; }
  .camera-card { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }
  .camera-card.blocked-active { border-color: rgba(255,68,85,0.5); box-shadow: 0 0 20px rgba(255,68,85,0.15); }
  .camera-card.camera-stale .camera-feed { opacity: 0.45; }
  .camera-header { padding: 14px 20px; background: var(--surface2); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
  .camera-name { font-weight: 600; font-size: 14px; }
  .camera-node { font-size: 11px; color: var(--muted); font-family: 'Share Tech Mono', monospace; }
  .camera-feed { width: 100%; height: 220px; background: #000; object-fit: cover; display: block; }
  .camera-feed-placeholder { width: 100%; height: 220px; background: #0d1117; display: flex; align-items: center; justify-content: center; color: var(--muted); font-family: 'Share Tech Mono', monospace; font-size: 12px; }
  .camera-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: var(--border); border-top: 1px solid var(--border); }
  .camera-stat { background: var(--surface); padding: 10px 14px; text-align: center; }
  .camera-stat-value { font-family: 'Share Tech Mono', monospace; font-size: 16px; }
  .camera-stat-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-top: 2px; font-family: 'Share Tech Mono', monospace; }
  .camera-footer { padding: 12px 16px; display: flex; align-items: center; justify-content: space-between; }
  .blocked-badge { display: inline-flex; align-items: center; gap: 4px; background: rgba(255,68,85,0.15); border: 1px solid rgba(255,68,85,0.3); color: var(--danger); font-size: 10px; padding: 2px 8px; border-radius: 20px; letter-spacing: 1px; text-transform: uppercase; font-family: 'Share Tech Mono', monospace; }
  .clear-badge { display: inline-flex; align-items: center; gap: 4px; background: rgba(0,255,136,0.1); border: 1px solid rgba(0,255,136,0.2); color: var(--accent); font-size: 10px; padding: 2px 8px; border-radius: 20px; letter-spacing: 1px; text-transform: uppercase; font-family: 'Share Tech Mono', monospace; }
  .toggle-cam-btn { background: rgba(125,133,144,0.15); border: 1px solid rgba(125,133,144,0.3); color: var(--muted); padding: 5px 12px; border-radius: 6px; font-size: 11px; cursor: pointer; font-family: 'Share Tech Mono', monospace; }
  .toggle-cam-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* DEVICES TAB */
  .devices-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
  .devices-title { font-size: 11px; letter-spacing: 3px; text-transform: uppercase; color: var(--muted); font-family: 'Share Tech Mono', monospace; }
  .device-count { background: rgba(0,255,136,0.1); border: 1px solid rgba(0,255,136,0.2); color: var(--accent); font-size: 11px; padding: 3px 10px; border-radius: 20px; font-family: 'Share Tech Mono', monospace; }
  .devices-table { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }
  .devices-table-header { display: grid; grid-template-columns: 140px 160px 1fr 120px 100px; padding: 12px 20px; background: var(--surface2); border-bottom: 1px solid var(--border); font-size: 10px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); font-family: 'Share Tech Mono', monospace; }
  .device-row { display: grid; grid-template-columns: 140px 160px 1fr 120px 100px; padding: 14px 20px; border-bottom: 1px solid var(--border); align-items: center; transition: background 0.2s; }
  .device-row:last-child { border-bottom: none; }
  .device-row:hover { background: var(--surface2); }
  .device-ip { font-family: 'Share Tech Mono', monospace; font-size: 13px; color: var(--accent2); }
  .device-mac { font-family: 'Share Tech Mono', monospace; font-size: 11px; color: var(--muted); }
  .device-name { font-size: 13px; font-weight: 600; }
  .device-type { font-size: 10px; padding: 3px 8px; border-radius: 20px; text-transform: uppercase; letter-spacing: 1px; font-family: 'Share Tech Mono', monospace; width: fit-content; }
  .device-type.esp32   { background: rgba(0,170,255,0.15); border: 1px solid rgba(0,170,255,0.3); color: var(--accent2); }
  .device-type.camera  { background: rgba(170,0,255,0.15); border: 1px solid rgba(170,0,255,0.3); color: #aa00ff; }
  .device-type.unknown { background: rgba(125,133,144,0.15); border: 1px solid rgba(125,133,144,0.3); color: var(--muted); }
  .device-alive { display: flex; align-items: center; gap: 6px; font-size: 11px; font-family: 'Share Tech Mono', monospace; }
  .alive-dot { width: 8px; height: 8px; border-radius: 50%; }
  .alive-dot.online  { background: var(--accent); box-shadow: 0 0 6px var(--accent); animation: pulse 2s infinite; }
  .alive-dot.offline { background: var(--muted); }
  .empty-devices { padding: 48px 20px; text-align: center; color: var(--muted); font-family: 'Share Tech Mono', monospace; font-size: 13px; }
  .scan-info { font-size: 11px; color: var(--muted); font-family: 'Share Tech Mono', monospace; }

  /* LOG */
  .log-section { margin-top: 32px; background: var(--surface); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }
  .log-header { padding: 14px 20px; background: var(--surface2); border-bottom: 1px solid var(--border); font-size: 11px; letter-spacing: 3px; text-transform: uppercase; color: var(--muted); font-family: 'Share Tech Mono', monospace; display: flex; justify-content: space-between; align-items: center; }
  .log-clear { background: none; border: 1px solid var(--border); color: var(--muted); padding: 4px 10px; border-radius: 4px; font-size: 10px; cursor: pointer; font-family: 'Share Tech Mono', monospace; letter-spacing: 1px; }
  .log-clear:hover { border-color: var(--accent); color: var(--accent); }
  #log-output { padding: 16px 20px; height: 180px; overflow-y: auto; font-family: 'Share Tech Mono', monospace; font-size: 12px; line-height: 1.8; }
  .log-entry { color: var(--muted); }
  .log-entry .ts { color: var(--accent2); margin-right: 8px; }
  .log-entry.cmd .ts  { color: var(--warn); }
  .log-entry.conn .ts { color: var(--accent); }
  .log-entry.err .ts  { color: var(--danger); }
  .log-entry.cam .ts  { color: #aa00ff; }
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  /* ══════════════════════════════════════════════
     PROFESSIONAL THEME (overrides) — flat, modern
     ══════════════════════════════════════════════ */
  :root {
    --bg: #0e1116; --surface: #161a21; --surface2: #1c212a;
    --border: #262c36; --accent: #4c8dff; --accent2: #7aa2f7;
    --warn: #d29922; --danger: #f85149; --text: #e6e9ef; --muted: #8b95a5;
    --success: #3fb950;
    --left-color: #4c8dff; --right-color: #a371f7;
    --stop-color: #f85149; --off-color: #8b95a5;
  }
  .icon { width: 1em; height: 1em; display: inline-block; vertical-align: -0.14em; flex-shrink: 0; }
  body { font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif; background: var(--bg); font-size: 14px; }
  body::before { display: none; }
  button { font-family: 'Inter', system-ui, sans-serif; }
  .container { max-width: 1240px; }

  .logo-text p, .status-bar, .broadcast-label, .node-ip, .node-status, .sign-label,
  .sensor-value, .sensor-unit, .camera-node, .camera-stat-value, .camera-stat-label,
  .toggle-cam-btn, .devices-title, .device-count, .devices-table-header, .device-ip,
  .device-mac, .device-type, .device-alive, .empty-devices, .scan-info, .log-header,
  .log-clear, #log-output, .last-seen, .badge, .pill, .card-sub, .stat-value,
  .stat-label, .thermal-legend, .media-placeholder {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
  }

  .logo-icon { width: 44px; height: 44px; border-radius: 11px; background: var(--surface2);
               border: 1px solid var(--border); box-shadow: none; color: var(--accent); }
  .logo-icon .icon { width: 24px; height: 24px; }
  .logo-text h1 { font-size: 22px; font-weight: 700; letter-spacing: 0.4px;
                  background: none; -webkit-text-fill-color: currentColor; color: var(--text); }
  .logo-text p { font-size: 11px; letter-spacing: 1.5px; color: var(--muted); text-transform: none; }
  .status-dot { background: var(--success); box-shadow: none; }
  .status-bar { color: var(--muted); letter-spacing: 0.5px; }

  .tabs { gap: 2px; }
  .tab-btn { display: inline-flex; align-items: center; gap: 8px; border-radius: 8px 8px 0 0;
             font-weight: 500; letter-spacing: 0.2px; text-transform: none; font-size: 13.5px; }
  .tab-btn .icon { width: 16px; height: 16px; }
  .tab-btn.active { color: var(--text); border-bottom-color: var(--accent); }

  .broadcast-bar { border-radius: 14px; }
  .btn { display: inline-flex; align-items: center; gap: 7px; border-radius: 8px;
         font-weight: 500; letter-spacing: 0.2px; text-transform: none; font-size: 13px;
         padding: 9px 16px; }
  .btn .icon { width: 15px; height: 15px; }
  .btn-left, .btn-right, .btn-stop, .btn-off, .btn-clear, .btn-scan {
    background: var(--surface2); border: 1px solid var(--border); box-shadow: none; }
  .btn-left  { color: var(--left-color); }
  .btn-right { color: var(--right-color); }
  .btn-stop  { color: var(--danger); }
  .btn-off   { color: var(--muted); }
  .btn-clear { color: var(--success); }
  .btn-scan  { color: var(--accent); }
  .btn-left:hover, .btn-right:hover, .btn-stop:hover, .btn-off:hover,
  .btn-clear:hover, .btn-scan:hover { background: #232a34; border-color: #333c48; box-shadow: none; }

  .node-card, .camera-card, .devices-table, .log-section, .thermal-card {
    border-radius: 14px; background: var(--surface); border: 1px solid var(--border); }
  .node-card.connected { border-color: #2f3a48; box-shadow: none; }
  .node-card.stale { border-color: rgba(210,153,34,0.65); }
  .node-card.override-active { border-color: rgba(210,153,34,0.55); box-shadow: none; }
  .node-header, .camera-header, .log-header { background: var(--surface2); }
  .conn-dot.online, .alive-dot.online { background: var(--success); box-shadow: none; animation: pulse 2s infinite; }

  .sign-icon { border-radius: 12px; box-shadow: none !important; }
  .sign-icon .icon { width: 34px; height: 34px; }
  .sign-icon.LEFT  { background: rgba(76,141,255,0.12);  border-color: rgba(76,141,255,0.35); }
  .sign-icon.RIGHT { background: rgba(163,113,247,0.12); border-color: rgba(163,113,247,0.35); }
  .sign-icon.STOP  { background: rgba(248,81,73,0.12);   border-color: rgba(248,81,73,0.35); }
  .sign-icon.OFF   { background: var(--surface2);        border-color: var(--border); }
  .sign-state { font-weight: 700; }

  .auto-badge, .override-badge, .blocked-badge, .clear-badge, .stale-badge, .disabled-badge, .device-count {
    display: inline-flex; align-items: center; gap: 6px; letter-spacing: 0.5px; font-weight: 500; }
  .auto-badge .icon, .override-badge .icon,
  .blocked-badge .icon, .clear-badge .icon { width: 12px; height: 12px; }
  .auto-badge     { background: rgba(63,185,80,0.12);  border: 1px solid rgba(63,185,80,0.30);  color: var(--success); }
  .override-badge { background: rgba(210,153,34,0.12); border: 1px solid rgba(210,153,34,0.30); color: var(--warn); }
  .clear-badge    { background: rgba(63,185,80,0.12);  border: 1px solid rgba(63,185,80,0.30);  color: var(--success); }
  .blocked-badge  { background: rgba(248,81,73,0.12);  border: 1px solid rgba(248,81,73,0.30);  color: var(--danger); }
  .device-count   { background: var(--surface2); border: 1px solid var(--border); color: var(--muted); }

  .sensor-value { font-size: 17px; }
  .sensor-value.normal { color: var(--accent); }
  .sensor-value.danger { color: var(--danger); }
  .sensor-value.warn   { color: var(--warn); }
  .camera-feed, .camera-feed-placeholder { height: 240px; }
  .toggle-cam-btn { background: var(--surface2); border-color: var(--border); }
  .toggle-cam-btn:hover { border-color: var(--accent); color: var(--accent); }
  .log-clear { background: var(--surface2); border-color: var(--border); }

  /* THERMAL TAB */
  .thermal-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 20px; }
  .thermal-card { overflow: hidden; }
  .card-title { font-weight: 600; font-size: 14px; }
  .card-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .pill { font-size: 10px; padding: 3px 9px; border-radius: 20px; letter-spacing: 0.5px;
          text-transform: uppercase; border: 1px solid var(--border); color: var(--muted);
          background: var(--surface2); }
  .pill-live { color: var(--success); border-color: rgba(63,185,80,0.30); background: rgba(63,185,80,0.12); }
  .pill-stale, .stale-badge { color: var(--warn); border-color: rgba(210,153,34,0.35); background: rgba(210,153,34,0.10); }
  .disabled-badge { color: var(--muted); border-color: var(--border); background: var(--surface2); }
  .thermal-card.stale-thermal .thermal-feed { opacity: 0.45; }
  .thermal-view { position: relative; background: #05070a; }
  .thermal-feed { width: 100%; height: 240px; object-fit: cover; display: block; }
  .media-placeholder { position: absolute; inset: 0; display: flex; align-items: center;
                       justify-content: center; background: var(--surface2); color: var(--muted); font-size: 12px; }
  .stat-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px;
              background: var(--border); border-top: 1px solid var(--border); }
  .stat { background: var(--surface); padding: 12px; text-align: center; }
  .stat-value { font-size: 17px; color: var(--text); }
  .stat-label { font-size: 10px; color: var(--muted); text-transform: uppercase;
                letter-spacing: 1px; margin-top: 3px; }
  .thermal-legend { display: flex; align-items: center; gap: 10px; padding: 12px 20px 16px;
                    font-size: 11px; color: var(--muted); }
  .legend-bar { flex: 1; height: 8px; border-radius: 4px;
                background: linear-gradient(90deg, #000004, #420a68, #932667, #dd513a, #fca50a, #fcffa4); }

  .prototype-notice { padding: 13px 16px; margin-bottom: 18px; border: 1px solid rgba(210,153,34,0.35);
                      border-radius: 10px; background: rgba(210,153,34,0.08); color: var(--warn); line-height: 1.5; }
  .overview-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin-bottom: 18px; }
  .overview-stat, .map-card, .selected-node-card { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; }
  .overview-stat { padding: 16px; min-width: 0; }
  .overview-stat-label, .overview-stat-detail { color: var(--muted); font-size: 11px; }
  .overview-stat-value { font-size: 20px; font-weight: 650; margin: 8px 0 4px; overflow-wrap: anywhere; }
  .overview-stat-value.warn { color: var(--warn); }
  .overview-stat-value.danger { color: var(--danger); }
  .map-card { padding: 16px; }
  #overview-container.layout-a .map-card { margin-bottom: 18px; }
  .map-header, .selected-node-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 14px; }
  .floor-switcher, .media-switcher { display: flex; gap: 8px; flex-wrap: wrap; }
  .floor-switch, .media-switch { border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px;
                                  color: var(--muted); background: var(--surface2); cursor: pointer; }
  .floor-switch.active, .media-switch.active { border-color: var(--accent); color: var(--text); }
  .model-level-switcher, .model-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .model-level-btn, .model-tool-btn { border: 1px solid var(--border); border-radius: 8px; padding: 8px 11px;
                                      color: var(--muted); background: var(--surface2); cursor: pointer; }
  .model-level-btn.active, .model-tool-btn:hover { border-color: var(--accent); color: var(--text); }
  .model-explode-toggle { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); font-size: 11px; }
  .model-explode-toggle input { accent-color: var(--accent); }
  .model-path-status { margin: 8px 0; color: var(--muted); font-size: 11px; }
  .building-model-viewport { position: relative; overflow: hidden; border: 1px solid var(--border);
                             border-radius: 10px; background: #0c121a; }
  #building-model-canvas { display: block; width: 100%; height: 520px; touch-action: none; cursor: grab; }
  #building-model-canvas.dragging { cursor: grabbing; }
  .building-model-hint { position: absolute; left: 12px; bottom: 10px; padding: 6px 9px;
                         border: 1px solid rgba(139,149,165,0.2); border-radius: 7px;
                         background: rgba(14,17,22,0.88); color: var(--muted); font-size: 10px; pointer-events: none; }
  .model-node-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 8px; margin-top: 12px; }
  .model-node-pick { display: grid; grid-template-columns: 9px 1fr auto; align-items: center; gap: 7px;
                     padding: 9px 10px; border: 1px solid var(--border); border-radius: 8px;
                     background: var(--surface2); color: var(--text); text-align: left; cursor: pointer; }
  .model-node-pick:hover, .model-node-pick.selected { border-color: var(--accent); }
  .model-node-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .model-node-pick.online .model-node-dot { background: var(--success); }
  .model-node-pick.stale .model-node-dot { background: var(--warn); }
  .model-node-pick.blocked .model-node-dot { background: var(--danger); }
  .model-node-label { font-size: 11px; font-weight: 600; }
  .model-node-area, .model-node-state { color: var(--muted); font-size: 9px; }
  .model-node-area { grid-column: 2 / 4; }
  .model-node-state { justify-self: end; }
  .map-2d-details { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; }
  .map-2d-details summary { color: var(--muted); font-size: 12px; cursor: pointer; }
  .map-2d-content { padding-top: 12px; }
  .map-2d-content .floor-switcher { margin-bottom: 10px; }
  .floor-plan { display: none; }
  .floor-plan.active { display: block; }
  .map-stage { position: relative; overflow: hidden; border-radius: 8px; background: #fff; line-height: 0; }
  .map-image { display: block; width: 100%; height: auto; }
  .map-node-marker { position: absolute; z-index: 2; transform: translate(-50%, -50%); display: grid; gap: 2px;
                     min-width: 74px; padding: 6px 9px; border: 2px solid #536274; border-radius: 10px;
                     background: rgba(22,26,33,0.95); color: var(--text); text-align: center; line-height: 1.2;
                     cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.28); }
  .map-node-marker.edge-left { transform: translate(0, -50%); }
  .map-node-marker.edge-right { transform: translate(-100%, -50%); }
  .map-node-marker:hover, .map-node-marker:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .map-node-marker.online { border-color: var(--success); }
  .map-node-marker.stale { border-color: var(--warn); }
  .map-node-marker.offline { border-color: #536274; }
  .map-node-marker.blocked { border-color: var(--danger); }
  .map-node-number { font-weight: 700; font-size: 12px; }
  .map-node-state { font-size: 9px; color: #d7dce3; }
  .map-note { color: var(--muted); font-size: 11px; margin-top: 10px; }
  .selected-node-card { padding: 18px; }
  .selected-node-subtitle { color: var(--muted); font-size: 11px; margin-top: 4px; }
  .selected-node-details { display: flex; flex-wrap: wrap; gap: 8px 18px; color: var(--muted); font-size: 11px; }
  .selected-node-details strong { color: var(--text); font-weight: 500; }
  .overview-data-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px;
                         background: var(--border); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; margin: 14px 0; }
  .overview-data { padding: 12px; background: var(--surface); text-align: center; }
  .overview-data-value { font: 600 17px 'JetBrains Mono', monospace; }
  .overview-data-label { color: var(--muted); font-size: 10px; margin-top: 4px; }
  .overview-media { position: relative; min-height: 220px; background: #05070a; border-radius: 10px; overflow: hidden; margin-top: 12px; }
  .overview-media img { display: block; width: 100%; max-height: 440px; object-fit: contain; margin: 0 auto; }
  .overview-media-placeholder { position: absolute; inset: 0; min-height: 220px; display: flex; align-items: center;
                                justify-content: center; color: var(--muted); text-align: center; padding: 18px; }
  .overview-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 14px; }
  .overview-controls select, .threshold-form input { padding: 9px 11px; background: var(--surface2); color: var(--text);
                                                    border: 1px solid var(--border); border-radius: 7px; }
  .threshold-form { margin-top: 18px; }
  .threshold-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
  .threshold-help { color: var(--muted); font-size: 11px; line-height: 1.5; margin-bottom: 12px; }
  .node-version { color: var(--muted); font-size: 10px; margin-top: 3px; }

  /* ══════════════════════════════════════════════
     LAYOUT SWITCHING
     ══════════════════════════════════════════════ */
  .layout-toggle { display: inline-flex; align-items: center; gap: 10px; font-size: 11px; color: var(--muted); }
  .layout-toggle-btn { padding: 6px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface2);
                       color: var(--muted); cursor: pointer; transition: all 0.2s; }
  .layout-toggle-btn.active { border-color: var(--accent); color: var(--text); }
  .layout-toggle-btn:hover { border-color: var(--accent); }

  /* LAYOUT A: Classic (default) */
  #overview-container { display: grid; grid-template-columns: 1fr; gap: 18px; }
  #overview-container.layout-b { grid-template-columns: 1fr 1fr; gap: 14px; }

  .map-card { grid-column: 1; }
  .selected-node-card { grid-column: 1; }
  .overview-other-nodes { grid-column: 1; display: none; }

  /* LAYOUT B: Compact side-by-side (3D model left, selected node info right) */
  #overview-container.layout-b .map-card { grid-column: 1; grid-row: 1; overflow: hidden; display: flex; flex-direction: column; }
  #overview-container.layout-b .selected-node-card { grid-column: 2; grid-row: 1; }
  #overview-container.layout-b .overview-other-nodes { display: none; }
  
  #overview-container.layout-b .map-card .building-model-viewport { max-height: 500px; }
  #overview-container.layout-b .map-card .model-node-list { display: none; }

  .overview-other-nodes { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; padding: 14px; background: var(--surface); border: 1px solid var(--border); border-radius: 14px; }
  .overview-other-nodes-title { grid-column: 1 / -1; font-size: 12px; font-weight: 600; color: var(--text); letter-spacing: 0.5px; margin-bottom: 2px; }
  .node-preview-card { background: var(--surface2); border: 1px solid var(--border); border-radius: 10px; padding: 11px; display: flex; flex-direction: column; gap: 8px; cursor: pointer; transition: all 0.2s; }
  .node-preview-card:hover { border-color: var(--accent); }
  .node-preview-card.selected { border-color: var(--accent); background: rgba(76,141,255,0.08); }
  .node-preview-name { font-size: 11px; font-weight: 600; color: var(--text); }
  .node-preview-status { font-size: 10px; color: var(--muted); }
  .node-preview-sensors { display: flex; flex-direction: column; gap: 4px; font-size: 10px; }
  .node-preview-sensor { display: flex; justify-content: space-between; align-items: center; }
  .node-preview-sensor-label { color: var(--muted); }
  .node-preview-sensor-value { color: var(--accent); font-family: 'JetBrains Mono', monospace; font-size: 9px; }
  .node-preview-sensor-value.warn { color: var(--warn); }
  .node-preview-sensor-value.danger { color: var(--danger); }
  .node-preview-media-toggle { display: flex; gap: 4px; }
  .node-preview-media-btn { flex: 1; padding: 4px 6px; font-size: 9px; border: 1px solid var(--border); border-radius: 4px; background: var(--surface); color: var(--muted); cursor: pointer; transition: all 0.2s; }
  .node-preview-media-btn.active { border-color: var(--accent); color: var(--accent); }
  .node-preview-media-btn:hover { border-color: var(--accent); }

  @media (max-width: 600px) {
    .broadcast-bar { flex-direction: column; align-items: flex-start; }
    .settings-grid, .settings-fields, .threshold-grid { grid-template-columns: 1fr; }
    .overview-summary { grid-template-columns: 1fr; }
    .overview-data-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .map-card { padding: 10px; }
    #building-model-canvas { height: 400px; }
    .map-node-marker { min-width: 54px; padding: 4px 5px; }
    .map-node-state { font-size: 8px; }
    .map-header, .selected-node-heading { align-items: flex-start; flex-direction: column; }
    header { flex-direction: column; gap: 12px; align-items: flex-start; }
    .devices-table-header, .device-row { grid-template-columns: 120px 1fr 80px 80px; }
    .device-mac { display: none; }
    #overview-container.layout-b { grid-template-columns: 1fr; }
    #overview-container.layout-b .map-card, #overview-container.layout-b .selected-node-card, #overview-container.layout-b .overview-other-nodes { grid-column: auto; grid-row: auto; }
    .layout-toggle { flex-direction: column; align-items: flex-start; }
  }
</style>
</head>
<body>
<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">
  <symbol id="i-shield" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/></symbol>
  <symbol id="i-cpu" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/></symbol>
  <symbol id="i-camera" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></symbol>
  <symbol id="i-flame" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/></symbol>
  <symbol id="i-list" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></symbol>
  <symbol id="i-arrow-left" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 19-7-7 7-7"/><path d="M19 12H5"/></symbol>
  <symbol id="i-arrow-right" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></symbol>
  <symbol id="i-octagon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2"/><path d="M9 12h6"/></symbol>
  <symbol id="i-power" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/></symbol>
  <symbol id="i-rotate" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></symbol>
  <symbol id="i-scan" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/></symbol>
  <symbol id="i-activity" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></symbol>
  <symbol id="i-alert" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4M12 17h.01"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/></symbol>
  <symbol id="i-thermometer" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0Z"/></symbol>
  <symbol id="i-x" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></symbol>
</svg>
<div class="container">

  <header>
    <div class="logo">
      <div class="logo-icon"><svg class="icon"><use href="#i-shield"/></svg></div>
      <div class="logo-text">
        <h1>GAKUNA</h1>
        <p>Dynamic Evacuation Control System</p>
      </div>
    </div>
    <div class="status-bar">
      <div class="status-dot"></div>
      <span id="server-status">SYSTEM ONLINE</span>
    </div>
  </header>

  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('overview', this)"><svg class="icon"><use href="#i-shield"/></svg>Overview</button>
    <button class="tab-btn"        onclick="switchTab('nodes',   this)"><svg class="icon"><use href="#i-cpu"/></svg>Nodes</button>
    <button class="tab-btn"        onclick="switchTab('cameras', this)"><svg class="icon"><use href="#i-camera"/></svg>Cameras</button>
    <button class="tab-btn"        onclick="switchTab('thermal', this)"><svg class="icon"><use href="#i-flame"/></svg>Thermal</button>
    <button class="tab-btn"        onclick="switchTab('devices', this)"><svg class="icon"><use href="#i-list"/></svg>Devices</button>
    <button class="tab-btn"        onclick="switchTab('settings', this)">Settings</button>
  </div>

  <!-- TAB: EVACUATION OVERVIEW -->
  <div class="tab-panel active" id="tab-overview">
    <div class="prototype-notice">
      <strong>Prototype guidance:</strong> automatic signs are based on the existing temperature/blockage rules.
      They do not calculate or validate an evacuation route. Follow approved site procedures.
    </div>
    <div class="overview-summary" aria-live="polite">
      <section class="overview-stat">
        <div class="overview-stat-label">Hottest live thermal area</div>
        <div class="overview-stat-value" id="summary-hotspot-value">No live thermal data</div>
        <div class="overview-stat-detail" id="summary-hotspot-detail">MLX90640 hotspot · thresholds not configured</div>
      </section>
      <section class="overview-stat">
        <div class="overview-stat-label">Node connectivity</div>
        <div class="overview-stat-value" id="summary-health-value">Checking nodes…</div>
        <div class="overview-stat-detail" id="summary-health-detail">Stale readings are not treated as live.</div>
      </section>
      <section class="overview-stat">
        <div class="overview-stat-label">Reported blocked areas</div>
        <div class="overview-stat-value" id="summary-blocked-value">--</div>
        <div class="overview-stat-detail" id="summary-blocked-detail">From configured node/camera sensors.</div>
      </section>
    </div>

    <div id="overview-container" class="layout-a">
      <section class="map-card">
        <div class="map-header">
          <div>
            <h2>Interactive 3D building model</h2>
            <p class="selected-node-subtitle">Select a node in the model or list to inspect its live details.</p>
          </div>
          <div style="display: flex; align-items: center; gap: 20px;">
            <div class="layout-toggle">
              <span>Layout:</span>
              <button type="button" class="layout-toggle-btn active" data-layout="a" onclick="setOverviewLayout('a', this)">Classic</button>
              <button type="button" class="layout-toggle-btn" data-layout="b" onclick="setOverviewLayout('b', this)">Compact</button>
            </div>
            <div class="model-level-switcher" role="group" aria-label="Visible building floors">
              <button type="button" class="model-level-btn active" aria-pressed="true" onclick="setBuildingLevel('both', this)">Both floors</button>
              <button type="button" class="model-level-btn" aria-pressed="false" onclick="setBuildingLevel('1', this)">Floor 1</button>
              <button type="button" class="model-level-btn" aria-pressed="false" onclick="setBuildingLevel('2', this)">Floor 2</button>
            </div>
          </div>
        </div>
        <div class="model-tools">
          <button type="button" class="model-tool-btn" onclick="resetBuildingView()">Reset view</button>
          <button type="button" class="model-tool-btn" aria-label="Zoom in" onclick="zoomBuildingModel(1.2)">+</button>
          <button type="button" class="model-tool-btn" aria-label="Zoom out" onclick="zoomBuildingModel(0.83)">−</button>
          <label class="model-explode-toggle">
            <input type="checkbox" id="building-model-explode">
            Separate floors
          </label>
          <label class="model-explode-toggle">
            <input type="checkbox" id="building-model-cutaway" checked>
            Cutaway upper floor
          </label>
          <label class="model-explode-toggle">
            <input type="checkbox" id="building-model-path" checked>
            Show selected path
          </label>
        </div>
        <p class="model-path-status" id="building-path-status" role="status" aria-live="polite">Select a node to generate its schematic path to the Floor 1 exit.</p>
        <div class="building-model-viewport">
          <canvas id="building-model-canvas" aria-label="Interactive isometric 3D schematic of both building floors">
            Interactive building model. Use the node list below to select a node.
          </canvas>
          <div class="building-model-hint">Drag to orbit · scroll to zoom · Shift+drag to pan · click a node for details</div>
        </div>
        <div class="model-node-list" aria-label="Select a node">
          {% for marker in floor_map_nodes %}
          <button type="button" class="model-node-pick" data-model-node-id="{{ marker.id }}">
            <span class="model-node-dot" aria-hidden="true"></span>
            <span class="model-node-label">Node {{ marker.label }}</span>
            <span class="model-node-state">Loading</span>
            <span class="model-node-area">{{ marker.area }}</span>
          </button>
          {% endfor %}
        </div>
        <p class="map-note">The path is a prototype visualization on the schematic hallway/stair graph, not validated evacuation guidance. Reported blocks are marked unknown; routes are not automatically rerouted.</p>
        <details class="map-2d-details">
          <summary>View original 2D floor plans</summary>
          <div class="map-2d-content">
            <div class="floor-switcher" role="tablist" aria-label="Select 2D floor plan">
              <button type="button" class="floor-switch active" data-floor="1" onclick="selectFloor(1, this)">Floor 1 · Ground</button>
              <button type="button" class="floor-switch" data-floor="2" onclick="selectFloor(2, this)">Floor 2</button>
            </div>
            <div class="floor-plan active" id="floor-plan-1">
              <div class="map-stage">
                <img class="map-image" src="/static/maps/floor-1.png" alt="Floor 1 ground floor schematic for CDM Building 2">
                {% for marker in floor_map_nodes if marker.floor == 1 %}
                <button type="button" class="map-node-marker offline {% if marker.edge %}edge-{{ marker.edge }}{% endif %}" data-node-id="{{ marker.id }}"
                        style="left:{{ marker.left }}%;top:{{ marker.top }}%"
                        aria-label="Select Node {{ marker.label }}, {{ marker.area }}">
                  <span class="map-node-number">Node {{ marker.label }}</span>
                  <span class="map-node-state">Loading</span>
                </button>
                {% endfor %}
              </div>
              <p class="map-note">Schematic, not to scale. Dashed node links are not validated route recommendations.</p>
            </div>
            <div class="floor-plan" id="floor-plan-2">
              <div class="map-stage">
                <img class="map-image" src="/static/maps/floor-2.png" alt="Floor 2 schematic for CDM Building 2">
                {% for marker in floor_map_nodes if marker.floor == 2 %}
                <button type="button" class="map-node-marker offline {% if marker.edge %}edge-{{ marker.edge }}{% endif %}" data-node-id="{{ marker.id }}"
                        style="left:{{ marker.left }}%;top:{{ marker.top }}%"
                        aria-label="Select Node {{ marker.label }}, {{ marker.area }}">
                  <span class="map-node-number">Node {{ marker.label }}</span>
                  <span class="map-node-state">Loading</span>
                </button>
                {% endfor %}
              </div>
              <p class="map-note">Schematic, not to scale. Dashed node links are not validated route recommendations.</p>
            </div>
          </div>
        </details>
      </section>
      <section class="selected-node-card" aria-live="polite">
        <div class="selected-node-heading">
          <div>
            <h2 id="selected-node-name">Node 11 - Floor 1 left end</h2>
            <p class="selected-node-subtitle" id="selected-node-status">Waiting for node data</p>
          </div>
          <div class="selected-node-details">
            <span>ID: <strong id="selected-node-id">node_11</strong></span>
            <span>IP: <strong id="selected-node-ip">--</strong></span>
            <span>Firmware: <strong id="selected-node-firmware">--</strong></span>
            <span>Camera: <strong id="selected-camera-info">--</strong></span>
            <span>Last seen: <strong id="selected-node-seen">--</strong></span>
          </div>
        </div>
        <div class="overview-data-grid">
          <div class="overview-data"><div class="overview-data-value" id="selected-node-ambient">--</div><div class="overview-data-label">DHT22 ambient °C</div></div>
          <div class="overview-data"><div class="overview-data-value" id="selected-node-humidity">--</div><div class="overview-data-label">DHT22 humidity</div></div>
          <div class="overview-data"><div class="overview-data-value" id="selected-node-hotspot">--</div><div class="overview-data-label">MLX90640 hotspot °C</div></div>
          <div class="overview-data"><div class="overview-data-value" id="selected-node-blocked">--</div><div class="overview-data-label">Reported blockage</div></div>
        </div>
        <div class="floor-switcher" role="group" aria-label="Select node camera view">
          <button type="button" class="media-switch active" data-media="thermal" onclick="selectNodeMedia('thermal', this)">Thermal array</button>
          <button type="button" class="media-switch" data-media="camera" onclick="selectNodeMedia('camera', this)">ESP32-CAM</button>
        </div>
        <div class="overview-media">
          <img id="selected-thermal-feed" alt="Selected node MLX90640 thermal view" style="display:none">
          <img id="selected-camera-feed" alt="Selected node ESP32-CAM view" style="display:none">
          <div class="overview-media-placeholder" id="selected-media-placeholder">Waiting for thermal data…</div>
        </div>
        <div class="overview-controls">
          <label for="selected-audio-track">Audio command</label>
          <select id="selected-audio-track">
            {% for track, label in sound_tracks.items() %}
            <option value="{{ track }}">{{ label }}</option>
            {% endfor %}
          </select>
          <button type="button" class="btn btn-clear btn-sm" onclick="playSelectedSound()">Play on selected node</button>
        </div>
      </section>

      <div class="overview-other-nodes">
        <div class="overview-other-nodes-title">Other nodes</div>
        <div id="node-previews">
          <!-- Populated by JS -->
        </div>
      </div>
    </div>
  </div>

  <!-- TAB: NODES -->
  <div class="tab-panel" id="tab-nodes">
    <div class="broadcast-bar">
      <span class="broadcast-label">Broadcast All</span>
      <div class="btn-group">
        <button class="btn btn-left"  onclick="broadcastAll('LEFT')"><svg class="icon"><use href="#i-arrow-left"/></svg>Left</button>
        <button class="btn btn-right" onclick="broadcastAll('RIGHT')">Right<svg class="icon"><use href="#i-arrow-right"/></svg></button>
        <button class="btn btn-stop"  onclick="broadcastAll('STOP')"><svg class="icon"><use href="#i-octagon"/></svg>Stop</button>
        <button class="btn btn-off"   onclick="broadcastAll('OFF')"><svg class="icon"><use href="#i-power"/></svg>Off</button>
        <button class="btn btn-clear" onclick="clearAllOverrides()"><svg class="icon"><use href="#i-rotate"/></svg>Auto All</button>
      </div>
    </div>

    <div class="nodes-grid" id="nodes-grid">
      {% for node in nodes %}
      <div class="node-card" id="card-{{ node.id }}">
        <div class="node-header">
          <div>
            <div class="node-name">{{ node.name }}</div>
            <div class="node-ip" id="node-ip-{{ node.id }}">{{ node.ip }}:{{ node.port }}</div>
            <div class="node-version" id="firmware-{{ node.id }}">Firmware: --</div>
          </div>
          <div class="node-status">
            <div class="conn-dot" id="dot-{{ node.id }}"></div>
            <span id="conn-label-{{ node.id }}">OFFLINE</span>
          </div>
        </div>
        <div class="sign-display">
          <div class="sign-icon OFF" id="sign-icon-{{ node.id }}"></div>
          <div class="sign-info">
            <div class="sign-label">Current Sign</div>
            <div class="sign-state OFF" id="sign-state-{{ node.id }}">OFF</div>
            <span class="auto-badge" id="mode-badge-{{ node.id }}"><svg class="icon"><use href="#i-rotate"/></svg>AUTO · DEMO</span>
          </div>
        </div>
        <div class="sensor-grid">
          <div class="sensor-item">
            <div class="sensor-value normal" id="temp-{{ node.id }}">--</div>
            <div class="sensor-unit">°C Temp</div>
          </div>
          <div class="sensor-item">
            <div class="sensor-value normal" id="humidity-{{ node.id }}">--</div>
            <div class="sensor-unit">% Humidity</div>
          </div>
          <div class="sensor-item">
            <div class="sensor-value normal" id="blocked-{{ node.id }}">--</div>
            <div class="sensor-unit">Blocked</div>
          </div>
        </div>
        <div class="node-controls">
          <button class="btn btn-left btn-sm"  onclick="sendCommand('{{ node.id }}', 'LEFT')"><svg class="icon"><use href="#i-arrow-left"/></svg>Left</button>
          <button class="btn btn-right btn-sm" onclick="sendCommand('{{ node.id }}', 'RIGHT')">Right<svg class="icon"><use href="#i-arrow-right"/></svg></button>
          <button class="btn btn-stop btn-sm"  onclick="sendCommand('{{ node.id }}', 'STOP')"><svg class="icon"><use href="#i-octagon"/></svg>Stop</button>
          <button class="btn btn-off btn-sm"   onclick="sendCommand('{{ node.id }}', 'OFF')"><svg class="icon"><use href="#i-power"/></svg>Off</button>
          <button class="btn btn-clear btn-sm" onclick="clearOverride('{{ node.id }}')"><svg class="icon"><use href="#i-rotate"/></svg>Auto</button>
          <span class="last-seen" id="lastseen-{{ node.id }}"></span>
        </div>
        <div class="node-controls">
          <span class="last-seen">Play sound:</span>
          {% for track, label in sound_tracks.items() %}
          <button class="btn btn-clear btn-sm" onclick="playSound('{{ node.id }}', {{ track }})">{{ label }}</button>
          {% endfor %}
        </div>
      </div>
      {% endfor %}
    </div>

    <div class="log-section">
      <div class="log-header">
        <span style="display:inline-flex;align-items:center;gap:8px;"><svg class="icon"><use href="#i-activity"/></svg>Event Log</span>
        <button class="log-clear" onclick="clearLog()">CLEAR</button>
      </div>
      <div id="log-output"></div>
    </div>
  </div>

  <!-- TAB: CAMERAS -->
  <div class="tab-panel" id="tab-cameras">
    <div class="cameras-grid" id="cameras-grid">
      <!-- Populated by JS -->
    </div>
    <div id="no-cameras" style="display:none; padding:48px; text-align:center; color:var(--muted); font-family:'Share Tech Mono',monospace;">
      No cameras registered yet
    </div>
  </div>

  <!-- TAB: THERMAL -->
  <div class="tab-panel" id="tab-thermal">
    <div class="thermal-grid" id="thermal-grid">
      <!-- Populated by JS -->
    </div>
    <div id="no-thermal" style="display:none; padding:48px; text-align:center; color:var(--muted); font-family:'JetBrains Mono',monospace;">
      No nodes with a thermal sensor configured
    </div>
  </div>

  <!-- TAB: DEVICES -->
  <div class="tab-panel" id="tab-devices">
    <div class="devices-header">
      <div>
        <div class="devices-title" style="display:inline-flex;align-items:center;gap:8px;"><svg class="icon"><use href="#i-list"/></svg>Devices on Hotspot</div>
        <div class="scan-info" id="last-scan-time">Scanning every 10 seconds</div>
      </div>
      <div style="display:flex;align-items:center;gap:12px;">
        <span class="device-count" id="device-count">0 devices</span>
        <button class="btn btn-scan" onclick="triggerScan()"><svg class="icon"><use href="#i-scan"/></svg>Scan Now</button>
      </div>
    </div>
    <div class="devices-table">
      <div class="devices-table-header">
        <span>IP Address</span>
        <span>MAC Address</span>
        <span>Device Name</span>
        <span>Type</span>
        <span>Status</span>
      </div>
      <div id="devices-list">
        <div class="empty-devices">Scanning for devices...</div>
      </div>
    </div>
  </div>

  <!-- TAB: SETTINGS -->
  <div class="tab-panel" id="tab-settings">
    <div class="settings-intro">
      <h2>System Settings</h2>
      <p>Register nodes and cameras. Settings are saved locally on this Raspberry Pi.</p>
    </div>
    <div class="settings-grid">
      <section class="settings-card">
        <h3>Add a node</h3>
        <p class="settings-help">The node ID must match <code>NODE_ID</code> in its ESP32 firmware.</p>
        <form id="add-node-form" class="settings-form">
          <div class="settings-fields">
            <label class="settings-field">
              Node ID
              <input name="id" required maxlength="32" pattern="[A-Za-z0-9_-]{1,32}" placeholder="node_3">
            </label>
            <label class="settings-field">
              Display name
              <input name="name" required maxlength="80" placeholder="Node 13 - Floor 2">
            </label>
          </div>
          <button class="btn btn-clear" type="submit">Add Node</button>
          <div class="settings-status" id="node-add-status" role="status" aria-live="polite"></div>
        </form>
      </section>

      <section class="settings-card">
        <h3>Add a camera</h3>
        <p class="settings-help">The camera ID must match <code>CAMERA_ID</code> in its ESP32-CAM firmware. Leave URL blank for the TCP stream.</p>
        <form id="add-camera-form" class="settings-form">
          <div class="settings-fields">
            <label class="settings-field">
              Camera ID
              <input name="id" required maxlength="32" pattern="[A-Za-z0-9_-]{1,32}" placeholder="cam_2">
            </label>
            <label class="settings-field">
              Display name
              <input name="name" required maxlength="80" placeholder="Camera - Node 12">
            </label>
            <label class="settings-field">
              Monitored node
              <select name="node_id" required>
                {% for node in nodes %}
                <option value="{{ node.id }}">{{ node.name }} ({{ node.id }})</option>
                {% endfor %}
              </select>
            </label>
            <label class="settings-field">
              HTTP snapshot URL (optional)
              <input name="url" type="url" placeholder="http://192.168.1.20/capture">
            </label>
          </div>
          <button class="btn btn-clear" type="submit">Add Camera</button>
          <div class="settings-status" id="camera-add-status" role="status" aria-live="polite"></div>
        </form>
      </section>
      <section class="settings-card threshold-form">
        <h3>Site temperature alerts</h3>
        <p class="threshold-help">
          Set thresholds approved for this site. These classify displayed readings only; they do not choose an evacuation route
          or change the prototype automatic sign rules. Leave a pair blank until validated.
        </p>
        <form id="threshold-form" class="settings-form">
          <div class="threshold-grid">
            <label class="settings-field">Ambient warning °C
              <input name="ambient_warning_c" type="number" step="0.1" min="-40" max="100"
                     value="{{ alert_thresholds.ambient_warning_c if alert_thresholds.ambient_warning_c is not none else '' }}">
            </label>
            <label class="settings-field">Ambient critical °C
              <input name="ambient_critical_c" type="number" step="0.1" min="-40" max="100"
                     value="{{ alert_thresholds.ambient_critical_c if alert_thresholds.ambient_critical_c is not none else '' }}">
            </label>
            <label class="settings-field">Thermal warning °C
              <input name="thermal_warning_c" type="number" step="0.1" min="-40" max="300"
                     value="{{ alert_thresholds.thermal_warning_c if alert_thresholds.thermal_warning_c is not none else '' }}">
            </label>
            <label class="settings-field">Thermal critical °C
              <input name="thermal_critical_c" type="number" step="0.1" min="-40" max="300"
                     value="{{ alert_thresholds.thermal_critical_c if alert_thresholds.thermal_critical_c is not none else '' }}">
            </label>
          </div>
          <button class="btn btn-clear" type="submit">Save thresholds</button>
          <div class="settings-status" id="threshold-status" role="status" aria-live="polite"></div>
        </form>
      </section>
    </div>
  </div>

</div>

<script>
  const socket = io();
  const SIGN_ICON_ID = { LEFT: 'i-arrow-left', RIGHT: 'i-arrow-right', STOP: 'i-octagon', OFF: 'i-power' };
  const svgIcon = (id, cls = 'icon') => `<svg class="${cls}"><use href="#${id}"/></svg>`;
  const escapeHtml = value => String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));
  const MAP_NODE_IDS = ['node_11', 'node_12', 'node_21', 'node_22', 'node_23'];
  let alertThresholds = {{ alert_thresholds | tojson }};
  const overviewNodes = {};
  const overviewCameras = [];
  let selectedNodeId = 'node_11';
  let selectedMedia = 'thermal';
  const BUILDING_NODE_POSITIONS = {
    node_11: { floor: 1, x: 0.5, y: 5.1 },
    node_12: { floor: 1, x: 11.5, y: 5.1 },
    node_21: { floor: 2, x: 0.5, y: 5.1 },
    node_22: { floor: 2, x: 6, y: 5.1 },
    node_23: { floor: 2, x: 11.5, y: 5.1 }
  };
  const BUILDING_WIDTH = 12;
  const BUILDING_DEPTH = 6.1;
  const buildingView = {
    level: 'both', yaw: -0.72, elevation: 0.62, zoom: 1, panX: 0, panY: 0,
    exploded: false, cutaway: true, showPath: true
  };
  const BUILDING_ROUTE_POINTS = {
    node_11: { floor: 1, x: 0.5, y: 5.1, nodeId: 'node_11' },
    node_12: { floor: 1, x: 11.5, y: 5.1, nodeId: 'node_12' },
    node_21: { floor: 2, x: 0.5, y: 5.1, nodeId: 'node_21' },
    node_22: { floor: 2, x: 6, y: 5.1, nodeId: 'node_22' },
    node_23: { floor: 2, x: 11.5, y: 5.1, nodeId: 'node_23' },
    floor1_hall_mid: { floor: 1, x: 6, y: 5.1 },
    floor1_exit: { floor: 1, x: 6, y: 6.72 },
    floor2_left_stairs: { floor: 2, x: 0.62, y: 1.75 },
    floor1_left_stairs: { floor: 1, x: 0.62, y: 1.75 },
    floor2_mid_stairs: { floor: 2, x: 6, y: 0.82 },
    floor1_mid_stairs: { floor: 1, x: 6, y: 0.82 },
    floor2_right_stairs: { floor: 2, x: 11.38, y: 1.75 },
    floor1_right_stairs: { floor: 1, x: 11.38, y: 1.75 }
  };
  const BUILDING_ROUTE_EDGES = [
    { from: 'node_11', to: 'floor1_hall_mid', kind: 'hallway' },
    { from: 'node_12', to: 'floor1_hall_mid', kind: 'hallway' },
    { from: 'floor1_hall_mid', to: 'floor1_exit', kind: 'hallway' },
    { from: 'node_21', to: 'node_22', kind: 'hallway' },
    { from: 'node_22', to: 'node_23', kind: 'hallway' },
    { from: 'node_21', to: 'floor2_left_stairs', kind: 'hallway' },
    { from: 'node_22', to: 'floor2_mid_stairs', kind: 'hallway' },
    { from: 'node_23', to: 'floor2_right_stairs', kind: 'hallway' },
    { from: 'floor2_left_stairs', to: 'floor1_left_stairs', kind: 'stairs' },
    { from: 'floor2_mid_stairs', to: 'floor1_mid_stairs', kind: 'stairs' },
    { from: 'floor2_right_stairs', to: 'floor1_right_stairs', kind: 'stairs' },
    { from: 'floor1_left_stairs', to: 'node_11', kind: 'hallway' },
    { from: 'floor1_mid_stairs', to: 'floor1_hall_mid', kind: 'hallway' },
    { from: 'floor1_right_stairs', to: 'node_12', kind: 'hallway' }
  ];
  let buildingNodeHits = [];
  let buildingDrag = null;

  // ── TABS ───────────────────────────────────
  function switchTab(name, btn) {
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    btn.classList.add('active');
    if (name === 'overview') window.requestAnimationFrame(renderBuildingModel);
  }

  function selectFloor(floor, button) {
    document.querySelectorAll('.floor-switch').forEach(item => item.classList.remove('active'));
    document.querySelectorAll('.floor-plan').forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    document.getElementById(`floor-plan-${floor}`).classList.add('active');
  }

  function setBuildingLevel(level, button) {
    buildingView.level = level;
    document.querySelectorAll('.model-level-btn').forEach(item => {
      item.classList.toggle('active', item === button);
      item.setAttribute('aria-pressed', item === button ? 'true' : 'false');
    });
    renderBuildingModel();
  }

  function resetBuildingView() {
    buildingView.yaw = -0.72;
    buildingView.elevation = 0.62;
    buildingView.zoom = 1;
    buildingView.panX = 0;
    buildingView.panY = 0;
    renderBuildingModel();
  }

  function zoomBuildingModel(factor) {
    buildingView.zoom = Math.max(0.65, Math.min(2.5, buildingView.zoom * factor));
    renderBuildingModel();
  }

  function initializeBuildingModel() {
    const canvas = document.getElementById('building-model-canvas');
    if (!canvas) return;

    canvas.addEventListener('pointerdown', event => {
      if (event.button !== 0 && event.button !== 1 && event.button !== 2) return;
      buildingDrag = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        moved: false,
        pan: event.shiftKey || event.button !== 0
      };
      canvas.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    canvas.addEventListener('pointermove', event => {
      if (!buildingDrag || buildingDrag.pointerId !== event.pointerId) {
        const hit = nearestBuildingNode(event.offsetX, event.offsetY);
        canvas.style.cursor = hit ? 'pointer' : 'grab';
        return;
      }
      const dx = event.clientX - buildingDrag.x;
      const dy = event.clientY - buildingDrag.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) buildingDrag.moved = true;
      if (buildingDrag.pan) {
        buildingView.panX += dx;
        buildingView.panY += dy;
      } else {
        buildingView.yaw += dx * 0.008;
        buildingView.elevation = Math.max(0.2, Math.min(1.05, buildingView.elevation - dy * 0.006));
      }
      buildingDrag.x = event.clientX;
      buildingDrag.y = event.clientY;
      canvas.classList.add('dragging');
      renderBuildingModel();
    });
    canvas.addEventListener('pointerup', event => {
      if (!buildingDrag || buildingDrag.pointerId !== event.pointerId) return;
      const moved = buildingDrag.moved;
      buildingDrag = null;
      canvas.classList.remove('dragging');
      if (!moved) {
        const bounds = canvas.getBoundingClientRect();
        const hit = nearestBuildingNode(event.clientX - bounds.left, event.clientY - bounds.top);
        if (hit) selectOverviewNode(hit);
      }
    });
    canvas.addEventListener('pointercancel', () => {
      buildingDrag = null;
      canvas.classList.remove('dragging');
    });
    canvas.addEventListener('wheel', event => {
      event.preventDefault();
      buildingView.zoom = Math.max(0.65, Math.min(2.5, buildingView.zoom * Math.exp(-event.deltaY * 0.001)));
      renderBuildingModel();
    }, { passive: false });
    canvas.addEventListener('contextmenu', event => event.preventDefault());
    document.querySelectorAll('.model-node-pick').forEach(button => {
      button.addEventListener('click', () => selectOverviewNode(button.dataset.modelNodeId));
    });
    document.getElementById('building-model-explode').addEventListener('change', event => {
      buildingView.exploded = event.target.checked;
      renderBuildingModel();
    });
    document.getElementById('building-model-cutaway').addEventListener('change', event => {
      buildingView.cutaway = event.target.checked;
      renderBuildingModel();
    });
    document.getElementById('building-model-path').addEventListener('change', event => {
      buildingView.showPath = event.target.checked;
      renderBuildingModel();
    });
    window.addEventListener('resize', renderBuildingModel);
    renderBuildingModel();
  }

  function nearestBuildingNode(x, y) {
    let nearest = null;
    let nearestDistance = 25;
    buildingNodeHits.forEach(hit => {
      const distance = Math.hypot(hit.x - x, hit.y - y);
      if (distance < nearestDistance) {
        nearest = hit.id;
        nearestDistance = distance;
      }
    });
    return nearest;
  }

  function visibleBuildingFloors() {
    return buildingView.level === 'both' ? [1, 2] : [Number(buildingView.level)];
  }

  function shortestBuildingRoute(startId) {
    const pointIds = Object.keys(BUILDING_ROUTE_POINTS);
    const distance = {};
    const previous = {};
    const previousEdge = {};
    const unvisited = new Set(pointIds);
    pointIds.forEach(id => { distance[id] = Infinity; });
    distance[startId] = 0;

    while (unvisited.size) {
      let current = null;
      unvisited.forEach(id => {
        if (current === null || distance[id] < distance[current]) current = id;
      });
      if (current === null || !Number.isFinite(distance[current])) break;
      unvisited.delete(current);
      if (current === 'floor1_exit') break;

      BUILDING_ROUTE_EDGES.forEach(edge => {
        const next = edge.from === current ? edge.to :
          edge.to === current ? edge.from : null;
        if (!next || !unvisited.has(next)) return;
        const fromPoint = BUILDING_ROUTE_POINTS[current];
        const toPoint = BUILDING_ROUTE_POINTS[next];
        const fromHeight = fromPoint.floor === 1 ? 0 : 2.65;
        const toHeight = toPoint.floor === 1 ? 0 : 2.65;
        const cost = Math.hypot(
          toPoint.x - fromPoint.x,
          toPoint.y - fromPoint.y,
          toHeight - fromHeight
        );
        if (distance[current] + cost < distance[next]) {
          distance[next] = distance[current] + cost;
          previous[next] = current;
          previousEdge[next] = edge.kind;
        }
      });
    }

    if (!Number.isFinite(distance.floor1_exit)) return [];
    const routeIds = [];
    for (let cursor = 'floor1_exit'; cursor; cursor = previous[cursor]) {
      routeIds.unshift(cursor);
      if (cursor === startId) break;
    }
    if (routeIds[0] !== startId) return [];
    return routeIds.map((id, index) => ({
      id,
      ...BUILDING_ROUTE_POINTS[id],
      edgeFromPrevious: index ? previousEdge[id] : null
    }));
  }

  function buildingRouteStatus(route) {
    const unknownNodes = [];
    const blockedNodes = [];
    route.forEach(point => {
      if (!point.nodeId) return;
      const node = overviewNodes[point.nodeId];
      const freshness = nodeFreshness(node);
      if (freshness !== 'online') {
        unknownNodes.push(point.nodeId);
        return;
      }
      const cameraBlocked = overviewCameras.some(camera =>
        camera.node_id === point.nodeId && cameraFreshness(camera) === 'online' && camera.blocked
      );
      if (node.blocked || cameraBlocked) blockedNodes.push(point.nodeId);
    });
    if (blockedNodes.length) {
      return {
        kind: 'blocked',
        message: `Blockage reported at ${blockedNodes.join(', ')}; route status unknown. No auto-reroute.`
      };
    }
    if (unknownNodes.length) {
      return {
        kind: 'unknown',
        message: `Sensor status unknown at ${unknownNodes.join(', ')}; dashed path is schematic only.`
      };
    }
    return {
      kind: 'clear',
      message: 'No blockage reported by path nodes; route is still not verified.'
    };
  }

  function drawBuildingRouteSegment(context, start, end, layout, color, dashed) {
    const first = projectBuildingPoint(start.x, start.y, start.z, layout);
    const last = projectBuildingPoint(end.x, end.y, end.z, layout);
    context.save();
    context.lineCap = 'round';
    context.lineJoin = 'round';
    context.setLineDash(dashed ? [6, 5] : []);
    context.beginPath();
    context.moveTo(first.x, first.y);
    context.lineTo(last.x, last.y);
    context.strokeStyle = '#081018';
    context.lineWidth = 9;
    context.stroke();
    context.strokeStyle = color;
    context.lineWidth = 4;
    context.stroke();
    context.restore();
    return { first, last };
  }

  function drawBuildingChevron(context, first, last, color) {
    const x = first.x + (last.x - first.x) * 0.62;
    const y = first.y + (last.y - first.y) * 0.62;
    const angle = Math.atan2(last.y - first.y, last.x - first.x);
    const wing = 7;
    context.beginPath();
    context.moveTo(x - Math.cos(angle - 0.65) * wing, y - Math.sin(angle - 0.65) * wing);
    context.lineTo(x, y);
    context.lineTo(x - Math.cos(angle + 0.65) * wing, y - Math.sin(angle + 0.65) * wing);
    context.strokeStyle = color;
    context.lineWidth = 2.5;
    context.stroke();
  }

  function drawBuildingStairBadge(context, point, direction, color) {
    const center = projectBuildingPoint(point.x, point.y, point.z, point.layout);
    const text = `${direction} · STAIRS`;
    context.save();
    context.font = '700 9px Inter, sans-serif';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    const width = context.measureText(text).width + 12;
    context.fillStyle = 'rgba(8,12,16,0.94)';
    context.fillRect(center.x - width / 2, center.y - 9, width, 18);
    context.strokeStyle = color;
    context.lineWidth = 1;
    context.strokeRect(center.x - width / 2, center.y - 9, width, 18);
    context.fillStyle = color;
    context.fillText(text, center.x, center.y);
    context.restore();
  }

  function renderBuildingPath(context, layout, floors) {
    const status = document.getElementById('building-path-status');
    if (!buildingView.showPath) {
      status.textContent = 'Path overlay hidden. Schematic routes are not verified guidance.';
      return;
    }
    const route = shortestBuildingRoute(selectedNodeId);
    if (!route.length) {
      status.textContent = 'No schematic hallway/stair path is configured for this node.';
      return;
    }

    const routeState = buildingRouteStatus(route);
    const partial = route.some(point => !floors.includes(point.floor));
    const modeMessage = routeState.kind === 'blocked' ? 'BLOCKED · ROUTE UNKNOWN' :
      routeState.kind === 'unknown' ? 'STATUS UNKNOWN · PROTOTYPE PATH' : 'PROTOTYPE PATH · NOT VERIFIED';
    status.textContent = `${modeMessage} · ${routeState.message}${partial ? ' Partial view; another floor is hidden.' : ''}`;
    const color = routeState.kind === 'blocked' ? '#f85149' :
      routeState.kind === 'unknown' ? '#d29922' : '#4c8dff';
    const dashed = routeState.kind !== 'clear';

    context.save();
    for (let index = 1; index < route.length; index++) {
      const from = route[index - 1];
      const to = route[index];
      const fromVisible = floors.includes(from.floor);
      const toVisible = floors.includes(to.floor);
      const fromPoint = {
        x: from.x, y: from.y, z: buildingFloorBase(from.floor) + 0.14
      };
      const toPoint = {
        x: to.x, y: to.y, z: buildingFloorBase(to.floor) + 0.14
      };

      if (fromVisible && toVisible) {
        const line = drawBuildingRouteSegment(
          context, fromPoint, toPoint, layout, color, dashed
        );
        if (to.edgeFromPrevious === 'stairs') {
          const direction = from.floor > to.floor ? 'DOWN' : 'UP';
          drawBuildingStairBadge(context, {
            x: from.x,
            y: from.y,
            z: (fromPoint.z + toPoint.z) / 2,
            layout
          }, direction, color);
          if (!dashed) drawBuildingChevron(context, line.first, line.last, color);
        } else if (!dashed) {
          drawBuildingChevron(context, line.first, line.last, color);
        }
      } else if (to.edgeFromPrevious === 'stairs' && (fromVisible || toVisible)) {
        const visible = fromVisible ? fromPoint : toPoint;
        const visibleFloor = fromVisible ? from.floor : to.floor;
        const stub = {
          x: visible.x,
          y: visible.y,
          z: visible.z + (visibleFloor === 1 ? 0.72 : -0.72)
        };
        drawBuildingRouteSegment(context, visible, stub, layout, color, true);
        drawBuildingStairBadge(context, {
          x: visible.x,
          y: visible.y,
          z: (visible.z + stub.z) / 2,
          layout
        }, `TO F${visibleFloor === 1 ? 2 : 1}`, color);
      }
    }
    context.restore();
  }

  function buildingFloorBase(floor) {
    if (floor === 1) return 0;
    return buildingView.exploded ? 4.6 : 2.65;
  }

  function buildingPalette(kind) {
    const palettes = {
      slab: { top: '#2c3745', x: '#1b2530', y: '#222d3a', stroke: '#526174' },
      wall: { top: '#7f8da0', x: '#435163', y: '#59697d', stroke: '#9aa7b7' },
      room: { top: '#63373d', x: '#3e292f', y: '#512f36', stroke: '#b55b64' },
      stair: { top: '#735b35', x: '#43371f', y: '#5d492b', stroke: '#c69a50' },
      exit: { top: '#286b47', x: '#17452f', y: '#20573a', stroke: '#54c989' },
      node: { top: '#4c8dff', x: '#274b87', y: '#3669b0', stroke: '#a5c5ff' },
      stale: { top: '#d29922', x: '#785315', y: '#9a6c1b', stroke: '#f0c45b' },
      blocked: { top: '#f85149', x: '#852c2a', y: '#b33b37', stroke: '#ff9b94' },
      offline: { top: '#7b8795', x: '#434d58', y: '#5d6876', stroke: '#a3adba' }
    };
    return palettes[kind];
  }

  function addBuildingPrism(boxes, x1, x2, y1, y2, z1, z2, kind) {
    boxes.push({ x1, x2, y1, y2, z1, z2, colors: buildingPalette(kind) });
  }

  function addBuildingArea(boxes, labels, x1, x2, y1, y2, base, kind, label, stairAxis) {
    const colors = buildingPalette(kind);
    addBuildingPrism(boxes, x1 + 0.04, x2 - 0.04, y1 + 0.04, y2 - 0.04,
      base + 0.02, base + 0.08, kind);
    const wallHeight = kind === 'room' ? 0.46 : 0.36;
    const thickness = 0.1;
    addBuildingPrism(boxes, x1, x2, y1, y1 + thickness, base + 0.04, base + wallHeight, kind);
    addBuildingPrism(boxes, x1, x2, y2 - thickness, y2, base + 0.04, base + wallHeight, kind);
    addBuildingPrism(boxes, x1, x1 + thickness, y1, y2, base + 0.04, base + wallHeight, kind);
    addBuildingPrism(boxes, x2 - thickness, x2, y1, y2, base + 0.04, base + wallHeight, kind);

    if (kind === 'stair') {
      const steps = 5;
      if (stairAxis === 'y') {
        const run = (y2 - y1 - 0.3) / steps;
        for (let index = 0; index < steps; index++) {
          addBuildingPrism(boxes, x1 + 0.16, x2 - 0.16,
            y1 + 0.15 + index * run, y1 + 0.15 + (index + 1) * run,
            base + 0.08, base + 0.12 + (index + 1) * 0.055, 'stair');
        }
      } else {
        const run = (x2 - x1 - 0.3) / steps;
        for (let index = 0; index < steps; index++) {
          addBuildingPrism(boxes,
            x1 + 0.15 + index * run, x1 + 0.15 + (index + 1) * run,
            y1 + 0.16, y2 - 0.16, base + 0.08,
            base + 0.12 + (index + 1) * 0.055, 'stair');
        }
      }
    }

    labels.push({
      text: label,
      x: (x1 + x2) / 2,
      y: (y1 + y2) / 2,
      z: base + wallHeight + 0.08,
      color: colors.stroke
    });
  }

  function createBuildingLevel(floor, boxes, labels) {
    const boxStart = boxes.length;
    const base = buildingFloorBase(floor);
    const depth = BUILDING_DEPTH;
    addBuildingPrism(boxes, 0, BUILDING_WIDTH, 0, depth, base - 0.2, base, 'slab');
    addBuildingArea(boxes, labels, 1.25, 4.95, 0.28, 3.2, base, 'room', 'ROOM · LEFT');
    addBuildingArea(boxes, labels, 7.05, 10.75, 0.28, 3.2, base, 'room', 'ROOM · RIGHT');
    addBuildingArea(boxes, labels, 0.2, 1.05, 0.28, 3.2, base, 'stair', 'STAIRS · LEFT', 'y');
    addBuildingArea(boxes, labels, 10.95, 11.8, 0.28, 3.2, base, 'stair', 'STAIRS · RIGHT', 'y');
    addBuildingArea(boxes, labels, 5.4, 6.6, 0.28, 1.35, base, 'stair', 'STAIRS · MID', 'x');
    labels.push({ text: 'CORRIDOR', x: 6, y: 4.1, z: base + 0.08, color: '#9aa7b7' });

    const wallBase = base + 0.04;
    const wallTop = base + 0.58;
    addBuildingPrism(boxes, 0, BUILDING_WIDTH, -0.12, 0, wallBase, wallTop, 'wall');
    addBuildingPrism(boxes, -0.12, 0, 0, depth, wallBase, wallTop, 'wall');
    addBuildingPrism(boxes, BUILDING_WIDTH, BUILDING_WIDTH + 0.12, 0, depth, wallBase, wallTop, 'wall');
    if (floor === 1) {
      addBuildingPrism(boxes, 0, 5.32, depth, depth + 0.12, wallBase, wallTop, 'wall');
      addBuildingPrism(boxes, 6.68, BUILDING_WIDTH, depth, depth + 0.12, wallBase, wallTop, 'wall');
      addBuildingPrism(boxes, 5.52, 6.48, depth + 0.2, depth + 0.62,
        base, base + 0.1, 'exit');
      labels.push({ text: 'EXIT', x: 6, y: depth + 0.88, z: base + 0.22, color: '#70dfa1' });
    } else {
      addBuildingPrism(boxes, 0, BUILDING_WIDTH, depth, depth + 0.12, wallBase, wallTop, 'wall');
    }

    Object.entries(BUILDING_NODE_POSITIONS).forEach(([id, position]) => {
      if (position.floor !== floor) return;
      const node = overviewNodes[id];
      const freshness = nodeFreshness(node);
      const cameraBlocked = overviewCameras.some(camera =>
        camera.node_id === id && cameraFreshness(camera) === 'online' && camera.blocked
      );
      const blocked = freshness === 'online' && ((node && node.blocked) || cameraBlocked);
      const kind = blocked ? 'blocked' :
        freshness === 'online' ? 'node' :
        freshness === 'stale' ? 'stale' : 'offline';
      const labelX = position.x < BUILDING_WIDTH / 2 ? position.x + 1.1 :
        position.x > BUILDING_WIDTH / 2 ? position.x - 1.1 : position.x;
      addBuildingPrism(boxes, position.x - 0.17, position.x + 0.17,
        position.y - 0.17, position.y + 0.17, base + 0.08, base + 0.48, kind);
      labels.push({
        text: `Node ${id.slice(-2)} · ${blocked ? 'BLOCKED' : freshness.toUpperCase()}`,
        x: labelX,
        y: position.y,
        z: base + 0.64,
        color: blocked ? '#ff9b94' : freshness === 'online' ? '#b7d1ff' :
          freshness === 'stale' ? '#f0c45b' : '#c1c8d2'
      });
      if (node && freshness === 'online' && node.temperature != null &&
          Number.isFinite(Number(node.temperature))) {
        labels.push({
          text: `${Number(node.temperature).toFixed(1)} °C`,
          x: labelX,
          y: position.y,
          z: base + 0.82,
          color: '#d8e0ea'
        });
      }
    });
    boxes.slice(boxStart).forEach(box => { box.floor = floor; });
  }

  function buildingModelLayout(width, height, floors) {
    const bases = floors.map(buildingFloorBase);
    const minZ = Math.min(...bases) - 0.2;
    const maxZ = Math.max(...bases) + 0.9;
    const cosYaw = Math.cos(buildingView.yaw);
    const sinYaw = Math.sin(buildingView.yaw);
    const sinElevation = Math.sin(buildingView.elevation);
    const cosElevation = Math.cos(buildingView.elevation);
    const projected = [];
    [0, BUILDING_WIDTH].forEach(x => [-0.3, BUILDING_DEPTH + 1.1].forEach(y => {
      [minZ, maxZ].forEach(z => projected.push({
        x: x * cosYaw - y * sinYaw,
        y: (x * sinYaw + y * cosYaw) * sinElevation - z * cosElevation
      }));
    }));
    const minX = Math.min(...projected.map(point => point.x));
    const maxX = Math.max(...projected.map(point => point.x));
    const minY = Math.min(...projected.map(point => point.y));
    const maxY = Math.max(...projected.map(point => point.y));
    const scale = Math.min((width - 72) / (maxX - minX), (height - 72) / (maxY - minY)) *
      buildingView.zoom;
    return {
      scale,
      centerX: width / 2 - (minX + maxX) * scale / 2 + buildingView.panX,
      centerY: height / 2 - (minY + maxY) * scale / 2 + buildingView.panY,
      cosYaw,
      sinYaw,
      sinElevation,
      cosElevation
    };
  }

  function projectBuildingPoint(x, y, z, layout) {
    return {
      x: layout.centerX + (x * layout.cosYaw - y * layout.sinYaw) * layout.scale,
      y: layout.centerY + ((x * layout.sinYaw + y * layout.cosYaw) * layout.sinElevation -
        z * layout.cosElevation) * layout.scale
    };
  }

  function buildingBoxFaces(box, layout) {
    const points = (coords, color) => {
      const projected = coords.map(([x, y, z]) => projectBuildingPoint(x, y, z, layout));
      const depth = coords.reduce((sum, [x, y, z]) =>
        sum + (x * layout.sinYaw + y * layout.cosYaw) * layout.cosElevation +
          z * layout.sinElevation, 0) / coords.length;
      return {
        points: projected,
        color,
        depth,
        stroke: box.colors.stroke,
        opacity: buildingView.level === 'both' && buildingView.cutaway && box.floor === 2 ? 0.32 : 1
      };
    };
    const xSide = layout.sinYaw > 0 ? box.x2 : box.x1;
    const ySide = layout.cosYaw > 0 ? box.y2 : box.y1;
    const faces = [];
    if (box.kind !== 'slab') {
      faces.push(points([
        [box.x1, box.y1, box.z2], [box.x2, box.y1, box.z2],
        [box.x2, box.y2, box.z2], [box.x1, box.y2, box.z2]
      ], box.colors.top));
    }
    faces.push(points([
      [xSide, box.y1, box.z1], [xSide, box.y2, box.z1],
      [xSide, box.y2, box.z2], [xSide, box.y1, box.z2]
    ], box.colors.x));
    faces.push(points([
      [box.x1, ySide, box.z1], [box.x2, ySide, box.z1],
      [box.x2, ySide, box.z2], [box.x1, ySide, box.z2]
    ], box.colors.y));
    return faces;
  }

  function drawBuildingLabel(context, label, layout) {
    const point = projectBuildingPoint(label.x, label.y, label.z, layout);
    context.save();
    context.font = '600 10px Inter, sans-serif';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillStyle = label.color;
    context.shadowColor = '#080c10';
    context.shadowBlur = 4;
    context.fillText(label.text, point.x, point.y);
    context.restore();
  }

  function renderBuildingNodeList() {
    document.querySelectorAll('.model-node-pick').forEach(button => {
      const id = button.dataset.modelNodeId;
      const node = overviewNodes[id];
      const freshness = nodeFreshness(node);
      const cameraBlocked = overviewCameras.some(camera =>
        camera.node_id === id && cameraFreshness(camera) === 'online' && camera.blocked
      );
      const blocked = freshness === 'online' && ((node && node.blocked) || cameraBlocked);
      button.classList.remove('online', 'stale', 'offline', 'blocked');
      button.classList.add(freshness === 'unregistered' ? 'offline' : freshness);
      if (blocked) button.classList.add('blocked');
      button.classList.toggle('selected', id === selectedNodeId);
      button.setAttribute('aria-pressed', id === selectedNodeId ? 'true' : 'false');
      button.querySelector('.model-node-state').textContent = blocked ? 'Blocked' :
        freshness === 'unregistered' ? 'Not registered' :
        freshness === 'online' ? 'Online' :
        freshness === 'stale' ? 'Stale' : 'Offline';
    });
  }

  function renderBuildingModel() {
    renderBuildingNodeList();
    const canvas = document.getElementById('building-model-canvas');
    if (!canvas) return;
    const bounds = canvas.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;

    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    const pixelWidth = Math.round(bounds.width * pixelRatio);
    const pixelHeight = Math.round(bounds.height * pixelRatio);
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    const context = canvas.getContext('2d');
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, bounds.width, bounds.height);
    context.fillStyle = '#0c121a';
    context.fillRect(0, 0, bounds.width, bounds.height);

    const floors = visibleBuildingFloors();
    const layout = buildingModelLayout(bounds.width, bounds.height, floors);
    const boxes = [];
    const labels = [];
    floors.forEach(floor => createBuildingLevel(floor, boxes, labels));
    const faces = boxes.flatMap(box => buildingBoxFaces(box, layout));
    faces.sort((left, right) => left.depth - right.depth);
    faces.forEach(face => {
      context.beginPath();
      context.moveTo(face.points[0].x, face.points[0].y);
      face.points.slice(1).forEach(point => context.lineTo(point.x, point.y));
      context.closePath();
      context.fillStyle = face.color;
      context.globalAlpha = face.opacity;
      context.fill();
      context.strokeStyle = face.stroke;
      context.lineWidth = 0.65;
      context.stroke();
      context.globalAlpha = 1;
    });
    renderBuildingPath(context, layout, floors);
    labels.forEach(label => drawBuildingLabel(context, label, layout));

    buildingNodeHits = [];
    Object.entries(BUILDING_NODE_POSITIONS).forEach(([id, position]) => {
      if (!floors.includes(position.floor)) return;
      const point = projectBuildingPoint(position.x, position.y,
        buildingFloorBase(position.floor) + 0.5, layout);
      buildingNodeHits.push({ id, x: point.x, y: point.y });
      if (id === selectedNodeId) {
        context.beginPath();
        context.arc(point.x, point.y, 13, 0, Math.PI * 2);
        context.strokeStyle = '#ffffff';
        context.lineWidth = 2;
        context.stroke();
      }
    });

    context.font = '600 10px Inter, sans-serif';
    context.textAlign = 'left';
    context.textBaseline = 'top';
    floors.forEach((floor, index) => {
      context.fillStyle = '#cbd5e1';
      context.fillText(floor === 1 ? 'FLOOR 1 · GROUND' : 'FLOOR 2', 14, 14 + index * 16);
    });
  }

  function selectOverviewNode(nodeId) {
    stopOverviewStreams();
    selectedNodeId = nodeId;
    renderSelectedNode();
    renderBuildingModel();
  }

  function selectNodeMedia(media, button) {
    stopOverviewStreams();
    selectedMedia = media;
    document.querySelectorAll('.media-switch').forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    renderSelectedMedia();
  }

  function stopOverviewStreams() {
    [document.getElementById('selected-thermal-feed'),
     document.getElementById('selected-camera-feed')].forEach(image => {
      image.onload = null;
      image.onerror = null;
      image.removeAttribute('src');
      delete image.dataset.source;
      delete image.dataset.failed;
      delete image.dataset.loaded;
    });
  }

  // ── SOCKET ─────────────────────────────────
  socket.on('connect',    () => addLog('Connected to GaKuna server', 'conn'));
  socket.on('disconnect', () => addLog('Disconnected from server',   'err'));
  socket.on('node_update',    node    => updateNodeCard(node));
  socket.on('nodes_changed', () => window.location.reload());
  socket.on('camera_update',  cam     => updateCameraCard(cam));
  socket.on('cameras_changed', () => window.location.reload());
  socket.on('devices_update', devices => renderDevices(devices));
  socket.on('thresholds_update', thresholds => {
    alertThresholds = thresholds;
    renderOverview();
  });

  document.querySelectorAll('.map-node-marker').forEach(marker => {
    marker.addEventListener('click', () => selectOverviewNode(marker.dataset.nodeId));
  });
  initializeBuildingModel();
  registerSettingsForm(
    'threshold-form',
    '/api/settings/thresholds',
    ['ambient_warning_c', 'ambient_critical_c', 'thermal_warning_c', 'thermal_critical_c'],
    'threshold-status'
  );
  window.setInterval(renderOverview, 1000);
  Promise.all([
    fetch('/api/nodes').then(response => {
      if (!response.ok) throw new Error('Could not load nodes');
      return response.json();
    }),
    fetch('/api/cameras').then(response => {
      if (!response.ok) throw new Error('Could not load cameras');
      return response.json();
    }),
    fetch('/api/settings/thresholds').then(response => {
      if (!response.ok) throw new Error('Could not load alert thresholds');
      return response.json();
    })
  ]).then(([nodes, cameras, thresholds]) => {
    alertThresholds = thresholds;
    nodes.forEach(updateNodeCard);
    cameras.forEach(updateCameraCard);
    renderOverview();
  }).catch(error => addLog(`Overview data error: ${error.message}`, 'err'));

  // ── NODE CARDS ─────────────────────────────
  function nodeFreshness(node) {
    if (!node) return 'unregistered';
    if (!node.connected) return 'offline';
    const ageAtReceipt = Number(node.last_seen_age_seconds);
    const ageNow = Number.isFinite(ageAtReceipt)
      ? ageAtReceipt + (Date.now() - node.__receivedAt) / 1000
      : Infinity;
    if (node.stale || !node.__receivedAt || ageNow > 5) return 'stale';
    return 'online';
  }

  function cameraFreshness(camera) {
    if (!camera) return 'unregistered';
    if (!camera.enabled) return 'disabled';
    const ageAtReceipt = Number(camera.last_seen_age_seconds);
    const ageNow = Number.isFinite(ageAtReceipt)
      ? ageAtReceipt + (Date.now() - camera.__receivedAt) / 1000
      : Infinity;
    return camera.stale || !camera.__receivedAt || ageNow > 5 ? 'stale' : 'online';
  }

  function updateCameraStatus(camera) {
    const statusEl = document.getElementById(`cam-status-${camera.id}`);
    const card = document.getElementById(`cam-card-${camera.id}`);
    if (!statusEl) return;
    const freshness = cameraFreshness(camera);
    const isBlocked = freshness === 'online' && camera.blocked;
    statusEl.className = isBlocked ? 'blocked-badge' :
      freshness === 'online' ? 'clear-badge' :
      freshness === 'stale' ? 'stale-badge' : 'disabled-badge';
    statusEl.textContent = isBlocked ? 'BLOCKED' :
      freshness === 'online' ? 'CLEAR' :
      freshness === 'stale' ? 'STALE' : 'DISABLED';
    if (card) card.classList.toggle('blocked-active', isBlocked);
    if (card) card.classList.toggle('camera-stale', freshness !== 'online');
  }

  function sensorSeverity(value, kind) {
    if (value == null) return 'normal';
    const warning = alertThresholds[`${kind}_warning_c`];
    const critical = alertThresholds[`${kind}_critical_c`];
    if (critical != null && value >= critical) return 'danger';
    if (warning != null && value >= warning) return 'warn';
    return 'normal';
  }

  function updateNodeCard(node) {
    node.__receivedAt = Date.now();
    overviewNodes[node.id] = node;
    renderOverview();

    const card      = document.getElementById(`card-${node.id}`);
    const dot       = document.getElementById(`dot-${node.id}`);
    const connLabel = document.getElementById(`conn-label-${node.id}`);
    const signIcon  = document.getElementById(`sign-icon-${node.id}`);
    const signState = document.getElementById(`sign-state-${node.id}`);
    const modeBadge = document.getElementById(`mode-badge-${node.id}`);
    const tempEl    = document.getElementById(`temp-${node.id}`);
    const humidityEl = document.getElementById(`humidity-${node.id}`);
    const blockedEl = document.getElementById(`blocked-${node.id}`);
    const lastSeen  = document.getElementById(`lastseen-${node.id}`);
    const firmwareEl = document.getElementById(`firmware-${node.id}`);
    const ipEl = document.getElementById(`node-ip-${node.id}`);
    if (!card) return;

    const freshness = nodeFreshness(node);
    dot.classList.toggle('online', freshness === 'online');
    connLabel.textContent = freshness.toUpperCase();
    card.classList.toggle('connected', freshness === 'online');
    card.classList.toggle('stale', freshness === 'stale');

    const sign = node.current_sign || 'OFF';
    signIcon.className   = `sign-icon ${sign}`;
    signIcon.innerHTML   = svgIcon(SIGN_ICON_ID[sign] || 'i-power');
    signState.className  = `sign-state ${sign}`;
    signState.textContent = sign;

    card.classList.toggle('override-active', node.override);
    modeBadge.className   = node.override ? 'override-badge' : 'auto-badge';
    modeBadge.innerHTML   = node.override
      ? svgIcon('i-alert') + 'OVERRIDE'
      : svgIcon('i-rotate') + 'AUTO · DEMO';

    if (node.temperature != null) {
      tempEl.textContent = parseFloat(node.temperature).toFixed(1);
      tempEl.className = `sensor-value ${sensorSeverity(node.temperature, 'ambient')}`;
    } else {
      tempEl.textContent = '--';
      tempEl.className = 'sensor-value normal';
    }
    if (humidityEl) {
      humidityEl.textContent = node.humidity == null ? '--' : `${parseFloat(node.humidity).toFixed(1)}`;
    }
    if (node.blocked != null) {
      blockedEl.textContent = node.blocked ? 'YES' : 'NO';
      blockedEl.className   = 'sensor-value ' + (node.blocked ? 'danger' : 'normal');
    }
    if (node.last_seen) lastSeen.textContent = node.last_seen;
    if (firmwareEl) firmwareEl.textContent = `Firmware: ${node.firmware_version || '--'}`;
    if (ipEl) ipEl.textContent = `${node.ip || 'DHCP'}:${node.port || 5001}`;

    updateThermalCard(node);
  }

  const MAP_NODE_AREAS = {
    node_11: 'Floor 1 · Left end',
    node_12: 'Floor 1 · Right end',
    node_21: 'Floor 2 · Left end',
    node_22: 'Floor 2 · Middle',
    node_23: 'Floor 2 · Right end'
  };

  function renderOverview() {
    const mappedNodes = MAP_NODE_IDS.map(id => overviewNodes[id]).filter(Boolean);
    let online = 0;
    let stale = 0;
    let offline = MAP_NODE_IDS.length - mappedNodes.length;
    const blocked = [];

    document.querySelectorAll('.map-node-marker').forEach(marker => {
      const node = overviewNodes[marker.dataset.nodeId];
      const freshness = nodeFreshness(node);
      const cameraBlocked = overviewCameras.some(camera =>
        camera.node_id === marker.dataset.nodeId &&
        cameraFreshness(camera) === 'online' &&
        camera.blocked
      );
      const nodeBlocked = freshness === 'online' && node.blocked;
      marker.classList.remove('online', 'stale', 'offline', 'blocked');
      marker.classList.add(freshness === 'unregistered' ? 'offline' : freshness);
      if (cameraBlocked || nodeBlocked) marker.classList.add('blocked');
      if (freshness === 'online') online++;
      else if (freshness === 'stale') stale++;
      else if (freshness === 'offline') offline++;
      if (cameraBlocked || nodeBlocked) {
        blocked.push(MAP_NODE_AREAS[marker.dataset.nodeId]);
      }
      marker.querySelector('.map-node-state').textContent =
        freshness === 'unregistered' ? 'Not registered' :
        cameraBlocked || nodeBlocked ? 'Blocked' :
        freshness === 'stale' ? 'Stale data' :
        freshness === 'online' ? 'Online' : 'Offline';
    });
    overviewCameras.forEach(updateCameraStatus);
    Object.values(overviewNodes).forEach(node => {
      if (document.getElementById(`t-status-${node.id}`)) updateThermalStatus(node);
    });

    const liveThermalNodes = MAP_NODE_IDS
      .map(id => overviewNodes[id])
      .filter(node => node && nodeFreshness(node) === 'online' && node.thermal_max != null);
    liveThermalNodes.sort((a, b) => b.thermal_max - a.thermal_max);
    const hotspotValue = document.getElementById('summary-hotspot-value');
    const hotspotDetail = document.getElementById('summary-hotspot-detail');
    hotspotValue.className = 'overview-stat-value';
    if (liveThermalNodes.length) {
      const hottest = liveThermalNodes[0];
      const severity = sensorSeverity(hottest.thermal_max, 'thermal');
      const thresholdsConfigured = alertThresholds.thermal_warning_c != null;
      hotspotValue.textContent = `${MAP_NODE_AREAS[hottest.id]} · ${Number(hottest.thermal_max).toFixed(1)} °C`;
      hotspotValue.classList.add(severity);
      hotspotDetail.textContent = `MLX90640 hotspot at ${hottest.thermal_last || 'latest frame'} · ${
        thresholdsConfigured ? (severity === 'normal' ? 'Below warning threshold' : severity.toUpperCase()) : 'No thermal thresholds configured'
      }`;
    } else {
      hotspotValue.textContent = 'No live thermal data';
      hotspotDetail.textContent = 'Offline, stale, or not-yet-reported thermal data is excluded.';
    }

    document.getElementById('summary-health-value').textContent =
      `${online} online · ${stale} stale · ${offline} offline`;
    document.getElementById('summary-health-detail').textContent =
      `${mappedNodes.length} of ${MAP_NODE_IDS.length} map nodes registered; stale/offline data is not treated as live.`;
    document.getElementById('summary-blocked-value').textContent =
      blocked.length ? `${blocked.length} reported` : 'None reported';
    document.getElementById('summary-blocked-detail').textContent =
      blocked.length ? blocked.join(' · ') : 'Offline or stale areas have unknown status.';

    renderSelectedNode();
    renderBuildingModel();
  }

  function renderSelectedNode() {
    const node = overviewNodes[selectedNodeId];
    const freshness = nodeFreshness(node);
    const area = MAP_NODE_AREAS[selectedNodeId] || 'Unmapped node';
    document.getElementById('selected-node-name').textContent =
      node ? node.name : `Node ${selectedNodeId.replace('node_', '')}`;
    document.getElementById('selected-node-status').textContent =
      freshness === 'online' ? `${area} · ONLINE` :
      freshness === 'stale' ? `${area} · STALE DATA — readings may be outdated` :
      freshness === 'offline' ? `${area} · OFFLINE — last readings are not live` :
      `${area} · NOT REGISTERED`;
    document.getElementById('selected-node-id').textContent = selectedNodeId;
    document.getElementById('selected-node-ip').textContent = node ? `${node.ip || 'DHCP'}:${node.port || 5001}` : '--';
    document.getElementById('selected-node-firmware').textContent = node?.firmware_version || '--';
    const camera = overviewCameras.find(item => item.node_id === selectedNodeId);
    document.getElementById('selected-camera-info').textContent =
      camera ? `${camera.id} · ${camera.ip || 'IP unknown'}${camera.enabled ? '' : ' · disabled'}` : 'None registered';
    document.getElementById('selected-node-seen').textContent = node?.last_seen || '--';
    document.getElementById('selected-node-ambient').textContent =
      node?.temperature == null ? '--' : `${Number(node.temperature).toFixed(1)} °C`;
    document.getElementById('selected-node-humidity').textContent =
      node?.humidity == null ? '--' : `${Number(node.humidity).toFixed(1)} %`;
    document.getElementById('selected-node-hotspot').textContent =
      node?.thermal_max == null ? '--' : `${Number(node.thermal_max).toFixed(1)} °C`;
    document.getElementById('selected-node-blocked').textContent =
      freshness !== 'online' ? 'UNKNOWN' : node.blocked ? 'BLOCKED' : 'No blockage reported';
    renderSelectedMedia();
  }

  function renderSelectedMedia() {
    const thermalImage = document.getElementById('selected-thermal-feed');
    const cameraImage = document.getElementById('selected-camera-feed');
    const placeholder = document.getElementById('selected-media-placeholder');
    thermalImage.style.display = 'none';
    cameraImage.style.display = 'none';
    placeholder.style.display = 'flex';

    if (selectedMedia === 'thermal') {
      const node = overviewNodes[selectedNodeId];
      if (!node || !node.has_thermal) {
        placeholder.textContent = 'No MLX90640 thermal frame received from this node yet.';
        return;
      }
      if (nodeFreshness(node) !== 'online') {
        placeholder.textContent = `Thermal feed is not live. Last frame: ${node.thermal_last || node.last_seen || 'unknown'}.`;
        return;
      }
      const source = `/api/node/${encodeURIComponent(selectedNodeId)}/thermal/feed`;
      if (thermalImage.dataset.source !== source) {
        thermalImage.dataset.source = source;
        thermalImage.dataset.failed = '';
        thermalImage.dataset.loaded = '';
        thermalImage.onerror = () => {
          thermalImage.dataset.failed = '1';
          thermalImage.style.display = 'none';
          placeholder.style.display = 'flex';
          placeholder.textContent = 'Thermal stream is unavailable.';
        };
        thermalImage.onload = () => {
          thermalImage.dataset.loaded = '1';
          placeholder.style.display = 'none';
        };
        thermalImage.src = source;
      }
      if (thermalImage.dataset.failed) {
        placeholder.style.display = 'flex';
        return;
      }
      thermalImage.style.display = thermalImage.dataset.loaded ? 'block' : 'none';
      placeholder.style.display = thermalImage.dataset.loaded ? 'none' : 'flex';
      placeholder.textContent = 'Connecting to the MLX90640 thermal feed…';
      return;
    }

    const camera = overviewCameras.find(item => item.node_id === selectedNodeId && item.enabled);
    if (!camera) {
      placeholder.textContent = 'No enabled ESP32-CAM is registered for this node.';
      return;
    }
    if (cameraFreshness(camera) !== 'online') {
      placeholder.textContent = `ESP32-CAM is stale or offline. Last frame: ${camera.last_seen || 'unknown'}.`;
      return;
    }
    const source = `/api/camera/${encodeURIComponent(camera.id)}/feed`;
    if (cameraImage.dataset.source !== source) {
      cameraImage.dataset.source = source;
      cameraImage.dataset.failed = '';
      cameraImage.dataset.loaded = '';
      cameraImage.onerror = () => {
        cameraImage.dataset.failed = '1';
        cameraImage.style.display = 'none';
        placeholder.style.display = 'flex';
        placeholder.textContent = 'ESP32-CAM stream is unavailable.';
      };
      cameraImage.onload = () => {
        cameraImage.dataset.loaded = '1';
        placeholder.style.display = 'none';
      };
      cameraImage.src = source;
    }
    if (cameraImage.dataset.failed) {
      placeholder.style.display = 'flex';
      return;
    }
    cameraImage.style.display = cameraImage.dataset.loaded ? 'block' : 'none';
    placeholder.style.display = cameraImage.dataset.loaded ? 'none' : 'flex';
    placeholder.textContent = 'Connecting to the ESP32-CAM feed…';
  }

  function playSelectedSound() {
    const track = Number(document.getElementById('selected-audio-track').value);
    playSound(selectedNodeId, track);
  }

  // ── LAYOUT SWITCHING ───────────────────────
  function setOverviewLayout(layout, button) {
    const container = document.getElementById('overview-container');
    if (!container) return;
    container.className = `layout-${layout}`;
    document.querySelectorAll('.layout-toggle-btn').forEach(btn => {
      btn.classList.toggle('active', btn === button);
    });
  }

  function renderNodePreviews() {
    const container = document.getElementById('node-previews');
    if (!container) return;
    container.innerHTML = '';
    const otherNodes = MAP_NODE_IDS.filter(id => id !== selectedNodeId);
    otherNodes.forEach(nodeId => {
      const node = overviewNodes[nodeId];
      if (!node) return;
      const freshness = nodeFreshness(node);
      const thermalValue = node.thermal != null ? node.thermal.toFixed(1) : '--';
      const tempValue = node.temperature != null ? node.temperature.toFixed(1) : '--';
      const humidityValue = node.humidity != null ? node.humidity.toFixed(0) : '--';
      const isBlocked = freshness === 'online' && node.blocked ? 'Yes' : 'No';
      const hasCamera = overviewCameras.some(c => c.node_id === nodeId && c.enabled);
      const hasThermal = node.has_thermal;
      const card = document.createElement('div');
      card.className = `node-preview-card ${nodeId === selectedNodeId ? 'selected' : ''}`;
      card.innerHTML = `
        <div class="node-preview-name">${node.name}</div>
        <div class="node-preview-status">${freshness.toUpperCase()}</div>
        <div class="node-preview-sensors">
          <div class="node-preview-sensor">
            <span class="node-preview-sensor-label">Temp:</span>
            <span class="node-preview-sensor-value ${sensorSeverity(node.temperature, 'ambient')}">${tempValue}°C</span>
          </div>
          <div class="node-preview-sensor">
            <span class="node-preview-sensor-label">Humidity:</span>
            <span class="node-preview-sensor-value">${humidityValue}%</span>
          </div>
          <div class="node-preview-sensor">
            <span class="node-preview-sensor-label">Blocked:</span>
            <span class="node-preview-sensor-value">${isBlocked}</span>
          </div>
          ${hasThermal ? `
          <div class="node-preview-sensor">
            <span class="node-preview-sensor-label">Thermal:</span>
            <span class="node-preview-sensor-value ${sensorSeverity(node.thermal, 'thermal')}">${thermalValue}°C</span>
          </div>
          ` : ''}
        </div>
        ${hasCamera || hasThermal ? `
        <div class="node-preview-media-toggle">
          ${hasThermal ? `<button type="button" class="node-preview-media-btn active">Thermal</button>` : ''}
          ${hasCamera ? `<button type="button" class="node-preview-media-btn">Camera</button>` : ''}
        </div>
        ` : ''}
      `;
      card.addEventListener('click', () => selectNodeFromPreview(nodeId));
      container.appendChild(card);
    });
  }

  function selectNodeFromPreview(nodeId) {
    if (!MAP_NODE_IDS.includes(nodeId)) return;
    selectedNodeId = nodeId;
    document.querySelectorAll('.model-node-pick').forEach(btn => {
      btn.classList.toggle('selected', btn.dataset.modelNodeId === nodeId);
    });
    updateSelectedNodeDisplay();
    renderBuildingModel();
    renderNodePreviews();
  }

  // ── THERMAL CARDS ──────────────────────────
  const thermalCards = {};

  function updateThermalStatus(node) {
    const status = document.getElementById(`t-status-${node.id}`);
    const card = thermalCards[node.id];
    if (!status || !card) return;
    if (!node.has_thermal) {
      status.className = 'pill';
      status.textContent = 'No data';
      card.classList.remove('stale-thermal');
      return;
    }

    const freshness = nodeFreshness(node);
    status.className = freshness === 'online' ? 'pill pill-live' :
      freshness === 'stale' ? 'pill pill-stale' : 'pill';
    status.textContent = freshness === 'online' ? 'Live' :
      freshness === 'stale' ? 'Stale' : 'Offline';
    card.classList.toggle('stale-thermal', freshness !== 'online');
  }

  function updateThermalCard(node) {
    let card = thermalCards[node.id];
    if (!card) {
      card = document.createElement('div');
      card.className = 'thermal-card';
      card.id = `tcard-${node.id}`;
      card.innerHTML = `
        <div class="node-header">
          <div>
            <div class="card-title"></div>
            <div class="card-sub">MLX90640 thermal camera</div>
          </div>
          <span class="pill" id="t-status-${node.id}">No data</span>
        </div>
        <div class="thermal-view">
          <img class="thermal-feed" id="tfeed-${node.id}" alt="Thermal preview">
          <div class="media-placeholder" id="tph-${node.id}">Waiting for thermal data...</div>
        </div>
        <div class="stat-row">
          <div class="stat"><div class="stat-value" id="t-hot-${node.id}">--</div><div class="stat-label">Hotspot &deg;C</div></div>
          <div class="stat"><div class="stat-value" id="t-min-${node.id}">--</div><div class="stat-label">Min &deg;C</div></div>
          <div class="stat"><div class="stat-value" id="t-amb-${node.id}">--</div><div class="stat-label">Ambient &deg;C</div></div>
        </div>
        <div class="thermal-legend"><span>10&deg;C</span><div class="legend-bar"></div><span>60&deg;C+</span></div>
      `;
      card.querySelector('.card-title').textContent = node.name;
      document.getElementById('thermal-grid').appendChild(card);
      document.getElementById('no-thermal').style.display = 'none';
      thermalCards[node.id] = card;
    }

    const img    = document.getElementById(`tfeed-${node.id}`);
    const ph     = document.getElementById(`tph-${node.id}`);

    if (node.has_thermal) {
      if (!img.dataset.started) {
        img.dataset.started = '1';
        img.src = `/api/node/${node.id}/thermal/feed`;
        img.onload = () => { ph.style.display = 'none'; };
      }
      ph.style.display = 'none';
    }

    const hot = document.getElementById(`t-hot-${node.id}`);
    const min = document.getElementById(`t-min-${node.id}`);
    const amb = document.getElementById(`t-amb-${node.id}`);
    if (node.thermal_max != null) hot.textContent = parseFloat(node.thermal_max).toFixed(1);
    if (node.thermal_min != null) min.textContent = parseFloat(node.thermal_min).toFixed(1);
    if (node.temperature != null) amb.textContent = parseFloat(node.temperature).toFixed(1);
    updateThermalStatus(node);
  }

  // ── CAMERA CARDS ───────────────────────────
  const cameraCards = {};

  function updateCameraCard(cam) {
    cam.__receivedAt = Date.now();
    const cameraIndex = overviewCameras.findIndex(item => item.id === cam.id);
    if (cameraIndex === -1) overviewCameras.push(cam);
    else overviewCameras[cameraIndex] = cam;
    renderOverview();

    const grid = document.getElementById('cameras-grid');
    const none = document.getElementById('no-cameras');

    if (!cameraCards[cam.id]) {
      // Create card
      const card = document.createElement('div');
      card.className = 'camera-card';
      card.id = `cam-card-${cam.id}`;
      card.innerHTML = `
        <div class="camera-header">
          <div>
            <div class="camera-name"></div>
            <div class="camera-node"></div>
          </div>
          <button class="toggle-cam-btn" onclick="toggleCamera('${cam.id}')">
            ${cam.enabled ? 'Disable' : 'Enable'}
          </button>
        </div>
        <img class="camera-feed" id="feed-${cam.id}"
             src="/api/camera/${cam.id}/feed"
             onerror="this.style.display='none';document.getElementById('placeholder-${cam.id}').style.display='flex'">
        <div class="camera-feed-placeholder" id="placeholder-${cam.id}" style="display:none">
          Camera offline or disabled
        </div>
        <div class="camera-stats">
          <div class="camera-stat">
            <div class="camera-stat-value" id="cam-debris-${cam.id}">--</div>
            <div class="camera-stat-label">Debris</div>
          </div>
          <div class="camera-stat">
            <div class="camera-stat-value" id="cam-dist-${cam.id}">--</div>
            <div class="camera-stat-label">Closest cm</div>
          </div>
          <div class="camera-stat">
            <div class="camera-stat-value" id="cam-seen-${cam.id}">--</div>
            <div class="camera-stat-label">Last Seen</div>
          </div>
        </div>
        <div class="camera-footer">
          <span class="clear-badge" id="cam-status-${cam.id}">${svgIcon('i-check')}CLEAR</span>
          <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted)" id="cam-error-${cam.id}"></span>
        </div>
      `;
      card.querySelector('.camera-name').textContent = cam.name;
      card.querySelector('.camera-node').textContent = `Monitoring: ${cam.node_id} · ${cam.id}`;
      grid.appendChild(card);
      cameraCards[cam.id] = card;
      none.style.display = 'none';
    }

    // Update values
    const card    = cameraCards[cam.id];
    const debrisEl = document.getElementById(`cam-debris-${cam.id}`);
    const distEl   = document.getElementById(`cam-dist-${cam.id}`);
    const seenEl   = document.getElementById(`cam-seen-${cam.id}`);
    const errorEl  = document.getElementById(`cam-error-${cam.id}`);
    const toggleButton = card.querySelector('.toggle-cam-btn');

    if (toggleButton) toggleButton.textContent = cam.enabled ? 'Disable' : 'Enable';
    const cameraNode = card.querySelector('.camera-node');
    if (cameraNode) cameraNode.textContent = `Monitoring: ${cam.node_id} · ${cam.id} · ${cam.ip || 'IP unknown'}`;
    if (debrisEl) debrisEl.textContent = cam.debris_count ?? '--';
    if (distEl)   distEl.textContent   = cam.closest_debris ? `${cam.closest_debris}` : '--';
    if (seenEl)   seenEl.textContent   = cam.last_seen ?? '--';

    if (errorEl) errorEl.textContent = cam.error ?? '';

    updateCameraStatus(cam);

    const currentlyBlocked = cameraFreshness(cam) === 'online' && cam.blocked;
    if (currentlyBlocked && !cam.__loggedBlocked) {
      addLog(`Camera ${cam.name} detected blockage -> Node ${cam.node_id} set to STOP`, 'cam');
    }
    cam.__loggedBlocked = currentlyBlocked;
  }

  function toggleCamera(camId) {
    socket.emit('toggle_camera', { cam_id: camId });
  }

  function registerSettingsForm(formId, endpoint, fields, statusId) {
    const form = document.getElementById(formId);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const status = document.getElementById(statusId);
      const submit = form.querySelector('button[type="submit"]');
      const values = new FormData(form);
      const payload = Object.fromEntries(fields.map(field => [field, values.get(field)]));
      submit.disabled = true;
      status.textContent = '';

      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not save settings');
        window.location.reload();
      } catch (error) {
        status.textContent = error.message;
        submit.disabled = false;
      }
    });
  }

  registerSettingsForm('add-node-form', '/api/nodes', ['id', 'name'], 'node-add-status');
  registerSettingsForm('add-camera-form', '/api/cameras', ['id', 'name', 'node_id', 'url'], 'camera-add-status');

  // ── DEVICES ────────────────────────────────
  function renderDevices(devices) {
    const list  = document.getElementById('devices-list');
    const count = document.getElementById('device-count');
    const ts    = document.getElementById('last-scan-time');

    count.textContent = `${devices.length} device${devices.length !== 1 ? 's' : ''}`;
    ts.textContent    = `Last scan: ${new Date().toLocaleTimeString('en-US', {hour12:false})}`;

    if (!devices.length) {
      list.innerHTML = '<div class="empty-devices">No devices found on hotspot</div>';
      return;
    }

    list.innerHTML = devices.map(d => `
      <div class="device-row">
        <div class="device-ip">${escapeHtml(d.ip)}</div>
        <div class="device-mac">${escapeHtml(d.mac)}</div>
        <div class="device-name" style="color:${d.type==='esp32'?'var(--accent2)':d.type==='camera'?'#aa00ff':'var(--text)'}">
          ${escapeHtml(d.node_name)}
        </div>
        <div><span class="device-type ${escapeHtml(d.type)}">${d.type==='esp32'?'ESP32':d.type==='camera'?'CAM':'Device'}</span></div>
        <div class="device-alive">
          <div class="alive-dot ${d.alive?'online':'offline'}"></div>
          <span style="color:${d.alive?'var(--accent)':'var(--muted)'}">${d.alive?'Online':'Offline'}</span>
        </div>
      </div>
    `).join('');
  }

  function triggerScan() {
    addLog('Manual scan triggered...', 'conn');
    fetch('/api/devices/scan', { method: 'POST' })
      .then(r => r.json())
      .then(d => addLog(`Scan complete: ${d.count} device(s)`, 'conn'))
      .catch(() => addLog('Scan failed', 'err'));
  }

  // ── NODE CONTROLS ──────────────────────────
  function sendCommand(nodeId, command) {
    socket.emit('send_command', { node_id: nodeId, command });
    addLog(`Override: ${nodeId} -> ${command}`, 'cmd');
  }
  function playSound(nodeId, track) {
    socket.emit('play_sound', { node_id: nodeId, track });
    addLog(`Play sound ${track} on ${nodeId}`, 'cmd');
  }
  function clearOverride(nodeId) {
    socket.emit('clear_override', { node_id: nodeId });
    addLog(`Auto restored: ${nodeId}`, 'conn');
  }
  function broadcastAll(command) {
    socket.emit('send_command', { node_id: 'all', command });
    addLog(`Broadcast ALL -> ${command}`, 'cmd');
  }
  function clearAllOverrides() {
    socket.emit('clear_override', { node_id: 'all' });
    addLog('All nodes -> AUTO mode', 'conn');
  }

  // ── LOG ────────────────────────────────────
  function addLog(msg, type = '') {
    const log   = document.getElementById('log-output');
    const ts    = new Date().toLocaleTimeString('en-US', { hour12: false });
    const entry = document.createElement('div');
    const timestamp = document.createElement('span');
    entry.className = `log-entry ${type}`;
    timestamp.className = 'ts';
    timestamp.textContent = `[${ts}]`;
    entry.append(timestamp, document.createTextNode(msg));
    log.appendChild(entry);
    log.scrollTop = log.scrollHeight;
  }
  function clearLog() { document.getElementById('log-output').innerHTML = ''; }

  addLog('GaKuna dashboard loaded', 'conn');
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == '__main__':
    # TCP server for ESP32 nodes
    tcp_thread = threading.Thread(target=start_tcp_server)
    tcp_thread.daemon = True
    tcp_thread.start()

    # TCP server for ESP32-CAM JPEG frames
    camera_stream_thread = threading.Thread(target=start_camera_stream_server)
    camera_stream_thread.daemon = True
    camera_stream_thread.start()

    # Device scanner
    scan_thread = threading.Thread(target=scan_devices_loop)
    scan_thread.daemon = True
    scan_thread.start()

    # Start camera detection threads
    for cam in camera_snapshot():
        start_camera_worker(cam)
        if cam.enabled:
            print(f"[CAM] Started detection for {cam.name}")

    print("=" * 50)
    print("  GaKuna Server Starting")
    print("=" * 50)
    print(f"  Dashboard:  http://0.0.0.0:8080")
    print(f"  ESP32 TCP:  0.0.0.0:{ESP32_SERVER_PORT}")
    print(f"  ESP32-CAM stream: 0.0.0.0:{CAMERA_STREAM_PORT}")
    print(f"  Focal Len:  {FOCAL_LENGTH:.1f}px")
    print(f"  Cameras:    {len([c for c in camera_snapshot() if c.enabled])} active")
    print("=" * 50)

    socketio.run(app, host='0.0.0.0', port=8080, debug=False, allow_unsafe_werkzeug=True)