#!/usr/bin/env python3
"""
Camera Capture Service
FastAPI server for warehouse camera access and image delivery

Port: 8001
Provides camera images to agents, web apps, and other services
"""

import sys
import os
import time
import logging
import sqlite3
import threading
import json
from pathlib import Path
from typing import Optional, List
from urllib.parse import quote
import base64
import io

import cv2
import numpy as np
import zmq
import requests
from requests.auth import HTTPDigestAuth
from fastapi import FastAPI, HTTPException, Response, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from capture import CameraCapture
from cache import ImageCache
from scanner import NVRScanner
from playback import capture_playback_frame
from mediator import (
    FrameMediator, StreamManager, resolve_camera_key, clear_key_cache,
)
from http_mediator import HttpCameraMediator
from m5camserver_client import M5CamServerProbe
from wedge_detector import detect_wedge
from dataclasses import asdict as _asdict

# ============================================================================
# CAMERA CREDENTIALS (.env)
# ============================================================================

ENV_PATH = Path(__file__).parent.parent.parent / ".env"
if ENV_PATH.exists():
    for _line in ENV_PATH.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

_CRED_GROUPS_PATH = Path(__file__).parent.parent.parent / "warehouses" / "lodge" / "cam-cred-groups.json"
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

# Configure logging - console + file
LOG_FILE = Path(__file__).parent / "camera-service.log"
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
# FASTAPI APPLICATION
# ============================================================================

app = FastAPI(
    title="Camera Capture Service",
    description="Warehouse camera access and image delivery API",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# GLOBAL STATE
# ============================================================================

# Initialize camera capture and cache
camera_capture = CameraCapture(warehouses_path="../warehouses")
image_cache = ImageCache(default_ttl=30)  # 30 second cache

# M5CamServer /version probe with TTL cache. Used to fingerprint flashed
# cams (sketch_md5, uptime, camera_ok, free_heap). Cached aggressively
# because probing stock firmware burns W5500 socket-pool cycles.
m5_probe = M5CamServerProbe()

# HTTP camera proxy (ESP32/PoE-CAM). Coalesces per-IP concurrent requests
# onto a single upstream connection since these devices lock up otherwise.
# Hooks m5_probe.version into the worker startup so the /version cache gets
# warmed during the only window where the W5500 single-client slot is free
# (right before the stream connect).
http_mediator = HttpCameraMediator(version_probe=m5_probe.version)

# ============================================================================
# MODELS
# ============================================================================

class CameraInfo(BaseModel):
    id: str
    name: str
    number: int
    location: str
    resolution: str
    channel: int

class BatchCaptureRequest(BaseModel):
    camera_ids: List[str]
    use_cache: bool = True

class HealthStatus(BaseModel):
    status: str
    service: str
    cache_stats: dict
    nvr_connectivity: Optional[dict] = None

class ScanRequest(BaseModel):
    nvr_ip: str
    username: str = "admin"
    password: str = ""
    port: int = 554
    max_channels: int = 32
    quick: bool = True

class ChannelInfo(BaseModel):
    path: str
    channel: Optional[int] = None
    width: int
    height: int
    resolution: str
    url: str

class ScanResponse(BaseModel):
    nvr_ip: str
    channels_found: int
    channels: List[ChannelInfo]

class CameraConfigUpdate(BaseModel):
    """Request model for updating camera configuration"""
    name: Optional[str] = None  # New food name (e.g., "Donut")
    location: Optional[str] = None  # Position description

class TagScanCamera(BaseModel):
    nvr: str
    channel: int

class TagScanRequest(BaseModel):
    cameras: List[TagScanCamera]
    timeout: int = 30
    push_to: str = "tcp://127.0.0.1:5557"
    scan_id: Optional[str] = None  # Client-provided ID; server generates if omitted
    encoding: str = "jpeg"  # "jpeg" or "png" (lossless, no compression artifacts)

# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/")
def root():
    """Root endpoint"""
    return {
        "service": "Camera Capture Service",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs"
    }

@app.get("/api/health", response_model=HealthStatus)
def health_check(facility: str = Query("lodge", description="Facility to check")):
    """
    Health check endpoint

    Args:
        facility: Facility name to check NVR connectivity
    """
    try:
        nvr_status = camera_capture.check_nvr_connectivity(facility)
    except Exception as e:
        logger.error(f"Error checking NVR: {e}")
        nvr_status = {"error": str(e)}

    return {
        "status": "healthy",
        "service": "camera-capture",
        "cache_stats": image_cache.stats(),
        "nvr_connectivity": nvr_status
    }

@app.get("/api/monitor")
def get_monitor_status():
    """
    Get worker thread status for monitoring.
    Shows active requests, completed counts, and current activity.
    """
    import time
    now = time.time()

    with worker_state_lock:
        state = {
            "timestamp": now,
            "zmq_rep": {
                **worker_state["zmq_rep"],
                "current": None
            },
            "zmq_router": {
                "status": worker_state["zmq_router"]["status"],
                "pending_count": len(worker_state["zmq_router"]["pending_requests"]),
                "active_count": len(worker_state["zmq_router"]["active_requests"]),
                "pending_requests": [],
                "active_requests": [],
                "completed": worker_state["zmq_router"]["completed"],
                "errors": worker_state["zmq_router"]["errors"],
                "total_bytes_mb": round(worker_state["zmq_router"]["total_bytes"] / (1024*1024), 2)
            }
        }

        # Add elapsed time to current REP request
        if worker_state["zmq_rep"]["current"]:
            current = worker_state["zmq_rep"]["current"].copy()
            current["elapsed_sec"] = round(now - current["started_at"], 1)
            del current["started_at"]
            state["zmq_rep"]["current"] = current

        # Add pending ROUTER requests (waiting for worker)
        for req_id, req in worker_state["zmq_router"]["pending_requests"].items():
            state["zmq_router"]["pending_requests"].append({
                "id": req_id,
                "nvr": req["nvr"],
                "channel": req["channel"],
                "source": req["source"],
                "waiting_sec": round(now - req["queued_at"], 1)
            })

        # Add active ROUTER requests (being processed)
        for req_id, req in worker_state["zmq_router"]["active_requests"].items():
            state["zmq_router"]["active_requests"].append({
                "id": req_id,
                "nvr": req["nvr"],
                "channel": req["channel"],
                "source": req["source"],
                "elapsed_sec": round(now - req.get("processing_started_at", req["queued_at"]), 1)
            })

        # Add per-NVR connection counts
        nvr_connections = {}
        for req in list(worker_state["zmq_router"]["active_requests"].values()):
            nvr_id = req["nvr"]
            nvr_connections[nvr_id] = nvr_connections.get(nvr_id, 0) + 1
        state["zmq_router"]["nvr_connections"] = nvr_connections
        state["zmq_router"]["nvr_max_concurrent"] = NVR_MAX_CONCURRENT

        # Include event log (last N events)
        state["zmq_router"]["event_log"] = list(worker_state["zmq_router"]["event_log"])

    # NVR gate status (covers all subsystems)
    state["nvr_gate"] = nvr_gate.status()

    # Mediator + streams status
    if frame_mediator:
        state["mediator"] = frame_mediator.status()

    # Tag scan status (separate lock)
    with tag_scan_lock:
        active = tag_scan_state["active_scan"]
        if active:
            elapsed = now - active["started_at"]
            state["tag_scan"] = {
                "active": True,
                "scan_id": active["scan_id"],
                "status": active["status"],
                "cameras": len(active["cameras"]),
                "elapsed_sec": round(elapsed, 1),
                "remaining_sec": round(max(0, active["timeout"] - elapsed), 1),
                "stats": active["stats"]
            }
        else:
            if tag_scan_state["history"]:
                last = tag_scan_state["history"][-1]
                elapsed = round(last["finished_at"] - last["started_at"], 1)
                state["tag_scan"] = {
                    "active": False,
                    "scan_id": last["scan_id"],
                    "status": last["status"],
                    "elapsed_sec": elapsed,
                    "remaining_sec": 0,
                    "stats": last["stats"]
                }
            else:
                state["tag_scan"] = {"active": False}

    return state

@app.post("/api/monitor/reset")
def reset_monitor_stats(clear_log: bool = Query(False, description="Also clear event log")):
    """Reset monitor statistics (completed counts, errors, bytes). Does not clear in-flight requests."""
    with worker_state_lock:
        worker_state["zmq_rep"]["completed"] = 0
        worker_state["zmq_rep"]["errors"] = 0
        worker_state["zmq_router"]["completed"] = 0
        worker_state["zmq_router"]["errors"] = 0
        worker_state["zmq_router"]["total_bytes"] = 0
        if clear_log:
            worker_state["zmq_router"]["event_log"] = []
    return {"message": "Stats reset" + (" (log cleared)" if clear_log else "")}

@app.get("/api/cameras/{facility}")
def list_cameras(facility: str):
    """
    List all cameras for a facility

    Args:
        facility: Facility name (e.g., "lodge")
    """
    try:
        cameras = camera_capture.list_cameras(facility)
        return {
            "facility": facility,
            "count": len(cameras),
            "cameras": cameras
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error listing cameras: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cameras/{facility}/unconfigured")
def list_unconfigured_cameras(facility: str):
    """
    List only unconfigured cameras for a facility.
    Unconfigured = modelTCameraId starts with 'camera_' or location contains 'needs configuration'

    Args:
        facility: Facility name (e.g., "lodge")
    """
    try:
        cameras = camera_capture.list_cameras(facility)
        unconfigured = [
            c for c in cameras
            if c['id'].startswith('camera_') or 'needs configuration' in c['location'].lower()
        ]
        return {
            "facility": facility,
            "total_cameras": len(cameras),
            "unconfigured_count": len(unconfigured),
            "cameras": unconfigured
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error listing unconfigured cameras: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cameras/{facility}/thumbnails")
def get_camera_thumbnails(
    facility: str,
    width: int = Query(320, description="Thumbnail width in pixels"),
    unconfigured_only: bool = Query(False, description="Only include unconfigured cameras"),
    use_cache: bool = Query(True, description="Use cached images if available")
):
    """
    Get thumbnails for all cameras (or unconfigured only).
    Returns base64-encoded resized images for efficient UI display.

    Args:
        facility: Facility name
        width: Target thumbnail width (height calculated to maintain aspect ratio)
        unconfigured_only: If True, only return unconfigured cameras
        use_cache: Use cached full-size images before capturing fresh
    """
    try:
        cameras = camera_capture.list_cameras(facility)

        # Filter if unconfigured_only
        if unconfigured_only:
            cameras = [
                c for c in cameras
                if c['id'].startswith('camera_') or 'needs configuration' in c['location'].lower()
            ]

        results = {}
        for cam in cameras:
            camera_id = cam['id']
            cache_key = f"{facility}/{camera_id}"

            # Get image data (from cache or fresh capture)
            image_data = None
            if use_cache:
                image_data = image_cache.get(cache_key)

            if not image_data:
                image_data = camera_capture.capture_camera(facility, camera_id)
                if image_data:
                    image_cache.set(cache_key, image_data)

            if image_data:
                # Resize to thumbnail
                nparr = np.frombuffer(image_data, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                if img is not None:
                    # Calculate height maintaining aspect ratio
                    h, w = img.shape[:2]
                    new_height = int(h * (width / w))
                    thumbnail = cv2.resize(img, (width, new_height), interpolation=cv2.INTER_AREA)

                    # Encode back to JPEG
                    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
                    _, buffer = cv2.imencode('.jpg', thumbnail, encode_param)

                    results[camera_id] = {
                        "success": True,
                        "name": cam['name'],
                        "channel": cam['channel'],
                        "location": cam['location'],
                        "original_resolution": cam['resolution'],
                        "thumbnail_size": f"{width}x{new_height}",
                        "image": base64.b64encode(buffer.tobytes()).decode('utf-8')
                    }
                else:
                    results[camera_id] = {"success": False, "error": "Failed to decode image"}
            else:
                results[camera_id] = {"success": False, "error": "Failed to capture"}

        successful = sum(1 for r in results.values() if r.get('success'))

        return {
            "facility": facility,
            "thumbnail_width": width,
            "total": len(results),
            "successful": successful,
            "failed": len(results) - successful,
            "results": results
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting thumbnails: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/warehouses/{warehouse_id}/camera-thumbnails")
def get_warehouse_camera_thumbnails(
    warehouse_id: str,
    width: int = Query(320, description="Thumbnail width in pixels")
):
    """
    Get thumbnails for all cameras across all NVRs in a warehouse.
    Format designed for Babylon viewer consumption.

    Args:
        warehouse_id: Warehouse/facility ID (e.g., "lodge")
        width: Target thumbnail width

    Returns:
        {nvrs: [{id, name, channels}], cameras: [{nvrId, channel, thumbnailUrl, label, image}]}
    """
    try:
        nvrs = camera_capture.list_nvrs(warehouse_id)
        cameras = camera_capture.list_cameras(warehouse_id)

        # Build NVR list with their channels
        nvrs_list = []
        for nvr in nvrs:
            nvr_channels = [c['channel'] for c in cameras if c.get('nvr_id') == nvr['id']]
            nvrs_list.append({
                'id': nvr['id'],
                'name': nvr['ip'],
                'channels': sorted(set(nvr_channels))
            })

        # Build camera list with thumbnails
        cameras_list = []
        for cam in cameras:
            camera_id = cam['id']
            nvr_id = cam.get('nvr_id', 'nvr1')
            cache_key = f"{warehouse_id}/{camera_id}"

            # Try to get/capture image
            image_data = image_cache.get(cache_key)
            if not image_data:
                image_data = camera_capture.capture_camera(warehouse_id, camera_id)
                if image_data:
                    image_cache.set(cache_key, image_data)

            thumbnail_b64 = None
            if image_data:
                # Resize to thumbnail
                nparr = np.frombuffer(image_data, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    h, w = img.shape[:2]
                    new_height = int(h * (width / w))
                    thumbnail = cv2.resize(img, (width, new_height), interpolation=cv2.INTER_AREA)
                    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
                    _, buffer = cv2.imencode('.jpg', thumbnail, encode_param)
                    thumbnail_b64 = base64.b64encode(buffer.tobytes()).decode('utf-8')

            cameras_list.append({
                'nvrId': nvr_id,
                'channel': cam['channel'],
                'cameraId': camera_id,
                'thumbnailUrl': f"/api/cameras/{warehouse_id}/{camera_id}/capture",
                'label': cam['name'],
                'location': cam.get('location', ''),
                'image': thumbnail_b64
            })

        return {
            'warehouseId': warehouse_id,
            'nvrs': nvrs_list,
            'cameras': cameras_list,
            'total': len(cameras_list)
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting warehouse thumbnails: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cameras/{facility}/{camera_id}/info")
def get_camera_info(facility: str, camera_id: str):
    """
    Get camera information

    Args:
        facility: Facility name
        camera_id: ModelT camera ID (e.g., "bagel")
    """
    try:
        info = camera_capture.get_camera_info(facility, camera_id)
        if not info:
            raise HTTPException(
                status_code=404,
                detail=f"Camera '{camera_id}' not found in facility '{facility}'"
            )
        return info
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting camera info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cameras/{facility}/{camera_id}/latest")
def get_latest_frame(
    facility: str,
    camera_id: str,
    format: str = Query("image", description="Response format: 'image' or 'base64'")
):
    """
    Get latest cached frame (fast, no NVR hit if cached)

    Args:
        facility: Facility name
        camera_id: ModelT camera ID
        format: 'image' returns JPEG, 'base64' returns JSON with base64 string
    """
    cache_key = f"{facility}/{camera_id}"

    # Try cache first
    image_data = image_cache.get(cache_key)

    if not image_data:
        # Cache miss - capture new frame
        logger.info(f"Cache miss for {cache_key}, capturing fresh frame")
        image_data = camera_capture.capture_camera(facility, camera_id)

        if not image_data:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to capture from camera '{camera_id}'"
            )

        # Cache it
        image_cache.set(cache_key, image_data)

    if format == "base64":
        return {
            "facility": facility,
            "camera_id": camera_id,
            "image": base64.b64encode(image_data).decode('utf-8'),
            "format": "jpeg"
        }
    else:
        return Response(content=image_data, media_type="image/jpeg")

@app.get("/api/cameras/{facility}/{camera_id}/capture")
def capture_live_frame(
    facility: str,
    camera_id: str,
    format: str = Query("image", description="Response format: 'image' or 'base64'"),
    refresh_cache: bool = Query(True, description="Update cache with new frame")
):
    """
    Capture live frame from camera (always hits NVR)

    Args:
        facility: Facility name
        camera_id: ModelT camera ID
        format: 'image' returns JPEG, 'base64' returns JSON with base64 string
        refresh_cache: Whether to update cache with new frame
    """
    image_data = camera_capture.capture_camera(facility, camera_id)

    if not image_data:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to capture from camera '{camera_id}'"
        )

    # Update cache if requested
    if refresh_cache:
        cache_key = f"{facility}/{camera_id}"
        image_cache.set(cache_key, image_data)

    if format == "base64":
        return {
            "facility": facility,
            "camera_id": camera_id,
            "image": base64.b64encode(image_data).decode('utf-8'),
            "format": "jpeg"
        }
    else:
        return Response(content=image_data, media_type="image/jpeg")

@app.patch("/api/cameras/{facility}/{channel}/config")
def update_camera_config(facility: str, channel: int, update: CameraConfigUpdate):
    """
    Update camera configuration (name and/or location).
    Used by configuration UI when user assigns a name to a camera position.

    Args:
        facility: Facility name (e.g., "lodge")
        channel: NVR channel number (1-32)
        update: Name and/or location to update

    Note: Designed to support multiple NVRs per facility in future.
    """
    if not update.name and not update.location:
        raise HTTPException(
            status_code=400,
            detail="At least one of 'name' or 'location' must be provided"
        )

    try:
        result = camera_capture.update_camera_config(
            facility=facility,
            channel=channel,
            name=update.name,
            location=update.location
        )

        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"Channel {channel} not found in facility '{facility}'"
            )

        return {
            "facility": facility,
            "channel": channel,
            "updated": result,
            "message": f"Camera config updated for channel {channel}"
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating camera config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/cameras/{facility}/batch")
def capture_batch(facility: str, request: BatchCaptureRequest):
    """
    Capture frames from multiple cameras

    Args:
        facility: Facility name
        request: Batch capture request with camera IDs
    """
    results = {}

    for camera_id in request.camera_ids:
        cache_key = f"{facility}/{camera_id}"

        # Use cache if requested
        if request.use_cache:
            image_data = image_cache.get(cache_key)
            if image_data:
                results[camera_id] = {
                    "success": True,
                    "cached": True,
                    "image": base64.b64encode(image_data).decode('utf-8')
                }
                continue

        # Capture fresh
        image_data = camera_capture.capture_camera(facility, camera_id)

        if image_data:
            # Cache it
            image_cache.set(cache_key, image_data)
            results[camera_id] = {
                "success": True,
                "cached": False,
                "image": base64.b64encode(image_data).decode('utf-8')
            }
        else:
            results[camera_id] = {
                "success": False,
                "error": "Failed to capture"
            }

    return {
        "facility": facility,
        "requested": len(request.camera_ids),
        "successful": sum(1 for r in results.values() if r.get('success')),
        "results": results
    }

@app.post("/api/cameras/{facility}/capture-all")
def capture_all_cameras(facility: str):
    """
    Capture frames from all cameras in facility

    Args:
        facility: Facility name
    """
    try:
        all_frames = camera_capture.capture_all(facility)

        results = {}
        for camera_id, image_data in all_frames.items():
            if image_data:
                # Cache it
                cache_key = f"{facility}/{camera_id}"
                image_cache.set(cache_key, image_data)

                results[camera_id] = {
                    "success": True,
                    "image": base64.b64encode(image_data).decode('utf-8')
                }
            else:
                results[camera_id] = {
                    "success": False,
                    "error": "Failed to capture"
                }

        successful = sum(1 for r in results.values() if r.get('success'))

        return {
            "facility": facility,
            "total": len(all_frames),
            "successful": successful,
            "failed": len(all_frames) - successful,
            "results": results
        }

    except Exception as e:
        logger.error(f"Error capturing all cameras: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/scan", response_model=ScanResponse)
def scan_nvr(request: ScanRequest):
    """
    Scan NVR for available camera channels

    Args:
        request: Scan request with NVR connection details

    Returns:
        List of discovered channels with resolutions and RTSP URLs
    """
    try:
        scanner = NVRScanner(
            nvr_ip=request.nvr_ip,
            username=request.username,
            password=request.password,
            port=request.port
        )

        if request.quick:
            # Quick scan using common pattern (ch01/0, ch02/0, etc.)
            channels = scanner.quick_scan(
                channels_to_test=list(range(1, request.max_channels + 1))
            )
        else:
            # Full scan testing all patterns
            channels = scanner.scan(max_channels=request.max_channels)

        return {
            "nvr_ip": request.nvr_ip,
            "channels_found": len(channels),
            "channels": channels
        }

    except Exception as e:
        logger.error(f"Error scanning NVR: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/cache/{facility}/{camera_id}")
def invalidate_cache(facility: str, camera_id: str):
    """
    Invalidate cached image for specific camera

    Args:
        facility: Facility name
        camera_id: ModelT camera ID
    """
    cache_key = f"{facility}/{camera_id}"
    removed = image_cache.invalidate(cache_key)

    return {
        "facility": facility,
        "camera_id": camera_id,
        "cache_key": cache_key,
        "removed": removed
    }

@app.delete("/api/cache")
def clear_cache():
    """Clear entire image cache"""
    image_cache.clear()
    return {
        "message": "Cache cleared",
        "stats": image_cache.stats()
    }

@app.post("/api/cameras/{facility}/generate-thumbnails")
def generate_thumbnails(
    facility: str,
    width: int = Query(320, description="Thumbnail width")
):
    """
    Capture thumbnails from all working cameras and save to disk.
    Saves to warehouses/{facility}/cameras/thumbnails/
    """
    try:
        cameras = camera_capture.list_cameras(facility)
        thumbnails_dir = camera_capture.warehouses_path / facility / "cameras" / "thumbnails"
        thumbnails_dir.mkdir(parents=True, exist_ok=True)

        results = {"success": [], "failed": []}

        for cam in cameras:
            cid = cam['id']
            logger.info(f"Generating thumbnail for {cid}...")

            image_data = camera_capture.capture_frame(cam['rtsp_url'], timeout=5)
            if image_data:
                # Resize to thumbnail
                nparr = np.frombuffer(image_data, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    h, w = img.shape[:2]
                    new_height = int(h * (width / w))
                    thumbnail = cv2.resize(img, (width, new_height), interpolation=cv2.INTER_AREA)

                    # Save to disk
                    thumb_path = thumbnails_dir / f"{cid}.jpg"
                    cv2.imwrite(str(thumb_path), thumbnail, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    results["success"].append(cid)
                    logger.info(f"{cid}: saved")
                else:
                    results["failed"].append(cid)
            else:
                results["failed"].append(cid)
                logger.info(f"{cid}: failed to capture")

        return {
            "facility": facility,
            "thumbnails_dir": str(thumbnails_dir),
            "success_count": len(results["success"]),
            "failed_count": len(results["failed"]),
            "success": results["success"],
            "failed": results["failed"]
        }
    except Exception as e:
        logger.error(f"Error generating thumbnails: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cameras/{facility}/status")
def get_camera_status(
    facility: str,
    timeout: int = Query(8, description="Timeout per camera in seconds"),
    concurrency: int = Query(2, description="Max concurrent camera probes (NVR limit)")
):
    """
    Scan all cameras with limited concurrency and return which are working.
    Limits parallel connections to avoid overwhelming NVR.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time

    try:
        cameras = camera_capture.list_cameras(facility)

        def probe_camera(cam):
            """Probe a single camera, return (camera_id, success, elapsed_ms)"""
            cid = cam['id']
            start = time.time()
            result = camera_capture.capture_frame(cam['rtsp_url'], timeout=timeout)
            elapsed = int((time.time() - start) * 1000)
            return (cid, result is not None, elapsed)

        working = []
        failed = []
        timings = {}

        start_time = time.time()

        # Probe cameras with limited concurrency to avoid overwhelming NVR
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(probe_camera, cam): cam for cam in cameras}

            for future in as_completed(futures):
                cid, success, elapsed = future.result()
                timings[cid] = elapsed
                if success:
                    working.append(cid)
                    logger.info(f"{cid}: OK ({elapsed}ms)")
                else:
                    failed.append(cid)
                    logger.info(f"{cid}: FAIL ({elapsed}ms)")

        total_elapsed = int((time.time() - start_time) * 1000)

        return {
            "facility": facility,
            "working": sorted(working),
            "failed": sorted(failed),
            "working_count": len(working),
            "failed_count": len(failed),
            "total": len(cameras),
            "elapsed_ms": total_elapsed,
            "concurrency": concurrency,
            "timings": timings
        }
    except Exception as e:
        logger.error(f"Error scanning cameras: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/restart")
def restart_service():
    """
    Restart the camera service.
    Exits the process - relies on external loop (bat file) to restart.
    """
    import os
    import threading

    def delayed_exit():
        import time
        time.sleep(0.5)  # Let response return first
        os._exit(0)

    threading.Thread(target=delayed_exit, daemon=True).start()
    logger.info("Restart requested - exiting for restart")
    return {"message": "Restarting..."}

@app.post("/api/admin/reload-config")
def reload_config():
    """
    Clear caches without full restart.
    Database queries always read fresh data (no config caching).
    """
    image_cache.clear()
    clear_key_cache()
    return {"message": "Image cache + camera key cache cleared"}

@app.get("/api/admin/circuit-breaker")
def get_circuit_breaker_status():
    """Show circuit breaker status for all NVRs"""
    now = _time.time()
    with nvr_down_lock:
        status = {}
        for nvr_ip, deadline in nvr_down_until.items():
            remaining = deadline - now
            if remaining > 0:
                status[nvr_ip] = {"down": True, "remaining_sec": round(remaining, 1)}
    return {"cooldown_sec": NVR_DOWN_COOLDOWN, "tripped": status}

@app.delete("/api/admin/circuit-breaker/{nvr_ip}")
def clear_circuit_breaker(nvr_ip: str):
    """Clear circuit breaker for a specific NVR IP"""
    clear_nvr_down(nvr_ip)
    return {"message": f"Circuit breaker cleared for {nvr_ip}"}

@app.get("/api/admin/concurrency")
def get_concurrency():
    """Get current NVR max concurrent connections"""
    return {"nvr_max_concurrent": NVR_MAX_CONCURRENT}

@app.put("/api/admin/concurrency/{value}")
def set_concurrency(value: int):
    """Set NVR max concurrent connections (live, no restart needed)"""
    global NVR_MAX_CONCURRENT
    if value < 1 or value > 16:
        raise HTTPException(status_code=400, detail="Value must be 1-16")
    old = NVR_MAX_CONCURRENT
    NVR_MAX_CONCURRENT = value
    nvr_gate.set_limit(value)
    logger.info(f"NVR_MAX_CONCURRENT changed: {old} -> {value}")
    return {"nvr_max_concurrent": value, "previous": old}

@app.get("/api/admin/cooldown")
def get_cooldown():
    """Get inter-capture cooldown in seconds"""
    return {"capture_cooldown": CAPTURE_COOLDOWN}

@app.put("/api/admin/cooldown/{value}")
def set_cooldown(value: float):
    """Set inter-capture cooldown in seconds (live, no restart needed)"""
    global CAPTURE_COOLDOWN
    if value < 0 or value > 5:
        raise HTTPException(status_code=400, detail="Value must be 0-5")
    old = CAPTURE_COOLDOWN
    CAPTURE_COOLDOWN = value
    logger.info(f"CAPTURE_COOLDOWN changed: {old} -> {value}")
    return {"capture_cooldown": value, "previous": old}

# ============================================================================
# NVR DIRECT ACCESS (queries lodge.db)
# ============================================================================

DB_PATH = Path(__file__).parent.parent.parent / "warehouses" / "lodge" / "lodge.db"

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

    # Format the path with channel number
    # Supports both {channel:02d} and {channel} formats
    if '{channel:02d}' in path_format:
        path = path_format.replace('{channel:02d}', f'{channel:02d}')
    else:
        path = path_format.replace('{channel}', str(channel))

    # URL-encode password for special chars
    password_encoded = quote(password, safe='')

    # Always include colon - some NVRs require admin:@ even with empty password
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

def resolve_capture_url(nvr_id: str, channel: int, requested_source: str) -> tuple:
    """
    Resolve the actual RTSP URL and source for a capture request.
    Auto-upgrades 'rtsp' to 'direct' when direct camera access is available.

    Returns: (rtsp_url, actual_source, target_ip)
      - actual_source: "direct" or "rtsp"
      - target_ip: camera IP (direct) or NVR IP (rtsp)
    """
    if requested_source in ("rtsp", "direct"):
        cam = get_direct_camera_info(nvr_id, channel)
        if cam:
            return (build_direct_rtsp_url(cam), "direct", cam['ip'])
    nvr = get_nvr_info(nvr_id)
    if not nvr:
        return (None, "rtsp", None)
    return (build_rtsp_url(nvr, channel), "rtsp", nvr['ip'])

def nvr_supports_snapshot(nvr: dict) -> bool:
    """Check if NVR supports HTTP snapshot (LAPI)"""
    # Currently only UNIVIEW NVRs support LAPI snapshot
    brand = (nvr.get('brand') or '').upper()
    return 'UNIVIEW' in brand

def nvr_supports_playback(nvr: dict) -> bool:
    """Check if NVR supports WebSocket-based historic playback"""
    # Only UNIVIEW NVRs support the LAPI + WS playback protocol
    brand = (nvr.get('brand') or '').upper()
    return 'UNIVIEW' in brand

def get_channel_status(nvr_id: str, channel: int) -> Optional[str]:
    """Look up channel status from lodge.db. Returns 'active', 'inactive', etc."""
    if not DB_PATH.exists():
        return None
    try:
        channel_id = f"{nvr_id}_ch{channel:02d}"
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM channels WHERE id = ?", (channel_id,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None

@app.get("/api/nvr/{nvr_id}/channel/{channel}/frame")
def get_nvr_channel_frame(
    nvr_id: str,
    channel: int,
    source: str = Query("rtsp", description="Capture source: 'rtsp', 'snapshot', or 'playback'"),
    format: str = Query("image", description="Response format: 'image' or 'base64'"),
    encoding: str = Query("jpeg", description="Image encoding: 'jpeg' (lossy, ~2MB) or 'png' (lossless, ~5MB)"),
    timeout: int = Query(8, description="Capture timeout in seconds"),
    timestamp: Optional[int] = Query(None, description="Unix epoch for playback source (required when source=playback)")
):
    """
    Capture frame directly from NVR channel.

    Args:
        nvr_id: NVR ID (e.g., "nvr1", "nvr2")
        channel: Channel number (1-32)
        source: 'rtsp' (decode stream), 'snapshot' (HTTP snapshot, UNIVIEW only), or 'playback' (historic, UNIVIEW only)
        format: 'image' returns raw bytes, 'base64' returns JSON with base64 string
        encoding: 'jpeg' (lossy) or 'png' (lossless, no compression artifacts)
        timeout: Capture timeout in seconds
        timestamp: Unix epoch seconds for playback source
    """
    nvr = get_nvr_info(nvr_id)
    if not nvr:
        raise HTTPException(status_code=404, detail=f"NVR '{nvr_id}' not found in lodge.db")

    actual_source = source  # may be overridden to "direct" below

    if source == "playback":
        # Circuit breaker — playback always goes through NVR
        if is_nvr_down(nvr['ip']):
            raise HTTPException(status_code=503, detail=f"NVR '{nvr_id}' ({nvr['ip']}) is unreachable (circuit breaker tripped)")
        if not nvr_supports_playback(nvr):
            raise HTTPException(
                status_code=400,
                detail=f"NVR '{nvr_id}' ({nvr.get('brand')}) does not support playback (UNIVIEW only)"
            )
        if timestamp is None:
            raise HTTPException(
                status_code=400,
                detail="timestamp query parameter is required when source=playback"
            )
        # Check playback cache first (historic frames are immutable)
        playback_cache_key = f"playback/{nvr_id}/ch{channel}/{timestamp}"
        image_data = image_cache.get(playback_cache_key)
        if not image_data:
            epoch_begin = timestamp
            epoch_end = timestamp + 60
            logger.info(f"Playback from {nvr_id} ch{channel} @{timestamp}: {nvr['ip']}")
            image_data = capture_playback_frame(
                nvr_ip=nvr['ip'],
                username=nvr['username'] or 'admin',
                password=nvr['password'] or '',
                channel=channel,
                epoch_begin=epoch_begin,
                epoch_end=epoch_end,
                timeout=max(timeout, 30)
            )
            if image_data:
                image_cache.set(playback_cache_key, image_data, ttl=300)
    elif source == "snapshot":
        # Circuit breaker — snapshot always goes through NVR
        if is_nvr_down(nvr['ip']):
            raise HTTPException(status_code=503, detail=f"NVR '{nvr_id}' ({nvr['ip']}) is unreachable (circuit breaker tripped)")
        if not nvr_supports_snapshot(nvr):
            raise HTTPException(
                status_code=400,
                detail=f"NVR '{nvr_id}' ({nvr.get('brand')}) does not support HTTP snapshot"
            )
        logger.info(f"Snapshot from {nvr_id} ch{channel}: {nvr['ip']}")
        image_data = capture_snapshot(nvr, channel)
    else:
        # RTSP / direct — use mediator (coalescing + stream support)
        logger.info(f"HTTP capture from {nvr_id} ch{channel} via {source} (mediator)")
        image_data = frame_mediator.get_frame(nvr_id, channel, source, timeout=timeout, fmt=encoding)

    if not image_data:
        # Determine actual source for error reporting
        used_source = actual_source if source in ("rtsp", "direct") else source
        # Only trip circuit breaker for NVR-path failures
        if used_source != "direct":
            mark_nvr_down_if_unreachable(nvr['ip'])
        detail = f"Failed to capture from {nvr_id} channel {channel} via {used_source}"
        if is_nvr_down(nvr['ip']):
            detail += f" — NVR marked unreachable for {NVR_DOWN_COOLDOWN}s"
        raise HTTPException(status_code=503, detail=detail)

    media_type = "image/png" if encoding == "png" else "image/jpeg"
    if format == "base64":
        return {
            "nvr_id": nvr_id,
            "channel": channel,
            "source": source,
            "image": base64.b64encode(image_data).decode('utf-8'),
            "format": encoding
        }
    else:
        return Response(content=image_data, media_type=media_type)

@app.get("/api/nvr/{nvr_id}/channel/{channel}/info")
def get_nvr_channel_info(
    nvr_id: str,
    channel: int,
    source: str = Query("rtsp", description="Capture source: 'rtsp', 'snapshot', or 'playback'")
):
    """
    Get capture info for an NVR channel (without actually capturing).
    Returns the URL and method that would be used.
    """
    nvr = get_nvr_info(nvr_id)
    if not nvr:
        raise HTTPException(status_code=404, detail=f"NVR '{nvr_id}' not found in lodge.db")

    supports_snapshot = nvr_supports_snapshot(nvr)
    supports_playback = nvr_supports_playback(nvr)

    if source == "playback":
        return {
            "nvr_id": nvr_id,
            "channel": channel,
            "nvr_ip": nvr['ip'],
            "nvr_brand": nvr['brand'],
            "source": "playback",
            "supports_playback": supports_playback,
            "method": "WebSocket RTSP playback via LAPI (UNIVIEW)" if supports_playback else "Not supported",
            "notes": "LAPI Login → KeepAlive → RecordURL → WS+RTSP → H.265 depacketize → ffmpeg. ~3-5s. Requires timestamp param." if supports_playback else f"{nvr.get('brand')} does not support WebSocket playback"
        }
    elif source == "snapshot":
        snapshot_url = f"http://{nvr['ip']}/LAPI/V1.0/Channels/{channel}/Media/Video/Streams/0/Snapshot"
        return {
            "nvr_id": nvr_id,
            "channel": channel,
            "nvr_ip": nvr['ip'],
            "nvr_brand": nvr['brand'],
            "source": "snapshot",
            "supports_snapshot": supports_snapshot,
            "capture_url": snapshot_url if supports_snapshot else None,
            "method": "HTTP snapshot via LAPI (UNIVIEW)" if supports_snapshot else "Not supported",
            "notes": "Direct JPEG from NVR, no transcoding. Auth: Digest. ~60KB, ~150ms" if supports_snapshot else f"{nvr.get('brand')} does not support HTTP snapshot"
        }
    else:
        # Check for direct camera access
        cam_direct = get_direct_camera_info(nvr_id, channel)
        if cam_direct:
            direct_url = build_direct_rtsp_url(cam_direct)
            # Mask password
            creds = get_camera_credentials(cam_direct['model'], cam_direct['ip'])
            display_url = direct_url.replace(quote(creds[1], safe=''), '***') if creds[1] else direct_url
            return {
                "nvr_id": nvr_id,
                "channel": channel,
                "nvr_ip": nvr['ip'],
                "nvr_brand": nvr['brand'],
                "source": "direct",
                "camera_ip": cam_direct['ip'],
                "camera_model": cam_direct['model'],
                "supports_snapshot": supports_snapshot,
                "supports_playback": supports_playback,
                "capture_url": display_url,
                "method": "Direct RTSP to camera IP (bypasses NVR)",
                "notes": f"Auto-upgraded from rtsp. Camera at {cam_direct['ip']}, model {cam_direct['model']}. No NVR gate slot needed."
            }

        rtsp_url = build_rtsp_url(nvr, channel)
        # Mask password in display URL
        display_url = rtsp_url
        if nvr['password']:
            display_url = rtsp_url.replace(quote(nvr['password'], safe=''), '***')

        return {
            "nvr_id": nvr_id,
            "channel": channel,
            "nvr_ip": nvr['ip'],
            "nvr_brand": nvr['brand'],
            "source": "rtsp",
            "supports_snapshot": supports_snapshot,
            "supports_playback": supports_playback,
            "capture_url": display_url,
            "method": "RTSP stream capture via OpenCV/FFmpeg (through NVR)",
            "notes": "Decodes H.264/HEVC stream, encodes to JPEG. Reads up to 3 frames, skips grey frames. ~1-2MB, ~1-2s"
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

# ============================================================================
# CAMERA-BY-IP ACCESS (direct IP-based frame capture + stream management)
# ============================================================================

def _get_camera_by_ip(camera_ip: str) -> Optional[dict]:
    """Look up camera by IP from lodge.db. Returns {mac, ip, model, rtsp_path}."""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH), timeout=2)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT mac, ip, model, rtsp_path, protocol, http_path
        FROM cameras
        WHERE ip = ?
    """, (camera_ip,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


@app.get("/api/camera/{camera_ip}/frame")
def get_camera_frame_by_ip(
    camera_ip: str,
    format: str = Query("image", description="Response format: 'image' or 'base64'"),
    encoding: str = Query("jpeg", description="'jpeg' or 'png'"),
    timeout: int = Query(8, description="Capture timeout in seconds"),
):
    """
    Get frame from a camera by its IP address.
    If a persistent stream is active, returns the latest frame instantly.
    Otherwise falls back to request-mode capture via mediator.
    """
    # Check for active stream first (instant path)
    stream = stream_manager.get(camera_ip)
    if stream:
        frame = stream.get_frame()
        if frame:
            media_type = "image/png" if encoding == "png" else "image/jpeg"
            if format == "base64":
                return {
                    "camera_ip": camera_ip,
                    "source": "stream",
                    "image": base64.b64encode(frame).decode('utf-8'),
                    "format": encoding,
                    "frame_age_ms": round(stream.frame_age_ms, 1),
                }
            return Response(content=frame, media_type=media_type)

    # Look up camera in DB
    cam = _get_camera_by_ip(camera_ip)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera with IP '{camera_ip}' not found in cameras table")

    # HTTP cameras (ESP32/PoE-CAM) — route through coalescing mediator.
    if cam.get("protocol") == "http":
        http_path = cam.get("http_path") or "/"
        try:
            image_data = http_mediator.get_frame(camera_ip, http_path)
        except requests.RequestException as e:
            raise HTTPException(status_code=503, detail=f"HTTP camera {camera_ip} failed: {e}")
        media_type = "image/jpeg"  # PoE-CAM firmware serves JPEG only
        if format == "base64":
            return {
                "camera_ip": camera_ip,
                "source": "http-mediator",
                "image": base64.b64encode(image_data).decode('utf-8'),
                "format": "jpeg",
            }
        return Response(content=image_data, media_type=media_type)

    # Build direct RTSP URL
    rtsp_url = build_direct_rtsp_url(cam)

    # Request-mode capture via mediator's executor
    image_data = camera_capture.capture_frame(rtsp_url, timeout=timeout, fmt=encoding)

    if not image_data:
        raise HTTPException(status_code=503, detail=f"Failed to capture from camera {camera_ip}")

    media_type = "image/png" if encoding == "png" else "image/jpeg"
    if format == "base64":
        return {
            "camera_ip": camera_ip,
            "source": "request",
            "image": base64.b64encode(image_data).decode('utf-8'),
            "format": encoding,
        }
    return Response(content=image_data, media_type=media_type)


@app.post("/api/camera/{camera_ip}/stream/start")
def start_camera_stream(camera_ip: str):
    """
    Start a persistent RTSP stream for a camera IP.
    The stream runs in a subprocess, decoding frames continuously.
    Subsequent requests to /api/camera/{ip}/frame return the latest frame instantly.
    """
    cam = _get_camera_by_ip(camera_ip)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera with IP '{camera_ip}' not found")

    rtsp_url = build_direct_rtsp_url(cam)
    stream = stream_manager.get_or_open(camera_ip, rtsp_url)

    return {
        "camera_ip": camera_ip,
        "status": "started" if stream.is_running else "starting",
        "pid": stream._process.pid if stream._process else None,
        "model": cam.get("model"),
    }


@app.post("/api/camera/{camera_ip}/stream/stop")
def stop_camera_stream(camera_ip: str):
    """Stop a persistent RTSP stream for a camera IP."""
    stream = stream_manager.get(camera_ip)
    if not stream:
        raise HTTPException(status_code=404, detail=f"No active stream for {camera_ip}")

    stream_manager.close(camera_ip)
    return {"camera_ip": camera_ip, "status": "stopped"}


@app.get("/api/streams")
def list_streams():
    """List all active persistent RTSP streams and their status."""
    return stream_manager.status()


@app.get("/api/http-cameras/status")
def http_cameras_status():
    """Per-camera state for HTTP (ESP32/PoE-CAM) proxy: frames served, failures, circuit state."""
    return http_mediator.status()


@app.get("/api/http-cameras/{camera_ip}/frame")
def http_camera_frame_via_mediator(
    camera_ip: str,
    http_path: str = Query("/stream", description="Upstream MJPEG path"),
):
    """Mediator-proxied frame fetch — no lodge.db lookup, useful for ad-hoc
    IPs (the m5camserver-emu, a freshly-flashed cam not yet in the DB, etc.).
    Returns the buffered JPEG; starts the persistent stream worker on demand."""
    try:
        image_data = http_mediator.get_frame(camera_ip, http_path)
    except requests.RequestException as e:
        raise HTTPException(status_code=503, detail=f"HTTP camera {camera_ip} failed: {e}")
    return Response(content=image_data, media_type="image/jpeg")


@app.post("/api/http-cameras/{camera_ip}/reset-circuit")
def http_camera_reset_circuit(camera_ip: str):
    """Clear a locked-open circuit breaker after a camera has been power-cycled.
    Also invalidates the /version cache — firmware could have been changed
    while the cam was off (manual flash, factory reset, board swap)."""
    ip, port = _split_ip_port(camera_ip)
    cleared = http_mediator.reset_circuit(camera_ip)
    if not cleared:
        raise HTTPException(status_code=404, detail=f"No HTTP camera state for {camera_ip}")
    m5_probe.invalidate(ip, port)
    return {"camera_ip": camera_ip, "status": "reset", "version_cache": "invalidated"}


def _split_ip_port(camera_ip: str, default_port: int = 80) -> tuple:
    """Accept '1.2.3.4' or '1.2.3.4:8080' from path params."""
    if ":" in camera_ip:
        host, port_str = camera_ip.split(":", 1)
        try:
            return host, int(port_str)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"bad port in '{camera_ip}'")
    return camera_ip, default_port


@app.get("/api/http-cameras/{camera_ip}/version")
def http_camera_version(
    camera_ip: str,
    force: bool = Query(False, description="Bypass cache and refetch (will fail with ConnectionRefused if the mediator is streaming)"),
):
    """
    Fingerprint a cam via M5CamServer /version. Returns parsed firmware info
    (sketch, sketch_md5, build_date/time, uptime_s, free_heap, camera_ok,
    resolutions) when the cam runs M5CamServer. Returns is_m5camserver=false
    with an `error` reason for stock firmware, unreachable cams, or non-cam
    devices. Cached 5 min by default — stock firmware probes burn W5500
    socket-pool cycles, so don't hammer.

    If the mediator is actively streaming this cam, a fresh /version probe
    would contend for the W5500 single-client slot and fail. We return cached
    info (even past TTL) in that case — firmware doesn't change without an
    explicit OTA, which invalidates the cache on success.
    """
    ip, port = _split_ip_port(camera_ip)

    if not force:
        med = http_mediator.status().get(ip)
        mediator_streaming = bool(med and med.get("worker_alive") and med.get("has_frame"))
        if mediator_streaming:
            cached = m5_probe.get_cached(ip, port)
            if cached is not None and cached.transport_ok:
                return {
                    "camera_ip": camera_ip,
                    "is_m5camserver": cached.is_m5camserver,
                    "sketch": cached.sketch,
                    "sketch_md5": cached.sketch_md5,
                    "build_date": cached.build_date,
                    "build_time": cached.build_time,
                    "uptime_s": cached.uptime_s,
                    "free_heap": cached.free_heap,
                    "camera_ok": cached.camera_ok,
                    "resolutions": cached.resolutions,
                    "error": cached.error,
                    "fetched_age_s": round(time.time() - cached.fetched_at, 1),
                    "source": "cache (mediator streaming — probe skipped)",
                }
            # No transport-OK cache and mediator owns the W5500 slot. A probe
            # would fail with ConnectionRefused; report honestly instead.
            frame_age_ms = med.get("frame_age_ms")
            return {
                "camera_ip": camera_ip,
                "is_m5camserver": None,
                "sketch": None,
                "sketch_md5": None,
                "build_date": None,
                "build_time": None,
                "uptime_s": None,
                "free_heap": None,
                "camera_ok": None,
                "resolutions": None,
                "error": (
                    f"firmware unprobeable while mediator streams (worker alive, "
                    f"frame age {frame_age_ms:.0f}ms): /version contends for the W5500 "
                    f"single-client slot. Stop the mediator stream to probe, or use "
                    f"?force=true (will likely fail until the slot frees)."
                ),
                "fetched_age_s": None,
                "source": "mediator-busy",
            }

    info = m5_probe.version(ip, port=port, force=force)
    return {
        "camera_ip": camera_ip,
        "is_m5camserver": info.is_m5camserver,
        "sketch": info.sketch,
        "sketch_md5": info.sketch_md5,
        "build_date": info.build_date,
        "build_time": info.build_time,
        "uptime_s": info.uptime_s,
        "free_heap": info.free_heap,
        "camera_ok": info.camera_ok,
        "resolutions": info.resolutions,
        "error": info.error,
        "fetched_age_s": round(time.time() - info.fetched_at, 1),
        "source": "probe",
    }


@app.get("/api/http-cameras/m5camserver-status")
def http_cameras_m5camserver_status():
    """All cached M5CamServer /version probes (per IP[:port])."""
    return m5_probe.status()


@app.get("/api/http-cameras/{camera_ip}/health")
def http_camera_health(
    camera_ip: str,
    tcp_timeout: float = Query(2.0, description="SYN-ACK wait seconds"),
    http_timeout: float = Query(6.0, description="HTTP response wait seconds"),
    ping: bool = Query(True, description="Run ICMP probe to disambiguate offline vs tcp-closed"),
    force_probe: bool = Query(False, description="Skip the mediator-proxied shortcut and run the active wedge probe even if the mediator is streaming"),
    fresh_ms: float = Query(5000, description="Max frame age (ms) for mediator-proxied alive verdict"),
):
    """
    Wedge-state diagnostic. Distinguishes:
      alive | wedged_app | wedged_tcp | offline | unreachable.

    If the mediator is actively streaming this cam with a fresh frame, we
    short-circuit to alive without probing — the W5500 holds one client
    slot, and a fresh TCP probe while the mediator owns it would falsely
    report wedged_tcp. Use force_probe=true to override.

    Active probe is firmware-agnostic — works on stock M5PoECam,
    M5CamServer, anything HTTP-on-W5500. Doesn't read body bytes; only
    checks whether the application layer responds at all (the gen-42
    wedge fingerprint is "TCP works, HTTP never responds").
    """
    ip, port = _split_ip_port(camera_ip)

    if not force_probe:
        med = http_mediator.status().get(ip)
        if med and med.get("worker_alive") and med.get("has_frame"):
            age_ms = med.get("frame_age_ms")
            if age_ms is not None and age_ms < fresh_ms:
                return {
                    "ip": ip,
                    "port": port,
                    "verdict": "alive",
                    "ping_ok": None,
                    "tcp_open": True,
                    "http_ok": True,
                    "tcp_ms": None,
                    "http_ms": None,
                    "http_status": 200,
                    "error": None,
                    "recommendation": f"alive — mediator streaming (frame age {age_ms:.0f}ms); active probe skipped to avoid contending for the W5500 single-client slot",
                    "source": "mediator",
                }

    report = _asdict(detect_wedge(
        ip, port,
        tcp_timeout=tcp_timeout,
        http_timeout=http_timeout,
        do_ping=ping,
    ))
    report["source"] = "probe"
    return report


class DuplexCommand(BaseModel):
    cmd: dict


@app.post("/api/http-cameras/{camera_ip}/duplex/enable")
def http_camera_duplex_enable(camera_ip: str):
    """
    Switch the mediator's upstream connection for this cam to POST /stream
    duplex mode. Verifies via /version that the cam runs M5CamServer first
    (stock M5PoECam doesn't speak POST /stream and would misbehave).

    Effective on the next stream start; if a stream is currently running
    in GET mode it gets stopped so the next get_frame call reconnects in
    duplex mode.
    """
    ip, port = _split_ip_port(camera_ip)
    # Use cached /version when available — a force=True probe would race
    # the mediator's persistent stream for the cam's single TCP slot and
    # fail with "connection refused" (correctly: the mediator is holding it).
    # Caller should hit /version first if the cache is cold.
    info = m5_probe.version(ip, port=port, force=False)
    if not info.is_m5camserver:
        raise HTTPException(
            status_code=400,
            detail=(f"refused: {camera_ip} is not running M5CamServer "
                    f"({info.error or 'no JSON /version response'}) — "
                    "POST /stream duplex is M5CamServer-only. "
                    "If the /version cache is empty, hit /api/http-cameras/{ip}/version "
                    "before enabling duplex (don't run while another stream is active)."),
        )
    http_mediator.enable_duplex(camera_ip)
    return {"camera_ip": camera_ip, "stream_mode": "POST_DUPLEX",
            "sketch_md5": info.sketch_md5, "uptime_s": info.uptime_s}


@app.post("/api/http-cameras/{camera_ip}/duplex/disable")
def http_camera_duplex_disable(camera_ip: str):
    """Revert the mediator to GET /stream mode for this cam."""
    http_mediator.disable_duplex(camera_ip)
    return {"camera_ip": camera_ip, "stream_mode": "GET"}


@app.post("/api/http-cameras/{camera_ip}/command")
def http_camera_send_command(camera_ip: str, body: DuplexCommand):
    """
    Queue a JSON command to be written upstream over the mediator's POST
    /stream duplex connection. Cam must already be in duplex mode (call
    /duplex/enable first).

    Body shape: {"cmd": {...}} — the {...} object is sent verbatim as one
    JSONL line. Example: {"cmd": {"action": "set_resolution", "preset": "VGA"}}.
    """
    try:
        http_mediator.send_command(camera_ip, body.cmd)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"camera_ip": camera_ip, "queued": body.cmd}


@app.post("/api/http-cameras/{camera_ip}/ota")
async def http_camera_ota(camera_ip: str, firmware: UploadFile = File(...)):
    """
    OTA upload to an M5CamServer cam. Streams NDJSON progress events:

      {"type":"phase","name":"preflight"}
      {"type":"progress","phase":"preflight","msg":"current /version: ..."}
      {"type":"phase","name":"upload","size_bytes":...}
      {"type":"progress","phase":"upload","sent":...,"total":...,"pct":...,"kbps":...}
      ...
      {"type":"phase","name":"verify"}
      {"type":"progress","phase":"verify","attempt":N,"msg":"..."}
      {"type":"complete","ok":true|false,"reason":"...","elapsed_s":...,...}

    Use `curl -N -X POST -F firmware=@sketch.ino.bin <url>` to see lines as
    they arrive. Refuses .merged.bin filenames and >2 MB uploads. Pre-flight
    md5 check skips the upload entirely if the cam is already running this
    firmware. Final verify uses /version polling as the primary success
    signal — even a 200 from /update isn't trusted until md5 matches.
    """
    ip, port = _split_ip_port(camera_ip)
    fw_bytes = await firmware.read()
    filename = firmware.filename or ""

    def event_stream():
        for ev in m5_probe.do_ota(ip, port, fw_bytes, filename=filename):
            yield json.dumps(ev) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


# ============================================================================
# ZMQ FRAME SERVER
# ============================================================================

ZMQ_ENDPOINT = "ipc:///tmp/camera_frames"
ZMQ_ASYNC_ENDPOINT = "ipc:///tmp/camera_frames_async"
# Windows doesn't support ipc://, use tcp instead
if sys.platform == "win32":
    ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"
    ZMQ_ASYNC_ENDPOINT = "tcp://127.0.0.1:5556"

# Worker thread tracking for monitor
import time as _time
worker_state = {
    "zmq_rep": {"status": "idle", "current": None, "completed": 0, "errors": 0},
    "zmq_router": {
        "status": "idle",
        "pending_requests": {},  # request_id -> {nvr, channel, source, queued_at} - waiting for worker
        "active_requests": {},   # request_id -> {nvr, channel, source, queued_at, processing_started_at} - being processed
        "completed": 0,
        "errors": 0,
        "total_bytes": 0,
        "event_log": []  # Circular buffer of recent events for debugging
    }
}
worker_state_lock = threading.Lock()
EVENT_LOG_MAX = 1000  # Keep last 1000 events
event_seq = 0  # Global sequence number for event ordering

# Tag scan state — one scan at a time
tag_scan_state = {
    "active_scan": None,
    "history": []
}
tag_scan_lock = threading.Lock()
TAG_SCAN_HISTORY_MAX = 10

def log_event(event_type: str, request_id: str, nvr: str, channel: int, source: str, extra: dict = None):
    """Add event to the circular event log"""
    global event_seq
    event_seq += 1
    event = {
        "seq": event_seq,
        "t": _time.time(),
        "type": event_type,
        "id": request_id,
        "nvr": nvr,
        "ch": channel,
        "src": source
    }
    if extra:
        event.update(extra)
    with worker_state_lock:
        log = worker_state["zmq_router"]["event_log"]
        log.append(event)
        if len(log) > EVENT_LOG_MAX:
            worker_state["zmq_router"]["event_log"] = log[-EVENT_LOG_MAX:]

# Per-NVR connection throttling
NVR_MAX_CONCURRENT = 2
# Inter-capture cooldown (seconds). 0 = no delay. Tunable live via PUT /api/admin/cooldown/{value}
CAPTURE_COOLDOWN = 0.0
# Max age for pending ROUTER requests before reaping (seconds)
PENDING_MAX_AGE = 300

class NvrGate:
    """
    Global per-NVR slot manager. Shared by ROUTER, tag scan, and probe.
    All acquire/release calls run in the main process — only the actual
    capture is dispatched to subprocesses.
    """

    def __init__(self, default_limit: int = 2):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._limit = default_limit
        self._slots = {}  # nvr_ip -> count of slots currently held

    def set_limit(self, limit: int):
        with self._cond:
            self._limit = limit
            self._cond.notify_all()

    def try_acquire(self, nvr_ip: str) -> bool:
        """Non-blocking. Returns True if slot acquired."""
        with self._lock:
            held = self._slots.get(nvr_ip, 0)
            if held < self._limit:
                self._slots[nvr_ip] = held + 1
                return True
            return False

    def acquire(self, nvr_ip: str, timeout: float = 30.0) -> bool:
        """Blocking with timeout. Returns True when slot acquired."""
        deadline = _time.monotonic() + timeout
        with self._cond:
            while self._slots.get(nvr_ip, 0) >= self._limit:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=remaining)
            self._slots[nvr_ip] = self._slots.get(nvr_ip, 0) + 1
            return True

    def release(self, nvr_ip: str):
        with self._cond:
            held = self._slots.get(nvr_ip, 0)
            self._slots[nvr_ip] = max(0, held - 1)
            self._cond.notify_all()

    def status(self) -> dict:
        with self._lock:
            return {
                "limit": self._limit,
                "nvrs": {
                    ip: {"held": count, "available": max(0, self._limit - count)}
                    for ip, count in self._slots.items()
                    if count > 0
                }
            }

nvr_gate = NvrGate(default_limit=NVR_MAX_CONCURRENT)

# Module-level ProcessPoolExecutor — shared by mediator, ROUTER, tag scan
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

frame_executor = ProcessPoolExecutor(max_workers=16)

# Stream manager + frame mediator
stream_manager = StreamManager(idle_timeout=60.0)
stream_manager.start()

# Initialized after _capture_from_url is defined (see below)
frame_mediator: Optional["FrameMediator"] = None

def is_nvr_reachable(nvr_ip: str, port: int = 554, timeout: float = 2) -> bool:
    """Quick TCP probe to NVR RTSP port. Used after capture failure to distinguish NVR-down from channel-dead."""
    import socket
    try:
        s = socket.create_connection((nvr_ip, port), timeout=timeout)
        s.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def mark_nvr_down_if_unreachable(nvr_ip: str):
    """Trip circuit breaker only if NVR is actually unreachable (TCP probe fails)."""
    if not is_nvr_reachable(nvr_ip):
        mark_nvr_down(nvr_ip)
    else:
        logger.info(f"Capture failed but NVR {nvr_ip} is TCP-reachable — channel issue, not tripping breaker")

# Circuit breaker: skip requests to NVRs that recently failed
NVR_DOWN_COOLDOWN = 60  # seconds
nvr_down_until = {}  # nvr_ip -> timestamp when retry is allowed
nvr_down_lock = threading.Lock()

def mark_nvr_down(nvr_ip: str):
    """Mark an NVR as unreachable for NVR_DOWN_COOLDOWN seconds"""
    with nvr_down_lock:
        nvr_down_until[nvr_ip] = _time.time() + NVR_DOWN_COOLDOWN
    logger.warning(f"Circuit breaker TRIPPED for {nvr_ip} — skipping requests for {NVR_DOWN_COOLDOWN}s")

def is_nvr_down(nvr_ip: str) -> bool:
    """Check if an NVR is marked as down"""
    with nvr_down_lock:
        deadline = nvr_down_until.get(nvr_ip, 0)
        if _time.time() < deadline:
            return True
        # Expired — clean up
        nvr_down_until.pop(nvr_ip, None)
        return False

def clear_nvr_down(nvr_ip: str):
    """Manually clear circuit breaker for an NVR"""
    with nvr_down_lock:
        nvr_down_until.pop(nvr_ip, None)
    logger.info(f"Circuit breaker CLEARED for {nvr_ip}")

def capture_snapshot(nvr: dict, channel: int) -> Optional[bytes]:
    """Capture frame via NVR's HTTP snapshot endpoint (UNIVIEW only)"""
    # UNIVIEW LAPI snapshot URL pattern
    url = f"http://{nvr['ip']}/LAPI/V1.0/Channels/{channel}/Media/Video/Streams/0/Snapshot"
    username = nvr['username'] or 'admin'
    password = nvr['password'] or ''

    try:
        resp = requests.get(url, auth=HTTPDigestAuth(username, password), timeout=5)
        if resp.status_code == 200 and resp.headers.get('content-type', '').startswith('image'):
            return resp.content
        else:
            logger.warning(f"Snapshot failed: {resp.status_code}")
            return None
    except Exception as e:
        logger.error(f"Snapshot error: {e}")
        return None

def _capture_from_url(rtsp_url: str, timeout: int = 8, cooldown: float = 0.0, fmt: str = "jpeg") -> bytes:
    """
    Capture a frame from an RTSP URL in a subprocess (own GIL, no contention).
    Called via ProcessPoolExecutor. URL resolved by caller in main process.
    Returns encoded image bytes or empty bytes on failure.
    """
    image_data = camera_capture.capture_frame(rtsp_url, timeout=timeout, fmt=fmt)
    if cooldown > 0:
        _time.sleep(cooldown)
    return image_data or b""

def _capture_playback_subprocess(nvr_id: str, channel: int, timestamp: int) -> bytes:
    """Capture a playback frame in a subprocess. NVR-specific (needs DB lookup)."""
    nvr = get_nvr_info(nvr_id)
    if not nvr or not nvr_supports_playback(nvr):
        return b""
    image_data = capture_playback_frame(
        nvr_ip=nvr['ip'],
        username=nvr['username'] or 'admin',
        password=nvr['password'] or '',
        channel=channel,
        epoch_begin=timestamp,
        epoch_end=timestamp + 60,
        timeout=30
    )
    return image_data or b""

def _capture_snapshot_subprocess(nvr_id: str, channel: int) -> bytes:
    """Capture an HTTP snapshot in a subprocess. NVR-specific (needs DB lookup)."""
    nvr = get_nvr_info(nvr_id)
    if not nvr:
        return b""
    image_data = capture_snapshot(nvr, channel)
    return image_data or b""


# Now that _capture_from_url is defined, initialize the mediator
frame_mediator = FrameMediator(
    executor=frame_executor,
    nvr_gate=nvr_gate,
    stream_manager=stream_manager,
    resolve_url_fn=resolve_capture_url,
    capture_fn=_capture_from_url,
    capture_cooldown_fn=lambda: CAPTURE_COOLDOWN,
)


def zmq_frame_handler():
    """ZMQ REP server for frame requests. Runs in background thread."""
    context = zmq.Context()
    socket = context.socket(zmq.REP)
    socket.bind(ZMQ_ENDPOINT)
    logger.info(f"ZMQ frame server listening on {ZMQ_ENDPOINT}")

    while True:
        try:
            # Update state to idle while waiting
            with worker_state_lock:
                worker_state["zmq_rep"]["status"] = "waiting"
                worker_state["zmq_rep"]["current"] = None

            # Receive request
            msg = socket.recv_string()
            req = json.loads(msg)

            nvr_id = req.get("nvr", "nvr1")
            channel = req.get("channel", 1)
            source = req.get("source", "rtsp")  # "rtsp" or "snapshot"

            # Update state to processing
            with worker_state_lock:
                worker_state["zmq_rep"]["status"] = "capturing"
                worker_state["zmq_rep"]["current"] = {
                    "nvr": nvr_id, "channel": channel, "source": source,
                    "started_at": _time.time()
                }

            logger.info(f"ZMQ request: {nvr_id} ch{channel} via {source}")

            # Get NVR info
            nvr = get_nvr_info(nvr_id)
            if not nvr:
                with worker_state_lock:
                    worker_state["zmq_rep"]["errors"] += 1
                socket.send(b"")  # Empty response for error
                continue

            # Capture based on source
            if source == "playback":
                # Circuit breaker — playback always through NVR
                if is_nvr_down(nvr['ip']):
                    logger.info(f"ZMQ REP: circuit breaker skipping {nvr_id} ch{channel}")
                    with worker_state_lock:
                        worker_state["zmq_rep"]["errors"] += 1
                    socket.send(b"")
                    continue
                timestamp = req.get("timestamp")
                if not timestamp or not nvr_supports_playback(nvr):
                    with worker_state_lock:
                        worker_state["zmq_rep"]["errors"] += 1
                    socket.send(b"")
                    continue
                epoch_begin = timestamp
                epoch_end = timestamp + 60
                image_data = capture_playback_frame(
                    nvr_ip=nvr['ip'],
                    username=nvr['username'] or 'admin',
                    password=nvr['password'] or '',
                    channel=channel,
                    epoch_begin=epoch_begin,
                    epoch_end=epoch_end,
                    timeout=30
                )
                actual_source = "playback"
            elif source == "snapshot":
                # Circuit breaker — snapshot always through NVR
                if is_nvr_down(nvr['ip']):
                    logger.info(f"ZMQ REP: circuit breaker skipping {nvr_id} ch{channel}")
                    with worker_state_lock:
                        worker_state["zmq_rep"]["errors"] += 1
                    socket.send(b"")
                    continue
                image_data = capture_snapshot(nvr, channel)
                actual_source = "snapshot"
            else:  # rtsp / direct — use mediator (coalescing + stream support)
                logger.info(f"ZMQ REP: {nvr_id} ch{channel} via {source} (mediator)")
                image_data = frame_mediator.get_frame(nvr_id, channel, source)
                actual_source = source

            if image_data:
                with worker_state_lock:
                    worker_state["zmq_rep"]["completed"] += 1
                socket.send(image_data)
            else:
                if actual_source not in ("direct", "playback", "snapshot"):
                    mark_nvr_down_if_unreachable(nvr['ip'])
                with worker_state_lock:
                    worker_state["zmq_rep"]["errors"] += 1
                socket.send(b"")  # Empty response for error

        except Exception as e:
            logger.error(f"ZMQ handler error: {e}")
            with worker_state_lock:
                worker_state["zmq_rep"]["errors"] += 1
            try:
                socket.send(b"")
            except:
                pass

def zmq_async_handler():
    """
    ZMQ ROUTER server for async frame requests.
    Allows multiple in-flight requests with out-of-order responses.

    Smart scheduling: only submits work when NVR has capacity,
    so workers never block and requests to different NVRs don't wait behind each other.

    Uses FrameMediator for N:1 coalescing — simultaneous requests for the same
    camera share one capture. Persistent streams provide instant frames.

    Protocol:
      Request: JSON with {id, nvr, channel, source}
      Response: [request_id bytes, frame_data bytes]

    Client should use DEALER socket.
    """
    global frame_executor
    from collections import defaultdict

    context = zmq.Context()
    socket = context.socket(zmq.ROUTER)
    socket.bind(ZMQ_ASYNC_ENDPOINT)
    logger.info(f"ZMQ async (ROUTER) server listening on {ZMQ_ASYNC_ENDPOINT}")

    # Poller for blocking recv with timeout (avoids GIL-hungry busy spin)
    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    # Use module-level executor (shared with mediator)
    executor = frame_executor

    # Pending queue: requests waiting for NVR capacity
    # {request_id -> {identity, nvr, channel, source, queued_at}}
    pending_queue = {}

    # Map nvr_id -> nvr_ip for tracking
    nvr_ip_cache = {}

    def get_nvr_ip(nvr_id: str) -> Optional[str]:
        """Get NVR IP, with caching"""
        if nvr_id not in nvr_ip_cache:
            nvr = get_nvr_info(nvr_id)
            if nvr:
                nvr_ip_cache[nvr_id] = nvr['ip']
        return nvr_ip_cache.get(nvr_id)

    # Index: (nvr_id, channel) -> request_id for dedup in pending queue
    pending_by_channel = {}  # (nvr, ch) -> req_id currently in pending_queue

    # Helper: send a response to a single waiter
    def send_response(identity, response_id, image_data, meta_dict):
        metadata = json.dumps(meta_dict).encode('utf-8')
        socket.send_multipart([identity, b"", response_id.encode('utf-8'), image_data, metadata])

    while True:
        try:
            # Update status
            with worker_state_lock:
                has_work = bool(frame_mediator._inflight) or bool(pending_queue)
                worker_state["zmq_router"]["status"] = "running" if has_work else "idle"

            # Poll for new requests (blocks up to 50ms, releasing GIL)
            poll_timeout = 1 if (frame_mediator._inflight or pending_queue) else 50
            socks = dict(poller.poll(poll_timeout))

            if socket in socks:
                frames = socket.recv_multipart()
                identity = frames[0]
                msg = frames[-1].decode('utf-8')
                req = json.loads(msg)

                request_id = req.get("id", "unknown")
                nvr_id = req.get("nvr", "nvr1")
                channel = req.get("channel", 1)
                source = req.get("source", "rtsp")
                req_timestamp = req.get("timestamp")  # For playback source

                logger.info(f"ZMQ async request {request_id}: {nvr_id} ch{channel} via {source}")

                recv_at = _time.time()
                log_event("RECV", request_id, nvr_id, channel, source)

                # For RTSP/direct sources, try mediator fast-paths first
                handled = False
                if source in ("rtsp", "direct"):
                    camera_key = resolve_camera_key(nvr_id, channel)

                    # Fast path 1: active stream → instant frame
                    instant_frame = frame_mediator.try_get(camera_key)
                    if instant_frame:
                        meta_dict = {
                            "queue_ms": 0, "capture_ms": 0,
                            "total_ms": int((_time.time() - recv_at) * 1000),
                            "bytes": len(instant_frame), "error": False,
                            "source": "stream", "coalesced": False
                        }
                        send_response(identity, request_id, instant_frame, meta_dict)
                        log_event("DONE", request_id, nvr_id, channel, "stream", {"bytes": len(instant_frame), "instant": True})
                        logger.info(f"ZMQ async INSTANT {request_id}: {camera_key} stream frame {len(instant_frame)} bytes")
                        with worker_state_lock:
                            worker_state["zmq_router"]["completed"] += 1
                            worker_state["zmq_router"]["total_bytes"] += len(instant_frame)
                        handled = True

                    # Fast path 2: inflight capture → coalesce as waiter
                    if not handled:
                        inflight = frame_mediator.try_join(camera_key)
                        if inflight:
                            waiter = {"identity": identity, "response_id": request_id, "recv_at": recv_at}
                            inflight.waiters.append(waiter)
                            log_event("COALESCE", request_id, nvr_id, channel, source, {"camera_key": camera_key})
                            logger.info(f"ZMQ async COALESCE {request_id}: joined inflight for {camera_key} ({len(inflight.waiters)} waiters)")
                            with worker_state_lock:
                                worker_state["zmq_router"]["active_requests"][request_id] = {
                                    "nvr": nvr_id, "channel": channel, "source": source,
                                    "coalesced_with": camera_key, "recv_at": recv_at,
                                    "processing_started_at": inflight.started_at
                                }
                            handled = True

                if not handled:
                    # Dedup: if there's already a PENDING request for same NVR+channel,
                    # keep queue position but update to respond to the new requester
                    ch_key = (nvr_id, channel)
                    old_req_id = pending_by_channel.get(ch_key)
                    if old_req_id and old_req_id in pending_queue:
                        old_req = pending_queue[old_req_id]
                        current_response_id = old_req.get("response_id", old_req_id)
                        metadata = json.dumps({
                            "queue_ms": 0, "capture_ms": 0,
                            "total_ms": int((recv_at - old_req.get("recv_at", recv_at)) * 1000),
                            "bytes": 0, "error": True, "superseded": True
                        }).encode('utf-8')
                        socket.send_multipart([old_req["identity"], b"", current_response_id.encode('utf-8'), b"", metadata])
                        log_event("SUPERSEDED", current_response_id, nvr_id, channel, source)
                        logger.info(f"ZMQ async SUPERSEDED {current_response_id}: replaced by {request_id}")
                        old_req["identity"] = identity
                        old_req["response_id"] = request_id
                        old_req["recv_at"] = recv_at
                        old_req["timestamp"] = req_timestamp
                        with worker_state_lock:
                            worker_state["zmq_router"]["pending_requests"].pop(current_response_id, None)
                            worker_state["zmq_router"]["pending_requests"][request_id] = {
                                "nvr": nvr_id, "channel": channel, "source": source,
                                "recv_at": recv_at, "queued_at": old_req["queued_at"]
                            }
                            worker_state["zmq_router"]["errors"] += 1
                    else:
                        # Add to pending queue
                        pending_queue[request_id] = {
                            "identity": identity,
                            "nvr": nvr_id,
                            "channel": channel,
                            "source": source,
                            "timestamp": req_timestamp,
                            "recv_at": recv_at,
                            "queued_at": recv_at
                        }
                        pending_by_channel[ch_key] = request_id
                        with worker_state_lock:
                            worker_state["zmq_router"]["pending_requests"][request_id] = {
                                "nvr": nvr_id, "channel": channel, "source": source,
                                "recv_at": recv_at, "queued_at": recv_at
                            }

            # Reap stale pending requests
            now_t = _time.time()
            stale = [
                rid for rid, rq in pending_queue.items()
                if now_t - rq.get("queued_at", now_t) > PENDING_MAX_AGE
            ]
            for rid in stale:
                rq = pending_queue.pop(rid)
                ck = (rq["nvr"], rq["channel"])
                if pending_by_channel.get(ck) == rid:
                    del pending_by_channel[ck]
                resp_id = rq.get("response_id", rid)
                age = round(now_t - rq.get("queued_at", now_t), 1)
                log_event("STALE", resp_id, rq["nvr"], rq["channel"], rq["source"], {"age_sec": age})
                logger.warning(f"ZMQ async STALE {resp_id}: reaped after {age}s")
                send_response(rq["identity"], resp_id, b"", {
                    "queue_ms": int((now_t - rq.get("recv_at", now_t)) * 1000),
                    "capture_ms": 0, "total_ms": int((now_t - rq.get("recv_at", now_t)) * 1000),
                    "bytes": 0, "error": True, "stale": True
                })
                with worker_state_lock:
                    worker_state["zmq_router"]["pending_requests"].pop(resp_id, None)
                    worker_state["zmq_router"]["errors"] += 1

            # Schedule: resolve URLs and check NVR capacity
            to_submit = []
            to_reject = []
            for req_id, req in list(pending_queue.items()):
                nvr_id = req["nvr"]
                source = req["source"]

                if source in ("rtsp", "direct"):
                    camera_key = resolve_camera_key(nvr_id, req["channel"])

                    # Check if another request already started a capture for this camera
                    inflight = frame_mediator.try_join(camera_key)
                    if inflight:
                        # Coalesce: add this request as a waiter on existing capture
                        del pending_queue[req_id]
                        ck = (nvr_id, req["channel"])
                        if pending_by_channel.get(ck) == req_id:
                            del pending_by_channel[ck]
                        response_id = req.get("response_id", req_id)
                        waiter = {"identity": req["identity"], "response_id": response_id, "recv_at": req.get("recv_at", now_t)}
                        inflight.waiters.append(waiter)
                        log_event("COALESCE", response_id, nvr_id, req["channel"], source, {"camera_key": camera_key})
                        logger.info(f"ZMQ async COALESCE {response_id}: joined inflight for {camera_key} (from pending)")
                        with worker_state_lock:
                            worker_state["zmq_router"]["pending_requests"].pop(response_id, None)
                            worker_state["zmq_router"]["active_requests"][response_id] = {
                                "nvr": nvr_id, "channel": req["channel"], "source": source,
                                "coalesced_with": camera_key, "recv_at": req.get("recv_at", now_t),
                                "processing_started_at": inflight.started_at
                            }
                        continue

                    # Resolve URL: auto-upgrade to direct when available
                    rtsp_url, actual_source, target_ip = resolve_capture_url(nvr_id, req["channel"], source)
                    if not rtsp_url:
                        to_submit.append((req_id, req, None, None, "rtsp", None, camera_key))
                        continue

                    if actual_source == "direct":
                        to_submit.append((req_id, req, None, rtsp_url, "direct", target_ip, camera_key))
                    else:
                        nvr_ip = target_ip
                        if is_nvr_down(nvr_ip):
                            to_reject.append((req_id, req, nvr_ip))
                            continue
                        if nvr_gate.try_acquire(nvr_ip):
                            to_submit.append((req_id, req, nvr_ip, rtsp_url, "rtsp", nvr_ip, camera_key))
                else:
                    # playback/snapshot — always NVR, need gate (no mediator coalescing)
                    nvr_ip = get_nvr_ip(nvr_id)
                    if not nvr_ip:
                        to_submit.append((req_id, req, None, None, source, None, None))
                        continue
                    if is_nvr_down(nvr_ip):
                        to_reject.append((req_id, req, nvr_ip))
                        continue
                    if nvr_gate.try_acquire(nvr_ip):
                        to_submit.append((req_id, req, nvr_ip, None, source, nvr_ip, None))

            # Reject circuit-breaker'd requests immediately
            for req_id, req, nvr_ip in to_reject:
                del pending_queue[req_id]
                ch_key = (req["nvr"], req["channel"])
                if pending_by_channel.get(ch_key) == req_id:
                    del pending_by_channel[ch_key]
                response_id = req.get("response_id", req_id)
                now = _time.time()
                log_event("CIRCUIT_BREAK", response_id, req["nvr"], req["channel"], req["source"], {"nvr_ip": nvr_ip})
                logger.info(f"ZMQ async CIRCUIT_BREAK {response_id}: {req['nvr']} ch{req['channel']} — NVR {nvr_ip} down")
                send_response(req["identity"], response_id, b"", {
                    "queue_ms": 0, "capture_ms": 0, "total_ms": int((now - req.get("recv_at", now)) * 1000),
                    "bytes": 0, "error": True, "circuit_breaker": True
                })
                with worker_state_lock:
                    worker_state["zmq_router"]["pending_requests"].pop(response_id, None)
                    worker_state["zmq_router"]["errors"] += 1

            # Submit scheduled requests via mediator
            pool_crashed = False
            for i, (req_id, req, gate_nvr_ip, rtsp_url, actual_source, target_ip, camera_key) in enumerate(to_submit):
                del pending_queue[req_id]
                ch_key = (req["nvr"], req["channel"])
                if pending_by_channel.get(ch_key) == req_id:
                    del pending_by_channel[ch_key]
                response_id = req.get("response_id", req_id)
                started_at = _time.time()
                recv_at = req.get("recv_at", started_at)
                queue_time = round(started_at - recv_at, 3)
                logger.info(f"ZMQ async STARTED {response_id}: {req['nvr']} ch{req['channel']} via {actual_source} -> {target_ip} (queued={queue_time}s)")
                log_event("START", response_id, req["nvr"], req["channel"], actual_source, {"target_ip": target_ip, "queue_sec": queue_time})

                with worker_state_lock:
                    req_info = worker_state["zmq_router"]["pending_requests"].pop(response_id, None)
                    if req_info:
                        req_info["processing_started_at"] = started_at
                    else:
                        req_info = {"nvr": req["nvr"], "channel": req["channel"], "source": actual_source}
                    req_info["started_at"] = started_at
                    req_info["source"] = actual_source
                    worker_state["zmq_router"]["active_requests"][response_id] = req_info

                try:
                    if actual_source in ("rtsp", "direct"):
                        if not rtsp_url:
                            raise ValueError(f"No capture URL for {req['nvr']} ch{req['channel']}")
                        # Use mediator to start capture (enables coalescing)
                        inflight = frame_mediator.start_capture(
                            camera_key=camera_key,
                            rtsp_url=rtsp_url,
                            gate_nvr_ip=gate_nvr_ip,
                            nvr_id=req["nvr"],
                            channel=req["channel"],
                            source=actual_source,
                            target_ip=target_ip,
                        )
                        # Register this request as the primary waiter
                        waiter = {"identity": req["identity"], "response_id": response_id, "recv_at": recv_at}
                        inflight.waiters.append(waiter)
                    elif actual_source == "playback":
                        # Playback/snapshot: direct executor submit (no coalescing)
                        future = executor.submit(
                            _capture_playback_subprocess,
                            req["nvr"], req["channel"], req.get("timestamp")
                        )
                        # Wrap in a one-off _Inflight registered in mediator
                        from mediator import _Inflight
                        inf = _Inflight(
                            camera_key=f"playback:{req['nvr']}:ch{req['channel']}:{req.get('timestamp')}",
                            future=future,
                            gate_nvr_ip=gate_nvr_ip,
                            nvr_id=req["nvr"],
                            channel=req["channel"],
                            source=actual_source,
                            target_ip=target_ip,
                            started_at=started_at,
                            waiters=[{"identity": req["identity"], "response_id": response_id, "recv_at": recv_at}],
                        )
                        with frame_mediator._lock:
                            frame_mediator._inflight[inf.camera_key] = inf
                    elif actual_source == "snapshot":
                        future = executor.submit(
                            _capture_snapshot_subprocess,
                            req["nvr"], req["channel"]
                        )
                        from mediator import _Inflight
                        inf = _Inflight(
                            camera_key=f"snapshot:{req['nvr']}:ch{req['channel']}",
                            future=future,
                            gate_nvr_ip=gate_nvr_ip,
                            nvr_id=req["nvr"],
                            channel=req["channel"],
                            source=actual_source,
                            target_ip=target_ip,
                            started_at=started_at,
                            waiters=[{"identity": req["identity"], "response_id": response_id, "recv_at": recv_at}],
                        )
                        with frame_mediator._lock:
                            frame_mediator._inflight[inf.camera_key] = inf
                    else:
                        raise ValueError(f"Unknown source: {actual_source}")
                except BrokenProcessPool:
                    logger.error(f"ProcessPool dead, cannot submit {response_id}")
                    now = _time.time()
                    send_response(req["identity"], response_id, b"", {
                        "queue_ms": int((now - recv_at) * 1000),
                        "capture_ms": 0, "total_ms": int((now - recv_at) * 1000),
                        "bytes": 0, "error": True, "pool_crash": True
                    })
                    log_event("ERROR", response_id, req["nvr"], req["channel"], actual_source, {"pool_crash": True})
                    if gate_nvr_ip:
                        nvr_gate.release(gate_nvr_ip)
                    with worker_state_lock:
                        worker_state["zmq_router"]["active_requests"].pop(response_id, None)
                        worker_state["zmq_router"]["errors"] += 1
                    pool_crashed = True
                    break
                except Exception as e:
                    logger.error(f"Submit error {response_id}: {e}")
                    now = _time.time()
                    send_response(req["identity"], response_id, b"", {
                        "queue_ms": int((now - recv_at) * 1000),
                        "capture_ms": 0, "total_ms": int((now - recv_at) * 1000),
                        "bytes": 0, "error": True
                    })
                    log_event("ERROR", response_id, req["nvr"], req["channel"], actual_source, {"error": str(e)})
                    if gate_nvr_ip:
                        nvr_gate.release(gate_nvr_ip)
                    with worker_state_lock:
                        worker_state["zmq_router"]["active_requests"].pop(response_id, None)
                        worker_state["zmq_router"]["errors"] += 1
                    continue

            if pool_crashed:
                # Release pre-acquired gate slots for remaining un-submitted items
                for _, _, rem_gate_ip, _, _, _, _ in to_submit[i + 1:]:
                    if rem_gate_ip:
                        nvr_gate.release(rem_gate_ip)
                # Recreate module-level executor
                logger.warning("Recreating ProcessPoolExecutor after subprocess crash")
                try:
                    executor.shutdown(wait=False)
                except Exception:
                    pass
                frame_executor = ProcessPoolExecutor(max_workers=16)
                executor = frame_executor
                # Update mediator so it stops submitting to the dead pool
                frame_mediator._executor = executor

            # ---- Completion: harvest results from mediator ----
            completed_inflights = frame_mediator.get_completed()
            needs_pool_recreate = False

            for inf in completed_inflights:
                done_at = _time.time()
                try:
                    image_data = inf.future.result()
                except BrokenProcessPool as e:
                    logger.error(f"Pool crash in completion for {inf.camera_key}: {e}")
                    image_data = b""
                    needs_pool_recreate = True
                except Exception as e:
                    logger.error(f"Capture error for {inf.camera_key}: {e}")
                    image_data = b""

                is_error = len(image_data) == 0

                # Circuit breaker — only for NVR-path failures
                if is_error and inf.source != "direct" and inf.target_ip:
                    mark_nvr_down_if_unreachable(inf.target_ip)

                capture_ms = int((done_at - inf.started_at) * 1000)

                # Signal dead channels
                dead = False
                if is_error:
                    ch_status = get_channel_status(inf.nvr_id, inf.channel)
                    if ch_status == "inactive":
                        dead = True

                # Send to ALL waiters
                n_waiters = len(inf.waiters)
                for waiter in inf.waiters:
                    w_identity = waiter["identity"]
                    w_response_id = waiter["response_id"]
                    w_recv_at = waiter.get("recv_at", inf.started_at)
                    queue_ms = int((inf.started_at - w_recv_at) * 1000)
                    total_ms = int((done_at - w_recv_at) * 1000)

                    meta_dict = {
                        "queue_ms": queue_ms,
                        "capture_ms": capture_ms,
                        "total_ms": total_ms,
                        "bytes": len(image_data),
                        "error": is_error,
                        "source": inf.source,
                        "coalesced": n_waiters > 1,
                    }
                    if dead:
                        meta_dict["dead"] = True

                    send_response(w_identity, w_response_id, image_data, meta_dict)
                    logger.info(f"ZMQ async response {w_response_id} ({inf.source}): {len(image_data)} bytes (q:{queue_ms}ms cap:{capture_ms}ms, waiters:{n_waiters})")

                    if is_error:
                        log_event("ERROR", w_response_id, inf.nvr_id, inf.channel, inf.source, {
                            "capture_sec": capture_ms / 1000
                        })
                    else:
                        log_event("DONE", w_response_id, inf.nvr_id, inf.channel, inf.source, {
                            "bytes": len(image_data), "capture_sec": capture_ms / 1000,
                            "coalesced": n_waiters > 1
                        })

                    with worker_state_lock:
                        worker_state["zmq_router"]["active_requests"].pop(w_response_id, None)
                        if is_error:
                            worker_state["zmq_router"]["errors"] += 1
                        else:
                            worker_state["zmq_router"]["completed"] += 1
                            worker_state["zmq_router"]["total_bytes"] += len(image_data)

                # Release NVR gate
                if inf.gate_nvr_ip:
                    nvr_gate.release(inf.gate_nvr_ip)

            if needs_pool_recreate:
                logger.warning("Recreating ProcessPoolExecutor (crash detected in completion loop)")
                try:
                    executor.shutdown(wait=False)
                except Exception:
                    pass
                frame_executor = ProcessPoolExecutor(max_workers=16)
                executor = frame_executor
                # Update mediator so it stops submitting to the dead pool
                frame_mediator._executor = executor

            # Poller handles sleep — no busy-spin needed

        except Exception as e:
            logger.error(f"ZMQ async handler error: {e}")

# ============================================================================
# TAG SCAN (continuous frame push for AprilTag detection)
# ============================================================================

def _nvr_scan_worker(scan, nvr_id: str, channels: List[int], push_socket, push_lock: threading.Lock, stop_event: threading.Event, executor):
    """
    N worker threads per NVR, round-robining channels from a shared queue.
    N = NVR_MAX_CONCURRENT. Captures run in ProcessPoolExecutor (own GIL).
    """
    import queue

    nvr = get_nvr_info(nvr_id)
    if not nvr:
        logger.error(f"Tag scan: NVR '{nvr_id}' not found")
        return

    nvr_ip = nvr['ip']
    started_at = scan["started_at"]
    timeout = scan["timeout"]

    # Channel queue — workers pull next channel, capture, re-enqueue
    ch_queue = queue.Queue()
    for ch in channels:
        ch_queue.put(ch)

    def worker():
        import uuid
        while not stop_event.is_set():
            if _time.time() - started_at >= timeout:
                break

            if is_nvr_down(nvr_ip):
                _time.sleep(5)
                continue

            try:
                channel = ch_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # Resolve URL: direct captures skip NVR gate
            rtsp_url, actual_source, target_ip = resolve_capture_url(nvr_id, channel, "rtsp")
            needs_gate = (actual_source != "direct")

            if needs_gate:
                if not nvr_gate.acquire(nvr_ip, timeout=30.0):
                    logger.warning(f"Tag scan: gate timeout for {nvr_ip} ch{channel}")
                    ch_queue.put(channel)
                    continue

            req_id = f"ts-{uuid.uuid4().hex[:8]}"
            log_event("START", req_id, nvr_id, channel, f"tagscan/{actual_source}")
            cap_start = _time.time()

            try:
                if rtsp_url:
                    future = executor.submit(
                        _capture_from_url, rtsp_url, 8, 0.0, scan.get("encoding", "jpeg")
                    )
                    try:
                        jpeg = future.result(timeout=10)
                    except Exception as e:
                        logger.error(f"Tag scan capture error {nvr_id} ch{channel}: {e}")
                        jpeg = b""
                else:
                    jpeg = b""
            finally:
                if needs_gate:
                    nvr_gate.release(nvr_ip)

            cap_sec = round(_time.time() - cap_start, 3)

            # Re-enqueue channel for next round
            ch_queue.put(channel)

            if not jpeg:
                if actual_source != "direct":
                    mark_nvr_down_if_unreachable(nvr_ip)
                log_event("ERROR", req_id, nvr_id, channel, f"tagscan/{actual_source}", {"capture_sec": cap_sec})
                with tag_scan_lock:
                    scan["stats"]["frames_failed"] += 1
                if is_nvr_down(nvr_ip):
                    _time.sleep(5)
                continue

            try:
                ts = str(_time.time()).encode('utf-8')
                with push_lock:
                    push_socket.send_multipart([
                        nvr_id.encode('utf-8'),
                        str(channel).encode('utf-8'),
                        jpeg,
                        ts
                    ], zmq.NOBLOCK)
                log_event("DONE", req_id, nvr_id, channel, f"tagscan/{actual_source}", {"bytes": len(jpeg), "capture_sec": cap_sec})
                with tag_scan_lock:
                    scan["stats"]["frames_pushed"] += 1
                    scan["stats"]["bytes_pushed"] += len(jpeg)
            except zmq.Again:
                logger.warning(f"Tag scan: ZMQ send buffer full, dropping {nvr_id} ch{channel}")
                log_event("ERROR", req_id, nvr_id, channel, f"tagscan/{actual_source}", {"capture_sec": cap_sec, "drop": True})
                with tag_scan_lock:
                    scan["stats"]["frames_failed"] += 1
            except Exception as e:
                logger.error(f"Tag scan: ZMQ send error: {e}")
                log_event("ERROR", req_id, nvr_id, channel, f"tagscan/{actual_source}", {"capture_sec": cap_sec})
                with tag_scan_lock:
                    scan["stats"]["frames_failed"] += 1

    n_workers = min(NVR_MAX_CONCURRENT, len(channels))
    threads = []
    for _ in range(n_workers):
        t = threading.Thread(target=worker, daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

def _tag_scan_coordinator(scan):
    """Spawns one worker thread per NVR, waits for all to finish, cleans up."""
    from concurrent.futures import ProcessPoolExecutor

    executor = None
    try:
        stop_event = scan["stop_event"]
        push_socket = scan["push_socket"]
        push_lock = threading.Lock()  # ZMQ socket not thread-safe

        # Group cameras by NVR
        from collections import defaultdict
        cameras_by_nvr = defaultdict(list)
        for cam in scan["cameras"]:
            cameras_by_nvr[cam["nvr"]].append(cam["channel"])

        # Total concurrent captures = NVR_MAX_CONCURRENT per NVR
        total_workers = NVR_MAX_CONCURRENT * len(cameras_by_nvr)
        executor = ProcessPoolExecutor(max_workers=total_workers)

        with tag_scan_lock:
            scan["status"] = "running"

        # One thread per NVR (each spawns per-channel threads internally)
        threads = []
        for nvr_id, channels in cameras_by_nvr.items():
            t = threading.Thread(
                target=_nvr_scan_worker,
                args=(scan, nvr_id, channels, push_socket, push_lock, stop_event, executor),
                daemon=True
            )
            threads.append(t)
            t.start()
            logger.info(f"Tag scan {scan['scan_id']}: started worker for {nvr_id} ({len(channels)} channels)")

        # Wait for all workers
        for t in threads:
            t.join()

        with tag_scan_lock:
            if scan["status"] != "error":
                scan["status"] = "stopped" if stop_event.is_set() else "completed"

    except Exception as e:
        logger.error(f"Tag scan coordinator error: {e}")
        with tag_scan_lock:
            scan["status"] = "error"
            scan["stats"]["error"] = str(e)

    finally:
        try:
            scan["push_socket"].close()
        except:
            pass
        if executor:
            executor.shutdown(wait=False)
        scan["finished_at"] = _time.time()
        logger.info(f"Tag scan {scan['scan_id']} {scan['status']}: {scan['stats']}")

        # Move to history
        with tag_scan_lock:
            tag_scan_state["active_scan"] = None
            tag_scan_state["history"].append({
                "scan_id": scan["scan_id"],
                "status": scan["status"],
                "started_at": scan["started_at"],
                "finished_at": scan["finished_at"],
                "stats": scan["stats"]
            })
            if len(tag_scan_state["history"]) > TAG_SCAN_HISTORY_MAX:
                tag_scan_state["history"] = tag_scan_state["history"][-TAG_SCAN_HISTORY_MAX:]

@app.post("/api/tag-scan/start")
def start_tag_scan(request: TagScanRequest):
    """
    Start continuous tag scan. Pushes frames via ZMQ PUSH to the given endpoint.
    One scan at a time. Each NVR's cameras are round-robined independently.
    """
    if not request.cameras:
        raise HTTPException(status_code=400, detail="No cameras specified")
    if request.timeout <= 0:
        raise HTTPException(status_code=400, detail="Timeout must be positive")

    with tag_scan_lock:
        if tag_scan_state["active_scan"] is not None:
            active = tag_scan_state["active_scan"]
            raise HTTPException(
                status_code=409,
                detail=f"Scan '{active['scan_id']}' already active ({active['status']})"
            )

    for cam in request.cameras:
        nvr = get_nvr_info(cam.nvr)
        if not nvr:
            raise HTTPException(status_code=400, detail=f"NVR '{cam.nvr}' not found")

    import uuid
    scan_id = request.scan_id or str(uuid.uuid4())[:8]
    ctx = zmq.Context()
    push_socket = ctx.socket(zmq.PUSH)
    push_socket.setsockopt(zmq.SNDTIMEO, 1000)
    push_socket.setsockopt(zmq.SNDHWM, 100)
    try:
        push_socket.connect(request.push_to)
    except Exception as e:
        push_socket.close()
        raise HTTPException(status_code=400, detail=f"Cannot connect to {request.push_to}: {e}")

    stop_event = threading.Event()
    scan = {
        "scan_id": scan_id,
        "cameras": [{"nvr": c.nvr, "channel": c.channel} for c in request.cameras],
        "timeout": request.timeout,
        "push_to": request.push_to,
        "encoding": request.encoding,
        "started_at": _time.time(),
        "finished_at": None,
        "stop_event": stop_event,
        "push_socket": push_socket,
        "status": "starting",
        "stats": {
            "frames_pushed": 0,
            "frames_failed": 0,
            "bytes_pushed": 0,
            "cycles_completed": 0,
            "error": None
        }
    }

    with tag_scan_lock:
        tag_scan_state["active_scan"] = scan

    coord = threading.Thread(target=_tag_scan_coordinator, args=(scan,), daemon=True)
    coord.start()

    logger.info(f"Tag scan {scan_id} started: {len(request.cameras)} cameras, {request.timeout}s, push_to={request.push_to}")

    return {
        "scan_id": scan_id,
        "status": "starting",
        "cameras": len(request.cameras),
        "timeout": request.timeout,
        "push_to": request.push_to
    }

@app.get("/api/tag-scan/{scan_id}/status")
def get_tag_scan_status(scan_id: str):
    """Get status of a tag scan (active or recently completed)."""
    now = _time.time()

    with tag_scan_lock:
        active = tag_scan_state["active_scan"]
        if active and active["scan_id"] == scan_id:
            elapsed = now - active["started_at"]
            return {
                "scan_id": scan_id,
                "status": active["status"],
                "elapsed_sec": round(elapsed, 1),
                "remaining_sec": round(max(0, active["timeout"] - elapsed), 1),
                "stats": active["stats"]
            }

        for entry in reversed(tag_scan_state["history"]):
            if entry["scan_id"] == scan_id:
                return {
                    "scan_id": scan_id,
                    "status": entry["status"],
                    "elapsed_sec": round(entry["finished_at"] - entry["started_at"], 1),
                    "remaining_sec": 0,
                    "stats": entry["stats"]
                }

    raise HTTPException(status_code=404, detail=f"Scan '{scan_id}' not found")

@app.post("/api/tag-scan/{scan_id}/stop")
def stop_tag_scan(scan_id: str):
    """Stop an active tag scan early."""
    with tag_scan_lock:
        active = tag_scan_state["active_scan"]
        if not active or active["scan_id"] != scan_id:
            raise HTTPException(status_code=404, detail=f"No active scan '{scan_id}'")
        active["stop_event"].set()
        active["status"] = "stopping"

    return {"scan_id": scan_id, "message": "Stop requested"}

# ============================================================================
# NVR CHANNEL PROBE
# ============================================================================







@app.get("/probe")
def probe_redirect():
    """Probe moved to nvr-service (port 7999)"""
    return RedirectResponse(url="http://localhost:7999/probe", status_code=302)




def start_zmq_server():
    """Start ZMQ servers in background threads"""
    # REP server (backward compatible, serial)
    thread = threading.Thread(target=zmq_frame_handler, daemon=True)
    thread.start()
    logger.info("ZMQ REP server thread started")

    # ROUTER server (async, parallel)
    async_thread = threading.Thread(target=zmq_async_handler, daemon=True)
    async_thread.start()
    logger.info("ZMQ ROUTER server thread started")

# ============================================================================
# TEST UI
# ============================================================================

# Mount static files for test UI
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/test")
def test_redirect():
    """Generic test entry point - redirects to service-specific page"""
    return RedirectResponse(url="/camera-viewer", status_code=302)

@app.get("/camera-viewer", response_class=HTMLResponse)
def camera_viewer():
    """Camera viewer UI for testing feeds and thumbnails"""
    test_html = STATIC_DIR / "test.html"
    if test_html.exists():
        return test_html.read_text()
    else:
        return "<h1>Camera Viewer not found</h1><p>Create static/test.html</p>"


@app.get("/cam-debug", response_class=HTMLResponse)
def cam_debug_page():
    """Single-cam debug UI — exercises every M5CamServer/mediator endpoint
    we added (frame poll, /version, /health, mediator status, /duplex/enable,
    /command, /ota streaming). Pass ?ip=<host[:port]> to target a specific
    cam; defaults to 127.0.0.1:8090 (the m5camserver-emu default)."""
    p = STATIC_DIR / "cam-debug.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return "<h1>cam-debug.html not found</h1><p>Expected at " + str(p) + "</p>"

@app.get("/monitor", response_class=HTMLResponse)
def monitor_page():
    """Server monitor UI showing worker thread activity"""
    return '''<!DOCTYPE html>
<html>
<head>
    <title>Camera Service Monitor</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; background: #1a1a2e; color: #eee; padding: 20px; }
        h1 { color: #00d4ff; margin-bottom: 20px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; }
        .panel { background: #16213e; border-radius: 8px; padding: 20px; }
        .panel h2 { color: #00d4ff; font-size: 14px; text-transform: uppercase; margin-bottom: 15px; border-bottom: 1px solid #0f3460; padding-bottom: 10px; }
        .stat { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #0f3460; }
        .stat:last-child { border-bottom: none; }
        .stat-label { color: #888; }
        .stat-value { font-weight: bold; }
        .status-idle { color: #888; }
        .status-waiting { color: #ffd700; }
        .status-capturing { color: #00ff88; }
        .status-running { color: #00ff88; }
        .active-list { margin-top: 10px; }
        .active-item { background: #0f3460; padding: 10px; border-radius: 4px; margin-bottom: 8px; font-size: 13px; }
        .active-item .id { color: #00d4ff; font-weight: bold; }
        .active-item .detail { color: #888; margin-top: 4px; }
        .active-item .elapsed { color: #ffd700; float: right; }
        .no-activity { color: #555; font-style: italic; padding: 10px 0; }
        .refresh-info { color: #555; font-size: 12px; margin-top: 20px; }
    </style>
</head>
<body>
    <h1>Camera Service Monitor</h1>
    <div class="grid">
        <div class="panel">
            <h2>ZMQ REP (Serial) :5555</h2>
            <div class="stat">
                <span class="stat-label">Status</span>
                <span class="stat-value" id="rep-status">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Completed</span>
                <span class="stat-value" id="rep-completed">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Errors</span>
                <span class="stat-value" id="rep-errors">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Current</span>
                <span class="stat-value" id="rep-current">-</span>
            </div>
        </div>
        <div class="panel">
            <h2>ZMQ ROUTER (Async) :5556</h2>
            <div class="stat">
                <span class="stat-label">Status</span>
                <span class="stat-value" id="router-status">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Pending / Active</span>
                <span class="stat-value"><span id="router-pending">-</span> / <span id="router-active">-</span></span>
            </div>
            <div class="stat">
                <span class="stat-label">Completed</span>
                <span class="stat-value" id="router-completed">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Errors</span>
                <span class="stat-value" id="router-errors">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Total Data</span>
                <span class="stat-value" id="router-bytes">-</span>
            </div>
            <div class="active-list" id="router-requests"></div>
        </div>
        <div class="panel" id="push-panel">
            <h2>PUSH :5557 (Tag Scan)</h2>
            <div class="stat">
                <span class="stat-label">Status</span>
                <span class="stat-value" id="push-status">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Scan ID</span>
                <span class="stat-value" id="push-scan-id">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Pushed / Failed</span>
                <span class="stat-value"><span id="push-pushed" style="color:#00ff88">-</span> / <span id="push-failed" style="color:#ff6b6b">-</span></span>
            </div>
            <div class="stat">
                <span class="stat-label">Throughput</span>
                <span class="stat-value" id="push-fps">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Data</span>
                <span class="stat-value" id="push-bytes">-</span>
            </div>
            <div class="stat">
                <span class="stat-label">Elapsed / Remaining</span>
                <span class="stat-value" id="push-time">-</span>
            </div>
        </div>
    </div>
    <div class="panel" style="margin-top: 20px;">
        <h2>Event Log <span id="log-count" style="color:#888; font-weight:normal;">(0)</span></h2>
        <div style="display: flex; gap: 10px; margin-bottom: 10px;">
            <label style="color:#888; font-size:12px;"><input type="checkbox" id="auto-scroll" checked> Auto-scroll</label>
            <button onclick="clearLog()" style="padding: 3px 10px; background: #0f3460; color: #ff6b6b; border: 1px solid #ff6b6b; border-radius: 4px; cursor: pointer; font-size: 11px;">Clear</button>
        </div>
        <div id="event-log" style="height: 300px; overflow-y: auto; background: #0a0a1a; border-radius: 4px; padding: 10px; font-family: monospace; font-size: 12px; line-height: 1.4;"></div>
    </div>
    <div class="refresh-info">
        Auto-refresh: 500ms
        <button onclick="resetStats()" style="margin-left: 20px; padding: 5px 15px; background: #0f3460; color: #00d4ff; border: 1px solid #00d4ff; border-radius: 4px; cursor: pointer;">Reset Stats</button>
    </div>
    <script>
        function statusClass(status) {
            return 'status-' + status.toLowerCase();
        }
        async function refresh() {
            try {
                const resp = await fetch('/api/monitor');
                const data = await resp.json();

                // REP stats
                const repStatus = document.getElementById('rep-status');
                repStatus.textContent = data.zmq_rep.status;
                repStatus.className = 'stat-value ' + statusClass(data.zmq_rep.status);
                document.getElementById('rep-completed').textContent = data.zmq_rep.completed;
                document.getElementById('rep-errors').textContent = data.zmq_rep.errors;

                if (data.zmq_rep.current) {
                    const c = data.zmq_rep.current;
                    document.getElementById('rep-current').textContent =
                        `${c.nvr} ch${c.channel} (${c.elapsed_sec}s)`;
                } else {
                    document.getElementById('rep-current').textContent = '-';
                }

                // ROUTER stats
                const routerStatus = document.getElementById('router-status');
                routerStatus.textContent = data.zmq_router.status;
                routerStatus.className = 'stat-value ' + statusClass(data.zmq_router.status);
                document.getElementById('router-pending').textContent = data.zmq_router.pending_count;
                document.getElementById('router-active').textContent = data.zmq_router.active_count;
                document.getElementById('router-completed').textContent = data.zmq_router.completed;
                document.getElementById('router-errors').textContent = data.zmq_router.errors;
                document.getElementById('router-bytes').textContent = data.zmq_router.total_bytes_mb + ' MB';

                // Pending + Active requests list
                const listEl = document.getElementById('router-requests');
                const pending = data.zmq_router.pending_requests.map(r => `
                    <div class="active-item" style="border-left: 3px solid #ffd700;">
                        <span class="id">${r.id}</span>
                        <span class="elapsed" style="color:#ffd700;">waiting ${r.waiting_sec}s</span>
                        <div class="detail">${r.nvr} ch${r.channel} via ${r.source}</div>
                    </div>
                `).join('');
                const active = data.zmq_router.active_requests.map(r => `
                    <div class="active-item" style="border-left: 3px solid #00ff88;">
                        <span class="id">${r.id}</span>
                        <span class="elapsed">${r.elapsed_sec}s</span>
                        <div class="detail">${r.nvr} ch${r.channel} via ${r.source}</div>
                    </div>
                `).join('');
                if (!pending && !active) {
                    listEl.innerHTML = '<div class="no-activity">No active requests</div>';
                } else {
                    listEl.innerHTML = pending + active;
                }

                // PUSH :5557 (Tag Scan)
                const ts = data.tag_scan;
                const pushStatus = document.getElementById('push-status');
                if (ts && ts.scan_id) {
                    const fps = ts.elapsed_sec > 0 ? (ts.stats.frames_pushed / ts.elapsed_sec).toFixed(2) : '0';
                    const mb = (ts.stats.bytes_pushed / (1024*1024)).toFixed(1);
                    pushStatus.textContent = ts.status;
                    pushStatus.className = 'stat-value status-' + (ts.active ? ts.status : 'idle');
                    document.getElementById('push-scan-id').textContent = ts.scan_id;
                    document.getElementById('push-pushed').textContent = ts.stats.frames_pushed;
                    document.getElementById('push-failed').textContent = ts.stats.frames_failed;
                    document.getElementById('push-fps').textContent = fps + ' fps';
                    document.getElementById('push-bytes').textContent = mb + ' MB';
                    document.getElementById('push-time').textContent = ts.active
                        ? ts.elapsed_sec + 's / ' + ts.remaining_sec + 's'
                        : ts.elapsed_sec + 's (done)';
                } else {
                    pushStatus.textContent = 'idle';
                    pushStatus.className = 'stat-value status-idle';
                    document.getElementById('push-scan-id').textContent = '-';
                    document.getElementById('push-pushed').textContent = '-';
                    document.getElementById('push-failed').textContent = '-';
                    document.getElementById('push-fps').textContent = '-';
                    document.getElementById('push-bytes').textContent = '-';
                    document.getElementById('push-time').textContent = '-';
                }

                // Event log
                renderEventLog(data.zmq_router.event_log || []);
            } catch (e) {
                console.error('Refresh error:', e);
            }
        }

        let lastLogSeq = 0;
        function renderEventLog(events) {
            const logEl = document.getElementById('event-log');
            const countEl = document.getElementById('log-count');
            const autoScroll = document.getElementById('auto-scroll').checked;

            countEl.textContent = `(${events.length})`;

            // Only re-render if log changed (check last seq, not length — length caps at EVENT_LOG_MAX)
            const lastSeq = events.length > 0 ? events[events.length - 1].seq : 0;
            if (lastSeq === lastLogSeq) return;
            lastLogSeq = lastSeq;

            const typeColors = {
                'RECV': '#888',
                'START': '#00d4ff',
                'DONE': '#00ff88',
                'ERROR': '#ff6b6b',
                'SUPERSEDED': '#ffa500',
                'CIRCUIT_BREAK': '#ff6b6b',
                'STALE': '#ff8c00'
            };

            const lines = events.map(e => {
                const time = new Date(e.t * 1000).toLocaleTimeString('en-US', {hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3});
                const color = typeColors[e.type] || '#888';
                let extra = '';
                if (e.type === 'DONE') {
                    const kb = Math.round((e.bytes || 0) / 1024);
                    const parts = [];
                    if (e.queue_sec != null) parts.push('q:' + e.queue_sec + 's');
                    if (e.capture_sec != null) parts.push('cap:' + e.capture_sec + 's');
                    if (e.total_sec != null) parts.push('= ' + e.total_sec + 's');
                    extra = ` <span style="color:#666">[${parts.join(' ')}, ${kb}KB]</span>`;
                }
                if (e.type === 'ERROR') {
                    const parts = [];
                    if (e.queue_sec != null) parts.push('q:' + e.queue_sec + 's');
                    if (e.capture_sec != null) parts.push('cap:' + e.capture_sec + 's');
                    if (e.total_sec != null) parts.push('= ' + e.total_sec + 's');
                    extra = ` <span style="color:#ff6b6b">[${parts.join(' ')}]</span>`;
                }
                if (e.type === 'START') {
                    extra = (e.queue_sec != null && e.queue_sec > 0.1) ? ` <span style="color:#ffd700">[queued ${e.queue_sec}s]</span>` : '';
                }
                const seq = e.seq ? `#${e.seq}` : '';
                return `<div><span style="color:#444">${seq.padStart(5)}</span> <span style="color:#555">${time}</span> <span style="color:${color};font-weight:bold;">${e.type.padEnd(5)}</span> <span style="color:#00d4ff">${e.id}</span> ${e.nvr} ch${e.ch} ${e.src}${extra}</div>`;
            });

            logEl.innerHTML = lines.join('');

            if (autoScroll) {
                logEl.scrollTop = logEl.scrollHeight;
            }
        }

        async function clearLog() {
            await fetch('/api/monitor/reset?clear_log=true', {method: 'POST'});
            document.getElementById('event-log').innerHTML = '';
            lastLogSeq = 0;
        }

        async function resetStats() {
            await fetch('/api/monitor/reset', {method: 'POST'});
        }
        refresh();
        setInterval(refresh, 500);
    </script>
</body>
</html>'''

# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    print("="*70)
    print("  Camera Capture Service")
    print("="*70)
    print("  HTTP:      http://localhost:8001")
    print("  Docs:      http://localhost:8001/docs")
    print(f"  ZMQ REP:   {ZMQ_ENDPOINT} (serial)")
    print(f"  ZMQ ROUTER:{ZMQ_ASYNC_ENDPOINT} (async)")
    print("="*70)
    print()

    # Start ZMQ frame server in background
    start_zmq_server()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8001,
        log_level="info"
    )
