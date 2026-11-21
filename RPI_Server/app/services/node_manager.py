"""Node state and management."""


class NodeState:
    """Represents a single node's state."""
    
    def __init__(self, node_id, name="", ip="", port=5001, floor=1, area=""):
        self.id = node_id
        self.name = name
        self.ip = ip
        self.port = port
        self.floor = floor
        self.area = area
        
        # Sensor data
        self.ambient_temp_c = None
        self.humidity_pct = None
        self.thermal_hotspot_c = None
        self.blockage_reported = None
        
        # Status
        self.is_online = False
        self.last_seen_ms = 0
        self.firmware_version = None
    
    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "ip": self.ip,
            "port": self.port,
            "floor": self.floor,
            "area": self.area,
            "ambient_temp_c": self.ambient_temp_c,
            "humidity_pct": self.humidity_pct,
            "thermal_hotspot_c": self.thermal_hotspot_c,
            "blockage_reported": self.blockage_reported,
            "is_online": self.is_online,
            "last_seen_ms": self.last_seen_ms,
            "firmware_version": self.firmware_version,
        }


class NodeManager:
    """Manages node registry and state."""
    
    def __init__(self, database_path):
        self.database_path = database_path
        self.nodes = {}
        self._load_nodes()
    
    def _load_nodes(self):
        """Load nodes from database."""
        # TODO: Implement database loading
        pass
    
    def get_node(self, node_id):
        """Get node by ID."""
        return self.nodes.get(node_id)
    
    def get_all_nodes(self):
        """Get all nodes."""
        return list(self.nodes.values())
    
    def update_node_state(self, node_id, **kwargs):
        """Update node state properties."""
        node = self.get_node(node_id)
        if node:
            for key, value in kwargs.items():
                if hasattr(node, key):
                    setattr(node, key, value)
    
    def add_node(self, node_id, **kwargs):
        """Add a new node."""
        node = NodeState(node_id, **kwargs)
        self.nodes[node_id] = node
        return node
