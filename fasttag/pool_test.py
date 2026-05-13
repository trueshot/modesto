#!/usr/bin/env python3
"""
Test the pooled detection system.

Usage:
    python pool_test.py

Starts 3 detectors, adds a test camera, runs for 30 seconds.
"""

import os
import sys
import time
import json
import logging
from pathlib import Path

# Load .env for credentials
ENV_PATH = Path(__file__).parent.parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)

from pool import DetectionPool

def main():
    # Test camera
    camera_ip = "192.168.0.105"
    rtsp_url = "rtsp://admin:123456@192.168.0.105:554/Streaming/Channels/101"

    print("=" * 60)
    print("  Pooled Detection Test")
    print("=" * 60)
    print(f"  Camera: {camera_ip}")
    print(f"  Detectors: 3")
    print("=" * 60)
    print()

    # Create pool with 3 detectors
    print("Creating DetectionPool (this will allocate ~15GB for 3 detectors)...")
    pool = DetectionPool(
        num_detectors=3,
        num_slots=30,
        slot_size=30_000_000,  # 30MB per slot (4K frames)
    )

    print("Starting pool...")
    pool.start()

    print(f"Adding camera {camera_ip}...")
    pool.add_camera(camera_ip, rtsp_url, target_fps=5.0)

    print()
    print("Running for 30 seconds. Check memory usage!")
    print("Expected: ~15GB for detectors + ~1GB for frame pool = ~16GB")
    print()

    start = time.time()
    detection_count = 0
    detector_frames = {}  # process_id -> frames_processed

    try:
        while time.time() - start < 30:
            # Drain results
            results = pool.get_results()
            for r in results:
                if r["type"] == "detection":
                    detection_count += 1
                    print(f"  Tag #{r['tag_id']} on {r['camera_ip']} (margin {r['decision_margin']:.0f})")
                elif r["type"] == "heartbeat" and "frames_processed" in r:
                    detector_frames[r["process_id"]] = r["frames_processed"]

            # Print status every 2 seconds
            elapsed = int(time.time() - start)
            if elapsed % 2 == 0 and elapsed > 0:
                status = pool.get_status()
                reader = status["readers"].get(camera_ip, {})
                dets = [d for d in status.get("detectors", []) if d.get("alive")]
                print(f"  [{elapsed:2d}s] reader={reader.get('status','?'):12s} fps={reader.get('fps', 0):.1f} frames={reader.get('frame_count', 0):4d}")
                print(f"        grabs={reader.get('grabs',0)} skip_grey={reader.get('skipped_grey',0)} last_std={reader.get('last_std',0):.1f}")
                det_frames = sum(detector_frames.values())
                print(f"        detectors={len(dets)} det_frames={det_frames} detections={detection_count}")

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nInterrupted")

    print()
    print(f"Total detections: {detection_count}")
    print()

    print("Stopping pool...")
    pool.stop()

    print("Done!")


if __name__ == "__main__":
    main()
