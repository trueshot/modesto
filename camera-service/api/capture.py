"""
Camera capture logic
Handles NVR connections and frame capture using OpenCV

Migrated from config.json to lodge.db (gen-6)
"""

import os
import cv2
import sqlite3
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
        self._db_connections = {}  # Cache db connections

    def _get_db(self, facility: str) -> sqlite3.Connection:
        """Get database connection for facility"""
        if facility not in self._db_connections:
            db_path = self.warehouses_path / facility / f"{facility}.db"
            if not db_path.exists():
                raise FileNotFoundError(f"Database not found: {db_path}")
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._db_connections[facility] = conn
            logger.info(f"Connected to {facility} database: {db_path}")
        return self._db_connections[facility]

    def get_camera_info(self, facility: str, camera_id: str) -> Optional[Dict[str, Any]]:
        """
        Get camera information by mount ID (food name)

        Args:
            facility: Facility name (e.g., "lodge")
            camera_id: Mount ID / food name (e.g., "bagel")

        Returns:
            Camera info dict or None if not found
        """
        db = self._get_db(facility)
        cursor = db.cursor()

        cursor.execute("""
            SELECT
                m.id as modelTCameraId,
                m.id as modelTCameraName,
                m.description as location,
                c.rtsp_path as rtspUrl,
                c.nvr_id as nvrId,
                c.channel_number as channel,
                c.resolution
            FROM mounts m
            JOIN linkages l ON l.mount_id = m.id
            JOIN channels c ON l.channel_id = c.id
            WHERE m.id = ?
        """, (camera_id,))

        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def capture_frame_ffmpeg(self, rtsp_url: str, timeout: int = 8) -> Optional[bytes]:
        """
        Capture single frame via ffmpeg subprocess — more reliable than cv2 for RTSP.
        Forces TCP transport. Returns JPEG bytes or None.
        """
        import subprocess
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
            if proc.returncode == 0 and len(proc.stdout) > 1000:
                return proc.stdout
            logger.warning(f"ffmpeg capture failed for {rtsp_url[:50]}: rc={proc.returncode} size={len(proc.stdout)}")
            return None
        except subprocess.TimeoutExpired:
            logger.warning(f"ffmpeg timeout ({timeout}s) for {rtsp_url[:50]}")
            return None
        except Exception as e:
            logger.warning(f"ffmpeg error for {rtsp_url[:50]}: {e}")
            return None

    def capture_frame(self, rtsp_url: str, timeout: int = 8, fmt: str = "jpeg") -> Optional[bytes]:
        """
        Capture single frame from RTSP stream.
        Uses ffmpeg subprocess (reliable TCP) with cv2 fallback.

        Args:
            rtsp_url: RTSP URL
            timeout: Timeout in seconds
            fmt: Output format — "jpeg" or "png"

        Returns:
            Encoded image bytes or None on failure
        """
        # Try ffmpeg first — more reliable for RTSP
        result = self.capture_frame_ffmpeg(rtsp_url, timeout=timeout)
        if result:
            return result

        # Fallback to cv2 (for NVR streams that ffmpeg might not handle well)
        import threading
        import queue

        result_queue = queue.Queue()
        cap_holder = [None]

        def _capture():
            try:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
                cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                cap_holder[0] = cap
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                if not cap.isOpened():
                    result_queue.put(None)
                    return

                # Read up to 5 frames - HEVC streams often start grey
                frame = None
                for _ in range(5):
                    ret, f = cap.read()
                    if not ret or f is None:
                        continue
                    # Check if frame has content (std > 10 means not grey)
                    if f.std() > 10:
                        frame = f
                        break

                if frame is None:
                    result_queue.put(None)
                    return

                # Encode frame
                if fmt == "png":
                    ret, buffer = cv2.imencode('.png', frame)
                else:
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
                cap = cap_holder[0]
                if cap is not None:
                    cap.release()

        # Run capture in thread with timeout
        thread = threading.Thread(target=_capture, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            # Timeout — force-release the RTSP connection so NVR doesn't see a ghost
            logger.warning(f"Timeout ({timeout}s) capturing from: {rtsp_url}")
            cap = cap_holder[0]
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
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
            camera_id: Mount ID / food name (e.g., "bagel")

        Returns:
            JPEG bytes or None on failure
        """
        camera_info = self.get_camera_info(facility, camera_id)

        if not camera_info:
            logger.error(f"Camera not found: {facility}/{camera_id}")
            return None

        rtsp_url = camera_info['rtspUrl']
        logger.info(f"Capturing from {camera_id}")

        return self.capture_frame(rtsp_url)

    def capture_all(self, facility: str) -> Dict[str, Optional[bytes]]:
        """
        Capture frames from all cameras in facility

        Args:
            facility: Facility name

        Returns:
            Dictionary mapping camera_id to image bytes
        """
        cameras = self.list_cameras(facility)
        results = {}

        for cam in cameras:
            camera_id = cam['id']
            logger.info(f"Capturing {camera_id}...")
            frame = self.capture_frame(cam['rtsp_url'])
            results[camera_id] = frame

        return results

    def _get_nvr_credentials(self, nvr_id: str) -> tuple:
        """Resolve NVR credentials from .env. E.g. nvr1 → NVR1_USER/NVR1_PASS."""
        prefix = nvr_id.upper()
        return (os.environ.get(f"{prefix}_USER", "admin"),
                os.environ.get(f"{prefix}_PASS", ""))

    def _build_rtsp_url(self, nvr_ip: str, nvr_id: str, rtsp_path: str) -> str:
        """Build full RTSP URL from NVR info and path."""
        from urllib.parse import quote
        username, password = self._get_nvr_credentials(nvr_id)
        password_encoded = quote(password, safe='')
        path = rtsp_path.lstrip('/')
        return f"rtsp://{username}:{password_encoded}@{nvr_ip}:554/{path}"

    def list_cameras(self, facility: str) -> List[Dict[str, Any]]:
        """
        List all cameras for a facility

        Args:
            facility: Facility name

        Returns:
            List of camera info dicts with full RTSP URLs
        """
        db = self._get_db(facility)
        cursor = db.cursor()

        cursor.execute("""
            SELECT
                COALESCE(m.id, 'camera_' || c.nvr_id || '_ch' || c.channel_number) as id,
                COALESCE(m.id, 'camera_' || c.nvr_id || '_ch' || c.channel_number) as name,
                COALESCE(m.description, 'Unmounted channel') as location,
                c.channel_number as channel,
                c.resolution,
                c.rtsp_path,
                c.nvr_id,
                n.ip as nvr_ip
            FROM channels c
            LEFT JOIN linkages l ON l.channel_id = c.id
            LEFT JOIN mounts m ON l.mount_id = m.id
            LEFT JOIN nvrs n ON c.nvr_id = n.id
            WHERE c.status = 'active'
            ORDER BY c.nvr_id, c.channel_number
        """)

        cameras = []
        for row in cursor.fetchall():
            rtsp_url = None
            if row['nvr_ip'] and row['rtsp_path']:
                rtsp_url = self._build_rtsp_url(row['nvr_ip'], row['nvr_id'], row['rtsp_path'])
            cameras.append({
                'id': row['id'],
                'name': row['name'],
                'number': row['channel'],
                'location': row['location'] or 'Unknown',
                'resolution': row['resolution'] or 'Unknown',
                'channel': row['channel'],
                'rtsp_url': rtsp_url,
                'nvr_id': row['nvr_id']
            })

        return cameras

    def list_nvrs(self, facility: str) -> List[Dict[str, Any]]:
        """
        List all NVRs for a facility

        Args:
            facility: Facility name

        Returns:
            List of NVR info dicts with id, ip, brand, etc.
        """
        db = self._get_db(facility)
        cursor = db.cursor()

        cursor.execute("SELECT * FROM nvrs ORDER BY id")

        nvrs = []
        for row in cursor.fetchall():
            nvrs.append(dict(row))

        return nvrs

    def check_nvr_connectivity(self, facility: str) -> Dict[str, Any]:
        """
        Check NVR configuration status (DB-only, no capture test).

        Args:
            facility: Facility name

        Returns:
            NVR config status dict
        """
        db = self._get_db(facility)
        cursor = db.cursor()

        # Get NVR info
        cursor.execute("SELECT id, ip FROM nvrs LIMIT 1")
        nvr_row = cursor.fetchone()

        if not nvr_row:
            return {'error': 'No NVRs configured', 'total_cameras': 0}

        # Get total camera count
        cursor.execute("""
            SELECT COUNT(*) as count FROM mounts m
            JOIN linkages l ON l.mount_id = m.id
        """)
        count_row = cursor.fetchone()
        total_cameras = count_row['count'] if count_row else 0

        return {
            'nvr_ip': nvr_row['ip'],
            'total_cameras': total_cameras
        }

    def update_camera_config(
        self,
        facility: str,
        channel: int,
        name: Optional[str] = None,
        location: Optional[str] = None,
        nvr_id: str = "nvr1"
    ) -> Optional[Dict[str, Any]]:
        """
        Update camera configuration for a specific channel.
        Updates mount ID and description in lodge.db.

        Args:
            facility: Facility name
            channel: NVR channel number
            name: New camera name (e.g., "Donut") - becomes mount ID (lowercase)
            location: New location description
            nvr_id: NVR ID (default "nvr1")

        Returns:
            Updated camera info dict, or None if channel not found
        """
        db = self._get_db(facility)
        cursor = db.cursor()

        # Find the channel
        channel_id = f"{nvr_id}_ch{channel:02d}"
        cursor.execute("SELECT id FROM channels WHERE id = ?", (channel_id,))
        if not cursor.fetchone():
            return None

        # Find current linkage
        cursor.execute("""
            SELECT l.mount_id, m.description
            FROM linkages l
            JOIN mounts m ON m.id = l.mount_id
            WHERE l.channel_id = ?
        """, (channel_id,))
        linkage = cursor.fetchone()

        old_mount_id = linkage['mount_id'] if linkage else None

        if name:
            new_mount_id = name.lower().replace(' ', '_')

            # Create new mount if it doesn't exist
            cursor.execute("SELECT id FROM mounts WHERE id = ?", (new_mount_id,))
            if not cursor.fetchone():
                cursor.execute("""
                    INSERT INTO mounts (id, description, zone_id)
                    VALUES (?, ?, 'unknown')
                """, (new_mount_id, location or 'Unknown - needs configuration'))

            # Update or create linkage
            if old_mount_id:
                cursor.execute("""
                    UPDATE linkages SET mount_id = ? WHERE channel_id = ?
                """, (new_mount_id, channel_id))
            else:
                cursor.execute("""
                    INSERT INTO linkages (mount_id, channel_id)
                    VALUES (?, ?)
                """, (new_mount_id, channel_id))

        if location and old_mount_id:
            mount_id = name.lower().replace(' ', '_') if name else old_mount_id
            cursor.execute("""
                UPDATE mounts SET description = ? WHERE id = ?
            """, (location, mount_id))

        db.commit()

        # Return updated info
        return self.get_camera_info(facility, name.lower().replace(' ', '_') if name else old_mount_id)
