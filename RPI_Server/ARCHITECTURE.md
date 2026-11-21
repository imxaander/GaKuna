# GaKuna RPi Server - Modular Architecture Diagram

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Flask Application (run.py)                       │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                      HTTP/SocketIO Client                           │ │
│  │                   (Browser Dashboard + Real-time)                   │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                 │                                        │
│                     ┌───────────┴───────────┐                           │
│                     ▼                       ▼                           │
│          ┌────────────────────┐  ┌────────────────────┐                │
│          │   API Routes       │  │   WebSocket Events │                │
│          │  (Flask Blueprint) │  │  (SocketIO)        │                │
│          │                    │  │                    │                │
│          │ /api/nodes        │  │ node:updated       │                │
│          │ /api/cameras      │  │ camera:updated     │                │
│          │ /api/settings     │  │ evacuation:alert   │                │
│          │ /dashboard        │  │                    │                │
│          └────────────────────┘  └────────────────────┘                │
│                     │                       │                           │
│                     └───────────┬───────────┘                           │
│                                 ▼                                        │
│          ┌────────────────────────────────────────────┐                │
│          │        Service Layer (Business Logic)      │                │
│          │                                            │                │
│          │  ┌──────────┐  ┌──────────┐  ┌──────────┐ │                │
│          │  │  Node    │  │ Camera   │  │ Thermal  │ │                │
│          │  │ Manager  │  │ Manager  │  │Processor │ │                │
│          │  └──────────┘  └──────────┘  └──────────┘ │                │
│          │                                            │                │
│          │  ┌──────────┐  ┌──────────────────────┐  │                │
│          │  │ Device   │  │ Evacuation Logic     │  │                │
│          │  │ Scanner  │  │ (Path generation)    │  │                │
│          │  └──────────┘  └──────────────────────┘  │                │
│          └────────────────────────────────────────────┘                │
│                     │                                                   │
│          ┌──────────┴──────────────────────────┐                       │
│          ▼                                     ▼                       │
│  ┌──────────────────────┐         ┌──────────────────────┐            │
│  │  Communication Layer │         │  Data Layer          │            │
│  │                      │         │  (Database)          │            │
│  │ ┌────────────────┐   │         │                      │            │
│  │ │ TCP Server     │   │         │  SQLite Database     │            │
│  │ │ (ESP32 Comms)  │───┼─────────│  - nodes table       │            │
│  │ └────────────────┘   │         │  - cameras table     │            │
│  │                      │         │  - settings table    │            │
│  │ ┌────────────────┐   │         │                      │            │
│  │ │ Camera Stream  │   │         │  Abstraction Layer   │            │
│  │ │ Workers        │   │         │  - CRUD operations   │            │
│  │ └────────────────┘   │         │  - Connection mgmt   │            │
│  │                      │         └──────────────────────┘            │
│  │ ┌────────────────┐   │                                             │
│  │ │ Device         │   │                                             │
│  │ │ Commands       │   │                                             │
│  │ └────────────────┘   │                                             │
│  └──────────────────────┘                                             │
│          │                                                             │
└──────────┼─────────────────────────────────────────────────────────────┘
           │
           ├─ ESP32 Nodes (TCP port 5001)
           │  - Temperature sensors
           │  - Thermal camera data
           │  - Blockage detection
           │  - Audio playback
           │
           ├─ IP Cameras (HTTP)
           │  - Thermal imaging
           │  - Blockage detection
           │
           └─ Local Network (mDNS / DHCP)
              - Device discovery
              - Network scanning
```

## Module Organization

```
RPI_Server/
│
├── run.py                       # Entry point - starts app and TCP server
│
├── app/                         # Main application package
│   │
│   ├── __init__.py             # App factory (create_app)
│   │                           # SocketIO initialization
│   │
│   ├── config.py               # Configuration classes
│   │                           # - Base, Development, Production, Testing
│   │                           # - Device constants and mappings
│   │
│   ├── database.py             # Database abstraction
│   │                           # - SQLite wrapper
│   │                           # - CRUD operations
│   │                           # - Connection pooling
│   │
│   ├── routes/                 # HTTP API endpoints (Flask Blueprints)
│   │   ├── __init__.py         # Blueprint registration
│   │   ├── nodes_routes.py     # /api/nodes endpoints
│   │   ├── cameras_routes.py   # /api/cameras endpoints
│   │   ├── settings_routes.py  # /api/settings endpoints
│   │   └── ui_routes.py        # / (dashboard) route
│   │
│   ├── services/               # Business logic (no Flask dependencies)
│   │   ├── __init__.py         # Service exports
│   │   ├── node_manager.py     # Node state management
│   │   ├── camera_manager.py   # Camera state management
│   │   ├── device_scanner.py   # Network scanning & discovery
│   │   └── thermal_processor.py # Thermal analysis & blockage detection
│   │
│   ├── comms/                  # Device communication
│   │   ├── __init__.py         # Communication module exports
│   │   ├── tcp_server.py       # TCP server for ESP32 nodes
│   │   ├── camera_stream.py    # Camera streaming workers
│   │   └── device_commands.py  # Device command protocol
│   │
│   └── templates/              # (TODO) HTML templates
│       └── dashboard.html      # Main dashboard (to be extracted)
│
├── static/                     # Static assets (CSS, JS, etc.)
│
├── GAKUNA_SERVER_V3.py        # Original monolithic file (reference)
│
├── requirements.txt            # Python dependencies
│
├── REFACTORING_SUMMARY.md     # Architecture overview & migration guide
│
└── IMPLEMENTATION_CHECKLIST.md # TODO list and extraction order
```

## Data Flow

### 1. Node Data Reception Flow

```
ESP32 Node (TCP)
      │
      │ Sends sensor data packet (TCP port 5001)
      ▼
┌─────────────────────┐
│   TCPServer         │
│ (tcp_server.py)     │
│                     │
│ - Accept connection │
│ - Parse packet      │
│ - Validate data     │
└─────────────────────┘
      │
      │ Update state
      ▼
┌─────────────────────┐
│  NodeManager        │
│ (node_manager.py)   │
│                     │
│ - Update NodeState  │
│ - Persist to DB     │
│ - Notify services   │
└─────────────────────┘
      │
      ├─ Persist to Database
      │  └─ Database (database.py)
      │
      └─ Real-time update
         └─ SocketIO Event
            └─ Browser Dashboard
```

### 2. Dashboard Request Flow

```
Browser Request (HTTP GET /)
      │
      ▼
┌─────────────────────────────┐
│  ui_routes.dashboard()      │
│ (ui_routes.py)              │
└─────────────────────────────┘
      │
      │ Fetch current state
      ▼
┌─────────────────────────────┐
│  Service Layer              │
│                             │
│ - NodeManager.get_all()     │
│ - CameraManager.get_all()   │
│ - Database.get_settings()   │
└─────────────────────────────┘
      │
      │ Render HTML
      ▼
┌─────────────────────────────┐
│  Jinja2 Template Rendering  │
│ (templates/dashboard.html)  │
│                             │
│ - Insert node data          │
│ - Insert camera list        │
│ - Insert 3D floor map       │
└─────────────────────────────┘
      │
      ▼
   HTML Response → Browser
```

### 3. Command Execution Flow

```
Browser Button Click (e.g., "Play Audio")
      │
      ▼
┌─────────────────────────────┐
│  SocketIO Event Handler     │
│ (node:play_audio)           │
└─────────────────────────────┘
      │
      │ Validate & process
      ▼
┌─────────────────────────────┐
│  DeviceCommandSender        │
│ (device_commands.py)        │
│                             │
│ - Create DeviceCommand      │
│ - Serialize to bytes        │
│ - Determine target node IP  │
└─────────────────────────────┘
      │
      │ Send command
      ▼
┌─────────────────────────────┐
│  TCPServer.send_command()   │
│ (tcp_server.py)             │
│                             │
│ - Connect to node           │
│ - Send packet               │
│ - Close connection          │
└─────────────────────────────┘
      │
      ▼
   ESP32 Node Receives & Executes Command
```

## Configuration Flow

```
Environment Variables
      │
      ▼
┌─────────────────────┐
│  config.py          │
│                     │
│ - Config class      │
│ - Dev/Prod variants │
│ - Device mappings   │
│ - Constants         │
└─────────────────────┘
      │
      ├─ Loaded by run.py
      │
      ├─ Passed to create_app()
      │
      └─ Available as app.config
```

## Service Dependencies

```
Routes Layer
      │
      ├─ Depends on → Services
      │               │
      │               ├─ NodeManager
      │               │   └─ Depends on → Database
      │               │
      │               ├─ CameraManager
      │               │   └─ Depends on → Database
      │               │
      │               ├─ DeviceScanner
      │               │   └─ No external deps
      │               │
      │               └─ ThermalProcessor
      │                   └─ Depends on → cv2 (numpy)
      │
      └─ Depends on → Communications
                      │
                      ├─ TCPServer
                      │   └─ No deps
                      │
                      ├─ CameraStreamWorker
                      │   └─ No deps
                      │
                      └─ DeviceCommandSender
                          └─ Depends on → TCPServer
```

## Deployment Architecture

```
┌────────────────────────────────────────────────────────┐
│                    Raspberry Pi                        │
│                                                        │
│  ┌────────────────────────────────────────────────┐  │
│  │  Python Application (Flask + SocketIO)         │  │
│  │  - Listens on :5000 (HTTP/WebSocket)          │  │
│  │  - Listens on :5001 (TCP from devices)        │  │
│  └────────────────────────────────────────────────┘  │
│          │                                             │
│          ├─ Local Network (Ethernet/WiFi)             │
│          │                                             │
│          ├─ Cloudflare Tunnel (admin.gakuna.site)   │
│          │                                             │
└──────────┼────────────────────────────────────────────┘
           │
           ├─ ESP32 Nodes (Local TCP)
           │
           ├─ IP Cameras (Local HTTP)
           │
           ├─ Browser (Local or Remote via Tunnel)
           │
           └─ Database (Local SQLite)
```

This modular architecture provides:
- ✅ Clear separation of concerns
- ✅ Easy testing and debugging
- ✅ Scalability for future features
- ✅ Configuration management
- ✅ Database abstraction
- ✅ Reusable business logic
- ✅ Modular communication layer
