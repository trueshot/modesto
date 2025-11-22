# ModelT Digital Twin - Features Summary

Quick reference guide to all features available in the ModelT warehouse digital twin system.

## Viewer Features

### Warehouse Selection
- **Dropdown Menu** - Select warehouse to view
- **Auto-Discovery** - Server auto-discovers all warehouses in `warehouses/` folder
- **Dynamic Loading** - SVG and metadata load on selection
- **Default Warehouse** - Defaults to 'lodge' on first load

### 3D View (Babylon.js)

#### Camera Controls
- **Overview Camera** - 360° orbital view, adjustable zoom (50-1500 units)
- **First-Person Cameras** - Position yourself at each security camera location
  - Click camera button to switch to that camera's view
  - Camera parameters: position, elevation, direction (0-360°), tilt angle
  - Real-time position and angle display
  - Adjustable field-of-view (FOV)
  - Keyboard + Mouse controls while in first-person

#### 3D Elements
- **Slab (Floor)** - Colored base with elevation
- **Perimeter Walls** - Building outer walls with corner detection
- **Partition Walls** - Interior walls with directional endcaps
- **Columns** - Structural H-beam supports
- **Doors** - All door types (bay, personnel, interior, cooler, rollup)
- **Cameras** - Red spheres at camera positions with coverage visualization
- **Ground Plane** - Grass-colored infinite ground
- **Coordinate Axes** - Red/Green/Blue (X/Y/Z) origin markers
- **Corner Markers** - Red (NW) and Green (SE) sphere markers

#### Real-Time Info Panel
- **Camera Name & Number** - ID from warehouse spec
- **Position (X, Y)** - Warehouse coordinates in feet
- **Elevation** - Height above slab
- **Direction** - Cardinal direction + degrees (0-360°)
- **Tilt** - Downward angle in degrees
- **Viewing Angle** - Field of view in degrees
- **Range** - Coverage range in feet

### 2D View (SVG Floor Plan)

#### Toggle Feature
- **Green Button** "View 2D SVG" in top bar
- **Full-Screen Display** - SVG takes entire viewport
- **Pan & Zoom** - Native browser SVG interaction
- **All Elements** - Floor plan shows walls, doors, cameras, columns
- **Embedded JSON** - Complete 3D data embedded in SVG

#### SVG Switching
- Click "View 2D SVG" to switch to floor plan
- Click "View 3D Model" to return to 3D view
- Seamless toggle - no data reloading needed

## API Endpoints

### Warehouse Data
| Endpoint | Returns |
|----------|---------|
| `GET /api/warehouses` | List of all warehouses |
| `GET /api/warehouses/:id` | Full spec + metadata for warehouse |
| `GET /api/warehouses/:id/metadata` | Metadata only |

### Camera Data
| Endpoint | Returns |
|----------|---------|
| `GET /api/warehouses/:id/cameras` | All cameras for warehouse |
| `GET /api/warehouses/:id/cameras/:id` | Specific camera details |

### SVG/3D Data
| Endpoint | Returns |
|----------|---------|
| `GET /api/warehouses/:id/svg` | SVG file (MIME: image/svg+xml) |
| `GET /api/warehouses/:id/svg-data` | SVG as JSON string |

### System
| Endpoint | Returns |
|----------|---------|
| `GET /api/health` | Server status `{status: "OK", environment: "development"}` |

## Warehouse Data Features

### Warehouse JSON Structure
- **Metadata** - Name, location, city/state, coordinates, dates
- **Property Boundary** - Overall property dimensions
- **Slabs** - Multiple building surfaces with elevation
- **Walls** - Perimeter, standalone, and partition walls
- **Doors** - Named by US Presidents (washington, lincoln, jefferson, etc.)
- **Columns** - Named by trees (oak, maple, pine, birch, etc.)
- **Cameras** - Named by foods (bagel, bacon, pizza, burger, etc.)

### SVG Generation
- **JSON → SVG** - `node generate-svg.js warehouse.json warehouse.svg`
- **Embedded JSON** - Full 3D spec embedded in SVG as metadata
- **2D Floor Plan** - Readable SVG layout for viewing and printing
- **Coordinate System** - Origin at NW corner, X=East, Y=South (feet)

## Naming Conventions

### Strict Naming Rules (Validated)
| Element | Name Source | Examples |
|---------|-------------|----------|
| Buildings/Slabs | Celestial bodies | mercury, venus, earth, mars, jupiter |
| Doors | US Presidents | washington, lincoln, jefferson, adams |
| Partition Walls | Female names | abigail, alice, catherine, charlotte |
| Cameras | Food names | bagel, bacon, pizza, donut, burger |
| Columns | Tree names | oak, maple, pine, birch, cedar |

See `modelt/NAMING_CONVENTIONS.json` for complete lists.

## Configuration

### Environment Variables (`.env`)
```
NODE_ENV=development
PORT=5173
```

### Server Config (`server/config/config.js`)
- CORS enabled (all origins)
- JSON body parsing
- Static file serving
- Auto-discovery of warehouses folder

## Performance Features

### Optimization
- **Lazy Loading** - SVG only fetched when warehouse selected
- **Efficient Parsing** - Turtle graphics converted to corners
- **Babylon.js Rendering** - GPU-accelerated 3D
- **Auto-Responsive** - Canvas scales with window resize

### Multi-Warehouse Ready
- **Scalable** - Add new warehouses without code changes
- **Auto-Discovery** - No registration needed
- **Seamless Switching** - Load different warehouses without restart

## Development Features

### Dev Mode
```bash
npm run dev
```
- Uses nodemon for auto-restart
- Watches file changes

### Debugging
- **Debug Panel** - Right side shows warehouse spec
- **Console Logging** - Camera parameters, timing info
- **Status Messages** - Loading, parsing, scene building progress

## Export Capabilities

### Data Export
- **Warehouse JSON** - Via API: `GET /api/warehouses/:id`
- **SVG Files** - Via API: `GET /api/warehouses/:id/svg`
- **Camera Positions** - Via API: `GET /api/warehouses/:id/cameras`

## Next Phase Ready (Not Implemented)

### Planned Features
- WebSocket real-time camera feeds
- Live object tracking (forklifts, products)
- Sensor data integration
- Historical playback
- Geofencing & alerts
- Multi-island navigation
- Autonomous agent visualization

---

## Usage Scenarios

### Facility Planning
1. View 2D SVG for layout analysis
2. Switch to 3D for spatial understanding
3. Check camera coverage from first-person views

### Security Operations
1. View 3D overview of entire facility
2. Switch to specific camera viewpoints
3. Identify blind spots with camera positioning

### Asset Tracking
1. Observe structure (columns, walls, doors) in 3D
2. Reference 2D layout for navigation
3. Use camera positions for monitoring points

### Development/Testing
1. Load different warehouses from dropdown
2. Test API endpoints directly
3. Export data for analysis

---

**Status**: Production Ready - All core features implemented and tested.
