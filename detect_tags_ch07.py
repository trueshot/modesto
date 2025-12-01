#!/usr/bin/env python
"""
AprilTag 36h11 Detection on Ch07 Captured Image
For benchmarking and testing
"""

import cv2
import json
import time
from pupil_apriltags import Detector

# Image path
IMAGE_PATH = "camera_captures/20251130_124734/ch07_brownie_Brownie.jpg"

# Calibration path
CALIBRATION_PATH = "warehouses/lodge/calibration/biscuit/calibration.json"

# AprilTag physical size
TAG_SIZE_INCHES = 8.0
TAG_SIZE_METERS = TAG_SIZE_INCHES * 0.0254  # Convert to meters

print("="*70)
print("AprilTag 36h11 Detection - Channel 7")
print(f"Image: {IMAGE_PATH}")
print(f"Tag size: {TAG_SIZE_INCHES} inches ({TAG_SIZE_METERS:.4f} meters)")
print("="*70)

# Load calibration
with open(CALIBRATION_PATH, 'r') as f:
    calib = json.load(f)

fx = calib['camera_matrix']['fx']
fy = calib['camera_matrix']['fy']
cx = calib['camera_matrix']['cx']
cy = calib['camera_matrix']['cy']
camera_params = (fx, fy, cx, cy)

print(f"Calibration: fx={fx:.1f}, fy={fy:.1f}, cx={cx:.1f}, cy={cy:.1f}")

# Load image
load_start = time.time()
frame = cv2.imread(IMAGE_PATH)
load_time = time.time() - load_start

if frame is None:
    print(f"ERROR: Could not load image from {IMAGE_PATH}")
    exit(1)

h, w = frame.shape[:2]

# Initialize detector
detector = Detector(
    families='tag36h11',
    nthreads=4,
    quad_decimate=2.0,
    quad_sigma=0.0,
    refine_edges=1,
    decode_sharpening=0.25,
    debug=0
)

# Convert to grayscale
gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

# Detect tags WITH POSE ESTIMATION
detect_start = time.time()
detections = detector.detect(
    gray,
    estimate_tag_pose=True,
    camera_params=camera_params,
    tag_size=TAG_SIZE_METERS
)
detect_time = time.time() - detect_start

# Results
print("\n" + "="*70)
print("TIMING")
print("="*70)
print(f"Load time:   {load_time:.3f} seconds")
print(f"Detect time: {detect_time:.3f} seconds")
print(f"Total time:  {load_time + detect_time:.3f} seconds")

print("\n" + "="*70)
print("RESULTS")
print("="*70)
print(f"Image size:  {w}x{h}")
print(f"Tags found:  {len(detections)}")

if detections:
    print("\nDetected tags:")
    for detection in detections:
        tag_id = detection.tag_id
        quality = detection.decision_margin

        # Get distance from pose (translation vector)
        if detection.pose_t is not None:
            # pose_t is [x, y, z] where z is distance from camera
            distance_m = detection.pose_t[2][0]
            distance_ft = distance_m * 3.28084
            distance_in = distance_m * 39.3701
            print(f"  ID {tag_id:3d}: {distance_ft:6.2f} ft ({distance_in:6.1f} in, {distance_m:5.2f} m) - quality {quality:.1f}")
        else:
            print(f"  ID {tag_id:3d}: quality {quality:.1f} (no pose)")

print("="*70)
