# Implementation Checklist for Modular Refactoring

## Completed ✅
- [x] Create modular directory structure
- [x] Implement Flask app factory with blueprint registration
- [x] Create centralized configuration module
- [x] Create database abstraction layer with SQLite
- [x] Create route blueprints (nodes, cameras, settings, ui)
- [x] Create service classes (NodeManager, CameraManager, etc.)
- [x] Create communication modules (TCPServer, CameraStream, DeviceCommands)
- [x] Create application entry point (run.py)
- [x] Verify all imports work correctly
- [x] Verify blueprints are properly registered

## TODO - High Priority 🔴

### 1. Extract Dashboard HTML Template
**File**: `GAKUNA_SERVER_V3.py` lines ~1947-2088
**Output**: `app/templates/dashboard.html`
- [ ] Copy HTML_TEMPLATE variable
- [ ] Update ui_routes.py dashboard() to use render_template()
- [ ] Test dashboard renders correctly
- [ ] Verify 3D model, node list, and controls work

### 2. Extract Node Management Logic
**File**: `GAKUNA_SERVER_V3.py` lines ~301-520
**Target**: `app/services/node_manager.py`
- [ ] Implement `_load_nodes()` to read from database
- [ ] Implement node state update logic
- [ ] Add node snapshot collection for dashboard
- [ ] Wire up to routes in `app/routes/nodes_routes.py`

### 3. Extract Camera Management Logic
**File**: `GAKUNA_SERVER_V3.py` camera-related code
**Target**: `app/services/camera_manager.py`
- [ ] Implement `_load_cameras()` from database
- [ ] Implement camera stream management
- [ ] Add camera snapshot collection
- [ ] Wire up to routes in `app/routes/cameras_routes.py`

### 4. Extract TCP Protocol Handler
**File**: `GAKUNA_SERVER_V3.py` TCP handling section
**Target**: `app/comms/tcp_server.py`
- [ ] Implement packet parsing (binary protocol)
- [ ] Extract sensor data from packets
- [ ] Update node state on data receipt
- [ ] Implement data validation

### 5. Implement Route Handlers
**Files**: `app/routes/*.py`
- [ ] nodes_routes.py: Implement list, add, get, command handlers
- [ ] cameras_routes.py: Implement list, feed, snapshot, enable/disable
- [ ] settings_routes.py: Implement threshold and mode management
- [ ] Wire up service layer calls

## TODO - Medium Priority 🟡

### 6. Extract Device Scanner
**File**: `GAKUNA_SERVER_V3.py` network scanning code
**Target**: `app/services/device_scanner.py`
- [ ] Implement network scanning logic
- [ ] Implement device discovery
- [ ] Add background scanner thread

### 7. Extract Thermal Processing
**File**: `GAKUNA_SERVER_V3.py` thermal processing code
**Target**: `app/services/thermal_processor.py`
- [ ] Implement blockage detection algorithm
- [ ] Implement hotspot analysis
- [ ] Implement thermal rendering with colormap

### 8. Data Migration
**Source**: Existing `gakuna_nodes.db` or GAKUNA_SERVER_V3.py state
**Target**: New database schema
- [ ] Migrate nodes to new database
- [ ] Migrate cameras to new database
- [ ] Migrate settings to new database
- [ ] Verify data integrity

### 9. SocketIO Integration
**File**: `GAKUNA_SERVER_V3.py` socket handlers
**Target**: Background tasks and SocketIO event handlers
- [ ] Implement background node scanner thread
- [ ] Implement camera stream workers
- [ ] Add SocketIO event handlers for real-time updates
- [ ] Add device status push notifications

## TODO - Low Priority 🟢

### 10. Logging & Error Handling
- [ ] Add structured logging throughout modules
- [ ] Add error handling in routes
- [ ] Add retry logic in device communication
- [ ] Add validation in models

### 11. Tests
- [ ] Unit tests for services
- [ ] Integration tests for routes
- [ ] Tests for database operations
- [ ] Mock tests for device communication

### 12. Documentation
- [ ] API endpoint documentation
- [ ] Service layer documentation
- [ ] Database schema documentation
- [ ] Protocol documentation for device communication

## Files to Extract/Update from GAKUNA_SERVER_V3.py

| Component | Lines | Status | Target |
|-----------|-------|--------|--------|
| NodeState class | 301-350 | ⏳ TODO | `app/services/node_manager.py` |
| CameraState class | 351-400 | ⏳ TODO | `app/services/camera_manager.py` |
| Node management logic | 401-500 | ⏳ TODO | `app/services/node_manager.py` |
| TCP packet parsing | 501-650 | ⏳ TODO | `app/comms/tcp_server.py` |
| Camera worker threads | 651-800 | ⏳ TODO | `app/comms/camera_stream.py` |
| Thermal processing | 801-950 | ⏳ TODO | `app/services/thermal_processor.py` |
| Route handlers | 1049-1300 | ⏳ TODO | `app/routes/*.py` |
| HTML template | 1947-2088 | ⏳ TODO | `app/templates/dashboard.html` |
| SocketIO handlers | 2089-2200 | ⏳ TODO | Background tasks + socket handlers |
| Device scanner thread | 2201-2350 | ⏳ TODO | `app/services/device_scanner.py` |

## Suggested Extraction Order

1. **Extract HTML Template** (highest impact on UI)
2. **Extract Node Management** (foundation for other features)
3. **Extract TCP Protocol** (enable device communication)
4. **Extract Route Handlers** (make API functional)
5. **Extract Camera Management** (enable camera feeds)
6. **Extract Thermal Processing** (complete evacuation features)
7. **Extract Background Threads** (SocketIO, scanning, monitoring)
8. **Testing & Documentation** (ensure quality and usability)

## Notes

- Original GAKUNA_SERVER_V3.py remains unchanged as reference
- All new code follows PEP 8 style guidelines
- Type hints used throughout for IDE support
- Modular structure allows testing without Flask context
- Configuration is centralized and environment-aware
- Database layer abstracts SQLite implementation details

## Commands to Test

```bash
# Run the application
python run.py

# Test imports
python -c "from app import create_app; app = create_app(); print('OK')"

# Check routes
python -c "from app import create_app; app = create_app(); print([r for r in app.url_map.iter_rules()])"

# Check database
python -c "from app.database import Database; db = Database('test.db'); print('OK')"
```
