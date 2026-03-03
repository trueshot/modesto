"""
FrameMediator — single-flight coalescing + persistent RTSP streams.

Two modes per camera:
  Request mode (default): first request starts capture, concurrent requests
      wait on same Future. No TTL cache — each burst triggers a fresh capture.
  Stream mode (direct-IP cameras): persistent RTSP connection in a subprocess,
      latest decoded frame always in memory, sub-second delivery.
"""

import logging
import multiprocessing
import sqlite3
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "warehouses" / "lodge" / "lodge.db"

# ---------------------------------------------------------------------------
# resolve_camera_key — maps (nvr_id, channel) to physical camera identity
# ---------------------------------------------------------------------------

_key_cache: Dict[Tuple[str, int], str] = {}
_key_cache_lock = threading.Lock()


def resolve_camera_key(nvr_id: str, channel: int) -> str:
    """
    Map (nvr_id, channel) to a physical camera identity string.
    Returns "cam:<ip>" if the channel is linked to a camera with a known IP,
    otherwise "nvr:<nvr_id>:ch<channel>".

    Results are cached in-memory; call clear_key_cache() on config reload.
    """
    key = (nvr_id, channel)
    with _key_cache_lock:
        cached = _key_cache.get(key)
        if cached is not None:
            return cached

    result = f"nvr:{nvr_id}:ch{channel}"
    try:
        if DB_PATH.exists():
            conn = sqlite3.connect(str(DB_PATH), timeout=2)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT cam.ip
                FROM channels ch
                JOIN cameras cam ON ch.camera_id = cam.mac
                WHERE ch.nvr_id = ? AND ch.channel_number = ?
                  AND cam.ip IS NOT NULL
            """, (nvr_id, channel))
            row = cursor.fetchone()
            conn.close()
            if row and row["ip"]:
                result = f"cam:{row['ip']}"
    except Exception as e:
        logger.warning(f"resolve_camera_key({nvr_id}, {channel}) DB error: {e}")

    with _key_cache_lock:
        _key_cache[key] = result
    return result


def clear_key_cache():
    """Clear the camera key cache (call on config reload)."""
    with _key_cache_lock:
        _key_cache.clear()
    logger.info("Camera key cache cleared")


# ---------------------------------------------------------------------------
# _Inflight — tracks a single in-progress capture request
# ---------------------------------------------------------------------------

@dataclass
class _Inflight:
    """One capture in progress, possibly shared by multiple waiters."""
    camera_key: str
    future: Future
    gate_nvr_ip: Optional[str]       # NVR IP if gate slot was acquired
    nvr_id: str
    channel: int
    source: str                       # "rtsp", "direct", "playback", "snapshot"
    target_ip: Optional[str]
    started_at: float
    waiters: List[Any] = field(default_factory=list)
    # Each waiter is an opaque dict the caller provides; e.g.
    # {"identity": bytes, "response_id": str, "recv_at": float}


# ---------------------------------------------------------------------------
# CameraStream — persistent RTSP reader in a subprocess
# ---------------------------------------------------------------------------

def _stream_loop(
    camera_ip: str,
    rtsp_url: str,
    frame_queue: multiprocessing.Queue,
    stop_event: multiprocessing.Event,
    status_queue: multiprocessing.Queue,
):
    """
    Subprocess entry point. Opens a persistent RTSP connection and decodes
    frames continuously. Latest JPEG is pushed to frame_queue (old frames
    are discarded so the queue only ever holds the most recent one).
    """
    import os
    # Reduce OpenCV/FFmpeg log noise in subprocess
    os.environ["OPENCV_LOG_LEVEL"] = "ERROR"

    cap = None
    try:
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not cap.isOpened():
            status_queue.put({"event": "error", "msg": f"Cannot open {rtsp_url}"})
            return

        status_queue.put({"event": "started", "ip": camera_ip})
        frames_decoded = 0
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 95]

        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret or frame is None:
                # Connection lost — brief pause then retry
                time.sleep(0.5)
                cap.release()
                cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if not cap.isOpened():
                    status_queue.put({"event": "reconnect_failed", "ip": camera_ip})
                    time.sleep(2)
                else:
                    status_queue.put({"event": "reconnected", "ip": camera_ip})
                continue

            # Skip grey / garbage frames
            if frame.std() < 10:
                continue

            # Encode to JPEG
            ok, buf = cv2.imencode('.jpg', frame, encode_param)
            if not ok:
                continue

            jpeg_bytes = buf.tobytes()
            frames_decoded += 1

            # Drain old frame from queue so it only holds the latest
            try:
                frame_queue.get_nowait()
            except Exception:
                pass
            frame_queue.put((jpeg_bytes, time.time()))

    except Exception as e:
        try:
            status_queue.put({"event": "error", "msg": str(e), "ip": camera_ip})
        except Exception:
            pass
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        try:
            status_queue.put({"event": "stopped", "ip": camera_ip})
        except Exception:
            pass


class CameraStream:
    """
    Persistent RTSP reader for one camera IP.
    Runs decode loop in a subprocess to avoid GIL contention.
    """

    def __init__(self, camera_ip: str, rtsp_url: str):
        self.camera_ip = camera_ip
        self.rtsp_url = rtsp_url
        self._process: Optional[multiprocessing.Process] = None
        self._frame_queue: Optional[multiprocessing.Queue] = None
        self._stop_event: Optional[multiprocessing.Event] = None
        self._status_queue: Optional[multiprocessing.Queue] = None
        self._latest_frame: Optional[bytes] = None
        self._latest_ts: float = 0.0
        self._last_requested: float = 0.0
        self._started_at: float = 0.0
        self._frames_served: int = 0
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        """Start the subprocess stream reader."""
        if self._running:
            return
        self._frame_queue = multiprocessing.Queue(maxsize=2)
        self._stop_event = multiprocessing.Event()
        self._status_queue = multiprocessing.Queue(maxsize=32)
        self._process = multiprocessing.Process(
            target=_stream_loop,
            args=(
                self.camera_ip,
                self.rtsp_url,
                self._frame_queue,
                self._stop_event,
                self._status_queue,
            ),
            daemon=True,
        )
        self._process.start()
        self._started_at = time.time()
        self._running = True
        logger.info(f"CameraStream started for {self.camera_ip} (pid={self._process.pid})")

    def stop(self):
        """Stop the subprocess stream reader."""
        if not self._running:
            return
        self._running = False
        if self._stop_event:
            self._stop_event.set()
        if self._process and self._process.is_alive():
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=2)
        # Drain queues to avoid broken-pipe warnings
        for q in (self._frame_queue, self._status_queue):
            if q:
                try:
                    while not q.empty():
                        q.get_nowait()
                except Exception:
                    pass
        logger.info(f"CameraStream stopped for {self.camera_ip}")

    def _drain_frames(self):
        """Pull latest frame from the subprocess queue."""
        if not self._frame_queue:
            return
        got = False
        while True:
            try:
                jpeg, ts = self._frame_queue.get_nowait()
                self._latest_frame = jpeg
                self._latest_ts = ts
                got = True
            except Exception:
                break
        return got

    def get_frame(self) -> Optional[bytes]:
        """
        Return the latest decoded frame (JPEG bytes), or None if no frame yet.
        Updates last_requested timestamp for idle tracking.
        """
        self._drain_frames()
        with self._lock:
            self._last_requested = time.time()
            if self._latest_frame:
                self._frames_served += 1
                return self._latest_frame
            return None

    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.is_alive()

    @property
    def idle_seconds(self) -> float:
        if self._last_requested == 0:
            return time.time() - self._started_at
        return time.time() - self._last_requested

    @property
    def frame_age_ms(self) -> float:
        if self._latest_ts == 0:
            return -1
        return (time.time() - self._latest_ts) * 1000

    def status(self) -> dict:
        self._drain_frames()
        # Drain status messages
        status_msgs = []
        if self._status_queue:
            while True:
                try:
                    status_msgs.append(self._status_queue.get_nowait())
                except Exception:
                    break
        return {
            "camera_ip": self.camera_ip,
            "running": self.is_running,
            "pid": self._process.pid if self._process else None,
            "uptime_sec": round(time.time() - self._started_at, 1) if self._started_at else 0,
            "idle_sec": round(self.idle_seconds, 1),
            "frame_age_ms": round(self.frame_age_ms, 1),
            "frames_served": self._frames_served,
            "has_frame": self._latest_frame is not None,
            "recent_events": status_msgs[-5:] if status_msgs else [],
        }


# ---------------------------------------------------------------------------
# StreamManager — manages stream lifecycle
# ---------------------------------------------------------------------------

class StreamManager:
    """Manages CameraStream instances: open, close, idle reaping."""

    def __init__(self, idle_timeout: float = 60.0):
        self._streams: Dict[str, CameraStream] = {}  # camera_ip -> CameraStream
        self._lock = threading.Lock()
        self._idle_timeout = idle_timeout
        self._reaper_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self):
        """Start the idle-reaper background thread."""
        self._stop_event.clear()
        self._reaper_thread = threading.Thread(
            target=self._reaper_loop, daemon=True, name="stream-reaper"
        )
        self._reaper_thread.start()

    def shutdown(self):
        """Stop all streams and the reaper thread."""
        self._stop_event.set()
        with self._lock:
            for ip, stream in list(self._streams.items()):
                stream.stop()
            self._streams.clear()
        logger.info("StreamManager shutdown complete")

    def get_or_open(self, camera_ip: str, rtsp_url: str) -> CameraStream:
        """Get existing stream or open a new one."""
        with self._lock:
            stream = self._streams.get(camera_ip)
            if stream and stream.is_running:
                return stream
            # Clean up dead stream
            if stream:
                stream.stop()
            # Open new
            stream = CameraStream(camera_ip, rtsp_url)
            stream.start()
            self._streams[camera_ip] = stream
            return stream

    def get(self, camera_ip: str) -> Optional[CameraStream]:
        """Get existing stream if running, else None."""
        with self._lock:
            stream = self._streams.get(camera_ip)
            if stream and stream.is_running:
                return stream
            return None

    def close(self, camera_ip: str):
        """Explicitly close a stream."""
        with self._lock:
            stream = self._streams.pop(camera_ip, None)
        if stream:
            stream.stop()

    def close_idle(self):
        """Close streams that have been idle longer than timeout."""
        to_close = []
        with self._lock:
            for ip, stream in list(self._streams.items()):
                if stream.idle_seconds > self._idle_timeout:
                    to_close.append(ip)
                elif not stream.is_running:
                    to_close.append(ip)

        for ip in to_close:
            with self._lock:
                stream = self._streams.pop(ip, None)
            if stream:
                logger.info(f"Closing idle stream for {ip} (idle {stream.idle_seconds:.0f}s)")
                stream.stop()

    def status(self) -> dict:
        with self._lock:
            streams = {}
            for ip, stream in self._streams.items():
                streams[ip] = stream.status()
            return {
                "active_streams": len(self._streams),
                "idle_timeout_sec": self._idle_timeout,
                "streams": streams,
            }

    def _reaper_loop(self):
        """Background thread that periodically closes idle streams."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=10)
            if self._stop_event.is_set():
                break
            try:
                self.close_idle()
            except Exception as e:
                logger.error(f"StreamManager reaper error: {e}")


# ---------------------------------------------------------------------------
# FrameMediator — the main coordination class
# ---------------------------------------------------------------------------

class FrameMediator:
    """
    Coordinates frame acquisition across HTTP, ZMQ REP, and ZMQ ROUTER.

    - Request mode: single-flight coalescing via Futures
    - Stream mode: instant frames from persistent RTSP streams
    """

    def __init__(
        self,
        executor: ProcessPoolExecutor,
        nvr_gate,          # NvrGate instance
        stream_manager: StreamManager,
        resolve_url_fn: Callable,   # resolve_capture_url(nvr_id, channel, source)
        capture_fn: Callable,       # _capture_from_url(rtsp_url, timeout, cooldown, fmt)
        capture_cooldown_fn: Callable = lambda: 0.0,  # returns current CAPTURE_COOLDOWN
    ):
        self._executor = executor
        self._nvr_gate = nvr_gate
        self._streams = stream_manager
        self._resolve_url = resolve_url_fn
        self._capture_fn = capture_fn
        self._capture_cooldown_fn = capture_cooldown_fn

        # Inflight captures keyed by camera_key
        self._inflight: Dict[str, _Inflight] = {}
        self._lock = threading.Lock()

        # Stats
        self._coalesced_count = 0
        self._request_count = 0

    # ------------------------------------------------------------------
    # Blocking API (HTTP, ZMQ REP)
    # ------------------------------------------------------------------

    def get_frame(
        self,
        nvr_id: str,
        channel: int,
        source: str = "rtsp",
        timeout: float = 10.0,
        fmt: str = "jpeg",
    ) -> Optional[bytes]:
        """
        Blocking: get a frame for the given camera.
        Checks for active stream first, then coalesces with inflight,
        then starts a new capture.

        Returns JPEG/PNG bytes or None on failure.
        """
        self._request_count += 1
        camera_key = resolve_camera_key(nvr_id, channel)

        # 1. Check for active stream
        if camera_key.startswith("cam:"):
            camera_ip = camera_key[4:]
            stream = self._streams.get(camera_ip)
            if stream:
                frame = stream.get_frame()
                if frame:
                    return frame

        # 2. Check for inflight capture — coalesce
        with self._lock:
            inflight = self._inflight.get(camera_key)
            if inflight and not inflight.future.done():
                self._coalesced_count += 1
                future = inflight.future
            else:
                inflight = None

        if inflight is not None:
            # Wait on existing future
            try:
                result = future.result(timeout=timeout)
                return result if result else None
            except Exception:
                return None

        # 3. Start new capture
        rtsp_url, actual_source, target_ip = self._resolve_url(nvr_id, channel, source)
        if not rtsp_url:
            return None

        # Acquire gate if NVR path
        gate_nvr_ip = None
        if actual_source != "direct":
            if not self._nvr_gate.acquire(target_ip, timeout=timeout):
                return None
            gate_nvr_ip = target_ip

        try:
            cooldown = self._capture_cooldown_fn()
            future = self._executor.submit(self._capture_fn, rtsp_url, 8, cooldown, fmt)

            # Register inflight
            inf = _Inflight(
                camera_key=camera_key,
                future=future,
                gate_nvr_ip=gate_nvr_ip,
                nvr_id=nvr_id,
                channel=channel,
                source=actual_source,
                target_ip=target_ip,
                started_at=time.time(),
            )
            with self._lock:
                self._inflight[camera_key] = inf

            # Wait for result
            result = future.result(timeout=timeout)

            return result if result else None

        except Exception as e:
            logger.error(f"FrameMediator.get_frame error: {e}")
            return None
        finally:
            # Clean up inflight and release gate
            with self._lock:
                if camera_key in self._inflight and self._inflight[camera_key].future is future:
                    del self._inflight[camera_key]
            if gate_nvr_ip:
                self._nvr_gate.release(gate_nvr_ip)

    # ------------------------------------------------------------------
    # Non-blocking API (ZMQ ROUTER)
    # ------------------------------------------------------------------

    def try_get(self, camera_key: str) -> Optional[bytes]:
        """
        Non-blocking: if a stream exists for this camera, return latest
        frame bytes immediately. Otherwise return None.
        """
        if camera_key.startswith("cam:"):
            camera_ip = camera_key[4:]
            stream = self._streams.get(camera_ip)
            if stream:
                return stream.get_frame()
        return None

    def try_join(self, camera_key: str) -> Optional[_Inflight]:
        """
        Non-blocking: if there's an inflight capture for this camera_key,
        return the _Inflight object so the caller can add a waiter.
        Otherwise return None.
        """
        with self._lock:
            inflight = self._inflight.get(camera_key)
            if inflight and not inflight.future.done():
                return inflight
            return None

    def start_capture(
        self,
        camera_key: str,
        rtsp_url: str,
        gate_nvr_ip: Optional[str],
        nvr_id: str,
        channel: int,
        source: str,
        target_ip: Optional[str],
        fmt: str = "jpeg",
    ) -> _Inflight:
        """
        Non-blocking: submit a capture to the executor and register it
        as inflight. Returns the _Inflight for the caller to add waiters to.
        """
        cooldown = self._capture_cooldown_fn()
        future = self._executor.submit(self._capture_fn, rtsp_url, 8, cooldown, fmt)

        inf = _Inflight(
            camera_key=camera_key,
            future=future,
            gate_nvr_ip=gate_nvr_ip,
            nvr_id=nvr_id,
            channel=channel,
            source=source,
            target_ip=target_ip,
            started_at=time.time(),
        )
        with self._lock:
            self._inflight[camera_key] = inf
        return inf

    def add_waiter(self, camera_key: str, waiter_data: Any) -> bool:
        """
        Add a waiter to an inflight capture. Returns True if added,
        False if no inflight exists.
        """
        with self._lock:
            inflight = self._inflight.get(camera_key)
            if inflight and not inflight.future.done():
                inflight.waiters.append(waiter_data)
                self._coalesced_count += 1
                return True
            return False

    def get_completed(self) -> List[_Inflight]:
        """
        Return list of inflight captures whose futures are done.
        Removes them from the inflight dict.
        """
        completed = []
        with self._lock:
            done_keys = [
                k for k, inf in self._inflight.items()
                if inf.future.done()
            ]
            for k in done_keys:
                completed.append(self._inflight.pop(k))
        return completed

    def complete(self, camera_key: str) -> Optional[_Inflight]:
        """
        Remove and return the inflight for camera_key if its future is done.
        The caller is responsible for:
          - calling future.result() to get frame bytes
          - sending to all waiters
          - releasing the NVR gate if gate_nvr_ip is set
        """
        with self._lock:
            inflight = self._inflight.get(camera_key)
            if inflight and inflight.future.done():
                del self._inflight[camera_key]
                return inflight
            return None

    # ------------------------------------------------------------------
    # Stream management passthrough
    # ------------------------------------------------------------------

    def get_or_open_stream(self, camera_ip: str, rtsp_url: str) -> CameraStream:
        """Open or get a persistent stream for a camera IP."""
        return self._streams.get_or_open(camera_ip, rtsp_url)

    def close_stream(self, camera_ip: str):
        """Close a persistent stream."""
        self._streams.close(camera_ip)

    # ------------------------------------------------------------------
    # Status / stats
    # ------------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            inflight_info = {}
            for key, inf in self._inflight.items():
                inflight_info[key] = {
                    "nvr_id": inf.nvr_id,
                    "channel": inf.channel,
                    "source": inf.source,
                    "target_ip": inf.target_ip,
                    "started_at": inf.started_at,
                    "elapsed_sec": round(time.time() - inf.started_at, 1),
                    "waiters": len(inf.waiters),
                    "done": inf.future.done(),
                }
        return {
            "inflight_captures": len(inflight_info),
            "coalesced_total": self._coalesced_count,
            "requests_total": self._request_count,
            "inflight": inflight_info,
            "streams": self._streams.status(),
        }
