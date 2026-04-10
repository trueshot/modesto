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
CAMERAS_DIR = Path(__file__).parent / "cameras"
CAMERAS_DIR.mkdir(exist_ok=True)

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
# PER-CAMERA CONFIG (fasttag/cameras/{ip}/config.json)
# ============================================================================

DEFAULT_CAMERA_CONFIG = {
    "enabled": True,
    "target_fps": 5.0,  # target detection rate (frames per second)
    "flip": None,       # None = normal, 1 = H-flip. Adaptive: auto-switches on detection failure.
}


def _camera_config_path(camera_ip: str) -> Path:
    """Path to per-camera config file."""
    return CAMERAS_DIR / camera_ip / "config.json"


def load_camera_config(camera_ip: str) -> dict:
    """Load per-camera config. Returns defaults if no file exists."""
    cfg_path = _camera_config_path(camera_ip)
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text())
            # Merge with defaults so new fields get picked up
            return {**DEFAULT_CAMERA_CONFIG, **data}
        except Exception as e:
            logger.warning(f"Bad config for {camera_ip}: {e}")
    return dict(DEFAULT_CAMERA_CONFIG)


def save_camera_config(camera_ip: str, config: dict):
    """Save per-camera config to disk."""
    cfg_path = _camera_config_path(camera_ip)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(config, indent=2))


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


def get_all_active_cameras() -> list:
    """
    Get all cameras from lodge.db that can be reached via RTSP.

    Returns cameras from two sources (deduplicated by IP):
    1. Cameras on active channels with direct IP + rtsp_path (access='direct')
    2. Cameras on active channels with IP but no rtsp_path — uses NVR RTSP URL (access='nvr')
    3. Cameras in cameras table with IP + rtsp_path not on any active channel (access='direct')
    """
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH), timeout=2)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cameras_by_ip = {}

    # 1. All cameras on active channels
    cur.execute("""
        SELECT DISTINCT cam.mac, cam.ip, cam.model, cam.rtsp_path,
               ch.nvr_id, ch.channel_number
        FROM channels ch
        JOIN cameras cam ON ch.camera_id = cam.mac
        WHERE ch.status = 'active' AND cam.ip IS NOT NULL
        ORDER BY cam.ip
    """)
    for row in cur.fetchall():
        ip = row['ip']
        if ip in cameras_by_ip:
            continue  # already seen (same camera on multiple NVRs)
        entry = {
            'mac': row['mac'], 'ip': ip, 'model': row['model'],
            'rtsp_path': row['rtsp_path'],
            'nvr_id': row['nvr_id'], 'channel': row['channel_number'],
        }
        if row['rtsp_path']:
            entry['access'] = 'direct'
        else:
            entry['access'] = 'nvr'
        cameras_by_ip[ip] = entry

    # 2. Cameras with direct access not on any active channel
    cur.execute("""
        SELECT mac, ip, model, rtsp_path
        FROM cameras
        WHERE ip IS NOT NULL AND rtsp_path IS NOT NULL
          AND mac NOT IN (
            SELECT camera_id FROM channels WHERE status = 'active' AND camera_id IS NOT NULL
          )
    """)
    for row in cur.fetchall():
        ip = row['ip']
        if ip not in cameras_by_ip:
            cameras_by_ip[ip] = {
                'mac': row['mac'], 'ip': ip, 'model': row['model'],
                'rtsp_path': row['rtsp_path'],
                'nvr_id': None, 'channel': None,
                'access': 'direct',
            }

    # 3. Load NVR info for building NVR-path URLs
    nvrs = {}
    cur.execute("SELECT id, ip, username, password, path_format FROM nvrs")
    for row in cur.fetchall():
        nvrs[row['id']] = dict(row)

    conn.close()

    # Build result list with RTSP URL info
    result = []
    for cam in cameras_by_ip.values():
        cam['_nvrs'] = nvrs  # attach for URL building
        result.append(cam)

    return result


def build_rtsp_url(cam: dict) -> str:
    """Build RTSP URL for a camera — direct if available, NVR fallback otherwise."""
    ip = cam['ip']

    if cam.get('rtsp_path'):
        # Direct to camera IP
        path = cam['rtsp_path']
        user, passwd = get_camera_credentials(cam['model'], ip)
        passwd_enc = quote(passwd, safe='')
        return f"rtsp://{user}:{passwd_enc}@{ip}:554{path}"

    # NVR fallback
    nvr_id = cam.get('nvr_id')
    channel = cam.get('channel')
    nvrs = cam.get('_nvrs', {})
    nvr = nvrs.get(nvr_id) if nvr_id else None
    if nvr and channel:
        nvr_ip = nvr['ip']
        username = nvr['username'] or 'admin'
        password = nvr['password'] or ''
        path_format = nvr['path_format']
        if '{channel:02d}' in path_format:
            path = path_format.replace('{channel:02d}', f'{channel:02d}')
        else:
            path = path_format.replace('{channel}', str(channel))
        password_enc = quote(password, safe='')
        return f"rtsp://{username}:{password_enc}@{nvr_ip}:554/{path}"

    return None  # no way to reach this camera

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
    "quad_decimate": 2.0,
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

    # Build lookup of all reachable cameras (includes NVR fallback info)
    all_cams = get_all_active_cameras()
    cam_lookup = {c['ip']: c for c in all_cams}

    # Resolve camera list
    if request.cameras:
        cam_ips = request.cameras
    else:
        # Start all enabled cameras (per-camera config)
        cam_ips = [c['ip'] for c in all_cams if load_camera_config(c['ip']).get('enabled', True)]

    if not cam_ips:
        raise HTTPException(status_code=400, detail="No cameras found")

    # Ensure result queue + collector
    if result_queue is None:
        result_queue = multiprocessing.Queue()
        t = threading.Thread(target=result_collector, daemon=True)
        t.start()

    started = []
    already_running = []
    skipped = []
    errors = []

    for ip in cam_ips:
        with workers_lock:
            if ip in workers:
                already_running.append(ip)
                continue

        cam = cam_lookup.get(ip)
        if not cam:
            errors.append({"ip": ip, "error": "not found in lodge.db"})
            continue

        # Check per-camera config
        cam_cfg = load_camera_config(ip)
        if not cam_cfg.get("enabled", True):
            skipped.append(ip)
            continue

        rtsp_url = build_rtsp_url(cam)
        if not rtsp_url:
            errors.append({"ip": ip, "error": "no RTSP path available (direct or NVR)"})
            continue

        # Merge detection config with per-camera settings
        worker_config = {
            **config,
            "target_fps": cam_cfg.get("target_fps", 5.0),
            "flip": cam_cfg.get("flip", None),
        }

        stop_event = multiprocessing.Event()

        proc = multiprocessing.Process(
            target=detection_worker,
            args=(ip, rtsp_url, result_queue, stop_event, worker_config),
            daemon=True,
        )
        proc.start()

        with workers_lock:
            workers[ip] = {
                "process": proc,
                "stop_event": stop_event,
                "started_at": time.time(),
                "config": worker_config,
                "model": cam.get("model"),
                "access": cam.get("access"),
            }

        started.append(ip)
        logger.info(f"Started worker for {ip} (pid {proc.pid}, model {cam.get('model')}, access {cam.get('access')}, fps {worker_config['target_fps']})")

    return {
        "started": started,
        "already_running": already_running,
        "skipped_disabled": skipped,
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
            "flip_mode": hb.get("flip_mode", "normal") if hb else "normal",
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
    """List all cameras from lodge.db with their config and detection status."""
    cams = get_all_active_cameras()
    with workers_lock:
        active_ips = set(workers.keys())

    return {
        "count": len(cams),
        "cameras": [
            {
                "ip": c["ip"],
                "model": c["model"],
                "access": c["access"],
                "detecting": c["ip"] in active_ips,
                "config": load_camera_config(c["ip"]),
            }
            for c in cams
        ],
    }


@app.get("/cameras/{camera_ip}/config")
def get_camera_config(camera_ip: str):
    """Get per-camera config. Returns defaults if no config file exists."""
    return {
        "ip": camera_ip,
        "config": load_camera_config(camera_ip),
        "has_file": _camera_config_path(camera_ip).exists(),
    }


class CameraConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    target_fps: Optional[float] = None
    flip: Optional[int] = None  # None=normal (auto-detect), 1=force H-flip


@app.put("/cameras/{camera_ip}/config")
def set_camera_config(camera_ip: str, update: CameraConfigUpdate):
    """
    Update per-camera config. Saves to fasttag/cameras/{ip}/config.json.
    Only provided fields are updated; others keep current values.
    Changes take effect on next /start (running workers keep their config).
    """
    current = load_camera_config(camera_ip)
    if update.enabled is not None:
        current["enabled"] = update.enabled
    if update.target_fps is not None:
        if update.target_fps <= 0 or update.target_fps > 30:
            raise HTTPException(status_code=400, detail="target_fps must be 0.1-30")
        current["target_fps"] = update.target_fps
    if update.flip is not None:
        if update.flip not in (0, 1):
            raise HTTPException(status_code=400, detail="flip must be 0 (normal) or 1 (H-flip)")
        current["flip"] = update.flip if update.flip == 1 else None
    save_camera_config(camera_ip, current)
    return {"ip": camera_ip, "config": current}


@app.get("/cameras/configs")
def list_all_configs():
    """List all cameras that have custom config files."""
    configs = {}
    if CAMERAS_DIR.exists():
        for ip_dir in sorted(CAMERAS_DIR.iterdir()):
            cfg_file = ip_dir / "config.json"
            if cfg_file.exists():
                try:
                    configs[ip_dir.name] = json.loads(cfg_file.read_text())
                except Exception:
                    configs[ip_dir.name] = {"error": "invalid JSON"}
    return {"count": len(configs), "configs": configs}


@app.get("/cameras/{camera_ip}/detections")
def get_camera_detections(
    camera_ip: str,
    tail: int = Query(50, description="Number of recent detections to return"),
):
    """Read recent detections from a camera's JSONL log."""
    jsonl_path = CAMERAS_DIR / camera_ip / "detections.jsonl"
    if not jsonl_path.exists():
        return {"ip": camera_ip, "count": 0, "detections": []}

    # Read last N lines efficiently
    lines = []
    try:
        with open(jsonl_path, "r") as f:
            # Read all lines, keep last `tail` — fine for typical JSONL sizes
            all_lines = f.readlines()
            for line in all_lines[-tail:]:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading log: {e}")

    return {"ip": camera_ip, "count": len(lines), "detections": lines}


@app.delete("/cameras/{camera_ip}/detections")
def clear_camera_detections(camera_ip: str):
    """Clear a camera's detection log."""
    jsonl_path = CAMERAS_DIR / camera_ip / "detections.jsonl"
    if jsonl_path.exists():
        jsonl_path.write_text("")
    return {"ip": camera_ip, "cleared": True}


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
