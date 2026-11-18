"""
GaKuna Server
-------------
Hosts a web dashboard AND manages TCP connections to ESP32 nodes.
Integrates OpenCV-based blockage detection from ESP32-CAM.
Run with: python3 server.py
Access dashboard at: http://<pi-ip>:8080
"""

import socket
import threading
import json
import struct
import time
import subprocess
import re
import urllib.request
import numpy as np
import cv2
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
NODE_CONFIG = [
    {"id": "node_1", "name": "Node 11 - Floor 1 Left",   "ip": "DHCP", "port": 5001},
    {"id": "node_2", "name": "Node 12 - Floor 1 Left",   "ip": "DHCP", "port": 5001},
]

# Listen on all interfaces; ESP32s connect to the Pi's router-assigned address.
PI_HOST = '0.0.0.0'
ESP32_SERVER_PORT = 5001
CAMERA_STREAM_PORT = 8081
VALID_COMMANDS    = ["LEFT", "RIGHT", "STOP", "OFF"]

# ─────────────────────────────────────────────
# CAMERA CONFIGURATION
# Each camera maps to a node it monitors
# ─────────────────────────────────────────────
CAMERA_CONFIG = [
    {
        "id":         "cam_1",
        "name":       "Camera - Node 11",
        "url":        None,  # Uses the ESP32-CAM TCP stream on CAMERA_STREAM_PORT
        "node_id":    "node_1",
        "enabled":    True,
    },
]

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
        self.error          = None
        self.latest_frame   = None   # JPEG bytes for live view
        self.tcp_frame      = None   # Raw JPEG received from ESP32-CAM
        self.tcp_seen       = 0.0
        self.lock           = threading.Lock()

    def to_dict(self):
        return {
            "id":             self.id,
            "name":           self.name,
            "url":            self.url,
            "ip":             self.ip,
            "node_id":        self.node_id,
            "enabled":        self.enabled,
            "blocked":        self.blocked,
            "debris_count":   self.debris_count,
            "closest_debris": self.closest_debris,
            "last_seen":      self.last_seen,
            "error":          self.error,
        }

cameras = {cfg["id"]: CameraState(cfg) for cfg in CAMERA_CONFIG}

def camera_matches_ip(cam, ip):
    with cam.lock:
        camera_ip = cam.ip
    return camera_ip == ip or (cam.url and cam.url.startswith(f"http://{ip}"))

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
        self.conn             = None
        self.lock             = threading.Lock()

    def to_dict(self):
        return {
            "id":               self.id,
            "name":             self.name,
            "ip":               self.ip,
            "temperature":      self.temperature,
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

nodes = {cfg["id"]: NodeState(cfg) for cfg in NODE_CONFIG}

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
    if data is None or time.time() - seen > 5:
        return None
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

def start_camera_stream_server():
    """Receive length-prefixed JPEG frames from ESP32-CAM on port 8081."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', CAMERA_STREAM_PORT))
    server.listen(5)
    print(f"[CAM] TCP frame stream listening on 0.0.0.0:{CAMERA_STREAM_PORT}")

    while True:
        conn, addr = server.accept()
        print(f"[CAM] Stream connected from {addr[0]}:{addr[1]}")
        target = cameras.get("cam_1")
        if target:
            with target.lock:
                target.ip = addr[0]
        try:
            while True:
                header = read_exact(conn, 4)
                if header is None:
                    break
                length = struct.unpack('<I', header)[0]
                if length == 0 or length > 200_000:
                    continue
                jpeg = read_exact(conn, length)
                if jpeg is None:
                    break
                if target:
                    with target.lock:
                        target.tcp_frame = jpeg
                        target.tcp_seen = time.time()
                        target.error = None
        except Exception as exc:
            print(f"[CAM] Stream error: {exc}")
        finally:
            conn.close()
            print("[CAM] Stream disconnected")

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
                cam.error = "Cannot reach camera"
            time.sleep(2)
            continue

        blocked, debris_count, closest, annotated = detect_blockage(frame)

        # Encode annotated frame as JPEG for live view
        _, jpeg = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])

        with cam.lock:
            cam.blocked        = blocked
            cam.debris_count   = debris_count
            cam.closest_debris = round(closest, 1) if closest else None
            cam.last_seen      = time.strftime("%H:%M:%S")
            cam.error          = None
            cam.latest_frame   = jpeg.tobytes()

        # Update the node this camera monitors
        node = nodes.get(cam.node_id)
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

        # Capture every 2 seconds — balances responsiveness vs power
        time.sleep(2)

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
                            for node in nodes.values():
                                if node.ip == ip:
                                    node_name   = node.name
                                    device_type = 'esp32'
                                    break
                            for cam in cameras.values():
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
                for node in nodes.values():
                    if node.ip == ip:
                        node_name   = node.name
                        device_type = 'esp32'
                        break
                for cam in cameras.values():
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
    for node in nodes.values():
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
                        reported_node = nodes.get(reported_id)
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
                        registered = True
                        print(f"[+] {node.name} connected from {client_ip}")
                        socketio.emit('node_update', node.to_dict())

                    # Render the thermal frame outside the lock (it is CPU-bound)
                    frame    = sensor_data.get("thermal_frame")
                    thermal_img = thermal_min = thermal_max = None
                    if isinstance(frame, list) and len(frame) == THERMAL_W * THERMAL_H:
                        thermal_img, thermal_min, thermal_max = render_thermal(frame)

                    with node.lock:
                        node.temperature = sensor_data.get("temp",     node.temperature)
                        node.seismic     = sensor_data.get("seismic",  node.seismic)
                        node.thermal     = sensor_data.get("thermal",  node.thermal)
                        if thermal_img:
                            node.thermal_img  = thermal_img
                            node.thermal_min  = round(thermal_min, 1)
                            node.thermal_max  = round(thermal_max, 1)
                            node.thermal_last = time.strftime("%H:%M:%S")
                        # Only update blocked from sensor if no camera is watching this node
                        cam_watching = any(c.node_id == node.id for c in cameras.values() if c.enabled)
                        if not cam_watching:
                            node.blocked = sensor_data.get("blocked", node.blocked)
                        node.last_seen = time.strftime("%H:%M:%S")

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
                            registered = True
                            print(f"[+] {node.name} connected from {client_ip}")
                            socketio.emit('node_update', node.to_dict())
                        with node.lock:
                            node.temperature = temp
                            node.last_seen   = time.strftime("%H:%M:%S")
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
    return render_template_string(HTML_TEMPLATE, nodes=list(nodes.values()))

@app.route('/api/nodes')
def api_nodes():
    return jsonify([n.to_dict() for n in nodes.values()])

@app.route('/api/cameras')
def api_cameras():
    return jsonify([c.to_dict() for c in cameras.values()])

@app.route('/api/camera/<cam_id>/feed')
def camera_feed(cam_id):
    """Live MJPEG feed for a specific camera — view in browser."""
    cam = cameras.get(cam_id)
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
    node = nodes.get(node_id)
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
    node = nodes.get(node_id)
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
    cam = cameras.get(cam_id)
    if not cam:
        return jsonify({"error": "Camera not found"}), 404
    cam.enabled = not cam.enabled
    socketio.emit('camera_update', cam.to_dict())
    return jsonify({"status": "ok", "enabled": cam.enabled})

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
        for node in nodes.values():
            node.override         = True
            node.override_command = command
            ok                    = node.send_command(command)
            results[node.id]      = ok
            socketio.emit('node_update', node.to_dict())
        return jsonify({"status": "broadcast", "results": results})

    elif node_id in nodes:
        node                  = nodes[node_id]
        node.override         = True
        node.override_command = command
        ok                    = node.send_command(command)
        socketio.emit('node_update', node.to_dict())
        return jsonify({"status": "ok", "sent": ok})

    return jsonify({"error": "Node not found"}), 404

@app.route('/api/override/clear', methods=['POST'])
def clear_override():
    data    = request.json
    node_id = data.get('node_id')

    if node_id == 'all':
        for node in nodes.values():
            node.override = False
            socketio.emit('node_update', node.to_dict())
        return jsonify({"status": "all overrides cleared"})

    elif node_id in nodes:
        nodes[node_id].override = False
        socketio.emit('node_update', nodes[node_id].to_dict())
        return jsonify({"status": "override cleared"})

    return jsonify({"error": "Node not found"}), 404

# ─────────────────────────────────────────────
# SOCKETIO EVENTS
# ─────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    for node in nodes.values():
        emit('node_update', node.to_dict())
    for cam in cameras.values():
        emit('camera_update', cam.to_dict())
    with device_cache_lock:
        emit('devices_update', device_cache)

@socketio.on('send_command')
def on_send_command(data):
    node_id = data.get('node_id')
    command = data.get('command', '').upper()
    if command not in VALID_COMMANDS:
        return
    target_nodes = list(nodes.values()) if node_id == 'all' else [nodes.get(node_id)]
    target_nodes = [n for n in target_nodes if n]
    for node in target_nodes:
        node.override         = True
        node.override_command = command
        node.send_command(command)
        socketio.emit('node_update', node.to_dict())

@socketio.on('clear_override')
def on_clear_override(data):
    node_id      = data.get('node_id')
    target_nodes = list(nodes.values()) if node_id == 'all' else [nodes.get(node_id)]
    target_nodes = [n for n in target_nodes if n]
    for node in target_nodes:
        node.override = False
        socketio.emit('node_update', node.to_dict())

@socketio.on('toggle_camera')
def on_toggle_camera(data):
    cam_id = data.get('cam_id')
    cam    = cameras.get(cam_id)
    if cam:
        cam.enabled = not cam.enabled
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
  .tabs { display: flex; gap: 4px; margin-bottom: 28px; border-bottom: 1px solid var(--border); }
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
  .sensor-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1px; background: var(--border); border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
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

  .auto-badge, .override-badge, .blocked-badge, .clear-badge, .device-count {
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

  @media (max-width: 600px) {
    .broadcast-bar { flex-direction: column; align-items: flex-start; }
    header { flex-direction: column; gap: 12px; align-items: flex-start; }
    .devices-table-header, .device-row { grid-template-columns: 120px 1fr 80px 80px; }
    .device-mac { display: none; }
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
    <button class="tab-btn active" onclick="switchTab('nodes',   this)"><svg class="icon"><use href="#i-cpu"/></svg>Nodes</button>
    <button class="tab-btn"        onclick="switchTab('cameras', this)"><svg class="icon"><use href="#i-camera"/></svg>Cameras</button>
    <button class="tab-btn"        onclick="switchTab('thermal', this)"><svg class="icon"><use href="#i-flame"/></svg>Thermal</button>
    <button class="tab-btn"        onclick="switchTab('devices', this)"><svg class="icon"><use href="#i-list"/></svg>Devices</button>
  </div>

  <!-- TAB: NODES -->
  <div class="tab-panel active" id="tab-nodes">
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
            <div class="node-ip">{{ node.ip }}:{{ node.port }}</div>
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
            <span class="auto-badge" id="mode-badge-{{ node.id }}"><svg class="icon"><use href="#i-rotate"/></svg>AUTO</span>
          </div>
        </div>
        <div class="sensor-grid">
          <div class="sensor-item">
            <div class="sensor-value normal" id="temp-{{ node.id }}">--</div>
            <div class="sensor-unit">°C Temp</div>
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
      No cameras configured in CAMERA_CONFIG
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

</div>

<script>
  const socket = io();
  const SIGN_ICON_ID = { LEFT: 'i-arrow-left', RIGHT: 'i-arrow-right', STOP: 'i-octagon', OFF: 'i-power' };
  const svgIcon = (id, cls = 'icon') => `<svg class="${cls}"><use href="#${id}"/></svg>`;

  // ── TABS ───────────────────────────────────
  function switchTab(name, btn) {
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    btn.classList.add('active');
  }

  // ── SOCKET ─────────────────────────────────
  socket.on('connect',    () => addLog('Connected to GaKuna server', 'conn'));
  socket.on('disconnect', () => addLog('Disconnected from server',   'err'));
  socket.on('node_update',    node    => updateNodeCard(node));
  socket.on('camera_update',  cam     => updateCameraCard(cam));
  socket.on('devices_update', devices => renderDevices(devices));

  // ── NODE CARDS ─────────────────────────────
  function updateNodeCard(node) {
    const card      = document.getElementById(`card-${node.id}`);
    const dot       = document.getElementById(`dot-${node.id}`);
    const connLabel = document.getElementById(`conn-label-${node.id}`);
    const signIcon  = document.getElementById(`sign-icon-${node.id}`);
    const signState = document.getElementById(`sign-state-${node.id}`);
    const modeBadge = document.getElementById(`mode-badge-${node.id}`);
    const tempEl    = document.getElementById(`temp-${node.id}`);
    const blockedEl = document.getElementById(`blocked-${node.id}`);
    const lastSeen  = document.getElementById(`lastseen-${node.id}`);
    if (!card) return;

    dot.classList.toggle('online', node.connected);
    connLabel.textContent = node.connected ? 'ONLINE' : 'OFFLINE';
    card.classList.toggle('connected', node.connected);

    const sign = node.current_sign || 'OFF';
    signIcon.className   = `sign-icon ${sign}`;
    signIcon.innerHTML   = svgIcon(SIGN_ICON_ID[sign] || 'i-power');
    signState.className  = `sign-state ${sign}`;
    signState.textContent = sign;

    card.classList.toggle('override-active', node.override);
    modeBadge.className   = node.override ? 'override-badge' : 'auto-badge';
    modeBadge.innerHTML   = node.override
      ? svgIcon('i-alert') + 'OVERRIDE'
      : svgIcon('i-rotate') + 'AUTO';

    if (node.temperature != null) {
      const t = parseFloat(node.temperature).toFixed(1);
      tempEl.textContent = t;
      tempEl.className = 'sensor-value ' + (t > 50 ? 'danger' : t > 35 ? 'warn' : 'normal');
    }
    if (node.blocked != null) {
      blockedEl.textContent = node.blocked ? 'YES' : 'NO';
      blockedEl.className   = 'sensor-value ' + (node.blocked ? 'danger' : 'normal');
    }
    if (node.last_seen) lastSeen.textContent = node.last_seen;

    updateThermalCard(node);
  }

  // ── THERMAL CARDS ──────────────────────────
  const thermalCards = {};

  function updateThermalCard(node) {
    let card = thermalCards[node.id];
    if (!card) {
      card = document.createElement('div');
      card.className = 'thermal-card';
      card.id = `tcard-${node.id}`;
      card.innerHTML = `
        <div class="node-header">
          <div>
            <div class="card-title">${node.name}</div>
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
      document.getElementById('thermal-grid').appendChild(card);
      document.getElementById('no-thermal').style.display = 'none';
      thermalCards[node.id] = card;
    }

    const status = document.getElementById(`t-status-${node.id}`);
    const img    = document.getElementById(`tfeed-${node.id}`);
    const ph     = document.getElementById(`tph-${node.id}`);

    if (node.has_thermal) {
      if (!img.dataset.started) {
        img.dataset.started = '1';
        img.src = `/api/node/${node.id}/thermal/feed`;
        img.onload = () => { ph.style.display = 'none'; };
      }
      ph.style.display = 'none';
      status.className = 'pill pill-live';
      status.textContent = 'Live';
    }

    const hot = document.getElementById(`t-hot-${node.id}`);
    const min = document.getElementById(`t-min-${node.id}`);
    const amb = document.getElementById(`t-amb-${node.id}`);
    if (node.thermal_max != null) hot.textContent = parseFloat(node.thermal_max).toFixed(1);
    if (node.thermal_min != null) min.textContent = parseFloat(node.thermal_min).toFixed(1);
    if (node.temperature != null) amb.textContent = parseFloat(node.temperature).toFixed(1);
  }

  // ── CAMERA CARDS ───────────────────────────
  const cameraCards = {};

  function updateCameraCard(cam) {
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
            <div class="camera-name">${cam.name}</div>
            <div class="camera-node">Monitoring: ${cam.node_id}</div>
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
      grid.appendChild(card);
      cameraCards[cam.id] = card;
      none.style.display = 'none';
    }

    // Update values
    const card    = cameraCards[cam.id];
    const debrisEl = document.getElementById(`cam-debris-${cam.id}`);
    const distEl   = document.getElementById(`cam-dist-${cam.id}`);
    const seenEl   = document.getElementById(`cam-seen-${cam.id}`);
    const statusEl = document.getElementById(`cam-status-${cam.id}`);
    const errorEl  = document.getElementById(`cam-error-${cam.id}`);

    if (debrisEl) debrisEl.textContent = cam.debris_count ?? '--';
    if (distEl)   distEl.textContent   = cam.closest_debris ? `${cam.closest_debris}` : '--';
    if (seenEl)   seenEl.textContent   = cam.last_seen ?? '--';

    if (statusEl) {
      statusEl.className   = cam.blocked ? 'blocked-badge' : 'clear-badge';
      statusEl.innerHTML   = cam.blocked ? svgIcon('i-alert') + 'BLOCKED' : svgIcon('i-check') + 'CLEAR';
    }

    if (errorEl) errorEl.textContent = cam.error ?? '';

    card.classList.toggle('blocked-active', cam.blocked);

    if (cam.blocked) {
      addLog(`Camera ${cam.name} detected blockage -> Node ${cam.node_id} set to STOP`, 'cam');
    }
  }

  function toggleCamera(camId) {
    socket.emit('toggle_camera', { cam_id: camId });
  }

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
        <div class="device-ip">${d.ip}</div>
        <div class="device-mac">${d.mac}</div>
        <div class="device-name" style="color:${d.type==='esp32'?'var(--accent2)':d.type==='camera'?'#aa00ff':'var(--text)'}">
          ${d.node_name}
        </div>
        <div><span class="device-type ${d.type}">${d.type==='esp32'?'ESP32':d.type==='camera'?'CAM':'Device'}</span></div>
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
    entry.className = `log-entry ${type}`;
    entry.innerHTML = `<span class="ts">[${ts}]</span>${msg}`;
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
    for cam in cameras.values():
        if cam.enabled:
            cam_thread = threading.Thread(target=camera_loop, args=(cam,))
            cam_thread.daemon = True
            cam_thread.start()
            print(f"[CAM] Started detection for {cam.name}")

    print("=" * 50)
    print("  GaKuna Server Starting")
    print("=" * 50)
    print(f"  Dashboard:  http://0.0.0.0:8080")
    print(f"  ESP32 TCP:  0.0.0.0:{ESP32_SERVER_PORT}")
    print(f"  ESP32-CAM stream: 0.0.0.0:{CAMERA_STREAM_PORT}")
    print(f"  Focal Len:  {FOCAL_LENGTH:.1f}px")
    print(f"  Cameras:    {len([c for c in cameras.values() if c.enabled])} active")
    print("=" * 50)

    socketio.run(app, host='0.0.0.0', port=8080, debug=False, allow_unsafe_werkzeug=True)