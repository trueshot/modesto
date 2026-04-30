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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List
from urllib.parse import quote
from collections import deque

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
import uvicorn

import cv2
import numpy as np
import requests

from worker import detection_worker
from http_worker import http_detection_worker

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
    cur.execute("SELECT mac, ip, model, rtsp_path, protocol, http_path FROM cameras WHERE ip = ?", (camera_ip,))
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
               cam.protocol, cam.http_path, ch.nvr_id, ch.channel_number
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
            'protocol': row['protocol'] or 'rtsp',
            'http_path': row['http_path'],
            'nvr_id': row['nvr_id'], 'channel': row['channel_number'],
        }
        if row['rtsp_path']:
            entry['access'] = 'direct'
        else:
            entry['access'] = 'nvr'
        cameras_by_ip[ip] = entry

    # 2. Cameras with direct access not on any active channel
    cur.execute("""
        SELECT mac, ip, model, rtsp_path, protocol, http_path
        FROM cameras
        WHERE ip IS NOT NULL AND (rtsp_path IS NOT NULL OR protocol IS NOT NULL)
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
                'protocol': row['protocol'] or 'rtsp',
                'http_path': row['http_path'],
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
    """Build RTSP URL for a camera — direct if available, NVR fallback otherwise.
    Returns None for non-RTSP cameras (e.g. HTTP/MJPEG)."""
    if cam.get('protocol', 'rtsp') != 'rtsp':
        return None  # HTTP cameras handled separately

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: launch background daemons. Names resolved at call time, so
    # forward references to functions defined later in the module are fine.
    threading.Thread(target=worker_reaper, daemon=True, name="worker-reaper").start()
    threading.Thread(target=camera_service_pinger, daemon=True, name="camera-svc-ping").start()
    yield
    # Shutdown: nothing — daemon threads die with the process.


app = FastAPI(
    title="FastTag — AprilTag Detection",
    description="High-speed continuous AprilTag detection via direct RTSP",
    version="1.0.0",
    lifespan=lifespan,
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
# WORKER REAPER (cleans dead/wedged workers; runs continuously)
# ============================================================================
#
# In production FastTag is expected to run 24/7. Workers can crash silently
# (RTSP timeout, NVR reboot, decoder fault, OOM) or wedge during cv2 startup
# without ever transitioning past status='starting'. Without a reaper they
# stay tracked, holding stats slots and (if alive but wedged) holding NVR
# slots and memory. The reaper periodically:
#   1. Removes workers whose process is dead (proc.is_alive() False)
#   2. Force-kills workers stuck in 'starting' status past STARTING_TIMEOUT_S

REAP_INTERVAL_S = 10
STARTING_TIMEOUT_S = 30
reaper_running = False

# Stagger between multiprocessing.Process.start() calls in /start. Mass
# back-to-back spawns on Windows trigger native-init races (cv2 DLL load,
# pupil_apriltags Detector instantiation) that segfault children before
# they execute any Python — bypasses the per-worker try/except. ~100ms
# gap is enough to avoid the race; total added startup for 30 workers
# is ~3s, negligible vs the cost of half the workers dying.
SPAWN_STAGGER_S = 0.15


def worker_reaper():
    """Background thread — periodically clean dead/wedged workers."""
    global reaper_running
    reaper_running = True
    logger.info("Worker reaper started")

    while reaper_running:
        time.sleep(REAP_INTERVAL_S)
        try:
            now = time.time()
            with workers_lock:
                items = list(workers.items())

            for ip, w in items:
                proc = w["process"]
                started_at = w["started_at"]
                age = now - started_at

                # Case 1: process is dead — drop from tracking
                if not proc.is_alive():
                    logger.info(f"reaper: removing dead worker for {ip} (pid {proc.pid}, age {age:.0f}s)")
                    with workers_lock:
                        workers.pop(ip, None)
                    with stats_lock:
                        camera_stats.pop(ip, None)
                    continue

                # Case 2: still 'starting' past the timeout — kill it
                with stats_lock:
                    hb = camera_stats.get(ip)
                status = hb.get("status") if hb else "starting"
                if age > STARTING_TIMEOUT_S and status == "starting":
                    logger.warning(f"reaper: killing stuck worker for {ip} (pid {proc.pid}, age {age:.0f}s, status=starting)")
                    w["stop_event"].set()
                    proc.join(timeout=2)
                    if proc.is_alive():
                        proc.kill()
                    with workers_lock:
                        workers.pop(ip, None)
                    with stats_lock:
                        camera_stats.pop(ip, None)

        except Exception as e:
            logger.exception(f"worker_reaper iteration error: {e}")

    logger.info("Worker reaper stopped")


# ============================================================================
# CAMERA-SERVICE HEALTH PING
# ============================================================================
#
# Some cameras (PoE-CAMs / M5CamServer) speak HTTP/MJPEG only — single
# W5500 client slot. Detection workers for those cams ride camera-service's
# http_mediator (port 8001) so they don't contend with the mediator's
# stream. If camera-service is down, those workers can't function. We
# need fast detection of that state so:
#   1. workers can fail-fast instead of busy-looping on dead requests
#   2. /tag-debug can surface a clear indicator

CAMERA_SERVICE_URL = os.environ.get("CAMERA_SERVICE_URL", "http://127.0.0.1:8001")
CAMERA_SERVICE_PING_INTERVAL_S = 5
CAMERA_SERVICE_PING_TIMEOUT_S = 2

camera_service_status = {
    "reachable": None,        # None = haven't checked yet
    "url": CAMERA_SERVICE_URL,
    "last_check_at": 0.0,
    "last_ok_at": 0.0,
    "last_error": None,
}


def camera_service_pinger():
    """Background thread: periodic ping to camera-service /api/health."""
    logger.info(f"Camera-service pinger started (target {CAMERA_SERVICE_URL})")
    while True:
        try:
            r = requests.get(f"{CAMERA_SERVICE_URL}/api/health",
                             timeout=CAMERA_SERVICE_PING_TIMEOUT_S)
            now = time.time()
            camera_service_status["last_check_at"] = now
            if r.status_code == 200:
                if camera_service_status["reachable"] is False:
                    logger.info("Camera-service back UP")
                camera_service_status["reachable"] = True
                camera_service_status["last_ok_at"] = now
                camera_service_status["last_error"] = None
            else:
                if camera_service_status["reachable"] is True:
                    logger.warning(f"Camera-service unhealthy: HTTP {r.status_code}")
                camera_service_status["reachable"] = False
                camera_service_status["last_error"] = f"HTTP {r.status_code}"
        except requests.exceptions.RequestException as e:
            now = time.time()
            camera_service_status["last_check_at"] = now
            if camera_service_status["reachable"] is True:
                logger.warning(f"Camera-service DOWN: {type(e).__name__}: {e}")
            camera_service_status["reachable"] = False
            camera_service_status["last_error"] = f"{type(e).__name__}: {e}"
        except Exception as e:
            logger.exception(f"camera_service_pinger unexpected error: {e}")

        time.sleep(CAMERA_SERVICE_PING_INTERVAL_S)


def is_camera_service_up() -> bool:
    """Synchronous read of the most recent ping state. False if unknown."""
    return camera_service_status.get("reachable") is True

# ============================================================================
# MODELS
# ============================================================================

class StartRequest(BaseModel):
    cameras: List[str] = []  # list of camera IPs, or empty for "all"
    config: Optional[dict] = None  # override default config
    auto_stop_seconds: float = 0  # 0 = no auto-stop; >0 schedules /stop after N seconds

class StopRequest(BaseModel):
    cameras: List[str] = []  # list of camera IPs, or empty for "all"


# ============================================================================
# AUTO-STOP TIMER
# ============================================================================
#
# /start can specify auto_stop_seconds; we schedule a single-shot timer to
# call the stop logic when it fires. Server-side so closing the page
# doesn't strand workers running indefinitely. The tag-debug page sets 20
# minutes by default — debug session safety net.

_auto_stop_timer: Optional[threading.Timer] = None
_auto_stop_deadline: float = 0.0  # epoch seconds; 0 = disabled


def _cancel_auto_stop():
    global _auto_stop_timer, _auto_stop_deadline
    if _auto_stop_timer is not None:
        _auto_stop_timer.cancel()
        _auto_stop_timer = None
    _auto_stop_deadline = 0.0


def _schedule_auto_stop(seconds: float):
    global _auto_stop_timer, _auto_stop_deadline
    _cancel_auto_stop()
    if seconds <= 0:
        return

    def _fire():
        logger.info(f"auto-stop firing — stopping all workers")
        _stop_all_workers()
        global _auto_stop_timer, _auto_stop_deadline
        _auto_stop_timer = None
        _auto_stop_deadline = 0.0

    _auto_stop_deadline = time.time() + seconds
    _auto_stop_timer = threading.Timer(seconds, _fire)
    _auto_stop_timer.daemon = True
    _auto_stop_timer.start()
    logger.info(f"auto-stop scheduled in {seconds:.0f}s")


def _stop_all_workers():
    """Internal: stop every running worker (called by auto-stop timer and /stop)."""
    with workers_lock:
        cam_ips = list(workers.keys())
    for ip in cam_ips:
        with workers_lock:
            w = workers.pop(ip, None)
        if w:
            w["stop_event"].set()
            w["process"].join(timeout=5)
            if w["process"].is_alive():
                w["process"].kill()
        with stats_lock:
            camera_stats.pop(ip, None)

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

        # Merge detection config with per-camera settings
        worker_config = {
            **config,
            "target_fps": cam_cfg.get("target_fps", 5.0),
            "flip": cam_cfg.get("flip", None),
        }

        # Dispatch by protocol: RTSP cams open their own connection;
        # HTTP cams (PoE-CAM / M5CamServer) ride camera-service's
        # http_mediator since their W5500 chip allows only one client.
        protocol = (cam.get("protocol") or "rtsp").lower()
        stop_event = multiprocessing.Event()

        if protocol == "http":
            if not is_camera_service_up():
                errors.append({
                    "ip": ip,
                    "error": f"http camera, but camera-service unreachable at {CAMERA_SERVICE_URL}"
                })
                continue
            target = http_detection_worker
            worker_args = (ip, CAMERA_SERVICE_URL, result_queue, stop_event, worker_config)
            access_label = "http-mediator"
        else:
            rtsp_url = build_rtsp_url(cam)
            if not rtsp_url:
                errors.append({"ip": ip, "error": "no RTSP path available (direct or NVR)"})
                continue
            target = detection_worker
            worker_args = (ip, rtsp_url, result_queue, stop_event, worker_config)
            access_label = cam.get("access")

        proc = multiprocessing.Process(target=target, args=worker_args, daemon=True)
        proc.start()

        with workers_lock:
            workers[ip] = {
                "process": proc,
                "stop_event": stop_event,
                "started_at": time.time(),
                "config": worker_config,
                "model": cam.get("model"),
                "access": access_label,
                "protocol": protocol,
            }

        started.append(ip)
        logger.info(f"Started {protocol} worker for {ip} (pid {proc.pid}, model {cam.get('model')}, access {access_label}, fps {worker_config['target_fps']})")

        # Stagger to avoid native-init races (see SPAWN_STAGGER_S comment).
        if SPAWN_STAGGER_S > 0:
            time.sleep(SPAWN_STAGGER_S)

    # Schedule auto-stop if requested (only when starting at least one worker;
    # if everyone errored or already-running, don't reset an existing timer).
    if request.auto_stop_seconds > 0 and started:
        _schedule_auto_stop(request.auto_stop_seconds)

    return {
        "started": started,
        "already_running": already_running,
        "skipped_disabled": skipped,
        "errors": errors,
        "total_active": len(workers),
        "auto_stop_in_seconds": (
            round(_auto_stop_deadline - time.time(), 1)
            if _auto_stop_deadline else None
        ),
    }


@app.post("/stop")
def stop_cameras(request: StopRequest):
    """Stop detection on cameras. Pass IPs or empty list for all."""
    # Stop-all cancels any pending auto-stop timer; per-camera stops do not
    # (the rest of the batch may still want the timer to apply).
    if not request.cameras:
        _cancel_auto_stop()

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
            "protocol": w.get("protocol", "rtsp"),
            "access": w.get("access"),
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

    cs = dict(camera_service_status)
    if cs["last_check_at"]:
        cs["last_check_age_s"] = round(now - cs["last_check_at"], 1)
    if cs["last_ok_at"]:
        cs["last_ok_age_s"] = round(now - cs["last_ok_at"], 1)

    auto_stop_in = (
        round(_auto_stop_deadline - now, 1)
        if _auto_stop_deadline > now else None
    )

    return {
        "active_cameras": len(cameras),
        "cameras": cameras,
        "detection_buffer_size": buf_size,
        "detection_buffer_capacity": DETECTION_BUFFER_SIZE,
        "camera_service": cs,
        "auto_stop_in_seconds": auto_stop_in,
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
# LIVE OVERLAY (debug — server-side cv2 drawing + multipart MJPEG)
# ============================================================================
#
# Opens its OWN RTSP connection to the camera (separate from any FastTag
# detection worker). Intended for ad-hoc debug viewing — close the browser
# tab to drop the connection.

_overlay_detectors = {}
_overlay_detectors_lock = threading.Lock()


def _get_overlay_detector(family: str):
    """Module-level cache of one Detector per family (per-process)."""
    with _overlay_detectors_lock:
        det = _overlay_detectors.get(family)
        if det is None:
            from pupil_apriltags import Detector
            det = Detector(
                families=family,
                nthreads=4,
                quad_decimate=2.0,
                quad_sigma=0.0,
                decode_sharpening=0.25,
            )
            _overlay_detectors[family] = det
        return det


def _draw_overlay(frame, detections):
    """Draw tag border, center, ID, and decision margin on the frame in-place."""
    for det in detections:
        corners = det.corners.astype(int)
        for i in range(4):
            cv2.line(frame, tuple(corners[i]), tuple(corners[(i + 1) % 4]), (0, 255, 0), 3)
        cx, cy = int(det.center[0]), int(det.center[1])
        cv2.circle(frame, (cx, cy), 8, (0, 0, 255), -1)
        cv2.putText(frame, f"#{det.tag_id}", (cx - 50, cy - 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
        cv2.putText(frame, f"Q:{det.decision_margin:.0f}", (cx - 50, cy + 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)


@app.get("/live-overlay/{camera_ip}")
def live_overlay(
    camera_ip: str,
    family: Optional[str] = Query(None, description="Tag family (defaults to current DEFAULT_CONFIG)"),
    fps: float = Query(10.0, description="Max frames per second to detect+encode (rate-limit guard); production-tuned default"),
    max_seconds: float = Query(300, description="Auto-close stream after N seconds (forgotten-tab guard)"),
):
    """Live MJPEG stream with detection overlay drawn server-side.

    Dispatch:
      RTSP cams — opens its own VideoCapture (independent of any FastTag
        worker on this cam).
      HTTP cams (PoE-CAM / M5CamServer) — fetches JPEG frames from
        camera-service's http_mediator. Single-client W5500 chip means
        we cannot open a fresh stream; ride the mediator.

    Closes on:
      - HTTP client disconnect (browser closes tab / removes <img>)
      - Frame read failure (RTSP drop / camera-service unreachable)
      - max_seconds elapsed (default 300s; forgotten-tab guard)

    Rate-limited to `fps` so a single overlay viewer can't saturate
    CPU running detection at full stream rate.
    """
    cams = {c["ip"]: c for c in get_all_active_cameras()}
    cam = cams.get(camera_ip)
    if cam is None:
        raise HTTPException(status_code=404, detail=f"camera {camera_ip} not in lodge.db")

    fam = family or DEFAULT_CONFIG["families"]
    detector = _get_overlay_detector(fam)
    frame_interval = 1.0 / fps if fps > 0 else 0
    deadline = time.time() + max_seconds
    protocol = (cam.get("protocol") or "rtsp").lower()

    if protocol == "http":
        if not is_camera_service_up():
            raise HTTPException(
                status_code=503,
                detail=f"camera-service unreachable at {CAMERA_SERVICE_URL}; cannot stream HTTP camera"
            )

        frame_url = f"{CAMERA_SERVICE_URL}/api/http-cameras/{camera_ip}/frame"

        def generate_http():
            session = requests.Session()
            last_emit = 0.0
            consecutive_fail = 0
            try:
                while time.time() < deadline:
                    now = time.time()
                    if frame_interval > 0 and (now - last_emit) < frame_interval:
                        time.sleep(max(0.01, frame_interval - (now - last_emit)))
                        continue
                    try:
                        r = session.get(frame_url, timeout=2)
                    except requests.exceptions.RequestException:
                        consecutive_fail += 1
                        if consecutive_fail >= 5:
                            break
                        time.sleep(0.5)
                        continue
                    if r.status_code != 200:
                        consecutive_fail += 1
                        if consecutive_fail >= 5:
                            break
                        time.sleep(0.5)
                        continue
                    consecutive_fail = 0
                    arr = np.frombuffer(r.content, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is None or frame.std() < 10:
                        continue
                    last_emit = time.time()
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    dets = detector.detect(gray)
                    _draw_overlay(frame, dets)
                    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if not ok:
                        continue
                    yield (b"--frame\r\n"
                           b"Content-Type: image/jpeg\r\n\r\n"
                           + jpeg.tobytes() + b"\r\n")
                logger.info(f"live-overlay (http): {camera_ip} closed")
            finally:
                session.close()

        return StreamingResponse(generate_http(), media_type="multipart/x-mixed-replace; boundary=frame")

    # RTSP path
    rtsp_url = build_rtsp_url(cam)
    if not rtsp_url:
        raise HTTPException(status_code=400, detail=f"no RTSP path available for {camera_ip}")

    def generate_rtsp():
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            logger.warning(f"live-overlay: failed to open {camera_ip}")
            return
        last_emit = 0.0
        try:
            while time.time() < deadline:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                if frame.std() < 10:
                    continue
                now = time.time()
                if frame_interval > 0 and (now - last_emit) < frame_interval:
                    continue
                last_emit = now
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                dets = detector.detect(gray)
                _draw_overlay(frame, dets)
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if not ok:
                    continue
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n"
                       + jpeg.tobytes() + b"\r\n")
            logger.info(f"live-overlay (rtsp): {camera_ip} closed")
        finally:
            cap.release()

    return StreamingResponse(generate_rtsp(), media_type="multipart/x-mixed-replace; boundary=frame")


# ============================================================================
# TAG-DEBUG PAGE (live tag↔camera pairs + drill-in)
# ============================================================================

@app.get("/tag-debug")
def tag_debug_page():
    """Live debug page — tag IDs in view paired with cameras seeing them."""
    return FileResponse(Path(__file__).parent / "tag-debug.html",
                        media_type="text/html")


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    # Distinctive Windows Terminal tab title via OSC escape (conhost handles ANSI natively on Win10+)
    sys.stdout.write("\033]0;FastTag (8003)\007")
    sys.stdout.flush()

    print("=" * 60)
    print("  FastTag — AprilTag Detection Server")
    print("=" * 60)
    print("  HTTP: http://localhost:8003")
    print("  Docs: http://localhost:8003/docs")
    print("=" * 60)
    print()

    uvicorn.run(app, host="0.0.0.0", port=8003, log_level="info")
