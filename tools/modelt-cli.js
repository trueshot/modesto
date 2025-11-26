#!/usr/bin/env node

/**
 * ModelT CLI Tool
 * Command-line interface for editing ModelT warehouse specifications
 *
 * Usage:
 *   node modelt-cli.js <warehouse> <command> [options]
 *
 * Examples:
 *   node modelt-cli.js lodge add-camera --slab mercury --x 200 --y 300 --elevation 12 --direction 90 --tilt 25
 *   node modelt-cli.js lodge move-door roosevelt 3 0
 *   node modelt-cli.js lodge delete-camera bagel
 */

const path = require('path');
const ModelTEditor = require('./modelt-editor');

// Parse command-line arguments
const args = process.argv.slice(2);

if (args.length < 2) {
  console.error('Usage: node modelt-cli.js <warehouse> <command> [options]');
  process.exit(1);
}

const warehouseName = args[0];
const command = args[1];
const options = parseOptions(args.slice(2));

// Resolve warehouse path
const warehousePath = path.resolve(__dirname, '..', 'warehouses', warehouseName, `${warehouseName}.modelT.json`);

/**
 * Parse command-line options into an object
 */
function parseOptions(args) {
  const opts = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith('--')) {
      const key = args[i].slice(2);
      const value = args[i + 1];

      // Handle numeric values
      if (value && !value.startsWith('--')) {
        opts[key] = isNaN(value) ? value : parseFloat(value);
        i++;
      } else {
        opts[key] = true;
      }
    } else if (!args[i].startsWith('--')) {
      // Positional arguments
      if (!opts._positional) opts._positional = [];
      opts._positional.push(isNaN(args[i]) ? args[i] : parseFloat(args[i]));
    }
  }
  return opts;
}

/**
 * Parse segment string format: "east:50,south:20" -> [{direction:'east',length:50},...]
 */
function parseSegments(segmentStr) {
  return segmentStr.split(',').map(seg => {
    const [direction, length] = seg.split(':');
    return { direction, length: parseFloat(length) };
  });
}

/**
 * Output result as JSON
 */
function output(result) {
  console.log(JSON.stringify(result, null, 2));
}

/**
 * Main command handler
 */
async function main() {
  try {
    const editor = new ModelTEditor(warehousePath);
    let result = null;

    switch (command) {
      // ==================== CAMERAS ====================
      case 'add-camera': {
        const camera = editor.addCamera(options.slab, {
          x: options.x,
          y: options.y,
          elevation: options.elevation,
          direction: options.direction,
          tilt: options.tilt,
          viewingAngle: options.viewingAngle,
          range: options.range,
          location: options.location
        });
        editor.commit();
        result = {
          success: true,
          action: 'add-camera',
          component: camera
        };
        break;
      }

      case 'move-camera': {
        const cameraId = options._positional[0];
        const deltaX = options._positional[1] || 0;
        const deltaY = options._positional[2] || 0;
        const camera = editor.moveCamera(cameraId, deltaX, deltaY);
        editor.commit();
        result = {
          success: true,
          action: 'move-camera',
          component: camera
        };
        break;
      }

      case 'update-camera': {
        const cameraId = options._positional[0];
        const updates = { ...options };
        delete updates._positional;
        const camera = editor.updateCamera(cameraId, updates);
        editor.commit();
        result = {
          success: true,
          action: 'update-camera',
          component: camera
        };
        break;
      }

      case 'delete-camera': {
        const cameraId = options._positional[0];
        const camera = editor.deleteCamera(cameraId);
        editor.commit();
        result = {
          success: true,
          action: 'delete-camera',
          component: camera
        };
        break;
      }

      // ==================== DOORS ====================
      case 'add-door': {
        const door = editor.addDoor(options.slab, {
          // Core properties
          wallId: options.wall,
          x: options.x,
          y: options.y,
          type: options.type,
          orientation: options.orientation,
          facing: options.facing,
          // Opening dimensions (new + legacy)
          openingWidth: options.openingWidth,
          openingHeight: options.openingHeight,
          bayWidth: options.bayWidth,
          doorWidth: options.doorWidth,
          width: options.width,
          // Optional core
          hardwareSide: options.hardwareSide,
          state: options.state,
          // Bay door properties
          hasDockSeal: options.hasDockSeal,
          hasDockLeveler: options.hasDockLeveler,
          hasSafetyStriping: options.hasSafetyStriping,
          dockSealWidth: options.dockSealWidth,
          dockSealHeight: options.dockSealHeight,
          levelerWidth: options.levelerWidth,
          levelerDepth: options.levelerDepth,
          // Rollup door properties
          housingHeight: options.housingHeight,
          trackWidth: options.trackWidth,
          // Personnel door properties
          frameWidth: options.frameWidth,
          swingDirection: options.swingDirection,
          hingePosition: options.hingePosition,
          // Cooler door properties
          insulation: options.insulation,
          slideDirection: options.slideDirection,
          trackPosition: options.trackPosition,
          // Interior door properties
          hasPhysicalDoor: options.hasPhysicalDoor
        });
        editor.commit();
        result = {
          success: true,
          action: 'add-door',
          component: door
        };
        break;
      }

      case 'move-door': {
        const doorId = options._positional[0];
        const deltaX = options._positional[1] || 0;
        const deltaY = options._positional[2] || 0;
        const door = editor.moveDoor(doorId, deltaX, deltaY);
        editor.commit();
        result = {
          success: true,
          action: 'move-door',
          component: door
        };
        break;
      }

      case 'update-door': {
        const doorId = options._positional[0];
        const updates = { ...options };
        delete updates._positional;
        const door = editor.updateDoor(doorId, updates);
        editor.commit();
        result = {
          success: true,
          action: 'update-door',
          component: door
        };
        break;
      }

      case 'delete-door': {
        const doorId = options._positional[0];
        const door = editor.deleteDoor(doorId);
        editor.commit();
        result = {
          success: true,
          action: 'delete-door',
          component: door
        };
        break;
      }

      // ==================== PARTITION WALLS ====================
      case 'add-partition': {
        const [startX, startY] = options.start.split(',').map(parseFloat);
        const segments = parseSegments(options.segments);
        const wall = editor.addPartitionWall(options.slab, {
          start: { x: startX, y: startY },
          segments
        });
        editor.commit();
        result = {
          success: true,
          action: 'add-partition',
          component: wall
        };
        break;
      }

      case 'delete-partition': {
        const wallId = options._positional[0];
        const wall = editor.deletePartitionWall(wallId);
        editor.commit();
        result = {
          success: true,
          action: 'delete-partition',
          component: wall
        };
        break;
      }

      // ==================== COLUMNS ====================
      case 'add-column': {
        const column = editor.addColumn(options.slab, {
          x: options.x,
          y: options.y,
          height: options.height,
          size: options.size,
          location: options.location
        });
        editor.commit();
        result = {
          success: true,
          action: 'add-column',
          component: column
        };
        break;
      }

      case 'move-column': {
        const columnId = options._positional[0];
        const deltaX = options._positional[1] || 0;
        const deltaY = options._positional[2] || 0;
        const column = editor.moveColumn(columnId, deltaX, deltaY);
        editor.commit();
        result = {
          success: true,
          action: 'move-column',
          component: column
        };
        break;
      }

      case 'delete-column': {
        const columnId = options._positional[0];
        const column = editor.deleteColumn(columnId);
        editor.commit();
        result = {
          success: true,
          action: 'delete-column',
          component: column
        };
        break;
      }

      // ==================== LIST OPERATIONS ====================
      case 'overview': {
        const overview = editor.getOverview();
        result = {
          success: true,
          action: 'overview',
          ...overview
        };
        break;
      }

      case 'list-cameras': {
        const cameras = editor.getAllCameras();
        result = {
          success: true,
          action: 'list-cameras',
          count: cameras.length,
          cameras
        };
        break;
      }

      case 'list-doors': {
        const doors = editor.getAllDoors();
        result = {
          success: true,
          action: 'list-doors',
          count: doors.length,
          doors
        };
        break;
      }

      case 'list-columns': {
        const columns = editor.getAllColumns();
        result = {
          success: true,
          action: 'list-columns',
          count: columns.length,
          columns
        };
        break;
      }

      default:
        throw new Error(`Unknown command: ${command}`);
    }

    output(result);
    process.exit(0);
  } catch (error) {
    output({
      success: false,
      error: error.message
    });
    process.exit(1);
  }
}

// Run the CLI
main();
