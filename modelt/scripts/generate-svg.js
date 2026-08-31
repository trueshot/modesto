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

  // Wrap in a group only to carry data-source when the wall geometry is an estimate (§5.4.2).
  const src = sourceAttr(wallStructure);
  return src ? `\n  <g${src}>${wallsSVG}\n  </g>` : wallsSVG;
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
// Source provenance (§5.4.2, ruled 2026-08-28; extended §5.14 2026-08-30): any element whose
// geometry was estimated carries `source: "estimate:..."`, and anything laid off the aerial
// basemap carries `source: "traced:..."` (not a guess, but not a survey either). Surface both
// as data-source so consumers can style them; measured/observed sources are not marked.
function sourceAttr(el) {
  const s = (el && typeof el.source === 'string') ? el.source : '';
  return (s.startsWith('estimate:') || s.startsWith('traced:')) ? ` data-source="${s}"` : '';
}

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

  // Live state (§5.4.2 + §5.4.7 amendment 2026-08-28): the plan RENDERS state.open (0..1).
  // Absent state = closed. openAngle (personnel) defaults to 90.
  const openFrac = (door.state && typeof door.state.open === 'number')
    ? Math.max(0, Math.min(1, door.state.open))
    : (door.state === 'open' ? 1 : 0);
  const openAngle = (door.openAngle !== undefined) ? door.openAngle : 90;

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
    const curtainAlong = L * (1 - openFrac);                                                       // state: open=1 -> clear
    if (curtainAlong > 0.001) parts.push(R(0, -(T2 + 0.10), curtainAlong, isRollup ? 0.10 : 0.15, 'fill="#999"')); // curtain (closed fraction)
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
      const slid = s * openFrac * PW;                                                             // state: panel slides along rail
      hardwareSide = slide;
      parts.push(R(s * PW / 2, pPanel, 2 * PW + 0.5, 0.25, 'fill="#444"'));                       // track/rail (fixed)
      parts.push(R(slid, pPanel, PW, t, 'fill="#8888aa"'));                                       // panel (shifted by state.open)
      parts.push(R(slid + PW / 4, pPanel, 0.2, 0.2, 'fill="#444"'));                              // hangers x2
      parts.push(R(slid - PW / 4, pPanel, 0.2, 0.2, 'fill="#444"'));
    } else {
      hardwareSide = 'unknown';  // never guess a side (§5.4.7): rail spanning the opening only
      parts.push(R(0, pPanel, W, 0.25, 'fill="#444"'));
    }
  } else if (type === 'personnel') {
    // §5.4.7 (2026-08-28): hingePosition REQUIRED (as seen from facing side); swing is always
    // toward the interior (swingDirection deprecated/derived). Leaf is a LINE of length L=W-0.2
    // from the hinge jamb point, rotated a = state.open*openAngle toward the interior, plus the
    // architect's quarter-circle swing arc (radius L) on the interior side.
    const hinge = door.hingePosition;
    const Lp = W - 0.2;
    parts.push(R(0, 0, W, T, `fill="${openingFill}"`));                                           // opening
    parts.push(R(+(W / 2 + 0.125), 0, 0.25, 0.25, 'fill="#00449e"'));                             // frame (side jamb)
    parts.push(R(-(W / 2 + 0.125), 0, 0.25, 0.25, 'fill="#00449e"'));                             // frame (side jamb)
    if (hinge === 'left' || hinge === 'right') {
      const h = hinge === 'left' ? 1 : -1;
      hardwareSide = `${hinge}/interior`;
      const hx = h * (W / 2 - 0.1), hy = T2;                   // hinge jamb point (interior face; canonical y = -p = +T2)
      const a = (openFrac * openAngle) * Math.PI / 180;        // current swing angle from the wall
      const tipX = round(hx - h * Lp * Math.cos(a));
      const tipY = round(hy + Lp * Math.sin(a));               // +y is interior
      parts.push(`\n    <line x1="${round(hx)}" y1="${round(hy)}" x2="${tipX}" y2="${tipY}" stroke="#999" stroke-width="0.15"/>`); // leaf
      const cX = round(hx - h * Lp), cY = round(hy);           // closed tip (along wall)
      const oX = round(hx), oY = round(hy + Lp);               // open-90 tip (into interior)
      const sweep = (h > 0) ? 0 : 1;
      parts.push(`\n    <path d="M ${cX},${cY} A ${round(Lp)},${round(Lp)} 0 0,${sweep} ${oX},${oY}" fill="none" stroke="#999" stroke-width="0.05" stroke-dasharray="0.2,0.2"/>`); // swing arc
    } else {
      hardwareSide = 'unknown';  // missing hingePosition: leaf centered in the opening, no swing
      parts.push(R(0, -T2, Lp, 0.15, 'fill="#999"'));
    }
  } else if (type === 'conveyor') {
    // Conveyor penetration (§5.4.7): a distinct-fill opening (so it can't read as a doorway)
    // plus a SYMBOL — a dashed centerline through the wall extending 3 ft each side, with a
    // small arrowhead on the facing (outward) side. No hardware; the belt's geometry is
    // unspecified (§5.4.3), so draw nothing that asserts extent/direction/height.
    hardwareSide = 'n/a';
    parts.push(R(0, 0, W, T, 'fill="#ccaa44" fill-opacity="0.5"'));                                // opening (distinct fill)
    const ext = T2 + 3;                                                                            // reach: T/2 + 3 ft each side
    parts.push(`\n    <line x1="0" y1="${round(-ext)}" x2="0" y2="${round(ext)}" stroke="#666" stroke-width="0.1" stroke-dasharray="0.4,0.3"/>`); // centerline (q=0, through the wall)
    parts.push(`\n    <path d="M -0.3,${round(-ext + 0.5)} L 0,${round(-ext)} L 0.3,${round(-ext + 0.5)}" fill="none" stroke="#666" stroke-width="0.1"/>`); // arrowhead on the facing (outward = -y) side
  } else {
    // interior / opening: opening only, no hardware
    parts.push(R(0, 0, W, T, `fill="${openingFill}"`));
  }

  const sillAttr = (type === 'conveyor' && door.sillHeight !== undefined) ? ` data-sill="${door.sillHeight}"` : '';
  const dataAttrs = `data-type="${type}" data-facing="${facing}" data-hardware-side="${hardwareSide}"${sillAttr}${sourceAttr(door)}`;
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
  <g id="${cameraId}" class="camera"${sourceAttr(camera)}>
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
  <g id="${columnId}" class="column"${sourceAttr(column)}>
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

// Generate a packing line (§5.11): elements in FLOW ORDER — RUNs (conveyor segments) and
// STATIONs (machines). Run = a band of `width` along the path with downstream chevrons
// ~every 8 ft; station = its footprint (rect or disc) lettered by kind with inlet/outlet
// arrows. Colors (Appendix A row pending): run #b8b8b8@0.6 / chevrons #555; station
// #d8c4a0 / letter + arrows #333. Whole-foot positions; sizes may be decimals.
function generateLine(line) {
  const round = v => Math.round(v * 1000) / 1000;
  const id = line.id || 'unnamed';
  const elements = line.elements || [];
  const arrowDir = { north: [0, -1], south: [0, 1], east: [1, 0], west: [-1, 0] };
  let svg = `\n    <g id="line_${id}"${sourceAttr(line)}>`;

  for (const el of elements) {
    if (Array.isArray(el.path) && el.path.length >= 2) {
      // RUN — band (one rotated rect per segment) + downstream chevrons every ~8 ft
      const w = (el.width !== undefined) ? el.width : 3;
      const pitch = 8;
      for (let i = 0; i < el.path.length - 1; i++) {
        const a = el.path[i], b = el.path[i + 1];
        const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy);
        if (len < 1e-6) continue;
        const deg = Math.atan2(dy, dx) * 180 / Math.PI, mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        svg += `\n      <g transform="translate(${round(mx)},${round(my)}) rotate(${round(deg)})"><rect x="${round(-len / 2)}" y="${round(-w / 2)}" width="${round(len)}" height="${round(w)}" fill="#b8b8b8" fill-opacity="0.6" stroke="#888" stroke-width="0.1"/></g>`;
      }
      // Shelf (§5.11): a lighter parallel band of shelf.width on the given flow-relative side.
      if (el.shelf && (el.shelf.side === 'left' || el.shelf.side === 'right')) {
        const sw = (el.shelf.width !== undefined) ? el.shelf.width : 1;
        const off = w / 2 + sw / 2;  // offset from the run centerline
        for (let i = 0; i < el.path.length - 1; i++) {
          const a = el.path[i], b = el.path[i + 1];
          const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy);
          if (len < 1e-6) continue;
          const ux = dx / len, uy = dy / len;
          // left-of-flow unit (screen y-down): (uy, -ux); right = negated
          const lx = uy, ly = -ux, sgn = (el.shelf.side === 'left') ? 1 : -1;
          const deg = Math.atan2(dy, dx) * 180 / Math.PI;
          const mx = (a.x + b.x) / 2 + sgn * lx * off, my = (a.y + b.y) / 2 + sgn * ly * off;
          svg += `\n      <g transform="translate(${round(mx)},${round(my)}) rotate(${round(deg)})"><rect x="${round(-len / 2)}" y="${round(-sw / 2)}" width="${round(len)}" height="${round(sw)}" fill="#b8b8b8" fill-opacity="0.4" stroke="#aaa" stroke-width="0.08"/></g>`;
        }
      }
      let acc = pitch / 2;
      for (let i = 0; i < el.path.length - 1; i++) {
        const a = el.path[i], b = el.path[i + 1];
        const dx = b.x - a.x, dy = b.y - a.y, segLen = Math.hypot(dx, dy);
        if (segLen < 1e-6) continue;
        const ux = dx / segLen, uy = dy / segLen, deg = Math.atan2(dy, dx) * 180 / Math.PI;
        const s = Math.min(w, 2) / 2;
        while (acc <= segLen) {
          const px = a.x + ux * acc, py = a.y + uy * acc;
          svg += `\n      <g transform="translate(${round(px)},${round(py)}) rotate(${round(deg)})"><path d="M ${round(-s * 0.6)},${round(-s)} L ${round(s * 0.6)},0 L ${round(-s * 0.6)},${round(s)}" fill="none" stroke="#555" stroke-width="0.15"/></g>`;
          acc += pitch;
        }
        acc -= segLen;
      }
    } else if (el.footprint) {
      // STATION — footprint (rect or disc), lettered by kind, with inlet/outlet arrows
      const fp = el.footprint;
      const letter = (el.kind || '?').charAt(0).toUpperCase();
      if (fp.r !== undefined) {
        svg += `\n      <circle cx="${round(fp.x)}" cy="${round(fp.y)}" r="${round(fp.r)}" fill="#d8c4a0" stroke="#333" stroke-width="0.15"/>`;
        svg += `\n      <text x="${round(fp.x)}" y="${round(fp.y + Math.min(fp.r, 2) / 3)}" font-size="${round(Math.min(fp.r, 2))}" text-anchor="middle" fill="#333" font-weight="bold">${letter}</text>`;
      } else {
        const w = fp.w || 2, d = fp.d || 2, rot = fp.rotation || 0;
        svg += `\n      <g transform="translate(${round(fp.x)},${round(fp.y)}) rotate(${round(rot)})"><rect x="${round(-w / 2)}" y="${round(-d / 2)}" width="${round(w)}" height="${round(d)}" fill="#d8c4a0" stroke="#333" stroke-width="0.15"/><text x="0" y="${round(Math.min(w, d, 3) / 3)}" font-size="${round(Math.min(w, d, 3) / 1.5)}" text-anchor="middle" fill="#333" font-weight="bold">${letter}</text></g>`;
      }
      const cx = fp.x, cy = fp.y;
      const reach = (fp.r !== undefined ? fp.r : Math.max(fp.w || 2, fp.d || 2) / 2) + 1;
      const drawArrow = (side, into) => {
        const v = arrowDir[side]; if (!v) return;
        const dir = into ? [-v[0], -v[1]] : v;
        const tipX = cx + v[0] * reach, tipY = cy + v[1] * reach;
        const baseX = tipX - dir[0] * 0.9, baseY = tipY - dir[1] * 0.9;
        const pdeg = Math.atan2(dir[1], dir[0]) * 180 / Math.PI;
        svg += `\n      <line x1="${round(baseX)}" y1="${round(baseY)}" x2="${round(tipX)}" y2="${round(tipY)}" stroke="#333" stroke-width="0.12"/><g transform="translate(${round(tipX)},${round(tipY)}) rotate(${round(pdeg)})"><path d="M -0.4,-0.25 L 0,0 L -0.4,0.25" fill="none" stroke="#333" stroke-width="0.12"/></g>`;
      };
      if (el.inlet) drawArrow(el.inlet, true);
      (el.outlets || []).forEach(o => drawArrow(o, false));
    }
  }

  if (line.name) {
    const f = elements[0];
    const ax = f ? (f.path ? f.path[0].x : (f.footprint ? f.footprint.x : 0)) : 0;
    const ay = f ? (f.path ? f.path[0].y : (f.footprint ? f.footprint.y : 0)) : 0;
    svg += `\n      <text x="${round(ax)}" y="${round(ay - 2)}" font-size="1.2" text-anchor="middle" fill="#333">${line.name}</text>`;
  }

  svg += `\n    </g>`;
  return svg;
}

function generateLines(lines) {
  if (!lines || lines.length === 0) return '';
  return lines.map(l => generateLine(l)).join('');
}

// Derive a truck well's footprint from the dock doors it serves (§5.12): centered on the
// doors, extending `width` past the outer jambs each side, out `padLength + rampLength` on
// the doors' outward (`facing`) side. Returns axis-aligned bounds + frame info, or null.
const FACING_VEC = { north: [0, -1], south: [0, 1], east: [1, 0], west: [-1, 0] };
function wellRect(well, doorsById) {
  const dl = (well.doors || []).map(id => doorsById[id]).filter(Boolean);
  if (dl.length === 0) return null;
  const facing = dl[0].facing || 'north';
  const out = FACING_VEC[facing] || [0, -1];
  const alongIsX = (facing === 'north' || facing === 'south');   // wall runs E-W -> along = x
  const coord = d => (alongIsX ? d.x : d.y);
  const extLo = Math.min(...dl.map(d => coord(d) - (d.openingWidth || 0) / 2));
  const extHi = Math.max(...dl.map(d => coord(d) + (d.openingWidth || 0) / 2));
  const width = well.width !== undefined ? well.width : 4;
  const alongMin = extLo - width, alongMax = extHi + width;
  const wallCoord = alongIsX ? dl[0].y : dl[0].x;                 // outward-face coord on the perp axis
  const padLength = well.padLength !== undefined ? well.padLength : 0;
  const depth = padLength + (well.rampLength !== undefined ? well.rampLength : 55);
  const outSign = alongIsX ? out[1] : out[0];
  const outer = wallCoord + outSign * depth;
  return { alongIsX, alongMin, alongMax, wallCoord, outer, padLength, out, centres: dl.map(coord).sort((a, b) => a - b) };
}

// Generate truck wells (§5.12): hatched ramp band out padLength+rampLength with a single
// slope arrow pointing down toward the wall; a hatched pad rect at the wall only if padLength>0.
function generateWell(well, doorsById) {
  const round = v => Math.round(v * 1000) / 1000;
  const r = wellRect(well, doorsById);
  if (!r) return '';
  const rect = (x0, y0, x1, y1, attrs) =>
    `\n      <rect x="${round(Math.min(x0, x1))}" y="${round(Math.min(y0, y1))}" width="${round(Math.abs(x1 - x0))}" height="${round(Math.abs(y1 - y0))}" ${attrs}/>`;
  let svg = `\n    <g id="well_${well.id}"${sourceAttr(well)}>`;
  // ramp band (hatched)
  if (r.alongIsX) svg += rect(r.alongMin, r.wallCoord, r.alongMax, r.outer, 'fill="url(#wellHatch)" stroke="#a89060" stroke-width="0.1"');
  else svg += rect(r.wallCoord, r.alongMin, r.outer, r.alongMax, 'fill="url(#wellHatch)" stroke="#a89060" stroke-width="0.1"');
  // slope arrow: down the ramp toward the wall, centered along
  const mid = (r.alongMin + r.alongMax) / 2;
  const outerPt = r.outer, wallPt = r.wallCoord;
  const nearWall = wallPt + (outerPt - wallPt) * 0.15;   // arrowhead sits ~15% from the wall
  let ax0, ay0, ax1, ay1;
  if (r.alongIsX) { ax0 = mid; ay0 = outerPt; ax1 = mid; ay1 = nearWall; }
  else { ax0 = outerPt; ay0 = mid; ax1 = nearWall; ay1 = mid; }
  const adeg = Math.atan2(ay1 - ay0, ax1 - ax0) * 180 / Math.PI;
  svg += `\n      <line x1="${round(ax0)}" y1="${round(ay0)}" x2="${round(ax1)}" y2="${round(ay1)}" stroke="#666" stroke-width="0.2"/>`;
  svg += `\n      <g transform="translate(${round(ax1)},${round(ay1)}) rotate(${round(adeg)})"><path d="M -0.9,-0.5 L 0,0 L -0.9,0.5" fill="none" stroke="#666" stroke-width="0.2"/></g>`;
  // pad hatch at the wall if padLength>0
  if (r.padLength > 0) {
    const padOuter = r.wallCoord + (r.out[r.alongIsX ? 1 : 0]) * r.padLength;
    if (r.alongIsX) svg += rect(r.alongMin, r.wallCoord, r.alongMax, padOuter, 'fill="url(#wellHatch)" stroke="#a89060" stroke-width="0.15"');
    else svg += rect(r.wallCoord, r.alongMin, padOuter, r.alongMax, 'fill="url(#wellHatch)" stroke="#a89060" stroke-width="0.15"');
  }
  svg += `\n    </g>`;
  return svg;
}

function generateWells(wells, doorsById) {
  if (!wells || wells.length === 0) return '';
  return wells.map(w => generateWell(w, doorsById)).join('');
}

// Generate markings (§5.13): truckBay = derived stripes across the well slope at each bay
// boundary; free-form = paint along a path (polyline) or a rect. `<g id=marking_{id}>`.
function generateMarking(mk, i, wellsById, doorsById) {
  const round = v => Math.round(v * 1000) / 1000;
  const id = mk.id || `${mk.kind}_${i}`;
  const color = mk.color || 'yellow';
  const width = mk.width !== undefined ? mk.width : 0.33;
  let svg = `\n    <g id="marking_${id}" data-kind="${mk.kind}"${sourceAttr(mk)}>`;

  if (mk.kind === 'truckBay') {
    const well = wellsById[mk.well];
    const r = well && wellRect(well, doorsById);
    if (r) {
      const c = r.centres;
      const boundaries = [];
      for (let k = 1; k < c.length; k++) boundaries.push((c[k - 1] + c[k]) / 2);
      const pitch = c.length > 1 ? (c[c.length - 1] - c[0]) / (c.length - 1) : 10;
      boundaries.unshift(Math.max(r.alongMin, c[0] - pitch / 2));
      boundaries.push(Math.min(r.alongMax, c[c.length - 1] + pitch / 2));
      for (const b of boundaries) {
        if (r.alongIsX) svg += `\n      <line x1="${round(b)}" y1="${round(r.wallCoord)}" x2="${round(b)}" y2="${round(r.outer)}" stroke="${color}" stroke-width="${round(width)}"/>`;
        else svg += `\n      <line x1="${round(r.wallCoord)}" y1="${round(b)}" x2="${round(r.outer)}" y2="${round(b)}" stroke="${color}" stroke-width="${round(width)}"/>`;
      }
    }
  } else if (Array.isArray(mk.path)) {
    const pts = mk.path.map(p => `${round(p.x)},${round(p.y)}`).join(' ');
    svg += `\n      <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="${round(width)}"/>`;
    if (mk.kind === 'sign' && mk.label) {
      const p0 = mk.path[0];
      svg += `\n      <text x="${round(p0.x)}" y="${round(p0.y)}" font-size="1" fill="${color}">${mk.label}</text>`;
    }
  } else if (mk.path && mk.path.w !== undefined) {
    const p = mk.path;
    svg += `\n      <rect x="${round(p.x - p.w / 2)}" y="${round(p.y - p.d / 2)}" width="${round(p.w)}" height="${round(p.d)}" fill="none" stroke="${color}" stroke-width="${round(width)}"/>`;
    if (mk.kind === 'sign' && mk.label) svg += `\n      <text x="${round(p.x)}" y="${round(p.y)}" font-size="1" text-anchor="middle" fill="${color}">${mk.label}</text>`;
  }

  svg += `\n    </g>`;
  return svg;
}

function generateMarkings(markings, wellsById, doorsById) {
  if (!markings || markings.length === 0) return '';
  return markings.map((mk, i) => generateMarking(mk, i, wellsById, doorsById)).join('');
}

// Site features (§5.14, 2026-08-30; driveway added 2026-08-31): FACILITY-level plant on the
// property, not slab-bound (spec.siteFeatures[], a sibling of slabs/property). Site features
// are free geometry — ANY angles are legal (the §5.1 orthogonality rule binds slabs only).
// LINEAR kind FENCE: the architect's symbol — one 0.25-stroke line along the path with
// 0.5x0.5 square posts at every vertex and every FENCE_POST_PITCH ft along each segment (a
// render constant, not data). `closed` joins the last vertex back to the first; `gaps` are
// RESERVED and not drawn. Colors (Appendix A): wooden #6b4f2a, any other material #666.
// AREA kind DRIVEWAY: an always-closed filled polygon (`closed` unused), fill by surface
// (Appendix A) @0.8, stroke #888 0.1, drawn in <g id="site_ground"> BEFORE the slabs —
// driveways are ground, they run under aprons; fences stay in the after-slabs site group.
// parking is RESERVED — nothing is invented ahead of data, so it emits an empty group only.
const FENCE_POST_PITCH = 8;
// Appendix A driveway fills; an unlisted surface (e.g. 'other') gets a neutral #cccccc —
// no App. A row for it yet (flagged to the spec owner).
const DRIVEWAY_FILLS = { gravel: '#c9c2b2', asphalt: '#9a9a9a', concrete: '#c0c0c8', dirt: '#b59b7a' };
function generateSiteFeature(f, i) {
  const round = v => Math.round(v * 1000) / 1000;
  const id = f.id || `${f.kind || 'feature'}_${i}`;
  const attrs = `data-kind="${f.kind}"` + (f.material ? ` data-material="${f.material}"` : '')
    + (f.surface ? ` data-surface="${f.surface}"` : '') + sourceAttr(f);
  let svg = `\n    <g id="site_${id}" ${attrs}>`;
  if (f.kind === 'driveway' && Array.isArray(f.path) && f.path.length >= 3) {
    const fill = DRIVEWAY_FILLS[f.surface] || '#cccccc';
    const d = f.path.map((p, k) => `${k === 0 ? 'M' : 'L'} ${round(p.x)},${round(p.y)}`).join(' ') + ' Z';
    svg += `\n      <path d="${d}" fill="${fill}" fill-opacity="0.8" stroke="#888" stroke-width="0.1"/>`;
  } else if (f.kind === 'fence' && Array.isArray(f.path) && f.path.length >= 2) {
    const color = (f.material === 'wooden') ? '#6b4f2a' : '#666';
    const pts = f.closed ? [...f.path, f.path[0]] : f.path;
    const d = pts.map((p, k) => `${k === 0 ? 'M' : 'L'} ${round(p.x)},${round(p.y)}`).join(' ');
    svg += `\n      <path d="${d}" fill="none" stroke="${color}" stroke-width="0.25"/>`;
    const post = (x, y) => `\n      <rect x="${round(x - 0.25)}" y="${round(y - 0.25)}" width="0.5" height="0.5" fill="${color}"/>`;
    f.path.forEach(p => { svg += post(p.x, p.y); });                                             // posts at vertices
    for (let k = 0; k < pts.length - 1; k++) {                                                   // + every 8 ft per segment
      const a = pts[k], b = pts[k + 1];
      const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy);
      if (len < 1e-6) continue;
      for (let s = FENCE_POST_PITCH; s < len - 1e-6; s += FENCE_POST_PITCH) {
        svg += post(a.x + dx * s / len, a.y + dy * s / len);
      }
    }
  }
  svg += `\n    </g>`;
  return svg;
}

// AREA kinds draw under the slabs in site_ground; everything else in the after-slabs site group.
// (parking joins SITE_GROUND_KINDS when it is defined — it follows the driveway area model.)
const SITE_GROUND_KINDS = ['driveway'];

// Facility-level <g id="site">, drawn AFTER the slabs (on top — a fence is thin and crosses pavement).
function generateSiteFeatures(siteFeatures) {
  const inner = (siteFeatures || []).filter(f => !SITE_GROUND_KINDS.includes(f.kind))
    .map((f, i) => generateSiteFeature(f, i)).join('');
  return `\n  <g id="site">${inner}\n  </g>`;
}

// Facility-level <g id="site_ground">, drawn BEFORE the slabs (§5.14: driveways are ground —
// they run under aprons and up to the building).
function generateSiteGround(siteFeatures) {
  const inner = (siteFeatures || []).filter(f => SITE_GROUND_KINDS.includes(f.kind))
    .map((f, i) => generateSiteFeature(f, i)).join('');
  return `\n    <g id="site_ground">${inner}\n    </g>`;
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
// generateDoor). Drawn HOLLOW to match the perimeter (Appendix A: two 0.2 ft rails):
// each segment is two rails at the band edges, joined by centered 1x1 corner pieces at
// interior bends and capped at free ends. Fill (#00449e) is inherited from the enclosing
// walls group.
function generatePartitionWalls(partitionWalls) {
  if (!partitionWalls || partitionWalls.length === 0) return '';
  const T = 1, H = T / 2, RW = 0.2;   // band thickness, half, rail width
  let wallsSVG = '';

  for (const partition of partitionWalls) {
    const points = convertTurtleToPoints(partition);
    const dirs = (partition.segments || []).map(s => s.direction);
    const wallName = partition.id || 'unnamed';
    const nSeg = points.length - 1;
    wallsSVG += `
  <g id="partition_${wallName}"${sourceAttr(partition)}>`;

    // Interior corner pieces: a 1x1 piece centered on the vertex, type from the turn.
    for (let i = 1; i < points.length - 1; i++) {
      const cType = getCornerTypeFromDirections(dirs[i - 1], dirs[i]);
      const V = points[i];
      wallsSVG += cornerTemplates[cType](V.x - H, V.y - H, `${wallName}_corner${i}`);
    }

    // Segments: two rails at the band edges, inset H where a corner piece sits, capped
    // (a rail bridging the two edges) at free ends.
    for (let i = 0; i < nSeg; i++) {
      const a = points[i], b = points[i + 1];
      const startCorner = i > 0, endCorner = i < nSeg - 1;
      const sInset = startCorner ? H : 0, eInset = endCorner ? H : 0;

      if (a.y === b.y) {
        const east = b.x > a.x;
        const x0 = east ? a.x + sInset : a.x - sInset;
        const x1 = east ? b.x - eInset : b.x + eInset;
        const left = Math.min(x0, x1), right = Math.max(x0, x1), len = right - left;
        if (len > 0) {
          wallsSVG += `
    <rect x="${left}" y="${a.y - H}" width="${len}" height="${RW}"/>
    <rect x="${left}" y="${a.y + H - RW}" width="${len}" height="${RW}"/>`;
        }
        if (!startCorner) wallsSVG += `
    <rect x="${east ? a.x : a.x - RW}" y="${a.y - H}" width="${RW}" height="${T}"/>`;
        if (!endCorner) wallsSVG += `
    <rect x="${east ? b.x - RW : b.x}" y="${a.y - H}" width="${RW}" height="${T}"/>`;
      } else if (a.x === b.x) {
        const south = b.y > a.y;
        const y0 = south ? a.y + sInset : a.y - sInset;
        const y1 = south ? b.y - eInset : b.y + eInset;
        const top = Math.min(y0, y1), bottom = Math.max(y0, y1), len = bottom - top;
        if (len > 0) {
          wallsSVG += `
    <rect x="${a.x - H}" y="${top}" width="${RW}" height="${len}"/>
    <rect x="${a.x + H - RW}" y="${top}" width="${RW}" height="${len}"/>`;
        }
        if (!startCorner) wallsSVG += `
    <rect x="${a.x - H}" y="${south ? a.y : a.y - RW}" width="${T}" height="${RW}"/>`;
        if (!endCorner) wallsSVG += `
    <rect x="${a.x - H}" y="${south ? b.y - RW : b.y}" width="${T}" height="${RW}"/>`;
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
  const { name, location, property, slabs, siteFeatures = [] } = spec;

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

  // Generate SVG for each slab, plus the facility-level site features (§5.14):
  // area kinds (site_ground) under the slabs, linear kinds (site) on top.
  const slabsSVG = slabs.map(slab => generateSlabSVG(slab)).join('\n');
  const siteGroundSVG = generateSiteGround(siteFeatures);
  const siteSVG = generateSiteFeatures(siteFeatures);

  // Embed the source JSON
  const embeddedJSON = JSON.stringify(spec, null, 2);

  return `<svg width="${width}" height="${height}" viewBox="${minX} ${minY} ${viewBoxWidth} ${viewBoxHeight}" version="1.1" id="facility-svg" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <pattern id="wellHatch" width="1.5" height="1.5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="1.5" height="1.5" fill="#e8dcc0" fill-opacity="0.5"/>
      <line x1="0" y1="0" x2="0" y2="1.5" stroke="#a89060" stroke-width="0.15"/>
    </pattern>
  </defs>
  <g id="layer1">
    <!-- Property boundary -->
    <rect id="land" width="${viewBoxWidth}" height="${viewBoxHeight}" x="${minX}" y="${minY}" style="fill:#e0e0e0;fill-opacity:0.3;stroke:none"/>

    <!-- Site ground (5.14): area site features, drawn before the slabs -->${siteGroundSVG}

    ${slabsSVG}

    <!-- Site features (5.14): facility-level, drawn after the slabs -->${siteSVG}
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
  const { id, name, corners, walls = [], columns = [], doors = [], cameras = [], packingLines = [], truckWells = [], markings = [] } = slab;

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
  const linesSVG = generateLines(packingLines);
  const doorsById = {}; doors.forEach(d => { doorsById[d.id] = d; });
  const wellsById = {}; truckWells.forEach(w => { wellsById[w.id] = w; });
  const wellsSVG = generateWells(truckWells, doorsById);
  const markingsSVG = generateMarkings(markings, wellsById, doorsById);

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

      <!-- Truck Wells -->
      <g id="${id}_wells" style="display:inline">
        ${wellsSVG}
      </g>

      <!-- Markings -->
      <g id="${id}_markings" style="display:inline">
        ${markingsSVG}
      </g>

      <!-- Packing Lines -->
      <g id="${id}_lines" style="display:inline">
        ${linesSVG}
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
  const packingLines = [];
  const truckWells = [];
  const markings = [];
  const doorsBySlab = {};  // slabId -> { doorId -> door }, for truck-well door cross-reference
  const wellsBySlab = {};  // slabId -> Set(wellId), for truckBay-marking well cross-reference

  slabs.forEach(s => {
    doorsBySlab[s.id] = {};
    wellsBySlab[s.id] = new Set();
    (s.doors || []).forEach(d => { doors.push({ ...d, _slab: s.id }); doorsBySlab[s.id][d.id] = d; });
    (s.cameras || []).forEach(c => cameras.push({ ...c, _slab: s.id }));
    (s.columns || []).forEach(c => columns.push({ ...c, _slab: s.id }));
    (s.walls || []).forEach(w => { if (w.type === 'partition') partitionWalls.push({ ...w, _slab: s.id }); });
    (s.packingLines || []).forEach(pl => packingLines.push({ ...pl, _slab: s.id }));
    (s.truckWells || []).forEach(tw => { truckWells.push({ ...tw, _slab: s.id }); wellsBySlab[s.id].add(tw.id); });
    (s.markings || []).forEach((mk, i) => markings.push({ ...mk, _slab: s.id, _index: i }));
  });
  (spec.doors || []).forEach(d => doors.push(d));
  (spec.cameras || []).forEach(c => cameras.push(c));
  (spec.columns || []).forEach(c => columns.push(c));
  (spec.partitionWalls || []).forEach(w => partitionWalls.push(w));
  (spec.packingLines || []).forEach(pl => packingLines.push(pl));
  // Site features (§5.14) are FACILITY-level only — never under a slab.
  const siteFeatures = Array.isArray(spec.siteFeatures) ? spec.siteFeatures : [];

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
    // Instance-side fields (§5.4 ruling 2026-08-27, amended 2026-08-28): the hardware SIDE
    // must never be defaulted. Absent -> warn; 2D draws a neutral opening with
    // data-hardware-side='unknown'. cooler needs slideDirection; personnel needs
    // hingePosition (swingDirection DEPRECATED — swing is derived, always toward interior).
    if (door.type === 'cooler' && door.slideDirection === undefined) {
      violations.push({
        type: 'door',
        id: door.id,
        message: `Cooler door "${door.id}"${where(door)} missing slideDirection (left|right) - required; hardware side must not be defaulted`
      });
    }
    if (door.type === 'personnel' && door.hingePosition === undefined) {
      violations.push({
        type: 'door',
        id: door.id,
        message: `Personnel door "${door.id}"${where(door)} missing hingePosition (left|right) - required; hinge side must not be defaulted`
      });
    }
    if (door.type === 'conveyor' && door.sillHeight === undefined) {
      violations.push({
        type: 'door',
        id: door.id,
        message: `Conveyor door "${door.id}"${where(door)} missing sillHeight (ft) - required (§5.4.3)`
      });
    }
    if (door.approach !== undefined && door.approach !== 'dock' && door.approach !== 'grade') {
      violations.push({
        type: 'door',
        id: door.id,
        message: `Door "${door.id}"${where(door)} approach "${door.approach}" invalid - must be "dock" or "grade" (§5.4.2)`
      });
    }
    if (door.swingDirection !== undefined) {
      violations.push({
        type: 'door',
        id: door.id,
        message: `Door "${door.id}"${where(door)} has deprecated swingDirection - the swing is derived (always toward interior); remove it`
      });
    }
  });

  // Overlapping openings (§8.3, ruled 2026-08-28): two openings on the same wall side must
  // not overlap. Group by wallId + orientation + fixed axis coordinate (same side), then
  // check the along-wall extents (center ± openingWidth/2) are disjoint; name both doors.
  const doorGroups = {};
  doors.forEach(d => {
    if (d.openingWidth === undefined || d.x === undefined || d.y === undefined) return;
    const vertical = d.orientation === 'vertical';
    const key = `${d.wallId}|${vertical ? 'V' + d.x : 'H' + d.y}`;
    const c = vertical ? d.y : d.x, half = d.openingWidth / 2;
    (doorGroups[key] || (doorGroups[key] = [])).push({ id: d.id, lo: c - half, hi: c + half, _slab: d._slab });
  });
  for (const key of Object.keys(doorGroups)) {
    const g = doorGroups[key].sort((a, b) => a.lo - b.lo);
    let maxHi = -Infinity, maxId = null;
    for (const d of g) {
      if (d.lo < maxHi - 1e-9) {
        violations.push({
          type: 'door',
          id: d.id,
          message: `Overlapping openings on the same wall: "${maxId}" and "${d.id}"${where(d)} - door extents must be disjoint`
        });
      }
      if (d.hi > maxHi) { maxHi = d.hi; maxId = d.id; }
    }
  }

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

  // Validate packing lines (§5.11, 2026-08-29): river-name id; each element is a RUN
  // (path) or STATION (footprint). Packing-line coords may be DECIMAL — equipment is off
  // the foot grid (§5.11 amendment 2026-08-29), so the whole-foot rule does NOT apply here.
  // A RUN `shelf` needs a side (no default — no-guessed-side rule, like slideDirection).
  // Station kind is an OPEN list — only warn when kind='other' with no label.
  packingLines.forEach(line => {
    if (validNames.packingLines && !validNames.packingLines.includes(line.id)) {
      violations.push({
        type: 'packingLine',
        id: line.id,
        message: `Invalid packing-line name "${line.id}"${where(line)} - must use river name from naming conventions`,
        validNames: validNames.packingLines
      });
    }
    (line.elements || []).forEach((el, i) => {
      const isRun = Array.isArray(el.path);
      const isStation = el.footprint !== undefined;
      if (isRun) {
        if (el.path.length < 2) {
          violations.push({ type: 'packingLine', id: line.id,
            message: `Packing line "${line.id}"${where(line)} run element[${i}] needs a path of >=2 vertices` });
        }
        if (el.shelf && el.shelf.side !== 'left' && el.shelf.side !== 'right') {
          violations.push({ type: 'packingLine', id: line.id,
            message: `Packing line "${line.id}"${where(line)} run element[${i}] shelf needs side (left|right) - no default (§5.11)` });
        }
      } else if (isStation) {
        if (el.kind === 'other' && !el.label) {
          violations.push({ type: 'packingLine', id: line.id,
            message: `Packing line "${line.id}"${where(line)} station element[${i}] kind 'other' requires a label` });
        }
      } else {
        violations.push({ type: 'packingLine', id: line.id,
          message: `Packing line "${line.id}"${where(line)} element[${i}] is neither a run (needs path) nor a station (needs footprint)` });
      }
    });
  });

  // Validate truck wells (§5.12, 2026-08-29): lake-name id; served doors must exist on the
  // same slab and must be DOCK doors (a grade door gets no well, §5.4.2 approach).
  truckWells.forEach(well => {
    if (validNames.truckWells && !validNames.truckWells.includes(well.id)) {
      violations.push({
        type: 'truckWell',
        id: well.id,
        message: `Invalid truck-well name "${well.id}"${where(well)} - must use lake name from naming conventions`,
        validNames: validNames.truckWells
      });
    }
    const slabDoors = doorsBySlab[well._slab] || {};
    (well.doors || []).forEach(did => {
      const d = slabDoors[did];
      if (!d) {
        violations.push({ type: 'truckWell', id: well.id,
          message: `Truck well "${well.id}"${where(well)} references door "${did}" which does not exist on this slab` });
      } else if (d.approach === 'grade') {
        violations.push({ type: 'truckWell', id: well.id,
          message: `Truck well "${well.id}"${where(well)} serves door "${did}" which is a grade door - grade doors get no well (§5.12)` });
      }
    });
    // Well-vs-boundary (§8.3, 2026-08-29): when property.boundary is explicit, a well whose
    // footprint (out padLength+rampLength) exceeds it is a measurement error — the ramp runs
    // off the property.
    const bnd = spec.property && spec.property.boundary;
    if (bnd) {
      const r = wellRect(well, doorsBySlab[well._slab] || {});
      if (r) {
        const perp = [Math.min(r.wallCoord, r.outer), Math.max(r.wallCoord, r.outer)];
        const xs = r.alongIsX ? [r.alongMin, r.alongMax] : perp;
        const ys = r.alongIsX ? perp : [r.alongMin, r.alongMax];
        if (xs[0] < bnd.x || xs[1] > bnd.x + bnd.width || ys[0] < bnd.y || ys[1] > bnd.y + bnd.height) {
          violations.push({ type: 'truckWell', id: well.id,
            message: `Truck well "${well.id}"${where(well)} extent exceeds property.boundary - the ramp runs off the property (§8.3)` });
        }
      }
    }
  });

  // Validate markings (§5.13, reconciled 2026-08-29): two forms — (a) truckBay DERIVED from
  // a well ({well, width, color}, id '<well>-lanes'); (b) free-form line|lane|parking|hatch|
  // sign with a path/rect. Colors limited to yellow|white|red|blue. No name pool.
  const MARKING_KINDS = ['truckBay', 'line', 'lane', 'parking', 'hatch', 'sign'];
  const MARKING_COLORS = ['yellow', 'white', 'red', 'blue'];
  markings.forEach(mk => {
    const mid = mk.id || `${mk.kind}_${mk._index}`;
    if (!MARKING_KINDS.includes(mk.kind)) {
      violations.push({ type: 'marking', id: mid,
        message: `Marking "${mid}"${where(mk)} kind "${mk.kind}" invalid - must be one of ${MARKING_KINDS.join('/')} (§5.13)` });
    }
    if (mk.color !== undefined && !MARKING_COLORS.includes(mk.color)) {
      violations.push({ type: 'marking', id: mid,
        message: `Marking "${mid}"${where(mk)} color "${mk.color}" invalid - must be ${MARKING_COLORS.join('/')} (§5.13)` });
    }
    if (mk.kind === 'truckBay') {
      const wells = wellsBySlab[mk._slab] || new Set();
      if (!mk.well) {
        violations.push({ type: 'marking', id: mid,
          message: `truckBay marking "${mid}"${where(mk)} needs a "well" - stripes are derived from a well (§5.13)` });
      } else if (!wells.has(mk.well)) {
        violations.push({ type: 'marking', id: mid,
          message: `truckBay marking "${mid}"${where(mk)} references well "${mk.well}" which does not exist on this slab` });
      }
    }
  });

  // Validate site features (§5.14 + 8.3; driveway added 2026-08-31): facility-level;
  // mountain-name id (one pool for all kinds); kind in fence|driveway|parking (parking
  // RESERVED — no rules invented ahead of data, so only id/kind/source apply); `source` is
  // REQUIRED on every site feature (usually traced:aerial). A FENCE needs material (no
  // default — a guessed material is a false fact), whole-foot height, a path of >= 2
  // WHOLE-FOOT vertices; a DRIVEWAY needs surface (no default; 'other' needs a label) and a
  // path of >= 3 whole-foot vertices (a 2-point area is a line). Every vertex must sit
  // inside property.boundary when explicit (off the property = tracing error). DIAGONAL
  // EDGES ARE LEGAL on site features — the §5.1 slab orthogonality rule does NOT apply.
  const SITE_KINDS = ['fence', 'driveway', 'parking'];
  const seenSiteIds = new Set();
  siteFeatures.forEach((f, i) => {
    const fid = f.id || `${f.kind || 'feature'}_${i}`;
    if (validNames.siteFeatures && !validNames.siteFeatures.includes(f.id)) {
      violations.push({
        type: 'siteFeature',
        id: fid,
        message: `Invalid site-feature name "${f.id}" - must use mountain name from naming conventions`,
        validNames: validNames.siteFeatures
      });
    }
    if (seenSiteIds.has(fid)) {
      violations.push({ type: 'siteFeature', id: fid,
        message: `Duplicate site-feature ID "${fid}" - each site feature must have a unique identifier` });
    }
    seenSiteIds.add(fid);
    if (!SITE_KINDS.includes(f.kind)) {
      violations.push({ type: 'siteFeature', id: fid,
        message: `Site feature "${fid}" kind "${f.kind}" invalid - must be one of ${SITE_KINDS.join('/')} (§5.14)` });
    }
    if (f.source === undefined) {
      violations.push({ type: 'siteFeature', id: fid,
        message: `Site feature "${fid}" missing source - required (traced:aerial | george:<date> | estimate:<who>) (§5.14)` });
    }
    if (f.kind === 'fence') {
      if (f.material === undefined) {
        violations.push({ type: 'siteFeature', id: fid,
          message: `Fence "${fid}" missing material (wooden|chainlink|wire|block|other) - required; a material must not be defaulted (§5.14)` });
      }
      if (f.height === undefined) {
        violations.push({ type: 'siteFeature', id: fid,
          message: `Fence "${fid}" missing height (whole ft above grade) - required, no default (§5.14)` });
      } else {
        fracWarn('fence', fid, 'height', f.height, '');
      }
      if (!Array.isArray(f.path) || f.path.length < 2) {
        violations.push({ type: 'siteFeature', id: fid,
          message: `Fence "${fid}" needs a path of >= 2 vertices (§5.14)` });
      } else {
        f.path.forEach((p, k) => {
          fracWarn('fence', fid, `path[${k}].x`, p.x, '');
          fracWarn('fence', fid, `path[${k}].y`, p.y, '');
        });
        const bnd = spec.property && spec.property.boundary;
        if (bnd) {
          const off = f.path.filter(p => p.x < bnd.x || p.x > bnd.x + bnd.width || p.y < bnd.y || p.y > bnd.y + bnd.height);
          if (off.length) {
            violations.push({ type: 'siteFeature', id: fid,
              message: `Fence "${fid}" has ${off.length} vertex(es) outside property.boundary (first: ${off[0].x},${off[0].y}) - a fence off the property is a tracing error (§8.3)` });
          }
        }
      }
    } else if (f.kind === 'driveway') {
      if (f.surface === undefined) {
        violations.push({ type: 'siteFeature', id: fid,
          message: `Driveway "${fid}" missing surface (gravel|asphalt|concrete|dirt|other) - required; a surface must not be defaulted (§5.14)` });
      } else if (f.surface === 'other' && !f.label) {
        violations.push({ type: 'siteFeature', id: fid,
          message: `Driveway "${fid}" surface 'other' requires a label (§5.14)` });
      }
      if (!Array.isArray(f.path) || f.path.length < 3) {
        violations.push({ type: 'siteFeature', id: fid,
          message: `Driveway "${fid}" needs a path of >= 3 vertices - a 2-point area is a line (§5.14)` });
      } else {
        f.path.forEach((p, k) => {
          fracWarn('driveway', fid, `path[${k}].x`, p.x, '');
          fracWarn('driveway', fid, `path[${k}].y`, p.y, '');
        });
        const bnd = spec.property && spec.property.boundary;
        if (bnd) {
          const off = f.path.filter(p => p.x < bnd.x || p.x > bnd.x + bnd.width || p.y < bnd.y || p.y > bnd.y + bnd.height);
          if (off.length) {
            violations.push({ type: 'siteFeature', id: fid,
              message: `Driveway "${fid}" has ${off.length} vertex(es) outside property.boundary (first: ${off[0].x},${off[0].y}) - a driveway off the property is a tracing error (§8.3)` });
          }
        }
      }
    } else if (f.kind === 'parking') {
      violations.push({ type: 'siteFeature', id: fid,
        message: `Site feature "${fid}" kind "parking" is RESERVED (§5.14) - not defined or drawn yet; nothing rendered` });
    }
  });

  // Write violations to a SEPARATE file (Section 12 ruling 2026-08-29). NAMING_CONVENTIONS.json
  // is PURE pool content and is never rewritten by the generator — previously the violations
  // array was written back into it, so a `git checkout` to reset violations silently reverted
  // uncommitted pool additions. Violations now live in NAMING_VIOLATIONS.json.
  const violationsPath = path.join(skillDir, 'NAMING_VIOLATIONS.json');
  fs.writeFileSync(violationsPath, JSON.stringify({ generatedAt: new Date().toISOString(), violations }, null, 2));

  // Output violations to console
  if (violations.length > 0) {
    console.error('\n⚠️  VALIDATION WARNINGS:');
    violations.forEach(v => {
      console.error(`  - ${v.message}`);
    });
    console.error(`\n${violations.length} violation(s) found. Details written to NAMING_VIOLATIONS.json`);
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
