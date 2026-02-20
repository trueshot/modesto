#!/usr/bin/env python
"""
AprilTag Detection Service
FastAPI endpoint that fetches frames, runs detection, returns annotated JPEGs.
Supports both HTTP and ZMQ frame sources for comparison.

Author: modeltapriltagcat gen-4
Updated: modeltapriltagcat gen-6 (pass-based API)

Usage:
  python detection-service.py [--port 8002]

Endpoints:
  GET /detect/{nvr}/{channel}?source=http|zmq  - Returns annotated JPEG (legacy)
  GET /cameras                                  - List available cameras
  GET /stats                                    - Detection timing stats

  POST /pass/start                              - Start a detection pass (all cameras parallel)
  GET /pass/{passId}/results                    - Poll for pass results
  GET /pass/{passId}/image/{nvr}/{channel}      - Get image for specific camera
"""

import cv2
import numpy as np
import requests
import zmq
import time
import json
import uuid
import asyncio
from fastapi import FastAPI, Response, Query
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from pupil_apriltags import Detector
from collections import defaultdict
import threading
import math
import os

app = FastAPI(title="AprilTag Detection Service")

# CORS for test page
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
CAMERA_SERVICE_URL = "http://127.0.0.1:8001"
ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"

# Stats tracking
stats = defaultdict(lambda: {"count": 0, "total_fetch_ms": 0, "total_detect_ms": 0, "total_tags": 0})
stats_lock = threading.Lock()

# Pass storage: passId -> { cameras: [...], results: {...}, images: {...}, startTime, done }
passes = {}
passes_lock = threading.Lock()
MAX_PASSES = 10  # Keep last N passes in memory

# Initialize detectors
print("Initializing AprilTag detectors...")
detectors = {
    'Fiducial36h11': {
        'detector': Detector(families='tag36h11', nthreads=4, quad_decimate=1.0,
                            quad_sigma=0.0, refine_edges=1, decode_sharpening=0.25),
        'color': (0, 255, 0)  # Green
    },
    'Forklift25h9': {
        'detector': Detector(families='tag25h9', nthreads=4, quad_decimate=1.0,
                            quad_sigma=0.0, refine_edges=1, decode_sharpening=0.25),
        'color': (0, 165, 255)  # Orange
    },
    'Pallet41h12': {
        'detector': Detector(families='tagStandard41h12', nthreads=4, quad_decimate=1.0,
                            quad_sigma=0.0, refine_edges=1, decode_sharpening=0.25),
        'color': (255, 0, 255)  # Magenta
    },
    'Pallet52h13': {
        'detector': Detector(families='tagStandard52h13', nthreads=4, quad_decimate=1.0,
                            quad_sigma=0.0, refine_edges=1, decode_sharpening=0.25),
        'color': (255, 255, 0)  # Cyan
    },
    'Pallet48h12': {
        'detector': Detector(families='tagCustom48h12', nthreads=4, quad_decimate=1.0,
                            quad_sigma=0.0, refine_edges=1, decode_sharpening=0.25),
        'color': (0, 200, 200)  # Teal
    },
}
print("Detectors ready.")

# OpenCV ArUco fallback detectors (better perspective tolerance)
print("Initializing ArUco fallback detectors...")
aruco_detectors = {
    'Fiducial36h11': {
        'dictionary': cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11),
        'params': cv2.aruco.DetectorParameters(),
        'color': (0, 255, 0),
    },
    'Forklift25h9': {
        'dictionary': cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_25h9),
        'params': cv2.aruco.DetectorParameters(),
        'color': (0, 165, 255),
    },
}
# Build ArUco detector objects
for cfg in aruco_detectors.values():
    cfg['detector'] = cv2.aruco.ArucoDetector(cfg['dictionary'], cfg['params'])
print("ArUco fallback detectors ready.")


class ArUcoDetection:
    """Wrapper to match pupil_apriltags detection interface"""
    def __init__(self, tag_id, corners, asset_name, color):
        self.tag_id = tag_id
        self.corners = corners  # 4x2 numpy array
        self.center = corners.mean(axis=0)
        self.decision_margin = -1  # ArUco doesn't provide this
        self.asset_name = asset_name
        self.color = color


# ZMQ REQ socket (legacy, synchronous)
zmq_context = None
zmq_req_socket = None

def get_zmq_req_socket():
    """Get or create ZMQ REQ socket (synchronous)"""
    global zmq_context, zmq_req_socket
    if zmq_req_socket is None:
        zmq_context = zmq.Context()
        zmq_req_socket = zmq_context.socket(zmq.REQ)
        zmq_req_socket.connect(ZMQ_ENDPOINT)
    return zmq_req_socket

# ZMQ DEALER socket (async, for parallel requests)
# Requires camera-service to use ROUTER on port 5556
ZMQ_DEALER_ENDPOINT = "tcp://127.0.0.1:5556"
zmq_dealer_socket = None
zmq_dealer_lock = threading.Lock()
zmq_pending_responses = {}  # request_id -> (asyncio.Event, result, loop)
zmq_event_loop = None  # Main asyncio loop for thread-safe signaling

def get_zmq_dealer_socket():
    """Get or create ZMQ DEALER socket"""
    global zmq_context, zmq_dealer_socket
    if zmq_dealer_socket is None:
        if zmq_context is None:
            zmq_context = zmq.Context()
        zmq_dealer_socket = zmq_context.socket(zmq.DEALER)
        zmq_dealer_socket.setsockopt_string(zmq.IDENTITY, f"detect-{uuid.uuid4().hex[:8]}")
        zmq_dealer_socket.connect(ZMQ_DEALER_ENDPOINT)
        # Start receiver thread
        receiver = threading.Thread(target=_dealer_receiver, daemon=True)
        receiver.start()
        print(f"DEALER socket connected to {ZMQ_DEALER_ENDPOINT}")
    return zmq_dealer_socket

def _dealer_receiver():
    """Background thread to receive DEALER responses"""
    global zmq_dealer_socket, zmq_pending_responses
    recv_count = 0
    while True:
        try:
            # DEALER receives: [empty, request_id, jpeg_bytes] (3 frames)
            # Or with timing: [empty, request_id, jpeg_bytes, timing_json] (4 frames)
            frames = zmq_dealer_socket.recv_multipart()
            recv_count += 1

            if len(frames) >= 3:
                _, request_id_bytes, jpeg_bytes = frames[:3]
                request_id = request_id_bytes.decode('utf-8')

                # Parse timing metadata if present (4th frame)
                timing_meta = None
                if len(frames) >= 4:
                    try:
                        timing_meta = json.loads(frames[3].decode('utf-8'))
                    except:
                        pass

                with zmq_dealer_lock:
                    if request_id in zmq_pending_responses:
                        event, _, _, loop = zmq_pending_responses[request_id]
                        zmq_pending_responses[request_id] = (event, jpeg_bytes, timing_meta, loop)
                        # Thread-safe signal to asyncio event
                        loop.call_soon_threadsafe(event.set)
        except Exception as e:
            print(f"DEALER receiver error: {e}")
            time.sleep(0.1)


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
    """Fetch frame via ZMQ REQ (synchronous), return (frame, fetch_time_ms)"""
    start = time.perf_counter()
    try:
        sock = get_zmq_req_socket()
        source = "snapshot" if use_snapshot else "rtsp"
        sock.send_json({"nvr": nvr, "channel": channel, "source": source})
        jpeg_bytes = sock.recv()
        if len(jpeg_bytes) > 0:
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            return frame, (time.perf_counter() - start) * 1000
    except Exception as e:
        print(f"ZMQ REQ fetch error: {e}")
    return None, (time.perf_counter() - start) * 1000


async def fetch_frame_zmq_dealer(nvr: str, channel: int, use_snapshot: bool = False) -> tuple[np.ndarray | None, float, dict | None]:
    """Fetch frame via ZMQ DEALER (async), return (frame, fetch_time_ms, timing_meta)"""
    start = time.perf_counter()
    try:
        sock = get_zmq_dealer_socket()
        source = "snapshot" if use_snapshot else "rtsp"
        request_id = uuid.uuid4().hex
        loop = asyncio.get_event_loop()

        # Register pending response with asyncio.Event
        event = asyncio.Event()
        with zmq_dealer_lock:
            zmq_pending_responses[request_id] = (event, None, None, loop)  # (event, jpeg_bytes, timing_meta, loop)

        # Send: JSON with 'id' field included (single frame)
        sock.send_json({"id": request_id, "nvr": nvr, "channel": channel, "source": source})

        # Async wait for response (with timeout)
        try:
            await asyncio.wait_for(event.wait(), timeout=30.0)
            with zmq_dealer_lock:
                _, jpeg_bytes, timing_meta, _ = zmq_pending_responses.pop(request_id, (None, None, None, None))

            if jpeg_bytes and len(jpeg_bytes) > 0:
                arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                return frame, (time.perf_counter() - start) * 1000, timing_meta
        except asyncio.TimeoutError:
            # Timeout - clean up
            with zmq_dealer_lock:
                zmq_pending_responses.pop(request_id, None)
    except Exception as e:
        print(f"ZMQ DEALER fetch error: {e}")
    return None, (time.perf_counter() - start) * 1000, None


TARGET_DETECT_WIDTH = 7680  # Effectively disabled — preserve all pixels for detection
LOW_RES_THRESHOLD = 1920    # Warn when source is below 1080p width


def run_detection(frame: np.ndarray) -> tuple[list, float, dict]:
    """Run all detectors, return (detections, detect_time_ms, detect_info)"""
    start = time.perf_counter()

    h, w = frame.shape[:2]
    detect_info = {"capture_w": w, "capture_h": h}

    # Right-size: scale down to TARGET_DETECT_WIDTH if larger, otherwise use as-is
    if w > TARGET_DETECT_WIDTH:
        scale = TARGET_DETECT_WIDTH / w
        small = cv2.resize(frame, (int(w * scale), int(h * scale)))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        detect_info["detect_w"] = int(w * scale)
        detect_info["detect_h"] = int(h * scale)
    else:
        scale = 1.0
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if w < LOW_RES_THRESHOLD:
            detect_info["low_res"] = True

    all_detections = []
    found_keys = set()  # (asset_name, tag_id) to deduplicate

    # Primary: pupil_apriltags
    for asset_name, config in detectors.items():
        dets = config['detector'].detect(gray, estimate_tag_pose=False)
        for d in dets:
            d.asset_name = asset_name
            d.color = config['color']
            # Scale back to full resolution
            d.center = d.center / scale
            d.corners = d.corners / scale
            all_detections.append(d)
            found_keys.add((asset_name, d.tag_id))

    # Fallback: OpenCV ArUco at full resolution (better perspective tolerance)
    gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    for asset_name, config in aruco_detectors.items():
        corners_list, ids, _ = config['detector'].detectMarkers(gray_full)
        if ids is not None:
            for i, tag_id in enumerate(ids):
                tid = int(tag_id[0])
                if (asset_name, tid) not in found_keys:
                    corners = corners_list[i][0]  # already full resolution
                    det = ArUcoDetection(tid, corners, asset_name, config['color'])
                    all_detections.append(det)
                    found_keys.add((asset_name, tid))

    return all_detections, (time.perf_counter() - start) * 1000, detect_info


def compute_tag_size(corners: np.ndarray) -> float:
    """Mean edge length in pixels from 4x2 corners array"""
    edges = [np.linalg.norm(corners[(i + 1) % 4] - corners[i]) for i in range(4)]
    return float(np.mean(edges))


def compute_tag_orientation(corners: np.ndarray) -> float:
    """Rotation angle in degrees from top edge (corners[0] -> corners[1])"""
    dx = corners[1][0] - corners[0][0]
    dy = corners[1][1] - corners[0][1]
    return float(math.degrees(math.atan2(dy, dx)))


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
    transport: str = Query("http", pattern="^(http|zmq|dealer)$"),
    snapshot: bool = Query(False)
):
    """Fetch frame, detect tags, return annotated JPEG"""

    # Fetch frame
    if transport == "dealer":
        frame, fetch_ms, _ = await fetch_frame_zmq_dealer(nvr, channel, use_snapshot=snapshot)
    elif transport == "zmq":
        frame, fetch_ms = fetch_frame_zmq(nvr, channel, use_snapshot=snapshot)
    else:
        frame, fetch_ms = fetch_frame_http(nvr, channel, use_snapshot=snapshot)

    # For stats tracking
    source = f"{transport}{'+snap' if snapshot else ''}"

    if frame is None:
        return Response(content=b"", media_type="image/jpeg", status_code=404)

    # Detect
    detections, detect_ms, detect_info = run_detection(frame)

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
    """List active cameras from camera-service.
    Combines /api/cameras/lodge (configured mounts) with /api/nvrs (brand info)
    to determine snapshot capability per NVR.
    """
    cameras = []
    camera_service_online = False
    try:
        # Fetch cameras and NVR info in parallel (well, sequentially here but both needed)
        cam_resp = requests.get(f"{CAMERA_SERVICE_URL}/api/cameras/lodge", timeout=5)
        nvr_resp = requests.get(f"{CAMERA_SERVICE_URL}/api/nvrs", timeout=5)

        if cam_resp.status_code == 200:
            camera_service_online = True

            # Build NVR brand lookup for snapshot capability
            nvr_brands = {}
            if nvr_resp.status_code == 200:
                for nvr in nvr_resp.json().get("nvrs", []):
                    brand = (nvr.get("brand") or "").upper()
                    nvr_brands[nvr["id"]] = "UNIVIEW" in brand

            data = cam_resp.json()
            for cam in data.get("cameras", []):
                nvr_id = cam.get("nvr_id", "nvr1")
                cameras.append({
                    "nvr": nvr_id,
                    "channel": cam.get("channel"),
                    "name": cam.get("name", f"ch{cam.get('channel')}"),
                    "resolution": cam.get("resolution"),
                    "snapshot": nvr_brands.get(nvr_id, False),
                })
    except Exception as e:
        print(f"Camera service unreachable: {e}")

    return {"cameras": cameras, "count": len(cameras), "camera_service_online": camera_service_online}


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


# =============================================================================
# Pass-based API (parallel detection for all cameras)
# =============================================================================

async def _process_camera_for_pass(pass_id: str, cam: dict):
    """Process a single camera for a pass - fetch, detect, store result"""
    nvr = cam["nvr"]
    channel = cam["channel"]
    key = f"{nvr}/{channel}"

    try:
        # Fetch frame via DEALER (parallel-friendly)
        use_snapshot = cam.get("snapshot", False)
        frame, fetch_ms, timing_meta = await fetch_frame_zmq_dealer(nvr, channel, use_snapshot=use_snapshot)

        if frame is None:
            with passes_lock:
                if pass_id in passes:
                    passes[pass_id]["results"][key] = {
                        "nvr": nvr, "channel": channel, "status": "failed",
                        "error": "no_frame", "fetch_ms": int(fetch_ms)
                    }
            return

        # Calculate overhead if camera-service provided timing
        queue_ms = timing_meta.get("queue_ms") if timing_meta else None
        capture_ms = timing_meta.get("capture_ms") if timing_meta else None
        overhead_ms = None
        if queue_ms is not None and capture_ms is not None:
            overhead_ms = max(0, int(fetch_ms) - queue_ms - capture_ms)

        # Run detection
        detections, detect_ms, detect_info = run_detection(frame)

        # Draw overlays
        annotated = draw_detections(frame.copy(), detections)

        # Add timing overlay
        h, w = annotated.shape[:2]
        source_label = "DEALER" + ("+SNAP" if use_snapshot else "")
        overlay_h = 140 if detect_info.get("low_res") else 110
        cv2.rectangle(annotated, (0, 0), (500, overlay_h), (40, 40, 40), -1)
        cv2.putText(annotated, f"{w}x{h}", (10, 28),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        cv2.putText(annotated, f"Fetch: {fetch_ms:.0f}ms ({source_label})", (10, 58),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.putText(annotated, f"Detect: {detect_ms:.0f}ms | Tags: {len(detections)}", (10, 88),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        if detect_info.get("low_res"):
            cv2.putText(annotated, f"LOW RES — {w}x{h} (no downscale)", (10, 118),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        elif "detect_w" in detect_info:
            cv2.putText(annotated, f"Detect: {detect_info['detect_w']}x{detect_info['detect_h']}", (10, 118),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)

        # Resize for web (max 800px wide)
        if w > 800:
            scale = 800 / w
            annotated = cv2.resize(annotated, (800, int(h * scale)))

        # Encode JPEG
        _, jpeg = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])

        # Build tag details list
        tag_details = []
        for det in detections:
            margin = det.decision_margin
            tag_details.append({
                "id": det.tag_id,
                "family": det.asset_name,
                "margin": round(margin, 1) if margin >= 0 else "aruco"
            })

        # Store result and image
        with passes_lock:
            if pass_id in passes:
                result = {
                    "nvr": nvr, "channel": channel, "status": "done",
                    "tags": len(detections), "tag_details": tag_details,
                    "resolution": f"{w}x{h}",
                    "fetch_ms": int(fetch_ms), "detect_ms": int(detect_ms)
                }
                if detect_info.get("low_res"):
                    result["low_res"] = True
                if "detect_w" in detect_info:
                    result["detect_res"] = f"{detect_info['detect_w']}x{detect_info['detect_h']}"
                # Include timing breakdown if available
                if queue_ms is not None:
                    result["queue_ms"] = queue_ms
                if capture_ms is not None:
                    result["capture_ms"] = capture_ms
                if overhead_ms is not None:
                    result["overhead_ms"] = overhead_ms
                passes[pass_id]["results"][key] = result
                passes[pass_id]["images"][key] = jpeg.tobytes()

    except Exception as e:
        with passes_lock:
            if pass_id in passes:
                passes[pass_id]["results"][key] = {
                    "nvr": nvr, "channel": channel, "status": "failed",
                    "error": str(e)
                }


async def _run_pass(pass_id: str):
    """Run detection on all cameras in parallel"""
    with passes_lock:
        if pass_id not in passes:
            return
        cameras = passes[pass_id]["cameras"]

    # Fire all cameras in parallel
    tasks = [_process_camera_for_pass(pass_id, cam) for cam in cameras]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Mark pass as done
    with passes_lock:
        if pass_id in passes:
            passes[pass_id]["done"] = True
            passes[pass_id]["endTime"] = time.time()


class NvrConfig(BaseModel):
    enabled: bool = True
    snapshots: bool = True

class PassStartRequest(BaseModel):
    nvrs: dict[str, NvrConfig] | None = None  # e.g. {"nvr1": {"enabled": true, "snapshots": false}}

@app.post("/pass/start")
async def start_pass(request: PassStartRequest = None):
    """Start a detection pass - fetches all cameras in parallel

    Body (optional):
        { "nvrs": { "nvr1": { "enabled": true, "snapshots": false }, "nvr2": { "enabled": true, "snapshots": true } } }
    """
    # Get camera list
    cameras_resp = await list_cameras()
    all_cameras = cameras_resp["cameras"]

    # Filter and configure cameras based on NVR settings
    if request and request.nvrs:
        cameras = []
        for cam in all_cameras:
            nvr_id = cam["nvr"]
            if nvr_id in request.nvrs:
                nvr_cfg = request.nvrs[nvr_id]
                if nvr_cfg.enabled:
                    # Override snapshot setting per NVR
                    cam_copy = {**cam, "snapshot": cam.get("snapshot", False) and nvr_cfg.snapshots}
                    cameras.append(cam_copy)
            # If NVR not in config, exclude it
    else:
        cameras = all_cameras

    # Generate pass ID
    pass_id = uuid.uuid4().hex[:12]

    # Initialize pass storage
    with passes_lock:
        # Cleanup old passes if we have too many
        if len(passes) >= MAX_PASSES:
            oldest = min(passes.keys(), key=lambda k: passes[k].get("startTime", 0))
            del passes[oldest]

        passes[pass_id] = {
            "cameras": cameras,
            "results": {f"{c['nvr']}/{c['channel']}": {"nvr": c["nvr"], "channel": c["channel"], "status": "pending"} for c in cameras},
            "images": {},
            "startTime": time.time(),
            "done": False
        }

    # Kick off parallel detection (don't await - let it run in background)
    asyncio.create_task(_run_pass(pass_id))

    return {"passId": pass_id, "total": len(cameras)}


@app.get("/pass/{pass_id}/results")
async def get_pass_results(pass_id: str):
    """Get current results for a pass"""
    with passes_lock:
        if pass_id not in passes:
            return {"error": "pass not found"}, 404

        p = passes[pass_id]
        results = list(p["results"].values())
        pending = sum(1 for r in results if r["status"] == "pending")
        done_count = sum(1 for r in results if r["status"] == "done")
        failed = sum(1 for r in results if r["status"] == "failed")

        return {
            "passId": pass_id,
            "done": p["done"],
            "pending": pending,
            "completed": done_count,
            "failed": failed,
            "total": len(results),
            "results": results
        }


@app.get("/pass/{pass_id}/image/{nvr}/{channel}")
async def get_pass_image(pass_id: str, nvr: str, channel: int):
    """Get the image for a specific camera from a pass"""
    key = f"{nvr}/{channel}"

    with passes_lock:
        if pass_id not in passes:
            return Response(content=b"", status_code=404)

        jpeg_bytes = passes[pass_id]["images"].get(key)
        if not jpeg_bytes:
            return Response(content=b"", status_code=404)

        return Response(content=jpeg_bytes, media_type="image/jpeg")


# =============================================================================
# Scan API (continuous detection, camera-service paced)
# =============================================================================

SCAN_PULL_PORT = 5557
SCAN_LOG_DIR = Path(__file__).parent / "logs"
MAX_SCANS = 10

scans = {}  # scanId -> {cameras, timeout, startTime, running, log_lines, counters, log_path}
scans_lock = threading.Lock()

# ZMQ PULL socket for receiving scan frames
zmq_pull_socket = None
zmq_pull_thread = None


def _ensure_pull_socket():
    """Start the ZMQ PULL socket and receiver thread if not running"""
    global zmq_pull_socket, zmq_pull_thread, zmq_context
    if zmq_pull_socket is not None:
        return
    if zmq_context is None:
        zmq_context = zmq.Context()
    zmq_pull_socket = zmq_context.socket(zmq.PULL)
    zmq_pull_socket.bind(f"tcp://*:{SCAN_PULL_PORT}")
    zmq_pull_thread = threading.Thread(target=_pull_receiver, daemon=True)
    zmq_pull_thread.start()
    print(f"PULL socket bound on port {SCAN_PULL_PORT}")


def _pull_receiver():
    """Background thread: receive frames from camera-service PUSH, run detection, log results"""
    global zmq_pull_socket
    while True:
        try:
            frames = zmq_pull_socket.recv_multipart()
            if len(frames) < 4:
                continue

            nvr = frames[0].decode('utf-8')
            channel = int(frames[1].decode('utf-8'))
            jpeg_bytes = frames[2]
            ts = float(frames[3].decode('utf-8'))

            # Decode frame
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                continue

            # Run detection
            detections, detect_ms, detect_info = run_detection(frame)

            key = f"{nvr}/{channel}"

            # Find which active scan owns this camera
            with scans_lock:
                active_scan_id = None
                for sid, scan in scans.items():
                    if scan["running"] and key in scan["camera_keys"]:
                        active_scan_id = sid
                        break

                if active_scan_id is None:
                    continue

                scan = scans[active_scan_id]
                scan["counters"][key]["frames"] += 1

                # Build tags array
                tags = []
                for det in detections:
                    size_px = compute_tag_size(det.corners)
                    orient_deg = compute_tag_orientation(det.corners)
                    tags.append({
                        "tag": det.tag_id,
                        "family": det.asset_name,
                        "margin": round(det.decision_margin, 1) if det.decision_margin >= 0 else "aruco",
                        "size_px": round(size_px, 1),
                        "orient_deg": round(orient_deg, 1),
                    })
                    scan["counters"][key]["tags"].add((det.asset_name, det.tag_id))

                # Log frame event with embedded tags (tags_found last for readability)
                frame_entry = {
                    "ts": round(ts, 3),
                    "nvr": nvr,
                    "ch": channel,
                    "type": "frame",
                    "detect_ms": round(detect_ms, 0),
                    "res": f"{detect_info['capture_w']}x{detect_info['capture_h']}",
                }
                if detect_info.get("low_res"):
                    frame_entry["low_res"] = True
                frame_entry["tags_found"] = tags
                scan["log_lines"].append(frame_entry)
                try:
                    with open(scan["log_path"], "a") as f:
                        f.write(json.dumps(frame_entry) + "\n")
                except Exception:
                    pass

        except Exception as e:
            print(f"PULL receiver error: {e}")
            time.sleep(0.1)


class ScanCamera(BaseModel):
    nvr: str
    channel: int

class ScanStartRequest(BaseModel):
    cameras: list[ScanCamera]
    timeout: int = 30


@app.post("/scan/start")
async def start_scan(request: ScanStartRequest):
    """Start a continuous detection scan.

    Camera-service controls frame pacing. Detection service processes
    frames as they arrive via ZMQ PULL.
    """
    _ensure_pull_socket()

    # Create log directory
    SCAN_LOG_DIR.mkdir(exist_ok=True)

    from datetime import datetime
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:4]
    scan_id = f"{ts_str}_{short_id}"
    camera_keys = set()
    camera_list = []
    for cam in request.cameras:
        key = f"{cam.nvr}/{cam.channel}"
        camera_keys.add(key)
        camera_list.append({"nvr": cam.nvr, "channel": cam.channel})

    log_path = SCAN_LOG_DIR / f"scan_{scan_id}.jsonl"

    with scans_lock:
        # Cleanup old scans
        if len(scans) >= MAX_SCANS:
            oldest = min(scans.keys(), key=lambda k: scans[k].get("startTime", 0))
            del scans[oldest]

        scans[scan_id] = {
            "cameras": camera_list,
            "camera_keys": camera_keys,
            "timeout": request.timeout,
            "startTime": time.time(),
            "running": True,
            "log_lines": [],
            "counters": {k: {"frames": 0, "tags": set()} for k in camera_keys},
            "log_path": str(log_path),
        }

    # Tell camera-service to start scanning
    push_to = f"tcp://127.0.0.1:{SCAN_PULL_PORT}"
    try:
        resp = requests.post(
            f"{CAMERA_SERVICE_URL}/api/tag-scan/start",
            json={
                "cameras": camera_list,
                "timeout": request.timeout,
                "push_to": push_to,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            with scans_lock:
                scans[scan_id]["running"] = False
            return {"error": f"Camera service returned {resp.status_code}", "detail": resp.text}
        cs_data = resp.json()
    except Exception as e:
        with scans_lock:
            scans[scan_id]["running"] = False
        return {"error": f"Camera service unreachable: {e}"}

    # Schedule auto-stop after timeout
    async def _auto_stop():
        await asyncio.sleep(request.timeout + 2)  # grace period
        with scans_lock:
            if scan_id in scans:
                scans[scan_id]["running"] = False
    asyncio.create_task(_auto_stop())

    return {
        "scanId": scan_id,
        "timeout": request.timeout,
        "cameras": camera_list,
        "log_path": str(log_path),
        "camera_service": cs_data,
    }


@app.get("/scan/{scan_id}/status")
async def get_scan_status(scan_id: str):
    """Get current scan state"""
    with scans_lock:
        if scan_id not in scans:
            return Response(content=json.dumps({"error": "scan not found"}),
                          media_type="application/json", status_code=404)

        scan = scans[scan_id]
        elapsed = time.time() - scan["startTime"]
        cameras = {}
        all_tags = set()
        for key, c in scan["counters"].items():
            cameras[key] = {"frames": c["frames"], "unique_tags": len(c["tags"])}
            all_tags.update(c["tags"])

        return {
            "scanId": scan_id,
            "running": scan["running"],
            "elapsed_s": round(elapsed, 1),
            "timeout": scan["timeout"],
            "cameras": cameras,
            "total_observations": len(scan["log_lines"]),
            "unique_tags": len(all_tags),
        }


@app.get("/scan/{scan_id}/log")
async def get_scan_log(scan_id: str, after: int = 0):
    """Get log lines after a given offset (for polling)"""
    with scans_lock:
        if scan_id not in scans:
            return Response(content=json.dumps({"error": "scan not found"}),
                          media_type="application/json", status_code=404)

        scan = scans[scan_id]
        lines = scan["log_lines"][after:]
        return {
            "lines": lines,
            "nextOffset": after + len(lines),
            "running": scan["running"],
        }


@app.get("/scan", response_class=HTMLResponse)
async def scan_page():
    """Serve the scan log HTML page"""
    html_path = Path(__file__).parent / "scan-log.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>scan-log.html not found</h1>"


if __name__ == "__main__":
    import uvicorn
    print("Starting detection service on port 8002...")
    uvicorn.run(app, host="0.0.0.0", port=8002)
