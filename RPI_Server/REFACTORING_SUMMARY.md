# GaKuna RPi Server Refactoring - Modular Architecture Summary

## Overview
Successfully refactored the monolithic 3,460-line `GAKUNA_SERVER_V3.py` into a well-organized, modular Flask application with separated concerns.

## Directory Structure

```
RPI_Server/
├── app/                          # Main application package
│   ├── __init__.py              # Flask app factory (create_app)
│   ├── config.py                # Centralized configuration
│   ├── database.py              # SQLite database wrapper
│   │
│   ├── routes/                  # API route blueprints
│   │   ├── __init__.py          # Blueprint registration
│   │   ├── nodes_routes.py      # Node management API
│   │   ├── cameras_routes.py    # Camera management API
│   │   ├── settings_routes.py   # Settings/config API
│   │   └── ui_routes.py         # Dashboard UI routes
│   │
│   ├── services/                # Business logic services
│   │   ├── __init__.py          # Service exports
│   │   ├── node_manager.py      # Node state management
│   │   ├── camera_manager.py    # Camera state management
│   │   ├── device_scanner.py    # Network device discovery
│   │   └── thermal_processor.py # Thermal image analysis
│   │
│   ├── comms/                   # Device communication
│   │   ├── __init__.py          # Communications modules
│   │   ├── tcp_server.py        # TCP server for ESP32 nodes
│   │   ├── camera_stream.py     # Camera streaming workers
│   │   └── device_commands.py   # Device command protocol
│   │
│   └── templates/               # (TODO) HTML templates
│       └── dashboard.html       # Main dashboard (to be extracted)
│
├── run.py                       # Application entry point
├── requirements.txt             # Python dependencies
├── GAKUNA_SERVER_V3.py         # Original monolithic file (reference)
├── static/                      # Static assets (CSS, JS)
└── tests/                       # Test suite
```

## Module Breakdown

### 1. app/config.py
- **Purpose**: Centralized configuration management
- **Contains**:
  - Base `Config` class with defaults
  - `DevelopmentConfig`, `ProductionConfig`, `TestingConfig` classes
  - Device configuration constants (DEFAULT_NODE_CONFIG, CAMERA_NODE_ASSIGNMENTS)
  - Building layout (FLOOR_MAP_NODES)
  - Sound track mappings
  - Legacy device ID mapping for backward compatibility

### 2. app/database.py
- **Purpose**: Database abstraction layer
- **Class**: `Database`
- **Features**:
  - SQLite connection pooling with context managers
  - Automatic table initialization
  - CRUD operations for nodes, cameras, and settings
  - Type hints and row factory configuration

### 3. app/routes/
- **Purpose**: HTTP API endpoints organized by domain
- **Files**:
  - `nodes_routes.py`: Node list, add, get, command endpoints
  - `cameras_routes.py`: Camera list, feeds, control endpoints
  - `settings_routes.py`: Configuration and threshold management
  - `ui_routes.py`: Dashboard HTML rendering
- **Pattern**: Flask blueprints with URL prefixes (`/api/nodes`, `/api/cameras`, etc.)

### 4. app/services/
- **Purpose**: Business logic and state management (no Flask dependencies)
- **Classes**:
  - `NodeState`: Single node data model with sensor readings
  - `NodeManager`: Registry and state snapshots for all nodes
  - `CameraState`: Single camera data model
  - `CameraManager`: Registry and streaming control
  - `DeviceScanner`: Network scanning and device discovery
  - `ThermalProcessor`: Thermal image analysis and blockage detection

### 5. app/comms/
- **Purpose**: Device communication protocols and streaming
- **Classes**:
  - `TCPServer`: Receives data from ESP32 nodes over TCP
  - `DeviceCommand`: Command serialization and device protocol
  - `DeviceCommandSender`: Sends commands to nodes
  - `CameraStreamWorker`: Manages individual camera streams
  - `CameraStreamManager`: Coordinates all active streams

## Benefits of This Architecture

1. **Separation of Concerns**
   - Config isolated from routes and services
   - Business logic (services) independent of Flask
   - Communication code separated from HTTP handlers
   - Database layer abstracted from application code

2. **Testability**
   - Each module can be tested in isolation
   - Services don't depend on Flask or routes
   - Easy to mock database and comms layers
   - Mock config for different test scenarios

3. **Maintainability**
   - Clear file organization by responsibility
   - Reduced file size (original was 3,460 lines)
   - Easy to locate specific functionality
   - Single responsibility principle per module

4. **Scalability**
   - Easy to add new API endpoints (just create new route file)
   - Easy to extend services with new logic
   - Communication layer ready for additional protocols
   - Database layer ready for migrations/optimization

5. **Reusability**
   - Services can be used without Flask (e.g., CLI, background jobs)
   - Communication layer can be integrated with other frameworks
   - Config can be shared across multiple apps

## Next Steps to Complete Refactoring

### 1. Extract HTML Template
- [ ] Extract the large HTML template from GAKUNA_SERVER_V3.py
- [ ] Create `app/templates/dashboard.html`
- [ ] Update `ui_routes.py` to use `render_template()`

### 2. Implement Route Handlers
- [ ] Fill in TODO placeholders in route files with actual logic
- [ ] Wire up service layer to routes
- [ ] Add proper error handling and validation

### 3. Implement Service Logic
- [ ] Populate `_load_nodes()` in NodeManager
- [ ] Implement DeviceScanner network scanning
- [ ] Implement ThermalProcessor blockage detection
- [ ] Add evacuation path generation logic

### 4. Database Integration
- [ ] Migrate data from GAKUNA_SERVER_V3.py to new database schema
- [ ] Implement node/camera persistence
- [ ] Add database migrations if schema changes

### 5. Communication Protocol
- [ ] Implement TCP packet parsing in TCPServer
- [ ] Implement DeviceCommand serialization
- [ ] Wire up data handlers to update node/camera state

### 6. Testing & Validation
- [ ] Create unit tests for each service
- [ ] Create integration tests for routes
- [ ] Test database operations
- [ ] Load test TCP server

### 7. Static Assets & Templates
- [ ] Extract or reorganize static files
- [ ] Ensure dashboard HTML is properly templated
- [ ] Set up CSS/JS loading

### 8. SocketIO Integration
- [ ] Wire up SocketIO event handlers
- [ ] Real-time node state updates to clients
- [ ] Real-time camera feed updates
- [ ] Real-time evacuation state updates

## Migration Path from Monolithic File

To transition from `GAKUNA_SERVER_V3.py` to the new modular structure:

1. **Phase 1**: Create basic modular structure (✅ DONE)
   - Set up directories and scaffolds
   - Create basic classes and TODOs

2. **Phase 2**: Extract configuration (✅ DONE)
   - Move all constants to config.py
   - Update imports in services

3. **Phase 3**: Extract models and database (🔄 IN PROGRESS)
   - Create NodeState, CameraState classes
   - Implement database layer
   - Migrate existing node data

4. **Phase 4**: Extract services
   - Implement each service class
   - Write unit tests for each service
   - Verify backward compatibility

5. **Phase 5**: Extract routes
   - Implement route handlers
   - Wire up to services
   - Create integration tests

6. **Phase 6**: Extract communications
   - Implement TCP server and protocol
   - Implement camera streaming
   - Test with real devices

7. **Phase 7**: Extract UI and templates
   - Move HTML to templates/
   - Update render routes
   - Test dashboard rendering

8. **Phase 8**: Complete testing & deployment
   - Full integration tests
   - Performance benchmarking
   - Documentation updates
   - Decommission old file or keep as reference

## Key Files Created

| File | Lines | Purpose |
|------|-------|---------|
| app/__init__.py | 25 | Flask app factory |
| app/config.py | 60 | Configuration management |
| app/database.py | 180 | Database layer |
| app/routes/__init__.py | 15 | Blueprint registration |
| app/routes/nodes_routes.py | 30 | Node API endpoints |
| app/routes/cameras_routes.py | 45 | Camera API endpoints |
| app/routes/settings_routes.py | 25 | Settings API endpoints |
| app/routes/ui_routes.py | 15 | Dashboard rendering |
| app/services/__init__.py | 10 | Service exports |
| app/services/node_manager.py | 85 | Node management |
| app/services/camera_manager.py | 100 | Camera management |
| app/services/device_scanner.py | 50 | Device discovery |
| app/services/thermal_processor.py | 60 | Thermal analysis |
| app/comms/__init__.py | 5 | Comms exports |
| app/comms/tcp_server.py | 80 | TCP communication |
| app/comms/camera_stream.py | 70 | Camera streaming |
| app/comms/device_commands.py | 85 | Device commands |
| run.py | 30 | Application entry point |
| **Total** | **~865** | **New modular code** |

## Testing the Refactored Structure

To test that the new structure works:

```bash
# Start the application
python run.py

# Or in a Python shell:
from app import create_app
from app.config import DevelopmentConfig

app = create_app(DevelopmentConfig)
print(app.config['TCP_PORT'])  # Should print 5001
```

## Notes

- All `TODO` comments mark places where original GAKUNA_SERVER_V3.py logic needs to be extracted
- Database schema is set up but data migration is not yet implemented
- TCP server accepts connections but packet parsing is a placeholder
- Routes have basic structure but handler logic is not yet wired up
- Original GAKUNA_SERVER_V3.py remains unchanged for reference during extraction

This modular structure provides a solid foundation for a production-grade Flask application that can scale with the project's growth.
