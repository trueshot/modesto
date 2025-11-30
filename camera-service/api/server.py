#!/usr/bin/env python3
"""
Camera Capture Service
FastAPI server for warehouse camera access and image delivery

Port: 8001
Provides camera images to agents, web apps, and other services
"""

import sys
import logging
from pathlib import Path
from typing import Optional, List
import base64

from fastapi import FastAPI, HTTPException, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from capture import CameraCapture
from cache import ImageCache
from scanner import NVRScanner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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

# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    print("="*70)
    print("  Camera Capture Service")
    print("="*70)
    print("  Port: 8001")
    print("  Docs: http://localhost:8001/docs")
    print("="*70)
    print()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8001,
        log_level="info"
    )
