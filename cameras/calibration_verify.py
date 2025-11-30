#!/usr/bin/env python
"""
Verify Camera Calibration

Captures a live frame, undistorts it, and shows before/after comparison.
This helps verify the calibration is working correctly.

Usage: python calibration_verify.py <facility> <camera_id>
Example: python calibration_verify.py lodge bagel
"""

import cv2
import json
import numpy as np
import sys
from pathlib import Path


def load_config(facility_name):
    """Load camera configuration for a facility"""
    config_path = Path(__file__).parent.parent / "warehouses" / facility_name / "cameras" / "config.json"
    with open(config_path, 'r') as f:
        return json.load(f)


def get_camera_by_id(config, camera_id):
    """Find camera info by modelTCameraId"""
    for channel in config['channels']:
        if channel['modelTCameraId'] == camera_id:
            return channel
    return None


def load_calibration(facility_name, camera_id):
    """Load calibration data"""
    cal_path = Path(__file__).parent.parent / "warehouses" / facility_name / "calibration" / camera_id / "calibration_opencv.npz"

    if not cal_path.exists():
        return None, None, None

    data = np.load(cal_path)
    return data['camera_matrix'], data['dist_coeffs'], tuple(data['image_size'])


def draw_info_overlay(frame, cal_data, is_undistorted=False):
    """Draw calibration info on frame"""
    h, w = frame.shape[:2]

    # Background for text
    cv2.rectangle(frame, (0, 0), (400, 120), (40, 40, 40), -1)

    title = "UNDISTORTED" if is_undistorted else "ORIGINAL (distorted)"
    color = (0, 255, 0) if is_undistorted else (0, 165, 255)

    cv2.putText(frame, title, (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)

    if cal_data:
        cv2.putText(frame, f"fx={cal_data['fx']:.1f} fy={cal_data['fy']:.1f}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.putText(frame, f"cx={cal_data['cx']:.1f} cy={cal_data['cy']:.1f}", (10, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.putText(frame, f"k1={cal_data['k1']:.4f} k2={cal_data['k2']:.4f}", (10, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    return frame


def main():
    if len(sys.argv) < 3:
        print("Usage: python calibration_verify.py <facility> <camera_id>")
        sys.exit(1)

    facility_name = sys.argv[1]
    camera_id = sys.argv[2]

    # Load config
    config = load_config(facility_name)
    camera_info = get_camera_by_id(config, camera_id)

    if not camera_info:
        print(f"ERROR: Camera '{camera_id}' not found")
        sys.exit(1)

    # Load calibration
    camera_matrix, dist_coeffs, image_size = load_calibration(facility_name, camera_id)

    if camera_matrix is None:
        print(f"ERROR: No calibration found for '{camera_id}'")
        print("Run calibration_capture.py and calibration_process.py first")
        sys.exit(1)

    cal_data = {
        'fx': camera_matrix[0, 0],
        'fy': camera_matrix[1, 1],
        'cx': camera_matrix[0, 2],
        'cy': camera_matrix[1, 2],
        'k1': dist_coeffs[0, 0],
        'k2': dist_coeffs[0, 1]
    }

    print(f"Verifying calibration for: {camera_info['modelTCameraName']} ({camera_id})")
    print(f"Image size: {image_size[0]}x{image_size[1]}")
    print(f"Connecting to camera...")

    # Connect to camera
    cap = cv2.VideoCapture(camera_info['rtspUrl'], cv2.CAP_FFMPEG)

    if not cap.isOpened():
        print("ERROR: Could not connect to camera")
        sys.exit(1)

    # Compute optimal new camera matrix for undistortion
    new_camera_matrix, roi = cv2.getOptimalNewCameraMatrix(
        camera_matrix, dist_coeffs, image_size, 1, image_size
    )

    print("Connected! Press Q to quit, S to save comparison image")
    print()

    # Create windows
    cv2.namedWindow("Original", cv2.WINDOW_NORMAL)
    cv2.namedWindow("Undistorted", cv2.WINDOW_NORMAL)

    # Resize windows
    cv2.resizeWindow("Original", 960, 540)
    cv2.resizeWindow("Undistorted", 960, 540)

    # Position windows side by side
    cv2.moveWindow("Original", 0, 100)
    cv2.moveWindow("Undistorted", 970, 100)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Lost connection")
            break

        # Undistort
        undistorted = cv2.undistort(frame, camera_matrix, dist_coeffs, None, new_camera_matrix)

        # Draw overlays
        original_display = draw_info_overlay(frame.copy(), cal_data, False)
        undistorted_display = draw_info_overlay(undistorted.copy(), cal_data, True)

        # Show frames
        cv2.imshow("Original", original_display)
        cv2.imshow("Undistorted", undistorted_display)

        key = cv2.waitKey(1) & 0xFF

        if key == ord('q') or key == ord('Q'):
            break
        elif key == ord('s') or key == ord('S'):
            # Save comparison
            comparison = np.hstack([original_display, undistorted_display])
            output_path = f"calibration_verify_{camera_id}.jpg"
            cv2.imwrite(output_path, comparison)
            print(f"Saved comparison to: {output_path}")

    cap.release()
    cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    exit(main())
