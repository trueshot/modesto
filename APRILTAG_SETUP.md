# AprilTag ZPL Printer Setup

Generate AprilTag codes for Zebra ZT421 printer (300 DPI, 6" width).

## Installation

```bash
npm install
```

## Usage

### Generate 5 Example ZPL Files

```bash
npm run generate
```

This creates:
- `zpl_output/apriltag_tagStandard52h13_00000.zpl` + image
- `zpl_output/apriltag_tagStandard52h13_01000.zpl` + image
- `zpl_output/apriltag_36h11_00000.zpl` + image
- `zpl_output/apriltag_36h11_05000.zpl` + image
- `zpl_output/apriltag_tagStandard41h12_00000.zpl` + image

### Generate Any Tag On Demand

```bash
node generate-tag.js tagStandard52h13 12345
node generate-tag.js 36h11 500
node generate-tag.js tagStandard41h12 999
```

## Output

- **ZPL Files**: Text files ready to send to printer
- **Images**: PNG files referenced by ZPL commands

## Specifications

| Parameter | Value |
|-----------|-------|
| Printer | Zebra ZT421 |
| DPI | 300 |
| Printer Width | 6" (1800 dots) |
| Tag Size | 5.5" (1650 dots) |
| Left Margin | 0.25" (75 dots) |
| Right Margin | 0.25" (75 dots) |
| Bottom Margin | 2.25" (675 dots) |
| Label Height | 7.75" (2325 dots) |

## Supported Tag Families

1. **tagStandard52h13** - 48,814 possible IDs (0-48,813)
2. **36h11** - 2,287 possible IDs (0-2,286)
3. **tagStandard41h12** - 4,295 possible IDs (0-4,294)

## Printing to ZT421

### Option 1: Network (Recommended)

```bash
# Linux/Mac
cat zpl_output/apriltag_*.zpl | nc -w1 192.168.1.100 9100

# Windows (using netcat)
gc zpl_output/apriltag_*.zpl | nc.exe -w1 192.168.1.100 9100
```

### Option 2: USB via lpr (Windows)

```bash
# Print to default printer
copy zpl_output\apriltag_tagStandard52h13_00000.zpl LPT1:
```

### Option 3: Manual

1. Copy ZPL file content
2. Use Zebra's ZPL viewer or utility
3. Send to printer via your preferred method

## Notes

- Each ZPL references a local image file - keep images in same directory
- ZPL files are plain text and human-readable
- Test first with 1-2 labels before batch printing
- Adjust `LEFT_MARGIN_INCHES`, `TAG_WIDTH_INCHES`, etc. in `generate-zpl.js` to customize
