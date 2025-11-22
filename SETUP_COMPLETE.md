# ModelT Digital Twin - Setup Complete

## Overview

Successfully set up the Express server infrastructure for the ModelT warehouse digital twin system. The system now has:

- **Modular architecture** with separate folders for infrastructure, warehouses, and server
- **REST APIs** for accessing warehouse data dynamically
- **3D Viewer** powered by Babylon.js that connects to APIs
- **Multi-warehouse support** (currently lodge, expandable to denver, atlanta, etc.)

## Folder Structure

```
modesto/
├── modelt/                           # ModelT infrastructure & scripts
│   ├── README.md
│   ├── scripts/
│   │   ├── generate-svg.js           # SVG generator from JSON
│   │   ├── warehouseParser.js        # Parse ModelT SVGs
│   │   ├── harvestWarehouseSVG2.js   # Batch processor
│   │   └── package.json
│   ├── NAMING_CONVENTIONS.json       # Naming rules
│   ├── *.txt                         # Naming lists (doors, cameras, etc.)
│   └── MODELT_SPECIFICATION.md       # Technical spec (in flowbrain)
│
├── warehouses/                       # Warehouse facility data
│   ├── README.md
│   └── lodge/                        # First warehouse (South Carolina)
│       ├── warehouse.json            # ModelT spec (source of truth)
│       ├── warehouse.svg             # Generated 2D floor plan + 3D data
│       └── metadata.json             # Facility metadata
│
└── server/                           # Express.js digital twin server
    ├── README.md
    ├── server.js                     # Main app
    ├── package.json                  # Dependencies installed ✓
    ├── config/
    │   └── config.js                 # Configuration
    ├── routes/
    │   └── api/
    │       ├── warehouses.js         # Warehouse endpoints
    │       ├── camera.js             # Camera endpoints
    │       └── svg.js                # SVG endpoints
    ├── middleware/
    │   └── errorHandler.js
    ├── public/
    │   ├── index.html                # 3D Viewer UI (dynamic, API-driven)
    │   └── js/
    │       └── warehouseParser.js    # ModelT parser library
    └── node_modules/                 # Dependencies (npm installed)
```

## What's Ready to Use

### 1. Server API Endpoints

```
GET /api/warehouses                    # List all warehouses
GET /api/warehouses/:id                # Get spec + metadata
GET /api/warehouses/:id/metadata       # Metadata only
GET /api/warehouses/:id/cameras        # All cameras for warehouse
GET /api/warehouses/:id/cameras/:id    # Specific camera
GET /api/warehouses/:id/svg            # SVG file (image/svg+xml)
GET /api/warehouses/:id/svg-data       # SVG as JSON
GET /api/health                        # Health check
```

### 2. 3D Viewer Features

**Interactive 3D Model:**
- Load any warehouse dynamically (defaults to 'lodge')
- Switch between warehouses via dropdown selector
- Overview camera (360° rotation)
- First-person camera views from security camera positions
- Real-time camera info display (position, direction, elevation, tilt)
- 3D scene with:
  - Slab footprints (colored floor)
  - Walls (perimeter, standalone, partition)
  - Columns (structural H-beams)
  - Doors (bay, personnel, interior, cooler, rollup)
  - Cameras (red markers with coverage cones)
  - Ground plane with coordinate axes

**2D SVG View Toggle** (NEW):
- Green "View 2D SVG" button switches to 2D floor plan
- Full-screen SVG viewer with pan/zoom capability
- "View 3D Model" button returns to 3D mode
- SVG displays complete floor plan layout with all elements
- Seamless switching between 2D and 3D views

### 3. SVG Generation

Lodge warehouse SVG has been generated from the JSON spec:
- `warehouses/lodge/warehouse.json` → `warehouse.svg`
- Contains embedded JSON for 3D data
- Ready for 3D viewer consumption

## Getting Started

### 1. Start the Server

```bash
cd server
npm start
```

Server will run on `http://localhost:5173` (configured in `.env`)

### 2. Open the Viewer

```
http://localhost:3000
```

Features:
- Warehouse dropdown (auto-discovers all warehouses)
- Camera buttons for each security camera
- Overview button for global view
- Real-time camera info panel

### 3. Add New Warehouses

```bash
mkdir warehouses/denver
cp warehouses/lodge/warehouse.json warehouses/denver/
# Edit warehouse.json with denver specs...
node modelt/scripts/generate-svg.js warehouses/denver/warehouse.json warehouses/denver/warehouse.svg
# Add metadata.json
```

Server auto-discovers new warehouses.

## Environment Variables

Create `.env` in server folder:

```
NODE_ENV=development
PORT=3000
```

## Development Mode

```bash
cd server
npm run dev
```

Uses nodemon to auto-restart on file changes.

## Architecture Decisions

### Why This Structure?

1. **Separation of Concerns**
   - `modelt/` = Shared tools (work with ANY warehouse)
   - `warehouses/` = Facility data (lodge, denver, atlanta, etc.)
   - `server/` = Runtime service layer

2. **Dynamic Routing**
   - Server discovers warehouses from filesystem
   - No hardcoding of warehouse IDs
   - Add warehouses = add folder + run generation

3. **API-First Design**
   - 3D viewer is decoupled from data source
   - Ready for WebSocket real-time updates
   - Mobile apps can use same APIs

4. **Client-Side Rendering**
   - Babylon.js handles 3D rendering
   - Server just serves data
   - Scales to many concurrent users

## Next Steps

### Ready Now
- [ ] Start server: `npm start`
- [ ] Test viewer at http://localhost:3000
- [ ] Try camera switching
- [ ] Test warehouse dropdown

### Phase 2 (Real-Time Digital Twin)
- [ ] Add WebSocket support for live camera feeds
- [ ] Add real-time object tracking (forklifts, products)
- [ ] Add sensor data integration
- [ ] Add historical playback
- [ ] Add geofencing/alerts

### Phase 3 (Multi-Island)
- [ ] Support multiple warehouses in one facility
- [ ] Add inter-warehouse routing
- [ ] Add autonomous agent visualization

## Technical Details

### ModelT Format
- **V2 (Current)**: Multi-slab format with slabs array
- **V1**: Legacy single-slab format (still supported)
- Embedded JSON in SVG for 3D data
- Turtle graphics for wall definitions

### Coordinate System
- **Origin**: Northwest corner (0, 0)
- **X-axis**: East (increases right)
- **Y-axis**: South (increases down)
- **Units**: Feet
- **Babylon conversion**: X→X, Y→-Z (negate for proper orientation)

### Naming Conventions
- **Buildings**: Celestial bodies (mercury, venus, earth, mars, jupiter, etc.)
- **Doors**: US Presidents (washington, lincoln, jefferson, etc.)
- **Partition Walls**: Female names (abigail, alice, catherine, etc.)
- **Cameras**: Food names (bagel, bacon, beef, biscuit, bread, etc.)
- **Columns**: Tree names (oak, maple, pine, birch, cedar, elm, etc.)

## Documentation

Full technical documentation available in:
- **ModelT Specification**: flowbrain/core-modesto
- **API Endpoints**: server/README.md
- **Warehouse Data Format**: modelt/scripts/README.md
- **Installation**: This file

## Support

### Port 3000 Already In Use?
```bash
# Change port in .env
PORT=3001

# Or kill existing process
netstat -ano | grep 3000
taskkill /PID <PID> /F
```

### Dependencies Missing?
```bash
cd server
npm install
```

### SVG Generation Failed?
```bash
cd modelt/scripts
node generate-svg.js ../../warehouses/lodge/warehouse.json ../../warehouses/lodge/warehouse.svg
```

## Completed Tasks

✅ Created server directory structure
✅ Created Express server with middleware
✅ Created REST API routes (warehouses, cameras, SVG)
✅ Adapted warehouse3d.html to use APIs
✅ Installed dependencies
✅ Generated lodge warehouse SVG
✅ Copied supporting scripts and naming conventions
✅ Created documentation

## Status

**READY FOR TESTING** - All setup complete, ready to start server and test 3D viewer.
