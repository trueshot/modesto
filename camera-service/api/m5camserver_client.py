"""
M5CamServer protocol client — TTL-cached /version probing + streaming OTA.

Why /version probing needs care: stock M5PoECam firmware returns a JPEG
on ANY URL path (except /stream), so a GET /version on a stock cam
both fails to identify it (no JSON) and burns one of the W5500 socket
pool cycles that wedge the device after ~40 of them. We cache aggressively
to avoid hammering — default TTL 300s. Cache key is the IP string the
caller passes (so 127.0.0.1:8090 and 127.0.0.1 are distinct entries —
useful for the emulator).

OTA is exposed as a generator yielding event dicts (preflight → upload →
verify → complete). The HTTP endpoint wraps it in an NDJSON streaming
response. Same upload + verify pattern as the M5CamServer ota.py CLI:
skip-if-match preflight, raw-socket upload (because http.client swallows
progress), 3s post-upload read window (the cam usually ESP.restart()s
before its FIN crosses the wire), and md5 verify via /version polling
as the *primary* success signal (not the 200 from /update).

Companion to http_mediator.py. Lives separately so M5CamServer-specific
operations accumulate here without bloating the streaming proxy.

Author: modeltcamerascat gen-43
"""

import hashlib
import json
import http.client
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Iterator, Optional


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
    # True if the probe got past TCP open and read an HTTP response (even a
    # non-M5CamServer one). False only on transport-layer failure
    # (ConnectionRefused, timeout, network unreachable).
    transport_ok: bool = False


class M5CamServerProbe:
    """TTL-cached /version probes."""

    # Fields M5CamServer.cpp::serveVersion always emits — used to verify
    # the response is actually from an M5CamServer and not a coincidentally-
    # JSON-returning device.
    _REQUIRED_FIELDS = frozenset({"sketch", "sketch_md5", "camera_ok"})

    def __init__(self, ttl_s: float = 86400.0, timeout_s: float = 3.0):
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

        On probe failure, prior successful info is preserved (timestamp bumped)
        rather than overwritten with the failure — transient connection issues
        (e.g. mediator holding the W5500 single-client slot) shouldn't erase
        known-good firmware info.
        """
        cache_key = f"{ip}:{port}"
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None and not force and self._is_fresh(cached):
                return cached

        info = self._probe(ip, port)
        with self._lock:
            prior = self._cache.get(cache_key)
            # Preserve a prior probe that reached the application layer
            # ("stock firmware detected" counts as informative) when the
            # current probe failed at transport (ConnectionRefused / timeout).
            if not info.transport_ok and prior is not None and prior.transport_ok:
                prior.fetched_at = time.time()
                return prior
            self._cache[cache_key] = info
        return info

    def get_cached(self, ip: str, port: int = 80) -> Optional[VersionInfo]:
        """Return cached VersionInfo without probing. None if no entry."""
        with self._lock:
            return self._cache.get(f"{ip}:{port}")

    def _probe(self, ip: str, port: int) -> VersionInfo:
        info = VersionInfo(is_m5camserver=False, fetched_at=time.time())
        conn = None
        try:
            conn = http.client.HTTPConnection(ip, port=port, timeout=self.timeout_s)
            conn.request("GET", "/version")
            resp = conn.getresponse()
            info.transport_ok = True  # got past TCP open + first HTTP byte
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

    # ── OTA ────────────────────────────────────────────────────────

    # Safety caps mirrored from the M5CamServer ota.py CLI. An ESP32 app
    # partition is typically 1-2 MB; anything bigger is almost certainly
    # the wrong file (full-flash merged image, debug binary, etc.).
    OTA_MAX_BYTES = 2 * 1024 * 1024
    # 3s read window after the upload completes — the cam usually
    # ESP.restart()s before its FIN crosses the wire, leaving us with
    # no clean response. That's expected; we fall through to /version
    # polling for the actual success signal.
    OTA_POST_UPLOAD_READ_TIMEOUT_S = 3.0
    # Per-chunk HTTP send timeout; matches ota.py's connect timeout.
    OTA_UPLOAD_TIMEOUT_S = 30.0
    # How long to poll /version waiting for the cam to come back with the
    # new sketch_md5. Real cams take 1-3s to reboot; 30s is a safe ceiling.
    OTA_VERIFY_DEADLINE_S = 30.0

    def do_ota(
        self,
        ip: str,
        port: int,
        firmware_bytes: bytes,
        filename: str = "",
    ) -> Iterator[dict]:
        """
        Run a full OTA against an M5CamServer cam, yielding event dicts.

        Phases (each event has a "type" field): "phase" (transition),
        "progress" (informational), "complete" (final, ok=true|false).

        Validation: refuses .merged.bin filenames (full-flash images that
        would brick the cam through OTA) and bytes > OTA_MAX_BYTES.

        Pre-flight: probes /version with force=True. If the cam already
        reports the expected sketch_md5, short-circuits with ok=true and
        no upload (matches ota.py's skip-if-match optimization).

        Upload: raw socket POST /update with chunked progress events.

        Verify: polls /version (bypassing cache) until sketch_md5 matches
        the uploaded bytes' md5, mismatches confirmed, or deadline elapses.

        Cache invalidation: drops the /version cache entry on completion
        so subsequent version() calls see the post-OTA state.
        """
        t_start = time.monotonic()
        size = len(firmware_bytes)

        def elapsed() -> float:
            return round(time.monotonic() - t_start, 2)

        # ── Validation
        if filename and filename.endswith(".merged.bin"):
            yield {"type": "complete", "ok": False,
                   "reason": (f"refused: '{filename}' looks like a full-flash image "
                              "(bootloader+partitions+app); OTA needs only the app .bin"),
                   "elapsed_s": elapsed()}
            return
        if size == 0:
            yield {"type": "complete", "ok": False,
                   "reason": "refused: empty firmware payload",
                   "elapsed_s": elapsed()}
            return
        if size > self.OTA_MAX_BYTES:
            yield {"type": "complete", "ok": False,
                   "reason": (f"refused: {size:,} bytes exceeds {self.OTA_MAX_BYTES:,} "
                              "(typical ESP32 app partition cap)"),
                   "elapsed_s": elapsed()}
            return

        expected_md5 = hashlib.md5(firmware_bytes).hexdigest()

        # ── Pre-flight: skip-if-match
        yield {"type": "phase", "name": "preflight"}
        info = self._probe(ip, port)  # bypass cache
        with self._lock:
            self._cache[f"{ip}:{port}"] = info  # warm cache with fresh probe
        if info.is_m5camserver:
            cur = (info.sketch_md5 or "").lower()
            yield {"type": "progress", "phase": "preflight",
                   "msg": f"current /version: md5={cur[:8]}... uptime={info.uptime_s}s sketch={info.sketch}"}
            if cur == expected_md5.lower():
                yield {"type": "complete", "ok": True,
                       "reason": "already running expected md5 — no upload needed",
                       "expected_md5": expected_md5,
                       "running_md5": info.sketch_md5,
                       "uptime_s": info.uptime_s,
                       "elapsed_s": elapsed()}
                return
        elif info.error:
            yield {"type": "progress", "phase": "preflight",
                   "msg": f"could not read current /version ({info.error}); proceeding anyway"}

        yield {"type": "progress", "phase": "preflight",
               "msg": f"new firmware: md5={expected_md5[:8]}... size={size:,} bytes"
                      + (f" filename={filename}" if filename else "")}

        # ── Upload
        yield {"type": "phase", "name": "upload", "size_bytes": size}
        upload_failed_reason = None
        post_upload_response = None
        try:
            for ev in self._stream_upload(ip, port, firmware_bytes):
                if ev.get("type") == "_upload_response":
                    post_upload_response = ev.get("status_line")
                    if post_upload_response:
                        yield {"type": "progress", "phase": "upload",
                               "msg": f"cam response: {post_upload_response}"}
                    else:
                        yield {"type": "progress", "phase": "upload",
                               "msg": "no response (cam likely already restarted — verify will confirm)"}
                else:
                    yield ev
        except OSError as e:
            upload_failed_reason = f"{type(e).__name__}: {e}"
            yield {"type": "progress", "phase": "upload",
                   "msg": f"socket error: {upload_failed_reason}"}

        if upload_failed_reason:
            yield {"type": "complete", "ok": False,
                   "reason": f"upload failed: {upload_failed_reason}",
                   "elapsed_s": elapsed()}
            self.invalidate(ip, port)
            return

        # ── Verify
        yield {"type": "phase", "name": "verify"}
        verify_start = time.monotonic()
        attempt = 0
        verify_ok = False
        verify_md5 = None
        verify_uptime = None
        verify_reason = "deadline reached without match"
        while time.monotonic() - verify_start < self.OTA_VERIFY_DEADLINE_S:
            attempt += 1
            v = self._probe(ip, port)
            if v.is_m5camserver:
                cur = (v.sketch_md5 or "").lower()
                if cur == expected_md5.lower():
                    yield {"type": "progress", "phase": "verify", "attempt": attempt,
                           "msg": f"running md5={cur[:8]}... uptime={v.uptime_s}s — match"}
                    verify_ok = True
                    verify_md5 = v.sketch_md5
                    verify_uptime = v.uptime_s
                    verify_reason = "md5 verified via /version"
                    break
                else:
                    yield {"type": "progress", "phase": "verify", "attempt": attempt,
                           "msg": (f"running md5={cur[:8]}... uptime={v.uptime_s}s — "
                                   f"mismatch (expected {expected_md5[:8]}...)")}
                    verify_md5 = v.sketch_md5
                    verify_uptime = v.uptime_s
                    verify_reason = "md5 mismatch — flash may not have stuck"
                    break
            else:
                yield {"type": "progress", "phase": "verify", "attempt": attempt,
                       "msg": f"cam unreachable ({v.error or 'no response'}) — expected during reboot"}
            time.sleep(1.0)

        # Cache invalidation: post-OTA the cam's md5 (and possibly other
        # fields) have changed. The verify probe already updated _probe
        # state, but explicit invalidate covers the case where verify
        # never got a successful probe.
        self.invalidate(ip, port)

        yield {"type": "complete",
               "ok": verify_ok,
               "reason": verify_reason,
               "expected_md5": expected_md5,
               "running_md5": verify_md5,
               "uptime_s": verify_uptime,
               "verify_attempts": attempt,
               "post_upload_response": post_upload_response,
               "elapsed_s": elapsed()}

    def _stream_upload(self, ip: str, port: int, firmware_bytes: bytes) -> Iterator[dict]:
        """Raw-socket POST /update with periodic progress events. Yields
        progress dicts during upload and a final {"type": "_upload_response",
        "status_line": str|None} once the cam's response (if any) is read.
        Raises OSError on socket failure."""
        size = len(firmware_bytes)
        sock = socket.create_connection((ip, port), timeout=self.OTA_UPLOAD_TIMEOUT_S)
        try:
            head = (
                f"POST /update HTTP/1.1\r\n"
                f"Host: {ip}:{port}\r\n"
                f"Content-Length: {size}\r\n"
                f"Content-Type: application/octet-stream\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode("ascii")
            sock.sendall(head)

            start = time.monotonic()
            sent = 0
            last_emit = 0.0
            CHUNK = 4096
            view = memoryview(firmware_bytes)
            while sent < size:
                chunk = view[sent:sent + CHUNK]
                sock.sendall(chunk)
                sent += len(chunk)
                now = time.monotonic()
                if now - last_emit >= 0.5 or sent == size:
                    elapsed = now - start
                    kbps = (sent / elapsed / 1024) if elapsed > 0 else 0.0
                    yield {"type": "progress", "phase": "upload",
                           "sent": sent, "total": size,
                           "pct": round(100.0 * sent / size, 1),
                           "kbps": round(kbps, 1)}
                    last_emit = now

            # Read the cam's response, if any. Short timeout because the
            # cam typically ESP.restart()s before its FIN crosses the wire.
            sock.settimeout(self.OTA_POST_UPLOAD_READ_TIMEOUT_S)
            buf = b""
            try:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > 4096:
                        break
            except socket.timeout:
                pass

            status_line = None
            if buf:
                first = buf.split(b"\r\n", 1)[0].decode("latin1", errors="replace").strip()
                if first:
                    status_line = first
            yield {"type": "_upload_response", "status_line": status_line}

        finally:
            try:
                sock.close()
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
