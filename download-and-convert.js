const { Jimp } = require('jimp');
const fs = require('fs');
const path = require('path');
const https = require('https');

// ============================================
// CONFIG
// ============================================
const PRINTER_DPI = 300;
const TAG_WIDTH_INCHES = 5.5;
const LEFT_MARGIN_INCHES = 0.25;
const TOP_MARGIN_INCHES = 0.25;

const inchesToDots = (inches) => Math.round(inches * PRINTER_DPI);
const TAG_WIDTH_DOTS = inchesToDots(TAG_WIDTH_INCHES);
const LEFT_MARGIN_DOTS = inchesToDots(LEFT_MARGIN_INCHES);
const TOP_MARGIN_DOTS = inchesToDots(TOP_MARGIN_INCHES);

const OUTPUT_DIR = './zpl_output';
const IMAGES_DIR = path.join(OUTPUT_DIR, 'images');
const ZPL_DIR = path.join(OUTPUT_DIR, 'zpl');

// Tag families
const TAG_FAMILIES = {
  'tagStandard52h13': { prefix: 'tag52_13', count: 48814, dir: 'tagStandard52h13' },
  '36h11': { prefix: 'tag36_11', count: 2287, dir: 'tag36h11' },
  'tagStandard41h12': { prefix: 'tag41_12', count: 4295, dir: 'tagStandard41h12' }
};

const GITHUB_BASE = 'https://github.com/AprilRobotics/apriltag-imgs/raw/master';

/**
 * Download a file via HTTPS and return as buffer
 */
function downloadFile(url) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    https.get(url, (response) => {
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => {
        resolve(Buffer.concat(chunks));
      });
    }).on('error', reject);
  });
}

/**
 * Convert PNG buffer to ZPL
 */
async function pngToZpl(imageBuffer, tagFamily, tagId) {
  try {
    let image = await Jimp.read(imageBuffer);

    const gridSize = image.width;
    const cellSize = TAG_WIDTH_DOTS / gridSize;

    let zplCommands = [];
    let blackCells = 0;

    for (let row = 0; row < gridSize; row++) {
      for (let col = 0; col < gridSize; col++) {
        const pixelColor = image.getPixelColor(col, row);
        const r = (pixelColor >> 24) & 0xFF;
        const g = (pixelColor >> 16) & 0xFF;
        const b = (pixelColor >> 8) & 0xFF;

        if ((r + g + b) < 384) {
          blackCells++;
          const x = LEFT_MARGIN_DOTS + Math.round(col * cellSize);
          const y = TOP_MARGIN_DOTS + Math.round(row * cellSize);
          const w = Math.round(cellSize);
          const h = Math.round(cellSize);

          zplCommands.push(`^FO${x},${y}^GB${w},${h},165^FS`);
        }
      }
    }

    const labelHeight = TOP_MARGIN_DOTS + TAG_WIDTH_DOTS + 675;
    let zpl = `^XA\n^LL${labelHeight}\n^PW1800\n`;
    zpl += `^FO75,10^AH12,10^FD${String(tagId).padStart(5, '0')}^FS\n`;
    zpl += zplCommands.join('\n');
    zpl += '\n^XZ';

    return { zpl, blackCells, gridSize };
  } catch (err) {
    throw err;
  }
}

/**
 * Download and convert one tag
 */
async function processTag(family, familyInfo, tagId) {
  const filename = `${familyInfo.prefix}_${String(tagId).padStart(5, '0')}.png`;
  const url = `${GITHUB_BASE}/${familyInfo.dir}/${filename}`;
  const zplFilename = `apriltag_${family}_${String(tagId).padStart(5, '0')}.zpl`;
  const zplPath = path.join(ZPL_DIR, zplFilename);

  try {
    // Download image to buffer
    const imageBuffer = await downloadFile(url);

    // Convert to ZPL
    const { zpl, blackCells, gridSize } = await pngToZpl(imageBuffer, family, tagId);

    // Save ZPL
    fs.writeFileSync(zplPath, zpl);

    return { success: true, tagId, blackCells, gridSize };
  } catch (err) {
    return { success: false, tagId, error: err.message };
  }
}

/**
 * Main: Download and convert all tags
 */
async function main() {
  // Create directories
  [OUTPUT_DIR, IMAGES_DIR, ZPL_DIR].forEach(dir => {
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
  });

  console.log('AprilTag Downloader & ZPL Converter\n');
  console.log('This will download all AprilTag images and convert to ZPL.\n');

  // Parse arguments
  const args = process.argv.slice(2);
  let families = Object.keys(TAG_FAMILIES);
  let limit = 10;

  if (args.length > 0) {
    // Check if last arg is a number (count)
    const lastArg = parseInt(args[args.length - 1]);
    if (!isNaN(lastArg)) {
      limit = lastArg;
      // If there are other args, they're family names
      if (args.length > 1) {
        families = args.slice(0, -1).filter(f => TAG_FAMILIES[f]);
      }
    } else {
      // All args are family names
      families = args.filter(f => TAG_FAMILIES[f]);
      limit = 10;
    }
  }

  for (const family of families) {
    const familyInfo = TAG_FAMILIES[family];
    console.log(`\n=== ${family} (${familyInfo.count} tags) ===`);
    console.log(`Downloading and converting...`);

    let processed = 0;
    let failed = 0;

    for (let tagId = 0; tagId < Math.min(limit, familyInfo.count); tagId++) {
      const result = await processTag(family, familyInfo, tagId);

      if (result.success) {
        processed++;
        if ((tagId + 1) % 5 === 0) {
          process.stdout.write(`\r  Processed ${tagId + 1}/${Math.min(limit, familyInfo.count)}`);
        }
      } else {
        failed++;
        console.log(`\n  ✗ Tag ${tagId}: ${result.error}`);
      }
    }

    console.log(`\n  ✓ Processed: ${processed}`);
    if (failed > 0) console.log(`  ✗ Failed: ${failed}`);
  }

  console.log('\n=== Summary ===');
  console.log(`Images: ${IMAGES_DIR}`);
  console.log(`ZPL files: ${ZPL_DIR}`);
  console.log('\nDone!');
}

// Show usage if no args
const args = process.argv.slice(2);
if (args.includes('--help') || args.includes('-h')) {
  console.log(`
Usage: node download-and-convert.js [families...] [count]

Examples:
  node download-and-convert.js                    # Download 10 of each family
  node download-and-convert.js 52                 # Download first 52 of each
  node download-and-convert.js tagStandard52h13 100  # Download first 100 of 52h13
  node download-and-convert.js 36h11 500          # Download first 500 of 36h11

Families:
  tagStandard52h13 (0-48,813)
  36h11 (0-2,286)
  tagStandard41h12 (0-4,294)
  `);
  process.exit(0);
}

main().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
