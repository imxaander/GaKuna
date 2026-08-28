"""
GaKuna Server
-------------
Hosts a web dashboard AND manages TCP connections to ESP32 nodes.
Integrates OpenCV-based blockage detection from ESP32-CAM.
Run with: python3 server.py
Access dashboard at: http://192.168.4.1:8080
"""

import socket
import threading
import json
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
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ─────────────────────────────────────────────
# NODE CONFIGURATION
# ─────────────────────────────────────────────
NODE_CONFIG = [
    {"id": "node_1", "name": "Node 11 - Floor 1 Left",   "ip": "10.42.0.1", "port": 5001},
    {"id": "node_2", "name": "Node 12 - Floor 1 Left",   "ip": "10.42.0.125", "port": 5001},
]

PI_HOST           = '0.0.0.0' #windows
# PI_HOST           = '10.42.0.1' #pi
ESP32_SERVER_PORT = 5001
VALID_COMMANDS    = ["LEFT", "RIGHT", "STOP", "OFF"]

# ─────────────────────────────────────────────
# CAMERA CONFIGURATION
# Each camera maps to a node it monitors
# ─────────────────────────────────────────────
CAMERA_CONFIG = [
    {
        "id":         "cam_1",
        "name":       "Camera - Node 11",
        "url":        "http://192.168.1.21/capture",  # ESP32-CAM IP
        "node_id":    "node_1",                         # Which node this camera monitors
        "enabled":    True,
    },
        {
        "id":         "cam_2",
        "name":       "Camera - Node 12",
        "url":        "http://192.168.1.21/capture",  # ESP32-CAM IP
        "node_id":    "node_2",                         # Which node this camera monitors
        "enabled":    True,
    },
    # Add more cameras here as needed
    # {
    #     "id":      "cam_2",
    #     "name":    "Camera - Node 21",
    #     "url":     "http://192.168.4.202/capture",
    #     "node_id": "node_3",
    #     "enabled": True,
    # },
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
        self.node_id  = config["node_id"]
        self.enabled  = config["enabled"]
        self.blocked  = False
        self.debris_count   = 0
        self.closest_debris = None   # cm
        self.last_seen      = None
        self.error          = None
        self.latest_frame   = None   # JPEG bytes for live view
        self.lock           = threading.Lock()

    def to_dict(self):
        return {
            "id":             self.id,
            "name":           self.name,
            "url":            self.url,
            "node_id":        self.node_id,
            "enabled":        self.enabled,
            "blocked":        self.blocked,
            "debris_count":   self.debris_count,
            "closest_debris": self.closest_debris,
            "last_seen":      self.last_seen,
            "error":          self.error,
        }

cameras = {cfg["id"]: CameraState(cfg) for cfg in CAMERA_CONFIG}

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

def camera_loop(cam):
    """Background thread — runs detection continuously for one camera."""
    print(f"[CAM] {cam.name} detection started")

    while True:
        if not cam.enabled:
            time.sleep(2)
            continue

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
                print(f"[CAM] {cam.name} → BLOCKED → Node {cam.node_id} → {cmd}")
            elif not blocked and node.blocked:
                node.blocked = False
                cmd = auto_determine_sign(node)
                node.send_command(cmd)
                socketio.emit('node_update', node.to_dict())
                print(f"[CAM] {cam.name} → CLEAR  → Node {cam.node_id} → {cmd}")

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
                                if cam.url.startswith(f"http://{ip}"):
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
                    if cam.url.startswith(f"http://{ip}"):
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

    if not node:
        print(f"[!] Unknown ESP32 from {client_ip}")
        conn.close()
        return

    print(f"[+] {node.name} connected from {client_ip}")
    with node.lock:
        node.conn      = conn
        node.connected = True

    socketio.emit('node_update', node.to_dict())

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
                    with node.lock:
                        node.temperature = sensor_data.get("temp",     node.temperature)
                        node.seismic     = sensor_data.get("seismic",  node.seismic)
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
                        with node.lock:
                            node.temperature = temp
                            node.last_seen   = time.strftime("%H:%M:%S")
                        socketio.emit('node_update', node.to_dict())
                        cmd = node.override_command if node.override else auto_determine_sign(node)
                        node.send_command(cmd)
                    except ValueError:
                        print(f"[{node.name}] Unparseable: {line}")

    except Exception as e:
        print(f"[{node.name}] Error: {e}")
    finally:
        print(f"[-] {node.name} disconnected")
        with node.lock:
            node.connected = False
            node.conn      = None
        socketio.emit('node_update', node.to_dict())

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
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@300;400;600;800&display=swap" rel="stylesheet">
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
  @media (max-width: 600px) {
    .broadcast-bar { flex-direction: column; align-items: flex-start; }
    header { flex-direction: column; gap: 12px; align-items: flex-start; }
    .devices-table-header, .device-row { grid-template-columns: 120px 1fr 80px 80px; }
    .device-mac { display: none; }
  }
</style>
</head>
<body>
<div class="container">

  <header>
    <div class="logo">
      <div class="logo-icon">🚨</div>
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
    <button class="tab-btn active" onclick="switchTab('nodes',   this)">⬡ Nodes</button>
    <button class="tab-btn"        onclick="switchTab('cameras', this)">⬡ Cameras</button>
    <button class="tab-btn"        onclick="switchTab('devices', this)">⬡ Devices</button>
  </div>

  <!-- TAB: NODES -->
  <div class="tab-panel active" id="tab-nodes">
    <div class="broadcast-bar">
      <span class="broadcast-label">⬡ Broadcast All</span>
      <div class="btn-group">
        <button class="btn btn-left"  onclick="broadcastAll('LEFT')">← Left</button>
        <button class="btn btn-right" onclick="broadcastAll('RIGHT')">Right →</button>
        <button class="btn btn-stop"  onclick="broadcastAll('STOP')">✕ Stop</button>
        <button class="btn btn-off"   onclick="broadcastAll('OFF')">○ Off</button>
        <button class="btn btn-clear" onclick="clearAllOverrides()">↺ Auto All</button>
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
          <div class="sign-icon OFF" id="sign-icon-{{ node.id }}">○</div>
          <div class="sign-info">
            <div class="sign-label">Current Sign</div>
            <div class="sign-state OFF" id="sign-state-{{ node.id }}">OFF</div>
            <span class="auto-badge" id="mode-badge-{{ node.id }}">● AUTO</span>
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
          <button class="btn btn-left btn-sm"  onclick="sendCommand('{{ node.id }}', 'LEFT')">← Left</button>
          <button class="btn btn-right btn-sm" onclick="sendCommand('{{ node.id }}', 'RIGHT')">Right →</button>
          <button class="btn btn-stop btn-sm"  onclick="sendCommand('{{ node.id }}', 'STOP')">✕ Stop</button>
          <button class="btn btn-off btn-sm"   onclick="sendCommand('{{ node.id }}', 'OFF')">○ Off</button>
          <button class="btn btn-clear btn-sm" onclick="clearOverride('{{ node.id }}')">↺ Auto</button>
          <span class="last-seen" id="lastseen-{{ node.id }}"></span>
        </div>
      </div>
      {% endfor %}
    </div>

    <div class="log-section">
      <div class="log-header">
        <span>⬡ Event Log</span>
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

  <!-- TAB: DEVICES -->
  <div class="tab-panel" id="tab-devices">
    <div class="devices-header">
      <div>
        <div class="devices-title">⬡ Devices on Hotspot</div>
        <div class="scan-info" id="last-scan-time">Scanning every 10 seconds</div>
      </div>
      <div style="display:flex;align-items:center;gap:12px;">
        <span class="device-count" id="device-count">0 devices</span>
        <button class="btn btn-scan" onclick="triggerScan()">↺ Scan Now</button>
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
  const SIGN_ICONS = { LEFT: '←', RIGHT: '→', STOP: '✕', OFF: '○' };

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
    signIcon.textContent = SIGN_ICONS[sign] || '○';
    signState.className  = `sign-state ${sign}`;
    signState.textContent = sign;

    card.classList.toggle('override-active', node.override);
    modeBadge.className   = node.override ? 'override-badge' : 'auto-badge';
    modeBadge.textContent = node.override ? '⚠ OVERRIDE' : '● AUTO';

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
          <span class="clear-badge" id="cam-status-${cam.id}">● CLEAR</span>
          <span style="font-family:'Share Tech Mono',monospace;font-size:10px;color:var(--muted)" id="cam-error-${cam.id}"></span>
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
      statusEl.textContent = cam.blocked ? '⚠ BLOCKED' : '● CLEAR';
    }

    if (errorEl) errorEl.textContent = cam.error ?? '';

    card.classList.toggle('blocked-active', cam.blocked);

    if (cam.blocked) {
      addLog(`Camera ${cam.name} detected blockage → Node ${cam.node_id} set to STOP`, 'cam');
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
    addLog(`Override: ${nodeId} → ${command}`, 'cmd');
  }
  function clearOverride(nodeId) {
    socket.emit('clear_override', { node_id: nodeId });
    addLog(`Auto restored: ${nodeId}`, 'conn');
  }
  function broadcastAll(command) {
    socket.emit('send_command', { node_id: 'all', command });
    addLog(`Broadcast ALL → ${command}`, 'cmd');
  }
  function clearAllOverrides() {
    socket.emit('clear_override', { node_id: 'all' });
    addLog('All nodes → AUTO mode', 'conn');
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
    print(f"  Focal Len:  {FOCAL_LENGTH:.1f}px")
    print(f"  Cameras:    {len([c for c in cameras.values() if c.enabled])} active")
    print("=" * 50)

    socketio.run(app, host='0.0.0.0', port=8080, debug=False, allow_unsafe_werkzeug=True)