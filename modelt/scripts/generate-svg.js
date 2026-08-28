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
// Bird's-eye door geometry — MODELT_SPECIFICATION §5.4.4 + §5.4.7 (normative, 2026-08-27).
// Canonical frame = a door facing NORTH (out = -y, interior = +y, left = +x). Each part
// from the §5.4.7 tables becomes an axis-aligned rect placed at (q along +left, p perp
// +outward): canonical x = q, y = -p. The whole door is then rotated by `facing` so the
// four cardinal orientations reduce to one transform. (x,y) is the door CENTER on the wall
// centerline (§5.4.5). Static plan draws every door CLOSED + the personnel swing arc.
function generateDoor(door, index, isPartition = false) {
  const { id, x, y, openingWidth, leafWidth, type = 'opening', facing = 'north' } = door;
  const doorId = id || `${type}_${index}`;
  const round = v => Math.round(v * 1000) / 1000;
  const bearing = { north: 0, east: 90, south: 180, west: 270 }[facing] ?? 0;

  // Frame origin (§5.4.5, ruled 2026-08-27):
  //  - PERIMETER wall sits flush-inward from the boundary, so its stored (x,y) is on the
  //    OUTWARD face and the §5.4.7 canonical frame is centered on the wall CENTERLINE
  //    C = (x,y) + in*(T/2), in = interior normal (opposite `facing`).
  //  - PARTITION band is CENTERED on the turtle line (§5.2/5.3 correction 2026-08-27), so a
  //    partition door's (x,y) is already the centerline: C = (x,y), no offset.
  const T = 1;          // wall thickness (walls[].thickness default 1 ft, §3.1) = the band 2D draws
  const T2 = T / 2;     // 0.5
  const inNormal = { north: [0, 1], south: [0, -1], east: [-1, 0], west: [1, 0] }[facing] ?? [0, 1];
  const off = isPartition ? 0 : T2;
  const Cx = round(x + inNormal[0] * off);
  const Cy = round(y + inNormal[1] * off);

  // Invalid door: openingWidth is required (§5.4.2). Draw a valid, loud error marker rather
  // than NaN geometry (which can make strict SVG consumers reject the whole file). (§8)
  if (openingWidth === undefined) {
    return `
  <g id="door_${doorId}" transform="translate(${Cx},${Cy}) rotate(${bearing})" data-type="${type}" data-error="missing-openingWidth">
    <rect x="-1" y="-0.5" width="2" height="1" fill="none" stroke="#ff0000" stroke-width="0.2" stroke-dasharray="0.3,0.3"/>
    <text x="0" y="0.35" font-size="1" text-anchor="middle" fill="#ff0000" font-weight="bold">!</text>
  </g>`;
  }

  const W = openingWidth;  // T, T2 already defined above (frame origin)
  // Per-type leafWidth default (§5.4.7): bay/rollup W-1, cooler W+1 (panel overlaps), personnel W-0.2.
  const leafDefault = { bay: W - 1, rollup: W - 1, cooler: W + 1, personnel: W - 0.2 };
  const L = (leafWidth !== undefined) ? leafWidth : (leafDefault[type] !== undefined ? leafDefault[type] : W - 1);
  const openingFill = (type === 'interior') ? '#ffffff' : '#8888aa';

  // Canonical part: q along +left, p perp +outward, `along` extent over left, `depth` over out.
  const R = (q, p, along, depth, attrs) =>
    `\n    <rect x="${round(q - along / 2)}" y="${round(-p - depth / 2)}" width="${round(along)}" height="${round(depth)}" ${attrs}/>`;

  const parts = [];
  let hardwareSide = 'n/a';

  if (type === 'bay' || type === 'rollup') {
    const isRollup = type === 'rollup';
    const dia = door.housingHeight || 2;
    const hasSeal = !isRollup && door.hasDockSeal !== false;
    const hasLeveler = !isRollup && door.hasDockLeveler !== false;
    const levW = door.levelerWidth || 8, levD = door.levelerDepth || 6;
    const jAlong = (W - L) / 2, jQ = W / 2 - (W - L) / 4;
    hardwareSide = 'interior';
    // z-order bottom -> top (§5.4.7): leveler, housing, tracks, curtain, opening, jambs, seal, bumpers.
    if (hasLeveler) parts.push(R(0, -(T2 + 3.0), levW, levD, 'fill="#888" fill-opacity="0.25"'));
    parts.push(R(0, -(T2 + 1.0), L + 1, dia, 'fill="#666" fill-opacity="0.9"'));                 // roll housing
    parts.push(R(+(L / 2 + 0.25), -(T2 + 0.15), 0.5, 0.3, 'fill="#555"'));                        // guide track (left)
    parts.push(R(-(L / 2 + 0.25), -(T2 + 0.15), 0.5, 0.3, 'fill="#555"'));                        // guide track (right)
    parts.push(R(0, -(T2 + 0.10), L, isRollup ? 0.10 : 0.15, 'fill="#999"'));                     // curtain
    parts.push(R(0, 0, W, T, `fill="${openingFill}"`));                                           // opening cut
    if (jAlong > 0.001) {                                                                          // jambs x2 (in wall)
      parts.push(R(+jQ, 0, jAlong, T, 'fill="#00449e"'));
      parts.push(R(-jQ, 0, jAlong, T, 'fill="#00449e"'));
    }
    if (hasSeal) parts.push(R(0, +(T2 + 0.75), W + 2, 1.5, 'fill="#333" fill-opacity="0.9"'));    // dock seal (one band)
    if (door.hasBumpers) {                                                                         // bumpers x2 (optional)
      parts.push(R(+(W / 2 - 0.5), +(T2 + 0.2), 1.0, 0.4, 'fill="#ffb000"'));
      parts.push(R(-(W / 2 - 0.5), +(T2 + 0.2), 1.0, 0.4, 'fill="#ffb000"'));
    }
  } else if (type === 'cooler') {
    const slide = door.slideDirection;
    const t = (door.insulation || 4) / 12;
    const PW = (leafWidth !== undefined) ? leafWidth : W + 1;
    const pPanel = +(T2 + 1 / 12 + t / 2);
    parts.push(R(0, 0, W, T, `fill="${openingFill}"`));                                           // opening
    parts.push(R(0, +(T2 + 0.04), W + 0.67, 0.083, 'fill="#00449e"'));                            // frame (outward face)
    parts.push(R(0, -(T2 + 0.04), W + 0.67, 0.083, 'fill="#00449e"'));                            // frame (interior face)
    if (slide === 'left' || slide === 'right') {
      const s = slide === 'left' ? 1 : -1;
      hardwareSide = slide;
      parts.push(R(s * PW / 2, pPanel, 2 * PW + 0.5, 0.25, 'fill="#444"'));                       // track/rail
      parts.push(R(0, pPanel, PW, t, 'fill="#8888aa"'));                                          // panel (closed)
      parts.push(R(+PW / 4, pPanel, 0.2, 0.2, 'fill="#444"'));                                    // hangers x2
      parts.push(R(-PW / 4, pPanel, 0.2, 0.2, 'fill="#444"'));
    } else {
      hardwareSide = 'unknown';  // never guess a side (§5.4.7): rail spanning the opening only
      parts.push(R(0, pPanel, W, 0.25, 'fill="#444"'));
    }
  } else if (type === 'personnel') {
    const hinge = door.hingePosition, swing = door.swingDirection;
    parts.push(R(0, 0, W, T, `fill="${openingFill}"`));                                           // opening
    parts.push(R(+(W / 2 + 0.125), 0, 0.25, 0.25, 'fill="#00449e"'));                             // frame (side jamb)
    parts.push(R(-(W / 2 + 0.125), 0, 0.25, 0.25, 'fill="#00449e"'));                             // frame (side jamb)
    if ((hinge === 'left' || hinge === 'right') && (swing === 'inward' || swing === 'outward')) {
      const h = hinge === 'left' ? 1 : -1, w = swing === 'outward' ? 1 : -1;
      hardwareSide = `${hinge}/${swing}`;
      const pLeaf = w * (T2 + 0.075);
      parts.push(R(0, pLeaf, W - 0.2, 0.15, 'fill="#999"'));                                      // leaf (closed)
      // swing arc: quarter circle radius W-0.2 centered on the hinge jamb, on the swing side.
      const Rr = W - 0.2;
      const Cx = round(-h * (W / 2 - 0.1)), Cy = round(-(w * (T2 + 0.075)));   // closed free end
      const Ox = round(h * (W / 2 - 0.1)), Oy = round(-(w * Rr));             // open free end
      const sweep = (h * w > 0) ? 1 : 0;
      parts.push(`\n    <path d="M ${Cx},${Cy} A ${round(Rr)},${round(Rr)} 0 0,${sweep} ${Ox},${Oy}" fill="none" stroke="#999" stroke-width="0.05" stroke-dasharray="0.2,0.2"/>`);
    } else {
      hardwareSide = 'unknown';  // missing hingePosition/swingDirection: opening + frame only
    }
  } else {
    // interior / opening: opening only, no hardware
    parts.push(R(0, 0, W, T, `fill="${openingFill}"`));
  }

  const dataAttrs = `data-type="${type}" data-facing="${facing}" data-hardware-side="${hardwareSide}"`;
  return `
  <g id="door_${doorId}" transform="translate(${Cx},${Cy}) rotate(${bearing})" ${dataAttrs}>${parts.join('')}
  </g>`;
}

// Generate all doors
function generateDoors(doors, partitionIds) {
  if (!doors || doors.length === 0) return '';

  return doors.map((door, index) =>
    generateDoor(door, index, !!(partitionIds && partitionIds.has(door.wallId)))).join('');
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

// Generate partition walls (open polylines). Bands are CENTERED on the turtle line
// (±T/2) per the §5.2/5.3 correction (2026-08-27): the turtle line is the reference
// line, so a partition door's (x,y) is the centerline (C = (x,y), no offset — see
// generateDoor). Each segment is one rect centered on its line, T wide, extended by
// T/2 into interior corners so adjacent segments overlap cleanly; free ends stop flush
// at the turtle endpoint. Fill (#00449e) is inherited from the enclosing walls group.
function generatePartitionWalls(partitionWalls) {
  if (!partitionWalls || partitionWalls.length === 0) return '';
  const T = 1, H = T / 2;
  let wallsSVG = '';

  for (const partition of partitionWalls) {
    const points = convertTurtleToPoints(partition);
    const wallName = partition.id || 'unnamed';
    wallsSVG += `
  <g id="partition_${wallName}">`;

    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i], b = points[i + 1];
      const aExt = (i > 0) ? H : 0;                   // interior corner at a -> extend to fill
      const bExt = (i < points.length - 2) ? H : 0;   // interior corner at b -> extend

      if (a.y === b.y) {
        // Horizontal segment, band centered on y = a.y
        const goingEast = b.x > a.x;
        const p0 = goingEast ? a.x - aExt : a.x + aExt;
        const p1 = goingEast ? b.x + bExt : b.x - bExt;
        const left = Math.min(p0, p1), right = Math.max(p0, p1);
        wallsSVG += `
    <rect id="wall_h_${wallName}_seg${i}" x="${left}" y="${a.y - H}" width="${right - left}" height="${T}"/>`;
      } else if (a.x === b.x) {
        // Vertical segment, band centered on x = a.x
        const goingSouth = b.y > a.y;
        const p0 = goingSouth ? a.y - aExt : a.y + aExt;
        const p1 = goingSouth ? b.y + bExt : b.y - bExt;
        const top = Math.min(p0, p1), bottom = Math.max(p0, p1);
        wallsSVG += `
    <rect id="wall_v_${wallName}_seg${i}" x="${a.x - H}" y="${top}" width="${T}" height="${bottom - top}"/>`;
      }
    }

    wallsSVG += `
  </g>`;

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
    // Calculate from all slabs. Pad by DOOR_MARGIN (§5.4.7) so outward door parts
    // (dock seals reach ~2 ft beyond the wall) never clip at the edge.
    const DOOR_MARGIN = 2.5;
    const allCorners = slabs.flatMap(slab => slab.corners || []);
    const allX = allCorners.map(c => c.x);
    const allY = allCorners.map(c => c.y);
    minX = Math.min(...allX) - DOOR_MARGIN;
    minY = Math.min(...allY) - DOOR_MARGIN;
    const maxX = Math.max(...allX) + DOOR_MARGIN;
    const maxY = Math.max(...allY) + DOOR_MARGIN;
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
  const partitionIds = new Set(partitionWalls.map(w => w.id));
  const columnsSVG = generateColumns(columns);
  const doorsSVG = generateDoors(doors, partitionIds);
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
    // Fall back to slab-based calculation, padded by DOOR_MARGIN (§5.4.7) so outward
    // door parts (dock seals) never clip at the edge.
    const DOOR_MARGIN = 2.5;
    const allX = slab.corners.map(c => c.x);
    const allY = slab.corners.map(c => c.y);
    minX = Math.min(...allX) - DOOR_MARGIN;
    maxX = Math.max(...allX) + DOOR_MARGIN;
    minY = Math.min(...allY) - DOOR_MARGIN;
    maxY = Math.max(...allY) + DOOR_MARGIN;
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
  const partitionIds = new Set((partitionWalls || []).map(w => w.id));
  const doorsSVG = generateDoors(doors, partitionIds);
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

  // Collect entities from BOTH formats so validation covers v2, not just v1:
  //   v1: top-level spec.doors / spec.cameras / spec.columns / spec.partitionWalls
  //   v2: spec.slabs[].doors / .cameras / .columns / .walls (type 'partition')
  // (Authorized by modestocat 2026-08-27 — before this, v2 specs were never validated.)
  const slabs = Array.isArray(spec.slabs) ? spec.slabs : [];
  const doors = [];
  const cameras = [];
  const columns = [];
  const partitionWalls = [];

  slabs.forEach(s => {
    (s.doors || []).forEach(d => doors.push({ ...d, _slab: s.id }));
    (s.cameras || []).forEach(c => cameras.push({ ...c, _slab: s.id }));
    (s.columns || []).forEach(c => columns.push({ ...c, _slab: s.id }));
    (s.walls || []).forEach(w => { if (w.type === 'partition') partitionWalls.push({ ...w, _slab: s.id }); });
  });
  (spec.doors || []).forEach(d => doors.push(d));
  (spec.cameras || []).forEach(c => cameras.push(c));
  (spec.columns || []).forEach(c => columns.push(c));
  (spec.partitionWalls || []).forEach(w => partitionWalls.push(w));

  const where = e => (e && e._slab) ? ` (slab ${e._slab})` : '';

  // Data resolution (Section 3.1, George 2026-08-27): REQUIRED fields are WHOLE FEET;
  // nothing may require a fraction. Warn (warn-only) on a fractional required length/
  // position. Optional fields (e.g. leafWidth) may carry up to 3 dp and are exempt.
  const isFraction = v => typeof v === 'number' && Math.abs(v - Math.round(v)) > 1e-9;
  const fracWarn = (kind, id, field, val, ctx) => {
    if (isFraction(val)) {
      violations.push({
        type: kind,
        id,
        message: `${kind} "${id}"${ctx} ${field} = ${val} must be a whole number of feet (required field)`
      });
    }
  };
  doors.forEach(d => ['x', 'y', 'openingWidth'].forEach(f => fracWarn('door', d.id, f, d[f], where(d))));
  cameras.forEach(c => ['x', 'y'].forEach(f => fracWarn('camera', c.id, f, c[f], where(c))));
  columns.forEach(c => ['x', 'y'].forEach(f => fracWarn('column', c.id, f, c[f], where(c))));
  const checkSegs = (segs, kind, id, ctx) => (segs || []).forEach((seg, i) => {
    if (isFraction(seg.length)) {
      violations.push({
        type: kind,
        id,
        message: `${kind} "${id}"${ctx} segment[${i}] length = ${seg.length} must be a whole number of feet (required field)`
      });
    }
  });
  slabs.forEach(s => {
    checkSegs(s.segments, 'slab', s.id, '');
    (s.walls || []).forEach(w => checkSegs(w.segments, 'wall', w.id, ` (slab ${s.id})`));
  });
  if (spec.slab) checkSegs(spec.slab.segments, 'slab', spec.slab.id || 'slab', '');
  (spec.partitionWalls || []).forEach(w => checkSegs(w.segments, 'wall', w.id, ''));

  // Validate slab names (celestial pool) — v2 only; v1 has a single unnamed slab
  slabs.forEach(slab => {
    if (slab.id && validNames.slabs && !validNames.slabs.includes(slab.id)) {
      violations.push({
        type: 'slab',
        id: slab.id,
        message: `Invalid slab name "${slab.id}" - must use celestial body from naming conventions`,
        validNames: validNames.slabs
      });
    }
  });

  // Validate partition wall names (female names)
  partitionWalls.forEach(wall => {
    if (!validNames.partitionWalls.includes(wall.id)) {
      violations.push({
        type: 'partitionWall',
        id: wall.id,
        message: `Invalid partition wall name "${wall.id}"${where(wall)} - must use female name from naming conventions`,
        validNames: validNames.partitionWalls
      });
    }
  });

  // Validate door names (from list + unique + has wallId)
  const seenDoorIds = new Set();
  doors.forEach(door => {
    if (!validNames.doors.includes(door.id)) {
      violations.push({
        type: 'door',
        id: door.id,
        message: `Invalid door name "${door.id}"${where(door)} - must use president/politician name from naming conventions`,
        validNames: validNames.doors
      });
    }
    if (seenDoorIds.has(door.id)) {
      violations.push({
        type: 'door',
        id: door.id,
        message: `Duplicate door ID "${door.id}"${where(door)} - each door must have unique identifier`
      });
    }
    seenDoorIds.add(door.id);
    if (!door.wallId) {
      violations.push({
        type: 'door',
        id: door.id,
        message: `Door "${door.id}"${where(door)} missing wallId - every door must belong to a wall`
      });
    }
    // Width model (v0.5 §5.4.2, Section 12 width ruling (5) addendum): openingWidth is
    // required; bayWidth/width/doorWidth are deprecated. generateDoor() no longer reads
    // the legacy fields, so a legacy file must fail LOUDLY here instead of rendering
    // from a dead path.
    ['bayWidth', 'width', 'doorWidth'].forEach(f => {
      if (door[f] !== undefined) {
        violations.push({
          type: 'door',
          id: door.id,
          message: `Deprecated width field "${f}" on door "${door.id}"${where(door)} - migrate to openingWidth/leafWidth`
        });
      }
    });
    if (door.openingWidth === undefined) {
      violations.push({
        type: 'door',
        id: door.id,
        message: `Door "${door.id}"${where(door)} missing openingWidth (ft) - required on every door`
      });
    }
    // Cooler panel must overlap the opening (§5.4.7): a stored cooler leafWidth <= W is invalid.
    if (door.type === 'cooler' && door.leafWidth !== undefined && door.openingWidth !== undefined
        && door.leafWidth <= door.openingWidth) {
      violations.push({
        type: 'door',
        id: door.id,
        message: `Cooler door "${door.id}"${where(door)} leafWidth ${door.leafWidth} <= openingWidth ${door.openingWidth} - the sliding panel must be wider than the hole (default W+1)`
      });
    }
    // Instance-side fields (v0.5 §5.4 ruling, modestocat 2026-08-27): the hardware
    // SIDE must never be defaulted. Absent -> warn; 2D draws a neutral opening with
    // data-hardware-side='unknown'. cooler needs slideDirection; personnel needs
    // hingePosition + swingDirection.
    if (door.type === 'cooler' && door.slideDirection === undefined) {
      violations.push({
        type: 'door',
        id: door.id,
        message: `Cooler door "${door.id}"${where(door)} missing slideDirection (left|right) - required; hardware side must not be defaulted`
      });
    }
    if (door.type === 'personnel') {
      if (door.hingePosition === undefined) {
        violations.push({
          type: 'door',
          id: door.id,
          message: `Personnel door "${door.id}"${where(door)} missing hingePosition (left|right) - required; hinge side must not be defaulted`
        });
      }
      if (door.swingDirection === undefined) {
        violations.push({
          type: 'door',
          id: door.id,
          message: `Personnel door "${door.id}"${where(door)} missing swingDirection (inward|outward) - required; swing side must not be defaulted`
        });
      }
    }
  });

  // Outward-extent check (§5.4.7): when property.boundary is explicit, a door's outward
  // parts (dock seal reaches ~1.5 ft beyond the stored outward-face (x,y)) must not exceed
  // it — the auto-calc path pads instead, but an explicit boundary is authored, so warn.
  const boundary = spec.property && spec.property.boundary;
  if (boundary) {
    const OUTWARD_REACH = 1.5;
    const round1 = v => Math.round(v * 10) / 10;
    const outVec = { north: [0, -1], south: [0, 1], east: [1, 0], west: [-1, 0] };
    const bx0 = boundary.x, by0 = boundary.y, bx1 = boundary.x + boundary.width, by1 = boundary.y + boundary.height;
    doors.forEach(d => {
      if (d.x === undefined || d.y === undefined) return;
      const v = outVec[d.facing] || [0, -1];
      const ex = d.x + v[0] * OUTWARD_REACH, ey = d.y + v[1] * OUTWARD_REACH;
      if (ex < bx0 || ex > bx1 || ey < by0 || ey > by1) {
        violations.push({
          type: 'door',
          id: d.id,
          message: `Door "${d.id}"${where(d)} outward extent (${round1(ex)},${round1(ey)}) exceeds property.boundary [${bx0},${by0},${bx1},${by1}] - seal will clip`
        });
      }
    });
  }

  // Validate camera names (food) + heading/tilt ranges (spec 8.3)
  cameras.forEach(camera => {
    if (!validNames.cameras.includes(camera.id)) {
      violations.push({
        type: 'camera',
        id: camera.id,
        message: `Invalid camera name "${camera.id}"${where(camera)} - must use food name from naming conventions`,
        validNames: validNames.cameras
      });
    }
    if (camera.direction !== undefined &&
        (typeof camera.direction !== 'number' || camera.direction < 0 || camera.direction >= 360)) {
      violations.push({
        type: 'camera',
        id: camera.id,
        message: `Camera "${camera.id}"${where(camera)} direction ${camera.direction} out of range - must be in [0, 360)`
      });
    }
    if (camera.tilt !== undefined &&
        (typeof camera.tilt !== 'number' || camera.tilt < 0 || camera.tilt > 90)) {
      violations.push({
        type: 'camera',
        id: camera.id,
        message: `Camera "${camera.id}"${where(camera)} tilt ${camera.tilt} out of range - must be in [0, 90]`
      });
    }
  });

  // Validate column names (tree)
  columns.forEach(column => {
    if (!validNames.columns.includes(column.id)) {
      violations.push({
        type: 'column',
        id: column.id,
        message: `Invalid column name "${column.id}"${where(column)} - must use tree name from naming conventions`,
        validNames: validNames.columns
      });
    }
  });

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
