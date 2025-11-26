# ModelT CLI Tools - Claude Usage Guide

This document explains how Claude should use the ModelT CLI tools for context-efficient editing of warehouse specifications.

## Overview

The ModelT CLI tools allow Claude to make surgical edits to warehouse specifications without loading entire JSON files into context. Operations are compact, names are auto-assigned, and SVG regeneration is automatic.

## Core Principle

**Instead of:**
1. Reading the entire 955-line JSON file (wastes context)
2. Making edits manually
3. Regenerating SVG

**Do this:**
1. Run a CLI command with parameters
2. Tool auto-assigns names, makes edits, regenerates SVG
3. Read the compact JSON output to inform the user

## Command Structure

```bash
node c:\clients\modesto\tools\modelt-cli.js <warehouse> <command> [options]
```

**Working Directory:** Should be the warehouse folder (e.g., `c:\clients\modesto\warehouses\lodge`)

## Auto-Naming

The tool automatically assigns names from convention lists:
- **Cameras**: Food names (bagel, bacon, beef, etc.)
- **Doors**: President names (washington, lincoln, roosevelt, etc.)
- **Partition Walls**: Female names (abigail, alice, charlotte, etc.)
- **Columns**: Tree names (oak, maple, pine, etc.)

Claude should **announce the auto-assigned name** to the user after each operation.

## Commands

### Cameras

#### Add Camera
```bash
node modelt-cli.js lodge add-camera \
  --slab mercury \
  --x 200 \
  --y 300 \
  --elevation 12 \
  --direction 90 \
  --tilt 25 \
  --viewingAngle 90 \
  --range 50 \
  --location "Optional description"
```

**Output:**
```json
{
  "success": true,
  "action": "add-camera",
  "component": {
    "id": "donut",
    "name": "Donut",
    "number": 16,
    "x": 200,
    "y": 300,
    "elevation": 12,
    "direction": 90,
    "tilt": 25,
    "viewingAngle": 90,
    "range": 50
  }
}
```

**Claude should say:** "Added camera **donut** (#16) at position (200, 300) facing east with 25° tilt"

#### Move Camera
```bash
node modelt-cli.js lodge move-camera bagel 5 -10
# Moves camera 'bagel' right 5 feet, up 10 feet (deltaX, deltaY)
```

#### Update Camera Properties
```bash
node modelt-cli.js lodge update-camera bagel --direction 45 --tilt 30
```

#### Delete Camera
```bash
node modelt-cli.js lodge delete-camera bagel
```

#### List All Cameras
```bash
node modelt-cli.js lodge list-cameras
```

---

### Doors

Doors support 5 types: **bay**, **rollup**, **personnel**, **cooler**, **interior**

#### Add Bay Door (Loading Dock)
```bash
node modelt-cli.js lodge add-door \
  --slab mercury \
  --wall mercury_perimeter \
  --x 180 \
  --y 400 \
  --type bay \
  --openingWidth 10 \
  --openingHeight 12 \
  --orientation horizontal \
  --facing south \
  --hardwareSide exterior \
  --hasDockSeal true \
  --hasDockLeveler true \
  --levelerWidth 8 \
  --levelerDepth 6
```

#### Add Roll-up Door
```bash
node modelt-cli.js lodge add-door \
  --slab mercury \
  --wall mercury_perimeter \
  --x 250 \
  --y 400 \
  --type rollup \
  --openingWidth 10 \
  --openingHeight 10 \
  --orientation horizontal \
  --facing south \
  --housingHeight 2 \
  --trackWidth 0.5
```

#### Add Personnel Door
```bash
node modelt-cli.js lodge add-door \
  --slab mercury \
  --wall mercury_perimeter \
  --x 300 \
  --y 400 \
  --type personnel \
  --openingWidth 3 \
  --openingHeight 7 \
  --orientation horizontal \
  --facing south \
  --swingDirection inward \
  --hingePosition left
```

#### Add Cooler Door
```bash
node modelt-cli.js lodge add-door \
  --slab mercury \
  --wall partition_1 \
  --x 220 \
  --y 200 \
  --type cooler \
  --openingWidth 6 \
  --openingHeight 8 \
  --orientation vertical \
  --facing east \
  --insulation 4 \
  --slideDirection left
```

#### Add Interior Opening
```bash
node modelt-cli.js lodge add-door \
  --slab mercury \
  --wall partition_1 \
  --x 240 \
  --y 200 \
  --type interior \
  --openingWidth 8 \
  --openingHeight 10 \
  --orientation vertical \
  --facing east \
  --hasPhysicalDoor false
```

**Output:**
```json
{
  "success": true,
  "action": "add-door",
  "component": {
    "id": "biden",
    "wallId": "mercury_perimeter",
    "x": 180,
    "y": 400,
    "type": "bay",
    "openingWidth": 10,
    "openingHeight": 12,
    "orientation": "horizontal",
    "facing": "south",
    "hardwareSide": "exterior",
    "hasDockSeal": true,
    "hasDockLeveler": true
  }
}
```

**Claude should say:** "Added bay door **biden** on mercury_perimeter wall at position (180, 400) facing south with dock seal and leveler"

#### Core Door Properties (All Types)
| Property | Description | Values |
|----------|-------------|--------|
| `--type` | Door type | bay, rollup, personnel, cooler, interior |
| `--openingWidth` | Width of opening (feet) | Number |
| `--openingHeight` | Height of opening (feet) | Number |
| `--orientation` | Wall orientation | horizontal, vertical |
| `--facing` | Direction through door | north, south, east, west |
| `--hardwareSide` | Which side has hardware | interior, exterior |
| `--state` | Door state | open, closed, partial |

#### Type-Specific Properties
| Type | Properties |
|------|------------|
| bay | `hasDockSeal`, `hasDockLeveler`, `hasSafetyStriping`, `dockSealWidth`, `dockSealHeight`, `levelerWidth`, `levelerDepth` |
| rollup | `housingHeight`, `trackWidth` |
| personnel | `frameWidth`, `swingDirection` (inward/outward), `hingePosition` (left/right) |
| cooler | `insulation`, `slideDirection` (left/right), `trackPosition` |
| interior | `hasPhysicalDoor` (true/false) |

#### Move Door
```bash
node modelt-cli.js lodge move-door roosevelt 3 0
# Moves door 'roosevelt' right 3 feet
```

#### Update Door Properties
```bash
node modelt-cli.js lodge update-door taft --openingWidth 12 --hasDockSeal true
```

#### Delete Door
```bash
node modelt-cli.js lodge delete-door nixon
```

#### List All Doors
```bash
node modelt-cli.js lodge list-doors
```

---

### Partition Walls

#### Add Partition Wall
```bash
node modelt-cli.js lodge add-partition \
  --slab mercury \
  --start 200,250 \
  --segments "east:50,south:20"
```

**Segments format:** `direction:length,direction:length,...`
- Directions: `north`, `south`, `east`, `west`
- Length: in feet

**Output:**
```json
{
  "success": true,
  "action": "add-partition",
  "component": {
    "id": "charlotte",
    "type": "partition",
    "start": {"x": 200, "y": 250},
    "segments": [
      {"direction": "east", "length": 50},
      {"direction": "south", "length": 20}
    ]
  }
}
```

**Claude should say:** "Added partition wall **charlotte** starting at (200, 250), running east 50ft then south 20ft"

#### Delete Partition Wall
```bash
node modelt-cli.js lodge delete-partition charlotte
```

---

### Columns

#### Add Column
```bash
node modelt-cli.js lodge add-column \
  --slab mercury \
  --x 240 \
  --y 150 \
  --height 15 \
  --size 1 \
  --location "Packing Line 2, 70ft W / 50ft N"
```

**Output:**
```json
{
  "success": true,
  "action": "add-column",
  "component": {
    "id": "ash",
    "name": "Ash",
    "x": 240,
    "y": 150,
    "height": 15,
    "size": 1,
    "location": "Packing Line 2, 70ft W / 50ft N"
  }
}
```

**Claude should say:** "Added column **ash** at position (240, 150) with height 15ft"

#### Move Column
```bash
node modelt-cli.js lodge move-column oak 2 -3
# Moves column 'oak' right 2 feet, up 3 feet
```

#### Delete Column
```bash
node modelt-cli.js lodge delete-column ash
```

#### List All Columns
```bash
node modelt-cli.js lodge list-columns
```

---

## Context-Efficient Workflow

### User Says:
"Add a camera on the west wall at 155, 250, facing east, 20° tilt"

### Claude Does:
1. Runs CLI command:
```bash
node c:\clients\modesto\tools\modelt-cli.js lodge add-camera \
  --slab mercury \
  --x 155 \
  --y 250 \
  --elevation 12 \
  --direction 90 \
  --tilt 20 \
  --viewingAngle 90 \
  --range 50
```

2. Reads JSON output (compact, ~10 lines)

3. Responds to user:
"Added camera **donut** (#16) at position (155, 250) facing east with 20° tilt. The SVG has been regenerated and BabylonJS should auto-reload."

### Total Context Used: < 200 tokens
vs. reading entire JSON: ~8000 tokens

---

## Error Handling

If the command fails, the output will be:
```json
{
  "success": false,
  "error": "Error message here"
}
```

Claude should inform the user of the error and suggest corrections.

---

## When to Use Manual Editing

Use the CLI tool for:
- Adding components
- Moving components
- Updating simple properties
- Deleting components

Fall back to manual JSON editing when:
- The tool doesn't support the operation
- Complex multi-step edits are needed
- Debugging tool issues

---

## Future Skill Integration

This document will be used to integrate these tools into the ModelT warehouse builder skill. In a dedicated session, we'll:
1. Read the skill architecture documentation
2. Update the skill prompt to reference these tools
3. Add usage examples to the skill
4. Test the integration

---

**Note:** Always run commands from the warehouse directory (e.g., `c:\clients\modesto\warehouses\lodge`) or use absolute paths.

---

## WebSocket Real-Time Queries

### Camera Intersection Raycasting

Query the live BabylonJS scene to find what a camera is pointing at using raycasting.

#### Command
```bash
node c:\clients\modesto\tools\modelt-query.js lodge get-camera-intersection <cameraId>
```

#### Example
```bash
node c:\clients\modesto\tools\modelt-query.js lodge get-camera-intersection brownie
```

#### Output
```json
{
  "camera": {
    "id": "brownie",
    "name": "Brownie",
    "number": 6,
    "position": { "x": 323, "y": 274, "elevation": 12 },
    "direction": 270,
    "tilt": 20
  },
  "intersection": {
    "hit": true,
    "objectName": "mercury_mercury_perimeter_seg3",
    "objectType": "wall",
    "point": { "x": 170.5, "y": 10.2, "z": -250.3 },
    "svgCoords": { "x": 170.5, "y": 250.3 },
    "distance": 45.2
  }
}
```

#### User Workflow

**User says:** "Look at brownie"

**Claude does:**
```bash
node modelt-query.js lodge get-camera-intersection brownie
```

**Claude responds:** 
"Camera **brownie** (#6) at position (323, 274) facing west (270°) with 20° tilt is pointing at the **mercury_perimeter wall** at SVG coordinates **(170.5, 250.3)**, 45.2 feet away."

**User says:** "Add a door there"

**Claude does:**
```bash
node modelt-cli.js lodge add-door \
  --slab mercury \
  --wall mercury_perimeter \
  --x 170.5 \
  --y 250.3 \
  --bayWidth 10 \
  --type bay \
  --orientation vertical \
  --facing west
```

**Claude responds:**
"Added door **biden** at (170.5, 250.3) on mercury_perimeter wall facing west"

#### Use Cases

1. **Spatial pointer** - Use camera as a pointer to identify wall locations
2. **Verify camera coverage** - Confirm what areas cameras can see
3. **Measure distances** - Know exact distance from camera to target
4. **Debug positioning** - Validate camera orientations
5. **Ground truth alignment** - Compare real camera view with model intersections

#### Prerequisites

- ModelT server must be running (`node server/server.js`)
- Browser must be viewing the warehouse at `http://localhost:3000`
- WebSocket connection automatically established on page load

#### Error Handling

If no intersection is found:
```json
{
  "camera": {
    "id": "brownie",
    ...
  },
  "intersection": {
    "hit": false,
    "message": "Camera view line does not intersect any objects within range"
  }
}
```

---

**Note:** WebSocket queries require the browser to be open and viewing the warehouse. The query is sent to the live 3D scene for real-time raycasting.
