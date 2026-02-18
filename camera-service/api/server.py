#!/usr/bin/env python3
"""
Camera Capture Service
FastAPI server for warehouse camera access and image delivery

Port: 8001
Provides camera images to agents, web apps, and other services
"""

import sys
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
from fastapi import FastAPI, HTTPException, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
import uvicorn

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from capture import CameraCapture
from cache import ImageCache
from scanner import NVRScanner
from playback import capture_playback_frame

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
    return {"message": "Image cache cleared"}

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

    # Circuit breaker check
    if is_nvr_down(nvr['ip']):
        raise HTTPException(
            status_code=503,
            detail=f"NVR '{nvr_id}' ({nvr['ip']}) is unreachable (circuit breaker tripped)"
        )

    if source == "playback":
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
        if not nvr_supports_snapshot(nvr):
            raise HTTPException(
                status_code=400,
                detail=f"NVR '{nvr_id}' ({nvr.get('brand')}) does not support HTTP snapshot"
            )
        logger.info(f"Snapshot from {nvr_id} ch{channel}: {nvr['ip']}")
        image_data = capture_snapshot(nvr, channel)
    else:
        rtsp_url = build_rtsp_url(nvr, channel)
        logger.info(f"RTSP capture from {nvr_id} ch{channel}: {nvr['ip']}")
        image_data = camera_capture.capture_frame(rtsp_url, timeout=timeout, fmt=encoding)

    if not image_data:
        mark_nvr_down_if_unreachable(nvr['ip'])
        detail = f"Failed to capture from {nvr_id} channel {channel} via {source}"
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
            "method": "RTSP stream capture via OpenCV/FFmpeg",
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
nvr_semaphores = {}  # nvr_ip -> threading.Semaphore
nvr_semaphores_lock = threading.Lock()

def get_nvr_semaphore(nvr_ip: str) -> threading.Semaphore:
    """Get or create semaphore for an NVR IP"""
    with nvr_semaphores_lock:
        if nvr_ip not in nvr_semaphores:
            nvr_semaphores[nvr_ip] = threading.Semaphore(NVR_MAX_CONCURRENT)
        return nvr_semaphores[nvr_ip]

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

def _capture_subprocess(nvr_id: str, channel: int, source: str, nvr_ip: str, timestamp=None, cooldown: float = 0.0, fmt: str = "jpeg") -> bytes:
    """
    Capture a frame in a separate process (own GIL, no contention).
    Called via ProcessPoolExecutor from zmq_async_handler.
    Returns encoded image bytes or empty bytes on failure.
    """
    nvr = get_nvr_info(nvr_id)
    if not nvr:
        return b""

    image_data = None
    if source == "playback":
        if not timestamp or not nvr_supports_playback(nvr):
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
    elif source == "snapshot":
        image_data = capture_snapshot(nvr, channel)
    else:
        rtsp_url = build_rtsp_url(nvr, channel)
        image_data = camera_capture.capture_frame(rtsp_url, timeout=8, fmt=fmt)

    if cooldown > 0:
        _time.sleep(cooldown)

    return image_data or b""


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

            # Circuit breaker check
            if is_nvr_down(nvr['ip']):
                logger.info(f"ZMQ REP: circuit breaker skipping {nvr_id} ch{channel}")
                with worker_state_lock:
                    worker_state["zmq_rep"]["errors"] += 1
                socket.send(b"")
                continue

            # Capture based on source
            if source == "playback":
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
            elif source == "snapshot":
                image_data = capture_snapshot(nvr, channel)
            else:  # rtsp
                rtsp_url = build_rtsp_url(nvr, channel)
                image_data = camera_capture.capture_frame(rtsp_url, timeout=8)

            if image_data:
                with worker_state_lock:
                    worker_state["zmq_rep"]["completed"] += 1
                socket.send(image_data)
            else:
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

    Protocol:
      Request: JSON with {id, nvr, channel, source}
      Response: [request_id bytes, frame_data bytes]

    Client should use DEALER socket.
    """
    from concurrent.futures import ProcessPoolExecutor
    from collections import defaultdict

    context = zmq.Context()
    socket = context.socket(zmq.ROUTER)
    socket.bind(ZMQ_ASYNC_ENDPOINT)
    logger.info(f"ZMQ async (ROUTER) server listening on {ZMQ_ASYNC_ENDPOINT}")

    # Poller for blocking recv with timeout (avoids GIL-hungry busy spin)
    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    # Process pool for parallel captures — each worker has its own GIL,
    # so HEVC decode on NVR2 doesn't block H.264 decode on NVR1
    executor = ProcessPoolExecutor(max_workers=8)

    # Pending queue: requests waiting for NVR capacity
    # {request_id -> {identity, nvr, channel, source, queued_at}}
    pending_queue = {}

    # Track active connections per NVR IP
    nvr_active_count = defaultdict(int)  # nvr_ip -> count
    nvr_active_lock = threading.Lock()

    # Map nvr_id -> nvr_ip for tracking
    nvr_ip_cache = {}

    def get_nvr_ip(nvr_id: str) -> Optional[str]:
        """Get NVR IP, with caching"""
        if nvr_id not in nvr_ip_cache:
            nvr = get_nvr_info(nvr_id)
            if nvr:
                nvr_ip_cache[nvr_id] = nvr['ip']
        return nvr_ip_cache.get(nvr_id)

    in_flight = {}  # request_id -> future
    in_flight_meta = {}  # request_id -> {identity, nvr_ip, nvr_id, channel, source, recv_at, started_at}
    # Index: (nvr_id, channel) -> request_id for dedup
    pending_by_channel = {}  # (nvr, ch) -> req_id currently in pending_queue

    while True:
        try:
            # Update status
            with worker_state_lock:
                has_work = bool(in_flight) or bool(pending_queue)
                worker_state["zmq_router"]["status"] = "running" if has_work else "idle"

            # Poll for new requests (blocks up to 50ms, releasing GIL)
            poll_timeout = 1 if (in_flight or pending_queue) else 50  # responsive when busy, relaxed when idle
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

                # Log event: received
                recv_at = _time.time()
                log_event("RECV", request_id, nvr_id, channel, source)

                # Dedup: if there's already a PENDING request for same NVR+channel,
                # keep queue position but update to respond to the new requester
                ch_key = (nvr_id, channel)
                old_req_id = pending_by_channel.get(ch_key)
                if old_req_id and old_req_id in pending_queue:
                    old_req = pending_queue[old_req_id]  # don't pop — keep queue position
                    # Send SUPERSEDED to old requester (frontend already gave up on that pass)
                    metadata = json.dumps({
                        "queue_ms": 0, "capture_ms": 0,
                        "total_ms": int((recv_at - old_req.get("recv_at", recv_at)) * 1000),
                        "bytes": 0, "error": True, "superseded": True
                    }).encode('utf-8')
                    socket.send_multipart([old_req["identity"], b"", old_req_id.encode('utf-8'), b"", metadata])
                    log_event("SUPERSEDED", old_req_id, nvr_id, channel, source)
                    logger.info(f"ZMQ async SUPERSEDED {old_req_id}: replaced by {request_id} (kept queue position)")
                    # Update entry in place — new requester takes this queue slot
                    old_req["identity"] = identity
                    old_req["response_id"] = request_id
                    old_req["recv_at"] = recv_at
                    old_req["timestamp"] = req_timestamp
                    with worker_state_lock:
                        worker_state["zmq_router"]["pending_requests"].pop(old_req_id, None)
                        worker_state["zmq_router"]["pending_requests"][request_id] = {
                            "nvr": nvr_id, "channel": channel, "source": source,
                            "recv_at": recv_at, "queued_at": old_req["queued_at"]
                        }
                        worker_state["zmq_router"]["errors"] += 1
                    # pending_by_channel stays pointing to old_req_id (the pending_queue key)
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

                    # Track in state
                    with worker_state_lock:
                        worker_state["zmq_router"]["pending_requests"][request_id] = {
                            "nvr": nvr_id,
                            "channel": channel,
                            "source": source,
                            "recv_at": recv_at,
                            "queued_at": recv_at
                        }

            # Schedule: find requests for NVRs with available capacity
            to_submit = []
            to_reject = []  # Requests to NVRs with tripped circuit breaker
            for req_id, req in list(pending_queue.items()):
                nvr_id = req["nvr"]
                nvr_ip = get_nvr_ip(nvr_id)
                if not nvr_ip:
                    # Invalid NVR, submit anyway to return error
                    to_submit.append((req_id, req, None))
                    continue

                # Circuit breaker: reject immediately if NVR is down
                if is_nvr_down(nvr_ip):
                    to_reject.append((req_id, req, nvr_ip))
                    continue

                with nvr_active_lock:
                    if nvr_active_count[nvr_ip] < NVR_MAX_CONCURRENT:
                        nvr_active_count[nvr_ip] += 1
                        to_submit.append((req_id, req, nvr_ip))

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

                metadata = json.dumps({
                    "queue_ms": 0, "capture_ms": 0, "total_ms": int((now - req.get("recv_at", now)) * 1000),
                    "bytes": 0, "error": True, "circuit_breaker": True
                }).encode('utf-8')
                socket.send_multipart([req["identity"], b"", response_id.encode('utf-8'), b"", metadata])

                with worker_state_lock:
                    worker_state["zmq_router"]["pending_requests"].pop(response_id, None)
                    worker_state["zmq_router"]["errors"] += 1

            # Submit scheduled requests
            for req_id, req, nvr_ip in to_submit:
                del pending_queue[req_id]
                ch_key = (req["nvr"], req["channel"])
                if pending_by_channel.get(ch_key) == req_id:
                    del pending_by_channel[ch_key]
                response_id = req.get("response_id", req_id)
                started_at = _time.time()
                recv_at = req.get("recv_at", started_at)
                queue_time = round(started_at - recv_at, 3)
                logger.info(f"ZMQ async STARTED {response_id}: {req['nvr']} ch{req['channel']} (nvr_ip={nvr_ip}, queued={queue_time}s)")
                log_event("START", response_id, req["nvr"], req["channel"], req["source"], {"nvr_ip": nvr_ip, "queue_sec": queue_time})

                # Move from pending to active in worker_state
                with worker_state_lock:
                    req_info = worker_state["zmq_router"]["pending_requests"].pop(response_id, None)
                    if req_info:
                        req_info["processing_started_at"] = started_at
                    else:
                        req_info = {"nvr": req["nvr"], "channel": req["channel"], "source": req["source"]}
                    req_info["started_at"] = started_at
                    worker_state["zmq_router"]["active_requests"][response_id] = req_info

                # Track metadata in main process (identity bytes can't cross process boundary)
                in_flight_meta[response_id] = {
                    "identity": req["identity"],
                    "nvr_ip": nvr_ip,
                    "nvr_id": req["nvr"],
                    "channel": req["channel"],
                    "source": req["source"],
                    "recv_at": recv_at,
                    "started_at": started_at,
                }

                future = executor.submit(
                    _capture_subprocess,
                    req["nvr"], req["channel"], req["source"],
                    nvr_ip or "", req.get("timestamp"), CAPTURE_COOLDOWN
                )
                in_flight[response_id] = future

            # Check for completed futures
            completed = []
            for req_id, future in in_flight.items():
                if future.done():
                    meta = in_flight_meta.get(req_id, {})
                    identity = meta.get("identity", b"")
                    nvr_ip = meta.get("nvr_ip", "")
                    nvr_id = meta.get("nvr_id", "?")
                    channel = meta.get("channel", 0)
                    source = meta.get("source", "?")
                    recv_at = meta.get("recv_at", _time.time())
                    started_at = meta.get("started_at", recv_at)
                    done_at = _time.time()

                    try:
                        image_data = future.result()
                    except Exception as e:
                        logger.error(f"Capture subprocess error for {req_id}: {e}")
                        image_data = b""

                    is_error = len(image_data) == 0

                    # Circuit breaker check (runs in main process)
                    if is_error and nvr_ip:
                        mark_nvr_down_if_unreachable(nvr_ip)

                    # Calculate timing breakdown (in ms for client convenience)
                    queue_ms = int((started_at - recv_at) * 1000)
                    capture_ms = int((done_at - started_at) * 1000)
                    total_ms = int((done_at - recv_at) * 1000)

                    # Build metadata JSON for 5th frame
                    metadata = json.dumps({
                        "queue_ms": queue_ms,
                        "capture_ms": capture_ms,
                        "total_ms": total_ms,
                        "bytes": len(image_data),
                        "error": is_error
                    }).encode('utf-8')

                    # Send response: [identity, empty, request_id, jpeg_bytes, metadata_json]
                    socket.send_multipart([identity, b"", req_id.encode('utf-8'), image_data, metadata])
                    logger.info(f"ZMQ async response {req_id}: {len(image_data)} bytes (q:{queue_ms}ms cap:{capture_ms}ms)")
                    completed.append((req_id, nvr_ip))

                    # Log event
                    queue_sec = queue_ms / 1000
                    capture_sec = capture_ms / 1000
                    total_sec = total_ms / 1000
                    if is_error:
                        log_event("ERROR", req_id, nvr_id, channel, source, {
                            "queue_sec": queue_sec, "capture_sec": capture_sec, "total_sec": total_sec
                        })
                    else:
                        log_event("DONE", req_id, nvr_id, channel, source, {
                            "bytes": len(image_data), "queue_sec": queue_sec, "capture_sec": capture_sec, "total_sec": total_sec
                        })

                    # Update stats
                    with worker_state_lock:
                        if req_id in worker_state["zmq_router"]["active_requests"]:
                            del worker_state["zmq_router"]["active_requests"][req_id]
                        if is_error:
                            worker_state["zmq_router"]["errors"] += 1
                        else:
                            worker_state["zmq_router"]["completed"] += 1
                            worker_state["zmq_router"]["total_bytes"] += len(image_data)

            # Release NVR slots and remove from in_flight
            for req_id, nvr_ip in completed:
                del in_flight[req_id]
                in_flight_meta.pop(req_id, None)
                if nvr_ip:
                    with nvr_active_lock:
                        nvr_active_count[nvr_ip] = max(0, nvr_active_count[nvr_ip] - 1)

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

            req_id = f"ts-{uuid.uuid4().hex[:8]}"
            log_event("START", req_id, nvr_id, channel, "tagscan")
            cap_start = _time.time()

            # Submit to ProcessPoolExecutor — no GIL contention across NVRs
            future = executor.submit(
                _capture_subprocess, nvr_id, channel, "rtsp", nvr_ip, None, 0.0, scan.get("encoding", "jpeg")
            )
            try:
                jpeg = future.result(timeout=10)
            except Exception as e:
                logger.error(f"Tag scan capture error {nvr_id} ch{channel}: {e}")
                jpeg = b""

            cap_sec = round(_time.time() - cap_start, 3)

            # Re-enqueue channel for next round
            ch_queue.put(channel)

            if not jpeg:
                mark_nvr_down_if_unreachable(nvr_ip)
                log_event("ERROR", req_id, nvr_id, channel, "tagscan", {"capture_sec": cap_sec})
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
                log_event("DONE", req_id, nvr_id, channel, "tagscan", {"bytes": len(jpeg), "capture_sec": cap_sec})
                with tag_scan_lock:
                    scan["stats"]["frames_pushed"] += 1
                    scan["stats"]["bytes_pushed"] += len(jpeg)
            except zmq.Again:
                logger.warning(f"Tag scan: ZMQ send buffer full, dropping {nvr_id} ch{channel}")
                log_event("ERROR", req_id, nvr_id, channel, "tagscan", {"capture_sec": cap_sec, "drop": True})
                with tag_scan_lock:
                    scan["stats"]["frames_failed"] += 1
            except Exception as e:
                logger.error(f"Tag scan: ZMQ send error: {e}")
                log_event("ERROR", req_id, nvr_id, channel, "tagscan", {"capture_sec": cap_sec})
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

probe_state = {}  # nvr_id -> active probe dict
probe_history = []  # last 10 completed probes
probe_lock = threading.Lock()
PROBE_HISTORY_MAX = 10

def _probe_channel_subprocess(nvr_id: str, channel: int, nvr_ip: str) -> dict:
    """
    Probe a single channel in a subprocess (own GIL).
    Returns {channel, success, std, bytes, resolution, error}.
    """
    nvr = get_nvr_info(nvr_id)
    if not nvr:
        return {"channel": channel, "success": False, "std": 0, "bytes": 0, "resolution": None, "error": "nvr_not_found"}

    rtsp_url = build_rtsp_url(nvr, channel)

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
    t.join(timeout=10)

    if t.is_alive():
        c = cap_holder[0]
        if c is not None:
            try:
                c.release()
            except:
                pass
        return {"channel": channel, "success": False, "std": 0, "bytes": 0, "resolution": None, "error": "timeout"}

    try:
        r = result_q.get_nowait()
        r["channel"] = channel
        return r
    except:
        return {"channel": channel, "success": False, "std": 0, "bytes": 0, "resolution": None, "error": "no_result"}


def _probe_coordinator(probe: dict):
    """Run probe: round-robin channels across attempts, update DB when done."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    nvr_id = probe["nvr_id"]
    attempts = probe["attempts"]
    channels = probe["channels"]
    stop_event = probe["stop_event"]

    executor = None
    try:
        with probe_lock:
            probe["status"] = "running"

        executor = ProcessPoolExecutor(max_workers=NVR_MAX_CONCURRENT)
        nvr = get_nvr_info(nvr_id)
        nvr_ip = nvr["ip"] if nvr else ""

        # Round-robin: attempt 1 for all channels, then attempt 2, etc.
        for attempt_num in range(1, attempts + 1):
            if stop_event.is_set():
                break

            futures = {}
            for ch in channels:
                if stop_event.is_set():
                    break
                f = executor.submit(_probe_channel_subprocess, nvr_id, ch, nvr_ip)
                futures[f] = ch

            for f in as_completed(futures):
                ch = futures[f]
                try:
                    result = f.result(timeout=15)
                except Exception as e:
                    result = {"channel": ch, "success": False, "std": 0, "bytes": 0, "resolution": None, "error": str(e)}

                ch_key = str(ch)
                with probe_lock:
                    if ch_key not in probe["results"]:
                        probe["results"][ch_key] = {
                            "channel": ch,
                            "attempts": [],
                            "verdict": None,
                            "thumbnail": None,
                            "resolution": None
                        }
                    ch_result = probe["results"][ch_key]
                    ch_result["attempts"].append({
                        "attempt": attempt_num,
                        "success": result["success"],
                        "std": result.get("std", 0),
                        "bytes": result.get("bytes", 0),
                        "error": result.get("error")
                    })
                    if result["success"]:
                        if result.get("resolution"):
                            ch_result["resolution"] = result["resolution"]
                        if ch_result["thumbnail"] is None and result.get("jpeg"):
                            ch_result["thumbnail"] = base64.b64encode(result["jpeg"]).decode("utf-8")
                    probe["stats"]["tested"] += 1

            with probe_lock:
                probe["stats"]["attempts_completed"] = attempt_num

        # Compute verdicts
        with probe_lock:
            for ch_key, ch_result in probe["results"].items():
                successes = sum(1 for a in ch_result["attempts"] if a["success"])
                if successes > 0:
                    ch_result["verdict"] = "active"
                    probe["stats"]["active"] += 1
                else:
                    ch_result["verdict"] = "inactive"
                    probe["stats"]["inactive"] += 1

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

        # Check if channel exists
        cursor.execute("SELECT id, status FROM channels WHERE id = ?", (channel_id,))
        row = cursor.fetchone()

        new_status = verdict if verdict else "inactive"

        if row:
            # Update existing
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
            # New channel — build RTSP path
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
    import subprocess
    try:
        subprocess.run(
            ["node", "warehouses/snapshot-database.js", "lodge"],
            cwd=str(Path(__file__).parent.parent.parent),
            capture_output=True, timeout=10
        )
        logger.info(f"Probe {probe['probe_id']}: DB snapshot taken")
    except Exception as e:
        logger.warning(f"Probe {probe['probe_id']}: snapshot failed: {e}")


class ProbeRequest(BaseModel):
    attempts: int = 3
    max_channels: Optional[int] = None  # None = use NVR max_channels from DB


@app.post("/api/nvr/{nvr_id}/probe")
def start_probe(nvr_id: str, request: ProbeRequest):
    """
    Start NVR channel probe. Tests all channels and updates lodge.db.
    Requires exclusive NVR access — 409 if tag scan or ROUTER requests active.
    """
    nvr = get_nvr_info(nvr_id)
    if not nvr:
        raise HTTPException(status_code=404, detail=f"NVR '{nvr_id}' not found")

    # Exclusive access checks
    with probe_lock:
        if nvr_id in probe_state:
            active = probe_state[nvr_id]
            raise HTTPException(status_code=409, detail=f"Probe '{active['probe_id']}' already active for {nvr_id}")

    with tag_scan_lock:
        if tag_scan_state["active_scan"] is not None:
            raise HTTPException(status_code=409, detail="Tag scan is active — probe needs exclusive NVR access")

    with worker_state_lock:
        if worker_state["zmq_router"]["active_requests"]:
            raise HTTPException(status_code=409, detail="ROUTER has active requests — probe needs exclusive NVR access")

    max_ch = request.max_channels or nvr.get("max_channels") or 16
    channels = list(range(1, max_ch + 1))

    import uuid
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
        "results": {},  # ch_num_str -> {channel, attempts[], verdict, thumbnail, resolution}
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
        # Check active
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

        # Check history
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
        .controls { display: flex; gap: 15px; align-items: center; margin-bottom: 20px; }
        select, button { padding: 8px 16px; border-radius: 4px; border: 1px solid #0f3460; background: #16213e; color: #eee; font-size: 14px; cursor: pointer; }
        button:hover { background: #0f3460; }
        button.start { border-color: #00ff88; color: #00ff88; }
        button.stop { border-color: #ff6b6b; color: #ff6b6b; }
        button:disabled { opacity: 0.4; cursor: not-allowed; }
        .progress { background: #0f3460; border-radius: 4px; height: 24px; margin-bottom: 20px; overflow: hidden; position: relative; }
        .progress-bar { height: 100%; background: linear-gradient(90deg, #00d4ff, #00ff88); transition: width 0.3s; }
        .progress-text { position: absolute; top: 0; left: 0; right: 0; text-align: center; line-height: 24px; font-size: 12px; color: #fff; }
        .stats { display: flex; gap: 20px; margin-bottom: 20px; font-size: 13px; }
        .stats span { color: #888; }
        .stats .val { color: #eee; font-weight: bold; }
        .stats .active { color: #00ff88; }
        .stats .inactive { color: #ff6b6b; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
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
        .card .verdict { font-size: 11px; padding: 2px 6px; border-radius: 3px; margin-left: 6px; }
        .verdict.active { background: #00ff8833; color: #00ff88; }
        .verdict.inactive { background: #ff6b6b33; color: #ff6b6b; }
        .verdict.new { background: #ffd70033; color: #ffd700; }
        .card .attempts { font-size: 11px; color: #888; margin-top: 4px; }
        .card .attempts .ok { color: #00ff88; }
        .card .attempts .fail { color: #ff6b6b; }
        .card .res { font-size: 11px; color: #555; }
        .status-msg { color: #888; font-style: italic; margin: 20px 0; }
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
            sel.innerHTML = data.nvrs.map(n => `<option value="${n.id}">${n.id} (${n.ip}) — ${n.brand}</option>`).join('');
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

                // Build placeholder cards
                const grid = document.getElementById('channel-grid');
                for (let ch = 1; ch <= data.channels; ch++) {
                    grid.innerHTML += `<div class="card pending" id="card-${ch}">
                        <div class="placeholder">ch${ch}</div>
                        <div class="info">
                            <span class="ch">ch${ch}</span>
                            <span class="verdict" id="verdict-${ch}"></span>
                            <div class="attempts" id="attempts-${ch}"></div>
                            <div class="res" id="res-${ch}"></div>
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

                // Update cards
                for (const [chKey, r] of Object.entries(data.results)) {
                    const ch = r.channel;
                    const card = document.getElementById(`card-${ch}`);
                    if (!card) continue;

                    // Verdict
                    card.className = 'card ' + (r.verdict || 'testing');
                    const vEl = document.getElementById(`verdict-${ch}`);
                    if (r.verdict) {
                        vEl.textContent = r.verdict;
                        vEl.className = 'verdict ' + r.verdict;
                    }

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
                    aEl.innerHTML = r.attempts.map(a =>
                        `<span class="${a.success ? 'ok' : 'fail'}">#${a.attempt}:${a.success ? a.std : 'x'}</span>`
                    ).join(' ');

                    // Resolution
                    if (r.resolution) {
                        document.getElementById(`res-${ch}`).textContent = r.resolution;
                    }
                }

                // Done?
                if (['completed', 'stopped', 'error'].includes(data.status)) {
                    clearInterval(refreshInterval);
                    refreshInterval = null;
                    document.getElementById('start-btn').disabled = false;
                    document.getElementById('stop-btn').disabled = true;
                    document.getElementById('status-msg').textContent =
                        `Probe ${data.status}. ${stats.active} active, ${stats.inactive} inactive channels. DB updated.`;
                }
            } catch (e) {
                console.error('Poll error:', e);
            }
        }

        loadNVRs();
    </script>
</body>
</html>'''


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
                'CIRCUIT_BREAK': '#ff6b6b'
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
