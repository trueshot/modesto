"""
Camera capture logic
Handles NVR connections and frame capture using OpenCV
"""

import cv2
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class CameraCapture:
    """Handles camera capture operations"""

    def __init__(self, warehouses_path: str = "../warehouses"):
        """
        Initialize camera capture

        Args:
            warehouses_path: Path to warehouses directory
        """
        self.warehouses_path = Path(warehouses_path)
        self._configs = {}  # Cache configs in memory

    def load_config(self, facility: str) -> Dict[str, Any]:
        """
        Load camera configuration for a facility

        Args:
            facility: Facility name (e.g., "lodge")

        Returns:
            Camera configuration dictionary

        Raises:
            FileNotFoundError: If config doesn't exist
        """
        # Check cache first
        if facility in self._configs:
            return self._configs[facility]

        config_path = self.warehouses_path / facility / "cameras" / "config.json"

        if not config_path.exists():
            raise FileNotFoundError(f"Camera config not found: {config_path}")

        with open(config_path, 'r') as f:
            config = json.load(f)

        # Cache it
        self._configs[facility] = config
        logger.info(f"Loaded config for {facility}: {len(config['channels'])} cameras")

        return config

    def get_camera_info(self, facility: str, camera_id: str) -> Optional[Dict[str, Any]]:
        """
        Get camera information by ModelT camera ID

        Args:
            facility: Facility name
            camera_id: ModelT camera ID (e.g., "bagel")

        Returns:
            Camera info dict or None if not found
        """
        config = self.load_config(facility)

        for channel in config['channels']:
            if channel['modelTCameraId'] == camera_id:
                return channel

        return None

    def capture_frame(self, rtsp_url: str, timeout: int = 5) -> Optional[bytes]:
        """
        Capture single frame from RTSP stream

        Args:
            rtsp_url: RTSP URL
            timeout: Timeout in seconds

        Returns:
            JPEG bytes or None on failure
        """
        cap = None
        try:
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                logger.error(f"Failed to open RTSP stream: {rtsp_url}")
                return None

            ret, frame = cap.read()

            if not ret or frame is None:
                logger.error(f"Failed to read frame from: {rtsp_url}")
                return None

            # Encode as JPEG
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 95]
            ret, buffer = cv2.imencode('.jpg', frame, encode_param)

            if not ret:
                logger.error("Failed to encode frame as JPEG")
                return None

            return buffer.tobytes()

        except Exception as e:
            logger.error(f"Error capturing frame: {e}")
            return None

        finally:
            if cap is not None:
                cap.release()

    def capture_camera(self, facility: str, camera_id: str) -> Optional[bytes]:
        """
        Capture frame from specific camera

        Args:
            facility: Facility name
            camera_id: ModelT camera ID

        Returns:
            JPEG bytes or None on failure
        """
        camera_info = self.get_camera_info(facility, camera_id)

        if not camera_info:
            logger.error(f"Camera not found: {facility}/{camera_id}")
            return None

        rtsp_url = camera_info['rtspUrl']
        logger.info(f"Capturing from {camera_id} ({camera_info['modelTCameraName']})")

        return self.capture_frame(rtsp_url)

    def capture_all(self, facility: str) -> Dict[str, Optional[bytes]]:
        """
        Capture frames from all cameras in facility

        Args:
            facility: Facility name

        Returns:
            Dictionary mapping camera_id to image bytes
        """
        config = self.load_config(facility)
        results = {}

        for channel in config['channels']:
            camera_id = channel['modelTCameraId']
            logger.info(f"Capturing {camera_id}...")

            frame = self.capture_frame(channel['rtspUrl'])
            results[camera_id] = frame

        return results

    def list_cameras(self, facility: str) -> List[Dict[str, Any]]:
        """
        List all cameras for a facility

        Args:
            facility: Facility name

        Returns:
            List of camera info dicts
        """
        config = self.load_config(facility)

        cameras = []
        for channel in config['channels']:
            cameras.append({
                'id': channel['modelTCameraId'],
                'name': channel['modelTCameraName'],
                'number': channel['modelTCameraNumber'],
                'location': channel['location'],
                'resolution': channel['resolution'],
                'channel': channel['channel']
            })

        return cameras

    def check_nvr_connectivity(self, facility: str) -> Dict[str, Any]:
        """
        Check NVR connectivity

        Args:
            facility: Facility name

        Returns:
            Connectivity status dict
        """
        config = self.load_config(facility)
        nvr_info = config['nvr']

        # Try to capture from first camera as connectivity test
        if config['channels']:
            first_channel = config['channels'][0]
            frame = self.capture_frame(first_channel['rtspUrl'])
            reachable = frame is not None
        else:
            reachable = False

        return {
            'nvr_ip': nvr_info['ip'],
            'reachable': reachable,
            'total_cameras': len(config['channels'])
        }
