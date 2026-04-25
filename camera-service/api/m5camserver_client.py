"""
M5CamServer protocol client — TTL-cached /version probing.

Why /version probing needs care: stock M5PoECam firmware returns a JPEG
on ANY URL path (except /stream), so a GET /version on a stock cam
both fails to identify it (no JSON) and burns one of the W5500 socket
pool cycles that wedge the device after ~40 of them. We cache aggressively
to avoid hammering — default TTL 300s. Cache key is the IP string the
caller passes (so 127.0.0.1:8090 and 127.0.0.1 are distinct entries —
useful for the emulator).

Companion to http_mediator.py. Lives separately so M5CamServer-specific
operations (version, OTA, /control, eventual POST /stream duplex) can
accumulate here without bloating the streaming proxy.

Author: modeltcamerascat gen-43
"""

import json
import http.client
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VersionInfo:
    """Result of one /version probe. is_m5camserver=False when the cam
    didn't respond with M5CamServer-shaped JSON (could be stock firmware,
    a non-camera HTTP device, or unreachable). The error field always
    explains the negative case."""
    is_m5camserver: bool
    sketch: Optional[str] = None
    sketch_md5: Optional[str] = None
    build_date: Optional[str] = None
    build_time: Optional[str] = None
    uptime_s: Optional[int] = None
    free_heap: Optional[int] = None
    camera_ok: Optional[bool] = None
    resolutions: Optional[list] = field(default=None)
    error: Optional[str] = None
    fetched_at: float = 0.0


class M5CamServerProbe:
    """TTL-cached /version probes."""

    # Fields M5CamServer.cpp::serveVersion always emits — used to verify
    # the response is actually from an M5CamServer and not a coincidentally-
    # JSON-returning device.
    _REQUIRED_FIELDS = frozenset({"sketch", "sketch_md5", "camera_ok"})

    def __init__(self, ttl_s: float = 300.0, timeout_s: float = 3.0):
        self.ttl_s = ttl_s
        self.timeout_s = timeout_s
        self._cache: dict[str, VersionInfo] = {}
        self._lock = threading.Lock()

    def _is_fresh(self, info: VersionInfo) -> bool:
        return (time.time() - info.fetched_at) < self.ttl_s

    def version(self, ip: str, port: int = 80, force: bool = False) -> VersionInfo:
        """
        Return cached VersionInfo if still fresh; otherwise probe and cache.
        Pass force=True to bypass cache and refetch (e.g. after a known firmware
        flash or power cycle).
        """
        cache_key = f"{ip}:{port}"
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None and not force and self._is_fresh(cached):
                return cached

        info = self._probe(ip, port)
        with self._lock:
            self._cache[cache_key] = info
        return info

    def _probe(self, ip: str, port: int) -> VersionInfo:
        info = VersionInfo(is_m5camserver=False, fetched_at=time.time())
        conn = None
        try:
            conn = http.client.HTTPConnection(ip, port=port, timeout=self.timeout_s)
            conn.request("GET", "/version")
            resp = conn.getresponse()
            if resp.status != 200:
                info.error = f"HTTP {resp.status}"
                return info
            ct = (resp.getheader("Content-Type") or "").lower()
            body = resp.read(8192)
            if "json" not in ct:
                # Stock M5PoECam returns a JPEG for any non-/stream path,
                # so a non-JSON response is the signature of stock firmware
                # (or any device that doesn't speak M5CamServer).
                info.error = f"non-JSON response ({ct or 'unknown'}) — likely stock firmware"
                return info
            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                info.error = f"invalid JSON: {e}"
                return info
            if not isinstance(data, dict) or not self._REQUIRED_FIELDS.issubset(data.keys()):
                info.error = "JSON missing M5CamServer fields"
                return info
            info.is_m5camserver = True
            info.sketch       = data.get("sketch")
            info.sketch_md5   = data.get("sketch_md5")
            info.build_date   = data.get("build_date")
            info.build_time   = data.get("build_time")
            info.uptime_s     = data.get("uptime_s")
            info.free_heap    = data.get("free_heap")
            info.camera_ok    = data.get("camera_ok")
            info.resolutions  = data.get("resolutions")
            return info
        except Exception as e:
            info.error = f"{type(e).__name__}: {e}"
            return info
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def invalidate(self, ip: str, port: int = 80):
        """Drop the cached entry for this IP — next call refetches."""
        cache_key = f"{ip}:{port}"
        with self._lock:
            self._cache.pop(cache_key, None)

    def status(self) -> dict:
        """Per-IP cached info, with freshness indicator."""
        now = time.time()
        with self._lock:
            cache = dict(self._cache)
        out = {}
        for key, v in cache.items():
            out[key] = {
                "is_m5camserver": v.is_m5camserver,
                "sketch": v.sketch,
                "sketch_md5": v.sketch_md5,
                "build_date": v.build_date,
                "build_time": v.build_time,
                "uptime_s": v.uptime_s,
                "free_heap": v.free_heap,
                "camera_ok": v.camera_ok,
                "resolutions": v.resolutions,
                "error": v.error,
                "fetched_age_s": round(now - v.fetched_at, 1),
                "stale": (now - v.fetched_at) >= self.ttl_s,
            }
        return out
