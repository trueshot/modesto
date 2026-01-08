# AprilTag Test Labels for Warehouse Vision Testing

## What Are These?

AprilTag QR codes (files like `apriltag_tagStandard41h12_00650.zpl`) are visual markers used for computer vision testing. Each file contains a unique 2D pattern that camera/vision systems can detect and recognize.

## What We Did

We created a **camera/vision test suite** by:

1. **Taking the warehouse pallet label template** (`aprtag.zpl`)
   - Contains: barcodes, product data (Broccoli Crowns), lot numbers, warehouse info

2. **Extracting AprilTag patterns** from this directory
   - Each file has a unique QR-like code (650, 651, 652... 671)

3. **Generating 22 test labels** by:
   - Keeping all product data **identical** (same barcodes, same product info, same lot number)
   - Replacing the QR code pattern with **different AprilTag codes** (650-671)
   - This creates visual variation while keeping data constant

4. **Printing all 22 labels** to the Zebra ZT421 printer

## Why This Matters

This tests if your warehouse detection system (SAM3/Claude) can:
- ✓ Recognize the same product with different visual markers
- ✓ Read barcodes consistently across QR variations
- ✓ Handle lighting, angles, and visual noise
- ✓ Work with real-world label variations

## How to Print Test Labels

### Print all AprilTag labels (650-671)
```bash
cd C:\clients\asksam
node generate-test-labels.js 650 22 true
```

### Print a subset
```bash
# Print tags 650-659 (10 labels)
node generate-test-labels.js 650 10 true

# Print tags 660-671 (12 labels)
node generate-test-labels.js 660 12 true
```

### Preview without printing
```bash
node generate-test-labels.js 650 5
```

## Generated Test Labels

All test labels are saved in `test-output/` directory:
```
test-output/
├── pallet_label_tag650.zpl
├── pallet_label_tag651.zpl
├── pallet_label_tag652.zpl
... (up to 671)
```

Each label contains:
- **AprilTag QR code**: Different for each label (650, 651, etc.)
- **Product data**: IDENTICAL across all labels
  - Product: Broccoli Crowns
  - Lot No: 2223
  - Date: 11/22/25
  - Warehouse: MAIN
  - Barcode: (01)10850806002923(13)251122(10)2223
  - PLU: 3082
  - Pallet No: 148837

## Available AprilTag Files

This directory contains 22 AprilTag files:
- `apriltag_tagStandard41h12_00650.zpl` through `apriltag_tagStandard41h12_00671.zpl`

Each file is a standalone ZPL graphic pattern that can be:
- Used as a test QR code
- Inserted into labels for variation testing
- Printed for camera calibration

## Testing Workflow

1. **Print the labels**
   ```bash
   node generate-test-labels.js 650 22 true
   ```

2. **Take photos** of the printed labels with your camera/warehouse system

3. **Test detection** - run warehouse detection on the photos:
   ```javascript
   const client = new AskSAMClient();
   const result = await client.analyzeImage('photo_of_label.jpg');
   console.log(result.summary);
   ```

4. **Verify results** - check that:
   - AprilTag QR code is detected (camera vision)
   - Barcodes are readable consistently
   - Product data matches (Broccoli Crowns, Lot 2223)
   - Detection works across all 22 label variations

## Helper Scripts

### `generate-test-labels.js`
Main script that creates test labels by combining:
- **aprtag.zpl** template (warehouse pallet label)
- **AprilTag files** from this directory (different QR codes)

```bash
# Usage
node generate-test-labels.js <start-tag> <count> [true|false]

# Examples
node generate-test-labels.js 650 3 true      # Print tags 650-652
node generate-test-labels.js 665 7 true      # Print tags 665-671
```

### `print-zpl.js`
Low-level printer command - sends ZPL files to Zebra ZT421 using Windows printer API (raw mode)

```bash
node print-zpl.js pallet_label_tag650.zpl
```

## Key Files

| File | Purpose |
|------|---------|
| `aprtag.zpl` | Warehouse pallet label template |
| `apriltag_tagStandard41h12_*.zpl` | Individual AprilTag QR patterns |
| `generate-test-labels.js` | Script to create & print test labels |
| `print-zpl.js` | Script to send ZPL to printer |
| `test-output/` | Generated test labels (after running script) |

## Technical Details

### Why AprilTags?
- Robust QR-like codes that work in poor lighting/angles
- Computer vision systems can detect them reliably
- Each has a unique 12-bit ID (41h12 = 41-bit payload, 12-bit ID)
- Perfect for warehouse automation testing

### Why Test Variation?
- Real warehouses have lighting variations
- Labels get damaged, angles change
- Vision system needs to work across variations
- AprilTag approach tests robustness without changing the actual product data

### Printer Details
- **Printer**: Zebra ZT421 (300 DPI)
- **Connection**: USB
- **Sending method**: Windows Printer API (RAW mode, not print spooler)
- **Label size**: 2400x1800 dots

## Next Steps When You Return

1. **If you need more labels**, edit `generate-test-labels.js` to use different products
2. **If labels aren't printing**, check:
   - Zebra printer is connected and powered on
   - Run: `node print-zpl.js test-output/pallet_label_tag650.zpl`
3. **To test vision system**, use Claude Code with:
   ```javascript
   const client = new AskSAMClient();
   const result = await client.analyzeImage('photo_of_label.jpg');
   ```

## Questions?

This was created for **camera/vision system testing** with warehouse pallet labels.
- AprilTags provide visual variation
- Product data stays identical
- Tests robustness of detection across QR code changes

---

*Created: 2025-11-23*
*Purpose: Warehouse Vision AI Testing*
*Status: 22 test labels printed and ready for testing*
