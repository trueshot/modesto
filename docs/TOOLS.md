# Tools Reference

CLI tools and scripts for managing warehouse data and infrastructure.

**Source of Truth:** `C:/clients/modesto/warehouses/lodge/lodge.db` (SQLite)

---

## NVR & Channel Tools

**Owner:** novicat

**Location:** `C:/clients/modesto/tools/nvr-cli.js`

```bash
# NVRs
node tools/nvr-cli.js list [--ownership OURS|NOT_OURS] [--json]
node tools/nvr-cli.js show <id> [--json]
node tools/nvr-cli.js add <id> --brand X --ip X [options]
node tools/nvr-cli.js update <id> [options]
node tools/nvr-cli.js remove <id>

# Channels
node tools/nvr-cli.js channel list [--nvr <id>] [--json]
node tools/nvr-cli.js channel show <id>
node tools/nvr-cli.js channel add --nvr <id> --channel <num> [options]
node tools/nvr-cli.js channel update <id> [options]
node tools/nvr-cli.js channel remove <id>
```

**NVR Scanner:** `C:/clients/modesto/tools/nvr-scanner.js`

```bash
# Full network scan (ONVIF + port scan + classify NVR vs camera)
node tools/nvr-scanner.js scan [--json]

# Probe single device
node tools/nvr-scanner.js probe <ip>

# Scan and sync to lodge.db
node tools/nvr-scanner.js sync [--dry-run]
```

**ONVIF Discovery:** `C:/clients/modesto/tools/`

```bash
# Scan network for ONVIF devices
python tools/onvif-scan.py [--json] [--timeout N]

# Query single device
python tools/onvif-info.py <ip> [-u user] [-p pass] [--json]
```

**Documentation:**
- `c:/mindmap/A-FlowBrain/islands/core-modesto/cameras/NVR-API.md` — RTSP patterns, credentials, channel mapping
- `c:/mindmap/A-FlowBrain/islands/core-modesto/cameras/NVR-DISCOVERY.md` — Fingerprinting methods, discovery pipeline

---

## ModelT Tools

**Owner:** modestocat

**Location:** `C:/clients/modesto/modelt/scripts/`

```bash
# Generate SVG from JSON spec
node modelt/scripts/generate-svg.js <input.json> <output.svg>

# Query warehouse data (requires ws module)
node tools/modelt-query.js <warehouse> <command> [args]
```

---

## Neo4j Graph Tools

**Owner:** neocat

**Location:** `C:/clients/neoga/`

**Connection:** `192.168.1.56:7687` (neo4j/mindshare)

**Dashboard:** `http://localhost:3333` (run `node graph-dashboard-server.js`)

```bash
# Add a single node
node graph-add.js node <Type> <name> [prop=value ...]
# Example: node graph-add.js node NVR nvr1 brand="GW Security" ip=192.168.0.165 addedBy=neocat

# Add a relationship
node graph-add.js edge <from> <RELATIONSHIP> <to> [prop=value ...]
# Example: node graph-add.js edge nvr1 HAS_CHANNEL channel-nvr1-01 addedBy=neocat

# Query graph
node graph-add.js query "<cypher>"
# Example: node graph-add.js query "MATCH (n:NVR) RETURN n.name, n.ip"

# Import from JSON file
node graph-add.js import <file.json>
# JSON format: {"nodes": [...], "edges": [...]}

# Export snapshot (all nodes to node-list.json)
node graph-add.js snapshot

# Claim interest in a node (adds you to stakeholders)
node graph-add.js interest <nodeName> <yourName>
```

**Node Types:** NVR, Channel, Camera, Pallet, Zone, StackLocation, Forklift, Load, Corporal, Project, File

**Edge Types:** HAS_CHANNEL, SEEN_AT, STACKED_IN, LOADED_ONTO, OWNS, DEPENDS_ON, CALLS

**Important:** Always include `addedBy=<yourName>` for provenance tracking.

---

## AprilTag Tools

**Owner:** modeltapriltagcat

**Location:** `C:/clients/modesto/`

### ZPL Generation (SVG to Zebra Printer)

```bash
# Convert AprilTag SVG to ZPL for Zebra printer
node svg-to-zpl-cli.js <svg-file> [family] [tagId]

# Examples:
node svg-to-zpl-cli.js apriltag.svg                          # Default: tagStandard52h13, ID 0
node svg-to-zpl-cli.js apriltag.svg tagStandard52h13 12345   # Specific family and ID
node svg-to-zpl-cli.js apriltag.svg 36h11 500                # Building fiducial

# Output: ./zpl_output/apriltag_<family>_<id>.zpl
# Print: Send ZPL to printer port 9100 (nc <printer-ip> 9100 < file.zpl)
```

**Tag Families:**
- `tagStandard52h13`: 0-48,813 IDs (reserved)
- `tag36h11`: 0-2,286 IDs (building fiducials)
- `tagStandard41h12`: 0-4,294 IDs (pallets)
- `tag25h9`: forklifts

**Printer Specs (Zebra ZT421):**
- Resolution: 300 DPI
- Tag size: 5.5" × 5.5" (1650 × 1650 dots)
- Cell size: 0.55" (165 dots per cell in 10×10 grid)

### AprilTag Detection

```bash
# Multi-family detection on biscuit camera (GUI mode)
python detect_tags_biscuit.py

# Requires: pupil-apriltags, opencv-python, requests
# Camera service: http://localhost:8001
```

**Detection Output:**
- Tag ID, family, center coordinates, corners
- Decision margin (confidence score)
- Pose estimation (if camera params provided)

### Camera Calibration

**Intrinsics** (checkerboard calibration):
```bash
# Process checkerboard images to compute camera matrix
python cameras/calibration_process.py <facility> <camera_id>

# Output: warehouses/<facility>/calibration/<camera>/calibration.json
```

**Extrinsics** (AprilTag-based pose):
- Requires 4+ coplanar fiducials with known world coordinates
- Uses OpenCV solvePnP with camera intrinsics
- Fiducial positions: `warehouses/lodge/fiducial-placement.json`

**Documentation:** See `C:/mindmap/A-FlowBrain/islands/core-modesto/modesto-team-CLAUDE.md` for calibration pipeline.

---

## Babylon 3D Viewer

**Owner:** modeltbabylon

**Location:** `C:/clients/modesto/server/public/index.html`

**URL:** `http://localhost:5173/` (requires server running)

### Starting the Server

```bash
cd C:/clients/modesto/server
npm start   # or: node server.js
```

### Viewer Features

| Feature | How to Use |
|---------|------------|
| Overview camera | Click "Overview" button (orbital view, scroll to zoom) |
| First-person camera | Click camera button (e.g., "Bagel (#1)") |
| Position marking | In first-person view, click green sphere on wall to mark position |
| Camera mapping | Select camera → click "Map Camera" → select matching thumbnail |

### WebSocket Queries (via modelt-query.js)

```bash
# Get camera intersection point (what camera is looking at)
node tools/modelt-query.js lodge get-camera-intersection bagel

# Response: hit point, surface name, distance
```

### Thumbnail API

```bash
# List all thumbnails for a warehouse
curl http://localhost:5173/api/warehouses/lodge/thumbnails

# Filter by NVR (nvr1 = our cameras)
curl http://localhost:5173/api/warehouses/lodge/thumbnails?nvr=nvr1
```

### Coordinate System

```
SVG (2D)           →  Babylon (3D)
Origin: NW corner      babylonX = svgX
X: East               babylonY = elevation
Y: South              babylonZ = -svgY  (NEGATE!)
```

---

## Add Your Tools

Specialists: Add your domain tools below in the same format.
