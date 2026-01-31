# Modesto ModelT CLI Tools

Command-line tools for editing ModelT warehouse specifications with automatic name assignment and SVG regeneration.

## modelt-cli.js

**Purpose**: Main CLI entry point for warehouse editing operations

**Location**: `tools/modelt-cli.js`

**Usage**:
```bash
node tools/modelt-cli.js <warehouse> <command> [options]
```

### Camera Commands
```bash
# Add camera with auto-assigned name (food names)
node modelt-cli.js lodge add-camera --slab mercury --x 200 --y 300 --elevation 12 --direction 90 --tilt 25 --viewingAngle 90 --range 50

# Move camera
node modelt-cli.js lodge move-camera bagel 5 -10

# Update camera properties
node modelt-cli.js lodge update-camera bagel --direction 45 --tilt 30

# Delete camera
node modelt-cli.js lodge delete-camera bagel

# List all cameras
node modelt-cli.js lodge list-cameras
```

### Door Commands
```bash
# Add door (types: bay, rollup, personnel, cooler, interior)
node modelt-cli.js lodge add-door --slab mercury --wall mercury_perimeter --x 180 --y 400 --type bay --openingWidth 10 --openingHeight 12 --orientation horizontal --facing south

# Move, update, delete, list doors
node modelt-cli.js lodge move-door roosevelt 3 0
node modelt-cli.js lodge update-door taft --openingWidth 12
node modelt-cli.js lodge delete-door nixon
node modelt-cli.js lodge list-doors
```

### Partition & Column Commands
```bash
# Add partition wall
node modelt-cli.js lodge add-partition --slab mercury --start 200,250 --segments "east:50,south:20"

# Add/move/delete columns
node modelt-cli.js lodge add-column --slab mercury --x 240 --y 150 --height 15 --size 1
node modelt-cli.js lodge move-column oak 2 -3
node modelt-cli.js lodge delete-column ash
```

**Naming Conventions**:
- Cameras: Food names (bagel, bacon, beef, etc.)
- Doors: President names (washington, lincoln, etc.)
- Partition Walls: Female names (abigail, alice, etc.)
- Columns: Tree names (oak, maple, pine, etc.)

---

## modelt-query.js

**Purpose**: WebSocket queries to live BabylonJS scene for real-time data

**Location**: `tools/modelt-query.js`

**Usage**:
```bash
# Get camera intersection (what camera is pointing at)
node tools/modelt-query.js lodge get-camera-intersection brownie
```

**Prerequisites**: ModelT server running, browser viewing warehouse

---

## modelt-editor.js

**Purpose**: Core editing library (used by modelt-cli.js)

**Location**: `tools/modelt-editor.js`

---

## name-manager.js

**Purpose**: Auto-naming from ModelT convention lists

**Location**: `tools/name-manager.js`

---

## reload-warehouse.js

**Purpose**: Reload warehouse specification in browser

**Location**: `tools/reload-warehouse.js`

---

## test-websocket.js

**Purpose**: Test WebSocket connection to ModelT server

**Location**: `tools/test-websocket.js`

---

## Related

- Core Modesto: ModelT warehouse digital twin island
- ModelT warehouse builder skill
- Server: `c:/clients/modesto/server/server.js` (port 3000)
- See `CLAUDE_USAGE.md` for detailed Claude integration guide

---

<!-- TOOLS.md Alignment Footer -->
<!--
  tool_hashes:
    modelt-cli.js: 289bf9b2c6ee
    modelt-query.js: 68a0c29f6592
    modelt-editor.js: 3be51b73cc65
    name-manager.js: f2c0e42dc6d4
    reload-warehouse.js: 22257bab7ab7
    test-websocket.js: 976e43da35dd
  documented_at: 2026-01-30
  documented_by: tool gen-3
  footer_version: 1
-->
