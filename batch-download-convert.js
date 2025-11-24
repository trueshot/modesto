const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const https = require('https');

// ============================================
// CONFIG
// ============================================
const OUTPUT_DIR = './zpl_output';
const IMAGES_DIR = path.join(OUTPUT_DIR, 'images');
const ZPL_DIR = path.join(OUTPUT_DIR, 'zpl');

const TAG_FAMILIES = {
  'tagStandard52h13': { prefix: 'tag52_13', count: 48814, dir: 'tagStandard52h13' },
  '36h11': { prefix: 'tag36_11', count: 2287, dir: 'tag36h11' },
  'tagStandard41h12': { prefix: 'tag41_12', count: 4295, dir: 'tagStandard41h12' }
};

const GITHUB_BASE = 'https://github.com/AprilRobotics/apriltag-imgs/raw/master';

/**
 * Download file and save to disk (with redirect follow)
 */
function downloadAndSave(url, filepath, redirects = 0) {
  return new Promise((resolve, reject) => {
    if (redirects > 5) {
      reject(new Error('Too many redirects'));
      return;
    }

    const dir = path.dirname(filepath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    https.get(url, (response) => {
      // Handle redirects
      if (response.statusCode === 301 || response.statusCode === 302 || response.statusCode === 303) {
        return downloadAndSave(response.headers.location, filepath, redirects + 1)
          .then(resolve)
          .catch(reject);
      }

      if (response.statusCode !== 200) {
        reject(new Error(`HTTP ${response.statusCode}`));
        return;
      }

      const file = fs.createWriteStream(filepath);
      response.pipe(file);
      file.on('finish', () => {
        file.close();
        resolve();
      });
      file.on('error', (err) => {
        fs.unlink(filepath, () => {});
        reject(err);
      });
    }).on('error', (err) => {
      fs.unlink(filepath, () => {});
      reject(err);
    });
  });
}

/**
 * Convert PNG to ZPL using simple-png-to-zpl.js
 */
function convertPngToZpl(imagePath, family, tagId) {
  return new Promise((resolve, reject) => {
    const child = spawn('node', [
      'simple-png-to-zpl.js',
      imagePath,
      family,
      String(tagId)
    ]);

    let output = '';
    let error = '';

    child.stdout.on('data', (data) => {
      output += data.toString();
    });

    child.stderr.on('data', (data) => {
      error += data.toString();
    });

    child.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(error || `Process exited with code ${code}`));
      }
    });
  });
}

/**
 * Main downloader
 */
async function main() {
  // Create directories
  [OUTPUT_DIR, IMAGES_DIR, ZPL_DIR].forEach(dir => {
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
  });

  // Parse arguments
  const args = process.argv.slice(2);
  let families = Object.keys(TAG_FAMILIES);
  let limit = 10;

  if (args.length > 0) {
    const lastArg = parseInt(args[args.length - 1]);
    if (!isNaN(lastArg)) {
      limit = lastArg;
      if (args.length > 1) {
        families = args.slice(0, -1).filter(f => TAG_FAMILIES[f]);
      }
    } else {
      families = args.filter(f => TAG_FAMILIES[f]);
    }
  }

  console.log('AprilTag Downloader & ZPL Converter\n');

  for (const family of families) {
    const familyInfo = TAG_FAMILIES[family];
    const actualLimit = Math.min(limit, familyInfo.count);

    console.log(`\n=== ${family} (${actualLimit} of ${familyInfo.count} tags) ===\n`);

    let processed = 0;
    let failed = 0;

    for (let tagId = 0; tagId < actualLimit; tagId++) {
      const filename = `${familyInfo.prefix}_${String(tagId).padStart(5, '0')}.png`;
      const url = `${GITHUB_BASE}/${familyInfo.dir}/${filename}`;
      const imagePath = path.join(IMAGES_DIR, filename);

      try {
        // Show progress
        if ((tagId + 1) % 10 === 0 || tagId === 0) {
          process.stdout.write(`\rDownloading & converting ${tagId + 1}/${actualLimit}...`);
        }

        // Download
        await downloadAndSave(url, imagePath);

        // Convert
        await convertPngToZpl(imagePath, family, tagId);

        processed++;
      } catch (err) {
        failed++;
        console.log(`\n  ✗ Tag ${tagId}: ${err.message}`);
      }
    }

    console.log(`\r                                      \r✓ Processed: ${processed}/${actualLimit}`);
    if (failed > 0) console.log(`✗ Failed: ${failed}`);
  }

  console.log('\n=== Summary ===');
  console.log(`ZPL files: ${ZPL_DIR}`);
  console.log('Done!');
}

// Show usage
if (process.argv.includes('--help') || process.argv.includes('-h')) {
  console.log(`
Usage: node batch-download-convert.js [count]

Examples:
  node batch-download-convert.js 50        # First 50 of each family
  node batch-download-convert.js 100       # First 100 of each
  node batch-download-convert.js 1000      # First 1000 of each
  `);
  process.exit(0);
}

main().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
