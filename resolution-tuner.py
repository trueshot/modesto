#!/usr/bin/env python
"""
Resolution Auto-Tuner for AprilTag Detection
Author: modeltapriltagcat gen-18

Cycles through camera resolutions, measures detection quality at each,
finds the optimal resolution for AprilTag detection.

Usage:
    python resolution-tuner.py <camera-ip>

APIs used:
    - Camera service (8001): resolution control via duplex mode, version info
    - FastTag (8003): detection metrics
    - lodge.db: sensor_profiles table for per-sensor resolution support
"""

import sys
import time
import json
import sqlite3
import requests
import numpy as np
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

CAMERA_SERVICE = "http://127.0.0.1:8001"
FASTTAG_SERVICE = "http://127.0.0.1:8003"
DB_PATH = Path("C:/clients/modesto/warehouses/lodge/lodge.db")

# Preset name to dimensions mapping
RESOLUTION_DIMS = {
    "QVGA": "320x240",
    "VGA": "640x480",
    "SVGA": "800x600",
    "XGA": "1024x768",
    "UXGA": "1600x1200",
}

SAMPLE_DURATION_SEC = 120  # 2 minutes per resolution
WARMUP_SEC = 10  # wait after resolution change before sampling
DARKNESS_THRESHOLD = 80  # mean pixel value below this = likely too dark for reliable detection


def get_camera_sketch(camera_ip: str) -> Optional[str]:
    """Get sketch name from camera /version endpoint."""
    url = f"{CAMERA_SERVICE}/api/http-cameras/{camera_ip}/version"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("sketch")
        return None
    except Exception as e:
        print(f"  Warning: couldn't get version ({e})")
        return None


def get_sensor_profile(sketch: str) -> tuple[list[str], str]:
    """
    Query sensor_profiles table for resolutions supported by this sketch.
    Returns (resolution_list, default_resolution).
    Fallback: (["SVGA"], "SVGA") if sketch not found or DB error.
    """
    fallback = (["SVGA"], "SVGA")

    if not DB_PATH.exists():
        print(f"  Warning: DB not found at {DB_PATH}, using fallback")
        return fallback

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT resolutions, default_res FROM sensor_profiles WHERE sketch = ?",
            (sketch,)
        )
        row = cur.fetchone()
        conn.close()

        if row:
            resolutions = json.loads(row[0])
            default_res = row[1]
            return (resolutions, default_res)
        else:
            print(f"  Warning: sketch '{sketch}' not in sensor_profiles, using fallback")
            return fallback

    except Exception as e:
        print(f"  Warning: DB query failed ({e}), using fallback")
        return fallback


def check_brightness(camera_ip: str) -> tuple[bool, float]:
    """
    Capture a frame and check if scene is too dark for detection.
    Returns (is_bright_enough, mean_pixel_value).
    """
    url = f"{CAMERA_SERVICE}/api/http-cameras/{camera_ip}/frame"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            print(f"  Warning: couldn't fetch frame for brightness check ({resp.status_code})")
            return True, -1  # assume OK if we can't check

        img_array = np.frombuffer(resp.content, dtype=np.uint8)
        # Decode JPEG - just need grayscale for brightness
        import cv2
        gray = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            return True, -1

        mean_val = float(np.mean(gray))
        return mean_val >= DARKNESS_THRESHOLD, mean_val

    except Exception as e:
        print(f"  Warning: brightness check failed ({e})")
        return True, -1  # assume OK on error


@dataclass
class ResolutionResult:
    preset: str
    dimensions: str
    detection_count: int
    margin_min: float
    margin_avg: float
    margin_max: float
    margin_above_50: int
    margin_above_100: int
    unique_tags: int
    fps: float
    sample_duration: float


def enable_duplex(camera_ip: str) -> bool:
    """Enable duplex mode on camera (required for resolution control)."""
    url = f"{CAMERA_SERVICE}/api/http-cameras/{camera_ip}/duplex/enable"
    try:
        resp = requests.post(url, timeout=10)
        if resp.status_code == 200:
            print(f"  Duplex mode enabled on {camera_ip}")
            return True
        else:
            print(f"  Failed to enable duplex: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"  Error enabling duplex: {e}")
        return False


def set_resolution(camera_ip: str, preset: str) -> bool:
    """Set camera resolution by preset name."""
    url = f"{CAMERA_SERVICE}/api/http-cameras/{camera_ip}/command"
    payload = {"cmd": {"action": "set_resolution", "preset": preset}}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"  Resolution set to {preset}")
            return True
        else:
            print(f"  Failed to set resolution: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"  Error setting resolution: {e}")
        return False


def start_fasttag(camera_ip: str, auto_stop_seconds: int = 0) -> bool:
    """Start FastTag detection on camera with optional auto-stop."""
    url = f"{FASTTAG_SERVICE}/start"
    payload = {"cameras": [camera_ip]}
    if auto_stop_seconds > 0:
        payload["auto_stop_seconds"] = auto_stop_seconds
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"  FastTag started on {camera_ip}")
            if auto_stop_seconds > 0:
                print(f"  Auto-stop in {auto_stop_seconds}s ({auto_stop_seconds // 60}m)")
            return True
        else:
            print(f"  Failed to start FastTag: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"  Error starting FastTag: {e}")
        return False


def get_fasttag_status(camera_ip: str) -> Optional[dict]:
    """Get FastTag status for camera."""
    url = f"{FASTTAG_SERVICE}/status"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("cameras", {}).get(camera_ip)
        return None
    except Exception:
        return None


def get_detections(camera_ip: str, since_ts: float) -> list:
    """Get detections since timestamp."""
    url = f"{FASTTAG_SERVICE}/detections"
    params = {"camera": camera_ip, "since": since_ts}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("detections", [])
        return []
    except Exception:
        return []


def compute_corner_span(corners: list) -> float:
    """Compute diagonal span of tag corners in pixels."""
    if len(corners) != 4:
        return 0.0
    c0, c2 = corners[0], corners[2]
    dx = c2[0] - c0[0]
    dy = c2[1] - c0[1]
    return (dx**2 + dy**2) ** 0.5


def sample_resolution(camera_ip: str, preset: str, dimensions: str) -> ResolutionResult:
    """Sample detection quality at current resolution."""
    print(f"\n  Sampling {preset} ({dimensions}) for {SAMPLE_DURATION_SEC}s...")

    start_ts = time.time()
    all_detections = []
    fps_samples = []

    while time.time() - start_ts < SAMPLE_DURATION_SEC:
        sample_start = time.time()

        # Get detections from last 5 seconds
        dets = get_detections(camera_ip, time.time() - 5)
        all_detections.extend(dets)

        # Get FPS from status
        status = get_fasttag_status(camera_ip)
        if status:
            fps_samples.append(status.get("fps", 0))

        # Sample every 5 seconds
        elapsed = time.time() - sample_start
        if elapsed < 5:
            time.sleep(5 - elapsed)

        # Progress
        progress = (time.time() - start_ts) / SAMPLE_DURATION_SEC * 100
        print(f"    {progress:.0f}% - {len(all_detections)} detections", end="\r")

    print()  # newline after progress

    # Deduplicate by (tag_id, frame_seq) if available
    seen = set()
    unique_dets = []
    for d in all_detections:
        key = (d.get("tag_id"), d.get("frame_seq", id(d)))
        if key not in seen:
            seen.add(key)
            unique_dets.append(d)

    # Compute stats
    margins = [d.get("decision_margin", 0) for d in unique_dets]
    unique_tags = len(set(d.get("tag_id") for d in unique_dets))

    if not margins:
        return ResolutionResult(
            preset=preset,
            dimensions=dimensions,
            detection_count=0,
            margin_min=0,
            margin_avg=0,
            margin_max=0,
            margin_above_50=0,
            margin_above_100=0,
            unique_tags=0,
            fps=sum(fps_samples) / len(fps_samples) if fps_samples else 0,
            sample_duration=SAMPLE_DURATION_SEC,
        )

    return ResolutionResult(
        preset=preset,
        dimensions=dimensions,
        detection_count=len(unique_dets),
        margin_min=min(margins),
        margin_avg=sum(margins) / len(margins),
        margin_max=max(margins),
        margin_above_50=sum(1 for m in margins if m > 50),
        margin_above_100=sum(1 for m in margins if m > 100),
        unique_tags=unique_tags,
        fps=sum(fps_samples) / len(fps_samples) if fps_samples else 0,
        sample_duration=SAMPLE_DURATION_SEC,
    )


def print_result(r: ResolutionResult):
    """Print result summary."""
    print(f"\n  {r.preset} ({r.dimensions}):")
    print(f"    Detections: {r.detection_count} ({r.unique_tags} unique tags)")
    print(f"    Margin: min={r.margin_min:.1f}, avg={r.margin_avg:.1f}, max={r.margin_max:.1f}")
    print(f"    Quality: {r.margin_above_50} above 50, {r.margin_above_100} above 100")
    print(f"    FPS: {r.fps:.1f}")


def recommend(results: list[ResolutionResult]) -> Optional[ResolutionResult]:
    """Recommend best resolution based on min(margin) > 50 + max count."""
    # Filter to results with min margin > 50
    reliable = [r for r in results if r.margin_min > 50 and r.detection_count > 0]

    if not reliable:
        # Fall back to highest min margin
        valid = [r for r in results if r.detection_count > 0]
        if not valid:
            return None
        return max(valid, key=lambda r: r.margin_min)

    # Among reliable, pick highest detection count
    return max(reliable, key=lambda r: r.detection_count)


def main():
    if len(sys.argv) < 2:
        print("Usage: python resolution-tuner.py <camera-ip>")
        print("Example: python resolution-tuner.py 192.168.0.250")
        sys.exit(1)

    camera_ip = sys.argv[1]
    print(f"\n{'='*60}")
    print(f"Resolution Auto-Tuner — Camera {camera_ip}")
    print(f"{'='*60}")

    # Setup
    print("\n[1/6] Enabling duplex mode...")
    if not enable_duplex(camera_ip):
        print("Failed to enable duplex mode. Aborting.")
        sys.exit(1)

    print("\n[2/6] Detecting sensor type...")
    sketch = get_camera_sketch(camera_ip)
    if sketch:
        print(f"  Sketch: {sketch}")
    else:
        print("  Could not detect sketch, using fallback profile")
        sketch = "__unknown__"

    resolutions, default_res = get_sensor_profile(sketch)
    print(f"  Resolutions: {resolutions}")
    print(f"  Default: {default_res}")

    print("\n[3/6] Checking scene brightness...")
    is_bright, mean_val = check_brightness(camera_ip)
    if not is_bright:
        print(f"  Scene too dark (mean={mean_val:.1f}, threshold={DARKNESS_THRESHOLD}).")
        print("  Lights may be off. Aborting.")
        sys.exit(1)
    print(f"  Brightness OK (mean={mean_val:.1f})")

    # Calculate estimated run time for auto-stop
    # (resolutions × (sample + warmup)) + setup overhead + 30 min buffer
    estimated_run = len(resolutions) * (SAMPLE_DURATION_SEC + WARMUP_SEC) + 60
    auto_stop = estimated_run + 1800

    print("\n[4/6] Starting FastTag detection...")
    if not start_fasttag(camera_ip, auto_stop_seconds=auto_stop):
        print("Failed to start FastTag. Aborting.")
        sys.exit(1)

    # Wait for detection to stabilize
    print("  Waiting 10s for detection to stabilize...")
    time.sleep(10)

    # Test each resolution (low→high for faster warmup at low res)
    print(f"\n[5/6] Testing {len(resolutions)} resolutions...")
    results = []

    for preset in resolutions:
        dimensions = RESOLUTION_DIMS.get(preset, "?x?")
        print(f"\n  Setting resolution to {preset}...")
        if not set_resolution(camera_ip, preset):
            print(f"  Skipping {preset} — failed to set")
            continue

        print(f"  Warming up for {WARMUP_SEC}s...")
        time.sleep(WARMUP_SEC)

        result = sample_resolution(camera_ip, preset, dimensions)
        results.append(result)
        print_result(result)

    # Reset to sensor default
    print(f"\n[6/6] Resetting to {default_res} (sensor default)...")
    set_resolution(camera_ip, default_res)

    # Summary
    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")

    for r in results:
        quality = "RELIABLE" if r.margin_min > 50 else "MARGINAL" if r.margin_min > 20 else "POOR"
        print(f"  {r.preset:6} | min={r.margin_min:5.1f} | avg={r.margin_avg:5.1f} | count={r.detection_count:4} | {quality}")

    # Recommendation
    best = recommend(results)
    if best:
        print(f"\n  RECOMMENDATION: {best.preset} ({best.dimensions})")
        if best.margin_min > 50:
            print(f"    Reason: min margin {best.margin_min:.1f} > 50 (reliable), {best.detection_count} detections")
        else:
            print(f"    Reason: highest min margin ({best.margin_min:.1f}) — but still below 50 threshold")
            print(f"    WARNING: All resolutions produced marginal/poor detections")
    else:
        print("\n  No recommendation — insufficient data")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
