#!/usr/bin/env python
"""
Multi-Family AprilTag Detection on Biscuit Camera
Detects building fiducials, forklifts, and pallets

Tag Families:
  - tag36h11: Building fiducials (fixed reference points)
  - tag25h9: Forklifts (mobile equipment)
  - tagStandard41h12: Pallets (inventory/goods)
  - tagStandard52h13: Reserved (future use)
"""

import cv2
import numpy as np
import requests
import time
from pupil_apriltags import Detector

# Camera service configuration
CAMERA_SERVICE_URL = "http://localhost:8001"
FACILITY = "lodge"
CAMERA_ID = "biscuit"


print("="*70)
print("Multi-Family AprilTag Detection - Biscuit Camera")
print("="*70)
print("Asset Types:")
print("  - Fiducials (36h11):  Building reference points [GREEN]")
print("  - Forklifts (25h9):   Mobile equipment [ORANGE]")
print("  - Pallets (41h12):    Inventory/goods [MAGENTA]")
print("  - Reserved (52h13):   Future use [CYAN]")
print("="*70)

# Initialize 4 separate detectors - one per asset type
print("Initializing detectors...")

detector_fiducial = Detector(
    families='tag36h11',
    nthreads=4,
    quad_decimate=1.0,
    quad_sigma=0.0,
    refine_edges=1,
    decode_sharpening=0.25,
    debug=0
)
print("  - Fiducial detector (tag36h11): ready")

detector_forklift = Detector(
    families='tag25h9',
    nthreads=4,
    quad_decimate=1.0,
    quad_sigma=0.0,
    refine_edges=1,
    decode_sharpening=0.25,
    debug=0
)
print("  - Forklift detector (tag25h9): ready")

detector_pallet = Detector(
    families='tagStandard41h12',
    nthreads=4,
    quad_decimate=1.0,
    quad_sigma=0.0,
    refine_edges=1,
    decode_sharpening=0.25,
    debug=0
)
print("  - Pallet detector (tagStandard41h12): ready")

detector_reserved = Detector(
    families='tagStandard52h13',
    nthreads=4,
    quad_decimate=1.0,
    quad_sigma=0.0,
    refine_edges=1,
    decode_sharpening=0.25,
    debug=0
)
print("  - Reserved detector (tagStandard52h13): ready")

# Detector mapping for iteration
DETECTORS = {
    'Fiducial': {'detector': detector_fiducial, 'color': (0, 255, 0)},      # Green
    'Forklift': {'detector': detector_forklift, 'color': (0, 165, 255)},    # Orange
    'Pallet': {'detector': detector_pallet, 'color': (255, 0, 255)},        # Magenta
    'Reserved': {'detector': detector_reserved, 'color': (255, 255, 0)},    # Cyan
}

# CLAHE for contrast enhancement
clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

def capture_frame_from_api():
    """Capture frame via camera service API"""
    try:
        response = requests.get(
            f"{CAMERA_SERVICE_URL}/api/cameras/{FACILITY}/{CAMERA_ID}/capture",
            params={"format": "image", "refresh_cache": False},
            timeout=5
        )
        if response.status_code == 200:
            img_array = np.frombuffer(response.content, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            return frame
        else:
            return None
    except Exception as e:
        print(f"Error fetching frame: {e}")
        return None

print("Starting detection (press Q to quit)...")
print()

frame_count = 0
detection_count = 0
last_tags = {}

# Create window
cv2.namedWindow("AprilTag Detection - Biscuit", cv2.WINDOW_NORMAL)

try:
    while True:
        # Capture frame
        frame = capture_frame_from_api()

        if frame is None:
            print("Failed to capture frame, retrying...")
            time.sleep(1)
            continue

        frame_count += 1

        # Resize for detection (50% for balance of speed/accuracy)
        h, w = frame.shape[:2]
        process_scale = 0.5
        process_w = int(w * process_scale)
        process_h = int(h * process_scale)
        process_frame = cv2.resize(frame, (process_w, process_h))

        # Convert to grayscale and enhance contrast
        gray = cv2.cvtColor(process_frame, cv2.COLOR_BGR2GRAY)
        gray = clahe.apply(gray)

        # Run all 3 detectors
        all_detections = []
        for asset_name, config in DETECTORS.items():
            detections = config['detector'].detect(
                gray,
                estimate_tag_pose=False,
                camera_params=None,
                tag_size=None
            )
            # Add asset info to each detection
            for d in detections:
                d.asset_name = asset_name
                d.color = config['color']
                # Scale back to full resolution
                d.center *= (1.0 / process_scale)
                d.corners *= (1.0 / process_scale)
                all_detections.append(d)

        # Draw detected tags
        current_tags = {}
        if all_detections:
            detection_count += len(all_detections)

            for detection in all_detections:
                tag_id = detection.tag_id
                asset_name = detection.asset_name
                color = detection.color
                center = detection.center
                corners = detection.corners
                decision_margin = detection.decision_margin

                current_tags[tag_id] = {
                    'center': center,
                    'margin': decision_margin,
                    'asset': asset_name
                }

                # Draw tag border (color by asset type)
                corners_int = corners.astype(int)
                for i in range(4):
                    pt1 = tuple(corners_int[i])
                    pt2 = tuple(corners_int[(i + 1) % 4])
                    cv2.line(frame, pt1, pt2, color, 4)

                # Draw center point (red)
                center_int = (int(center[0]), int(center[1]))
                cv2.circle(frame, center_int, 10, (0, 0, 255), -1)

                # Draw asset type and tag ID
                label = f"{asset_name} #{tag_id}"
                cv2.putText(
                    frame,
                    label,
                    (int(center[0]) - 80, int(center[1]) - 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.5,
                    color,
                    4
                )

                # Draw decision margin (quality indicator)
                cv2.putText(
                    frame,
                    f"Quality: {decision_margin:.1f}",
                    (int(center[0]) - 80, int(center[1]) + 50),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.0,
                    (255, 255, 0),
                    3
                )

        # Print new detections
        for tag_id in current_tags:
            if tag_id not in last_tags:
                info = current_tags[tag_id]
                margin = info['margin']
                center = info['center']
                asset = info['asset']
                print(f"  NEW: {asset} #{tag_id} at ({center[0]:.0f}, {center[1]:.0f}), quality: {margin:.1f}")

        last_tags = current_tags

        # Count by asset type
        fiducials = sum(1 for t in current_tags.values() if t['asset'] == 'Fiducial')
        forklifts = sum(1 for t in current_tags.values() if t['asset'] == 'Forklift')
        pallets = sum(1 for t in current_tags.values() if t['asset'] == 'Pallet')

        # Add status overlay
        status_bg = frame.copy()
        cv2.rectangle(status_bg, (0, 0), (frame.shape[1], 100), (40, 40, 40), -1)
        frame = cv2.addWeighted(frame, 0.7, status_bg, 0.3, 0)

        status_text = f"Frame: {frame_count} | Fiducials: {fiducials} | Forklifts: {forklifts} | Pallets: {pallets}"
        cv2.putText(frame, status_text, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)

        # Resize for display (biscuit is 4096x3072, way too big)
        height, width = frame.shape[:2]
        display_scale = min(1920 / width, 1080 / height)
        display_w = int(width * display_scale)
        display_h = int(height * display_scale)
        display_frame = cv2.resize(frame, (display_w, display_h))

        # Display
        cv2.imshow("AprilTag Detection - Biscuit", display_frame)

        # Check for key press
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == ord('Q'):
            print("\nStopped by user")
            break

        # Small delay between captures (API-based, don't hammer it)
        time.sleep(0.2)

except KeyboardInterrupt:
    print("\nInterrupted by user")

finally:
    cv2.destroyAllWindows()
    print("\n" + "="*70)
    print("Detection Summary:")
    print(f"  Frames processed: {frame_count}")
    print(f"  Total detections: {detection_count}")
    if last_tags:
        # Group by asset type
        by_type = {}
        for tag_id, info in last_tags.items():
            asset = info['asset']
            if asset not in by_type:
                by_type[asset] = []
            by_type[asset].append(tag_id)
        print("  Final tags by type:")
        for asset, ids in sorted(by_type.items()):
            print(f"    {asset}: {sorted(ids)}")
    print("="*70)
