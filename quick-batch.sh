#!/bin/bash

# Download and convert AprilTag images
# Usage: ./quick-batch.sh <limit> <family>

LIMIT=${1:-20}
FAMILY=${2:-tagStandard52h13}

GITHUB_BASE="https://github.com/AprilRobotics/apriltag-imgs/raw/master"
OUTPUT_DIR="./zpl_output"
IMAGES_DIR="$OUTPUT_DIR/images"
ZPL_DIR="$OUTPUT_DIR/zpl"

# Setup directories
mkdir -p "$IMAGES_DIR" "$ZPL_DIR"

# Define families
declare -A FAMILIES=(
  ["tagStandard52h13"]="tag52_13:tagStandard52h13"
  ["36h11"]="tag36_11:tag36h11"
  ["tagStandard41h12"]="tag41_12:tagStandard41h12"
)

# Parse family config
IFS=':' read -r PREFIX DIR <<< "${FAMILIES[$FAMILY]}"

echo "Downloading and converting $FAMILY (first $LIMIT tags)..."
echo ""

for i in $(seq 0 $((LIMIT-1))); do
  TAG_NUM=$(printf "%05d" $i)
  FILENAME="${PREFIX}_${TAG_NUM}.png"
  URL="${GITHUB_BASE}/${DIR}/${FILENAME}"
  IMAGE_PATH="${IMAGES_DIR}/${FILENAME}"

  # Download with curl (handles redirects)
  echo -n "[$((i+1))/$LIMIT] Downloading and converting ${FILENAME}... "

  if curl -L -s -o "$IMAGE_PATH" "$URL"; then
    # Convert to ZPL
    if node simple-png-to-zpl.js "$IMAGE_PATH" "$FAMILY" "$i" > /dev/null 2>&1; then
      echo "✓"
    else
      echo "✗ (conversion failed)"
      rm -f "$IMAGE_PATH"
    fi
  else
    echo "✗ (download failed)"
  fi
done

echo ""
echo "Done! ZPL files in: $ZPL_DIR"
ls -lh "$ZPL_DIR"/*.zpl 2>/dev/null | tail -5
