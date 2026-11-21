"""Camera state and management."""


class CameraState:
    """Represents a single camera's state."""
    
    def __init__(self, cam_id, name="", node_id="", ip="", port=80):
        self.id = cam_id
        self.name = name
        self.node_id = node_id
        self.ip = ip
        self.port = port
        
        # Status
        self.is_online = False
        self.is_enabled = True
        self.last_seen_ms = 0
        self.firmware_version = None
        
        # Detection
        self.blockage_detected = False
        self.blockage_confidence = 0.0
    
    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "node_id": self.node_id,
            "ip": self.ip,
            "port": self.port,
            "is_online": self.is_online,
            "is_enabled": self.is_enabled,
            "last_seen_ms": self.last_seen_ms,
            "firmware_version": self.firmware_version,
            "blockage_detected": self.blockage_detected,
            "blockage_confidence": self.blockage_confidence,
        }


class CameraManager:
    """Manages camera registry and state."""
    
    def __init__(self, database_path):
        self.database_path = database_path
        self.cameras = {}
        self._load_cameras()
    
    def _load_cameras(self):
        """Load cameras from database."""
        # TODO: Implement database loading
        pass
    
    def get_camera(self, cam_id):
        """Get camera by ID."""
        return self.cameras.get(cam_id)
    
    def get_all_cameras(self):
        """Get all cameras."""
        return list(self.cameras.values())
    
    def get_cameras_by_node(self, node_id):
        """Get cameras for a specific node."""
        return [cam for cam in self.cameras.values() if cam.node_id == node_id]
    
    def update_camera_state(self, cam_id, **kwargs):
        """Update camera state properties."""
        camera = self.get_camera(cam_id)
        if camera:
            for key, value in kwargs.items():
                if hasattr(camera, key):
                    setattr(camera, key, value)
    
    def add_camera(self, cam_id, **kwargs):
        """Add a new camera."""
        camera = CameraState(cam_id, **kwargs)
        self.cameras[cam_id] = camera
        return camera
    
    def enable_camera(self, cam_id):
        """Enable a camera."""
        camera = self.get_camera(cam_id)
        if camera:
            camera.is_enabled = True
    
    def disable_camera(self, cam_id):
        """Disable a camera."""
        camera = self.get_camera(cam_id)
        if camera:
            camera.is_enabled = False
