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
        config = camera_capture.load_config(warehouse_id)

        # Build NVR list
        nvrs_list = []
        if 'nvrs' in config:
            for nvr in config['nvrs']:
                nvr_channels = [c['channel'] for c in config['channels'] if c.get('nvrId') == nvr['id']]
                # Also include channels without nvrId as nvr1
                if nvr['id'] == 'nvr1':
                    nvr_channels.extend([c['channel'] for c in config['channels'] if 'nvrId' not in c])
                nvrs_list.append({
                    'id': nvr['id'],
                    'name': f"{nvr['ip']}",
                    'channels': sorted(set(nvr_channels))
                })
        else:
            # Legacy single NVR
            nvrs_list.append({
                'id': 'nvr1',
                'name': config['nvr']['ip'],
                'channels': [c['channel'] for c in config['channels']]
            })

        # Build camera list with thumbnails
        cameras_list = []
        for channel in config['channels']:
            camera_id = channel['modelTCameraId']
            nvr_id = channel.get('nvrId', 'nvr1')
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
                'channel': channel['channel'],
                'cameraId': camera_id,
                'thumbnailUrl': f"/api/cameras/{warehouse_id}/{camera_id}/capture",
                'label': channel['modelTCameraName'],
                'location': channel.get('location', ''),
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

@app.post("/api/cameras/{facility}/scan-and-update")
def scan_and_update_facility(
    facility: str,
    quick: bool = Query(True, description="Use quick scan (common patterns only)"),
    max_channels: int = Query(32, description="Maximum channels to test"),
    preserve_modelt_info: bool = Query(True, description="Keep existing camera names/locations")
):
    """
    Scan facility NVR and update config.json with discovered channels

    Args:
        facility: Facility name (e.g., "lodge")
        quick: Use quick scan (faster, common patterns only)
        max_channels: Maximum number of channels to test
        preserve_modelt_info: Preserve existing ModelT camera IDs and locations

    Returns:
        Updated config with scan results
    """
    try:
        # Load facility config to get NVR details
        config = camera_capture.load_config(facility)
        nvr_info = config['nvr']

        # Scan NVR
        scanner = NVRScanner(
            nvr_ip=nvr_info['ip'],
            username=nvr_info.get('username', 'admin'),
            password=nvr_info.get('password', ''),
            port=nvr_info.get('port', 554)
        )

        if quick:
            channels = scanner.quick_scan(
                channels_to_test=list(range(1, max_channels + 1))
            )
        else:
            channels = scanner.scan(max_channels=max_channels)

        if not channels:
            raise HTTPException(
                status_code=404,
                detail=f"No cameras found on NVR {nvr_info['ip']}"
            )

        # Update config with scanned channels
        updated_config = camera_capture.update_channels_from_scan(
            facility=facility,
            scanned_channels=channels,
            preserve_modelt_info=preserve_modelt_info
        )

        return {
            "facility": facility,
            "nvr_ip": nvr_info['ip'],
            "channels_found": len(channels),
            "channels_updated": len(updated_config['channels']),
            "preserved_modelt_info": preserve_modelt_info,
            "message": f"Updated {facility} config with {len(channels)} discovered cameras"
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error scanning and updating: {e}")
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
        config = camera_capture.load_config(facility)
        thumbnails_dir = camera_capture.warehouses_path / facility / "cameras" / "thumbnails"
        thumbnails_dir.mkdir(parents=True, exist_ok=True)

        results = {"success": [], "failed": []}

        for ch in config['channels']:
            cid = ch['modelTCameraId']
            logger.info(f"Generating thumbnail for {cid}...")

            image_data = camera_capture.capture_frame(ch['rtspUrl'], timeout=5)
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
        config = camera_capture.load_config(facility)
        channels = config['channels']

        def probe_camera(ch):
            """Probe a single camera, return (camera_id, success, elapsed_ms)"""
            cid = ch['modelTCameraId']
            start = time.time()
            result = camera_capture.capture_frame(ch['rtspUrl'], timeout=timeout)
            elapsed = int((time.time() - start) * 1000)
            return (cid, result is not None, elapsed)

        working = []
        failed = []
        timings = {}

        start_time = time.time()

        # Probe cameras with limited concurrency to avoid overwhelming NVR
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(probe_camera, ch): ch for ch in channels}

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
            "total": len(channels),
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
    Reload camera configuration from disk without full restart.
    Clears config cache so next request loads fresh config.
    """
    camera_capture._configs.clear()
    image_cache.clear()
    return {"message": "Config cache cleared - next request will load fresh config"}

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
# Windows doesn't support ipc://, use tcp instead
if sys.platform == "win32":
    ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"

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
            # Receive request
            msg = socket.recv_string()
            req = json.loads(msg)

            nvr_id = req.get("nvr", "nvr1")
            channel = req.get("channel", 1)
            source = req.get("source", "rtsp")  # "rtsp" or "snapshot"

            logger.info(f"ZMQ request: {nvr_id} ch{channel} via {source}")

            # Get NVR info
            nvr = get_nvr_info(nvr_id)
            if not nvr:
                socket.send(b"")  # Empty response for error
                continue

            # Capture based on source
            if source == "snapshot":
                image_data = capture_snapshot(nvr, channel)
            else:  # rtsp
                rtsp_url = build_rtsp_url(nvr, channel)
                image_data = camera_capture.capture_frame(rtsp_url, timeout=8)

            if image_data:
                socket.send(image_data)
            else:
                socket.send(b"")  # Empty response for error

        except Exception as e:
            logger.error(f"ZMQ handler error: {e}")
            try:
                socket.send(b"")
            except:
                pass

def start_zmq_server():
    """Start ZMQ server in background thread"""
    thread = threading.Thread(target=zmq_frame_handler, daemon=True)
    thread.start()
    logger.info("ZMQ frame server thread started")

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

# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    print("="*70)
    print("  Camera Capture Service")
    print("="*70)
    print("  Port: 8001")
    print("  Docs: http://localhost:8001/docs")
    print(f"  ZMQ:  {ZMQ_ENDPOINT}")
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
