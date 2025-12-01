#!/usr/bin/env python
"""
AprilTag 36h11 Detection on Biscuit Camera
Uses camera service API for reliable frame capture
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
print("AprilTag 36h11 Detection - Biscuit Camera")
print("="*70)

# Initialize AprilTag detector for tag36h11
print("Initializing detector...")
detector = Detector(
    families='tag36h11',
    nthreads=4,
    quad_decimate=2.0,
    quad_sigma=0.0,
    refine_edges=1,
    decode_sharpening=0.25,
    debug=0
)

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

        # Resize for faster detection (process at 25% resolution)
        h, w = frame.shape[:2]
        process_scale = 0.25
        process_w = int(w * process_scale)
        process_h = int(h * process_scale)
        process_frame = cv2.resize(frame, (process_w, process_h))

        # Convert to grayscale for detection
        gray = cv2.cvtColor(process_frame, cv2.COLOR_BGR2GRAY)

        # Detect AprilTags on smaller image (MUCH faster!)
        detections = detector.detect(
            gray,
            estimate_tag_pose=False,
            camera_params=None,
            tag_size=None
        )

        # Scale detections back to full resolution for display
        for detection in detections:
            detection.center *= (1.0 / process_scale)
            detection.corners *= (1.0 / process_scale)

        # Draw detected tags
        current_tags = {}
        if detections:
            detection_count += len(detections)

            for detection in detections:
                tag_id = detection.tag_id
                center = detection.center
                corners = detection.corners
                decision_margin = detection.decision_margin

                current_tags[tag_id] = {
                    'center': center,
                    'margin': decision_margin
                }

                # Draw tag border (green)
                corners_int = corners.astype(int)
                for i in range(4):
                    pt1 = tuple(corners_int[i])
                    pt2 = tuple(corners_int[(i + 1) % 4])
                    cv2.line(frame, pt1, pt2, (0, 255, 0), 4)

                # Draw center point (red)
                center_int = (int(center[0]), int(center[1]))
                cv2.circle(frame, center_int, 10, (0, 0, 255), -1)

                # Draw tag ID (large text)
                cv2.putText(
                    frame,
                    f"ID: {tag_id}",
                    (int(center[0]) - 80, int(center[1]) - 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.5,
                    (0, 255, 0),
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
                margin = current_tags[tag_id]['margin']
                center = current_tags[tag_id]['center']
                print(f"  NEW TAG: ID {tag_id} at ({center[0]:.0f}, {center[1]:.0f}), quality: {margin:.1f}")

        last_tags = current_tags

        # Add status overlay
        status_bg = frame.copy()
        cv2.rectangle(status_bg, (0, 0), (frame.shape[1], 100), (40, 40, 40), -1)
        frame = cv2.addWeighted(frame, 0.7, status_bg, 0.3, 0)

        status_text = f"Frame: {frame_count} | Tags Found: {len(detections) if detections else 0} | Total Detections: {detection_count}"
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
        print(f"  Tags visible at end: {list(last_tags.keys())}")
    print("="*70)
