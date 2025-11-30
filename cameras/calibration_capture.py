#!/usr/bin/env python
"""
Camera Calibration Image Capture Tool

Interactive tool to capture checkerboard images for camera calibration.
Shows live preview and detects checkerboard pattern in real-time.

Usage: python calibration_capture.py <facility> <camera_id>
Example: python calibration_capture.py lodge bagel

Controls:
  SPACE - Capture image (only when checkerboard detected)
  Q     - Quit and save
  R     - Reset (delete all captured images for this camera)
"""

import cv2
import json
import os
import sys
import numpy as np
from datetime import datetime
from pathlib import Path

# Checkerboard configuration
# Standard checkerboard: count INNER corners (not squares)
# A 9x6 checkerboard has 8x5 inner corners
CHECKERBOARD_SIZE = (8, 5)  # (columns, rows) of inner corners
SQUARE_SIZE_MM = 25.0  # Size of each square in millimeters

# Minimum images needed for good calibration
MIN_IMAGES = 15
RECOMMENDED_IMAGES = 20


def load_config(facility_name):
    """Load camera configuration for a facility"""
    config_path = Path(__file__).parent.parent / "warehouses" / facility_name / "cameras" / "config.json"

    if not config_path.exists():
        print(f"ERROR: Config not found at {config_path}")
        sys.exit(1)

    with open(config_path, 'r') as f:
        return json.load(f)


def get_camera_by_id(config, camera_id):
    """Find camera info by modelTCameraId"""
    for channel in config['channels']:
        if channel['modelTCameraId'] == camera_id:
            return channel
    return None


def ensure_calibration_dir(facility_name, camera_id):
    """Create calibration directory structure"""
    cal_dir = Path(__file__).parent.parent / "warehouses" / facility_name / "calibration" / camera_id
    cal_dir.mkdir(parents=True, exist_ok=True)
    return cal_dir


def get_existing_images(cal_dir):
    """Count existing calibration images"""
    return sorted(cal_dir.glob("calib_*.jpg"))


def draw_status(frame, detected, corners, image_count):
    """Draw status overlay on frame"""
    h, w = frame.shape[:2]

    # Status bar background
    cv2.rectangle(frame, (0, 0), (w, 80), (40, 40, 40), -1)

    # Detection status
    if detected:
        status_text = "CHECKERBOARD DETECTED - Press SPACE to capture"
        status_color = (0, 255, 0)
        # Draw detected corners
        cv2.drawChessboardCorners(frame, CHECKERBOARD_SIZE, corners, detected)
    else:
        status_text = "Position checkerboard in view..."
        status_color = (0, 165, 255)

    cv2.putText(frame, status_text, (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)

    # Image count
    count_color = (0, 255, 0) if image_count >= MIN_IMAGES else (0, 165, 255)
    count_text = f"Images: {image_count}/{RECOMMENDED_IMAGES} (min {MIN_IMAGES})"
    cv2.putText(frame, count_text, (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, count_color, 2)

    # Controls hint
    cv2.putText(frame, "Q=Quit  R=Reset  SPACE=Capture", (w - 350, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

    return frame


def capture_calibration_images(rtsp_url, camera_id, camera_name, cal_dir):
    """Main capture loop with live preview"""

    print(f"\nConnecting to {camera_name} ({camera_id})...")
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)

    if not cap.isOpened():
        print("ERROR: Could not connect to camera")
        return False

    # Get resolution
    ret, frame = cap.read()
    if not ret:
        print("ERROR: Could not read frame")
        cap.release()
        return False

    h, w = frame.shape[:2]
    print(f"Connected! Resolution: {w}x{h}")
    print(f"Calibration directory: {cal_dir}")

    # Window setup
    window_name = f"Calibration: {camera_name} ({camera_id})"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    # Scale window for display (these cameras are high-res)
    display_scale = min(1920 / w, 1080 / h, 1.0)
    display_w = int(w * display_scale)
    display_h = int(h * display_scale)
    cv2.resizeWindow(window_name, display_w, display_h)

    existing_images = get_existing_images(cal_dir)
    image_count = len(existing_images)
    print(f"Existing calibration images: {image_count}")

    print("\n" + "="*60)
    print("CALIBRATION CAPTURE MODE")
    print("="*60)
    print(f"Checkerboard size: {CHECKERBOARD_SIZE[0]+1}x{CHECKERBOARD_SIZE[1]+1} squares")
    print(f"                   ({CHECKERBOARD_SIZE[0]}x{CHECKERBOARD_SIZE[1]} inner corners)")
    print(f"Square size: {SQUARE_SIZE_MM}mm")
    print("\nTips for good calibration:")
    print("  - Hold checkerboard at various angles")
    print("  - Cover all areas of the frame (corners, edges, center)")
    print("  - Vary the distance from camera")
    print("  - Keep checkerboard steady when capturing")
    print("="*60 + "\n")

    # Criteria for corner refinement
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Lost connection to camera")
            break

        # Convert to grayscale for detection
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Find checkerboard corners
        detected, corners = cv2.findChessboardCorners(
            gray, CHECKERBOARD_SIZE,
            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE + cv2.CALIB_CB_FAST_CHECK
        )

        # Refine corners if found
        if detected:
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)

        # Draw status overlay
        display_frame = frame.copy()
        display_frame = draw_status(display_frame, detected, corners, image_count)

        cv2.imshow(window_name, display_frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord('q') or key == ord('Q'):
            # Quit
            print(f"\nExiting. Captured {image_count} images.")
            break

        elif key == ord(' ') and detected:
            # Capture image
            image_count += 1
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"calib_{image_count:03d}_{timestamp}.jpg"
            filepath = cal_dir / filename

            # Save the original frame (not the one with overlay)
            cv2.imwrite(str(filepath), frame, [cv2.IMWRITE_JPEG_QUALITY, 100])
            print(f"Captured: {filename} ({image_count}/{RECOMMENDED_IMAGES})")

            # Flash feedback
            flash = frame.copy()
            cv2.rectangle(flash, (0, 0), (w, h), (0, 255, 0), 20)
            cv2.imshow(window_name, flash)
            cv2.waitKey(100)

        elif key == ord('r') or key == ord('R'):
            # Reset - delete all images
            confirm = input("\nDelete all calibration images for this camera? (y/N): ")
            if confirm.lower() == 'y':
                for img_path in get_existing_images(cal_dir):
                    img_path.unlink()
                image_count = 0
                print("Reset complete. All calibration images deleted.")
            else:
                print("Reset cancelled.")

    cap.release()
    cv2.destroyAllWindows()

    return image_count >= MIN_IMAGES


def main():
    if len(sys.argv) < 3:
        print("Usage: python calibration_capture.py <facility> <camera_id>")
        print("Example: python calibration_capture.py lodge bagel")
        print("\nTo see available cameras, check the facility's cameras/config.json")
        sys.exit(1)

    facility_name = sys.argv[1]
    camera_id = sys.argv[2]

    # Load config
    config = load_config(facility_name)

    # Find camera
    camera_info = get_camera_by_id(config, camera_id)
    if not camera_info:
        print(f"ERROR: Camera '{camera_id}' not found in facility '{facility_name}'")
        print("\nAvailable cameras:")
        for ch in config['channels']:
            print(f"  {ch['modelTCameraId']:12} - {ch['modelTCameraName']} (CH{ch['channel']:02d})")
        sys.exit(1)

    # Setup calibration directory
    cal_dir = ensure_calibration_dir(facility_name, camera_id)

    # Run capture
    success = capture_calibration_images(
        camera_info['rtspUrl'],
        camera_id,
        camera_info['modelTCameraName'],
        cal_dir
    )

    if success:
        print(f"\n{'='*60}")
        print("CALIBRATION IMAGES CAPTURED SUCCESSFULLY")
        print(f"{'='*60}")
        print(f"\nNext step: Run calibration processing:")
        print(f"  python calibration_process.py {facility_name} {camera_id}")
    else:
        print(f"\nNeed at least {MIN_IMAGES} images for calibration.")
        print("Run this script again to capture more images.")

    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
