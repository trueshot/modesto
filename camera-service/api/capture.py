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
        Capture single frame from RTSP stream with hard timeout

        Args:
            rtsp_url: RTSP URL
            timeout: Timeout in seconds (enforced via threading)

        Returns:
            JPEG bytes or None on failure
        """
        import threading
        import queue

        result_queue = queue.Queue()

        def _capture():
            cap = None
            try:
                cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                if not cap.isOpened():
                    result_queue.put(None)
                    return

                # Read up to 3 frames - first frame from HEVC streams is often grey
                frame = None
                for _ in range(3):
                    ret, f = cap.read()
                    if not ret or f is None:
                        continue
                    # Check if frame has content (std > 10 means not grey)
                    if f.std() > 10:
                        frame = f
                        break
                    frame = f  # Keep last frame even if grey

                if frame is None:
                    result_queue.put(None)
                    return

                # Encode as JPEG
                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 95]
                ret, buffer = cv2.imencode('.jpg', frame, encode_param)

                if not ret:
                    result_queue.put(None)
                    return

                result_queue.put(buffer.tobytes())

            except Exception as e:
                logger.error(f"Error capturing frame: {e}")
                result_queue.put(None)

            finally:
                if cap is not None:
                    cap.release()

        # Run capture in thread with timeout
        thread = threading.Thread(target=_capture, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            # Timeout - thread still running, return None
            logger.warning(f"Timeout ({timeout}s) capturing from: {rtsp_url}")
            return None

        try:
            return result_queue.get_nowait()
        except queue.Empty:
            return None

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

    def update_channels_from_scan(
        self,
        facility: str,
        scanned_channels: List[Dict[str, Any]],
        preserve_modelt_info: bool = True
    ) -> Dict[str, Any]:
        """
        Update facility config with scanned channels

        Args:
            facility: Facility name
            scanned_channels: List of channel dicts from scanner
            preserve_modelt_info: Keep existing ModelT camera names/IDs if available

        Returns:
            Updated config dictionary
        """
        config = self.load_config(facility)
        config_path = self.warehouses_path / facility / "cameras" / "config.json"

        # Create a map of existing channels by channel number
        existing_channels = {}
        for channel in config.get('channels', []):
            existing_channels[channel['channel']] = channel

        # Update channels list
        updated_channels = []

        for scanned in scanned_channels:
            channel_num = scanned.get('channel')

            # Start with scanned data
            channel_info = {
                'channel': channel_num,
                'nvrPath': scanned['path'],
                'rtspUrl': scanned['url'],
                'resolution': scanned['resolution']
            }

            # Preserve ModelT info if it exists
            if preserve_modelt_info and channel_num in existing_channels:
                existing = existing_channels[channel_num]
                channel_info.update({
                    'modelTCameraId': existing.get('modelTCameraId', f'camera_{channel_num}'),
                    'modelTCameraName': existing.get('modelTCameraName', f'Camera {channel_num}'),
                    'modelTCameraNumber': existing.get('modelTCameraNumber', channel_num),
                    'location': existing.get('location', 'Unknown')
                })
            else:
                # New channel - create default ModelT info
                channel_info.update({
                    'modelTCameraId': f'camera_{channel_num}',
                    'modelTCameraName': f'Camera {channel_num}',
                    'modelTCameraNumber': channel_num,
                    'location': 'Unknown - needs configuration'
                })

            updated_channels.append(channel_info)

        # Update config
        config['channels'] = updated_channels

        # Save to file
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        # Update cache
        self._configs[facility] = config

        logger.info(f"Updated {facility} config: {len(updated_channels)} channels")

        return config

    def update_camera_config(
        self,
        facility: str,
        channel: int,
        name: Optional[str] = None,
        location: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Update camera configuration for a specific channel.
        Updates modelTCameraId, modelTCameraName, and/or location.

        Args:
            facility: Facility name
            channel: NVR channel number
            name: New camera name (e.g., "Donut") - will generate ID as lowercase
            location: New location description

        Returns:
            Updated channel info dict, or None if channel not found
        """
        # Force reload config from disk to get latest
        if facility in self._configs:
            del self._configs[facility]

        config = self.load_config(facility)
        config_path = self.warehouses_path / facility / "cameras" / "config.json"

        # Find the channel
        channel_info = None
        channel_idx = None
        for idx, ch in enumerate(config['channels']):
            if ch['channel'] == channel:
                channel_info = ch
                channel_idx = idx
                break

        if channel_info is None:
            return None

        # Update fields
        if name:
            # Generate ID from name (lowercase, no spaces)
            camera_id = name.lower().replace(' ', '_')
            channel_info['modelTCameraId'] = camera_id
            channel_info['modelTCameraName'] = name

        if location:
            channel_info['location'] = location

        # Update config
        config['channels'][channel_idx] = channel_info

        # Save to file
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        # Update cache
        self._configs[facility] = config

        logger.info(f"Updated camera config for {facility} channel {channel}: name={name}, location={location}")

        return channel_info
