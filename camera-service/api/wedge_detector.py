"""
Wedge detector — diagnostic probe for ESP32+W5500 PoE-CAM health.

Distinguishes the gen-42 wedge fingerprints we documented after a day
of testing the M5PoECAM in the warehouse:

  alive        TCP/80 SYN accepts AND HTTP responds within timeout.
               Camera is healthy.
  wedged_app   TCP/80 SYN accepts BUT HTTP doesn't respond within timeout.
               ESP sketch is hung; W5500 chip still answering SYN/ACK in
               hardware. **Permanent until power cycle.**
  wedged_tcp   ICMP works BUT TCP/80 refuses or times out at SYN.
               W5500 socket pool exhausted (the gen-42 stock-firmware
               failure after ~40 connection cycles). **Permanent until
               power cycle.**
  offline      No ICMP, no TCP. Powered off, unplugged, or wrong IP.
  unreachable  ICMP not run (do_ping=False) and TCP closed; can't tell
               whether the cam is offline or just refusing connections.

Firmware-agnostic — works on stock M5PoECam, M5CamServer, anything that
talks HTTP on the W5500. The HTTP probe doesn't read the body, only
whether a response line arrives.

Author: modeltcamerascat gen-43
"""

import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional


VERDICT_ALIVE       = "alive"
VERDICT_WEDGED_APP  = "wedged_app"
VERDICT_WEDGED_TCP  = "wedged_tcp"
VERDICT_OFFLINE     = "offline"
VERDICT_UNREACHABLE = "unreachable"


@dataclass
class WedgeReport:
    ip: str
    port: int
    verdict: str
    ping_ok: Optional[bool]
    tcp_open: bool
    http_ok: bool
    tcp_ms: Optional[float]
    http_ms: Optional[float]
    http_status: Optional[int]
    error: Optional[str] = None
    recommendation: str = ""


def _ping(ip: str, timeout_ms: int = 800) -> bool:
    """One ICMP echo. Cross-platform. Subprocess overhead is ~30ms which is
    fine — we only call this once per probe."""
    if sys.platform == "win32":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    else:
        # BSD/GNU ping: -W is whole seconds (we round up to at least 1)
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), ip]
    try:
        r = subprocess.run(cmd, capture_output=True,
                           timeout=(timeout_ms / 1000.0) + 1.0)
    except Exception:
        return False
    if r.returncode != 0:
        return False
    # Some platforms return 0 on host-unreachable; sanity-check the output
    # contains "Reply from" / "bytes from"
    out = r.stdout.decode("latin1", errors="replace").lower()
    return "reply from" in out or "bytes from" in out


def _tcp_connect(ip: str, port: int, timeout: float) -> tuple:
    """Returns (success, elapsed_ms, error_str_or_None)."""
    t0 = time.monotonic()
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        return (True, (time.monotonic() - t0) * 1000.0, None)
    except Exception as e:
        return (False, (time.monotonic() - t0) * 1000.0,
                f"{type(e).__name__}: {e}")


def _http_first_response(ip: str, port: int, timeout: float) -> tuple:
    """
    Open TCP, send GET /, wait for the first HTTP response line. Doesn't
    read the body — we only care whether the application layer responds
    at all (the wedge fingerprint is "TCP works, HTTP never responds").
    Returns (responded, elapsed_ms, status_code_or_None, error_or_None).
    """
    t0 = time.monotonic()
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.settimeout(timeout)
        s.sendall(f"GET / HTTP/1.1\r\nHost: {ip}\r\nConnection: close\r\n\r\n".encode())
        deadline = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < deadline and b"\r\n" not in buf:
            try:
                chunk = s.recv(256)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        try:
            s.close()
        except Exception:
            pass
        if b"\r\n" not in buf:
            return (False, elapsed_ms, None, "no response within timeout")
        first = buf.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
        parts = first.split(" ", 2)
        status = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
        return (True, elapsed_ms, status, None)
    except Exception as e:
        return (False, (time.monotonic() - t0) * 1000.0, None,
                f"{type(e).__name__}: {e}")


_RECOMMENDATIONS = {
    VERDICT_ALIVE:       "no action — cam is responding normally",
    VERDICT_WEDGED_APP:  "power cycle — sketch is hung; W5500 still answering SYN in hardware",
    VERDICT_WEDGED_TCP:  "power cycle — W5500 socket pool exhausted; ICMP works but no TCP",
    VERDICT_OFFLINE:     "check power/cable; cam is not on the network at this IP",
    VERDICT_UNREACHABLE: "enable ping (do_ping=True) to disambiguate offline vs tcp-closed",
}


def detect_wedge(
    ip: str,
    port: int = 80,
    *,
    tcp_timeout: float = 2.0,
    http_timeout: float = 6.0,
    do_ping: bool = True,
) -> WedgeReport:
    """
    Probe an HTTP cam for W5500 wedge fingerprints.

    Args:
        ip:           target IP
        port:         HTTP port (default 80)
        tcp_timeout:  seconds to wait for SYN-ACK
        http_timeout: seconds to wait for HTTP response after TCP open
        do_ping:      include ICMP probe (helps distinguish offline vs
                      tcp-closed); default True

    Returns:
        WedgeReport with verdict + per-layer breakdown + recommendation.
    """
    ping_ok = _ping(ip) if do_ping else None
    tcp_open, tcp_ms, tcp_err = _tcp_connect(ip, port, tcp_timeout)

    if tcp_open:
        http_ok, http_ms, http_status, http_err = _http_first_response(
            ip, port, http_timeout
        )
    else:
        http_ok, http_ms, http_status, http_err = False, None, None, None

    if tcp_open and http_ok:
        verdict, error = VERDICT_ALIVE, None
    elif tcp_open and not http_ok:
        verdict, error = VERDICT_WEDGED_APP, http_err
    elif not tcp_open and ping_ok is True:
        verdict, error = VERDICT_WEDGED_TCP, tcp_err
    elif not tcp_open and ping_ok is False:
        verdict, error = VERDICT_OFFLINE, tcp_err
    else:
        # ping not run + tcp closed
        verdict, error = VERDICT_UNREACHABLE, tcp_err

    return WedgeReport(
        ip=ip,
        port=port,
        verdict=verdict,
        ping_ok=ping_ok,
        tcp_open=tcp_open,
        http_ok=http_ok,
        tcp_ms=round(tcp_ms, 1) if tcp_ms is not None else None,
        http_ms=round(http_ms, 1) if http_ms is not None else None,
        http_status=http_status,
        error=error,
        recommendation=_RECOMMENDATIONS[verdict],
    )
