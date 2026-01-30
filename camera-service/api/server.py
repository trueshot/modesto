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

@app.get("/api/nvr/{nvr_id}/channel/{channel}/frame")
def get_nvr_channel_frame(
    nvr_id: str,
    channel: int,
    source: str = Query("rtsp", description="Capture source: 'rtsp' or 'snapshot'"),
    format: str = Query("image", description="Response format: 'image' or 'base64'"),
    timeout: int = Query(8, description="Capture timeout in seconds")
):
    """
    Capture frame directly from NVR channel.

    Args:
        nvr_id: NVR ID (e.g., "nvr1", "nvr2")
        channel: Channel number (1-32)
        source: 'rtsp' (decode stream) or 'snapshot' (HTTP snapshot, UNIVIEW only)
        format: 'image' returns JPEG, 'base64' returns JSON with base64 string
        timeout: Capture timeout in seconds
    """
    nvr = get_nvr_info(nvr_id)
    if not nvr:
        raise HTTPException(status_code=404, detail=f"NVR '{nvr_id}' not found in lodge.db")

    if source == "snapshot":
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
        image_data = camera_capture.capture_frame(rtsp_url, timeout=timeout)

    if not image_data:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to capture from {nvr_id} channel {channel} via {source}"
        )

    if format == "base64":
        return {
            "nvr_id": nvr_id,
            "channel": channel,
            "source": source,
            "image": base64.b64encode(image_data).decode('utf-8'),
            "format": "jpeg"
        }
    else:
        return Response(content=image_data, media_type="image/jpeg")

@app.get("/api/nvr/{nvr_id}/channel/{channel}/info")
def get_nvr_channel_info(
    nvr_id: str,
    channel: int,
    source: str = Query("rtsp", description="Capture source: 'rtsp' or 'snapshot'")
):
    """
    Get capture info for an NVR channel (without actually capturing).
    Returns the URL and method that would be used.
    """
    nvr = get_nvr_info(nvr_id)
    if not nvr:
        raise HTTPException(status_code=404, detail=f"NVR '{nvr_id}' not found in lodge.db")

    supports_snapshot = nvr_supports_snapshot(nvr)

    if source == "snapshot":
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
EVENT_LOG_MAX = 200  # Keep last 200 events
event_seq = 0  # Global sequence number for event ordering

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

# Per-NVR connection throttling (NVRs typically handle max 2 concurrent RTSP)
NVR_MAX_CONCURRENT = 2
nvr_semaphores = {}  # nvr_ip -> threading.Semaphore
nvr_semaphores_lock = threading.Lock()

def get_nvr_semaphore(nvr_ip: str) -> threading.Semaphore:
    """Get or create semaphore for an NVR IP"""
    with nvr_semaphores_lock:
        if nvr_ip not in nvr_semaphores:
            nvr_semaphores[nvr_ip] = threading.Semaphore(NVR_MAX_CONCURRENT)
        return nvr_semaphores[nvr_ip]

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
            if source == "snapshot":
                image_data = capture_snapshot(nvr, channel)
            else:  # rtsp
                rtsp_url = build_rtsp_url(nvr, channel)
                image_data = camera_capture.capture_frame(rtsp_url, timeout=8)

            if image_data:
                with worker_state_lock:
                    worker_state["zmq_rep"]["completed"] += 1
                socket.send(image_data)
            else:
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
    from concurrent.futures import ThreadPoolExecutor
    from collections import defaultdict

    context = zmq.Context()
    socket = context.socket(zmq.ROUTER)
    socket.bind(ZMQ_ASYNC_ENDPOINT)
    logger.info(f"ZMQ async (ROUTER) server listening on {ZMQ_ASYNC_ENDPOINT}")

    # Thread pool for parallel captures
    executor = ThreadPoolExecutor(max_workers=8)  # More workers since they don't block

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

    def process_request(identity: bytes, request_id: str, nvr_id: str, channel: int, source: str, nvr_ip: str):
        """Process single frame request, return result tuple"""
        try:
            # Mark as actively processing
            with worker_state_lock:
                if request_id in worker_state["zmq_router"]["pending_requests"]:
                    req_info = worker_state["zmq_router"]["pending_requests"].pop(request_id)
                    req_info["processing_started_at"] = _time.time()
                    worker_state["zmq_router"]["active_requests"][request_id] = req_info

            nvr = get_nvr_info(nvr_id)
            if not nvr:
                return (identity, request_id, b"", True, nvr_ip)

            if source == "snapshot":
                image_data = capture_snapshot(nvr, channel)
            else:
                rtsp_url = build_rtsp_url(nvr, channel)
                image_data = camera_capture.capture_frame(rtsp_url, timeout=8)

            return (identity, request_id, image_data or b"", not image_data, nvr_ip)
        except Exception as e:
            logger.error(f"Async capture error: {e}")
            return (identity, request_id, b"", True, nvr_ip)

    in_flight = {}  # request_id -> future

    while True:
        try:
            # Update status
            with worker_state_lock:
                has_work = bool(in_flight) or bool(pending_queue)
                worker_state["zmq_router"]["status"] = "running" if has_work else "idle"

            # Non-blocking receive to check for new requests
            try:
                frames = socket.recv_multipart(zmq.NOBLOCK)
                identity = frames[0]
                msg = frames[-1].decode('utf-8')
                req = json.loads(msg)

                request_id = req.get("id", "unknown")
                nvr_id = req.get("nvr", "nvr1")
                channel = req.get("channel", 1)
                source = req.get("source", "rtsp")

                logger.info(f"ZMQ async request {request_id}: {nvr_id} ch{channel} via {source}")

                # Log event: received
                recv_at = _time.time()
                log_event("RECV", request_id, nvr_id, channel, source)

                # Add to pending queue
                pending_queue[request_id] = {
                    "identity": identity,
                    "nvr": nvr_id,
                    "channel": channel,
                    "source": source,
                    "recv_at": recv_at,
                    "queued_at": recv_at  # Alias for backward compat
                }

                # Track in state
                with worker_state_lock:
                    worker_state["zmq_router"]["pending_requests"][request_id] = {
                        "nvr": nvr_id,
                        "channel": channel,
                        "source": source,
                        "recv_at": recv_at,
                        "queued_at": recv_at
                    }

            except zmq.Again:
                pass

            # Schedule: find requests for NVRs with available capacity
            to_submit = []
            for req_id, req in list(pending_queue.items()):
                nvr_id = req["nvr"]
                nvr_ip = get_nvr_ip(nvr_id)
                if not nvr_ip:
                    # Invalid NVR, submit anyway to return error
                    to_submit.append((req_id, req, None))
                    continue

                with nvr_active_lock:
                    if nvr_active_count[nvr_ip] < NVR_MAX_CONCURRENT:
                        nvr_active_count[nvr_ip] += 1
                        to_submit.append((req_id, req, nvr_ip))

            # Submit scheduled requests
            for req_id, req, nvr_ip in to_submit:
                del pending_queue[req_id]
                started_at = _time.time()
                queue_time = round(started_at - req.get("recv_at", started_at), 3)
                logger.info(f"ZMQ async STARTED {req_id}: {req['nvr']} ch{req['channel']} (nvr_ip={nvr_ip}, queued={queue_time}s)")
                log_event("START", req_id, req["nvr"], req["channel"], req["source"], {"nvr_ip": nvr_ip, "queue_sec": queue_time})

                # Store started_at in state for capture_time calculation
                with worker_state_lock:
                    if req_id in worker_state["zmq_router"]["pending_requests"]:
                        worker_state["zmq_router"]["pending_requests"][req_id]["started_at"] = started_at
                future = executor.submit(
                    process_request,
                    req["identity"], req_id, req["nvr"], req["channel"], req["source"],
                    nvr_ip or ""
                )
                in_flight[req_id] = future

            # Check for completed futures
            completed = []
            for req_id, future in in_flight.items():
                if future.done():
                    identity, request_id, image_data, is_error, nvr_ip = future.result()
                    done_at = _time.time()

                    # Get timing info from active_requests (moved there by process_request)
                    with worker_state_lock:
                        req_info = worker_state["zmq_router"]["active_requests"].get(req_id, {})
                        if not req_info:
                            req_info = worker_state["zmq_router"]["pending_requests"].get(req_id, {})
                        nvr_id = req_info.get("nvr", "?")
                        channel = req_info.get("channel", 0)
                        source = req_info.get("source", "?")
                        recv_at = req_info.get("recv_at", done_at)
                        started_at = req_info.get("started_at", recv_at)

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
                    socket.send_multipart([identity, b"", request_id.encode('utf-8'), image_data, metadata])
                    logger.info(f"ZMQ async response {request_id}: {len(image_data)} bytes (q:{queue_ms}ms cap:{capture_ms}ms)")
                    completed.append((req_id, nvr_ip))

                    # Log event
                    queue_sec = queue_ms / 1000
                    capture_sec = capture_ms / 1000
                    total_sec = total_ms / 1000
                    if is_error:
                        log_event("ERROR", request_id, nvr_id, channel, source, {
                            "queue_sec": queue_sec, "capture_sec": capture_sec, "total_sec": total_sec
                        })
                    else:
                        log_event("DONE", request_id, nvr_id, channel, source, {
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
                if nvr_ip:
                    with nvr_active_lock:
                        nvr_active_count[nvr_ip] = max(0, nvr_active_count[nvr_ip] - 1)

            # Small sleep to avoid busy-spinning
            if not in_flight and not pending_queue:
                _time.sleep(0.001)

        except Exception as e:
            logger.error(f"ZMQ async handler error: {e}")

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
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
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

                // Event log
                renderEventLog(data.zmq_router.event_log || []);
            } catch (e) {
                console.error('Refresh error:', e);
            }
        }

        let lastLogLength = 0;
        function renderEventLog(events) {
            const logEl = document.getElementById('event-log');
            const countEl = document.getElementById('log-count');
            const autoScroll = document.getElementById('auto-scroll').checked;

            countEl.textContent = `(${events.length})`;

            // Only re-render if log changed
            if (events.length === lastLogLength) return;
            lastLogLength = events.length;

            const typeColors = {
                'RECV': '#888',
                'START': '#00d4ff',
                'DONE': '#00ff88',
                'ERROR': '#ff6b6b'
            };

            const lines = events.map(e => {
                const time = new Date(e.t * 1000).toLocaleTimeString('en-US', {hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3});
                const color = typeColors[e.type] || '#888';
                let extra = '';
                if (e.type === 'DONE') {
                    const kb = Math.round(e.bytes / 1024);
                    extra = ` <span style="color:#666">[q:${e.queue_sec}s cap:${e.capture_sec}s = ${e.total_sec}s, ${kb}KB]</span>`;
                }
                if (e.type === 'ERROR') {
                    extra = ` <span style="color:#ff6b6b">[q:${e.queue_sec}s cap:${e.capture_sec}s = ${e.total_sec}s]</span>`;
                }
                if (e.type === 'START') {
                    extra = e.queue_sec > 0.1 ? ` <span style="color:#ffd700">[queued ${e.queue_sec}s]</span>` : '';
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
            lastLogLength = 0;
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
