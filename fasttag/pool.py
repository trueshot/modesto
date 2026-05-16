#!/usr/bin/env python3
"""
Pooled AprilTag Detection — shared memory frame pool + detector processes.

Architecture:
  - FramePool: shared memory ring buffer for frames (zero-copy)
  - FrameReader: thread per camera, grabs frames, writes to pool
  - DetectorProcess: N processes, each with one Detector (~5GB), pulls from queue

Author: modeltcamerascat gen-45
"""

import os
import time
import threading
import multiprocessing
from multiprocessing import shared_memory
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
import logging
import json
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ============================================================================
# FRAME POOL — shared memory ring buffer
# ============================================================================

@dataclass
class FrameSlot:
    """Metadata for one slot in the frame pool."""
    shm_name: str
    size: int  # bytes
    in_use: bool = False


class FramePool:
    """
    Shared memory pool for frames. Pre-allocates N slots of fixed size.

    Writer (reader thread):
        slot_id = pool.acquire()
        pool.write(slot_id, frame)
        queue.put((camera_ip, slot_id, shape, timestamp))

    Reader (detector process):
        frame = pool.read(slot_id, shape)
        # ... detect ...
        pool.release(slot_id)
    """

    def __init__(self, num_slots: int = 30, slot_size: int = 30_000_000):
        """
        Args:
            num_slots: Number of frame slots (should be >= num cameras)
            slot_size: Max bytes per frame (30MB covers 4K RGB)
        """
        self.num_slots = num_slots
        self.slot_size = slot_size
        self.slots: List[shared_memory.SharedMemory] = []
        self.slot_lock = threading.Lock()
        self.slot_in_use = [False] * num_slots

        # Create shared memory blocks
        for i in range(num_slots):
            shm = shared_memory.SharedMemory(create=True, size=slot_size)
            self.slots.append(shm)
            logger.debug(f"Created shared memory slot {i}: {shm.name}")

        logger.info(f"FramePool created: {num_slots} slots × {slot_size/1e6:.1f}MB = {num_slots*slot_size/1e9:.2f}GB")

    def acquire(self, timeout: float = 1.0) -> Optional[int]:
        """Get a free slot index. Returns None if none available."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.slot_lock:
                for i, in_use in enumerate(self.slot_in_use):
                    if not in_use:
                        self.slot_in_use[i] = True
                        return i
            time.sleep(0.01)
        return None

    def release(self, slot_id: int):
        """Mark slot as free."""
        with self.slot_lock:
            self.slot_in_use[slot_id] = False

    def write(self, slot_id: int, frame: np.ndarray):
        """Write frame to slot. Frame must fit in slot_size."""
        data = frame.tobytes()
        if len(data) > self.slot_size:
            raise ValueError(f"Frame {len(data)} bytes exceeds slot size {self.slot_size}")
        self.slots[slot_id].buf[:len(data)] = data

    def get_shm_name(self, slot_id: int) -> str:
        """Get shared memory name for a slot (for cross-process access)."""
        return self.slots[slot_id].name

    def get_all_shm_names(self) -> List[str]:
        """Get all shared memory names (passed to detector processes at startup)."""
        return [s.name for s in self.slots]

    def close(self):
        """Clean up shared memory."""
        for shm in self.slots:
            try:
                shm.close()
                shm.unlink()
            except Exception as e:
                logger.warning(f"Error closing shm {shm.name}: {e}")
        self.slots.clear()


class FramePoolReader:
    """Read-only view of FramePool for detector processes."""

    def __init__(self, shm_names: List[str], slot_size: int):
        self.slot_size = slot_size
        self.slots: List[shared_memory.SharedMemory] = []
        for name in shm_names:
            shm = shared_memory.SharedMemory(name=name, create=False)
            self.slots.append(shm)

    def read(self, slot_id: int, shape: Tuple[int, ...], dtype=np.uint8) -> np.ndarray:
        """Read frame from slot."""
        size = int(np.prod(shape))
        arr = np.frombuffer(self.slots[slot_id].buf[:size], dtype=dtype)
        return arr.reshape(shape)

    def close(self):
        # Don't close - main process owns the shared memory.
        # Just clear our references and let them be garbage collected
        # after the process exits (avoids BufferError from numpy refs).
        self.slots = []


# ============================================================================
# FRAME READER — one thread per camera
# ============================================================================

class FrameReader(threading.Thread):
    """
    Thread that grabs frames from one camera and writes to the shared pool.
    """

    def __init__(
        self,
        camera_ip: str,
        rtsp_url: str,
        frame_pool: FramePool,
        frame_queue: multiprocessing.Queue,
        stop_event: threading.Event,
        target_fps: float = 5.0,
    ):
        super().__init__(daemon=True, name=f"reader-{camera_ip}")
        self.camera_ip = camera_ip
        self.rtsp_url = rtsp_url
        self.frame_pool = frame_pool
        self.frame_queue = frame_queue
        self.stop_event = stop_event
        self.target_fps = target_fps
        self.frame_interval = 1.0 / target_fps if target_fps > 0 else 0

        self.status = "starting"
        self.fps = 0.0
        self.frame_count = 0
        self.last_error: Optional[str] = None
        self._fps_times: List[float] = []
        self.started_at = time.time()
        # Debug counters
        self.grabs = 0
        self.skipped_rate = 0
        self.skipped_grey = 0
        self.skipped_retrieve = 0
        self.skipped_pool = 0
        self.skipped_oversize = 0
        self.last_std = 0.0  # debug: last frame's std value
        self._last_oversize_warn = 0.0
        # Latest frame for overlay (avoids opening second RTSP connection)
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_frame_ts: float = 0.0
        self._latest_frame_lock = threading.Lock()

    def run(self):
        reconnect_delay = 1.0
        max_reconnect_delay = 30.0

        while not self.stop_event.is_set():
            try:
                cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                if not cap.isOpened():
                    self.status = "reconnecting"
                    self.last_error = "VideoCapture failed to open"
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    continue

                self.status = "running"
                self.last_error = None
                reconnect_delay = 1.0
                last_frame_time = 0.0

                while not self.stop_event.is_set():
                    if not cap.grab():
                        self.status = "reconnecting"
                        self.last_error = "grab() failed"
                        break

                    self.grabs += 1
                    now = time.time()

                    # Rate limiting
                    if self.frame_interval > 0 and (now - last_frame_time) < self.frame_interval:
                        self.skipped_rate += 1
                        continue

                    ret, frame = cap.retrieve()
                    if not ret or frame is None:
                        self.skipped_retrieve += 1
                        continue

                    # Skip grey frames (disabled for testing)
                    self.last_std = float(frame.std())
                    # if self.last_std < 10:
                    #     self.skipped_grey += 1
                    #     continue

                    last_frame_time = now

                    # Store latest frame for overlay access (before pool write)
                    with self._latest_frame_lock:
                        self._latest_frame = frame.copy()
                        self._latest_frame_ts = now

                    # Acquire pool slot
                    slot_id = self.frame_pool.acquire(timeout=0.5)
                    if slot_id is None:
                        # Pool full, skip frame
                        self.skipped_pool += 1
                        continue

                    # Write frame to shared memory
                    frame_bytes = frame.nbytes
                    if frame_bytes > self.frame_pool.slot_size:
                        self.frame_pool.release(slot_id)
                        self.skipped_oversize += 1
                        if now - self._last_oversize_warn > 30:
                            logger.warning(f"FrameReader {self.camera_ip}: frame {frame_bytes/1e6:.1f}MB exceeds slot {self.frame_pool.slot_size/1e6:.0f}MB (skipped {self.skipped_oversize})")
                            self._last_oversize_warn = now
                        continue

                    try:
                        self.frame_pool.write(slot_id, frame)
                        self.frame_queue.put({
                            "camera_ip": self.camera_ip,
                            "slot_id": slot_id,
                            "shape": frame.shape,
                            "timestamp": now,
                        })
                        self.frame_count += 1
                        self._update_fps(now)
                    except Exception as e:
                        self.frame_pool.release(slot_id)
                        logger.warning(f"FrameReader {self.camera_ip}: write error: {e}")

                cap.release()

            except Exception as e:
                self.status = "error"
                self.last_error = str(e)
                logger.exception(f"FrameReader {self.camera_ip} error: {e}")
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

        self.status = "stopped"

    def _update_fps(self, now: float):
        self._fps_times.append(now)
        # Keep last 2 seconds
        cutoff = now - 2.0
        self._fps_times = [t for t in self._fps_times if t > cutoff]
        if len(self._fps_times) >= 2:
            elapsed = self._fps_times[-1] - self._fps_times[0]
            self.fps = (len(self._fps_times) - 1) / elapsed if elapsed > 0 else 0
        else:
            self.fps = 0.0

    def get_latest_frame(self) -> Tuple[Optional[np.ndarray], float]:
        """Get the most recent frame and its timestamp. Returns (None, 0) if no frame yet."""
        with self._latest_frame_lock:
            if self._latest_frame is None:
                return None, 0.0
            return self._latest_frame.copy(), self._latest_frame_ts


# ============================================================================
# DETECTOR PROCESS — one Detector per process
# ============================================================================

def detector_process(
    process_id: int,
    shm_names: List[str],
    slot_size: int,
    frame_queue: multiprocessing.Queue,
    result_queue: multiprocessing.Queue,
    release_queue: multiprocessing.Queue,
    stop_event: multiprocessing.Event,
    config: dict,
):
    """
    Detector worker process. Pulls frames from queue, detects, pushes results.

    Args:
        process_id: Identifier for this detector (0, 1, 2, ...)
        shm_names: List of shared memory names for the frame pool
        slot_size: Size of each slot in bytes
        frame_queue: Input queue of frame metadata
        result_queue: Output queue for detections + heartbeats
        release_queue: Queue to signal slot release back to main process
        stop_event: Shutdown signal
        config: Detection config (families, quad_decimate, etc.)
    """
    from pupil_apriltags import Detector

    # Connect to shared memory
    pool = FramePoolReader(shm_names, slot_size)

    # Suppress SharedMemory BufferError only during shutdown (harmless Windows quirk).
    import sys
    _orig_stderr = sys.stderr
    class _SuppressBufferErrorOnShutdown:
        def write(self, s):
            # Only suppress during shutdown (stop_event set)
            if stop_event.is_set() and ("BufferError" in s or "cannot close exported" in s):
                return
            _orig_stderr.write(s)
        def flush(self):
            _orig_stderr.flush()
    sys.stderr = _SuppressBufferErrorOnShutdown()

    # Create detector
    families = config.get("families", "tagCustom48h12")
    quad_decimate = config.get("quad_decimate", 2.0)
    nthreads = config.get("nthreads", 4)

    detector = Detector(
        families=families,
        nthreads=nthreads,
        quad_decimate=quad_decimate,
        quad_sigma=0.0,
        decode_sharpening=0.25,
    )

    logger.info(f"Detector process {process_id} started: families={families}, decimate={quad_decimate}, threads={nthreads}")

    # Send initial heartbeat
    result_queue.put({
        "type": "heartbeat",
        "process_id": process_id,
        "status": "running",
        "timestamp": time.time(),
    })

    last_heartbeat = time.time()
    detect_count = 0
    frames_processed = 0

    while not stop_event.is_set():
        try:
            # Get frame from queue (with timeout for heartbeat)
            try:
                msg = frame_queue.get(timeout=0.5)
            except Exception:
                # Timeout, send heartbeat
                now = time.time()
                if now - last_heartbeat >= 2.0:
                    result_queue.put({
                        "type": "heartbeat",
                        "frames_processed": frames_processed,
                        "process_id": process_id,
                        "status": "running",
                        "detect_count": detect_count,
                        "timestamp": now,
                    })
                    last_heartbeat = now
                continue

            camera_ip = msg["camera_ip"]
            slot_id = msg["slot_id"]
            shape = msg["shape"]
            frame_ts = msg["timestamp"]

            # Read frame from shared memory
            try:
                frame = pool.read(slot_id, shape)
            except Exception as e:
                logger.warning(f"Detector {process_id}: read error slot {slot_id}: {e}")
                release_queue.put(slot_id)
                continue

            # Convert to grayscale
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # Detect
            detections = detector.detect(gray)
            frames_processed += 1

            # Release slot ASAP
            release_queue.put(slot_id)

            # Send detections
            for det in detections:
                detect_count += 1
                result_queue.put({
                    "type": "detection",
                    "process_id": process_id,
                    "camera_ip": camera_ip,
                    "timestamp": frame_ts,
                    "detect_time": time.time(),
                    "tag_family": det.tag_family.decode() if isinstance(det.tag_family, bytes) else det.tag_family,
                    "tag_id": det.tag_id,
                    "center": det.center.tolist(),
                    "corners": det.corners.tolist(),
                    "decision_margin": float(det.decision_margin),
                })

            # Periodic heartbeat
            now = time.time()
            if now - last_heartbeat >= 2.0:
                result_queue.put({
                    "type": "heartbeat",
                    "process_id": process_id,
                    "status": "running",
                    "detect_count": detect_count,
                    "frames_processed": frames_processed,
                    "timestamp": now,
                })
                last_heartbeat = now

        except Exception as e:
            logger.exception(f"Detector {process_id} error: {e}")

    pool.close()
    logger.info(f"Detector process {process_id} stopped, {detect_count} total detections")


# ============================================================================
# POOL MANAGER — coordinates readers and detectors
# ============================================================================

class DetectionPool:
    """
    Manages frame readers (threads) and detector processes.

    Usage:
        pool = DetectionPool(num_detectors=3)
        pool.start()
        pool.add_camera("192.168.0.105", rtsp_url, target_fps=5.0)
        ...
        results = pool.get_results()  # list of detections
        pool.stop()
    """

    def __init__(
        self,
        num_detectors: int = 3,
        num_slots: int = 50,
        slot_size: int = 30_000_000,
        config: Optional[dict] = None,
    ):
        self.num_detectors = num_detectors
        self.config = config or {
            "families": "tagCustom48h12",
            "quad_decimate": 2.0,
            "nthreads": 4,
        }

        # Frame pool
        self.frame_pool = FramePool(num_slots=num_slots, slot_size=slot_size)

        # Queues
        self.frame_queue = multiprocessing.Queue()
        self.result_queue = multiprocessing.Queue()
        self.release_queue = multiprocessing.Queue()

        # Stop events
        self.stop_event = threading.Event()
        self.detector_stop = multiprocessing.Event()

        # State
        self.readers: Dict[str, FrameReader] = {}
        self.detectors: List[multiprocessing.Process] = []
        self.release_thread: Optional[threading.Thread] = None
        self._started = False

    def start(self):
        """Start detector processes and release thread."""
        if self._started:
            return

        # Start detector processes
        shm_names = self.frame_pool.get_all_shm_names()
        for i in range(self.num_detectors):
            p = multiprocessing.Process(
                target=detector_process,
                args=(
                    i,
                    shm_names,
                    self.frame_pool.slot_size,
                    self.frame_queue,
                    self.result_queue,
                    self.release_queue,
                    self.detector_stop,
                    self.config,
                ),
                daemon=True,
            )
            p.start()
            self.detectors.append(p)
            logger.info(f"Started detector process {i} (pid {p.pid})")

        # Start release thread (returns slots to pool)
        self.release_thread = threading.Thread(target=self._release_loop, daemon=True)
        self.release_thread.start()

        self._started = True
        logger.info(f"DetectionPool started: {self.num_detectors} detectors")

    def _release_loop(self):
        """Thread that releases slots back to the pool."""
        while not self.stop_event.is_set():
            try:
                slot_id = self.release_queue.get(timeout=0.5)
                self.frame_pool.release(slot_id)
            except Exception:
                continue

    def add_camera(self, camera_ip: str, rtsp_url: str, target_fps: float = 5.0):
        """Add a camera to the pool."""
        if camera_ip in self.readers:
            logger.warning(f"Camera {camera_ip} already in pool")
            return

        reader = FrameReader(
            camera_ip=camera_ip,
            rtsp_url=rtsp_url,
            frame_pool=self.frame_pool,
            frame_queue=self.frame_queue,
            stop_event=self.stop_event,
            target_fps=target_fps,
        )
        reader.start()
        self.readers[camera_ip] = reader
        logger.info(f"Added camera {camera_ip} at {target_fps} fps")

    def remove_camera(self, camera_ip: str):
        """Remove a camera from the pool."""
        reader = self.readers.pop(camera_ip, None)
        if reader:
            # Reader will stop on next iteration since we removed it
            # Could add per-reader stop event for cleaner shutdown
            logger.info(f"Removed camera {camera_ip}")

    def get_results(self, max_items: int = 100) -> List[dict]:
        """Drain result queue, return list of detections/heartbeats."""
        results = []
        while len(results) < max_items:
            try:
                msg = self.result_queue.get_nowait()
                results.append(msg)
            except Exception:
                break
        return results

    def get_latest_frame(self, camera_ip: str) -> Tuple[Optional[np.ndarray], float]:
        """Get the most recent frame from a camera's reader. Returns (None, 0) if not available."""
        reader = self.readers.get(camera_ip)
        if reader is None:
            return None, 0.0
        return reader.get_latest_frame()

    def get_status(self) -> dict:
        """Get status of readers and detectors."""
        # Queue depth (frames waiting for detectors) — reliable on Windows
        try:
            queue_depth = self.frame_queue.qsize()
        except NotImplementedError:
            queue_depth = None  # macOS doesn't support qsize()

        slots_in_use = sum(self.frame_pool.slot_in_use)
        slots_total = self.frame_pool.num_slots

        return {
            "readers": {
                ip: {
                    "status": r.status,
                    "fps": round(r.fps, 2),
                    "frame_count": r.frame_count,
                    "last_error": r.last_error,
                    "grabs": r.grabs,
                    "skipped_rate": r.skipped_rate,
                    "skipped_grey": r.skipped_grey,
                    "skipped_retrieve": r.skipped_retrieve,
                    "skipped_pool": r.skipped_pool,
                    "skipped_oversize": r.skipped_oversize,
                    "last_std": round(r.last_std, 1),
                    "started_at": r.started_at,
                }
                for ip, r in self.readers.items()
            },
            "detectors": [
                {"pid": p.pid, "alive": p.is_alive()}
                for p in self.detectors
            ],
            "pool_slots_in_use": slots_in_use,
            "pool_slots_total": slots_total,
            "queue_depth": queue_depth,
            "queue_pressure": round(slots_in_use / slots_total * 100, 1) if slots_total > 0 else 0,
        }

    def stop(self):
        """Stop all readers and detectors."""
        logger.info("Stopping DetectionPool...")

        # Stop readers first (they produce frames)
        self.stop_event.set()
        for ip, reader in self.readers.items():
            reader.join(timeout=2)
        self.readers.clear()

        # Drain remaining frames from queue so detectors can exit cleanly
        while True:
            try:
                msg = self.frame_queue.get_nowait()
                if "slot_id" in msg:
                    self.frame_pool.release(msg["slot_id"])
            except Exception:
                break

        # Stop detectors
        self.detector_stop.set()
        for p in self.detectors:
            p.join(timeout=5)
            if p.is_alive():
                p.kill()
        self.detectors.clear()

        # Small delay to let detector processes fully exit
        import time
        time.sleep(0.2)

        # Cleanup shared memory
        self.frame_pool.close()
        self._started = False
        logger.info("DetectionPool stopped")
