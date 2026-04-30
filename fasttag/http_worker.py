"""
FastTag HTTP-camera worker — per-camera detection process for cams that
speak HTTP/MJPEG only (PoE-CAMs, M5CamServer).

Cannot open a fresh connection to W5500-based cams (single client slot),
so we ride camera-service's http_mediator on port 8001 — fetch the latest
frame from /api/http-cameras/{ip}/frame at our target_fps. The mediator
already holds one persistent stream upstream and serves coalesced frames
to all consumers, so this approach scales (one mediator stream, N FastTag
consumers).

Same heartbeat / detection-record / JSONL contract as worker.py's RTSP
detection_worker — the only differences are the frame source and the
fail-fast behavior when camera-service is unreachable.

Author: modeltcamerascat gen-44
"""

import json
import time
import logging
import multiprocessing
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import requests

logger = logging.getLogger(__name__)

CAMERAS_DIR = Path(__file__).parent / "cameras"


def http_detection_worker(
    camera_ip: str,
    camera_service_url: str,
    result_queue: multiprocessing.Queue,
    stop_event: multiprocessing.Event,
    config: dict,
):
    """Wrapper that captures any subprocess-level crash to a per-cam log."""
    crash_log = CAMERAS_DIR / camera_ip / "worker_crash.log"
    crash_log.parent.mkdir(parents=True, exist_ok=True)
    try:
        return _http_detection_worker_impl(
            camera_ip, camera_service_url, result_queue, stop_event, config
        )
    except BaseException as e:
        import traceback as _tb
        try:
            with open(crash_log, "a") as f:
                f.write(f"\n=== {time.time()} {type(e).__name__}: {e} ===\n")
                _tb.print_exc(file=f)
        except Exception:
            pass
        # Best-effort heartbeat so the parent sees the death
        try:
            result_queue.put({
                "type": "heartbeat",
                "camera_ip": camera_ip,
                "timestamp": time.time(),
                "frame_seq": 0,
                "detect_count": 0,
                "fps": 0.0,
                "status": "crashed",
                "error": f"{type(e).__name__}: {e}",
                "flip_mode": "normal",
                "source_type": "http",
            })
        except Exception:
            pass
        raise


def _http_detection_worker_impl(
    camera_ip: str,
    camera_service_url: str,
    result_queue: multiprocessing.Queue,
    stop_event: multiprocessing.Event,
    config: dict,
):
    """
    Per-process detection loop for an HTTP/MJPEG cam.

    Args:
        camera_ip: identifier (also lookup key into camera-service)
        camera_service_url: e.g. "http://127.0.0.1:8001"
        result_queue: shared queue for heartbeats/detections
        stop_event: graceful shutdown
        config: same shape as worker.py's config — families, quad_decimate,
                target_fps, flip, etc.
    """
    from pupil_apriltags import Detector

    families = config.get("families", "tagCustom48h12")
    quad_decimate = config.get("quad_decimate", 2.0)
    nthreads = config.get("nthreads", 4)
    target_fps = config.get("target_fps", 5.0)
    frame_interval = 1.0 / target_fps if target_fps > 0 else 0
    flip_mode = config.get("flip", None)
    flip_check_after = config.get("flip_check_after", 30)
    fetch_timeout_s = 2.0  # fail-fast on dead camera-service

    consecutive_empty = 0

    detector = Detector(
        families=families,
        nthreads=nthreads,
        quad_decimate=quad_decimate,
        quad_sigma=0.0,
        decode_sharpening=0.25,
    )

    frame_url = f"{camera_service_url}/api/http-cameras/{camera_ip}/frame"
    session = requests.Session()  # connection pooling for repeated polls

    frame_seq = 0
    detect_count = 0
    fps_window: list[float] = []
    backoff_s = 1.0
    max_backoff_s = 30.0

    log_dir = CAMERAS_DIR / camera_ip
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / "detections.jsonl"
    jsonl_file = open(jsonl_path, "a", buffering=1)

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
            "source_type": "http",
        })

    def calc_fps() -> float:
        now = time.time()
        while fps_window and now - fps_window[0] > 2.0:
            fps_window.pop(0)
        if len(fps_window) < 2:
            return 0.0
        elapsed = fps_window[-1] - fps_window[0]
        return (len(fps_window) - 1) / elapsed if elapsed > 0 else 0.0

    send_heartbeat(0.0, status="starting")
    last_heartbeat = time.time()
    last_frame_time = 0.0
    have_transitioned_to_running = False

    while not stop_event.is_set():
        # Rate limit polling to target_fps
        now = time.time()
        if frame_interval > 0 and (now - last_frame_time) < frame_interval:
            time.sleep(max(0.01, frame_interval - (now - last_frame_time)))
            # Wall-clock heartbeat even when rate-limited / between frames —
            # prevents the reaper from misreading a healthy idle worker as
            # "stuck in starting".
            if time.time() - last_heartbeat >= 2.0:
                send_heartbeat(calc_fps(),
                               status=("running" if have_transitioned_to_running else "starting"))
                last_heartbeat = time.time()
            continue
        last_frame_time = time.time()

        # Fetch a frame
        try:
            r = session.get(frame_url, timeout=fetch_timeout_s)
        except requests.exceptions.RequestException as e:
            send_heartbeat(calc_fps(), status="reconnecting",
                           error=f"camera-service: {type(e).__name__}")
            last_heartbeat = time.time()
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, max_backoff_s)
            continue

        if r.status_code != 200:
            send_heartbeat(calc_fps(), status="reconnecting",
                           error=f"HTTP {r.status_code}")
            last_heartbeat = time.time()
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, max_backoff_s)
            continue

        backoff_s = 1.0

        # First successful fetch → transition to running, regardless of
        # whether this particular frame yields a detection. The mediator
        # is serving us; we are healthy.
        if not have_transitioned_to_running:
            have_transitioned_to_running = True
            send_heartbeat(calc_fps(), status="running")
            last_heartbeat = time.time()

        # Decode JPEG
        try:
            arr = np.frombuffer(r.content, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception as e:
            send_heartbeat(calc_fps(), status="reconnecting",
                           error=f"jpeg decode: {e}")
            last_heartbeat = time.time()
            continue
        if frame is None:
            continue
        if frame.std() < 10:
            # Grey frame — still send periodic wall-clock heartbeat so the
            # reaper sees we're alive (mediator may temporarily serve a
            # blanked frame during reconnect).
            if time.time() - last_heartbeat >= 2.0:
                send_heartbeat(calc_fps(), status="running",
                               error="grey frames (std<10)")
                last_heartbeat = time.time()
            continue

        frame_seq += 1
        capture_ts = time.time()
        fps_window.append(capture_ts)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        detect_gray = cv2.flip(gray, flip_mode) if flip_mode is not None else gray
        detections = detector.detect(detect_gray)

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
        else:
            consecutive_empty = 0

        for det in detections:
            detect_count += 1
            det_record = {
                "type": "detection",
                "camera_ip": camera_ip,
                "timestamp": capture_ts,
                "tag_family": det.tag_family.decode() if isinstance(det.tag_family, bytes) else det.tag_family,
                "tag_id": det.tag_id,
                "center": det.center.tolist(),
                "corners": det.corners.tolist(),
                "decision_margin": float(det.decision_margin),
                "frame_seq": frame_seq,
                "flip_mode": "H-flip" if flip_mode == 1 else "normal",
                "source_type": "http",
            }
            result_queue.put(det_record)
            try:
                jsonl_file.write(json.dumps(det_record) + "\n")
            except Exception:
                pass

        # Wall-clock heartbeat (not capture_ts based) — keeps liveness
        # signal independent of detection cadence.
        if time.time() - last_heartbeat >= 2.0:
            send_heartbeat(calc_fps())
            last_heartbeat = time.time()

    try:
        jsonl_file.close()
    except Exception:
        pass
    send_heartbeat(0.0, status="stopped")
