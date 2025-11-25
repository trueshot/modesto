#!/usr/bin/env python
"""
Grab Current Screenshots from All NVR Channels
Captures one frame from each channel and saves to timestamped directory

Usage: python grab_all_screenshots.py <facility>
Example: python grab_all_screenshots.py lodge
"""

import cv2
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# Thread-safe results
results_lock = threading.Lock()
results = {
    'successful': 0,
    'failed': 0,
    'channels': {}
}

def load_config(facility_name):
    """Load camera configuration for a facility"""
    config_path = Path(__file__).parent.parent / "warehouses" / facility_name / "cameras" / "config.json"

    if not config_path.exists():
        print(f"ERROR: Config not found at {config_path}")
        sys.exit(1)

    with open(config_path, 'r') as f:
        return json.load(f)

def capture_channel(channel_info, output_dir):
    """Capture a single frame from a channel"""

    channel_num = channel_info['channel']
    rtsp_url = channel_info['rtspUrl']
    camera_id = channel_info['modelTCameraId']
    camera_name = channel_info['modelTCameraName']

    print(f"[CH{channel_num:02d}] {camera_name:10} Connecting...", end=" ", flush=True)

    try:
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)

        if not cap.isOpened():
            print("[FAIL]")
            with results_lock:
                results['failed'] += 1
                results['channels'][channel_num] = {
                    'status': 'failed',
                    'reason': 'Could not connect',
                    'camera_id': camera_id,
                    'camera_name': camera_name
                }
            return

        # Try to read a frame
        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            print("[FAIL]")
            with results_lock:
                results['failed'] += 1
                results['channels'][channel_num] = {
                    'status': 'failed',
                    'reason': 'Could not capture frame',
                    'camera_id': camera_id,
                    'camera_name': camera_name
                }
            return

        # Get resolution
        height, width = frame.shape[:2]
        print(f"[OK] {width}x{height}", end=" ", flush=True)

        # Save frame with camera name
        filename = f"ch{channel_num:02d}_{camera_id}_{width}x{height}.jpg"
        filepath = os.path.join(output_dir, filename)
        cv2.imwrite(filepath, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])

        print(f"[SAVED]")

        with results_lock:
            results['successful'] += 1
            results['channels'][channel_num] = {
                'status': 'success',
                'resolution': f"{width}x{height}",
                'filename': filename,
                'camera_id': camera_id,
                'camera_name': camera_name
            }

    except Exception as e:
        print(f"[ERROR] {e}")
        with results_lock:
            results['failed'] += 1
            results['channels'][channel_num] = {
                'status': 'error',
                'reason': str(e),
                'camera_id': camera_id,
                'camera_name': camera_name
            }


def main():
    # Check for facility argument
    if len(sys.argv) < 2:
        print("Usage: python grab_all_screenshots.py <facility>")
        print("Example: python grab_all_screenshots.py lodge")
        sys.exit(1)

    facility_name = sys.argv[1]

    # Load facility config
    config = load_config(facility_name)

    # Create output directory with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f"{facility_name}_screenshots_{timestamp}"
    os.makedirs(output_dir, exist_ok=True)

    print("="*70)
    print(f"GRAB ALL NVR CHANNEL SCREENSHOTS - {config['facilityName']}")
    print("="*70)
    print(f"Facility: {facility_name}")
    print(f"Location: {config['location']}")
    print(f"NVR: {config['nvr']['ip']}")
    print(f"Channels: {len(config['channels'])}")
    print(f"Output: {output_dir}/")
    print("="*70 + "\n")

    # Start capture threads for all channels
    threads = []
    for channel_info in config['channels']:
        t = threading.Thread(
            target=capture_channel,
            args=(channel_info, output_dir),
            daemon=False
        )
        t.start()
        threads.append(t)
        time.sleep(0.1)  # Stagger the starts

    # Wait for all threads to complete
    for t in threads:
        t.join()

    # Print summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Total channels: {len(config['channels'])}")
    print(f"Successful:    {results['successful']}")
    print(f"Failed:        {results['failed']}")
    print(f"\nOutput directory: {output_dir}/")

    if results['successful'] > 0:
        print("\nCaptured channels:")
        for ch_num in sorted(results['channels'].keys()):
            ch = results['channels'][ch_num]
            if ch['status'] == 'success':
                print(f"  CH{ch_num:02d}: {ch['camera_name']:10} ({ch['camera_id']:10}) {ch['resolution']} -> {ch['filename']}")

    if results['failed'] > 0:
        print("\nFailed channels:")
        for ch_num in sorted(results['channels'].keys()):
            ch = results['channels'][ch_num]
            if ch['status'] != 'success':
                print(f"  CH{ch_num:02d}: {ch['camera_name']:10} ({ch['camera_id']:10}) {ch['reason']}")

    print("="*70 + "\n")

    return 0 if results['failed'] == 0 else 1


if __name__ == "__main__":
    exit(main())
