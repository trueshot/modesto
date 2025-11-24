const fs = require('fs');
const path = require('path');

// ============================================
// CONFIGURATION
// ============================================
const PRINTER_DPI = 300;
const TAG_WIDTH_INCHES = 5.5;
const LEFT_MARGIN_INCHES = 0.25;
const TOP_MARGIN_INCHES = 0.25;
const BOTTOM_MARGIN_INCHES = 2.25;

// Convert inches to dots (DPI)
const inchesToDots = (inches) => Math.round(inches * PRINTER_DPI);

const TAG_WIDTH_DOTS = inchesToDots(TAG_WIDTH_INCHES);              // 1650
const LEFT_MARGIN_DOTS = inchesToDots(LEFT_MARGIN_INCHES);          // 75
const TOP_MARGIN_DOTS = inchesToDots(TOP_MARGIN_INCHES);            // 75
const CELL_SIZE_DOTS = TAG_WIDTH_DOTS / 10;  // 165 dots per cell for 10x10 grid

// Output directory
const OUTPUT_DIR = './zpl_output';

/**
 * Parse SVG viewBox (0-100 x 0-100) where each cell = 10 units
 * Returns a 10x10 boolean grid: true = black, false = white
 */
function parseSvgToGrid(svgContent) {
  const grid = Array(10).fill(null).map(() => Array(10).fill(false));

  // Find all path elements with black fill
  const blackPathRegex = /<path[^>]*style="[^"]*fill:#000000[^"]*"[^>]*d="([^"]*)"/g;
  let match;

  while ((match = blackPathRegex.exec(svgContent)) !== null) {
    const pathData = match[1];
    const cells = parsePathDataToCells(pathData);

    // Mark cells as black
    for (const cell of cells) {
      if (cell.x >= 0 && cell.x < 10 && cell.y >= 0 && cell.y < 10) {
        grid[cell.y][cell.x] = true;
      }
    }
  }

  return grid;
}

/**
 * Extract vertices from SVG path data
 */
function extractVertices(pathData) {
  const vertices = [];
  // Match M and L commands with coordinates
  const cmdRegex = /([ML])\s*([\d.]+)\s+([\d.]+)/g;
  let match;

  while ((match = cmdRegex.exec(pathData)) !== null) {
    vertices.push({
      x: parseFloat(match[2]),
      y: parseFloat(match[3])
    });
  }

  return vertices;
}

/**
 * Ray casting algorithm for point-in-polygon test
 */
function isPointInPolygon(point, vertices) {
  let inside = false;
  let p1 = vertices[vertices.length - 1];

  for (let p2 of vertices) {
    if ((p2.y > point.y) !== (p1.y > point.y) &&
        point.x < (p1.x - p2.x) * (point.y - p2.y) / (p1.y - p2.y) + p2.x) {
      inside = !inside;
    }
    p1 = p2;
  }

  return inside;
}

/**
 * Parse SVG path data to extract which grid cells it covers
 * Uses point-in-polygon test for each grid cell
 */
function parsePathDataToCells(pathData) {
  const vertices = extractVertices(pathData);
  const cells = new Set();

  if (vertices.length < 3) return Array.from(cells);

  // Test each grid cell's center point
  for (let cellY = 0; cellY < 10; cellY++) {
    for (let cellX = 0; cellX < 10; cellX++) {
      // Test center of cell (cellX*10+5, cellY*10+5)
      const cellCenterX = cellX * 10 + 5;
      const cellCenterY = cellY * 10 + 5;

      if (isPointInPolygon({ x: cellCenterX, y: cellCenterY }, vertices)) {
        cells.add(`${cellX},${cellY}`);
      }
    }
  }

  return Array.from(cells).map(c => {
    const [x, y] = c.split(',').map(Number);
    return { x, y };
  });
}

/**
 * Find rectangular regions of black cells
 * This optimizes by combining adjacent cells into larger rectangles
 */
function findRectangles(grid) {
  const processed = Array(10).fill(null).map(() => Array(10).fill(false));
  const rectangles = [];

  for (let y = 0; y < 10; y++) {
    for (let x = 0; x < 10; x++) {
      if (grid[y][x] && !processed[y][x]) {
        // Find the largest rectangle starting from this cell
        const rect = findMaxRectangle(grid, processed, x, y);
        if (rect) {
          rectangles.push(rect);
          // Mark as processed
          for (let py = rect.y; py < rect.y + rect.height; py++) {
            for (let px = rect.x; px < rect.x + rect.width; px++) {
              processed[py][px] = true;
            }
          }
        }
      }
    }
  }

  return rectangles;
}

/**
 * Find the maximum rectangle of black cells starting at (x, y)
 */
function findMaxRectangle(grid, processed, startX, startY) {
  if (!grid[startY][startX] || processed[startY][startX]) {
    return null;
  }

  // Find width
  let width = 1;
  while (startX + width < 10 && grid[startY][startX + width] && !processed[startY][startX + width]) {
    width++;
  }

  // Find height
  let height = 1;
  while (startY + height < 10) {
    let canExtend = true;
    for (let x = startX; x < startX + width; x++) {
      if (!grid[startY + height][x] || processed[startY + height][x]) {
        canExtend = false;
        break;
      }
    }
    if (!canExtend) break;
    height++;
  }

  return {
    x: startX,
    y: startY,
    width: width,
    height: height
  };
}

/**
 * Convert grid rectangles to ZPL commands
 */
function generateZplFromRectangles(rectangles) {
  let zpl = `^XA
^LL${TOP_MARGIN_DOTS + TAG_WIDTH_DOTS + 675}
^PW1800
`;

  for (const rect of rectangles) {
    const xDots = LEFT_MARGIN_DOTS + Math.round(rect.x * CELL_SIZE_DOTS);
    const yDots = TOP_MARGIN_DOTS + Math.round(rect.y * CELL_SIZE_DOTS);
    const widthDots = Math.round(rect.width * CELL_SIZE_DOTS);
    const heightDots = Math.round(rect.height * CELL_SIZE_DOTS);

    // ^FO = set position, ^GB = draw box (filled if thickness = 0)
    zpl += `^FO${xDots},${yDots}^GB${widthDots},${heightDots},0,B^FS\n`;
  }

  zpl += `^XZ`;
  return zpl;
}

/**
 * Print grid as ASCII for debugging
 */
function printGrid(grid) {
  console.log('\n10x10 Grid (■ = black, □ = white):');
  for (let y = 0; y < 10; y++) {
    let row = '';
    for (let x = 0; x < 10; x++) {
      row += grid[y][x] ? '■ ' : '□ ';
    }
    console.log(row);
  }
  console.log('');
}

/**
 * Main conversion function
 */
function convertSvgToZpl(svgContent, tagFamily, tagId) {
  // Parse SVG to grid
  const grid = parseSvgToGrid(svgContent);
  printGrid(grid);

  // Find rectangles
  const rectangles = findRectangles(grid);
  console.log(`Found ${rectangles.length} black rectangles:\n`);

  rectangles.forEach((rect, i) => {
    const cellSize = CELL_SIZE_DOTS;
    console.log(`  ${i + 1}. Cell(${rect.x},${rect.y}) ${rect.width}x${rect.height} → ` +
      `${Math.round(rect.x * cellSize)}dp, ${Math.round(rect.y * cellSize)}dp ` +
      `${Math.round(rect.width * cellSize)}x${Math.round(rect.height * cellSize)}dp`);
  });
  console.log('');

  // Generate ZPL
  const zpl = generateZplFromRectangles(rectangles);

  // Save
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  const zplFilename = `apriltag_${tagFamily}_${String(tagId).padStart(5, '0')}.zpl`;
  const zplPath = path.join(OUTPUT_DIR, zplFilename);
  fs.writeFileSync(zplPath, zpl);

  console.log(`✓ Generated: ${zplPath}`);
  console.log(`\nZPL Preview (first 500 chars):\n${zpl.substring(0, 500)}...\n`);

  return zplPath;
}

// ============================================
// TEST (only run if this script is executed directly)
// ============================================

if (require.main === module) {
  const sampleSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">
<path style="fill:#000000; stroke:none;" d="M0 0L0 10L10 10L10 100L20 100L20 90L30 90L30 100L50 100L50 90L70 90L70 100L80 100L80 90L90 90L90 100L100 100L100 60L90 60L90 50L100 50L100 40L90 40L90 30L100 30L100 10L90 10L90 0L20 0L20 10L10 10L10 0L0 0z"></path>
<path style="fill:#ffffff; stroke:none;" d="M10 0L10 10L20 10L20 0L10 0M90 0L90 10L100 10L100 0L90 0M0 10L0 100L10 100L10 10L0 10M20 20L20 80L80 80L80 20L20 20z"></path>
<path style="fill:#000000; stroke:none;" d="M50 30L50 40L30 40L30 70L60 70L60 40L70 40L70 30L50 30z"></path>
<path style="fill:#ffffff; stroke:none;" d="M90 30L90 40L100 40L100 30L90 30M40 50L40 60L50 60L50 50L40 50M90 50L90 60L100 60L100 50L90 50M20 90L20 100L30 100L30 90L20 90M50 90L50 100L70 100L70 90L50 90M80 90L80 100L90 100L90 90L80 90z"></path>
</svg>`;

  console.log('=== SVG AprilTag to ZPL Converter ===\n');
  convertSvgToZpl(sampleSvg, 'tagStandard52h13', 0);

  console.log('\nZPL Specifications:');
  console.log(`  Grid: 10x10 cells`);
  console.log(`  Cell size: ${Math.round(CELL_SIZE_DOTS)}dp (${(CELL_SIZE_DOTS / PRINTER_DPI).toFixed(2)}")`);
  console.log(`  Tag size: ${TAG_WIDTH_DOTS}dp (${TAG_WIDTH_INCHES}")`);
  console.log(`  Margins: L=${LEFT_MARGIN_DOTS}dp T=${TOP_MARGIN_DOTS}dp B=675dp`);
}

module.exports = { parseSvgToGrid, findRectangles, convertSvgToZpl, generateZplFromRectangles };
