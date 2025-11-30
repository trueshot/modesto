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
import requests
from datetime import datetime
from pathlib import Path

# Camera service API
CAMERA_SERVICE_URL = "http://localhost:8001"

# Checkerboard configuration
# Standard checkerboard: count INNER corners (not squares)
# Lodge facility: 3'x4' checkerboard (6 squares × 8 squares) = 5x7 inner corners
CHECKERBOARD_SIZE = (5, 7)  # (columns, rows) of inner corners
SQUARE_SIZE_MM = 152.4  # Size of each square in millimeters (6 inches)

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
    if detected and corners is not None:
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


def capture_frame_from_api(facility, camera_id):
    """Capture frame via camera service API (more reliable than direct RTSP)"""
    try:
        response = requests.get(
            f"{CAMERA_SERVICE_URL}/api/cameras/{facility}/{camera_id}/capture",
            params={"format": "image", "refresh_cache": False},
            timeout=5
        )
        if response.status_code == 200:
            # Decode JPEG to numpy array
            img_array = np.frombuffer(response.content, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            return frame
        else:
            return None
    except Exception as e:
        print(f"Error fetching frame: {e}")
        return None


def capture_calibration_images(facility, camera_id, camera_name, cal_dir):
    """Main capture loop with live preview"""

    print(f"\nConnecting to {camera_name} ({camera_id}) via camera service API...")

    # Get initial frame to determine resolution
    frame = capture_frame_from_api(facility, camera_id)
    if frame is None:
        print("ERROR: Could not capture initial frame from camera service")
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

    # Calculate processing resolution (much smaller for speed)
    process_scale = 0.25  # Process at 25% resolution for speed
    process_w = int(w * process_scale)
    process_h = int(h * process_scale)
    print(f"Processing resolution: {process_w}x{process_h} (for speed)")
    print(f"Display resolution: {display_w}x{display_h}")
    print(f"Capture resolution: {w}x{h} (full quality)\n")

    frame_count = 0
    while True:
        frame_count += 1
        if frame_count % 30 == 0:  # Print every 30 frames
            print(f"Fetching frame {frame_count}...")

        # Capture frame from API
        frame = capture_frame_from_api(facility, camera_id)
        if frame is None:
            print("ERROR: Failed to fetch frame from camera service")
            break

        # Resize for processing (MUCH faster)
        process_frame = cv2.resize(frame, (process_w, process_h))

        # Convert to grayscale for detection
        gray = cv2.cvtColor(process_frame, cv2.COLOR_BGR2GRAY)

        # Find checkerboard corners on smaller image
        detected, corners = cv2.findChessboardCorners(
            gray, CHECKERBOARD_SIZE,
            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE + cv2.CALIB_CB_FAST_CHECK
        )

        # Refine corners if found
        if detected:
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)

            # Scale corners back to display resolution for overlay
            scale_x = display_w / process_w
            scale_y = display_h / process_h
            corners_scaled = corners.copy()
            corners_scaled[:, 0, 0] *= scale_x
            corners_scaled[:, 0, 1] *= scale_y
        else:
            corners_scaled = None

        # Resize for display first
        display_frame = cv2.resize(process_frame, (display_w, display_h))

        # Draw status overlay on display-sized frame (proper font size!)
        display_frame = draw_status(display_frame, detected, corners_scaled, image_count)

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

            # Save the FULL RESOLUTION original frame (not the processed one!)
            cv2.imwrite(str(filepath), frame, [cv2.IMWRITE_JPEG_QUALITY, 100])
            print(f"Captured: {filename} ({image_count}/{RECOMMENDED_IMAGES}) - Full resolution: {w}x{h}")

            # Flash feedback (on display)
            flash = display_frame.copy()
            cv2.rectangle(flash, (0, 0), (display_w, display_h), (0, 255, 0), 20)
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
        facility_name,
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
