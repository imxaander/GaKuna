"""Application configuration."""
import os


class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'gakuna2025-dev')
    
    # Database
    DATABASE = 'gakuna_nodes.db'
    
    # Flask-SocketIO
    SOCKETIO_ASYNC_MODE = 'threading'
    SOCKETIO_MANAGE_SESSION = False
    
    # Camera settings
    CAMERA_TIMEOUT = 3
    CAMERA_FOCAL_LENGTH = 360.0
    
    # Device scan settings
    DEVICE_SCAN_INTERVAL = 30  # seconds
    PING_TIMEOUT = 1
    
    # TCP settings
    TCP_HOST = '0.0.0.0'
    TCP_PORT = 5001
    TCP_BUFFER_SIZE = 4096
    TCP_KEEPALIVE_INTERVAL = 60


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    TESTING = False


class TestingConfig(Config):
    """Testing configuration."""
    DEBUG = True
    TESTING = True
    DATABASE = ':memory:'


# Node and camera configuration
DEFAULT_NODE_CONFIG = [
    {"id": "node_11", "name": "Node 11 - Floor 1 left end", "ip": "DHCP", "port": 5001},
    {"id": "node_12", "name": "Node 12 - Floor 1 right end", "ip": "DHCP", "port": 5001},
    {"id": "node_21", "name": "Node 21 - Floor 2 left end", "ip": "DHCP", "port": 5001},
    {"id": "node_22", "name": "Node 22 - Floor 2 middle", "ip": "DHCP", "port": 5001},
    {"id": "node_23", "name": "Node 23 - Floor 2 right end", "ip": "DHCP", "port": 5001},
]

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

# Building floor map configuration
FLOOR_MAP_NODES = [
    {"id": "node_11", "label": "11", "floor": 1, "left": 10, "top": 10, "area": "Floor 1 · Left end", "edge": "left"},
    {"id": "node_12", "label": "12", "floor": 1, "left": 90, "top": 10, "area": "Floor 1 · Right end", "edge": "right"},
    {"id": "node_21", "label": "21", "floor": 2, "left": 10, "top": 50, "area": "Floor 2 · Left end", "edge": "left"},
    {"id": "node_22", "label": "22", "floor": 2, "left": 50, "top": 50, "area": "Floor 2 · Middle", "edge": None},
    {"id": "node_23", "label": "23", "floor": 2, "left": 90, "top": 50, "area": "Floor 2 · Right end", "edge": "right"},
]

SOUND_TRACKS = {
    "left": "Go left",
    "right": "Go right",
    "do_not_proceed": "Do not proceed here",
    "down": "Go down",
    "up": "Go up",
    "other_side": "Go to the other side",
}

# Alert threshold defaults
ALERT_THRESHOLDS = {
    "ambient_warning_c": 30.0,
    "ambient_critical_c": 40.0,
    "thermal_warning_c": 35.0,
    "thermal_critical_c": 50.0,
}

