#!/usr/bin/env python
"""
AprilTag Detection Service
FastAPI endpoint that fetches frames, runs detection, returns annotated JPEGs.
Supports both HTTP and ZMQ frame sources for comparison.

Author: modeltapriltagcat gen-4

Usage:
  python detection-service.py [--port 8002]

Endpoints:
  GET /detect/{nvr}/{channel}?source=http|zmq  - Returns annotated JPEG
  GET /cameras                                  - List available cameras
  GET /stats                                    - Detection timing stats
"""

import cv2
import numpy as np
import requests
import zmq
import time
import json
from fastapi import FastAPI, Response, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from pupil_apriltags import Detector
from collections import defaultdict
import threading

app = FastAPI(title="AprilTag Detection Service")

# CORS for test page
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
CAMERA_SERVICE_URL = "http://localhost:8001"
ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"

# Stats tracking
stats = defaultdict(lambda: {"count": 0, "total_fetch_ms": 0, "total_detect_ms": 0, "total_tags": 0})
stats_lock = threading.Lock()

# Initialize detectors
print("Initializing AprilTag detectors...")
detectors = {
    'Fiducial': {
        'detector': Detector(families='tag36h11', nthreads=4, quad_decimate=1.0,
                            quad_sigma=0.0, refine_edges=1, decode_sharpening=0.25),
        'color': (0, 255, 0)  # Green
    },
    'Forklift': {
        'detector': Detector(families='tag25h9', nthreads=4, quad_decimate=1.0,
                            quad_sigma=0.0, refine_edges=1, decode_sharpening=0.25),
        'color': (0, 165, 255)  # Orange
    },
    'Pallet': {
        'detector': Detector(families='tagStandard41h12', nthreads=4, quad_decimate=1.0,
                            quad_sigma=0.0, refine_edges=1, decode_sharpening=0.25),
        'color': (255, 0, 255)  # Magenta
    },
    'Reserved': {
        'detector': Detector(families='tagStandard52h13', nthreads=4, quad_decimate=1.0,
                            quad_sigma=0.0, refine_edges=1, decode_sharpening=0.25),
        'color': (255, 255, 0)  # Cyan
    },
}
print("Detectors ready.")

# ZMQ context (lazy init)
zmq_context = None
zmq_socket = None

def get_zmq_socket():
    """Get or create ZMQ socket"""
    global zmq_context, zmq_socket
    if zmq_socket is None:
        zmq_context = zmq.Context()
        zmq_socket = zmq_context.socket(zmq.REQ)
        zmq_socket.connect(ZMQ_ENDPOINT)
    return zmq_socket


def fetch_frame_http(nvr: str, channel: int, use_snapshot: bool = False) -> tuple[np.ndarray | None, float]:
    """Fetch frame via HTTP, return (frame, fetch_time_ms)"""
    start = time.perf_counter()
    try:
        params = {"format": "image"}
        if use_snapshot:
            params["source"] = "snapshot"
        response = requests.get(
            f"{CAMERA_SERVICE_URL}/api/nvr/{nvr}/channel/{channel}/frame",
            params=params,
            timeout=10
        )
        if response.status_code == 200:
            arr = np.frombuffer(response.content, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            return frame, (time.perf_counter() - start) * 1000
    except Exception as e:
        print(f"HTTP fetch error: {e}")
    return None, (time.perf_counter() - start) * 1000


def fetch_frame_zmq(nvr: str, channel: int, use_snapshot: bool = False) -> tuple[np.ndarray | None, float]:
    """Fetch frame via ZMQ, return (frame, fetch_time_ms)"""
    start = time.perf_counter()
    try:
        sock = get_zmq_socket()
        source = "snapshot" if use_snapshot else "rtsp"
        sock.send_json({"nvr": nvr, "channel": channel, "source": source})
        jpeg_bytes = sock.recv()
        if len(jpeg_bytes) > 0:
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            return frame, (time.perf_counter() - start) * 1000
    except Exception as e:
        print(f"ZMQ fetch error: {e}")
    return None, (time.perf_counter() - start) * 1000


def run_detection(frame: np.ndarray) -> tuple[list, float]:
    """Run all detectors, return (detections, detect_time_ms)"""
    start = time.perf_counter()

    # Resize for detection (50% for speed)
    h, w = frame.shape[:2]
    scale = 0.5
    small = cv2.resize(frame, (int(w * scale), int(h * scale)))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    # CLAHE enhancement
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    all_detections = []
    for asset_name, config in detectors.items():
        dets = config['detector'].detect(gray, estimate_tag_pose=False)
        for d in dets:
            d.asset_name = asset_name
            d.color = config['color']
            # Scale back to full resolution
            d.center = d.center / scale
            d.corners = d.corners / scale
            all_detections.append(d)

    return all_detections, (time.perf_counter() - start) * 1000


def draw_detections(frame: np.ndarray, detections: list) -> np.ndarray:
    """Draw detection overlays on frame"""
    for det in detections:
        color = det.color
        corners = det.corners.astype(int)

        # Draw border
        for i in range(4):
            pt1 = tuple(corners[i])
            pt2 = tuple(corners[(i + 1) % 4])
            cv2.line(frame, pt1, pt2, color, 3)

        # Draw center
        center = (int(det.center[0]), int(det.center[1]))
        cv2.circle(frame, center, 8, (0, 0, 255), -1)

        # Label
        label = f"{det.asset_name} #{det.tag_id}"
        cv2.putText(frame, label, (center[0] - 60, center[1] - 25),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.putText(frame, f"{det.decision_margin:.1f}", (center[0] - 30, center[1] + 35),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)

    return frame


@app.get("/detect/{nvr}/{channel}")
async def detect(
    nvr: str,
    channel: int,
    transport: str = Query("http", pattern="^(http|zmq)$"),
    snapshot: bool = Query(False)
):
    """Fetch frame, detect tags, return annotated JPEG"""

    # Fetch frame
    if transport == "zmq":
        frame, fetch_ms = fetch_frame_zmq(nvr, channel, use_snapshot=snapshot)
    else:
        frame, fetch_ms = fetch_frame_http(nvr, channel, use_snapshot=snapshot)

    # For stats tracking
    source = f"{transport}{'+snap' if snapshot else ''}"

    if frame is None:
        return Response(content=b"", media_type="image/jpeg", status_code=404)

    # Detect
    detections, detect_ms = run_detection(frame)

    # Draw overlays
    annotated = draw_detections(frame.copy(), detections)

    # Add timing overlay
    h, w = annotated.shape[:2]
    source_label = transport.upper() + ("+SNAP" if snapshot else "")
    cv2.rectangle(annotated, (0, 0), (450, 110), (40, 40, 40), -1)
    cv2.putText(annotated, f"{w}x{h}", (10, 28),
               cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.putText(annotated, f"Fetch: {fetch_ms:.0f}ms ({source_label})", (10, 58),
               cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    cv2.putText(annotated, f"Detect: {detect_ms:.0f}ms | Tags: {len(detections)}", (10, 88),
               cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

    # Resize for web (max 800px wide)
    if w > 800:
        scale = 800 / w
        annotated = cv2.resize(annotated, (800, int(h * scale)))

    # Encode JPEG
    _, jpeg = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])

    # Update stats
    key = f"{nvr}/{channel}/{source}"
    with stats_lock:
        stats[key]["count"] += 1
        stats[key]["total_fetch_ms"] += fetch_ms
        stats[key]["total_detect_ms"] += detect_ms
        stats[key]["total_tags"] += len(detections)

    return Response(
        content=jpeg.tobytes(),
        media_type="image/jpeg",
        headers={
            "X-Resolution": f"{w}x{h}",
            "X-Fetch-Ms": str(int(fetch_ms)),
            "X-Detect-Ms": str(int(detect_ms)),
            "X-Tags": str(len(detections)),
        }
    )


@app.get("/cameras")
async def list_cameras():
    """List active cameras from camera-service"""
    cameras = []
    try:
        # Get configured cameras from lodge
        response = requests.get(f"{CAMERA_SERVICE_URL}/api/cameras/lodge", timeout=5)
        if response.status_code == 200:
            data = response.json()
            for cam in data.get("cameras", []):
                cameras.append({
                    "nvr": cam.get("nvr_id", "nvr1"),
                    "channel": cam.get("channel"),
                    "name": cam.get("name", f"ch{cam.get('channel')}"),
                    "resolution": cam.get("resolution"),
                    "snapshot": False,  # nvr1 doesn't support snapshots
                })
    except Exception as e:
        print(f"Error fetching cameras: {e}")

    # Add nvr2 channels 2-5 (unconfigured but active, supports snapshots)
    for ch in [2, 3, 4, 5]:
        cameras.append({
            "nvr": "nvr2",
            "channel": ch,
            "name": f"nvr2-ch{ch}",
            "resolution": "unknown",
            "snapshot": True,  # nvr2 (UNIVIEW) supports snapshots
        })

    if not cameras:
        # Fallback
        cameras = [{"nvr": "nvr1", "channel": 7, "name": "biscuit", "snapshot": False}]

    return {"cameras": cameras, "count": len(cameras)}


@app.get("/stats")
async def get_stats():
    """Return detection timing stats"""
    with stats_lock:
        result = {}
        for key, s in stats.items():
            if s["count"] > 0:
                result[key] = {
                    "count": s["count"],
                    "avg_fetch_ms": round(s["total_fetch_ms"] / s["count"], 1),
                    "avg_detect_ms": round(s["total_detect_ms"] / s["count"], 1),
                    "avg_tags": round(s["total_tags"] / s["count"], 2),
                }
        return result


@app.post("/stats/reset")
async def reset_stats():
    """Reset stats"""
    with stats_lock:
        stats.clear()
    return {"status": "reset"}


@app.get("/test", response_class=HTMLResponse)
async def test_page():
    """Serve the detection test HTML page"""
    html_path = Path(__file__).parent / "detection-test.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>detection-test.html not found</h1>"


if __name__ == "__main__":
    import uvicorn
    print("Starting detection service on port 8002...")
    uvicorn.run(app, host="0.0.0.0", port=8002)
