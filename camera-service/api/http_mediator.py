"""
HTTP camera mediator — single-flight frame proxy for flaky ESP32/PoE-CAM devices.

Problem:
- M5Stack PoE-CAM and similar ESP32 HTTP cameras die after 1-2 concurrent connections.
- Direct consumer access causes lockups requiring power cycle.

Solution:
- One persistent requests.Session per camera IP (HTTP keep-alive).
- Coalesce concurrent callers onto a single in-flight fetch (single-flight).
- TTL cache so rapid repeat calls hit the buffer, not the device.
- Circuit-break after consecutive failures so locked-up cameras don't thrash.

Author: modeltcamerascat gen-42
"""

import time
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class _CamState:
    session: requests.Session = field(default_factory=requests.Session)
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_frame: Optional[bytes] = None
    last_ts: float = 0.0
    last_error: Optional[str] = None
    in_flight: Optional[threading.Event] = None
    consecutive_fails: int = 0
    unhealthy_until: float = 0.0
    ok_count: int = 0
    fail_count: int = 0


class HttpCameraMediator:
    """
    Per-IP frame proxy with coalescing and circuit breaker.

    Config:
        cache_ttl_ms: return cached frame if younger than this (default 500ms)
        timeout_s: upstream HTTP timeout (default 5s)
        circuit_fail_threshold: consecutive failures to open circuit (default 3)
        circuit_cooldown_s: how long the circuit stays open (default 30s)
    """

    def __init__(
        self,
        cache_ttl_ms: int = 500,
        timeout_s: float = 5.0,
        circuit_fail_threshold: int = 3,
        circuit_cooldown_s: float = 30.0,
    ):
        self.cache_ttl_ms = cache_ttl_ms
        self.timeout_s = timeout_s
        self.circuit_fail_threshold = circuit_fail_threshold
        self.circuit_cooldown_s = circuit_cooldown_s
        self._cams: dict[str, _CamState] = {}
        self._cams_lock = threading.Lock()

    def _state(self, ip: str) -> _CamState:
        with self._cams_lock:
            s = self._cams.get(ip)
            if s is None:
                s = _CamState()
                self._cams[ip] = s
            return s

    def get_frame(self, ip: str, http_path: str = "/") -> bytes:
        """
        Return a JPEG frame from the camera. Coalesces with concurrent callers
        and returns the cached frame if one is fresher than cache_ttl_ms.

        Raises requests.RequestException on upstream failure (or circuit open).
        """
        s = self._state(ip)
        now = time.time()

        # Fast path: fresh cache hit.
        with s.lock:
            if s.last_frame is not None and (now - s.last_ts) * 1000 < self.cache_ttl_ms:
                return s.last_frame

            # Circuit open?
            if now < s.unhealthy_until:
                raise requests.RequestException(
                    f"{ip} circuit open until {s.unhealthy_until - now:.1f}s "
                    f"(last error: {s.last_error})"
                )

            # Join existing in-flight fetch, or become leader.
            if s.in_flight is None:
                ev = threading.Event()
                s.in_flight = ev
                is_leader = True
            else:
                ev = s.in_flight
                is_leader = False

        if not is_leader:
            # Waiter: block until leader finishes, then return the result.
            ev.wait(timeout=self.timeout_s + 1)
            with s.lock:
                if s.last_frame is not None and (time.time() - s.last_ts) * 1000 < self.cache_ttl_ms + 2000:
                    return s.last_frame
                raise requests.RequestException(
                    f"{ip} coalesced fetch failed: {s.last_error or 'unknown'}"
                )

        # Leader: perform the actual fetch outside the lock.
        try:
            url = f"http://{ip}{http_path}"
            resp = s.session.get(url, timeout=self.timeout_s)
            resp.raise_for_status()
            body = resp.content
            if not body:
                raise requests.RequestException("empty body")

            with s.lock:
                s.last_frame = body
                s.last_ts = time.time()
                s.last_error = None
                s.consecutive_fails = 0
                s.ok_count += 1
            return body

        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            with s.lock:
                s.last_error = err
                s.consecutive_fails += 1
                s.fail_count += 1
                if s.consecutive_fails >= self.circuit_fail_threshold:
                    s.unhealthy_until = time.time() + self.circuit_cooldown_s
                    logger.warning(
                        f"HTTP camera {ip}: circuit opened for {self.circuit_cooldown_s}s "
                        f"after {s.consecutive_fails} consecutive failures ({err})"
                    )
                # Drop the session on error so next attempt reconnects fresh.
                try:
                    s.session.close()
                except Exception:
                    pass
                s.session = requests.Session()
            raise

        finally:
            with s.lock:
                if s.in_flight is ev:
                    s.in_flight = None
            ev.set()

    def status(self) -> dict:
        """Per-camera health snapshot."""
        now = time.time()
        out = {}
        with self._cams_lock:
            cams = list(self._cams.items())
        for ip, s in cams:
            with s.lock:
                frame_age_ms = round((now - s.last_ts) * 1000, 1) if s.last_ts else None
                out[ip] = {
                    "ok_count": s.ok_count,
                    "fail_count": s.fail_count,
                    "consecutive_fails": s.consecutive_fails,
                    "last_error": s.last_error,
                    "circuit_open": now < s.unhealthy_until,
                    "circuit_reopens_in_s": round(max(0, s.unhealthy_until - now), 1),
                    "has_frame": s.last_frame is not None,
                    "frame_size": len(s.last_frame) if s.last_frame else 0,
                    "frame_age_ms": frame_age_ms,
                }
        return out

    def reset_circuit(self, ip: str) -> bool:
        """Clear circuit breaker for a camera (after power-cycle)."""
        s = self._cams.get(ip)
        if not s:
            return False
        with s.lock:
            s.unhealthy_until = 0.0
            s.consecutive_fails = 0
            s.last_error = None
        return True
