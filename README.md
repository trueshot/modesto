# AprilTag to ZPL Converter for Zebra ZT421

Convert AprilTag SVG files directly to Zebra ZPL (Zebra Programming Language) commands for printing on your 6-inch wide ZT421 printer at 300 DPI.

## Features

- Parses AprilTag SVG files (viewBox 0-100 x 0-100 mapping to 10x10 grid)
- Optimizes black regions into rectangles (combines adjacent cells)
- Generates efficient ZPL commands using `^GB` (draw box) primitives
- Supports all three AprilTag families: 52h13, 36h11, 41h12
- Handles any tag ID from 0-48,813

## Print Specifications

| Parameter | Value |
|-----------|-------|
| **Printer** | Zebra ZT421 |
| **Resolution** | 300 DPI |
| **Print Width** | 6" (1800 dots) |
| **Tag Size** | 5.5" × 5.5" (1650 × 1650 dots) |
| **Grid** | 10 × 10 cells |
| **Cell Size** | 0.55" × 0.55" (165 × 165 dots) |
| **Left Margin** | 0.25" (75 dots) |
| **Top Margin** | 0.25" (75 dots) |
| **Bottom Margin** | 2.25" (675 dots) |
| **Total Height** | 8.0" (2400 dots) |

## Installation

```bash
npm install
```

## Usage

### Basic: Convert SVG to ZPL

```bash
node svg-to-zpl-cli.js <svg-file> [family] [tagId]
```

### Examples

```bash
# Convert with default settings (52h13, ID 0)
node svg-to-zpl-cli.js apriltag.svg

# Specify tag family and ID
node svg-to-zpl-cli.js apriltag.svg tagStandard52h13 12345

# Other families
node svg-to-zpl-cli.js apriltag.svg 36h11 500
node svg-to-zpl-cli.js apriltag.svg tagStandard41h12 999
```

### Output

- Generates `zpl_output/apriltag_<family>_<id>.zpl`
- Shows 10×10 grid visualization (■ = black, □ = white)
- Lists all black rectangles with coordinates
- Displays the complete ZPL code

## ZPL Format Explanation

Each line in the output ZPL is a command. Key commands:

- `^XA` - Start label
- `^LL2400` - Label length (2400 dots = 8")
- `^PW1800` - Print width (1800 dots = 6")
- `^FO75,75` - Field Origin (x, y position in dots)
- `^GB165,165,0,B` - Draw Box: width, height, thickness (0=filled), color (B=black)
- `^FS` - Field Separator (end field)
- `^XZ` - End label

## Example: Printing

### Via Network (ZT421 default port 9100):

**Windows (PowerShell):**
```powershell
$zpl = Get-Content zpl_output\apriltag_tagStandard52h13_12345.zpl -Raw
$client = New-Object System.Net.Sockets.TcpClient("192.168.1.100", 9100)
$stream = $client.GetStream()
$writer = New-Object System.IO.StreamWriter($stream)
$writer.Write($zpl)
$writer.Flush()
$writer.Close()
```

**Via USB (Windows):**
```cmd
copy zpl_output\apriltag_tagStandard52h13_12345.zpl LPT1:
```

## File Structure

```
.
├── svg-grid-parser.js         # Core SVG parsing & ZPL generation
├── svg-to-zpl-cli.js          # Command-line interface
├── test_apriltag.svg          # Example SVG file
├── zpl_output/                # Generated ZPL files
│   ├── apriltag_*.zpl         # ZPL label files
│   └── (SVG files are not needed for printing)
└── package.json               # Node.js dependencies
```

## Algorithm

1. **SVG Parsing**: Extract black regions from SVG paths using point-in-polygon testing
2. **Grid Mapping**: Map SVG coordinates (0-100) to 10×10 grid cells
3. **Rectangle Optimization**: Combine adjacent black cells into larger rectangles
4. **ZPL Generation**: Create `^FO` and `^GB` commands for each rectangle
5. **Output**: Save complete ZPL label file

## Troubleshooting

### "File not found" error
- Ensure SVG file path is correct and file exists
- Use relative or absolute paths

### "Tag ID out of range" error
- 52h13: 0-48,813
- 36h11: 0-2,286
- 41h12: 0-4,294

### ZPL doesn't print correctly
- Verify printer is set to 300 DPI
- Check printer's IP address for network printing
- Ensure label media is loaded (6" wide minimum)

## Advanced Usage

### Programmatically (Node.js)

```javascript
const { parseSvgToGrid, findRectangles, generateZplFromRectangles } = require('./svg-grid-parser.js');
const fs = require('fs');

const svgContent = fs.readFileSync('apriltag.svg', 'utf-8');
const grid = parseSvgToGrid(svgContent);
const rectangles = findRectangles(grid);
const zpl = generateZplFromRectangles(rectangles);

console.log(zpl);
```

## Notes

- SVG viewBox must be exactly 0-100 x 0-100 (10×10 grid)
- White regions become transparent (no ZPL commands)
- Black regions are optimized into rectangular boxes
- Margins account for typical label layout on 6" media
- Output is plain text ZPL, can be sent to any compatible Zebra printer
