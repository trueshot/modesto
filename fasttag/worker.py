"""
FastTag worker — per-camera detection process.

Each worker opens a persistent RTSP connection direct to camera IP,
reads frames continuously, runs AprilTag detection, and pushes
results to a shared multiprocessing Queue.

Author: modeltcamerascat gen-26
"""

import json
import time
import logging
import multiprocessing
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

CAMERAS_DIR = Path(__file__).parent / "cameras"


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
            - flip: int or None — initial flip mode (None=normal, 1=H-flip)
            - flip_check_after: int — consecutive empty frames before trying flip (default 30)
    """
    # Import detector inside the process — each gets its own instance
    from pupil_apriltags import Detector

    families = config.get("families", "tagCustom48h12")
    quad_decimate = config.get("quad_decimate", 1.0)
    nthreads = config.get("nthreads", 4)
    target_fps = config.get("target_fps", 0)  # 0 = no limit
    frame_interval = 1.0 / target_fps if target_fps > 0 else 0

    # Adaptive flip: if camera image is mirrored, tags are undetectable
    # (mirror of a valid codeword lands outside error correction threshold).
    # Strategy: track consecutive empty frames. After flip_check_after empties,
    # try the other orientation. Sticky until it stops working too.
    flip_mode = config.get("flip", None)  # None = normal, 1 = H-flip
    flip_check_after = config.get("flip_check_after", 30)
    consecutive_empty = 0

    detector = Detector(
        families=families,
        nthreads=nthreads,
        quad_decimate=quad_decimate,
        quad_sigma=0.0,
        decode_sharpening=0.25,
    )

    frame_seq = 0
    detect_count = 0
    fps_window: list[float] = []  # timestamps of recent frames for fps calc
    reconnect_delay = 1.0
    max_reconnect_delay = 30.0

    # Per-camera JSONL detection log
    log_dir = CAMERAS_DIR / camera_ip
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / "detections.jsonl"
    jsonl_file = open(jsonl_path, "a", buffering=1)  # line-buffered

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
            "flip_mode": "H-flip" if flip_mode == 1 else "normal",
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

            last_frame_time = 0.0

            while not stop_event.is_set():
                ret, frame = cap.read()
                if not ret or frame is None:
                    send_heartbeat(calc_fps(), status="reconnecting", error="read() failed")
                    break

                # Skip grey frames
                if frame.std() < 10:
                    continue

                now = time.time()

                # Rate limiting: skip frames to hit target_fps
                if frame_interval > 0 and (now - last_frame_time) < frame_interval:
                    continue
                last_frame_time = now

                frame_seq += 1
                fps_window.append(now)

                # Convert to grayscale for detection
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

                # Apply flip if active
                detect_gray = cv2.flip(gray, flip_mode) if flip_mode is not None else gray

                # Detect
                detections = detector.detect(detect_gray)

                # Adaptive flip: if no detections, count empties.
                # After flip_check_after consecutive empties, try the other orientation.
                if not detections:
                    consecutive_empty += 1
                    if consecutive_empty == flip_check_after:
                        alt_flip = None if flip_mode is not None else 1
                        alt_gray = cv2.flip(gray, alt_flip) if alt_flip is not None else gray
                        alt_dets = detector.detect(alt_gray)
                        if alt_dets:
                            flip_mode = alt_flip
                            detections = alt_dets
                            detect_gray = alt_gray
                            consecutive_empty = 0
                        # If alt also empty, reset counter and keep current mode.
                        # We'll check again after another flip_check_after frames.
                        else:
                            consecutive_empty = 0
                else:
                    consecutive_empty = 0

                for det in detections:
                    detect_count += 1
                    det_record = {
                        "type": "detection",
                        "camera_ip": camera_ip,
                        "timestamp": now,
                        "tag_family": det.tag_family.decode() if isinstance(det.tag_family, bytes) else det.tag_family,
                        "tag_id": det.tag_id,
                        "center": det.center.tolist(),
                        "corners": det.corners.tolist(),
                        "decision_margin": float(det.decision_margin),
                        "frame_seq": frame_seq,
                        "flip_mode": "H-flip" if flip_mode == 1 else "normal",
                    }
                    result_queue.put(det_record)
                    # Per-camera JSONL log
                    try:
                        jsonl_file.write(json.dumps(det_record) + "\n")
                    except Exception:
                        pass  # don't let log failure kill detection

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
    try:
        jsonl_file.close()
    except Exception:
        pass
    send_heartbeat(0.0, status="stopped")
