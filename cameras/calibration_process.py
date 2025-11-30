#!/usr/bin/env python
"""
Camera Calibration Processing Tool

Processes captured checkerboard images to compute camera intrinsic parameters.
Outputs camera matrix, distortion coefficients, and reprojection error.

Usage: python calibration_process.py <facility> <camera_id>
Example: python calibration_process.py lodge bagel
"""

import cv2
import json
import numpy as np
import sys
from pathlib import Path
from datetime import datetime

# Checkerboard configuration (must match capture script)
CHECKERBOARD_SIZE = (8, 5)  # (columns, rows) of inner corners
SQUARE_SIZE_MM = 25.0  # Size of each square in millimeters


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


def get_calibration_dir(facility_name, camera_id):
    """Get calibration directory"""
    return Path(__file__).parent.parent / "warehouses" / facility_name / "calibration" / camera_id


def prepare_object_points():
    """Prepare 3D object points for the checkerboard"""
    # Object points in checkerboard coordinate system
    # (0,0,0), (1,0,0), (2,0,0), ..., (7,4,0)
    objp = np.zeros((CHECKERBOARD_SIZE[0] * CHECKERBOARD_SIZE[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:CHECKERBOARD_SIZE[0], 0:CHECKERBOARD_SIZE[1]].T.reshape(-1, 2)
    objp *= SQUARE_SIZE_MM  # Scale to real-world units
    return objp


def process_calibration_images(cal_dir, show_detections=False):
    """Process all calibration images and find corners"""

    image_files = sorted(cal_dir.glob("calib_*.jpg"))
    if not image_files:
        print("ERROR: No calibration images found")
        return None, None, None

    print(f"Found {len(image_files)} calibration images")

    # Prepare object points
    objp = prepare_object_points()

    # Arrays to store object points and image points
    object_points = []  # 3D points in real world space
    image_points = []   # 2D points in image plane
    image_size = None

    # Corner refinement criteria
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)

    successful = 0
    failed = 0

    for img_path in image_files:
        img = cv2.imread(str(img_path))
        if img is None:
            print(f"  [FAIL] Could not read: {img_path.name}")
            failed += 1
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        if image_size is None:
            image_size = gray.shape[::-1]  # (width, height)

        # Find checkerboard corners
        ret, corners = cv2.findChessboardCorners(
            gray, CHECKERBOARD_SIZE,
            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
        )

        if ret:
            # Refine corners
            corners_refined = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)

            object_points.append(objp)
            image_points.append(corners_refined)
            successful += 1
            print(f"  [OK] {img_path.name}")

            if show_detections:
                vis = img.copy()
                cv2.drawChessboardCorners(vis, CHECKERBOARD_SIZE, corners_refined, ret)
                cv2.namedWindow("Detection", cv2.WINDOW_NORMAL)
                cv2.resizeWindow("Detection", 1280, 720)
                cv2.imshow("Detection", vis)
                cv2.waitKey(500)
        else:
            print(f"  [FAIL] No pattern found: {img_path.name}")
            failed += 1

    if show_detections:
        cv2.destroyAllWindows()

    print(f"\nProcessed: {successful} successful, {failed} failed")

    if successful < 10:
        print("WARNING: Less than 10 images. Calibration may be unreliable.")

    return object_points, image_points, image_size


def calibrate_camera(object_points, image_points, image_size):
    """Run OpenCV camera calibration"""

    print("\nRunning calibration...")

    # Calibrate
    ret, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
        object_points,
        image_points,
        image_size,
        None,
        None,
        flags=cv2.CALIB_FIX_K3  # Fix k3 to reduce overfitting
    )

    print(f"Calibration complete!")
    print(f"RMS reprojection error: {ret:.4f} pixels")

    # Interpret the error
    if ret < 0.5:
        quality = "Excellent"
    elif ret < 1.0:
        quality = "Good"
    elif ret < 2.0:
        quality = "Acceptable"
    else:
        quality = "Poor - consider recapturing"

    print(f"Calibration quality: {quality}")

    return ret, camera_matrix, dist_coeffs, rvecs, tvecs


def compute_fov(camera_matrix, image_size):
    """Compute field of view from camera matrix"""
    fx = camera_matrix[0, 0]
    fy = camera_matrix[1, 1]
    w, h = image_size

    fov_x = 2 * np.arctan(w / (2 * fx)) * 180 / np.pi
    fov_y = 2 * np.arctan(h / (2 * fy)) * 180 / np.pi

    return fov_x, fov_y


def save_calibration(cal_dir, camera_id, camera_matrix, dist_coeffs, image_size, rms_error):
    """Save calibration results to JSON"""

    # Convert numpy arrays to lists for JSON serialization
    calibration_data = {
        "camera_id": camera_id,
        "calibration_date": datetime.now().isoformat(),
        "image_size": {
            "width": image_size[0],
            "height": image_size[1]
        },
        "camera_matrix": {
            "fx": float(camera_matrix[0, 0]),
            "fy": float(camera_matrix[1, 1]),
            "cx": float(camera_matrix[0, 2]),
            "cy": float(camera_matrix[1, 2]),
            "matrix": camera_matrix.tolist()
        },
        "distortion_coefficients": {
            "k1": float(dist_coeffs[0, 0]),
            "k2": float(dist_coeffs[0, 1]),
            "p1": float(dist_coeffs[0, 2]),
            "p2": float(dist_coeffs[0, 3]),
            "k3": float(dist_coeffs[0, 4]) if len(dist_coeffs[0]) > 4 else 0.0,
            "array": dist_coeffs.flatten().tolist()
        },
        "field_of_view": {
            "horizontal_deg": float(2 * np.arctan(image_size[0] / (2 * camera_matrix[0, 0])) * 180 / np.pi),
            "vertical_deg": float(2 * np.arctan(image_size[1] / (2 * camera_matrix[1, 1])) * 180 / np.pi)
        },
        "rms_reprojection_error": float(rms_error),
        "checkerboard": {
            "inner_corners": list(CHECKERBOARD_SIZE),
            "square_size_mm": SQUARE_SIZE_MM
        }
    }

    # Save to calibration directory
    output_path = cal_dir / "calibration.json"
    with open(output_path, 'w') as f:
        json.dump(calibration_data, f, indent=2)

    print(f"\nCalibration saved to: {output_path}")

    # Also save OpenCV format for direct loading
    cv_path = cal_dir / "calibration_opencv.npz"
    np.savez(cv_path,
             camera_matrix=camera_matrix,
             dist_coeffs=dist_coeffs,
             image_size=np.array(image_size))
    print(f"OpenCV format saved to: {cv_path}")

    return calibration_data


def print_calibration_summary(camera_matrix, dist_coeffs, image_size, fov):
    """Print human-readable calibration summary"""

    print("\n" + "="*60)
    print("CALIBRATION RESULTS")
    print("="*60)

    print(f"\nImage size: {image_size[0]} x {image_size[1]} pixels")

    print(f"\nCamera Matrix (K):")
    print(f"  fx = {camera_matrix[0, 0]:.2f} pixels")
    print(f"  fy = {camera_matrix[1, 1]:.2f} pixels")
    print(f"  cx = {camera_matrix[0, 2]:.2f} pixels (principal point x)")
    print(f"  cy = {camera_matrix[1, 2]:.2f} pixels (principal point y)")

    print(f"\nDistortion Coefficients:")
    print(f"  k1 = {dist_coeffs[0, 0]:.6f} (radial)")
    print(f"  k2 = {dist_coeffs[0, 1]:.6f} (radial)")
    print(f"  p1 = {dist_coeffs[0, 2]:.6f} (tangential)")
    print(f"  p2 = {dist_coeffs[0, 3]:.6f} (tangential)")
    if len(dist_coeffs[0]) > 4:
        print(f"  k3 = {dist_coeffs[0, 4]:.6f} (radial)")

    print(f"\nField of View:")
    print(f"  Horizontal: {fov[0]:.1f}°")
    print(f"  Vertical:   {fov[1]:.1f}°")
    print(f"  Diagonal:   {np.sqrt(fov[0]**2 + fov[1]**2):.1f}°")

    print("="*60)


def main():
    if len(sys.argv) < 3:
        print("Usage: python calibration_process.py <facility> <camera_id>")
        print("Example: python calibration_process.py lodge bagel")
        sys.exit(1)

    facility_name = sys.argv[1]
    camera_id = sys.argv[2]

    # Optional: show detections
    show_detections = "--show" in sys.argv

    # Load config
    config = load_config(facility_name)

    # Verify camera exists
    camera_info = get_camera_by_id(config, camera_id)
    if not camera_info:
        print(f"ERROR: Camera '{camera_id}' not found")
        sys.exit(1)

    print(f"Processing calibration for: {camera_info['modelTCameraName']} ({camera_id})")

    # Get calibration directory
    cal_dir = get_calibration_dir(facility_name, camera_id)
    if not cal_dir.exists():
        print(f"ERROR: Calibration directory not found: {cal_dir}")
        print(f"Run calibration_capture.py first to capture images.")
        sys.exit(1)

    # Process images
    object_points, image_points, image_size = process_calibration_images(cal_dir, show_detections)

    if object_points is None or len(object_points) == 0:
        print("ERROR: No valid calibration images found")
        sys.exit(1)

    # Run calibration
    rms_error, camera_matrix, dist_coeffs, rvecs, tvecs = calibrate_camera(
        object_points, image_points, image_size
    )

    # Compute FOV
    fov = compute_fov(camera_matrix, image_size)

    # Print summary
    print_calibration_summary(camera_matrix, dist_coeffs, image_size, fov)

    # Save results
    calibration_data = save_calibration(
        cal_dir, camera_id, camera_matrix, dist_coeffs, image_size, rms_error
    )

    print(f"\nTo update ModelT with intrinsics, run:")
    print(f"  python calibration_update_modelt.py {facility_name}")

    return 0


if __name__ == "__main__":
    exit(main())
