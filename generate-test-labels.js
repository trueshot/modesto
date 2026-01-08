#!/usr/bin/env node
/**
 * Generate test pallet labels with different AprilTag codes
 *
 * Takes aprtag.zpl template and swaps AprilTag QR patterns from labels/
 * Keeps all barcode data, text, and warehouse info identical
 * Perfect for camera/vision testing
 *
 * Usage: node generate-test-labels.js [tag-start] [count] [print]
 *   node generate-test-labels.js 650 3 true
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const TEMPLATE_FILE = './aprtag.zpl';
const LABELS_DIR = './zpl_output';  // 52h13 pallet tags
const OUTPUT_DIR = './test-output';

const startTag = parseInt(process.argv[2]) || 0;  // 52h13 starts at 0
const count = parseInt(process.argv[3]) || 1;
const doPrint = process.argv[4] === 'true' || process.argv[4] === '1';

console.log(`\n🏷️  Pallet Label Test Generator (Vision Testing)\n`);
console.log(`Template: ${TEMPLATE_FILE}`);
console.log(`AprilTag Range: ${startTag} - ${startTag + count - 1}`);
console.log(`Count: ${count}`);
console.log(`Print: ${doPrint ? 'YES' : 'NO (save only)'}`);
console.log('─'.repeat(50));

// Create output directory
if (!fs.existsSync(OUTPUT_DIR)) {
  fs.mkdirSync(OUTPUT_DIR);
  console.log(`\n✓ Created ${OUTPUT_DIR}/`);
}

// Read template
if (!fs.existsSync(TEMPLATE_FILE)) {
  console.error(`✗ Template not found: ${TEMPLATE_FILE}`);
  process.exit(1);
}

const template = fs.readFileSync(TEMPLATE_FILE, 'utf8');

// Split template into parts
// The AprilTag grid is at the end (after line 70 in original)
const lines = template.split('\n');
let templateStart = '';
let templateEnd = '';
let apriltAgSection = '';

// Find where the AprilTag section starts (look for ^FO75,75)
let apriltTagStart = -1;
for (let i = 0; i < lines.length; i++) {
  if (lines[i].includes('^FO75,75^GB165,165,165^FS')) {
    apriltTagStart = i;
    break;
  }
}

if (apriltTagStart === -1) {
  console.error('✗ Could not find AprilTag section in template');
  process.exit(1);
}

templateStart = lines.slice(0, apriltTagStart).join('\n');
apriltAgSection = lines.slice(apriltTagStart).join('\n');

console.log(`\n✓ Found AprilTag section at line ${apriltTagStart + 1}`);

// Get available AprilTag files (52h13 for pallets)
const tagFiles = fs.readdirSync(LABELS_DIR)
  .filter(f => f.endsWith('.zpl') && f.includes('tagStandard52h13'))
  .map(f => {
    const match = f.match(/_(\d+)\.zpl$/);
    return match ? { num: parseInt(match[1]), file: f } : null;
  })
  .filter(x => x !== null)
  .sort((a, b) => a.num - b.num);

console.log(`✓ Found ${tagFiles.length} AprilTag files`);

// Generate test labels
console.log(`\n📋 Generating ${count} test labels...\n`);

const generated = [];
for (let i = 0; i < count; i++) {
  const tagNum = startTag + i;
  const tagFileObj = tagFiles.find(t => t.num === tagNum);

  if (!tagFileObj) {
    console.warn(`⚠️  AprilTag ${tagNum} not found, skipping`);
    continue;
  }

  // Read the AprilTag QR code pattern
  const tagFilePath = path.join(LABELS_DIR, tagFileObj.file);
  const tagContent = fs.readFileSync(tagFilePath, 'utf8');

  // Extract just the QR pattern (skip header ^XA, ^LL, ^PW, and trailer ^XZ)
  const tagLines = tagContent.split('\n');
  let qrPattern = '';
  for (let j = 0; j < tagLines.length; j++) {
    const line = tagLines[j].trim();
    // Skip headers and footers, capture the box drawing commands
    if (line.startsWith('^FO') && line.includes('^GB')) {
      qrPattern += line + '\n';
    }
  }

  // Combine: template start + new QR pattern + template end
  const newLabel = templateStart + '\n' + qrPattern + '\n^XZ\n';

  // Save to file
  const outputFile = path.join(OUTPUT_DIR, `pallet_label_tag${tagNum}.zpl`);
  fs.writeFileSync(outputFile, newLabel);

  generated.push({
    tagNum,
    outputFile,
    filename: path.basename(outputFile)
  });

  console.log(`[${i + 1}/${count}] Generated: ${path.basename(outputFile)}`);
}

if (generated.length === 0) {
  console.error('\n✗ No labels generated');
  process.exit(1);
}

console.log(`\n✓ Generated ${generated.length} test labels in ${OUTPUT_DIR}/\n`);

// Print if requested
if (doPrint) {
  console.log('🖨️  Printing to Zebra ZT421...\n');

  generated.forEach((label, idx) => {
    try {
      console.log(`[${idx + 1}/${generated.length}] Printing: ${label.filename}`);
      execSync(`node print-zpl.js "${label.outputFile}"`, {
        stdio: 'pipe',
        cwd: process.cwd()
      });
      console.log(`  ✓ Sent`);
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
    }
  });

  console.log(`\n✓ Printed ${generated.length} labels!\n`);
} else {
  console.log('✓ Saved to test-output/ (ready to print)\n');
  console.log('To print these labels:');
  console.log(`  node generate-test-labels.js ${startTag} ${count} true\n`);
}

// Summary
console.log('═'.repeat(50));
console.log('TEST LABELS GENERATED');
console.log('─'.repeat(50));
console.log(`AprilTag codes: ${generated.map(l => l.tagNum).join(', ')}`);
console.log(`Product data: IDENTICAL (Broccoli Crowns, Lot 2223, etc.)`);
console.log(`Purpose: Test camera recognition with different QR patterns`);
console.log('═'.repeat(50) + '\n');
