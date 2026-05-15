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
import random
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
from pool import DetectionPool

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

def _cleanup_orphaned_multiprocessing_children():
    """Kill multiprocessing children whose parent no longer exists.

    When FastTag crashes, detector pool children become orphans eating ~6GB each.
    This runs at startup to clean up any stragglers from a prior crash.
    """
    import subprocess
    import psutil

    killed = []
    try:
        for proc in psutil.process_iter(['pid', 'ppid', 'name', 'cmdline']):
            try:
                info = proc.info
                if info['name'] != 'python.exe':
                    continue
                cmdline = info.get('cmdline') or []
                cmdline_str = ' '.join(cmdline)
                if 'multiprocessing.spawn' not in cmdline_str:
                    continue
                # Check if parent exists
                ppid = info['ppid']
                if not psutil.pid_exists(ppid):
                    proc.kill()
                    killed.append(info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        # psutil not available — skip cleanup
        logger.warning("psutil not installed; skipping orphan cleanup")
        return

    if killed:
        logger.info(f"Cleaned up {len(killed)} orphaned multiprocessing children: {killed}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cleanup orphaned detector processes from prior crashes
    _cleanup_orphaned_multiprocessing_children()

    # Startup: launch background daemons. Names resolved at call time, so
    # forward references to functions defined later in the module are fine.
    threading.Thread(target=worker_reaper, daemon=True, name="worker-reaper").start()
    threading.Thread(target=camera_service_pinger, daemon=True, name="camera-svc-ping").start()
    threading.Thread(target=_prewarm_overlay_detector, daemon=True, name="overlay-prewarm").start()
    threading.Thread(target=session_summary_thread, daemon=True, name="session-summary").start()
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

# Pooled detection (RTSP cameras) — 3 detectors sharing all cameras
pool: Optional[DetectionPool] = None
pool_lock = threading.Lock()

# Per-camera worker tracking — HTTP cameras only (PoE-CAM / M5CamServer)
# camera_ip -> {process, stop_event, started_at, config, last_heartbeat}
workers: dict = {}
workers_lock = threading.Lock()

# Detection ring buffer
DETECTION_BUFFER_SIZE = 10000
detection_buffer: deque = deque(maxlen=DETECTION_BUFFER_SIZE)
detection_lock = threading.Lock()

# Per-camera stats (updated from heartbeats — HTTP workers only)
camera_stats: dict = {}  # camera_ip -> latest heartbeat dict
stats_lock = threading.Lock()

# Frame sequence staleness tracking — ip -> {"seq": N, "changed_at": time}
frame_seq_history: dict = {}
STALE_THRESHOLD_SECONDS = 5.0

# Session summary tracking — logs aggregate camera health every 20 minutes
SESSION_SUMMARY_INTERVAL_S = 1200  # 20 minutes
session_start_time: float = time.time()
last_summary_time: float = time.time()
# ip -> {"mac", "model", "first_seen", "last_seen", "samples", "total_fps", "total_frames", "reconnects", "running_samples"}
session_camera_stats: dict = {}
session_stats_lock = threading.Lock()

# Pool-level stats for session summary
session_pool_stats = {
    "samples": 0,
    "total_queue_depth": 0,
    "max_queue_depth": 0,
    "total_pressure": 0.0,
    "max_pressure": 0.0,
    "total_slots_in_use": 0,
}

# Shared queue for HTTP workers only
result_queue: Optional[multiprocessing.Queue] = None

# Default detection config
DEFAULT_CONFIG = {
    "families": "tagCustom48h12",
    "quad_decimate": 2.0,
}

# Pool config
POOL_NUM_DETECTORS = 3
POOL_NUM_SLOTS = 50
POOL_SLOT_SIZE = 50_000_000  # 50MB per slot (handles 5K+ frames)

# ============================================================================
# RESULT COLLECTOR THREAD
# ============================================================================

collector_running = False


def result_collector():
    """Drain results from pool (RTSP) and result_queue (HTTP) into detection_buffer."""
    global collector_running
    collector_running = True
    logger.info("Result collector started")

    while collector_running:
        # Drain pool results (RTSP cameras)
        if pool is not None:
            for msg in pool.get_results(max_items=100):
                if msg.get("type") == "detection":
                    with detection_lock:
                        detection_buffer.append(msg)

        # Drain HTTP worker queue
        if result_queue is not None:
            while True:
                try:
                    msg = result_queue.get_nowait()
                except Exception:
                    break
                if msg.get("type") == "heartbeat":
                    with stats_lock:
                        camera_stats[msg["camera_ip"]] = msg
                elif msg.get("type") == "detection":
                    with detection_lock:
                        detection_buffer.append(msg)

        time.sleep(0.1)

    logger.info("Result collector stopped")


# ============================================================================
# SESSION SUMMARY THREAD
# ============================================================================

summary_running = False


def _update_session_camera_stats():
    """Sample current camera states into session_camera_stats."""
    now = time.time()
    pool_status = pool.get_status() if pool else {}
    pool_readers = pool_status.get("readers", {})

    with workers_lock:
        http_workers_snapshot = dict(workers)
    with stats_lock:
        http_stats_snapshot = dict(camera_stats)

    # Combine all active IPs
    all_ips = set(pool_readers.keys()) | set(http_workers_snapshot.keys())

    with session_stats_lock:
        for ip in all_ips:
            # Get current state
            if ip in pool_readers:
                reader = pool_readers[ip]
                status = reader.get("status", "unknown")
                fps = reader.get("fps", 0)
                frame_seq = reader.get("frame_count", 0)
                respawn_count = 0
            elif ip in http_stats_snapshot:
                hb = http_stats_snapshot[ip]
                status = hb.get("status", "unknown")
                fps = hb.get("fps", 0)
                frame_seq = hb.get("frame_seq", 0)
                w = http_workers_snapshot.get(ip, {})
                respawn_count = w.get("respawn_count", 0)
            else:
                continue

            # Get MAC and model from DB if not cached
            if ip not in session_camera_stats:
                cam = get_camera_by_ip(ip)
                session_camera_stats[ip] = {
                    "mac": cam.get("mac") if cam else None,
                    "model": cam.get("model") if cam else None,
                    "first_seen": now,
                    "last_seen": now,
                    "samples": 0,
                    "total_fps": 0.0,
                    "last_frame_seq": 0,
                    "total_frames": 0,
                    "peak_frame_seq": 0,
                    "reconnects": 0,
                    "running_samples": 0,
                }

            s = session_camera_stats[ip]
            s["last_seen"] = now
            s["samples"] += 1
            s["total_fps"] += fps if fps else 0

            # Track peak frame_seq (camera ever produced frames, even between samples)
            if frame_seq > s["peak_frame_seq"]:
                s["peak_frame_seq"] = frame_seq

            # Count new frames since last sample
            if frame_seq > s["last_frame_seq"]:
                s["total_frames"] += (frame_seq - s["last_frame_seq"])
            s["last_frame_seq"] = frame_seq

            # Track reconnects (respawn_count increases)
            if respawn_count > s["reconnects"]:
                s["reconnects"] = respawn_count

            # Track how many samples were in "running" state
            if status == "running":
                s["running_samples"] += 1

        # Track pool-level stats
        if pool_status:
            queue_depth = pool_status.get("queue_depth") or 0
            pressure = pool_status.get("queue_pressure", 0)
            slots_in_use = pool_status.get("pool_slots_in_use", 0)

            session_pool_stats["samples"] += 1
            session_pool_stats["total_queue_depth"] += queue_depth
            session_pool_stats["max_queue_depth"] = max(session_pool_stats["max_queue_depth"], queue_depth)
            session_pool_stats["total_pressure"] += pressure
            session_pool_stats["max_pressure"] = max(session_pool_stats["max_pressure"], pressure)
            session_pool_stats["total_slots_in_use"] += slots_in_use


def _log_session_summary():
    """Log aggregate camera health summary for the last interval."""
    global last_summary_time
    now = time.time()
    interval_min = (now - last_summary_time) / 60
    session_min = (now - session_start_time) / 60

    with session_stats_lock:
        if not session_camera_stats:
            logger.info(f"[SESSION SUMMARY] @{session_min:.0f}m (last {interval_min:.0f}m) — no cameras tracked")
            return

        lines = [f"[SESSION SUMMARY] @{session_min:.0f}m (last {interval_min:.0f}m) — {len(session_camera_stats)} cameras:"]

        # Sort by IP numerically
        sorted_ips = sorted(session_camera_stats.keys(),
                           key=lambda x: tuple(int(p) for p in x.split('.')))

        for ip in sorted_ips:
            s = session_camera_stats[ip]
            mac = s["mac"] or "?"
            model = s["model"] or "?"
            samples = s["samples"]
            running = s["running_samples"]

            # Calculate metrics
            uptime_pct = (running / samples * 100) if samples > 0 else 0
            avg_fps = (s["total_fps"] / samples) if samples > 0 else 0
            total_frames = s["total_frames"]
            peak_frames = s["peak_frame_seq"]
            reconnects = s["reconnects"]

            # Health grade
            if peak_frames == 0:
                grade = "X"  # never produced any frames
            elif uptime_pct >= 95 and avg_fps >= 1.0:
                grade = "A"
            elif uptime_pct >= 80:
                grade = "B"
            elif uptime_pct >= 50:
                grade = "C"
            else:
                grade = "F"

            # Add reconnect penalty (doesn't apply to X)
            if grade != "X":
                if reconnects >= 3:
                    grade = min(grade, "C")  # cap at C if many reconnects
                elif reconnects >= 1:
                    grade = chr(min(ord(grade) + 1, ord("F")))  # downgrade one letter

            lines.append(
                f"  {ip:>15} | {mac:>17} | {model:>12} | "
                f"fps={avg_fps:>4.1f} frames={total_frames:>6} peak={peak_frames:>6} up={uptime_pct:>5.1f}% "
                f"reconn={reconnects} [{grade}]"
            )

        # Pool pressure summary
        ps = session_pool_stats
        if ps["samples"] > 0:
            avg_queue = ps["total_queue_depth"] / ps["samples"]
            avg_pressure = ps["total_pressure"] / ps["samples"]
            avg_slots = ps["total_slots_in_use"] / ps["samples"]
            lines.append(
                f"  [POOL] queue: avg={avg_queue:.1f} max={ps['max_queue_depth']} | "
                f"pressure: avg={avg_pressure:.1f}% max={ps['max_pressure']:.1f}% | "
                f"slots: avg={avg_slots:.1f}"
            )

        logger.info("\n".join(lines))

        # Reset stats for next interval (per-window, not cumulative)
        session_camera_stats.clear()
        session_pool_stats["samples"] = 0
        session_pool_stats["total_queue_depth"] = 0
        session_pool_stats["max_queue_depth"] = 0
        session_pool_stats["total_pressure"] = 0.0
        session_pool_stats["max_pressure"] = 0.0
        session_pool_stats["total_slots_in_use"] = 0

    last_summary_time = now


def session_summary_thread():
    """Background thread that logs session summary every 20 minutes."""
    global summary_running, last_summary_time
    summary_running = True
    logger.info("Session summary thread started")

    while summary_running:
        time.sleep(10)  # Sample stats every 10 seconds
        _update_session_camera_stats()

        # Log summary every SESSION_SUMMARY_INTERVAL_S
        now = time.time()
        if now - last_summary_time >= SESSION_SUMMARY_INTERVAL_S:
            _log_session_summary()

    logger.info("Session summary thread stopped")


# ============================================================================
# WORKER REAPER (HTTP workers only — pool handles its own detectors)
# ============================================================================
#
# HTTP workers (PoE-CAM / M5CamServer) still use per-camera processes. The
# reaper manages only these — the pool handles RTSP camera lifecycle internally.
# The reaper periodically:
#   1. Removes HTTP workers whose process is dead (proc.is_alive() False)
#   2. Force-kills HTTP workers stuck in 'starting' status past STARTING_TIMEOUT_S

REAP_INTERVAL_S = 10
STARTING_TIMEOUT_MIN = 15
STARTING_TIMEOUT_MAX = 20
reaper_running = False

# Wave spawning: start N workers, wait for them to stabilize, then next wave
WAVE_SIZE = 5
WAVE_SETTLE_S = 3  # seconds to wait between waves

# Stagger between multiprocessing.Process.start() calls in /start. Mass
# back-to-back spawns on Windows trigger native-init races (cv2 DLL load,
# pupil_apriltags Detector instantiation) that segfault children before
# they execute any Python — bypasses the per-worker try/except. ~100ms
# gap is enough to avoid the race; total added startup for 30 workers
# is ~3s, negligible vs the cost of half the workers dying.
SPAWN_STAGGER_S = 0.15

# Respawn budget for workers that die a native death (no Python crash log,
# proc dead within RESPAWN_AGE_S). The reaper retries up to RESPAWN_MAX
# times; subsequent reaper iterations naturally space out respawns. Reset
# on /start. Workers that exit via Python exception (crash_log written) or
# that ran past RESPAWN_AGE_S are NOT respawned — those are real failures.
RESPAWN_MAX = 3
RESPAWN_AGE_S = 30
_respawn_attempts: dict = {}
_respawn_lock = threading.Lock()
_exhausted_ips: set = set()  # IPs that hit respawn limit — shown as "exhausted" in subnet-state
_spawn_offset: int = 0  # rotates each /start so different cameras get priority


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
                saved_config = w.get("config", {})

                # Case 1: process is dead — possibly respawn
                if not proc.is_alive():
                    crash_log = CAMERAS_DIR / ip / "worker_crash.log"
                    has_python_crash = crash_log.exists() and crash_log.stat().st_size > 0

                    # Drop from tracking first
                    with workers_lock:
                        workers.pop(ip, None)
                    with stats_lock:
                        camera_stats.pop(ip, None)

                    # Respawn if: native crash (no Python trace) AND died
                    # quickly (within RESPAWN_AGE_S) AND budget left.
                    # Otherwise it's a real failure or worn-out spawn slot.
                    if not has_python_crash and age < RESPAWN_AGE_S:
                        with _respawn_lock:
                            count = _respawn_attempts.get(ip, 0)
                            if count >= RESPAWN_MAX:
                                logger.warning(f"reaper: {ip} hit respawn limit ({RESPAWN_MAX}) — giving up")
                                _exhausted_ips.add(ip)
                                continue
                            _respawn_attempts[ip] = count + 1
                            new_count = count + 1

                        # Re-resolve cam from lodge.db (handles metadata changes)
                        cams = {c["ip"]: c for c in get_all_active_cameras()}
                        cam = cams.get(ip)
                        if not cam:
                            logger.warning(f"reaper: {ip} no longer in lodge.db, dropping")
                            continue
                        ok, err = _spawn_one_worker(ip, cam, saved_config, respawn_count=new_count)
                        if ok:
                            logger.info(f"reaper: respawned {ip} after native death (attempt {new_count}/{RESPAWN_MAX})")
                        else:
                            logger.warning(f"reaper: respawn failed for {ip}: {err}")
                    else:
                        reason = "Python exception" if has_python_crash else f"died after {age:.0f}s (past RESPAWN_AGE_S)"
                        logger.info(f"reaper: removed dead worker for {ip} (pid {proc.pid}, {reason})")
                    continue

                # Case 2: still 'starting' past the timeout — kill it
                with stats_lock:
                    hb = camera_stats.get(ip)
                status = hb.get("status") if hb else "starting"

                # If a worker successfully reached running, clear its respawn budget.
                if status == "running":
                    with _respawn_lock:
                        _respawn_attempts.pop(ip, None)

                starting_timeout = w.get("starting_timeout", STARTING_TIMEOUT_MIN)
                if age > starting_timeout and status == "starting":
                    logger.warning(f"reaper: killing stuck worker for {ip} (pid {proc.pid}, age {age:.0f}s, timeout {starting_timeout:.0f}s)")
                    w["stop_event"].set()
                    proc.join(timeout=2)
                    if proc.is_alive():
                        proc.kill()
                    with workers_lock:
                        workers.pop(ip, None)
                    with stats_lock:
                        camera_stats.pop(ip, None)

                    # Respawn stuck workers same as native-crash deaths
                    with _respawn_lock:
                        count = _respawn_attempts.get(ip, 0)
                        if count >= RESPAWN_MAX:
                            logger.warning(f"reaper: {ip} hit respawn limit ({RESPAWN_MAX}) after stuck-kill — giving up")
                            _exhausted_ips.add(ip)
                            continue
                        _respawn_attempts[ip] = count + 1
                        new_count = count + 1

                    cams = {c["ip"]: c for c in get_all_active_cameras()}
                    cam = cams.get(ip)
                    if not cam:
                        logger.warning(f"reaper: {ip} no longer in lodge.db, dropping")
                        continue
                    ok, err = _spawn_one_worker(ip, cam, saved_config, respawn_count=new_count)
                    if ok:
                        logger.info(f"reaper: respawned {ip} after stuck-kill (attempt {new_count}/{RESPAWN_MAX})")
                    else:
                        logger.warning(f"reaper: respawn failed for {ip}: {err}")

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
    global pool

    # Stop pool
    with pool_lock:
        if pool is not None:
            pool.stop()
            pool = None
            logger.info("Auto-stop: released DetectionPool")

    # Stop HTTP workers
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
    pool_status = pool.get_status() if pool else {}
    pool_count = len(pool_status.get("readers", {}))
    with workers_lock:
        http_count = len(workers)
    return {
        "service": "FastTag",
        "version": "1.0.0",
        "port": 8003,
        "active_cameras": pool_count + http_count,
        "pool_active": pool is not None,
    }


def _spawn_one_worker(ip: str, cam: dict, worker_config: dict, *, respawn_count: int = 0):
    """
    Spawn a single detection worker for `cam`. Caller must hold no locks
    and must have verified `ip` is not already in `workers`. Returns
    (success, error_or_None).

    Used by both /start (initial spawns) and the reaper (auto-respawn on
    native crash).
    """
    if result_queue is None:
        return False, "result_queue not initialized — call /start first"

    protocol = (cam.get("protocol") or "rtsp").lower()
    stop_event = multiprocessing.Event()

    if protocol == "http":
        if not is_camera_service_up():
            return False, f"camera-service unreachable at {CAMERA_SERVICE_URL}"
        target = http_detection_worker
        worker_args = (ip, CAMERA_SERVICE_URL, result_queue, stop_event, worker_config)
        access_label = "http-mediator"
    else:
        rtsp_url = build_rtsp_url(cam)
        if not rtsp_url:
            return False, "no RTSP path available (direct or NVR)"
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
            "starting_timeout": random.uniform(STARTING_TIMEOUT_MIN, STARTING_TIMEOUT_MAX),
            "config": worker_config,
            "model": cam.get("model"),
            "access": access_label,
            "protocol": protocol,
            "respawn_count": respawn_count,
        }

    suffix = f" [respawn {respawn_count}/{RESPAWN_MAX}]" if respawn_count else ""
    logger.info(f"Started {protocol} worker for {ip} (pid {proc.pid}, model {cam.get('model')}, access {access_label}, fps {worker_config['target_fps']}){suffix}")
    return True, None


@app.post("/start")
def start_cameras(request: StartRequest):
    """Start detection on cameras. Pass IPs or empty list for all."""
    global result_queue, pool, _spawn_offset

    config = {**DEFAULT_CONFIG, **(request.config or {})}

    # Clear exhausted set — fresh /start gives everyone a new chance
    _exhausted_ips.clear()

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

    # Ensure pool for RTSP cameras (creates 3 detector processes)
    with pool_lock:
        if pool is None:
            logger.info(f"Creating DetectionPool ({POOL_NUM_DETECTORS} detectors, {POOL_NUM_SLOTS} slots)")
            pool = DetectionPool(
                num_detectors=POOL_NUM_DETECTORS,
                num_slots=POOL_NUM_SLOTS,
                slot_size=POOL_SLOT_SIZE,
                config=config,
            )
            pool.start()

    # Ensure result queue + collector for HTTP workers
    if result_queue is None:
        result_queue = multiprocessing.Queue()
        t = threading.Thread(target=result_collector, daemon=True)
        t.start()

    started = []
    already_running = []
    skipped = []
    errors = []

    # Get current pool readers to check already-running
    pool_status = pool.get_status() if pool else {}
    pool_readers = set(pool_status.get("readers", {}).keys())

    for ip in cam_ips:
        cam = cam_lookup.get(ip)
        if not cam:
            errors.append({"ip": ip, "error": "not found in lodge.db"})
            continue

        cam_cfg = load_camera_config(ip)
        if not cam_cfg.get("enabled", True):
            skipped.append(ip)
            continue

        protocol = (cam.get("protocol") or "rtsp").lower()

        if protocol == "http":
            # HTTP cameras use individual workers (PoE-CAM / M5CamServer via mediator)
            with workers_lock:
                if ip in workers:
                    already_running.append(ip)
                    continue

            worker_config = {
                **config,
                "target_fps": cam_cfg.get("target_fps", 5.0),
                "flip": cam_cfg.get("flip", None),
            }

            with _respawn_lock:
                _respawn_attempts.pop(ip, None)

            ok, err = _spawn_one_worker(ip, cam, worker_config)
            if ok:
                started.append(ip)
            else:
                errors.append({"ip": ip, "error": err})
        else:
            # RTSP cameras use the pool
            if ip in pool_readers:
                already_running.append(ip)
                continue

            rtsp_url = build_rtsp_url(cam)
            if not rtsp_url:
                errors.append({"ip": ip, "error": "no RTSP path available"})
                continue

            target_fps = cam_cfg.get("target_fps", 5.0)
            pool.add_camera(ip, rtsp_url, target_fps=target_fps)
            started.append(ip)
            logger.info(f"Added {ip} to pool (fps {target_fps})")

    # Count total active
    pool_status = pool.get_status() if pool else {}
    total_active = len(pool_status.get("readers", {})) + len(workers)

    # Schedule auto-stop if requested
    if request.auto_stop_seconds > 0 and started:
        _schedule_auto_stop(request.auto_stop_seconds)

    return {
        "started": started,
        "already_running": already_running,
        "skipped_disabled": skipped,
        "errors": errors,
        "total_active": total_active,
        "pool_detectors": len([d for d in pool_status.get("detectors", []) if d.get("alive")]),
        "auto_stop_in_seconds": (
            round(_auto_stop_deadline - time.time(), 1)
            if _auto_stop_deadline else None
        ),
    }


@app.post("/stop")
def stop_cameras(request: StopRequest):
    """Stop detection on cameras. Pass IPs or empty list for all."""
    global pool

    # Stop-all cancels any pending auto-stop timer; per-camera stops do not
    # (the rest of the batch may still want the timer to apply).
    stop_all = not request.cameras
    if stop_all:
        _cancel_auto_stop()

    # Get current pool readers
    pool_status = pool.get_status() if pool else {}
    pool_readers = set(pool_status.get("readers", {}).keys())

    if request.cameras:
        cam_ips = request.cameras
    else:
        # All cameras: pool + HTTP workers
        with workers_lock:
            http_ips = list(workers.keys())
        cam_ips = list(pool_readers) + http_ips

    stopped = []
    not_running = []

    for ip in cam_ips:
        # Check pool first
        if ip in pool_readers:
            pool.remove_camera(ip)
            stopped.append(ip)
            logger.info(f"Removed {ip} from pool")
            continue

        # Then HTTP workers
        with workers_lock:
            w = workers.pop(ip, None)

        if not w:
            not_running.append(ip)
            continue

        w["stop_event"].set()
        w["process"].join(timeout=5)
        if w["process"].is_alive():
            w["process"].kill()
            logger.warning(f"Force-killed HTTP worker for {ip}")

        stopped.append(ip)
        logger.info(f"Stopped HTTP worker for {ip}")

        # Clear stats
        with stats_lock:
            camera_stats.pop(ip, None)

    # If stop-all, also stop the pool entirely
    if stop_all and pool is not None:
        with pool_lock:
            pool.stop()
            pool = None
        logger.info("Stopped and released DetectionPool")

    # Count remaining
    pool_status = pool.get_status() if pool else {}
    total_active = len(pool_status.get("readers", {})) + len(workers)

    return {
        "stopped": stopped,
        "not_running": not_running,
        "total_active": total_active,
    }


@app.get("/status")
def get_status():
    """Per-camera stats: fps, detection count, uptime, status."""
    now = time.time()
    cameras = {}

    def check_staleness(ip: str, frame_seq: int) -> bool:
        """Track frame_seq changes; return True if stale (no change in STALE_THRESHOLD_SECONDS)."""
        hist = frame_seq_history.get(ip)
        if hist is None or hist["seq"] != frame_seq:
            frame_seq_history[ip] = {"seq": frame_seq, "changed_at": now}
            return False
        return (now - hist["changed_at"]) > STALE_THRESHOLD_SECONDS

    # Pool cameras (RTSP)
    pool_status = pool.get_status() if pool else {}
    for ip, reader in pool_status.get("readers", {}).items():
        frame_seq = reader.get("frame_count", 0)
        fps_stale = check_staleness(ip, frame_seq)
        cameras[ip] = {
            "pid": None,  # pool readers are threads, not processes
            "alive": reader.get("status") != "stopped",
            "uptime_sec": round(now - reader.get("started_at", now), 1) if reader.get("started_at") else 0,
            "model": None,  # pool doesn't track model
            "protocol": "rtsp",
            "access": "pool",
            "respawn_count": 0,
            "fps": reader.get("fps", 0),
            "fps_stale": fps_stale,
            "frame_seq": frame_seq,
            "detect_count": 0,  # pool tracks this per-detector, not per-camera
            "status": reader.get("status", "starting"),
            "error": reader.get("error"),
            "flip_mode": "normal",  # pool doesn't track flip per-camera yet
            "last_heartbeat_age": None,
            "grabs": reader.get("grabs", 0),
            "skipped_grey": reader.get("skipped_grey", 0),
        }

    # HTTP workers
    with workers_lock:
        worker_ips = dict(workers)

    for ip, w in worker_ips.items():
        with stats_lock:
            hb = camera_stats.get(ip)

        proc = w["process"]
        frame_seq = hb["frame_seq"] if hb else 0
        fps_stale = check_staleness(ip, frame_seq)
        cameras[ip] = {
            "pid": proc.pid,
            "alive": proc.is_alive(),
            "uptime_sec": round(now - w["started_at"], 1),
            "model": w.get("model"),
            "protocol": w.get("protocol", "http"),
            "access": w.get("access"),
            "respawn_count": w.get("respawn_count", 0),
            "fps": hb["fps"] if hb else 0,
            "fps_stale": fps_stale,
            "frame_seq": frame_seq,
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

    # Pool detector stats
    detectors = pool_status.get("detectors", []) if pool_status else []
    alive_detectors = [d for d in detectors if d.get("alive")]

    return {
        "active_cameras": len(cameras),
        "cameras": cameras,
        "detection_buffer_size": buf_size,
        "detection_buffer_capacity": DETECTION_BUFFER_SIZE,
        "camera_service": cs,
        "auto_stop_in_seconds": auto_stop_in,
        "pool": {
            "detectors_alive": len(alive_detectors),
            "detectors_total": len(detectors),
            "total_frames_processed": sum(d.get("frames_processed", 0) for d in detectors),
            "detectors": detectors,
        } if pool else None,
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


# ============================================================================
# SUBNET STATE (live grid view of /24)
# ============================================================================

import re as _re
import subprocess as _subprocess

_subnet_cache = {"data": None, "fetched_at": 0.0}
SUBNET_CACHE_TTL_S = 5


def _detect_subnet() -> str:
    """Most common /24 prefix among lodge.db cams."""
    if not DB_PATH.exists():
        return "192.168.0"
    conn = sqlite3.connect(str(DB_PATH), timeout=2)
    cur = conn.cursor()
    cur.execute("SELECT ip FROM cameras WHERE ip IS NOT NULL")
    counts = {}
    for (ip,) in cur.fetchall():
        m = _re.match(r"^(\d+\.\d+\.\d+)\.", ip)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    conn.close()
    if not counts:
        return "192.168.0"
    return max(counts.items(), key=lambda x: x[1])[0]


def _read_arp_table() -> dict:
    """Returns {ip: mac} from `arp -a` for IPv4 entries (Windows-format output)."""
    try:
        result = _subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=2)
    except Exception:
        return {}
    arp = {}
    for line in result.stdout.splitlines():
        m = _re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([\da-f]{2}(?:[-:][\da-f]{2}){5})\s+", line, _re.I)
        if m:
            arp[m.group(1)] = m.group(2).lower().replace("-", ":")
    return arp


def _ping_sweep(subnet: str):
    """Fire ping at all .1-.254 in parallel to populate the ARP table.
    ~3-5s on a /24 with concurrency 64."""
    import concurrent.futures

    def ping_one(ip):
        try:
            _subprocess.run(["ping", "-n", "1", "-w", "300", ip],
                            stdout=_subprocess.DEVNULL,
                            stderr=_subprocess.DEVNULL,
                            timeout=1.5)
        except Exception:
            pass

    ips = [f"{subnet}.{i}" for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        list(ex.map(ping_one, ips))


def _load_mac_prefixes() -> list:
    """Cam-like MAC prefixes from lodge.db mac_prefixes table."""
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH), timeout=2)
    cur = conn.cursor()
    try:
        cur.execute("SELECT prefix FROM mac_prefixes")
        return [p.lower() for (p,) in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _mac_is_cam_like(mac: str, prefixes: list) -> bool:
    if not mac:
        return False
    m = mac.lower()
    for p in prefixes:
        if m.startswith(p):
            return True
    return False


@app.get("/subnet-state")
def get_subnet_state(
    ping: bool = Query(False, description="Run a ping sweep first to refresh ARP (~3-5s); otherwise uses stale ARP table"),
):
    """Per-IP state for the local /24 subnet.

    Combines four signals:
      - lodge.db cameras (which IPs are known cams)
      - ARP table (which IPs have recently communicated; gives MAC)
      - FastTag /status workers (which cams have detection running)
      - mac_prefixes (which MACs look cam-like, to flag potential new cams)

    Returns an array of 256 cells (octets 0..255), each tagged with a state:
      active     — cam in lodge.db, FastTag worker running
      starting   — cam in lodge.db, FastTag worker still starting
      wedged     — cam in lodge.db, FastTag worker reconnecting
      known_idle — cam in lodge.db, on network (ARP hit), no FastTag worker
      known_offline — cam in lodge.db, not on network
      discovered — IP responds with cam-like MAC, NOT in lodge.db
      non_cam    — IP responds, MAC not cam-like (NVR, server, switch)
      empty      — nothing on this IP
    """
    now = time.time()
    cached = _subnet_cache["data"]
    if not ping and cached and (now - _subnet_cache["fetched_at"] < SUBNET_CACHE_TTL_S):
        return cached

    subnet = _detect_subnet()

    if ping:
        _ping_sweep(subnet)

    arp = _read_arp_table()
    mac_prefixes = _load_mac_prefixes()

    db_cams = {}
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH), timeout=2)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT ip, mac, model, protocol FROM cameras WHERE ip IS NOT NULL")
        for row in cur.fetchall():
            db_cams[row["ip"]] = dict(row)
        conn.close()

    # Pool readers (RTSP) + HTTP workers
    pool_status = pool.get_status() if pool else {}
    pool_readers = pool_status.get("readers", {})

    with workers_lock:
        http_workers = set(workers.keys())
    with stats_lock:
        http_stats = dict(camera_stats)

    # Combine active IPs from both sources
    active = set(pool_readers.keys()) | http_workers

    cells = []
    for i in range(256):
        ip = f"{subnet}.{i}"
        mac = arp.get(ip)
        cam = db_cams.get(ip)

        # Get status from pool or HTTP stats
        if ip in pool_readers:
            reader = pool_readers[ip]
            worker_status = reader.get("status")
            fps = reader.get("fps")
            frame_seq = reader.get("frame_count", 0)
        elif ip in http_stats:
            hb = http_stats[ip]
            worker_status = hb.get("status")
            fps = hb.get("fps")
            frame_seq = hb.get("frame_seq", 0)
        else:
            worker_status = None
            fps = None
            frame_seq = 0

        # Check fps staleness
        fps_stale = False
        if fps is not None:
            hist = frame_seq_history.get(ip)
            if hist is None or hist["seq"] != frame_seq:
                frame_seq_history[ip] = {"seq": frame_seq, "changed_at": now}
            elif (now - hist["changed_at"]) > STALE_THRESHOLD_SECONDS:
                fps_stale = True

        if cam and ip in active:
            if worker_status == "running":
                state = "active"
            elif worker_status == "reconnecting":
                state = "wedged"
            else:
                state = "starting"
        elif cam:
            if ip in _exhausted_ips:
                state = "exhausted"
            elif mac:
                state = "known_idle"
            else:
                state = "known_offline"
        elif mac:
            state = "discovered" if _mac_is_cam_like(mac, mac_prefixes) else "non_cam"
        else:
            state = "empty"

        cells.append({
            "ip": ip,
            "octet": i,
            "state": state,
            "mac": mac,
            "model": cam.get("model") if cam else None,
            "protocol": cam.get("protocol") if cam else None,
            "worker_status": worker_status,
            "fps": fps,
            "fps_stale": fps_stale,
        })

    out = {
        "subnet": subnet,
        "fetched_at": now,
        "ping_swept": ping,
        "cells": cells,
    }
    _subnet_cache["data"] = out
    _subnet_cache["fetched_at"] = now
    return out


@app.get("/cameras")
def list_available_cameras():
    """List all cameras from lodge.db with their config and detection status."""
    cams = get_all_active_cameras()

    # Active IPs from pool + HTTP workers
    pool_status = pool.get_status() if pool else {}
    pool_readers = set(pool_status.get("readers", {}).keys())
    with workers_lock:
        http_active = set(workers.keys())
    active_ips = pool_readers | http_active

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


class AddCameraRequest(BaseModel):
    ip: str
    mac: str
    model: str = "PoE-CAM"
    protocol: str = "http"
    http_path: Optional[str] = None
    rtsp_path: Optional[str] = None


@app.post("/cameras/add")
def add_camera(request: AddCameraRequest):
    """Add a discovered camera to lodge.db and optionally to running detection."""
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail="lodge.db not found")

    conn = sqlite3.connect(str(DB_PATH), timeout=2)
    cur = conn.cursor()

    # Check if already exists
    cur.execute("SELECT ip FROM cameras WHERE ip = ? OR mac = ?", (request.ip, request.mac))
    existing = cur.fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=409, detail=f"Camera already exists: {existing[0]}")

    # Insert based on protocol
    http_path = request.http_path or "/stream"
    rtsp_path = request.rtsp_path or "/Streaming/Channels/101"
    if request.protocol == "http":
        cur.execute(
            "INSERT INTO cameras (mac, ip, model, protocol, http_path) VALUES (?, ?, ?, ?, ?)",
            (request.mac, request.ip, request.model, request.protocol, http_path)
        )
    else:
        cur.execute(
            "INSERT INTO cameras (mac, ip, model, protocol, rtsp_path) VALUES (?, ?, ?, ?, ?)",
            (request.mac, request.ip, request.model, request.protocol, rtsp_path)
        )
    conn.commit()
    conn.close()

    logger.info(f"Added camera {request.ip} ({request.mac}, {request.model}) to lodge.db")

    # Auto-add to running detection if pool is active
    started = False
    if pool is not None:
        cam_cfg = load_camera_config(request.ip)
        target_fps = cam_cfg.get("target_fps", 5.0)

        if request.protocol == "http":
            # HTTP cameras need individual workers via camera-service
            if is_camera_service_up() and result_queue is not None:
                worker_config = {
                    **DEFAULT_CONFIG,
                    "target_fps": target_fps,
                }
                stop_event = multiprocessing.Event()
                proc = multiprocessing.Process(
                    target=http_detection_worker,
                    args=(request.ip, CAMERA_SERVICE_URL, result_queue, stop_event, worker_config),
                    daemon=True
                )
                proc.start()
                with workers_lock:
                    workers[request.ip] = {
                        "process": proc,
                        "stop_event": stop_event,
                        "started_at": time.time(),
                        "config": worker_config,
                        "model": request.model,
                        "access": "http-mediator",
                        "protocol": "http",
                    }
                started = True
                logger.info(f"Auto-started HTTP worker for {request.ip}")
        else:
            # RTSP cameras go into the pool
            user, passwd = get_camera_credentials(request.model, request.ip)
            passwd_enc = quote(passwd, safe='')
            rtsp_url = f"rtsp://{user}:{passwd_enc}@{request.ip}:554{rtsp_path}"
            pool.add_camera(request.ip, rtsp_url, target_fps=target_fps)
            started = True
            logger.info(f"Auto-added {request.ip} to pool")

    return {
        "added": request.ip,
        "mac": request.mac,
        "model": request.model,
        "protocol": request.protocol,
        "started": started,
    }


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
_overlay_prewarm_done = threading.Event()


def _get_overlay_detector(family: str):
    """Module-level cache of one Detector per family (per-process)."""
    with _overlay_detectors_lock:
        det = _overlay_detectors.get(family)
        if det is None:
            logger.info(f"Loading overlay detector for {family} (first call, ~60s)...")
            start = time.time()
            from pupil_apriltags import Detector
            det = Detector(
                families=family,
                nthreads=4,
                quad_decimate=2.0,
                quad_sigma=0.0,
                decode_sharpening=0.25,
            )
            _overlay_detectors[family] = det
            logger.info(f"Overlay detector loaded in {time.time()-start:.1f}s")
        return det


def _prewarm_overlay_detector():
    """Background pre-warm of overlay detector so first /live-overlay is instant."""
    try:
        _get_overlay_detector(DEFAULT_CONFIG["families"])
        _overlay_prewarm_done.set()
    except Exception as e:
        logger.warning(f"Overlay detector pre-warm failed: {e}")


def _draw_overlay(frame, detections):
    """Draw tag border, center, ID, and decision margin on the frame in-place.

    Accepts both pupil_apriltags Detection objects (with attributes) and
    dict records from detection_buffer (with keys).
    """
    for det in detections:
        # Support both object attributes and dict keys
        if hasattr(det, 'corners'):
            corners = det.corners.astype(int)
            center = det.center
            tag_id = det.tag_id
            margin = det.decision_margin
        else:
            corners = np.array(det['corners'], dtype=int)
            center = det['center']
            tag_id = det['tag_id']
            margin = det['decision_margin']

        for i in range(4):
            cv2.line(frame, tuple(corners[i]), tuple(corners[(i + 1) % 4]), (0, 255, 0), 3)
        cx, cy = int(center[0]), int(center[1])
        cv2.circle(frame, (cx, cy), 8, (0, 0, 255), -1)
        cv2.putText(frame, f"#{tag_id}", (cx - 50, cy - 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
        cv2.putText(frame, f"Q:{margin:.0f}", (cx - 50, cy + 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)


def _get_recent_detections(camera_ip: str, max_age_s: float = 0.5) -> list:
    """Get recent detections for a camera from the detection buffer."""
    cutoff = time.time() - max_age_s
    with detection_lock:
        return [d for d in detection_buffer
                if d['camera_ip'] == camera_ip and d.get('detect_time', 0) > cutoff]


@app.get("/live-overlay/{camera_ip}")
def live_overlay(
    camera_ip: str,
    family: Optional[str] = Query(None, description="Tag family (defaults to current DEFAULT_CONFIG)"),
    fps: float = Query(10.0, description="Max frames per second to detect+encode (rate-limit guard); production-tuned default"),
    max_seconds: float = Query(300, description="Auto-close stream after N seconds (forgotten-tab guard)"),
):
    """Live MJPEG stream with detection overlay drawn server-side.

    Dispatch:
      Pool cams — uses frames from DetectionPool (no extra RTSP connection).
      HTTP cams (PoE-CAM / M5CamServer) — fetches from camera-service mediator.
      RTSP cams not in pool — returns 503; must /start the camera first.

    Detection overlay uses results from pool's detector subprocesses (via
    detection_buffer), not a local detector call. This avoids native segfaults
    in the main process — crashes in detector subprocesses are contained and
    respawned automatically.

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

    # If camera is in pool, try pool frames first (avoids slow second RTSP connection)
    # But if no fresh frames available, fall through to direct RTSP
    in_pool = pool is not None and camera_ip in pool.readers
    logger.info(f"live-overlay: {camera_ip} in_pool={in_pool}")
    if in_pool:
        # Quick check: does the pool have a fresh frame? (within last 2 seconds)
        test_frame, test_ts = pool.get_latest_frame(camera_ip)
        frame_age = time.time() - test_ts if test_ts > 0 else 999
        logger.info(f"live-overlay: {camera_ip} test_frame={'yes' if test_frame is not None else 'no'}, ts={test_ts:.1f}, age={frame_age:.1f}s")
        if test_frame is not None and frame_age < 2.0:
            def generate_pool():
                last_emit = 0.0
                last_frame_ts = 0.0
                no_frame_count = 0
                logger.info(f"live-overlay: using pool frames for {camera_ip}")
                while time.time() < deadline:
                    now = time.time()
                    if frame_interval > 0 and (now - last_emit) < frame_interval:
                        time.sleep(0.02)
                        continue
                    frame, ts = pool.get_latest_frame(camera_ip)
                    if frame is None:
                        no_frame_count += 1
                        if no_frame_count == 100:  # ~2 seconds of no frames
                            logger.warning(f"live-overlay: {camera_ip} pool frames stopped")
                            break
                        time.sleep(0.02)
                        continue
                    if ts == last_frame_ts:
                        time.sleep(0.02)
                        continue
                    no_frame_count = 0
                    last_frame_ts = ts
                    last_emit = now
                    # Validate frame before cv2 operations
                    if frame.ndim != 3 or frame.shape[2] != 3 or frame.shape[0] < 10 or frame.shape[1] < 10:
                        logger.warning(f"live-overlay: {camera_ip} invalid frame shape {frame.shape}")
                        continue
                    try:
                        # Use pool detections instead of running detector in main process
                        dets = _get_recent_detections(camera_ip, max_age_s=0.5)
                        _draw_overlay(frame, dets)
                        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                        if not ok:
                            continue
                        yield (b"--frame\r\n"
                               b"Content-Type: image/jpeg\r\n\r\n"
                               + jpeg.tobytes() + b"\r\n")
                    except Exception as e:
                        logger.warning(f"live-overlay: {camera_ip} frame processing error: {e}")
                        continue
                logger.info(f"live-overlay (pool): {camera_ip} closed")

            return StreamingResponse(generate_pool(), media_type="multipart/x-mixed-replace; boundary=frame")
        else:
            logger.warning(f"live-overlay: {camera_ip} in pool but no fresh frames (age={frame_age:.1f}s)")
            raise HTTPException(
                status_code=503,
                detail=f"camera {camera_ip} is in pool but frames are stale ({frame_age:.1f}s old); worker may be stuck"
            )

    if protocol == "http":
        if not is_camera_service_up():
            raise HTTPException(
                status_code=503,
                detail=f"camera-service unreachable at {CAMERA_SERVICE_URL}; cannot stream HTTP camera"
            )

        frame_url = f"{CAMERA_SERVICE_URL}/api/http-cameras/{camera_ip}/frame"
        logger.info(f"live-overlay: {camera_ip} using HTTP path via {frame_url}")

        def generate_http():
            session = requests.Session()
            last_emit = 0.0
            consecutive_fail = 0
            frame_count = 0
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
                    # Validate frame before cv2 operations
                    if frame.ndim != 3 or frame.shape[2] != 3 or frame.shape[0] < 10 or frame.shape[1] < 10:
                        logger.warning(f"live-overlay: {camera_ip} invalid frame shape {frame.shape}")
                        continue
                    try:
                        last_emit = time.time()
                        # Use pool detections instead of running detector in main process
                        dets = _get_recent_detections(camera_ip, max_age_s=0.5)
                        _draw_overlay(frame, dets)
                        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                        if not ok:
                            continue
                        frame_count += 1
                        if frame_count == 1:
                            logger.info(f"live-overlay (http): {camera_ip} first frame sent")
                        yield (b"--frame\r\n"
                               b"Content-Type: image/jpeg\r\n\r\n"
                               + jpeg.tobytes() + b"\r\n")
                    except Exception as e:
                        logger.warning(f"live-overlay: {camera_ip} frame processing error: {e}")
                        continue
                logger.info(f"live-overlay (http): {camera_ip} closed after {frame_count} frames")
            finally:
                session.close()

        return StreamingResponse(generate_http(), media_type="multipart/x-mixed-replace; boundary=frame")

    # RTSP path — disabled because cv2.VideoCapture can segfault and crash the service
    raise HTTPException(
        status_code=503,
        detail=f"RTSP camera {camera_ip} is not running in pool; use /start to begin detection first"
    )


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
