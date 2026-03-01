#!/usr/bin/env python3
"""
FastTag — High-Speed AprilTag Detection Server

Direct RTSP to camera IPs, one process per camera, no middleman.
Port: 8003

Author: modeltcamerascat gen-26
"""

import os
import sys
import time
import json
import sqlite3
import logging
import threading
import multiprocessing
from pathlib import Path
from typing import Optional, List
from urllib.parse import quote
from collections import deque

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn

from worker import detection_worker

# ============================================================================
# CREDENTIALS (same pattern as camera-service)
# ============================================================================

ENV_PATH = Path(__file__).parent.parent / ".env"
if ENV_PATH.exists():
    for _line in ENV_PATH.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

_CRED_GROUPS_PATH = Path(__file__).parent.parent / "warehouses" / "lodge" / "cam-cred-groups.json"
_raw = json.loads(_CRED_GROUPS_PATH.read_text())
CAM_CRED_GROUPS = {k: v for k, v in _raw.items() if not k.startswith("_")}

DB_PATH = Path(__file__).parent.parent / "warehouses" / "lodge" / "lodge.db"

# ============================================================================
# LOGGING
# ============================================================================

LOG_FILE = Path(__file__).parent / "fasttag.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# CAMERA LOOKUP
# ============================================================================

def get_camera_credentials(model: str, ip: str) -> tuple:
    """Resolve camera credentials. IP-specific override > model group > default."""
    ip_suffix = ip.split('.')[-1]
    ip_user = os.environ.get(f"CAM_{ip_suffix}_USER")
    ip_pass = os.environ.get(f"CAM_{ip_suffix}_PASS")
    if ip_user is not None:
        return (ip_user, ip_pass or "")
    prefix = CAM_CRED_GROUPS.get(model)
    if prefix:
        return (os.environ.get(f"{prefix}_USER", "admin"),
                os.environ.get(f"{prefix}_PASS", ""))
    return ("admin", "")


def get_camera_by_ip(camera_ip: str) -> Optional[dict]:
    """Look up camera by IP from lodge.db."""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH), timeout=2)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT mac, ip, model, rtsp_path FROM cameras WHERE ip = ?", (camera_ip,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_cameras_with_direct() -> list:
    """Get all cameras that have direct IP + RTSP path."""
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH), timeout=2)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT mac, ip, model, rtsp_path FROM cameras WHERE ip IS NOT NULL AND rtsp_path IS NOT NULL")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def build_rtsp_url(cam: dict) -> str:
    """Build RTSP URL direct to camera IP."""
    ip = cam['ip']
    path = cam['rtsp_path']
    user, passwd = get_camera_credentials(cam['model'], ip)
    passwd_enc = quote(passwd, safe='')
    return f"rtsp://{user}:{passwd_enc}@{ip}:554{path}"

# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(
    title="FastTag — AprilTag Detection",
    description="High-speed continuous AprilTag detection via direct RTSP",
    version="1.0.0",
)

# ============================================================================
# STATE
# ============================================================================

# Per-camera worker tracking
# camera_ip -> {process, stop_event, started_at, config, last_heartbeat}
workers: dict = {}
workers_lock = threading.Lock()

# Detection ring buffer
DETECTION_BUFFER_SIZE = 10000
detection_buffer: deque = deque(maxlen=DETECTION_BUFFER_SIZE)
detection_lock = threading.Lock()

# Per-camera stats (updated from heartbeats)
camera_stats: dict = {}  # camera_ip -> latest heartbeat dict
stats_lock = threading.Lock()

# Shared queue for all workers
result_queue: Optional[multiprocessing.Queue] = None

# Default detection config
DEFAULT_CONFIG = {
    "families": "tagCustom48h12",
    "quad_decimate": 1.0,
}

# ============================================================================
# RESULT COLLECTOR THREAD
# ============================================================================

collector_running = False


def result_collector():
    """Drain result_queue into detection_buffer and camera_stats."""
    global collector_running
    collector_running = True
    logger.info("Result collector started")

    while collector_running:
        try:
            msg = result_queue.get(timeout=0.5)
        except Exception:
            continue

        if msg.get("type") == "heartbeat":
            with stats_lock:
                camera_stats[msg["camera_ip"]] = msg
        elif msg.get("type") == "detection":
            with detection_lock:
                detection_buffer.append(msg)

    logger.info("Result collector stopped")

# ============================================================================
# MODELS
# ============================================================================

class StartRequest(BaseModel):
    cameras: List[str] = []  # list of camera IPs, or empty for "all"
    config: Optional[dict] = None  # override default config

class StopRequest(BaseModel):
    cameras: List[str] = []  # list of camera IPs, or empty for "all"

# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/")
def root():
    with workers_lock:
        n = len(workers)
    return {
        "service": "FastTag",
        "version": "1.0.0",
        "port": 8003,
        "active_cameras": n,
    }


@app.post("/start")
def start_cameras(request: StartRequest):
    """Start detection on cameras. Pass IPs or empty list for all."""
    global result_queue

    config = {**DEFAULT_CONFIG, **(request.config or {})}

    # Resolve camera list
    if request.cameras:
        cam_ips = request.cameras
    else:
        all_cams = get_all_cameras_with_direct()
        cam_ips = [c['ip'] for c in all_cams]

    if not cam_ips:
        raise HTTPException(status_code=400, detail="No cameras found")

    # Ensure result queue + collector
    if result_queue is None:
        result_queue = multiprocessing.Queue()
        t = threading.Thread(target=result_collector, daemon=True)
        t.start()

    started = []
    already_running = []
    errors = []

    for ip in cam_ips:
        with workers_lock:
            if ip in workers:
                already_running.append(ip)
                continue

        cam = get_camera_by_ip(ip)
        if not cam:
            errors.append({"ip": ip, "error": "not found in lodge.db"})
            continue

        rtsp_url = build_rtsp_url(cam)
        stop_event = multiprocessing.Event()

        proc = multiprocessing.Process(
            target=detection_worker,
            args=(ip, rtsp_url, result_queue, stop_event, config),
            daemon=True,
        )
        proc.start()

        with workers_lock:
            workers[ip] = {
                "process": proc,
                "stop_event": stop_event,
                "started_at": time.time(),
                "config": config,
                "model": cam.get("model"),
            }

        started.append(ip)
        logger.info(f"Started worker for {ip} (pid {proc.pid}, model {cam.get('model')})")

    return {
        "started": started,
        "already_running": already_running,
        "errors": errors,
        "total_active": len(workers),
    }


@app.post("/stop")
def stop_cameras(request: StopRequest):
    """Stop detection on cameras. Pass IPs or empty list for all."""
    if request.cameras:
        cam_ips = request.cameras
    else:
        with workers_lock:
            cam_ips = list(workers.keys())

    stopped = []
    not_running = []

    for ip in cam_ips:
        with workers_lock:
            w = workers.pop(ip, None)

        if not w:
            not_running.append(ip)
            continue

        w["stop_event"].set()
        w["process"].join(timeout=5)
        if w["process"].is_alive():
            w["process"].kill()
            logger.warning(f"Force-killed worker for {ip}")

        stopped.append(ip)
        logger.info(f"Stopped worker for {ip}")

        # Clear stats
        with stats_lock:
            camera_stats.pop(ip, None)

    return {
        "stopped": stopped,
        "not_running": not_running,
        "total_active": len(workers),
    }


@app.get("/status")
def get_status():
    """Per-camera stats: fps, detection count, uptime, status."""
    now = time.time()
    cameras = {}

    with workers_lock:
        worker_ips = dict(workers)

    for ip, w in worker_ips.items():
        with stats_lock:
            hb = camera_stats.get(ip)

        proc = w["process"]
        cameras[ip] = {
            "pid": proc.pid,
            "alive": proc.is_alive(),
            "uptime_sec": round(now - w["started_at"], 1),
            "model": w.get("model"),
            "fps": hb["fps"] if hb else 0,
            "frame_seq": hb["frame_seq"] if hb else 0,
            "detect_count": hb["detect_count"] if hb else 0,
            "status": hb["status"] if hb else "starting",
            "error": hb.get("error") if hb else None,
            "last_heartbeat_age": round(now - hb["timestamp"], 1) if hb else None,
        }

    with detection_lock:
        buf_size = len(detection_buffer)

    return {
        "active_cameras": len(cameras),
        "cameras": cameras,
        "detection_buffer_size": buf_size,
        "detection_buffer_capacity": DETECTION_BUFFER_SIZE,
    }


@app.get("/detections")
def get_detections(
    camera: Optional[str] = Query(None, description="Filter by camera IP"),
    tag_id: Optional[int] = Query(None, description="Filter by tag ID"),
    since: Optional[float] = Query(None, description="Unix timestamp — only detections after this"),
    limit: int = Query(100, description="Max results to return"),
):
    """Recent detections with optional filters."""
    with detection_lock:
        results = list(detection_buffer)

    if camera:
        results = [d for d in results if d["camera_ip"] == camera]
    if tag_id is not None:
        results = [d for d in results if d["tag_id"] == tag_id]
    if since:
        results = [d for d in results if d["timestamp"] > since]

    # Return most recent first, capped at limit
    results = results[-limit:]
    results.reverse()

    return {
        "count": len(results),
        "detections": results,
    }


@app.get("/detections/stream")
def detection_stream(
    camera: Optional[str] = Query(None, description="Filter by camera IP"),
    tag_id: Optional[int] = Query(None, description="Filter by tag ID"),
):
    """SSE stream of live detections."""
    def generate():
        last_idx = len(detection_buffer)
        while True:
            time.sleep(0.1)
            with detection_lock:
                current = list(detection_buffer)
            new_items = current[last_idx:]
            last_idx = len(current)

            for det in new_items:
                if camera and det["camera_ip"] != camera:
                    continue
                if tag_id is not None and det["tag_id"] != tag_id:
                    continue
                yield f"data: {json.dumps(det)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/cameras")
def list_available_cameras():
    """List all cameras that support direct RTSP (candidates for detection)."""
    cams = get_all_cameras_with_direct()
    with workers_lock:
        active_ips = set(workers.keys())

    return {
        "count": len(cams),
        "cameras": [
            {
                "ip": c["ip"],
                "model": c["model"],
                "active": c["ip"] in active_ips,
            }
            for c in cams
        ],
    }


@app.put("/config")
def update_default_config(config: dict):
    """Update default detection config for new workers."""
    global DEFAULT_CONFIG
    for k in ("families", "quad_decimate"):
        if k in config:
            DEFAULT_CONFIG[k] = config[k]
    return {"config": DEFAULT_CONFIG}


@app.post("/admin/restart")
def restart_service():
    """Exit for restart by external loop."""
    def delayed_exit():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=delayed_exit, daemon=True).start()
    return {"message": "Restarting..."}


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  FastTag — AprilTag Detection Server")
    print("=" * 60)
    print("  HTTP: http://localhost:8003")
    print("  Docs: http://localhost:8003/docs")
    print("=" * 60)
    print()

    uvicorn.run(app, host="0.0.0.0", port=8003, log_level="info")
