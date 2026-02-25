#!/usr/bin/env python3
"""
NVR Probe Service
Standalone NVR channel probing and infrastructure verification.

Port: 7999
Tests both NVR-path and direct-camera RTSP for each channel.
Optionally updates lodge.db with results (dry_run mode skips DB writes).

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
import re
import time as _time
from pathlib import Path
from typing import Optional, List
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

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
PROBE_HTML = Path(__file__).parent / "probe.html"

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

_CRED_GROUPS_PATH = MODESTO_ROOT / "warehouses" / "lodge" / "cam-cred-groups.json"
_raw = json.loads(_CRED_GROUPS_PATH.read_text())
CAM_CRED_GROUPS = {k: v for k, v in _raw.items() if not k.startswith("_")}

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
    return dict(row) if row else None

def get_nvr_credentials(nvr_id: str) -> tuple:
    """Resolve NVR credentials from .env. E.g. nvr1 → NVR1_USER/NVR1_PASS."""
    prefix = nvr_id.upper()
    return (os.environ.get(f"{prefix}_USER", "admin"),
            os.environ.get(f"{prefix}_PASS", ""))

def build_rtsp_url(nvr: dict, channel: int) -> str:
    """Build RTSP URL from NVR info and channel number. Creds from .env."""
    ip = nvr['ip']
    username, password = get_nvr_credentials(nvr['id'])
    path_format = nvr['path_format']

    if '{channel:02d}' in path_format:
        path = path_format.replace('{channel:02d}', f'{channel:02d}')
    else:
        path = path_format.replace('{channel}', str(channel))

    password_encoded = quote(password, safe='')
    return f"rtsp://{username}:{password_encoded}@{ip}:554/{path}"

def get_channel_camera_info(nvr_id: str, channel: int) -> Optional[dict]:
    """Get camera IP, rtsp_path, model for a channel. Returns None if no camera linked."""
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
          AND cam.ip IS NOT NULL
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
    version="2.0.0"
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
# PROBE CAPTURE — ffmpeg subprocess for RTSP frame grab
# ============================================================================

def _try_rtsp_capture(rtsp_url: str, timeout: int = 10) -> dict:
    """
    Attempt RTSP frame capture via ffmpeg subprocess.
    Returns {success, bytes, resolution, jpeg, error}.
    """
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner",
                "-rtsp_transport", "tcp",
                "-i", rtsp_url,
                "-frames:v", "1", "-q:v", "2",
                "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"
            ],
            capture_output=True,
            timeout=timeout
        )

        jpeg_data = proc.stdout
        if proc.returncode != 0 or len(jpeg_data) < 1000:
            stderr_tail = proc.stderr.decode('utf-8', errors='replace')[-300:] if proc.stderr else "no_output"
            return {"success": False, "bytes": 0, "resolution": None, "error": stderr_tail}

        # Parse resolution from ffmpeg stderr
        resolution = None
        stderr_text = proc.stderr.decode('utf-8', errors='replace')
        m = re.search(r'(\d{3,5})x(\d{3,5})', stderr_text)
        if m:
            resolution = f"{m.group(1)}x{m.group(2)}"

        return {
            "success": True,
            "bytes": len(jpeg_data),
            "resolution": resolution,
            "jpeg": jpeg_data
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "bytes": 0, "resolution": None, "error": "timeout"}
    except Exception as e:
        logger.warning(f"RTSP capture error for {rtsp_url[:60]}...: {e}")
        return {"success": False, "bytes": 0, "resolution": None, "error": str(e)}


def _probe_channel(nvr_id: str, channel: int) -> dict:
    """
    Probe a single channel. Tests both NVR-path and direct-camera RTSP.
    Runs in a thread; ffmpeg subprocess handles the actual RTSP work.
    """
    result = {
        "channel": channel,
        "nvr_success": False, "nvr_bytes": 0,
        "nvr_resolution": None, "nvr_error": None,
        "direct_success": False, "direct_bytes": 0,
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
    result["nvr_bytes"] = nvr_result.get("bytes", 0)
    result["nvr_resolution"] = nvr_result.get("resolution")
    result["nvr_error"] = nvr_result.get("error")
    if nvr_result.get("jpeg"):
        result["jpeg"] = nvr_result["jpeg"]

    # --- Direct-camera test ---
    cam = get_channel_camera_info(nvr_id, channel)
    if cam:
        result["direct_ip"] = cam["ip"]
        result["direct_model"] = cam["model"]
        if cam.get("rtsp_path"):
            direct_url = build_direct_rtsp_url(cam)
            direct_result = _try_rtsp_capture(direct_url)
            result["direct_success"] = direct_result["success"]
            result["direct_bytes"] = direct_result.get("bytes", 0)
            result["direct_resolution"] = direct_result.get("resolution")
            result["direct_error"] = direct_result.get("error")
            if direct_result.get("jpeg") and result["jpeg"] is None:
                result["jpeg"] = direct_result["jpeg"]
        else:
            result["direct_error"] = "no_rtsp_path"
    else:
        result["direct_error"] = "no_camera_linked"

    return result


# ============================================================================
# PROBE COORDINATOR
# ============================================================================

def _probe_coordinator(probe: dict):
    """Run probe: round-robin channels across attempts, optionally update DB."""
    nvr_id = probe["nvr_id"]
    attempts = probe["attempts"]
    channels = probe["channels"]
    stop_event = probe["stop_event"]
    dry_run = probe["dry_run"]

    executor = None
    try:
        with probe_lock:
            probe["status"] = "running"

        executor = ThreadPoolExecutor(max_workers=4)

        for attempt_num in range(1, attempts + 1):
            if stop_event.is_set():
                break

            futures = {}
            for ch in channels:
                if stop_event.is_set():
                    break
                f = executor.submit(_probe_channel, nvr_id, ch)
                futures[f] = ch

            for f in as_completed(futures):
                ch = futures[f]
                try:
                    result = f.result(timeout=30)
                except Exception as e:
                    logger.warning(f"Probe channel {ch} error: {e}")
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
                        "nvr_bytes": result.get("nvr_bytes", 0),
                        "nvr_error": result.get("nvr_error"),
                        "direct_success": result.get("direct_success", False),
                        "direct_bytes": result.get("direct_bytes", 0),
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

        # Compute DB diff (always) and apply changes (unless dry_run)
        _probe_update_db(probe, dry_run=dry_run)

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
                "dry_run": probe["dry_run"],
                "status": probe["status"],
                "started_at": probe["started_at"],
                "finished_at": probe["finished_at"],
                "stats": probe["stats"],
                "results": probe["results"]
            })
            if len(probe_history) > PROBE_HISTORY_MAX:
                del probe_history[0]


def _probe_update_db(probe: dict, dry_run: bool = False):
    """
    Compute DB diff from probe results. Always populates stats with
    status_changes and new_channels. Only writes to lodge.db if dry_run=False.
    """
    if not DB_PATH.exists():
        return

    nvr_id = probe["nvr_id"]
    now = _time.strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    touched = 0
    for ch_key, ch_result in probe["results"].items():
        ch_num = ch_result["channel"]
        verdict = ch_result["verdict"]
        resolution = ch_result["resolution"]
        channel_id = f"{nvr_id}_ch{ch_num:02d}"

        cursor.execute("SELECT id, status FROM channels WHERE id = ?", (channel_id,))
        row = cursor.fetchone()

        new_status = verdict if verdict else "inactive"

        if row:
            touched += 1
            old_status = row[1]
            if old_status != new_status:
                probe["stats"].setdefault("status_changes", []).append(
                    {"channel": ch_num, "old": old_status, "new": new_status}
                )
            if not dry_run:
                updates = {"status": new_status, "last_probed": now}
                if resolution:
                    updates["resolution"] = resolution
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                cursor.execute(f"UPDATE channels SET {set_clause} WHERE id = ?",
                               list(updates.values()) + [channel_id])
        else:
            probe["stats"].setdefault("new_channels", []).append(ch_num)
            if not dry_run:
                nvr = get_nvr_info(nvr_id)
                rtsp_path = build_rtsp_url(nvr, ch_num) if nvr else None
                cursor.execute("""
                    INSERT INTO channels (id, nvr_id, channel_number, rtsp_path, status, last_probed, resolution, recording)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """, (channel_id, nvr_id, ch_num, rtsp_path, new_status, now, resolution))

    probe["stats"]["touched"] = touched

    changes = probe["stats"].get("status_changes", [])
    new_chs = probe["stats"].get("new_channels", [])

    if not dry_run:
        conn.commit()
        conn.close()
        logger.info(f"Probe {probe['probe_id']}: lodge.db updated — {touched} touched, {len(changes)} status change(s), {len(new_chs)} new channel(s)")

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
    else:
        conn.close()
        logger.info(f"Probe {probe['probe_id']}: dry run — {touched} would be touched, {len(changes)} status change(s), {len(new_chs)} new channel(s)")


# ============================================================================
# MODELS
# ============================================================================

class ProbeRequest(BaseModel):
    attempts: int = 3
    max_channels: Optional[int] = None  # None = use NVR max_channels from DB
    dry_run: bool = False


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/")
def root():
    return {
        "service": "NVR Probe Service",
        "version": "2.0.0",
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
    and direct-camera RTSP. Updates lodge.db with results unless dry_run=True.
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
        "dry_run": request.dry_run,
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

    logger.info(f"Probe {probe_id} started: {nvr_id}, {len(channels)} channels, {request.attempts} attempts, dry_run={request.dry_run}")

    return {
        "probe_id": probe_id,
        "nvr_id": nvr_id,
        "status": "starting",
        "channels": len(channels),
        "attempts": request.attempts,
        "dry_run": request.dry_run
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
                "dry_run": active["dry_run"],
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
                    "dry_run": entry.get("dry_run", False),
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
    if PROBE_HTML.exists():
        return PROBE_HTML.read_text(encoding="utf-8")
    return "<h1>probe.html not found</h1>"


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  NVR Probe Service v2.0.0")
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
