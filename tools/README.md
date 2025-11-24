# ModelT CLI Tools

Command-line tools for editing ModelT warehouse specifications with automatic name assignment and SVG regeneration.

## Files

- **`modelt-cli.js`** - Main CLI entry point
- **`modelt-editor.js`** - Core editing library
- **`name-manager.js`** - Auto-naming from convention lists
- **`CLAUDE_USAGE.md`** - Complete guide for Claude Code integration
- **`package.json`** - Node.js package configuration

## Quick Start

### Add a camera
```bash
node modelt-cli.js lodge add-camera \
  --slab mercury \
  --x 200 --y 300 \
  --elevation 12 \
  --direction 90 \
  --tilt 25 \
  --viewingAngle 90 \
  --range 50
```

### Move a door
```bash
node modelt-cli.js lodge move-door roosevelt 3 0
```

### Add a partition wall
```bash
node modelt-cli.js lodge add-partition \
  --slab mercury \
  --start 200,250 \
  --segments "east:50,south:20"
```

### List all cameras
```bash
node modelt-cli.js lodge list-cameras
```

## Features

- **Auto-naming**: Names are automatically assigned from ModelT naming conventions
- **Auto-increment**: Camera numbers increment automatically
- **SVG regeneration**: SVG is regenerated automatically after edits
- **Compact output**: Returns minimal JSON for context efficiency
- **Error handling**: Clear error messages with suggestions

## Naming Conventions

Names are read from the ModelT warehouse builder skill:
- **Cameras**: Food names (bagel, bacon, beef, biscuit, etc.)
- **Doors**: US Presidents (washington, lincoln, roosevelt, etc.)
- **Partition Walls**: Female names (abigail, alice, charlotte, etc.)
- **Columns**: Tree names (oak, maple, pine, birch, etc.)

## Commands

### Cameras
- `add-camera` - Add a new camera with auto-assigned name
- `move-camera <id> <deltaX> <deltaY>` - Move a camera
- `update-camera <id> [--prop value]` - Update camera properties
- `delete-camera <id>` - Delete a camera
- `list-cameras` - List all cameras

### Doors
- `add-door` - Add a new door with auto-assigned name
- `move-door <id> <deltaX> <deltaY>` - Move a door
- `update-door <id> [--prop value]` - Update door properties
- `delete-door <id>` - Delete a door
- `list-doors` - List all doors

### Partition Walls
- `add-partition` - Add a new partition wall with auto-assigned name
- `delete-partition <id>` - Delete a partition wall

### Columns
- `add-column` - Add a new column with auto-assigned name
- `move-column <id> <deltaX> <deltaY>` - Move a column
- `delete-column <id>` - Delete a column
- `list-columns` - List all columns

## Output Format

All commands return JSON:

```json
{
  "success": true,
  "action": "add-camera",
  "component": {
    "id": "donut",
    "name": "Donut",
    "number": 16,
    ...
  }
}
```

## Integration

See **`CLAUDE_USAGE.md`** for detailed integration guide with Claude Code and the ModelT warehouse builder skill.

## Dependencies

- Node.js (built-in modules only)
- ModelT warehouse builder skill (for naming lists and SVG generation)

## Working Directory

Commands should be run from the warehouse directory:
```bash
cd c:\clients\modesto\warehouses\lodge
node ../../tools/modelt-cli.js lodge add-camera ...
```

Or use absolute paths from anywhere.
