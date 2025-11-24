const { Jimp } = require('jimp');
const fs = require('fs');
const path = require('path');

// ============================================
// CONFIG
// ============================================
const PRINTER_DPI = 300;
const TAG_WIDTH_INCHES = 5.5;
const LEFT_MARGIN_INCHES = 0.25;
const TOP_MARGIN_INCHES = 0.25;

const inchesToDots = (inches) => Math.round(inches * PRINTER_DPI);
const TAG_WIDTH_DOTS = inchesToDots(TAG_WIDTH_INCHES);  // 1650
const LEFT_MARGIN_DOTS = inchesToDots(LEFT_MARGIN_INCHES);  // 75
const TOP_MARGIN_DOTS = inchesToDots(TOP_MARGIN_INCHES);  // 75

const OUTPUT_DIR = './zpl_output';

/**
 * Convert PNG AprilTag image to ZPL
 */
async function pngToZpl(imagePath, tagFamily, tagId) {
  try {
    // Load image
    let image = await Jimp.read(imagePath);

    // AprilTags are square, get the size
    const gridSize = image.width;  // Assume square
    const cellSize = TAG_WIDTH_DOTS / gridSize;

    console.log(`\nImage: ${path.basename(imagePath)}`);
    console.log(`Grid size: ${gridSize}×${gridSize}`);
    console.log(`Cell size: ${cellSize.toFixed(1)} dots`);

    // Scan image: black = draw rectangle, white = skip
    let blackCells = 0;
    let zplCommands = [];

    for (let row = 0; row < gridSize; row++) {
      for (let col = 0; col < gridSize; col++) {
        // Get pixel value directly
        const pixelColor = image.getPixelColor(col, row);

        // Extract RGB components (ignore alpha)
        const r = (pixelColor >> 24) & 0xFF;
        const g = (pixelColor >> 16) & 0xFF;
        const b = (pixelColor >> 8) & 0xFF;

        // If mostly black (R+G+B < 384 out of 765 max)
        if ((r + g + b) < 384) {
          blackCells++;

          // Calculate position and size
          const x = LEFT_MARGIN_DOTS + Math.round(col * cellSize);
          const y = TOP_MARGIN_DOTS + Math.round(row * cellSize);
          const w = Math.round(cellSize);
          const h = Math.round(cellSize);

          // ZPL: ^FO = position, ^GB = box (width,height,thickness) - thickness=165 fills solid
          zplCommands.push(`^FO${x},${y}^GB${w},${h},165^FS`);
        }
      }
    }

    console.log(`Black cells: ${blackCells} / ${gridSize * gridSize}\n`);

    // Build ZPL
    const labelHeight = TOP_MARGIN_DOTS + TAG_WIDTH_DOTS + 675;  // +675 for bottom margin
    let zpl = `^XA\n^LL${labelHeight}\n^PW1800\n`;
    // Add tag number at top
    zpl += `^FO75,10^A0N,12,12^FD${String(tagId).padStart(5, '0')}^FS\n`;
    zpl += zplCommands.join('\n');
    zpl += '\n^XZ';

    // Save
    if (!fs.existsSync(OUTPUT_DIR)) {
      fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    }

    const filename = `apriltag_${tagFamily}_${String(tagId).padStart(5, '0')}.zpl`;
    const zplPath = path.join(OUTPUT_DIR, filename);
    fs.writeFileSync(zplPath, zpl);

    console.log(`✓ Saved: ${zplPath}`);
    console.log(`\nZPL (${zplCommands.length} rectangles):\n`);
    console.log(zpl.substring(0, 500) + (zpl.length > 500 ? '...' : ''));

    return zplPath;
  } catch (err) {
    console.error(`Error: ${err.message}`);
    throw err;
  }
}

// ============================================
// CLI
// ============================================

const args = process.argv.slice(2);

if (args.length < 1 || args[0] === '-h' || args[0] === '--help') {
  console.log(`
Usage: node simple-png-to-zpl.js <png-file> [family] [tagId]

Arguments:
  <png-file>  AprilTag PNG image
  [family]    Tag family (default: tagStandard52h13)
  [tagId]     Tag ID (default: 0)

Example:
  node simple-png-to-zpl.js tag52_13_00001.png tagStandard52h13 1
  `);
  process.exit(args.length < 1 ? 1 : 0);
}

const pngFile = args[0];
const family = args[1] || 'tagStandard52h13';
const tagId = parseInt(args[2]) || 0;

if (!fs.existsSync(pngFile)) {
  console.error(`Error: File not found: ${pngFile}`);
  process.exit(1);
}

pngToZpl(pngFile, family, tagId).catch(err => {
  console.error(err);
  process.exit(1);
});
