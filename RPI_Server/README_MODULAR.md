# GaKuna RPi Server - Modular Architecture

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python run.py

# Verify structure
python verify_structure.py
```

The application will start on `http://localhost:5000` with:
- Flask development server on port 5000
- TCP server on port 5001 (ESP32 device communication)
- WebSocket events via Flask-SocketIO

## Architecture Overview

This codebase has been refactored from a monolithic 3,460-line file into a professional modular Flask application. The structure follows Flask best practices with clear separation of concerns.

### Key Components

**1. Routes** (`app/routes/`)
- RESTful API endpoints organized by domain
- Flask Blueprints for modular routing
- `/api/nodes` - Node management
- `/api/cameras` - Camera management
- `/api/settings` - Configuration management
- `/` - Dashboard UI

**2. Services** (`app/services/`)
- Business logic independent of Flask
- Easy to test and reuse
- **NodeManager** - Node state and registry
- **CameraManager** - Camera state and control
- **DeviceScanner** - Network device discovery
- **ThermalProcessor** - Thermal image analysis

**3. Communications** (`app/comms/`)
- Device interaction and protocols
- **TCPServer** - Receives data from ESP32 nodes
- **CameraStreamManager** - Manages camera streams
- **DeviceCommandSender** - Sends commands to devices

**4. Database** (`app/database.py`)
- SQLite abstraction layer
- CRUD operations for nodes, cameras, settings
- Connection pooling with context managers

**5. Configuration** (`app/config.py`)
- Centralized configuration management
- Environment-based classes (Dev, Prod, Test)
- Device constants and mappings
- Backward compatibility mappings

## File Structure

```
RPI_Server/
├── app/
│   ├── __init__.py              # App factory
│   ├── config.py                # Configuration
│   ├── database.py              # SQLite layer
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── nodes_routes.py
│   │   ├── cameras_routes.py
│   │   ├── settings_routes.py
│   │   └── ui_routes.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── node_manager.py
│   │   ├── camera_manager.py
│   │   ├── device_scanner.py
│   │   └── thermal_processor.py
│   └── comms/
│       ├── __init__.py
│       ├── tcp_server.py
│       ├── camera_stream.py
│       └── device_commands.py
├── run.py                       # Entry point
├── requirements.txt             # Dependencies
├── verify_structure.py          # Verification script
│
├── GAKUNA_SERVER_V3.py         # Original monolithic (reference)
├── static/                      # Static assets
└── templates/                   # HTML templates (TODO)
```

## API Endpoints

### Nodes (`/api/nodes`)
- `GET /api/nodes` - List all nodes
- `POST /api/nodes` - Add new node
- `GET /api/nodes/<node_id>` - Get node details
- `POST /api/nodes/<node_id>/command` - Send command to node

### Cameras (`/api/cameras`)
- `GET /api/cameras` - List all cameras
- `POST /api/cameras` - Add new camera
- `GET /api/cameras/<cam_id>/feed` - Get camera stream
- `GET /api/cameras/<cam_id>/snapshot` - Get snapshot
- `POST /api/cameras/<cam_id>/enable` - Enable camera
- `POST /api/cameras/<cam_id>/disable` - Disable camera

### Settings (`/api/settings`)
- `GET /api/settings/thresholds` - Get alert thresholds
- `POST /api/settings/thresholds` - Update thresholds
- `GET /api/settings/evacuation-mode` - Get evacuation mode
- `POST /api/settings/evacuation-mode` - Set evacuation mode

### Dashboard
- `GET /` - Main dashboard UI

## Configuration

### Environment Variables

```bash
# Flask configuration
FLASK_ENV=development  # or production
SECRET_KEY=your-secret-key

# Database
DATABASE=gakuna_nodes.db

# Device settings
TCP_HOST=0.0.0.0
TCP_PORT=5001
```

### Configuration Classes

```python
from app.config import DevelopmentConfig, ProductionConfig

# Use during development
app = create_app(DevelopmentConfig)

# Use in production
app = create_app(ProductionConfig)
```

## Usage Examples

### Create App Instance
```python
from app import create_app
from app.config import DevelopmentConfig

app = create_app(DevelopmentConfig)
```

### Use Services
```python
from app.services import NodeManager, CameraManager
from app.database import Database

db = Database('gakuna_nodes.db')
node_mgr = NodeManager('gakuna_nodes.db')

# Get all nodes
nodes = node_mgr.get_all_nodes()

# Update node state
node_mgr.update_node_state('node_11', ambient_temp_c=25.5, is_online=True)
```

### Use TCP Server
```python
from app.comms.tcp_server import TCPServer

server = TCPServer(host='0.0.0.0', port=5001)
server.on_data_received = lambda ip, data: print(f"Data from {ip}")
server.start()
```

### Use Database
```python
from app.database import Database

db = Database('gakuna_nodes.db')

# Get all nodes
nodes = db.get_all_nodes()

# Add a node
db.add_node('node_11', 'Node 11', '192.168.1.100', 5001, floor=1, area='Left End')

# Get a node
node = db.get_node('node_11')
```

## Testing

### Verify Structure
```bash
python verify_structure.py
```

### Test Imports
```bash
python -c "from app import create_app; app = create_app(); print('OK')"
```

### Test Routes
```bash
# Start the app first
python run.py

# In another terminal
curl http://localhost:5000/api/nodes
curl http://localhost:5000/api/cameras
curl http://localhost:5000/
```

## Development

### Adding New Routes

1. Create handler in `app/routes/new_routes.py`
2. Register blueprint in `app/routes/__init__.py`
3. Add blueprint registration in `app/__init__.py` (already done for all)

### Adding New Services

1. Create class in `app/services/new_service.py`
2. Add import to `app/services/__init__.py`
3. Use in routes by importing and instantiating

### Adding Database Tables

1. Add CREATE TABLE in `Database._init_db()` in `app/database.py`
2. Add CRUD methods for new table
3. Update relevant services

## Documentation

Detailed documentation available:

- **[ARCHITECTURE.md](./ARCHITECTURE.md)** - System architecture and diagrams
- **[REFACTORING_SUMMARY.md](./REFACTORING_SUMMARY.md)** - Refactoring overview and benefits
- **[IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md)** - TODO list and extraction order
- **[REFACTORING_COMPLETION_REPORT.md](./REFACTORING_COMPLETION_REPORT.md)** - Completion summary

## Migration from Monolithic File

The original `GAKUNA_SERVER_V3.py` remains available for reference. See `IMPLEMENTATION_CHECKLIST.md` for detailed extraction steps to complete the refactoring.

## Troubleshooting

### Import Errors
```bash
# Verify all modules import correctly
python verify_structure.py
```

### Port Already in Use
```bash
# Change Flask port in run.py
# Change TCP port in app/config.py
```

### Database Errors
```bash
# Reset database
rm gakuna_nodes.db
python run.py  # Creates fresh database
```

## Next Steps

1. Extract HTML template to `app/templates/dashboard.html`
2. Implement remaining service logic
3. Add background threads for device scanning
4. Implement SocketIO real-time events
5. Add comprehensive tests
6. Deploy to production

See `IMPLEMENTATION_CHECKLIST.md` for detailed roadmap.

## Support

- Original monolithic file: `GAKUNA_SERVER_V3.py` (reference only)
- Configuration: `app/config.py`
- Routes: `app/routes/`
- Services: `app/services/`
- Communication: `app/comms/`
- Database: `app/database.py`

## Credits

Refactored by Copilot - Converting monolithic Flask app into professional modular architecture with separation of concerns, improved testability, and scalability.
