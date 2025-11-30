"""
NVR Channel Scanner
Discovers available camera channels on NVR by testing common channel patterns
"""

import cv2
import logging
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)


class NVRScanner:
    """Scans NVR for available camera channels"""

    def __init__(self, nvr_ip: str, username: str = "admin", password: str = "", port: int = 554):
        """
        Initialize NVR scanner

        Args:
            nvr_ip: NVR IP address
            username: RTSP username
            password: RTSP password
            port: RTSP port
        """
        self.nvr_ip = nvr_ip
        self.username = username
        self.password = password
        self.port = port

    def _generate_channel_patterns(self, max_channels: int = 32) -> List[str]:
        """
        Generate common NVR channel path patterns

        Args:
            max_channels: Maximum number of channels to test

        Returns:
            List of RTSP path patterns to test
        """
        patterns = []

        # Generic RTSP patterns (most common)
        patterns.extend([f"ch{i:02d}/0" for i in range(1, max_channels + 1)])

        # Hikvision NVR patterns
        patterns.extend([f"Streaming/Channels/{i}01" for i in range(1, max_channels + 1)])  # Main stream
        patterns.extend([f"Streaming/Channels/{i}02" for i in range(1, max_channels + 1)])  # Sub stream

        # Dahua NVR patterns
        patterns.extend([f"cam/realmonitor?channel={i}&subtype=0" for i in range(1, max_channels + 1)])  # Main
        patterns.extend([f"cam/realmonitor?channel={i}&subtype=1" for i in range(1, max_channels + 1)])  # Sub

        # Alternative patterns
        patterns.extend([f"rtsp/streaming?channel={i}&subtype=0" for i in range(1, max_channels + 1)])
        patterns.extend([f"channel{i}" for i in range(1, max_channels + 1)])
        patterns.extend([f"live/ch{i:02d}" for i in range(1, max_channels + 1)])
        patterns.extend([f"stream{i}" for i in range(1, max_channels + 1)])

        return patterns

    def _test_channel(self, path: str) -> Tuple[bool, int, int]:
        """
        Test if a channel path works

        Args:
            path: RTSP path to test

        Returns:
            Tuple of (success, width, height)
        """
        url = f"rtsp://{self.username}:{self.password}@{self.nvr_ip}:{self.port}/{path}"

        try:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()

                if ret and frame is not None:
                    height, width = frame.shape[:2]
                    return True, width, height

            cap.release()
        except Exception as e:
            logger.debug(f"Error testing {path}: {e}")

        return False, 0, 0

    def scan(self, max_channels: int = 32, progress_callback=None) -> List[Dict[str, Any]]:
        """
        Scan NVR for available channels

        Args:
            max_channels: Maximum number of channels to test
            progress_callback: Optional callback(tested, total, found) for progress updates

        Returns:
            List of found channel dicts with path, resolution, and URL
        """
        logger.info(f"Scanning NVR {self.nvr_ip} for channels...")

        patterns = self._generate_channel_patterns(max_channels)
        found_channels = []
        tested = 0

        for path in patterns:
            tested += 1

            if progress_callback:
                progress_callback(tested, len(patterns), len(found_channels))

            success, width, height = self._test_channel(path)

            if success:
                channel_info = {
                    'path': path,
                    'width': width,
                    'height': height,
                    'resolution': f"{width}x{height}",
                    'url': f"rtsp://{self.username}:{self.password}@{self.nvr_ip}:{self.port}/{path}"
                }
                found_channels.append(channel_info)
                logger.info(f"Found channel: {path} ({width}x{height})")

        logger.info(f"Scan complete: {len(found_channels)} channels found")
        return found_channels

    def quick_scan(self, channels_to_test: List[int] = None) -> List[Dict[str, Any]]:
        """
        Quick scan using only the most common pattern (ch01/0, ch02/0, etc.)

        Args:
            channels_to_test: List of channel numbers to test (default: 1-32)

        Returns:
            List of found channel dicts
        """
        if channels_to_test is None:
            channels_to_test = list(range(1, 33))

        logger.info(f"Quick scanning NVR {self.nvr_ip}...")

        found_channels = []

        for channel_num in channels_to_test:
            path = f"ch{channel_num:02d}/0"
            success, width, height = self._test_channel(path)

            if success:
                channel_info = {
                    'path': path,
                    'channel': channel_num,
                    'width': width,
                    'height': height,
                    'resolution': f"{width}x{height}",
                    'url': f"rtsp://{self.username}:{self.password}@{self.nvr_ip}:{self.port}/{path}"
                }
                found_channels.append(channel_info)
                logger.info(f"Found channel {channel_num}: {width}x{height}")

        logger.info(f"Quick scan complete: {len(found_channels)} channels found")
        return found_channels
