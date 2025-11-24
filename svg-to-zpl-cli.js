#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

// Import the parser
const { parseSvgToGrid, findRectangles, generateZplFromRectangles } = require('./svg-grid-parser.js');

const OUTPUT_DIR = './zpl_output';

/**
 * CLI tool for converting SVG AprilTag to ZPL
 */
function main() {
  const args = process.argv.slice(2);

  if (args.length < 1 || args[0] === '--help' || args[0] === '-h') {
    printUsage();
    process.exit(args.length < 1 ? 1 : 0);
  }

  // First argument is SVG file path
  const svgPath = args[0];
  const tagFamily = args[1] || 'tagStandard52h13';
  const tagId = parseInt(args[2]) || 0;

  if (!fs.existsSync(svgPath)) {
    console.error(`Error: File not found: ${svgPath}`);
    process.exit(1);
  }

  if (isNaN(tagId) || tagId < 0 || tagId > 48813) {
    console.error('Error: Tag ID must be between 0 and 48813');
    process.exit(1);
  }

  try {
    const svgContent = fs.readFileSync(svgPath, 'utf-8');
    convertAndSave(svgContent, tagFamily, tagId, svgPath);
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  }
}

/**
 * Parse and save SVG as ZPL
 */
function convertAndSave(svgContent, tagFamily, tagId, sourceFile) {
  console.log(`\n=== SVG to ZPL Converter ===`);
  console.log(`Source: ${sourceFile}`);
  console.log(`Family: ${tagFamily}, ID: ${tagId}\n`);

  // Parse SVG to grid
  const grid = parseSvgToGrid(svgContent);

  // Print grid visualization
  printGrid(grid);

  // Find rectangles
  const rectangles = findRectangles(grid);
  console.log(`Found ${rectangles.length} black rectangles:\n`);

  // Show rectangles
  const CELL_SIZE_DOTS = 165; // 1650 / 10
  rectangles.forEach((rect, i) => {
    console.log(
      `  ${i + 1}. Cell(${rect.x},${rect.y}) ` +
      `${rect.width}×${rect.height} → ` +
      `${Math.round(rect.x * CELL_SIZE_DOTS) + 75}dp, ` +
      `${Math.round(rect.y * CELL_SIZE_DOTS) + 75}dp ` +
      `${Math.round(rect.width * CELL_SIZE_DOTS)}×${Math.round(rect.height * CELL_SIZE_DOTS)}dp`
    );
  });

  // Generate ZPL
  const zpl = generateZplFromRectangles(rectangles);

  // Save ZPL
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  const zplFilename = `apriltag_${tagFamily}_${String(tagId).padStart(5, '0')}.zpl`;
  const zplPath = path.join(OUTPUT_DIR, zplFilename);
  fs.writeFileSync(zplPath, zpl);

  console.log(`\n✓ Generated: ${zplPath}`);
  console.log(`\nZPL Commands (copy to printer):\n`);
  console.log(zpl);
  console.log('\n');
}

/**
 * Print grid as ASCII
 */
function printGrid(grid) {
  console.log('Grid (■ = black, □ = white):');
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
 * Print usage info
 */
function printUsage() {
  console.log(`
Usage: node svg-to-zpl-cli.js <svg-file> [family] [tagId]

Arguments:
  <svg-file>     Path to SVG AprilTag file (required)
  [family]       Tag family (default: tagStandard52h13)
                 Options: tagStandard52h13, 36h11, tagStandard41h12
  [tagId]        Tag ID (default: 0)
                 Range: 0-48813 (for 52h13)

Examples:
  node svg-to-zpl-cli.js apriltag.svg
  node svg-to-zpl-cli.js apriltag.svg tagStandard52h13 12345
  node svg-to-zpl-cli.js apriltag.svg 36h11 500

Output:
  Generated ZPL files saved to ./zpl_output/
  `);
}

main();
