"""
HTTP camera mediator — persistent MJPEG stream proxy for M5Stack PoE-CAM
and other ESP32/W5500 HTTP cameras.

Why:
- Request/response locks up the W5500 socket pool after ~40 connection cycles;
  the camera stays bricked until power-cycled.
- A single persistent /stream connection is stable indefinitely (verified 12+
  minutes / 7000+ frames without any drift).

Design:
- One background worker thread per camera IP. The worker opens GET /stream once,
  parses multipart/x-mixed-replace frames forever, and updates a per-camera
  JPEG buffer.
- Consumers call get_frame(ip) and receive the latest buffered JPEG instantly.
- Idle grace: if no consumer asks for a frame within idle_grace_s, the worker
  exits and the stream closes (thermal duty cycling — the M5 doc warns that
  continuous streaming can overheat).
- Circuit breaker: after connect_fail_threshold consecutive reconnect failures,
  open the circuit for circuit_cooldown_s. get_frame() raises during cooldown.
- MJPEG parser: stock M5PoECam firmware sends a bogus per-part Content-Length
  (1073450776 observed); the planned M5CamServer replacement sends a correct
  one. We ignore both and scan for the next inter-frame boundary, which is
  firmware-agnostic — verified end-to-end against M5CamServer's protocol via
  m5camserver-emu (gen-43, 2026-04-25): 69 frames in 10s, 0 reconnects.

Author: modeltcamerascat gen-42 (verified against M5CamServer protocol gen-43)
"""

import json
import queue
import time
import logging
import threading
import http.client
import socket
from dataclasses import dataclass, field
from typing import Optional

import requests  # kept for requests.RequestException exception type on API

logger = logging.getLogger(__name__)


@dataclass
class _CamState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    frame_cond: threading.Condition = field(init=False)
    worker_thread: Optional[threading.Thread] = None
    stop_event: Optional[threading.Event] = None

    last_frame: Optional[bytes] = None
    last_frame_ts: float = 0.0
    last_consumer_access: float = 0.0

    # Session stats — reset on each worker restart
    session_frame_count: int = 0
    session_byte_count: int = 0
    stream_started_at: float = 0.0

    # Lifetime stats across worker restarts
    total_frame_count: int = 0
    total_byte_count: int = 0
    total_reconnects: int = 0

    last_error: Optional[str] = None
    consecutive_connect_fails: int = 0
    unhealthy_until: float = 0.0

    # Stream mode per cam:
    #   "GET"          GET /stream — works on stock M5PoECam and anything else.
    #   "POST_DUPLEX"  POST /stream — JSONL commands written upstream while
    #                  reading MJPEG downstream. Only valid for M5CamServer
    #                  cams (caller must verify firmware before enabling).
    stream_mode: str = "GET"
    cmd_queue: queue.Queue = field(default_factory=queue.Queue)
    cmd_count_sent: int = 0     # session counter — cleared on reconnect
    cmd_count_total: int = 0    # lifetime counter

    # Cached http_path captured on first get_frame so worker restarts
    # (after enable_duplex / disable_duplex) and command-only callers
    # (send_command) can spin up a worker without re-passing the path.
    http_path: str = "/stream"

    def __post_init__(self):
        self.frame_cond = threading.Condition(self.lock)


class HttpCameraMediator:
    """
    Persistent per-IP MJPEG stream proxy.

    Config:
        idle_grace_s: close the upstream stream after this many seconds of no
            consumer calls. Default 60s.
        first_frame_timeout_s: how long get_frame() will block on a cold start
            waiting for the worker's first frame. Default 5s.
        connect_fail_threshold: consecutive connect failures before opening
            circuit. Default 3.
        circuit_cooldown_s: how long the circuit stays open. Default 30s.
        reconnect_backoff_s: wait between reconnect attempts. Default 1s.
        read_timeout_s: socket read timeout during streaming. Default 10s.
    """

    def __init__(
        self,
        idle_grace_s: float = 60.0,
        first_frame_timeout_s: float = 5.0,
        stale_after_ms: int = 2000,
        connect_fail_threshold: int = 3,
        circuit_cooldown_s: float = 30.0,
        reconnect_backoff_s: float = 1.0,
        read_timeout_s: float = 10.0,
    ):
        self.idle_grace_s = idle_grace_s
        self.first_frame_timeout_s = first_frame_timeout_s
        self.stale_after_ms = stale_after_ms
        self.connect_fail_threshold = connect_fail_threshold
        self.circuit_cooldown_s = circuit_cooldown_s
        self.reconnect_backoff_s = reconnect_backoff_s
        self.read_timeout_s = read_timeout_s
        self._cams: dict[str, _CamState] = {}
        self._cams_lock = threading.Lock()

    def _state(self, ip: str) -> _CamState:
        with self._cams_lock:
            s = self._cams.get(ip)
            if s is None:
                s = _CamState()
                self._cams[ip] = s
            return s

    def get_frame(self, ip: str, http_path: str = "/stream") -> bytes:
        """
        Return the latest JPEG frame. Starts the stream worker on demand.
        If the cached frame is older than stale_after_ms, waits for a fresh
        frame (up to first_frame_timeout_s). Otherwise returns instantly.

        Raises requests.RequestException if circuit is open or no fresh
        frame arrives within the timeout.
        """
        s = self._state(ip)
        now = time.time()

        with s.lock:
            s.last_consumer_access = now

            if now < s.unhealthy_until:
                raise requests.RequestException(
                    f"{ip} circuit open for {s.unhealthy_until - now:.1f}s more "
                    f"(last error: {s.last_error})"
                )

            s.http_path = http_path
            self._ensure_worker(ip, s)

            frame_age_ms = (now - s.last_frame_ts) * 1000 if s.last_frame_ts else float('inf')
            if s.last_frame is not None and frame_age_ms < self.stale_after_ms:
                return s.last_frame

            # Either no frame yet, or the cached frame is stale — wait for a
            # new one. Capture ts to detect arrival of a newer frame.
            wait_after_ts = s.last_frame_ts
            deadline = now + self.first_frame_timeout_s

            def fresh_frame_available():
                return (
                    s.last_frame is not None
                    and s.last_frame_ts > wait_after_ts
                )

            # Wait with timeout using the Condition. The worker notifies
            # s.frame_cond on every published frame.
            while not fresh_frame_available():
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise requests.RequestException(
                        f"{ip} fresh-frame timeout: {s.last_error or 'no frame arrived'}"
                    )
                s.frame_cond.wait(timeout=remaining)

            return s.last_frame

    def _ensure_worker(self, ip: str, s: _CamState):
        """Start a stream worker if none is running. Caller must hold s.lock.
        Uses the http_path stored in state (captured on first get_frame call;
        defaults to "/stream" if no caller has supplied one yet)."""
        if s.worker_thread is not None and s.worker_thread.is_alive():
            return
        s.stop_event = threading.Event()
        s.stream_started_at = time.time()
        s.session_frame_count = 0
        s.session_byte_count = 0
        s.cmd_count_sent = 0
        t = threading.Thread(
            target=self._worker_loop,
            args=(ip, s.http_path, s),
            name=f"mjpeg-{ip}",
            daemon=True,
        )
        s.worker_thread = t
        t.start()

    def _worker_loop(self, ip: str, http_path: str, s: _CamState):
        """Maintain a persistent MJPEG stream, update the buffer on each frame.
        Dispatches to GET or POST-duplex implementation based on stream_mode."""
        logger.info(f"HTTP camera {ip}: stream worker starting ({http_path}, mode={s.stream_mode})")

        while not s.stop_event.is_set():
            now = time.time()
            # Idle grace check.
            with s.lock:
                idle_for = now - s.last_consumer_access
            if idle_for > self.idle_grace_s:
                logger.info(
                    f"HTTP camera {ip}: idle for {idle_for:.1f}s, closing stream"
                )
                return

            mode = s.stream_mode
            try:
                if mode == "POST_DUPLEX":
                    self._run_post_duplex_stream(ip, http_path, s)
                else:
                    self._run_get_stream(ip, http_path, s)

                if s.stop_event.is_set():
                    return

            except (socket.timeout, ConnectionError, http.client.HTTPException, OSError) as e:
                err = f"{type(e).__name__}: {e}"
                with s.lock:
                    s.last_error = err
                    s.consecutive_connect_fails += 1
                    s.total_reconnects += 1
                    if s.consecutive_connect_fails >= self.connect_fail_threshold:
                        s.unhealthy_until = time.time() + self.circuit_cooldown_s
                        logger.warning(
                            f"HTTP camera {ip}: circuit opened for "
                            f"{self.circuit_cooldown_s}s after "
                            f"{s.consecutive_connect_fails} consecutive failures ({err})"
                        )
                        return
                logger.warning(f"HTTP camera {ip}: connect/stream failed ({err}); retrying")

            # Backoff before next reconnect attempt.
            if s.stop_event.wait(self.reconnect_backoff_s):
                return

    def _run_get_stream(self, ip: str, http_path: str, s: _CamState):
        """Plain GET /stream via http.client. Firmware-agnostic — works on
        stock M5PoECam and M5CamServer alike."""
        conn = None
        resp = None
        try:
            conn = http.client.HTTPConnection(ip, timeout=self.read_timeout_s)
            conn.request("GET", http_path)
            resp = conn.getresponse()

            if resp.status != 200:
                raise ConnectionError(f"HTTP {resp.status}")

            ctype = resp.getheader("Content-Type", "")
            if "multipart" not in ctype:
                raise ConnectionError(f"non-multipart Content-Type: {ctype}")

            boundary = None
            for part in ctype.split(";"):
                part = part.strip()
                if part.lower().startswith("boundary="):
                    boundary = part.split("=", 1)[1].strip().strip('"')
                    break
            if not boundary:
                raise ConnectionError(f"no boundary in Content-Type: {ctype}")

            with s.lock:
                s.consecutive_connect_fails = 0
                s.last_error = None

            self._read_mjpeg_stream(ip, resp, boundary, s)

        finally:
            if resp is not None:
                try: resp.close()
                except Exception: pass
            if conn is not None:
                try: conn.close()
                except Exception: pass

    def _run_post_duplex_stream(self, ip: str, http_path: str, s: _CamState):
        """POST /stream via raw socket — alternates writing JSONL command
        bytes upstream and reading MJPEG frames downstream on the same TCP
        connection. Pattern matches M5CamServer's clients/python/cam_client.py.
        Only valid for M5CamServer cams; caller must verify firmware first."""
        # Parse host:port (mediator usually gets bare IPs but be defensive)
        if ":" in ip:
            host, port_str = ip.split(":", 1)
            port = int(port_str)
        else:
            host, port = ip, 80

        sock = socket.create_connection((host, port), timeout=self.read_timeout_s)
        try:
            # Send headers only — no Content-Length, body is streamed JSONL
            request = (
                f"POST {http_path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Content-Type: application/jsonl\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode("ascii")
            sock.sendall(request)

            # Short read timeout so we can interleave queue draining
            sock.settimeout(0.2)

            # Read response headers
            buf = b""
            deadline = time.time() + self.read_timeout_s
            while b"\r\n\r\n" not in buf and time.time() < deadline:
                if s.stop_event.is_set():
                    return
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("EOF before response headers")
                buf += chunk
            if b"\r\n\r\n" not in buf:
                raise ConnectionError("timeout waiting for response headers")
            head, _, body = buf.partition(b"\r\n\r\n")
            head_lines = head.decode("latin1", errors="replace").split("\r\n")
            status_line = head_lines[0] if head_lines else ""
            parts = status_line.split(" ", 2)
            if len(parts) < 2 or parts[1] != "200":
                raise ConnectionError(f"non-200 status: {status_line}")

            ctype = ""
            for line in head_lines[1:]:
                if line.lower().startswith("content-type:"):
                    ctype = line.split(":", 1)[1].strip()
                    break
            if "multipart" not in ctype.lower():
                raise ConnectionError(f"non-multipart Content-Type: {ctype}")
            boundary = None
            for piece in ctype.split(";"):
                piece = piece.strip()
                if piece.lower().startswith("boundary="):
                    boundary = piece.split("=", 1)[1].strip().strip('"')
                    break
            if not boundary:
                raise ConnectionError(f"no boundary in Content-Type: {ctype}")

            with s.lock:
                s.consecutive_connect_fails = 0
                s.last_error = None
                s.cmd_count_sent = 0

            self._duplex_read_write_loop(ip, sock, body, boundary, s)

        finally:
            try: sock.close()
            except Exception: pass

    def _duplex_read_write_loop(self, ip: str, sock: socket.socket,
                                  initial_buf: bytes, boundary: str, s: _CamState):
        """Inner loop for POST_DUPLEX mode: drain command queue → recv → parse
        frames → repeat. Same multipart parsing shape as _read_mjpeg_stream
        (boundary scanning, ignore per-part Content-Length) but with the
        command-write step interleaved between recvs."""
        boundary_bytes = b"--" + boundary.encode()
        next_sep = b"\r\n" + boundary_bytes
        buf = initial_buf

        # Discard preamble to first boundary
        while True:
            if s.stop_event.is_set():
                return
            idx = buf.find(boundary_bytes)
            if idx >= 0:
                end = idx + len(boundary_bytes)
                if buf[end:end+2] == b"\r\n":
                    end += 2
                buf = buf[end:]
                break
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("EOF before first boundary")
            buf += chunk

        while not s.stop_event.is_set():
            # Idle grace
            if time.time() - s.last_consumer_access > self.idle_grace_s:
                logger.info(f"HTTP camera {ip}: idle grace reached mid-duplex-stream, closing")
                return

            # Drain the command queue
            try:
                while True:
                    cmd = s.cmd_queue.get_nowait()
                    line = (json.dumps(cmd) + "\n").encode("utf-8")
                    sock.sendall(line)
                    with s.lock:
                        s.cmd_count_sent += 1
                        s.cmd_count_total += 1
                    logger.info(f"HTTP camera {ip}: sent duplex command: {cmd}")
            except queue.Empty:
                pass

            # Skip part headers
            while b"\r\n\r\n" not in buf:
                if s.stop_event.is_set():
                    return
                try:
                    chunk = sock.recv(8192)
                except socket.timeout:
                    chunk = b""
                if chunk == b"":
                    if not chunk:
                        # A timeout returns b"" too; distinguish by checking
                        # whether the connection is still alive — try a tiny
                        # send (no-op) or just continue. If peer closed we'll
                        # get EOF on next recv attempt; the timeout case
                        # gives us a chance to drain commands.
                        # Drain commands and continue
                        try:
                            while True:
                                cmd = s.cmd_queue.get_nowait()
                                line = (json.dumps(cmd) + "\n").encode("utf-8")
                                sock.sendall(line)
                                with s.lock:
                                    s.cmd_count_sent += 1
                                    s.cmd_count_total += 1
                                logger.info(f"HTTP camera {ip}: sent duplex command: {cmd}")
                        except queue.Empty:
                            pass
                        continue
                buf += chunk
            buf = buf[buf.index(b"\r\n\r\n") + 4:]

            # Read JPEG body until next boundary
            sep_idx = buf.find(next_sep)
            while sep_idx == -1:
                if s.stop_event.is_set():
                    return
                try:
                    chunk = sock.recv(8192)
                except socket.timeout:
                    chunk = b""
                if not chunk:
                    # Could be timeout or EOF — try draining commands then continue
                    try:
                        while True:
                            cmd = s.cmd_queue.get_nowait()
                            line = (json.dumps(cmd) + "\n").encode("utf-8")
                            sock.sendall(line)
                            with s.lock:
                                s.cmd_count_sent += 1
                                s.cmd_count_total += 1
                            logger.info(f"HTTP camera {ip}: sent duplex command: {cmd}")
                    except queue.Empty:
                        pass
                    continue
                buf += chunk
                sep_idx = buf.find(next_sep)

            jpeg_bytes = buf[:sep_idx]
            end = sep_idx + len(next_sep)
            if buf[end:end+2] == b"\r\n":
                end += 2
            buf = buf[end:]

            now = time.time()
            with s.frame_cond:
                s.last_frame = jpeg_bytes
                s.last_frame_ts = now
                s.session_frame_count += 1
                s.session_byte_count += len(jpeg_bytes)
                s.total_frame_count += 1
                s.total_byte_count += len(jpeg_bytes)
                s.frame_cond.notify_all()

    def _read_mjpeg_stream(
        self, ip: str, resp: http.client.HTTPResponse, boundary: str, s: _CamState
    ):
        """
        Parse multipart/x-mixed-replace and update the buffer on each JPEG frame.
        Ignores the per-part Content-Length (bogus in M5 firmware) and scans
        for the next boundary instead.
        Exits on EOF, stop_event, or idle grace.
        """
        boundary_bytes = b"--" + boundary.encode()
        next_sep = b"\r\n" + boundary_bytes
        raw = resp.fp

        # Discard preamble up to and including the first boundary.
        buf = b""
        while True:
            if s.stop_event.is_set():
                return
            chunk = raw.read1(4096)
            if not chunk:
                raise ConnectionError("EOF before first boundary")
            buf += chunk
            idx = buf.find(boundary_bytes)
            if idx != -1:
                end = idx + len(boundary_bytes)
                if buf[end:end+2] == b"\r\n":
                    end += 2
                buf = buf[end:]
                break

        while not s.stop_event.is_set():
            # Idle grace — stop streaming if nobody's asking.
            if time.time() - s.last_consumer_access > self.idle_grace_s:
                logger.info(f"HTTP camera {ip}: idle grace reached mid-stream, closing")
                return

            # Skip part headers (Content-Type, Content-Length) until \r\n\r\n
            while b"\r\n\r\n" not in buf:
                chunk = raw.read1(8192)
                if not chunk:
                    raise ConnectionError("EOF reading part headers")
                buf += chunk
            buf = buf[buf.index(b"\r\n\r\n") + 4:]

            # Read JPEG until next inter-frame separator.
            sep_idx = buf.find(next_sep)
            while sep_idx == -1:
                chunk = raw.read1(8192)
                if not chunk:
                    raise ConnectionError("EOF reading frame body")
                buf += chunk
                sep_idx = buf.find(next_sep)

            jpeg_bytes = buf[:sep_idx]
            end = sep_idx + len(next_sep)
            if buf[end:end+2] == b"\r\n":
                end += 2
            buf = buf[end:]

            # Publish the frame.
            now = time.time()
            with s.frame_cond:
                s.last_frame = jpeg_bytes
                s.last_frame_ts = now
                s.session_frame_count += 1
                s.session_byte_count += len(jpeg_bytes)
                s.total_frame_count += 1
                s.total_byte_count += len(jpeg_bytes)
                s.frame_cond.notify_all()

    def status(self) -> dict:
        """Per-camera state for introspection."""
        now = time.time()
        out = {}
        with self._cams_lock:
            cams = list(self._cams.items())
        for ip, s in cams:
            with s.lock:
                worker_alive = s.worker_thread is not None and s.worker_thread.is_alive()
                stream_uptime = (
                    round(now - s.stream_started_at, 1)
                    if worker_alive and s.stream_started_at else None
                )
                fps = (
                    round(s.session_frame_count / (now - s.stream_started_at), 2)
                    if worker_alive and s.stream_started_at and now > s.stream_started_at
                    else None
                )
                out[ip] = {
                    "worker_alive": worker_alive,
                    "has_frame": s.last_frame is not None,
                    "frame_size": len(s.last_frame) if s.last_frame else 0,
                    "frame_age_ms": round((now - s.last_frame_ts) * 1000, 1) if s.last_frame_ts else None,
                    "session_frame_count": s.session_frame_count,
                    "session_fps": fps,
                    "session_uptime_s": stream_uptime,
                    "total_frame_count": s.total_frame_count,
                    "total_byte_count": s.total_byte_count,
                    "total_reconnects": s.total_reconnects,
                    "last_consumer_age_s": round(now - s.last_consumer_access, 1) if s.last_consumer_access else None,
                    "consecutive_connect_fails": s.consecutive_connect_fails,
                    "last_error": s.last_error,
                    "circuit_open": now < s.unhealthy_until,
                    "circuit_reopens_in_s": round(max(0, s.unhealthy_until - now), 1),
                    "stream_mode": s.stream_mode,
                    "cmd_queue_depth": s.cmd_queue.qsize(),
                    "cmd_count_session": s.cmd_count_sent,
                    "cmd_count_total": s.cmd_count_total,
                }
        return out

    def reset_circuit(self, ip: str) -> bool:
        """Clear the circuit breaker for a camera (after power-cycle)."""
        with self._cams_lock:
            s = self._cams.get(ip)
        if not s:
            return False
        with s.lock:
            s.unhealthy_until = 0.0
            s.consecutive_connect_fails = 0
            s.last_error = None
        return True

    def _switch_mode(self, ip: str, new_mode: str) -> bool:
        """Internal: change stream_mode and synchronously stop+join the old
        worker so the next ensure_worker call starts fresh in the new mode.
        Returns True if mode changed, False if already in new_mode."""
        s = self._state(ip)
        with s.lock:
            if s.stream_mode == new_mode:
                return False
            s.stream_mode = new_mode
            old_worker = s.worker_thread
            old_event = s.stop_event
            # Pre-clear so _ensure_worker (called next) sees no live worker
            s.worker_thread = None
        if old_event is not None:
            old_event.set()
        if old_worker is not None and old_worker.is_alive():
            # Bounded wait — worker checks stop_event between frames (~ms-200ms)
            old_worker.join(timeout=2.0)
        return True

    def enable_duplex(self, ip: str) -> bool:
        """Switch this cam's stream mode to POST_DUPLEX. Caller MUST verify
        the cam runs M5CamServer first (stock M5PoECam doesn't speak POST
        /stream and will misbehave). Synchronously stops + joins any
        currently-running GET worker; the next get_frame or send_command
        call starts a fresh worker in POST_DUPLEX mode.

        Returns True regardless of prior state — idempotent."""
        changed = self._switch_mode(ip, "POST_DUPLEX")
        if changed:
            logger.info(f"HTTP camera {ip}: enabled POST_DUPLEX mode")
        return True

    def disable_duplex(self, ip: str) -> bool:
        """Revert this cam to plain GET /stream mode. Synchronously stops
        + joins the current worker so the next get_frame reconnects without
        the duplex protocol."""
        changed = self._switch_mode(ip, "GET")
        if changed:
            logger.info(f"HTTP camera {ip}: reverted to GET mode")
        return True

    def send_command(self, ip: str, cmd: dict) -> bool:
        """Queue a JSON command to be written upstream over the duplex
        connection. Starts a worker if none is running (using the cached
        http_path; defaults to "/stream" if no get_frame has been called
        yet). Cam must be in POST_DUPLEX mode — call enable_duplex first.

        Returns True on enqueue. Raises ValueError if the cam isn't in
        duplex mode."""
        s = self._state(ip)
        with s.lock:
            if s.stream_mode != "POST_DUPLEX":
                raise ValueError(
                    f"{ip} is in {s.stream_mode} mode; call enable_duplex(ip) first"
                )
            s.last_consumer_access = time.time()
            self._ensure_worker(ip, s)
        s.cmd_queue.put(cmd)
        return True

    def shutdown(self):
        """Stop all workers. Call on service shutdown."""
        with self._cams_lock:
            cams = list(self._cams.values())
        for s in cams:
            if s.stop_event is not None:
                s.stop_event.set()
        for s in cams:
            if s.worker_thread is not None:
                s.worker_thread.join(timeout=3)
