#!/usr/bin/env python
"""
Update ModelT with Camera Intrinsic Calibration Data

Reads calibration results from each camera's calibration.json and
adds intrinsic parameters to the lodge.modelT.json file.

Usage: python calibration_update_modelt.py <facility>
Example: python calibration_update_modelt.py lodge
"""

import json
import sys
from pathlib import Path
from datetime import datetime


def load_modelt(facility_name):
    """Load ModelT JSON for a facility"""
    modelt_path = Path(__file__).parent.parent / "warehouses" / facility_name / f"{facility_name}.modelT.json"

    if not modelt_path.exists():
        print(f"ERROR: ModelT not found at {modelt_path}")
        sys.exit(1)

    with open(modelt_path, 'r') as f:
        return json.load(f), modelt_path


def get_calibration_files(facility_name):
    """Find all camera calibration files"""
    cal_base = Path(__file__).parent.parent / "warehouses" / facility_name / "calibration"

    if not cal_base.exists():
        return {}

    calibrations = {}
    for camera_dir in cal_base.iterdir():
        if camera_dir.is_dir():
            cal_file = camera_dir / "calibration.json"
            if cal_file.exists():
                with open(cal_file, 'r') as f:
                    calibrations[camera_dir.name] = json.load(f)

    return calibrations


def update_camera_intrinsics(modelt, calibrations):
    """Update camera entries in ModelT with intrinsic data"""

    updated = 0
    not_found = []

    for slab in modelt.get('slabs', []):
        for camera in slab.get('cameras', []):
            camera_id = camera['id']

            if camera_id in calibrations:
                cal = calibrations[camera_id]

                # Add intrinsics to camera object
                camera['intrinsics'] = {
                    "calibrated": True,
                    "calibration_date": cal['calibration_date'],
                    "image_size": cal['image_size'],
                    "fx": cal['camera_matrix']['fx'],
                    "fy": cal['camera_matrix']['fy'],
                    "cx": cal['camera_matrix']['cx'],
                    "cy": cal['camera_matrix']['cy'],
                    "k1": cal['distortion_coefficients']['k1'],
                    "k2": cal['distortion_coefficients']['k2'],
                    "p1": cal['distortion_coefficients']['p1'],
                    "p2": cal['distortion_coefficients']['p2'],
                    "k3": cal['distortion_coefficients'].get('k3', 0.0),
                    "rms_error": cal['rms_reprojection_error'],
                    "fov_horizontal": cal['field_of_view']['horizontal_deg'],
                    "fov_vertical": cal['field_of_view']['vertical_deg']
                }

                updated += 1
                print(f"  [UPDATED] {camera_id} - {camera.get('name', 'unnamed')}")
            else:
                not_found.append(camera_id)

    return updated, not_found


def main():
    if len(sys.argv) < 2:
        print("Usage: python calibration_update_modelt.py <facility>")
        print("Example: python calibration_update_modelt.py lodge")
        sys.exit(1)

    facility_name = sys.argv[1]

    print(f"Updating ModelT intrinsics for facility: {facility_name}")
    print("="*60)

    # Load ModelT
    modelt, modelt_path = load_modelt(facility_name)

    # Find calibration files
    calibrations = get_calibration_files(facility_name)

    if not calibrations:
        print("ERROR: No calibration files found")
        print(f"Run calibration_capture.py and calibration_process.py first")
        sys.exit(1)

    print(f"Found {len(calibrations)} calibration file(s):")
    for cam_id in calibrations:
        print(f"  - {cam_id}")

    print()

    # Update ModelT
    updated, not_found = update_camera_intrinsics(modelt, calibrations)

    if not_found:
        print(f"\nCameras without calibration ({len(not_found)}):")
        for cam_id in not_found:
            print(f"  - {cam_id}")

    if updated > 0:
        # Backup original
        backup_path = modelt_path.with_suffix('.json.bak')
        with open(backup_path, 'w') as f:
            json.dump(modelt, f, indent=2)
        print(f"\nBackup saved to: {backup_path}")

        # Save updated ModelT
        with open(modelt_path, 'w') as f:
            json.dump(modelt, f, indent=2)

        print(f"Updated ModelT saved to: {modelt_path}")

    print(f"\n{'='*60}")
    print(f"Summary: {updated} cameras updated, {len(not_found)} without calibration")
    print("="*60)

    return 0


if __name__ == "__main__":
    exit(main())
