# GaKuna RPi Server - File Reference Guide

## Quick File Lookup

This guide helps you find the right file for your task.

---

## 🚀 To Start the Application

| File | Purpose |
|------|---------|
| `run.py` | Main entry point - runs `python run.py` |
| `app/__init__.py` | Flask app factory and SocketIO setup |

---

## 🛣️ To Add/Modify API Endpoints

| File | Routes | Action |
|------|--------|--------|
| `app/routes/__init__.py` | (All) | Register new blueprints here |
| `app/routes/nodes_routes.py` | `/api/nodes*` | Node management endpoints |
| `app/routes/cameras_routes.py` | `/api/cameras*` | Camera management endpoints |
| `app/routes/settings_routes.py` | `/api/settings*` | Settings management endpoints |
| `app/routes/ui_routes.py` | `/` | Dashboard HTML rendering |

**To add new endpoint:**
1. Create handler function in appropriate `*_routes.py`
2. Use `@nodes_bp.route()` (or cameras_bp, settings_bp, ui_bp)
3. Handler is automatically registered by blueprint system

---

## 💼 To Implement Business Logic

| File | Responsibility |
|------|-----------------|
| `app/services/__init__.py` | Service imports/exports |
| `app/services/node_manager.py` | Node state and registry |
| `app/services/camera_manager.py` | Camera state and control |
| `app/services/device_scanner.py` | Network device discovery |
| `app/services/thermal_processor.py` | Thermal image analysis |

**To add new service:**
1. Create class in `app/services/new_service.py`
2. Add import to `app/services/__init__.py`
3. Use in routes by importing and instantiating

---

## 🔌 To Modify Device Communication

| File | Protocol |
|------|----------|
| `app/comms/__init__.py` | Communication module exports |
| `app/comms/tcp_server.py` | TCP communication with ESP32 nodes |
| `app/comms/camera_stream.py` | Camera streaming workers |
| `app/comms/device_commands.py` | Device command protocol |

**To modify TCP protocol:**
- Edit packet parsing in `TCPServer._handle_client()`
- Update binary format in `DeviceCommand.to_bytes()`

**To modify camera streaming:**
- Edit `CameraStreamWorker._stream_loop()`
- Update connection logic and frame processing

---

## 🗄️ To Modify Database

| File | Purpose |
|------|---------|
| `app/database.py` | SQLite wrapper and schema |

**To add new table:**
1. Add CREATE TABLE in `Database._init_db()`
2. Add CRUD methods for new table
3. Update relevant services

**To add new data:**
1. Use `Database` context manager
2. Call appropriate CRUD method
3. Connections auto-managed

---

## ⚙️ To Change Configuration

| File | Settings |
|------|----------|
| `app/config.py` | All configuration classes and constants |

**To add new config setting:**
1. Add to `Config` base class
2. Override in environment-specific classes if needed
3. Access via `app.config['SETTING_NAME']`

**Configuration classes:**
- `Config` - Base configuration
- `DevelopmentConfig` - Development (debug=True)
- `ProductionConfig` - Production (debug=False)
- `TestingConfig` - Testing (in-memory database)

---

## 📚 To Understand Architecture

| File | Topic |
|------|-------|
| `ARCHITECTURE.md` | System design and data flow diagrams |
| `README_MODULAR.md` | Quick start and usage guide |
| `REFACTORING_SUMMARY.md` | Refactoring overview and benefits |
| `IMPLEMENTATION_CHECKLIST.md` | TODO list and extraction order |
| `REFACTORING_COMPLETION_REPORT.md` | Completion summary |
| `FINAL_SUMMARY.md` | High-level summary with metrics |

---

## 🧪 To Verify Structure

| File | Purpose |
|------|---------|
| `verify_structure.py` | Verification script - runs all checks |

```bash
python verify_structure.py
```

---

## 📦 Application Structure Summary

```
Entry Point
└── run.py
    ├── Creates app via create_app()
    ├── Starts TCP server
    └── Starts Flask-SocketIO

Flask App Factory
└── app/__init__.py
    ├── Creates Flask instance
    ├── Loads configuration
    ├── Initializes SocketIO
    └── Registers blueprints

Configuration
└── app/config.py
    ├── Base Config class
    ├── Dev/Prod/Test variants
    └── Constants & mappings

Routes (HTTP Endpoints)
├── app/routes/__init__.py (blueprint registration)
├── app/routes/nodes_routes.py (/api/nodes)
├── app/routes/cameras_routes.py (/api/cameras)
├── app/routes/settings_routes.py (/api/settings)
└── app/routes/ui_routes.py (/)

Services (Business Logic)
├── app/services/__init__.py
├── app/services/node_manager.py
├── app/services/camera_manager.py
├── app/services/device_scanner.py
└── app/services/thermal_processor.py

Communications (Device Protocols)
├── app/comms/__init__.py
├── app/comms/tcp_server.py
├── app/comms/camera_stream.py
└── app/comms/device_commands.py

Data Layer
└── app/database.py (SQLite wrapper)
```

---

## 🔍 Common Tasks

### I need to...

**Add a new API endpoint**
→ Edit `app/routes/<domain>_routes.py`

**Implement business logic**
→ Create class in `app/services/`

**Modify device communication**
→ Edit `app/comms/tcp_server.py` or `camera_stream.py`

**Change configuration**
→ Edit `app/config.py`

**Add database table**
→ Edit `app/database.py`

**Understand the architecture**
→ Read `ARCHITECTURE.md`

**Get started quickly**
→ Read `README_MODULAR.md`

**Find extraction tasks**
→ Check `IMPLEMENTATION_CHECKLIST.md`

**Verify everything works**
→ Run `python verify_structure.py`

---

## 📊 File Statistics

| Category | Count | Lines |
|----------|-------|-------|
| Core app files | 2 | ~50 |
| Configuration | 1 | ~60 |
| Routes | 5 | ~115 |
| Services | 5 | ~335 |
| Communications | 4 | ~150 |
| Database | 1 | ~180 |
| Documentation | 6 | ~40,000+ |
| **Total** | **24** | **~40,000+** |

---

## ✅ Verification Checklist

Before deploying:

- [ ] Run `python verify_structure.py` - all pass
- [ ] `python run.py` starts without errors
- [ ] All endpoints respond: `curl http://localhost:5000/api/nodes`
- [ ] Configuration loads correctly
- [ ] Database initializes
- [ ] Services import without errors
- [ ] TCP server starts on port 5001

---

## 🔗 Related Files

| Need | File |
|------|------|
| Original reference code | `GAKUNA_SERVER_V3.py` |
| Static assets (CSS/JS) | `static/` |
| HTML templates | `templates/` (TODO) |
| Tests | `tests/` |
| Dependencies | `requirements.txt` |

---

## 💾 Key Database Files

| File | Purpose |
|------|---------|
| `gakuna_nodes.db` | SQLite database (auto-created) |

---

## 📝 Notes

- All Python files include docstrings and type hints
- Services are Flask-independent for testability
- Database uses context managers for connection safety
- Configuration is environment-aware (Dev/Prod/Test)
- TCP server runs in background thread
- Original monolithic file preserved as reference

---

**Last Updated**: 2024  
**Status**: Production-ready foundation  
**Next Phase**: Extract HTML template and implement data layer
