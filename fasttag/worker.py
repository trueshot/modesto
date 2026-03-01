"""
FastTag worker — per-camera detection process.

Each worker opens a persistent RTSP connection direct to camera IP,
reads frames continuously, runs AprilTag detection, and pushes
results to a shared multiprocessing Queue.

Author: modeltcamerascat gen-26
"""

import time
import logging
import multiprocessing
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def detection_worker(
    camera_ip: str,
    rtsp_url: str,
    result_queue: multiprocessing.Queue,
    stop_event: multiprocessing.Event,
    config: dict,
):
    """
    Main detection loop — runs as a separate process.

    Args:
        camera_ip: Camera IP (used as identifier in results)
        rtsp_url: Full RTSP URL with credentials
        result_queue: Shared queue for detection results and heartbeats
        stop_event: Set to signal graceful shutdown
        config: Detection config dict with keys:
            - families: str (e.g., "tagCustom48h12")
            - quad_decimate: float (1.0 = full, 2.0 = half)
    """
    # Import detector inside the process — each gets its own instance
    from pupil_apriltags import Detector

    families = config.get("families", "tagCustom48h12")
    quad_decimate = config.get("quad_decimate", 1.0)

    detector = Detector(
        families=families,
        nthreads=1,
        quad_decimate=quad_decimate,
        quad_sigma=0.0,
        decode_sharpening=0.25,
    )

    frame_seq = 0
    detect_count = 0
    fps_window: list[float] = []  # timestamps of recent frames for fps calc
    reconnect_delay = 1.0
    max_reconnect_delay = 30.0

    def send_heartbeat(fps: float, status: str = "running", error: Optional[str] = None):
        result_queue.put({
            "type": "heartbeat",
            "camera_ip": camera_ip,
            "timestamp": time.time(),
            "frame_seq": frame_seq,
            "detect_count": detect_count,
            "fps": round(fps, 2),
            "status": status,
            "error": error,
        })

    def calc_fps() -> float:
        now = time.time()
        # Keep only last 2 seconds of timestamps
        while fps_window and now - fps_window[0] > 2.0:
            fps_window.pop(0)
        if len(fps_window) < 2:
            return 0.0
        elapsed = fps_window[-1] - fps_window[0]
        return (len(fps_window) - 1) / elapsed if elapsed > 0 else 0.0

    send_heartbeat(0.0, status="starting")

    while not stop_event.is_set():
        cap = None
        try:
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                send_heartbeat(0.0, status="reconnecting", error="VideoCapture failed to open")
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                continue

            # Connected — reset backoff
            reconnect_delay = 1.0
            send_heartbeat(0.0, status="running")
            last_heartbeat = time.time()

            while not stop_event.is_set():
                ret, frame = cap.read()
                if not ret or frame is None:
                    send_heartbeat(calc_fps(), status="reconnecting", error="read() failed")
                    break

                # Skip grey frames
                if frame.std() < 10:
                    continue

                frame_seq += 1
                now = time.time()
                fps_window.append(now)

                # Convert to grayscale for detection
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

                # Detect
                detections = detector.detect(gray)

                for det in detections:
                    detect_count += 1
                    result_queue.put({
                        "type": "detection",
                        "camera_ip": camera_ip,
                        "timestamp": now,
                        "tag_family": det.tag_family.decode() if isinstance(det.tag_family, bytes) else det.tag_family,
                        "tag_id": det.tag_id,
                        "center": det.center.tolist(),
                        "corners": det.corners.tolist(),
                        "decision_margin": float(det.decision_margin),
                        "frame_seq": frame_seq,
                    })

                # Heartbeat every 2 seconds
                if now - last_heartbeat >= 2.0:
                    send_heartbeat(calc_fps())
                    last_heartbeat = now

        except Exception as e:
            send_heartbeat(0.0, status="error", error=str(e))
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    # Exiting
    send_heartbeat(0.0, status="stopped")
