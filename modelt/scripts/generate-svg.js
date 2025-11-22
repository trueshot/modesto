#!/usr/bin/env node
/**
 * ModelT SVG Generator
 *
 * Takes a JSON warehouse specification and generates a ModelT-compliant SVG file.
 *
 * Usage:
 *   node generate-svg.js input.json output.svg
 *   cat warehouse.json | node generate-svg.js > output.svg
 */

const fs = require('fs');
const path = require('path');

// ============================================================================
// COORDINATE CONVERSION FUNCTIONS
// ============================================================================

/**
 * Convert turtle graphics segments to absolute corner coordinates
 * @param {Object} start - Starting position {x, y}
 * @param {Array} segments - Array of {direction, length}
 * @returns {Array} Array of corner coordinates {x, y}
 */
function turtleToCorners(start, segments) {
  const corners = [{ x: start.x, y: start.y }];
  let currentX = start.x;
  let currentY = start.y;

  for (const segment of segments) {
    const { direction, length } = segment;

    switch (direction) {
      case 'north':
        currentY -= length;
        break;
      case 'south':
        currentY += length;
        break;
      case 'east':
        currentX += length;
        break;
      case 'west':
        currentX -= length;
        break;
      default:
        throw new Error(`Invalid direction: ${direction}`);
    }

    corners.push({ x: currentX, y: currentY });
  }

  // Remove last corner if it closes back to start (within floating point tolerance)
  const lastCorner = corners[corners.length - 1];
  if (Math.abs(lastCorner.x - start.x) < 0.001 && Math.abs(lastCorner.y - start.y) < 0.001) {
    corners.pop();
  }

  return corners;
}

/**
 * Convert absolute corner coordinates to turtle graphics segments
 * @param {Array} corners - Array of corner coordinates {x, y}
 * @returns {Array} Array of {direction, length} segments
 */
function cornersToTurtle(corners) {
  const segments = [];

  for (let i = 0; i < corners.length; i++) {
    const current = corners[i];
    const next = corners[(i + 1) % corners.length];

    const dx = next.x - current.x;
    const dy = next.y - current.y;

    let direction;
    let length;

    if (Math.abs(dx) < 0.001) {
      // Vertical movement
      if (dy > 0) {
        direction = 'south';
        length = dy;
      } else {
        direction = 'north';
        length = -dy;
      }
    } else if (Math.abs(dy) < 0.001) {
      // Horizontal movement
      if (dx > 0) {
        direction = 'east';
        length = dx;
      } else {
        direction = 'west';
        length = -dx;
      }
    } else {
      throw new Error(`Diagonal movement not supported: (${current.x},${current.y}) → (${next.x},${next.y})`);
    }

    segments.push({ direction, length });
  }

  return segments;
}

/**
 * Normalize slab and perimeter walls: ensure both corners and segments exist
 * SOURCE OF TRUTH: segments (if present) are used to generate corners
 * BACKWARD COMPATIBILITY: if only corners present, generate segments
 * @param {Object} spec - Warehouse specification (v1 or v2)
 */
function normalizeClosedShapes(spec) {
  // Detect v2 format (slabs array)
  if (spec.slabs && Array.isArray(spec.slabs)) {
    // V2: Process each slab
    spec.slabs.forEach(slab => {
      normalizeSlabShape(slab);
      // Process walls within each slab
      if (slab.walls && Array.isArray(slab.walls)) {
        slab.walls.forEach(wall => {
          if (wall.type === 'slabPerimeter' || wall.type === 'perimeter') {
            normalizeClosedWall(wall, `${slab.id}/${wall.id}`);
          }
          // Partition walls don't need normalization (already turtle format)
        });
      }
    });
    return;
  }

  // V1 format: old flat structure
  if (spec.slab) {
    normalizeSlabShape(spec.slab);
  }

  if (spec.walls) {
    normalizeClosedWall(spec.walls, 'walls');
  }
}

/**
 * Normalize a single slab shape
 */
function normalizeSlabShape(slab) {
  if (slab.segments && slab.segments.length > 0) {
    // Segments present - this is source of truth
    let start;
    if (slab.start) {
      start = slab.start;
    } else if (slab.corners && slab.corners.length > 0) {
      start = slab.corners[0];
    } else {
      throw new Error(`Slab ${slab.id || ''} has segments but no start position and no corners to infer start from`);
    }

    slab.corners = turtleToCorners(start, slab.segments);
    slab.start = start;
    console.error(`✓ Slab ${slab.id || ''}: Generated corners from segments`);
  } else if (slab.corners && slab.corners.length > 0) {
    slab.segments = cornersToTurtle(slab.corners);
    slab.start = slab.corners[0];
    console.error(`✓ Slab ${slab.id || ''}: Generated segments from corners`);
  } else {
    throw new Error(`Slab ${slab.id || ''} must have either corners or segments`);
  }
}

/**
 * Normalize a closed wall (slabPerimeter or perimeter type)
 */
function normalizeClosedWall(wall, label) {
  if (wall.segments && wall.segments.length > 0) {
    let start;
    if (wall.start) {
      start = wall.start;
    } else if (wall.corners && wall.corners.length > 0) {
      start = wall.corners[0];
    } else {
      throw new Error(`Wall ${label} has segments but no start position and no corners to infer start from`);
    }

    wall.corners = turtleToCorners(start, wall.segments);
    wall.start = start;
    console.error(`✓ Wall ${label}: Generated corners from segments`);
  } else if (wall.corners && wall.corners.length > 0) {
    wall.segments = cornersToTurtle(wall.corners);
    wall.start = wall.corners[0];
    console.error(`✓ Wall ${label}: Generated segments from corners`);
  } else {
    throw new Error(`Wall ${label} must have either corners or segments`);
  }
}

// ============================================================================
// SVG GENERATORS
// ============================================================================

// Corner SVG generators
const cornerTemplates = {
  NE: (x, y, id = null) => `
  <g id="corner_NE_${id || `${x}_${y}`}" transform="translate(${x},${y})">
    <rect x="0.8" y="0" width="0.2" height="1"/>
    <rect x="0" y="0" width="1" height="0.2"/>
    <rect x="0" y="0.8" width="0.2" height="0.2"/>
  </g>`,

  NW: (x, y, id = null) => `
  <g id="corner_NW_${id || `${x}_${y}`}" transform="translate(${x},${y})">
    <rect x="0" y="0" width="0.2" height="1"/>
    <rect x="0" y="0" width="1" height="0.2"/>
    <rect x="0.8" y="0.8" width="0.2" height="0.2"/>
  </g>`,

  SE: (x, y, id = null) => `
  <g id="corner_SE_${id || `${x}_${y}`}" transform="translate(${x},${y})">
    <rect x="0.8" y="0" width="0.2" height="1"/>
    <rect x="0" y="0.8" width="1" height="0.2"/>
    <rect x="0" y="0" width="0.2" height="0.2"/>
  </g>`,

  SW: (x, y, id = null) => `
  <g id="corner_SW_${id || `${x}_${y}`}" transform="translate(${x},${y})">
    <rect x="0" y="0" width="0.2" height="1"/>
    <rect x="0" y="0.8" width="1" height="0.2"/>
    <rect x="0.8" y="0" width="0.2" height="0.2"/>
  </g>`
};

// Generate horizontal endcap
// Generate directional endcaps (bracket shapes)
function generateNorthEndcap(id, x, y) {
  return `
  <g id="endcap_north_${id}" transform="translate(${x},${y})">
    <rect width="1" height="0.2" x="0" y="0"/>
    <rect width="0.2" height="0.5" x="0" y="0"/>
    <rect width="0.2" height="0.5" x="0.8" y="0"/>
  </g>`;
}

function generateSouthEndcap(id, x, y) {
  return `
  <g id="endcap_south_${id}" transform="translate(${x},${y})">
    <rect width="0.2" height="0.5" x="0" y="0"/>
    <rect width="0.2" height="0.5" x="0.8" y="0"/>
    <rect width="1" height="0.2" x="0" y="0.3"/>
  </g>`;
}

function generateEastEndcap(id, x, y) {
  return `
  <g id="endcap_east_${id}" transform="translate(${x},${y})">
    <rect width="0.5" height="0.2" x="0" y="0"/>
    <rect width="0.5" height="0.2" x="0" y="0.8"/>
    <rect width="0.2" height="1" x="0.3" y="0"/>
  </g>`;
}

function generateWestEndcap(id, x, y) {
  return `
  <g id="endcap_west_${id}" transform="translate(${x},${y})">
    <rect width="0.5" height="0.2" x="0" y="0"/>
    <rect width="0.5" height="0.2" x="0" y="0.8"/>
    <rect width="0.2" height="1" x="0" y="0"/>
  </g>`;
}

// Generate horizontal wall segment
function generateHorizontalWall(id, startX, y, length) {
  const idStr = typeof id === 'string' ? id : `wall_h_${id}`;
  return `
  <g id="${idStr}" transform="translate(${startX},${y})">
    <rect width="${length}" height="0.2" x="0" y="0"/>
    <rect width="${length}" height="0.2" x="0" y="0.8"/>
  </g>`;
}

// Generate vertical wall segment
function generateVerticalWall(id, x, startY, length) {
  const idStr = typeof id === 'string' ? id : `wall_v_${id}`;
  return `
  <g id="${idStr}" transform="translate(${x},${startY})">
    <rect width="0.2" height="${length}" x="0" y="0"/>
    <rect width="0.2" height="${length}" x="0.8" y="0"/>
  </g>`;
}

// Infer corner type (NW, NE, SE, SW) from wall directions
// Rule: First corner is always NW (topmost, leftmost)
function inferCornerType(corners, index, isInterior) {
  // First corner is always NW
  if (index === 0) {
    return 'NW';
  }

  const prev = corners[(index - 1 + corners.length) % corners.length];
  const current = corners[index];
  const next = corners[(index + 1) % corners.length];

  // Determine incoming direction (from prev to current)
  const incomingDir =
    current.x > prev.x ? 'East' :
    current.x < prev.x ? 'West' :
    current.y > prev.y ? 'South' : 'North';

  // Determine outgoing direction (from current to next)
  const outgoingDir =
    next.x > current.x ? 'East' :
    next.x < current.x ? 'West' :
    next.y > current.y ? 'South' : 'North';

  // Map incoming/outgoing direction pairs to corner types
  // Based on clockwise traversal with exterior corners
  const key = `${incomingDir}-${outgoingDir}`;
  const exteriorCornerMap = {
    'East-South': 'NE',
    'South-West': 'SE',
    'West-North': 'SW',
    'North-East': 'NW',
  };

  const interiorCornerMap = {
    'East-North': 'SE',
    'North-West': 'NE',
    'West-South': 'NW',
    'South-East': 'SW',
  };

  if (isInterior) {
    return interiorCornerMap[key] || 'NW';
  } else {
    return exteriorCornerMap[key] || 'NW';
  }
}

// Determine if a corner is interior or exterior
// Uses cross product of vectors to detect turn direction
function isInteriorCorner(corners, index) {
  const prev = corners[(index - 1 + corners.length) % corners.length];
  const current = corners[index];
  const next = corners[(index + 1) % corners.length];

  // Vectors: prev->current and current->next
  const v1x = current.x - prev.x;
  const v1y = current.y - prev.y;
  const v2x = next.x - current.x;
  const v2y = next.y - current.y;

  // Cross product (z-component)
  const cross = v1x * v2y - v1y * v2x;

  // For CLOCKWISE polygon traversal (as used in ModelT):
  // cross < 0: interior corner (inward turn, notch into the shape)
  // cross > 0: exterior corner (outward turn, outer edge sticking out)
  return cross < 0;
}

// Get interior connection point where walls meet for a corner
function getInteriorPoint(x, y, type, isInterior) {
  const offsets = {
    exterior: {
      NW: { dx: 1, dy: 1 },
      NE: { dx: -1, dy: 1 },
      SE: { dx: -1, dy: -1 },
      SW: { dx: 1, dy: -1 }
    },
    interior: {
      NW: { dx: -1, dy: -1 },
      NE: { dx: 1, dy: -1 },
      SE: { dx: 1, dy: 1 },
      SW: { dx: -1, dy: 1 }
    }
  };

  const offset = offsets[isInterior ? 'interior' : 'exterior'][type];
  return {
    x: x + offset.dx,
    y: y + offset.dy
  };
}

// Get corner piece position (where to place translate)
function getCornerPosition(x, y, type, isInterior) {
  const offsets = {
    exterior: {
      NW: { dx: 0, dy: 0 },    // Perfect
      NE: { dx: -1, dy: 0 },
      SE: { dx: -1, dy: -1 },
      SW: { dx: 0, dy: -1 }
    },
    interior: {
      NW: { dx: -1, dy: -1 },
      NE: { dx: 0, dy: -1 },
      SE: { dx: 0, dy: 0 },    // Perfect
      SW: { dx: -1, dy: 0 }
    }
  };

  const offset = offsets[isInterior ? 'interior' : 'exterior'][type];
  return {
    x: x + offset.dx,
    y: y + offset.dy
  };
}

// Generate walls from ordered corner array
function generateWalls(wallStructure) {
  let wallsSVG = '';

  // Support both old format (array of corners) and new format (object with id and corners)
  const corners = Array.isArray(wallStructure) ? wallStructure : wallStructure.corners;
  const wallName = wallStructure.id || 'perimeter';
  let segmentIndex = 0;

  // Calculate interior points for all corners (where walls connect)
  const cornerData = corners.map((corner, index) => {
    const { x, y } = corner;
    const isInterior = isInteriorCorner(corners, index);
    const type = inferCornerType(corners, index, isInterior);  // Infer type from geometry
    const position = getCornerPosition(x, y, type, isInterior);
    const interiorPoint = getInteriorPoint(x, y, type, isInterior);

    return {
      slabX: x,
      slabY: y,
      type,
      isInterior,
      pieceX: position.x,
      pieceY: position.y,
      interiorX: interiorPoint.x,
      interiorY: interiorPoint.y
    };
  });

  // Generate corner pieces at calculated positions
  cornerData.forEach((corner, index) => {
    // Place corner piece with named id
    if (cornerTemplates[corner.type]) {
      wallsSVG += cornerTemplates[corner.type](corner.pieceX, corner.pieceY, `${wallName}_corner${index}`);
    }
  });

  // Generate walls between consecutive corners
  // Simple logic: connect two 1x1 corner rectangles
  for (let i = 0; i < cornerData.length; i++) {
    const current = cornerData[i];
    const next = cornerData[(i + 1) % cornerData.length];

    // Corner pieces are positioned at (pieceX, pieceY) and are 1x1 feet
    const c1 = { x: current.pieceX, y: current.pieceY };
    const c2 = { x: next.pieceX, y: next.pieceY };

    if (c1.y === c2.y) {
      // Horizontal wall - same Y
      const leftX = Math.min(c1.x, c2.x);
      const rightX = Math.max(c1.x, c2.x);
      const wallStart = leftX + 1;  // Right edge of left corner
      const wallEnd = rightX;        // Left edge of right corner
      const wallLength = wallEnd - wallStart;

      if (wallLength > 0) {
        wallsSVG += generateHorizontalWall(`wall_h_${wallName}_seg${segmentIndex++}`, wallStart, c1.y, wallLength);
      }
    } else if (c1.x === c2.x) {
      // Vertical wall - same X
      const topY = Math.min(c1.y, c2.y);
      const bottomY = Math.max(c1.y, c2.y);
      const wallStart = topY + 1;   // Bottom edge of top corner
      const wallEnd = bottomY;       // Top edge of bottom corner
      const wallLength = wallEnd - wallStart;

      if (wallLength > 0) {
        wallsSVG += generateVerticalWall(`wall_v_${wallName}_seg${segmentIndex++}`, c1.x, wallStart, wallLength);
      }
    }
  }

  return wallsSVG;
}

// Generate slab path from corners
function generateSlab(corners) {
  if (corners.length === 0) return '';

  const pathData = corners.map((corner, i) => {
    const command = i === 0 ? 'M' : 'L';
    return `${command} ${corner.x},${corner.y}`;
  }).join(' ') + ' Z';

  return `<path id="slab" d="${pathData}" style="fill:#8888aa;fill-opacity:0.8;stroke:#000000;stroke-width:0.05"/>`;
}

// Generate door overlay
function generateDoor(door, index) {
  const { id, x, y, width, bayWidth, doorWidth, type = 'opening', orientation = 'horizontal', facing = 'north' } = door;

  // Convert doorWidth from inches to feet if provided
  const doorWidthFeet = doorWidth ? doorWidth / 12 : null;

  // If bayWidth and doorWidth are specified, calculate side walls
  const useBayAndDoor = bayWidth && doorWidthFeet;
  const actualBayWidth = useBayAndDoor ? bayWidth : width;
  const actualDoorWidth = useBayAndDoor ? doorWidthFeet : (width - 1); // Old: door was width - 1 (0.5' endcaps on each side)
  const sideWallWidth = useBayAndDoor ? (bayWidth - doorWidthFeet) / 2 : 0.5;

  const halfBayWidth = actualBayWidth / 2;
  const halfDoorWidth = actualDoorWidth / 2;
  const doorId = id || `${type}_${index}`;

  // Determine opening color based on door type
  // Interior doors: white to show clear opening through partition walls
  // Bay/exterior doors: slab color to blend with floor
  const openingFill = (type === 'interior') ? '#ffffff' : '#8888aa';

  let doorSVG = `
  <g id="door_${doorId}" transform="translate(${x},${y})">`;

  if (orientation === 'horizontal') {
    // Horizontal door (on N/S wall)
    // Left side wall
    doorSVG += `
    <rect x="${-halfBayWidth}" y="0" width="${sideWallWidth}" height="1" fill="#00449e"/>`;

    // Center opening: white for interior, slab color for exterior
    doorSVG += `
    <rect x="${-halfDoorWidth}" y="0" width="${actualDoorWidth}" height="1" fill="${openingFill}"/>`;

    // Right side wall
    doorSVG += `
    <rect x="${halfDoorWidth}" y="0" width="${sideWallWidth}" height="1" fill="#00449e"/>`;

    // Type-specific decorations on the opening
    if (type === 'bay') {
      // Sectional lines for bay door
      doorSVG += `
    <line x1="${-halfDoorWidth}" y1="0.33" x2="${halfDoorWidth}" y2="0.33" stroke="#666" stroke-width="0.05"/>
    <line x1="${-halfDoorWidth}" y1="0.67" x2="${halfDoorWidth}" y2="0.67" stroke="#666" stroke-width="0.05"/>`;
    } else if (type === 'personnel') {
      // Door swing arc
      if (facing === 'north') {
        doorSVG += `
    <path d="M ${-halfDoorWidth},-0.5 A ${actualDoorWidth},${actualDoorWidth} 0 0,1 ${halfDoorWidth},-0.5" stroke="#999" stroke-width="0.05" fill="none" stroke-dasharray="0.2,0.2"/>`;
      } else {
        doorSVG += `
    <path d="M ${-halfDoorWidth},1.5 A ${actualDoorWidth},${actualDoorWidth} 0 0,0 ${halfDoorWidth},1.5" stroke="#999" stroke-width="0.05" fill="none" stroke-dasharray="0.2,0.2"/>`;
      }
    } else if (type === 'cooler') {
      // Light blue tint for cooler
      doorSVG += `
    <rect x="${-halfDoorWidth}" y="0" width="${actualDoorWidth}" height="1" fill="#aaccff" fill-opacity="0.3"/>`;
    } else if (type === 'rollup') {
      // Horizontal ribbing for rollup door
      doorSVG += `
    <line x1="${-halfDoorWidth}" y1="0.2" x2="${halfDoorWidth}" y2="0.2" stroke="#666" stroke-width="0.03"/>
    <line x1="${-halfDoorWidth}" y1="0.4" x2="${halfDoorWidth}" y2="0.4" stroke="#666" stroke-width="0.03"/>
    <line x1="${-halfDoorWidth}" y1="0.6" x2="${halfDoorWidth}" y2="0.6" stroke="#666" stroke-width="0.03"/>
    <line x1="${-halfDoorWidth}" y1="0.8" x2="${halfDoorWidth}" y2="0.8" stroke="#666" stroke-width="0.03"/>`;
    }

  } else {
    // Vertical door (on E/W wall)
    // Top side wall
    doorSVG += `
    <rect x="0" y="${-halfBayWidth}" width="1" height="${sideWallWidth}" fill="#00449e"/>`;

    // Center opening: white for interior, slab color for exterior
    doorSVG += `
    <rect x="0" y="${-halfDoorWidth}" width="1" height="${actualDoorWidth}" fill="${openingFill}"/>`;

    // Bottom side wall
    doorSVG += `
    <rect x="0" y="${halfDoorWidth}" width="1" height="${sideWallWidth}" fill="#00449e"/>`;

    // Type-specific decorations
    if (type === 'bay') {
      doorSVG += `
    <line x1="0.33" y1="${-halfDoorWidth}" x2="0.33" y2="${halfDoorWidth}" stroke="#666" stroke-width="0.05"/>
    <line x1="0.67" y1="${-halfDoorWidth}" x2="0.67" y2="${halfDoorWidth}" stroke="#666" stroke-width="0.05"/>`;
    } else if (type === 'personnel') {
      if (facing === 'east') {
        doorSVG += `
    <path d="M 1.5,${-halfDoorWidth} A ${actualDoorWidth},${actualDoorWidth} 0 0,1 1.5,${halfDoorWidth}" stroke="#999" stroke-width="0.05" fill="none" stroke-dasharray="0.2,0.2"/>`;
      } else {
        doorSVG += `
    <path d="M -0.5,${-halfDoorWidth} A ${actualDoorWidth},${actualDoorWidth} 0 0,0 -0.5,${halfDoorWidth}" stroke="#999" stroke-width="0.05" fill="none" stroke-dasharray="0.2,0.2"/>`;
      }
    } else if (type === 'cooler') {
      doorSVG += `
    <rect x="0" y="${-halfDoorWidth}" width="1" height="${actualDoorWidth}" fill="#aaccff" fill-opacity="0.3"/>`;
    } else if (type === 'rollup') {
      // Horizontal ribbing for rollup door (rotated for vertical)
      doorSVG += `
    <line x1="0.2" y1="${-halfDoorWidth}" x2="0.2" y2="${halfDoorWidth}" stroke="#666" stroke-width="0.03"/>
    <line x1="0.4" y1="${-halfDoorWidth}" x2="0.4" y2="${halfDoorWidth}" stroke="#666" stroke-width="0.03"/>
    <line x1="0.6" y1="${-halfDoorWidth}" x2="0.6" y2="${halfDoorWidth}" stroke="#666" stroke-width="0.03"/>
    <line x1="0.8" y1="${-halfDoorWidth}" x2="0.8" y2="${halfDoorWidth}" stroke="#666" stroke-width="0.03"/>`;
    }
  }

  doorSVG += `
  </g>`;

  return doorSVG;
}

// Generate all doors
function generateDoors(doors) {
  if (!doors || doors.length === 0) return '';

  return doors.map((door, index) => generateDoor(door, index)).join('');
}

// Generate camera overlay
function generateCamera(camera) {
  const {
    id,
    number,
    x,
    y,
    elevation = 12,  // height above floor in feet (default 12ft ceiling mount)
    direction = 0,  // horizontal pan: 0=north, 90=east, 180=south, 270=west
    tilt = 30,  // degrees down from horizontal (0=level, 90=straight down)
    viewingAngle = 60,  // horizontal cone angle in degrees
    range = 50,  // viewing distance in feet
    model = '',
    ipAddress = '',
    location = ''
  } = camera;

  const cameraId = id || `camera_${number}`;

  // Calculate effective ground range based on elevation and tilt
  // This affects how far the camera actually sees on the floor
  const tiltRad = tilt * Math.PI / 180;
  const effectiveRange = elevation > 0 && tilt > 0
    ? Math.min(range, elevation / Math.tan(tiltRad))
    : range;

  // Convert direction to radians (0° is north, which is -90° in standard math)
  // In SVG, y-axis increases downward, so we need to adjust
  const dirRad = (direction - 90) * Math.PI / 180;

  // Calculate center line endpoint (horizontal projection on floor)
  const centerX = x + effectiveRange * Math.cos(dirRad);
  const centerY = y + effectiveRange * Math.sin(dirRad);

  // Calculate cone edges
  const halfAngle = viewingAngle / 2;
  const leftAngle = (direction - 90 - halfAngle) * Math.PI / 180;
  const rightAngle = (direction - 90 + halfAngle) * Math.PI / 180;

  const leftX = x + effectiveRange * Math.cos(leftAngle);
  const leftY = y + effectiveRange * Math.sin(leftAngle);
  const rightX = x + effectiveRange * Math.cos(rightAngle);
  const rightY = y + effectiveRange * Math.sin(rightAngle);

  // Determine if we need large arc flag (for angles > 180°)
  const largeArcFlag = viewingAngle > 180 ? 1 : 0;

  let cameraSVG = `
  <g id="${cameraId}" class="camera">
    <!-- Camera position circle -->
    <circle cx="${x}" cy="${y}" r="0.5" fill="#ff0000" stroke="#000" stroke-width="0.1"/>

    <!-- Camera number label -->
    <text x="${x}" y="${y - 1.5}" font-size="1.5" text-anchor="middle" fill="#ff0000" font-weight="bold">${number || ''}</text>

    <!-- Elevation indicator -->
    <text x="${x}" y="${y + 2}" font-size="0.8" text-anchor="middle" fill="#666">${elevation}'</text>

    <!-- Center direction line (dashed) - shows horizontal projection -->
    <line x1="${x}" y1="${y}" x2="${centerX}" y2="${centerY}" stroke="#ff0000" stroke-width="0.1" stroke-dasharray="0.5,0.5" opacity="0.7"/>

    <!-- Viewing cone (horizontal projection on floor) -->
    <path d="M ${x},${y} L ${leftX},${leftY} A ${effectiveRange},${effectiveRange} 0 ${largeArcFlag},1 ${rightX},${rightY} Z"
          fill="#ff0000"
          fill-opacity="0.1"
          stroke="#ff0000"
          stroke-width="0.1"
          stroke-dasharray="0.3,0.3"/>
  </g>`;

  return cameraSVG;
}

// Generate all cameras
function generateCameras(cameras) {
  if (!cameras || cameras.length === 0) return '';

  return cameras.map((camera) => generateCamera(camera)).join('');
}

// Generate structural support column (H-shaped)
function generateColumn(column) {
  const {
    id,
    name,
    x,
    y,
    height = 15,  // column height in feet (for 3D)
    size = 1,  // column footprint size in feet
    type = 'H-beam'  // column type
  } = column;

  const columnId = id || `column_${name}`;
  const halfSize = size / 2;

  // H-beam shape: two vertical bars with horizontal crossbar
  const barWidth = size * 0.2;  // 20% of size for bar thickness
  const leftBar = -halfSize;
  const rightBar = halfSize - barWidth;
  const crossbarY = 0;

  let columnSVG = `
  <g id="${columnId}" class="column">
    <!-- H-beam structural column -->
    <g transform="translate(${x},${y})">
      <!-- Left vertical bar -->
      <rect x="${leftBar}" y="${-halfSize}" width="${barWidth}" height="${size}" fill="#4a4a4a" stroke="#000" stroke-width="0.05"/>

      <!-- Right vertical bar -->
      <rect x="${rightBar}" y="${-halfSize}" width="${barWidth}" height="${size}" fill="#4a4a4a" stroke="#000" stroke-width="0.05"/>

      <!-- Horizontal crossbar -->
      <rect x="${leftBar}" y="${crossbarY - barWidth/2}" width="${size}" height="${barWidth}" fill="#4a4a4a" stroke="#000" stroke-width="0.05"/>

      <!-- Column label -->
      <text x="0" y="${halfSize + 1.5}" font-size="1" text-anchor="middle" fill="#333" font-weight="bold">${name || ''}</text>
    </g>
  </g>`;

  return columnSVG;
}

// Generate all columns
function generateColumns(columns) {
  if (!columns || columns.length === 0) return '';

  return columns.map((column) => generateColumn(column)).join('');
}

// Convert turtle graphics segments to points
function convertTurtleToPoints(partitionWall) {
  const { start, segments } = partitionWall;
  const points = [{ x: start.x, y: start.y, direction: segments[0]?.direction }];

  let currentX = start.x;
  let currentY = start.y;

  for (let i = 0; i < segments.length; i++) {
    const { direction, length } = segments[i];

    switch (direction) {
      case 'north':
        currentY -= length;
        break;
      case 'south':
        currentY += length;
        break;
      case 'east':
        currentX += length;
        break;
      case 'west':
        currentX -= length;
        break;
    }

    // Add direction for next segment (if any)
    const nextDirection = segments[i + 1]?.direction || direction;
    points.push({ x: currentX, y: currentY, direction, nextDirection });
  }

  return points;
}

// Determine corner type from direction change
function getCornerTypeFromDirections(fromDir, toDir) {
  const key = `${fromDir}-${toDir}`;
  const cornerMap = {
    'east-south': 'NE',
    'south-west': 'SE',
    'west-north': 'SW',
    'north-east': 'NW',
    'east-north': 'SE',
    'north-west': 'NE',
    'west-south': 'NW',
    'south-east': 'SW'
  };
  return cornerMap[key] || 'NW';
}

// Generate partition walls (not closed loops)
function generatePartitionWalls(partitionWalls) {
  if (!partitionWalls || partitionWalls.length === 0) return '';

  let wallsSVG = '';

  for (const partition of partitionWalls) {
    const points = convertTurtleToPoints(partition);
    const wallName = partition.id || 'unnamed';
    let segmentIndex = 0;

    // Generate components for each point
    for (let i = 0; i < points.length; i++) {
      const point = points[i];

      if (i === 0) {
        // Start point: endcap faces OPPOSITE to first segment direction
        const dir = partition.segments[0].direction;
        const oppositeDir = { north: 'south', south: 'north', east: 'west', west: 'east' }[dir];

        // South and East endcaps need offset at start point
        if (oppositeDir === 'north') wallsSVG += generateNorthEndcap(`${wallName}_start`, point.x, point.y);
        else if (oppositeDir === 'south') wallsSVG += generateSouthEndcap(`${wallName}_start`, point.x, point.y - 0.5);
        else if (oppositeDir === 'east') wallsSVG += generateEastEndcap(`${wallName}_start`, point.x - 0.5, point.y);
        else if (oppositeDir === 'west') wallsSVG += generateWestEndcap(`${wallName}_start`, point.x, point.y);
      } else if (i === points.length - 1) {
        // End point: endcap faces SAME as last segment direction
        const dir = partition.segments[partition.segments.length - 1].direction;
        const isMultiSegment = partition.segments.length > 1;
        const prevDir = isMultiSegment ? partition.segments[partition.segments.length - 2].direction : null;

        let endcapX = point.x;
        let endcapY = point.y;

        // Adjust position based on previous segment direction
        if (isMultiSegment) {
          if (dir === 'north' && prevDir === 'east') {
            endcapY += 1;
            endcapX -= 1;
          } else if (dir === 'north') {
            endcapY += 1;
          } else if (dir === 'south' && prevDir === 'east') {
            endcapX -= 1;
          } else if (dir === 'west' && prevDir === 'north') {
            endcapX += 1;
          } else if (dir === 'east' && prevDir === 'south') {
            endcapY -= 1;
          } else if (dir === 'west' && prevDir === 'south') {
            endcapY -= 1;
            endcapX += 1;
          }
        }

        if (dir === 'north') wallsSVG += generateNorthEndcap(`${wallName}_end`, endcapX, endcapY);
        else if (dir === 'south') wallsSVG += generateSouthEndcap(`${wallName}_end`, endcapX, endcapY - 0.5);
        else if (dir === 'east') wallsSVG += generateEastEndcap(`${wallName}_end`, endcapX - 0.5, endcapY);
        else if (dir === 'west') wallsSVG += generateWestEndcap(`${wallName}_end`, endcapX, endcapY);
      } else {
        // Middle point: corner
        const prevDir = partition.segments[i - 1].direction;
        const nextDir = partition.segments[i].direction;
        const cornerType = getCornerTypeFromDirections(prevDir, nextDir);

        // Adjust corner position so it fits within the segment length
        let cornerX = point.x;
        let cornerY = point.y;

        if (prevDir === 'east' || prevDir === 'west') {
          cornerX = prevDir === 'east' ? point.x - 1 : point.x;
        }
        if (prevDir === 'north' || prevDir === 'south') {
          cornerY = prevDir === 'south' ? point.y - 1 : point.y;
        }

        wallsSVG += cornerTemplates[cornerType](cornerX, cornerY, `${wallName}_corner${i - 1}`);
      }

      // Generate wall segment to next point
      if (i < points.length - 1) {
        const current = point;
        const next = points[i + 1];
        const isFirstSegment = (i === 0);
        const isLastSegment = (i === points.length - 2);
        const isSingleSegment = isFirstSegment && isLastSegment;

        // Adjust current position if it's at a corner (middle point)
        let currentX = current.x;
        let currentY = current.y;

        if (i > 0) {
          // This is not the first segment, so there's a corner at current position
          const prevDir = partition.segments[i - 1].direction;
          if (prevDir === 'east') currentX = current.x - 1;
          else if (prevDir === 'south') currentY = current.y - 1;
        }

        if (current.y === next.y) {
          // Horizontal wall
          const leftX = Math.min(currentX, next.x);
          const rightX = Math.max(currentX, next.x);
          const goingEast = next.x > currentX;

          let wallStart, wallEnd;

          if (isSingleSegment) {
            // Single segment: endcaps take 0.5' at both ends
            wallStart = leftX + 0.5;
            wallEnd = rightX - 0.5;
          } else if (isFirstSegment) {
            // First segment: endcap takes 0.5' at start, corner takes 1' at end
            wallStart = goingEast ? leftX + 0.5 : leftX + 1;
            wallEnd = goingEast ? rightX - 1 : rightX - 0.5;
          } else if (isLastSegment) {
            // Last segment: corner at start (connects), endcap takes 0.5' at end
            wallStart = goingEast ? leftX + 1 : leftX + 1.5;
            wallEnd = goingEast ? rightX - 0.5 : rightX;
          } else {
            // Middle segment: corners at both ends (connect)
            wallStart = leftX + 1;
            wallEnd = rightX;
          }

          const wallLength = wallEnd - wallStart;

          if (wallLength > 0) {
            wallsSVG += generateHorizontalWall(`wall_h_${wallName}_seg${segmentIndex++}`, wallStart, currentY, wallLength);
          }
        } else if (current.x === next.x) {
          // Vertical wall
          const topY = Math.min(currentY, next.y);
          const bottomY = Math.max(currentY, next.y);
          const goingSouth = next.y > currentY;

          let wallStart, wallEnd;

          if (isSingleSegment) {
            // Single segment: endcaps take 0.5' at both ends
            wallStart = topY + 0.5;
            wallEnd = bottomY - 0.5;
          } else if (isFirstSegment) {
            // First segment: endcap takes 0.5' at start, corner takes 1' at end
            wallStart = goingSouth ? topY + 0.5 : topY + 1;
            wallEnd = goingSouth ? bottomY - 1 : bottomY - 0.5;
          } else if (isLastSegment) {
            // Last segment: corner at start (connects), endcap takes 0.5' at end
            wallStart = goingSouth ? topY + 1 : topY + 1.5;
            wallEnd = goingSouth ? bottomY - 0.5 : bottomY;
          } else {
            // Middle segment: corners at both ends (connect)
            wallStart = topY + 1;
            wallEnd = bottomY;
          }

          const wallLength = wallEnd - wallStart;

          if (wallLength > 0) {
            wallsSVG += generateVerticalWall(`wall_v_${wallName}_seg${segmentIndex++}`, currentX, wallStart, wallLength);
          }
        }
      }
    }

    // Add label if provided
    if (partition.label) {
      const startPoint = partition.start;
      wallsSVG += `
  <text x="${startPoint.x}" y="${startPoint.y + 2}" font-family="Arial" font-size="1" fill="#000000" text-anchor="middle">${partition.label}</text>`;
    }
  }

  return wallsSVG;
}

// Generate complete SVG
function generateModelTSVG(spec) {
  // Detect format: v2 (slabs array) or v1 (flat slab/walls)
  const isV2 = spec.slabs && Array.isArray(spec.slabs);

  if (!isV2) {
    // V1 format - convert to V2 internally for processing
    return generateModelTSVG_V1(spec);
  }

  // V2 format - multi-slab facility
  const { name, location, property, slabs } = spec;

  // Calculate viewBox from property boundary
  let minX, minY, viewBoxWidth, viewBoxHeight;

  if (property && property.boundary) {
    minX = property.boundary.x;
    minY = property.boundary.y;
    viewBoxWidth = property.boundary.width;
    viewBoxHeight = property.boundary.height;
  } else {
    // Calculate from all slabs
    const allCorners = slabs.flatMap(slab => slab.corners || []);
    const allX = allCorners.map(c => c.x);
    const allY = allCorners.map(c => c.y);
    minX = Math.min(...allX);
    minY = Math.min(...allY);
    const maxX = Math.max(...allX);
    const maxY = Math.max(...allY);
    viewBoxWidth = maxX - minX;
    viewBoxHeight = maxY - minY;
  }

  // Calculate width/height attributes with 3.78x scaling
  const width = (viewBoxWidth * 3.78).toFixed(1);
  const height = (viewBoxHeight * 3.78).toFixed(1);

  // Generate SVG for each slab
  const slabsSVG = slabs.map(slab => generateSlabSVG(slab)).join('\n');

  // Embed the source JSON
  const embeddedJSON = JSON.stringify(spec, null, 2);

  return `<svg width="${width}" height="${height}" viewBox="${minX} ${minY} ${viewBoxWidth} ${viewBoxHeight}" version="1.1" id="facility-svg" xmlns="http://www.w3.org/2000/svg">
  <g id="layer1">
    <!-- Property boundary -->
    <rect id="land" width="${viewBoxWidth}" height="${viewBoxHeight}" x="${minX}" y="${minY}" style="fill:#e0e0e0;fill-opacity:0.3;stroke:none"/>

    ${slabsSVG}
  </g>

  <!-- Embedded ModelT JSON source data (non-visual, machine-readable) -->
  <script type="application/json" id="modelt-schema">
${embeddedJSON}
  </script>
</svg>`;
}

/**
 * Generate SVG for a single slab (v2 format)
 */
function generateSlabSVG(slab) {
  const { id, name, corners, walls = [], columns = [], doors = [], cameras = [] } = slab;

  // Generate slab footprint
  const slabSVG = generateSlab(corners);

  // Separate walls by type
  const slabPerimeterWalls = walls.filter(w => w.type === 'slabPerimeter');
  const perimeterWalls = walls.filter(w => w.type === 'perimeter');
  const partitionWalls = walls.filter(w => w.type === 'partition');

  // Generate wall SVG
  const slabPerimeterSVG = slabPerimeterWalls.map(w => generateWalls(w)).join('\n');
  const perimeterSVG = perimeterWalls.map(w => generateWalls(w)).join('\n');
  const partitionSVG = generatePartitionWalls(partitionWalls);

  // Generate other components
  const columnsSVG = generateColumns(columns);
  const doorsSVG = generateDoors(doors);
  const camerasSVG = generateCameras(cameras);

  return `
    <!-- Slab: ${id} (${name}) -->
    <g id="slab_${id}">
      <!-- Slab footprint -->
      ${slabSVG}

      <!-- Structural Columns -->
      <g id="${id}_columns" style="display:inline">
        ${columnsSVG}
      </g>

      <!-- Walls -->
      <g id="${id}_walls" style="display:inline;fill:#00449e;fill-opacity:1">
        ${slabPerimeterSVG}
        ${perimeterSVG}
        ${partitionSVG}
      </g>

      <!-- Doors -->
      <g id="${id}_doors" style="display:inline">
        ${doorsSVG}
      </g>

      <!-- Cameras -->
      <g id="${id}_cameras" style="display:inline">
        ${camerasSVG}
      </g>
    </g>`;
}

/**
 * Generate SVG for v1 format (backward compatibility)
 */
function generateModelTSVG_V1(warehouseSpec) {
  const { name, slab, walls = [], doors = [], partitionWalls = [], cameras = [], columns = [], property } = warehouseSpec;

  // Calculate viewBox - use property boundary if provided, otherwise use slab
  let minX, maxX, minY, maxY, viewBoxWidth, viewBoxHeight;

  if (property && property.boundary) {
    // Use explicit property boundary
    minX = property.boundary.x;
    minY = property.boundary.y;
    viewBoxWidth = property.boundary.width;
    viewBoxHeight = property.boundary.height;
    maxX = minX + viewBoxWidth;
    maxY = minY + viewBoxHeight;
  } else {
    // Fall back to slab-based calculation
    const allX = slab.corners.map(c => c.x);
    const allY = slab.corners.map(c => c.y);
    minX = Math.min(...allX);
    maxX = Math.max(...allX);
    minY = Math.min(...allY);
    maxY = Math.max(...allY);
    viewBoxWidth = maxX - minX;
    viewBoxHeight = maxY - minY;
  }

  // Calculate width/height attributes with 3.78x scaling
  const width = (viewBoxWidth * 3.78).toFixed(1);
  const height = (viewBoxHeight * 3.78).toFixed(1);

  const slabSVG = generateSlab(slab.corners);
  // Support both old format (array) and new format (object with id)
  const hasWalls = (Array.isArray(walls) && walls.length > 0) || (walls && walls.corners);
  const wallsSVG = hasWalls ? generateWalls(walls) : '';
  const partitionWallsSVG = generatePartitionWalls(partitionWalls);
  const doorsSVG = generateDoors(doors);
  const camerasSVG = generateCameras(cameras);
  const columnsSVG = generateColumns(columns);

  // Embed the source JSON as a script tag
  const embeddedJSON = JSON.stringify(warehouseSpec, null, 2);

  return `<svg width="${width}" height="${height}" viewBox="${minX} ${minY} ${viewBoxWidth} ${viewBoxHeight}" version="1.1" id="warehouse-svg" xmlns="http://www.w3.org/2000/svg">
  <g id="layer1">
    <!-- Property boundary -->
    <rect id="land" width="${viewBoxWidth}" height="${viewBoxHeight}" x="${minX}" y="${minY}" style="fill:#e0e0e0;fill-opacity:0.3;stroke:none"/>

    <!-- Slab -->
    ${slabSVG}

    <!-- Structural Columns -->
    <g id="columns" style="display:inline">
      ${columnsSVG}
    </g>

    <!-- Walls -->
    <g id="walls" style="display:inline;fill:#00449e;fill-opacity:1">
      ${wallsSVG}
      ${partitionWallsSVG}
    </g>

    <!-- Doors -->
    <g id="doors" style="display:inline">
      ${doorsSVG}
    </g>

    <!-- Cameras -->
    <g id="cameras" style="display:inline">
      ${camerasSVG}
    </g>
  </g>

  <!-- Embedded ModelT JSON source data (non-visual, machine-readable) -->
  <script type="application/json" id="modelt-schema">
${embeddedJSON}
  </script>
</svg>`;
}

// Validation function
function validateWarehouseSpec(spec) {
  // Load naming conventions
  const skillDir = path.join(__dirname, '..');
  const conventionsPath = path.join(skillDir, 'NAMING_CONVENTIONS.json');
  const conventions = JSON.parse(fs.readFileSync(conventionsPath, 'utf-8'));

  const violations = [];
  const validNames = conventions.namingConventions;

  // Validate partition wall names
  if (spec.partitionWalls) {
    spec.partitionWalls.forEach(wall => {
      if (!validNames.partitionWalls.includes(wall.id)) {
        violations.push({
          type: 'partitionWall',
          id: wall.id,
          message: `Invalid partition wall name "${wall.id}" - must use female name from naming conventions`,
          validNames: validNames.partitionWalls
        });
      }
    });
  }

  // Validate door names (unique + from list)
  if (spec.doors) {
    const doorIds = spec.doors.map(d => d.id);
    const seenIds = new Set();

    spec.doors.forEach(door => {
      // Check if name is valid
      if (!validNames.doors.includes(door.id)) {
        violations.push({
          type: 'door',
          id: door.id,
          message: `Invalid door name "${door.id}" - must use president/politician name from naming conventions`,
          validNames: validNames.doors
        });
      }

      // Check for duplicates
      if (seenIds.has(door.id)) {
        violations.push({
          type: 'door',
          id: door.id,
          message: `Duplicate door ID "${door.id}" - each door must have unique identifier`
        });
      }
      seenIds.add(door.id);

      // Check if door has wallId
      if (!door.wallId) {
        violations.push({
          type: 'door',
          id: door.id,
          message: `Door "${door.id}" missing wallId - every door must belong to a wall`
        });
      }
    });
  }

  // Validate camera names
  if (spec.cameras) {
    spec.cameras.forEach(camera => {
      if (!validNames.cameras.includes(camera.id)) {
        violations.push({
          type: 'camera',
          id: camera.id,
          message: `Invalid camera name "${camera.id}" - must use food name from naming conventions`,
          validNames: validNames.cameras
        });
      }
    });
  }

  // Validate column names
  if (spec.columns) {
    spec.columns.forEach(column => {
      if (!validNames.columns.includes(column.id)) {
        violations.push({
          type: 'column',
          id: column.id,
          message: `Invalid column name "${column.id}" - must use tree name from naming conventions`,
          validNames: validNames.columns
        });
      }
    });
  }

  // Write violations back to JSON
  conventions.violations = violations;
  fs.writeFileSync(conventionsPath, JSON.stringify(conventions, null, 2));

  // Output violations to console
  if (violations.length > 0) {
    console.error('\n⚠️  VALIDATION WARNINGS:');
    violations.forEach(v => {
      console.error(`  - ${v.message}`);
    });
    console.error(`\n${violations.length} violation(s) found. Details written to NAMING_CONVENTIONS.json`);
    console.error('Generation will continue, but please review and fix violations.\n');
  }

  return violations;
}

// Main execution
function main() {
  const args = process.argv.slice(2);

  // Read input
  let inputData;
  if (args.length === 0 || args[0] === '-') {
    // Read from stdin
    inputData = fs.readFileSync(0, 'utf-8');
  } else {
    // Read from file
    inputData = fs.readFileSync(args[0], 'utf-8');
  }

  const warehouseSpec = JSON.parse(inputData);

  // Normalize slab and walls (convert between corners and segments)
  normalizeClosedShapes(warehouseSpec);

  // Validate the specification
  validateWarehouseSpec(warehouseSpec);

  const svg = generateModelTSVG(warehouseSpec);

  // Write output
  if (args.length >= 2) {
    fs.writeFileSync(args[1], svg);
    console.error(`SVG written to ${args[1]}`);
  } else {
    console.log(svg);
  }
}

if (require.main === module) {
  main();
}

module.exports = { generateModelTSVG, generateWalls, generateSlab };
