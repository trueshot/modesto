#!/bin/bash

# Stage 1: Download all PNG images for all families
# Usage: ./download-all-pngs.sh

GITHUB_BASE="https://github.com/AprilRobotics/apriltag-imgs/raw/master"
IMAGES_DIR="./zpl_output/images"

mkdir -p "$IMAGES_DIR"

# Define families: name:prefix:dir:count
declare -a FAMILIES=(
  "tagStandard52h13:tag52_13:tagStandard52h13:48814"
  "36h11:tag36_11:tag36h11:2287"
  "tagStandard41h12:tag41_12:tagStandard41h12:4295"
)

TOTAL_TO_DOWNLOAD=55396
DOWNLOADED=0
FAILED=0

echo "=== AprilTag PNG Downloader ==="
echo "Total to download: $TOTAL_TO_DOWNLOAD"
echo ""

for FAMILY_INFO in "${FAMILIES[@]}"; do
  IFS=':' read -r FAMILY PREFIX DIR COUNT <<< "$FAMILY_INFO"

  echo "Downloading $FAMILY ($COUNT images)..."

  for i in $(seq 0 $((COUNT-1))); do
    TAG_NUM=$(printf "%05d" $i)
    FILENAME="${PREFIX}_${TAG_NUM}.png"
    URL="${GITHUB_BASE}/${DIR}/${FILENAME}"
    IMAGE_PATH="${IMAGES_DIR}/${FILENAME}"

    # Skip if already exists
    if [ -f "$IMAGE_PATH" ]; then
      ((DOWNLOADED++))
      continue
    fi

    # Download
    if curl -L -s -o "$IMAGE_PATH" "$URL"; then
      ((DOWNLOADED++))
      if ((i % 500 == 0)); then
        echo "  [$DOWNLOADED/$TOTAL_TO_DOWNLOAD] Downloaded $FILENAME"
      fi
    else
      ((FAILED++))
      rm -f "$IMAGE_PATH"
      echo "  Failed: $FILENAME"
    fi
  done

  echo "  ✓ $FAMILY complete"
  echo ""
done

echo "=== Download Summary ==="
echo "✓ Downloaded: $DOWNLOADED"
echo "✗ Failed: $FAILED"
echo "Total images in $IMAGES_DIR: $(ls -1 $IMAGES_DIR/*.png 2>/dev/null | wc -l)"
