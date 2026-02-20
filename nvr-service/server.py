#!/usr/bin/env python3
"""
NVR Probe Service
Standalone NVR channel probing and infrastructure verification.

Port: 7999
Tests both NVR-path and direct-camera RTSP for each channel.
Updates lodge.db with results.

Run during maintenance when camera-service is idle.
"""

import sys
import os
import logging
import sqlite3
import threading
import json
import base64
import uuid
import subprocess
import time as _time
from pathlib import Path
from typing import Optional, List
from urllib.parse import quote
from concurrent.futures import ProcessPoolExecutor, as_completed

import cv2
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

# ============================================================================
# PATHS
# ============================================================================

MODESTO_ROOT = Path(__file__).parent.parent
DB_PATH = MODESTO_ROOT / "warehouses" / "lodge" / "lodge.db"
LOG_FILE = Path(__file__).parent / "nvr-service.log"

# ============================================================================
# CAMERA CREDENTIALS (.env)
# ============================================================================

ENV_PATH = MODESTO_ROOT / ".env"
if ENV_PATH.exists():
    for _line in ENV_PATH.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

CAM_CRED_GROUPS = {
    "IPCamera": "CAM_UNIVIEW",
    "N802-IRC-GW": "CAM_N802",
    "YM600F_AF": "CAM_YM600F",
    "YMF52_STARIR_GW_AF": "CAM_066",
}

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

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# DB HELPERS
# ============================================================================

def get_nvr_info(nvr_id: str) -> Optional[dict]:
    """Query lodge.db for NVR connection info"""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM nvrs WHERE id = ?", (nvr_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def build_rtsp_url(nvr: dict, channel: int) -> str:
    """Build RTSP URL from NVR info and channel number"""
    ip = nvr['ip']
    username = nvr['username'] or 'admin'
    password = nvr['password'] or ''
    path_format = nvr['path_format']

    if '{channel:02d}' in path_format:
        path = path_format.replace('{channel:02d}', f'{channel:02d}')
    else:
        path = path_format.replace('{channel}', str(channel))

    password_encoded = quote(password, safe='')
    return f"rtsp://{username}:{password_encoded}@{ip}:554/{path}"

def get_direct_camera_info(nvr_id: str, channel: int) -> Optional[dict]:
    """Get camera IP, rtsp_path, model for direct access. Returns None if not available."""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cam.ip, cam.rtsp_path, cam.model
        FROM channels ch
        JOIN cameras cam ON ch.camera_id = cam.mac
        WHERE ch.nvr_id = ? AND ch.channel_number = ?
          AND cam.ip IS NOT NULL AND cam.rtsp_path IS NOT NULL
    """, (nvr_id, channel))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def build_direct_rtsp_url(camera: dict) -> str:
    """Build RTSP URL directly to camera IP, bypassing NVR."""
    ip = camera['ip']
    path = camera['rtsp_path']
    user, passwd = get_camera_credentials(camera['model'], ip)
    passwd_enc = quote(passwd, safe='')
    return f"rtsp://{user}:{passwd_enc}@{ip}:554{path}"

# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

app = FastAPI(
    title="NVR Probe Service",
    description="NVR channel probing and infrastructure verification",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# PROBE STATE
# ============================================================================

probe_state = {}  # nvr_id -> active probe dict
probe_history = []  # last 10 completed probes
probe_lock = threading.Lock()
PROBE_HISTORY_MAX = 10

# ============================================================================
# PROBE SUBPROCESS — tests both NVR-path and direct-camera RTSP
# ============================================================================

def _try_rtsp_capture(rtsp_url: str, timeout: int = 10) -> dict:
    """
    Attempt RTSP capture with thread-based timeout.
    Returns {success, std, bytes, resolution, jpeg, error}.
    """
    import threading as _thr
    import queue as _q
    result_q = _q.Queue()
    cap_holder = [None]

    def _cap():
        try:
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            cap_holder[0] = cap
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                result_q.put({"success": False, "std": 0, "bytes": 0, "resolution": None})
                return
            for _ in range(5):
                ret, f = cap.read()
                if ret and f is not None and f.std() > 10:
                    h, w = f.shape[:2]
                    _, buf = cv2.imencode('.jpg', f, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    result_q.put({
                        "success": True,
                        "std": round(float(f.std()), 1),
                        "bytes": len(buf),
                        "resolution": f"{w}x{h}",
                        "jpeg": buf.tobytes()
                    })
                    cap.release()
                    return
            result_q.put({"success": False, "std": 0, "bytes": 0, "resolution": None})
        except Exception as e:
            result_q.put({"success": False, "std": 0, "bytes": 0, "resolution": None, "error": str(e)})
        finally:
            c = cap_holder[0]
            if c is not None:
                try:
                    c.release()
                except:
                    pass

    t = _thr.Thread(target=_cap, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        c = cap_holder[0]
        if c is not None:
            try:
                c.release()
            except:
                pass
        return {"success": False, "std": 0, "bytes": 0, "resolution": None, "error": "timeout"}

    try:
        return result_q.get_nowait()
    except:
        return {"success": False, "std": 0, "bytes": 0, "resolution": None, "error": "no_result"}


def _probe_channel_subprocess(nvr_id: str, channel: int) -> dict:
    """
    Probe a single channel in a subprocess (own GIL).
    Tests both NVR-path and direct-camera RTSP.
    """
    result = {
        "channel": channel,
        "nvr_success": False, "nvr_std": 0, "nvr_bytes": 0,
        "nvr_resolution": None, "nvr_error": None,
        "direct_success": False, "direct_std": 0, "direct_bytes": 0,
        "direct_resolution": None, "direct_error": None,
        "direct_ip": None, "direct_model": None,
        "jpeg": None
    }

    # --- NVR-path test ---
    nvr = get_nvr_info(nvr_id)
    if not nvr:
        result["nvr_error"] = "nvr_not_found"
        return result

    nvr_url = build_rtsp_url(nvr, channel)
    nvr_result = _try_rtsp_capture(nvr_url)
    result["nvr_success"] = nvr_result["success"]
    result["nvr_std"] = nvr_result.get("std", 0)
    result["nvr_bytes"] = nvr_result.get("bytes", 0)
    result["nvr_resolution"] = nvr_result.get("resolution")
    result["nvr_error"] = nvr_result.get("error")
    if nvr_result.get("jpeg"):
        result["jpeg"] = nvr_result["jpeg"]

    # --- Direct-camera test ---
    cam = get_direct_camera_info(nvr_id, channel)
    if cam:
        result["direct_ip"] = cam["ip"]
        result["direct_model"] = cam["model"]
        direct_url = build_direct_rtsp_url(cam)
        direct_result = _try_rtsp_capture(direct_url)
        result["direct_success"] = direct_result["success"]
        result["direct_std"] = direct_result.get("std", 0)
        result["direct_bytes"] = direct_result.get("bytes", 0)
        result["direct_resolution"] = direct_result.get("resolution")
        result["direct_error"] = direct_result.get("error")
        if direct_result.get("jpeg") and result["jpeg"] is None:
            result["jpeg"] = direct_result["jpeg"]
    else:
        result["direct_error"] = "no_direct_path"

    return result


# ============================================================================
# PROBE COORDINATOR
# ============================================================================

def _probe_coordinator(probe: dict):
    """Run probe: round-robin channels across attempts, update DB when done."""
    nvr_id = probe["nvr_id"]
    attempts = probe["attempts"]
    channels = probe["channels"]
    stop_event = probe["stop_event"]

    executor = None
    try:
        with probe_lock:
            probe["status"] = "running"

        executor = ProcessPoolExecutor(max_workers=4)

        for attempt_num in range(1, attempts + 1):
            if stop_event.is_set():
                break

            futures = {}
            for ch in channels:
                if stop_event.is_set():
                    break
                f = executor.submit(_probe_channel_subprocess, nvr_id, ch)
                futures[f] = ch

            for f in as_completed(futures):
                ch = futures[f]
                try:
                    result = f.result(timeout=30)
                except Exception as e:
                    result = {
                        "channel": ch,
                        "nvr_success": False, "nvr_error": str(e),
                        "direct_success": False, "direct_error": str(e),
                    }

                ch_key = str(ch)
                with probe_lock:
                    if ch_key not in probe["results"]:
                        probe["results"][ch_key] = {
                            "channel": ch,
                            "attempts": [],
                            "verdict": None,
                            "nvr_verdict": None,
                            "direct_verdict": None,
                            "thumbnail": None,
                            "resolution": None,
                            "direct_ip": None,
                            "direct_model": None,
                        }
                    ch_result = probe["results"][ch_key]
                    ch_result["attempts"].append({
                        "attempt": attempt_num,
                        "nvr_success": result.get("nvr_success", False),
                        "nvr_std": result.get("nvr_std", 0),
                        "nvr_error": result.get("nvr_error"),
                        "direct_success": result.get("direct_success", False),
                        "direct_std": result.get("direct_std", 0),
                        "direct_error": result.get("direct_error"),
                    })
                    # Track direct camera info
                    if result.get("direct_ip"):
                        ch_result["direct_ip"] = result["direct_ip"]
                        ch_result["direct_model"] = result.get("direct_model")
                    # Resolution from whichever succeeded
                    res = result.get("nvr_resolution") or result.get("direct_resolution")
                    if res:
                        ch_result["resolution"] = res
                    # Thumbnail from first success
                    if ch_result["thumbnail"] is None and result.get("jpeg"):
                        ch_result["thumbnail"] = base64.b64encode(result["jpeg"]).decode("utf-8")
                    probe["stats"]["tested"] += 1

            with probe_lock:
                probe["stats"]["attempts_completed"] = attempt_num

        # Compute verdicts
        with probe_lock:
            for ch_key, ch_result in probe["results"].items():
                nvr_ok = sum(1 for a in ch_result["attempts"] if a.get("nvr_success"))
                direct_ok = sum(1 for a in ch_result["attempts"] if a.get("direct_success"))

                ch_result["nvr_verdict"] = "active" if nvr_ok > 0 else "inactive"
                if ch_result["direct_ip"]:
                    ch_result["direct_verdict"] = "active" if direct_ok > 0 else "inactive"

                # Combined verdict: active if either path works
                if nvr_ok > 0 or direct_ok > 0:
                    ch_result["verdict"] = "active"
                    probe["stats"]["active"] += 1
                else:
                    ch_result["verdict"] = "inactive"
                    probe["stats"]["inactive"] += 1

                # Flag mismatches (direct exists but fails, or vice versa)
                if ch_result["direct_ip"] and nvr_ok > 0 and direct_ok == 0:
                    ch_result["mismatch"] = "nvr_only"
                    probe["stats"].setdefault("mismatches", []).append({
                        "channel": ch_result["channel"],
                        "type": "nvr_only",
                        "direct_ip": ch_result["direct_ip"],
                        "direct_error": ch_result["attempts"][-1].get("direct_error", "unknown")
                    })
                elif ch_result["direct_ip"] and direct_ok > 0 and nvr_ok == 0:
                    ch_result["mismatch"] = "direct_only"
                    probe["stats"].setdefault("mismatches", []).append({
                        "channel": ch_result["channel"],
                        "type": "direct_only",
                    })

        # Update lodge.db
        _probe_update_db(probe)

        with probe_lock:
            if probe["status"] != "error":
                probe["status"] = "stopped" if stop_event.is_set() else "completed"

    except Exception as e:
        logger.error(f"Probe coordinator error: {e}")
        with probe_lock:
            probe["status"] = "error"
            probe["stats"]["error"] = str(e)
    finally:
        if executor:
            executor.shutdown(wait=False)
        probe["finished_at"] = _time.time()
        logger.info(f"Probe {probe['probe_id']} {probe['status']}: {probe['stats']}")

        with probe_lock:
            probe_state.pop(probe["nvr_id"], None)
            probe_history.append({
                "probe_id": probe["probe_id"],
                "nvr_id": probe["nvr_id"],
                "status": probe["status"],
                "started_at": probe["started_at"],
                "finished_at": probe["finished_at"],
                "stats": probe["stats"],
                "results": probe["results"]
            })
            if len(probe_history) > PROBE_HISTORY_MAX:
                del probe_history[0]


def _probe_update_db(probe: dict):
    """Update lodge.db channels table with probe results."""
    if not DB_PATH.exists():
        return

    nvr_id = probe["nvr_id"]
    now = _time.strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for ch_key, ch_result in probe["results"].items():
        ch_num = ch_result["channel"]
        verdict = ch_result["verdict"]
        resolution = ch_result["resolution"]
        channel_id = f"{nvr_id}_ch{ch_num:02d}"

        cursor.execute("SELECT id, status FROM channels WHERE id = ?", (channel_id,))
        row = cursor.fetchone()

        new_status = verdict if verdict else "inactive"

        if row:
            old_status = row[1]
            if old_status != new_status:
                probe["stats"].setdefault("status_changes", []).append(
                    {"channel": ch_num, "old": old_status, "new": new_status}
                )
            updates = {"status": new_status, "last_probed": now}
            if resolution:
                updates["resolution"] = resolution
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            cursor.execute(f"UPDATE channels SET {set_clause} WHERE id = ?",
                           list(updates.values()) + [channel_id])
        else:
            nvr = get_nvr_info(nvr_id)
            rtsp_path = build_rtsp_url(nvr, ch_num) if nvr else None
            cursor.execute("""
                INSERT INTO channels (id, nvr_id, channel_number, rtsp_path, status, last_probed, resolution, recording)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (channel_id, nvr_id, ch_num, rtsp_path, new_status, now, resolution))
            probe["stats"].setdefault("new_channels", []).append(ch_num)

    conn.commit()
    conn.close()
    logger.info(f"Probe {probe['probe_id']}: lodge.db updated")

    # Snapshot
    try:
        subprocess.run(
            ["node", "warehouses/snapshot-database.js", "lodge"],
            cwd=str(MODESTO_ROOT),
            capture_output=True, timeout=10
        )
        logger.info(f"Probe {probe['probe_id']}: DB snapshot taken")
    except Exception as e:
        logger.warning(f"Probe {probe['probe_id']}: snapshot failed: {e}")


# ============================================================================
# MODELS
# ============================================================================

class ProbeRequest(BaseModel):
    attempts: int = 3
    max_channels: Optional[int] = None  # None = use NVR max_channels from DB


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/")
def root():
    return {
        "service": "NVR Probe Service",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "probe_ui": "/probe"
    }

@app.get("/api/nvrs")
def list_nvrs():
    """List all NVRs from lodge.db"""
    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail="lodge.db not found")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, brand, model, ip, max_channels, ownership, onvif_supported FROM nvrs")
    rows = cursor.fetchall()
    conn.close()

    return {
        "count": len(rows),
        "nvrs": [dict(row) for row in rows]
    }

@app.get("/api/nvrs/{nvr_id}/channels")
def list_nvr_channels(nvr_id: str):
    """List channels for an NVR from lodge.db"""
    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail="lodge.db not found")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, channel_number, camera_id, status, recording, resolution
        FROM channels WHERE nvr_id = ? ORDER BY channel_number
    """, (nvr_id,))
    rows = cursor.fetchall()
    conn.close()

    return {
        "nvr_id": nvr_id,
        "count": len(rows),
        "channels": [dict(row) for row in rows]
    }


@app.post("/api/nvr/{nvr_id}/probe")
def start_probe(nvr_id: str, request: ProbeRequest):
    """
    Start NVR channel probe. Tests all channels via both NVR-path
    and direct-camera RTSP. Updates lodge.db with results.
    """
    nvr = get_nvr_info(nvr_id)
    if not nvr:
        raise HTTPException(status_code=404, detail=f"NVR '{nvr_id}' not found")

    with probe_lock:
        if nvr_id in probe_state:
            active = probe_state[nvr_id]
            raise HTTPException(status_code=409, detail=f"Probe '{active['probe_id']}' already active for {nvr_id}")

    max_ch = request.max_channels or nvr.get("max_channels") or 16
    channels = list(range(1, max_ch + 1))

    probe_id = str(uuid.uuid4())[:8]

    probe = {
        "probe_id": probe_id,
        "nvr_id": nvr_id,
        "nvr_ip": nvr["ip"],
        "attempts": request.attempts,
        "channels": channels,
        "started_at": _time.time(),
        "finished_at": None,
        "stop_event": threading.Event(),
        "status": "starting",
        "results": {},
        "stats": {
            "tested": 0,
            "total": len(channels) * request.attempts,
            "active": 0,
            "inactive": 0,
            "attempts_completed": 0,
            "error": None
        }
    }

    with probe_lock:
        probe_state[nvr_id] = probe

    coord = threading.Thread(target=_probe_coordinator, args=(probe,), daemon=True)
    coord.start()

    logger.info(f"Probe {probe_id} started: {nvr_id}, {len(channels)} channels, {request.attempts} attempts")

    return {
        "probe_id": probe_id,
        "nvr_id": nvr_id,
        "status": "starting",
        "channels": len(channels),
        "attempts": request.attempts
    }


@app.get("/api/nvr/{nvr_id}/probe/{probe_id}")
def get_probe_status(nvr_id: str, probe_id: str):
    """Get probe progress and per-channel results."""
    now = _time.time()

    with probe_lock:
        active = probe_state.get(nvr_id)
        if active and active["probe_id"] == probe_id:
            elapsed = now - active["started_at"]
            return {
                "probe_id": probe_id,
                "nvr_id": nvr_id,
                "status": active["status"],
                "elapsed_sec": round(elapsed, 1),
                "stats": active["stats"],
                "results": {k: {kk: vv for kk, vv in v.items() if kk != "jpeg"} for k, v in active["results"].items()}
            }

        for entry in reversed(probe_history):
            if entry["probe_id"] == probe_id:
                elapsed = round(entry["finished_at"] - entry["started_at"], 1)
                return {
                    "probe_id": probe_id,
                    "nvr_id": nvr_id,
                    "status": entry["status"],
                    "elapsed_sec": elapsed,
                    "stats": entry["stats"],
                    "results": entry["results"]
                }

    raise HTTPException(status_code=404, detail=f"Probe '{probe_id}' not found")


@app.post("/api/nvr/{nvr_id}/probe/{probe_id}/stop")
def stop_probe(nvr_id: str, probe_id: str):
    """Stop an active probe early."""
    with probe_lock:
        active = probe_state.get(nvr_id)
        if not active or active["probe_id"] != probe_id:
            raise HTTPException(status_code=404, detail=f"No active probe '{probe_id}' for {nvr_id}")
        active["stop_event"].set()
        active["status"] = "stopping"

    return {"probe_id": probe_id, "message": "Stop requested"}


# ============================================================================
# PROBE UI
# ============================================================================

@app.get("/probe", response_class=HTMLResponse)
def probe_page():
    """NVR Channel Probe UI"""
    return '''<!DOCTYPE html>
<html>
<head>
    <title>NVR Channel Probe</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; background: #1a1a2e; color: #eee; padding: 20px; }
        h1 { color: #00d4ff; margin-bottom: 20px; }
        .controls { display: flex; gap: 15px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
        select, button { padding: 8px 16px; border-radius: 4px; border: 1px solid #0f3460; background: #16213e; color: #eee; font-size: 14px; cursor: pointer; }
        button:hover { background: #0f3460; }
        button.start { border-color: #00ff88; color: #00ff88; }
        button.stop { border-color: #ff6b6b; color: #ff6b6b; }
        button:disabled { opacity: 0.4; cursor: not-allowed; }
        .progress { background: #0f3460; border-radius: 4px; height: 24px; margin-bottom: 20px; overflow: hidden; position: relative; }
        .progress-bar { height: 100%; background: linear-gradient(90deg, #00d4ff, #00ff88); transition: width 0.3s; }
        .progress-text { position: absolute; top: 0; left: 0; right: 0; text-align: center; line-height: 24px; font-size: 12px; color: #fff; }
        .stats { display: flex; gap: 20px; margin-bottom: 20px; font-size: 13px; flex-wrap: wrap; }
        .stats span { color: #888; }
        .stats .val { color: #eee; font-weight: bold; }
        .stats .active { color: #00ff88; }
        .stats .inactive { color: #ff6b6b; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
        .card { background: #16213e; border-radius: 8px; overflow: hidden; border: 2px solid transparent; }
        .card.active { border-color: #00ff88; }
        .card.inactive { border-color: #ff6b6b; }
        .card.pending { border-color: #444; }
        .card.testing { border-color: #ffd700; animation: pulse 1s infinite; }
        @keyframes pulse { 50% { border-color: #886600; } }
        .card img { width: 100%; height: 120px; object-fit: cover; background: #0a0a1a; }
        .card .placeholder { width: 100%; height: 120px; background: #0a0a1a; display: flex; align-items: center; justify-content: center; color: #333; font-size: 40px; }
        .card .info { padding: 8px 10px; }
        .card .ch { font-weight: bold; color: #00d4ff; }
        .badge { font-size: 10px; padding: 2px 6px; border-radius: 3px; margin-left: 4px; }
        .badge.active { background: #00ff8833; color: #00ff88; }
        .badge.inactive { background: #ff6b6b33; color: #ff6b6b; }
        .badge.nvr_only { background: #ffa50033; color: #ffa500; }
        .badge.direct_only { background: #ffa50033; color: #ffa500; }
        .card .attempts { font-size: 11px; color: #888; margin-top: 4px; }
        .card .attempts .ok { color: #00ff88; }
        .card .attempts .fail { color: #ff6b6b; }
        .card .meta { font-size: 10px; color: #555; margin-top: 2px; }
        .status-msg { color: #888; font-style: italic; margin: 20px 0; }
        .mismatches { background: #2a1a00; border: 1px solid #ffa500; border-radius: 8px; padding: 15px; margin-bottom: 20px; display: none; }
        .mismatches h3 { color: #ffa500; font-size: 13px; margin-bottom: 8px; }
        .mismatches .item { font-size: 12px; color: #ddd; padding: 3px 0; }
    </style>
</head>
<body>
    <h1>NVR Channel Probe</h1>
    <div class="controls">
        <select id="nvr-select"></select>
        <label style="color:#888; font-size:13px;">Attempts: <input type="number" id="attempts" value="3" min="1" max="10" style="width:50px; padding:6px; background:#16213e; border:1px solid #0f3460; color:#eee; border-radius:4px;"></label>
        <button class="start" id="start-btn" onclick="startProbe()">Start Probe</button>
        <button class="stop" id="stop-btn" onclick="stopProbe()" disabled>Stop</button>
    </div>
    <div class="progress" id="progress-wrap" style="display:none;">
        <div class="progress-bar" id="progress-bar" style="width:0%"></div>
        <div class="progress-text" id="progress-text">0 / 0</div>
    </div>
    <div class="stats" id="stats" style="display:none;">
        <div><span>Status:</span> <span class="val" id="s-status">-</span></div>
        <div><span>Elapsed:</span> <span class="val" id="s-elapsed">-</span></div>
        <div><span>Active:</span> <span class="val active" id="s-active">0</span></div>
        <div><span>Inactive:</span> <span class="val inactive" id="s-inactive">0</span></div>
        <div><span>Attempt:</span> <span class="val" id="s-attempt">-</span></div>
    </div>
    <div class="mismatches" id="mismatches">
        <h3>Path Mismatches</h3>
        <div id="mismatch-list"></div>
    </div>
    <div id="status-msg" class="status-msg">Select an NVR and click Start Probe.</div>
    <div class="grid" id="channel-grid"></div>
    <script>
        let currentProbeId = null;
        let currentNvrId = null;
        let refreshInterval = null;

        async function loadNVRs() {
            const resp = await fetch('/api/nvrs');
            const data = await resp.json();
            const sel = document.getElementById('nvr-select');
            sel.innerHTML = data.nvrs.map(n => `<option value="${n.id}">${n.id} (${n.ip}) &mdash; ${n.brand}</option>`).join('');
        }

        async function startProbe() {
            const nvrId = document.getElementById('nvr-select').value;
            const attempts = parseInt(document.getElementById('attempts').value) || 3;
            document.getElementById('start-btn').disabled = true;

            try {
                const resp = await fetch(`/api/nvr/${nvrId}/probe`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({attempts})
                });
                if (!resp.ok) {
                    const err = await resp.json();
                    alert(err.detail || 'Failed to start probe');
                    document.getElementById('start-btn').disabled = false;
                    return;
                }
                const data = await resp.json();
                currentProbeId = data.probe_id;
                currentNvrId = nvrId;

                document.getElementById('stop-btn').disabled = false;
                document.getElementById('progress-wrap').style.display = '';
                document.getElementById('stats').style.display = '';
                document.getElementById('status-msg').textContent = '';
                document.getElementById('channel-grid').innerHTML = '';
                document.getElementById('mismatches').style.display = 'none';

                const grid = document.getElementById('channel-grid');
                for (let ch = 1; ch <= data.channels; ch++) {
                    grid.innerHTML += `<div class="card pending" id="card-${ch}">
                        <div class="placeholder">ch${ch}</div>
                        <div class="info">
                            <span class="ch">ch${ch}</span>
                            <span id="badges-${ch}"></span>
                            <div class="attempts" id="attempts-${ch}"></div>
                            <div class="meta" id="meta-${ch}"></div>
                        </div>
                    </div>`;
                }

                if (refreshInterval) clearInterval(refreshInterval);
                refreshInterval = setInterval(pollProbe, 1000);
            } catch (e) {
                alert('Error: ' + e.message);
                document.getElementById('start-btn').disabled = false;
            }
        }

        async function stopProbe() {
            if (!currentProbeId || !currentNvrId) return;
            await fetch(`/api/nvr/${currentNvrId}/probe/${currentProbeId}/stop`, {method: 'POST'});
        }

        async function pollProbe() {
            if (!currentProbeId || !currentNvrId) return;
            try {
                const resp = await fetch(`/api/nvr/${currentNvrId}/probe/${currentProbeId}`);
                if (!resp.ok) return;
                const data = await resp.json();

                const stats = data.stats;
                const pct = stats.total > 0 ? Math.round(stats.tested / stats.total * 100) : 0;
                document.getElementById('progress-bar').style.width = pct + '%';
                document.getElementById('progress-text').textContent = `${stats.tested} / ${stats.total}`;
                document.getElementById('s-status').textContent = data.status;
                document.getElementById('s-elapsed').textContent = data.elapsed_sec + 's';
                document.getElementById('s-active').textContent = stats.active;
                document.getElementById('s-inactive').textContent = stats.inactive;
                document.getElementById('s-attempt').textContent = `${stats.attempts_completed || 0}`;

                for (const [chKey, r] of Object.entries(data.results)) {
                    const ch = r.channel;
                    const card = document.getElementById(`card-${ch}`);
                    if (!card) continue;

                    card.className = 'card ' + (r.verdict || 'testing');

                    // Badges: NVR verdict + Direct verdict
                    const badgesEl = document.getElementById(`badges-${ch}`);
                    let badges = '';
                    if (r.nvr_verdict) {
                        badges += `<span class="badge ${r.nvr_verdict}">nvr:${r.nvr_verdict}</span>`;
                    }
                    if (r.direct_verdict) {
                        badges += `<span class="badge ${r.direct_verdict}">direct:${r.direct_verdict}</span>`;
                    }
                    if (r.mismatch) {
                        badges += `<span class="badge ${r.mismatch}">${r.mismatch}</span>`;
                    }
                    badgesEl.innerHTML = badges;

                    // Thumbnail
                    if (r.thumbnail) {
                        const existing = card.querySelector('img');
                        if (!existing) {
                            const placeholder = card.querySelector('.placeholder');
                            if (placeholder) {
                                const img = document.createElement('img');
                                img.src = 'data:image/jpeg;base64,' + r.thumbnail;
                                placeholder.replaceWith(img);
                            }
                        }
                    }

                    // Attempts
                    const aEl = document.getElementById(`attempts-${ch}`);
                    aEl.innerHTML = r.attempts.map(a => {
                        const nok = a.nvr_success ? `<span class="ok">N:${a.nvr_std}</span>` : `<span class="fail">N:x</span>`;
                        const dok = a.direct_success ? `<span class="ok">D:${a.direct_std}</span>` : (a.direct_error === 'no_direct_path' ? '' : `<span class="fail">D:x</span>`);
                        return `#${a.attempt}: ${nok} ${dok}`;
                    }).join(' &nbsp; ');

                    // Meta: direct IP, resolution
                    const metaEl = document.getElementById(`meta-${ch}`);
                    let metaParts = [];
                    if (r.resolution) metaParts.push(r.resolution);
                    if (r.direct_ip) metaParts.push(r.direct_ip);
                    if (r.direct_model) metaParts.push(r.direct_model);
                    metaEl.textContent = metaParts.join(' | ');
                }

                // Mismatches panel
                if (stats.mismatches && stats.mismatches.length > 0) {
                    document.getElementById('mismatches').style.display = '';
                    document.getElementById('mismatch-list').innerHTML = stats.mismatches.map(m =>
                        `<div class="item">ch${m.channel}: <b>${m.type}</b>${m.direct_ip ? ' (' + m.direct_ip + ')' : ''} ${m.direct_error ? '&mdash; ' + m.direct_error : ''}</div>`
                    ).join('');
                }

                if (['completed', 'stopped', 'error'].includes(data.status)) {
                    clearInterval(refreshInterval);
                    refreshInterval = null;
                    document.getElementById('start-btn').disabled = false;
                    document.getElementById('stop-btn').disabled = true;
                    const mm = stats.mismatches ? stats.mismatches.length : 0;
                    document.getElementById('status-msg').textContent =
                        `Probe ${data.status}. ${stats.active} active, ${stats.inactive} inactive.${mm ? ' ' + mm + ' path mismatch(es)!' : ''} DB updated.`;
                }
            } catch (e) {
                console.error('Poll error:', e);
            }
        }

        loadNVRs();
    </script>
</body>
</html>'''


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  NVR Probe Service")
    print("=" * 60)
    print("  HTTP:     http://localhost:7999")
    print("  Docs:     http://localhost:7999/docs")
    print("  Probe UI: http://localhost:7999/probe")
    print("=" * 60)
    print()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=7999,
        log_level="info"
    )
