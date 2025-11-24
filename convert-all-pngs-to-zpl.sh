#!/bin/bash

# Stage 2: Convert all downloaded PNGs to ZPL
# Usage: ./convert-all-pngs-to-zpl.sh

IMAGES_DIR="./zpl_output/images"
ZPL_DIR="./zpl_output/zpl"

mkdir -p "$ZPL_DIR"

TOTAL=$(ls -1 "$IMAGES_DIR"/*.png 2>/dev/null | wc -l)
CONVERTED=0
FAILED=0

echo "=== AprilTag PNG to ZPL Converter ==="
echo "Total images to convert: $TOTAL"
echo ""

for PNG_FILE in "$IMAGES_DIR"/*.png; do
  if [ ! -f "$PNG_FILE" ]; then
    break
  fi

  BASENAME=$(basename "$PNG_FILE" .png)

  # Extract family and ID from filename
  # Format: tag52_13_00001.png -> family=tagStandard52h13, id=00001
  if [[ $BASENAME =~ ^tag52_13_(.*)$ ]]; then
    FAMILY="tagStandard52h13"
    ID="${BASH_REMATCH[1]}"
  elif [[ $BASENAME =~ ^tag36_11_(.*)$ ]]; then
    FAMILY="36h11"
    ID="${BASH_REMATCH[1]}"
  elif [[ $BASENAME =~ ^tag41_12_(.*)$ ]]; then
    FAMILY="tagStandard41h12"
    ID="${BASH_REMATCH[1]}"
  else
    echo "  ✗ Unknown format: $BASENAME"
    ((FAILED++))
    continue
  fi

  # Convert ID from padded to integer
  ID_INT=$((10#$ID))

  # Convert
  if node simple-png-to-zpl.js "$PNG_FILE" "$FAMILY" "$ID_INT" > /dev/null 2>&1; then
    ((CONVERTED++))
    if ((CONVERTED % 1000 == 0)); then
      echo "  [$CONVERTED/$TOTAL] Converted $BASENAME"
    fi
  else
    ((FAILED++))
    echo "  ✗ Failed: $BASENAME"
  fi
done

echo ""
echo "=== Conversion Summary ==="
echo "✓ Converted: $CONVERTED"
echo "✗ Failed: $FAILED"
echo "Total ZPL files in $ZPL_DIR: $(ls -1 $ZPL_DIR/*.zpl 2>/dev/null | wc -l)"
