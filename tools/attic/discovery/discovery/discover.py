#!/usr/bin/env python3
"""
Network Camera Discovery Tool

Walk onto a new site, run this, learn what's there.
Progressive discovery: no credentials first, then layer them on.

Phase 1 (no creds): broadcast, port scan, HTTP fingerprint
Phase 2 (with creds): ONVIF login, RTSP probe

Results go into a local discovery.db — never touches lodge.db.

Author: modeltcamerascat gen-35
"""

import argparse
import json
import socket
import sqlite3
import subprocess
import sys
import time
import threading
import re
from pathlib import Path
from datetime import datetime

# ============================================================================
# DISCOVERY DB
# ============================================================================

DB_PATH = Path(__file__).parent / "discovery.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    ip TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS probes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    method TEXT NOT NULL,       -- ws_discovery, port_scan, http_fingerprint, onvif_login, rtsp_probe
    timestamp TEXT NOT NULL,
    success INTEGER NOT NULL,   -- 1/0
    detail TEXT,                -- JSON blob with whatever the probe returned
    FOREIGN KEY (ip) REFERENCES devices(ip)
);

CREATE TABLE IF NOT EXISTS device_facts (
    ip TEXT NOT NULL,
    key TEXT NOT NULL,          -- mac, model, manufacturer, serial, hostname, onvif, http_server, port_554, port_80, etc.
    value TEXT,
    source TEXT NOT NULL,       -- which method discovered this
    timestamp TEXT NOT NULL,
    PRIMARY KEY (ip, key, source)
);
"""


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def record_device(conn, ip):
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO devices (ip, first_seen, last_seen) VALUES (?, ?, ?) "
        "ON CONFLICT(ip) DO UPDATE SET last_seen = ?",
        (ip, now, now, now)
    )


def record_probe(conn, ip, method, success, detail=None):
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO probes (ip, method, timestamp, success, detail) VALUES (?, ?, ?, ?, ?)",
        (ip, method, now, 1 if success else 0, json.dumps(detail) if detail else None)
    )


def record_fact(conn, ip, key, value, source):
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO device_facts (ip, key, value, source, timestamp) VALUES (?, ?, ?, ?, ?)",
        (ip, key, str(value) if value is not None else None, source, now)
    )


# ============================================================================
# PHASE 1: NO CREDENTIALS
# ============================================================================

def scan_ws_discovery(timeout=10):
    """Broadcast WS-Discovery. Returns list of IPs that responded."""
    try:
        from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery
    except ImportError:
        print("  SKIP: wsdiscovery not installed (pip install wsdiscovery)")
        return []

    print(f"  Broadcasting WS-Discovery (timeout {timeout}s)...")
    wsd = WSDiscovery()
    wsd.start()
    time.sleep(2)
    services = wsd.searchServices(timeout=timeout - 2)
    wsd.stop()

    ips = set()
    for service in services:
        for addr in service.getXAddrs():
            if 'onvif' in addr.lower():
                match = re.search(r'http://([0-9.]+)', addr)
                if match:
                    ips.add(match.group(1))

    return sorted(ips)


def scan_port(ip, port, timeout=2):
    """Test if a single port is open. Returns True/False."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def scan_ports_subnet(subnet="192.168.0", ports=(80, 554), timeout=1.5):
    """Scan a /24 subnet for devices with open ports. Returns {ip: [open_ports]}."""
    print(f"  Scanning {subnet}.1-254 for ports {ports} (timeout {timeout}s each)...")
    found = {}
    lock = threading.Lock()

    def check_ip(ip):
        open_ports = []
        for port in ports:
            if scan_port(ip, port, timeout):
                open_ports.append(port)
        if open_ports:
            with lock:
                found[ip] = open_ports

    threads = []
    for i in range(1, 255):
        ip = f"{subnet}.{i}"
        t = threading.Thread(target=check_ip, args=(ip,))
        t.start()
        threads.append(t)
        # Don't flood — batch in groups of 50
        if len(threads) >= 50:
            for t in threads:
                t.join()
            threads = []

    for t in threads:
        t.join()

    return found


def fingerprint_http(ip, port=80, timeout=3):
    """GET / and read the Server header + page title. No credentials."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        sock.sendall(b"GET / HTTP/1.0\r\nHost: " + ip.encode() + b"\r\n\r\n")
        response = b""
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if len(response) > 16000:
                    break
            except socket.timeout:
                break
        sock.close()

        text = response.decode("utf-8", errors="replace")
        headers = text.split("\r\n\r\n")[0] if "\r\n\r\n" in text else text[:500]

        server = None
        for line in headers.split("\r\n"):
            if line.lower().startswith("server:"):
                server = line.split(":", 1)[1].strip()
                break

        title = None
        m = re.search(r'<title>(.*?)</title>', text, re.IGNORECASE | re.DOTALL)
        if m:
            title = m.group(1).strip()[:100]

        return {"server": server, "title": title, "status_line": headers.split("\r\n")[0][:100]}

    except Exception as e:
        return {"error": str(e)[:80]}


# ============================================================================
# PHASE 2: WITH CREDENTIALS
# ============================================================================

def probe_onvif(ip, credentials, port=80, timeout=10):
    """Try ONVIF login with given credentials. Returns device info or error."""
    try:
        from onvif import ONVIFCamera
    except ImportError:
        return {"error": "onvif-zeep not installed"}

    for username, password in credentials:
        try:
            cam = ONVIFCamera(ip, port, username, password, timeout=timeout)
            devicemgmt = cam.create_devicemgmt_service()
            info = devicemgmt.GetDeviceInformation()

            result = {
                "manufacturer": getattr(info, "Manufacturer", None),
                "model": getattr(info, "Model", None),
                "serial": getattr(info, "SerialNumber", None),
                "firmware": getattr(info, "FirmwareVersion", None),
                "credentials": f"{username}:{'***' if password else '(empty)'}",
            }

            # MAC
            try:
                net = devicemgmt.GetNetworkInterfaces()
                if net and hasattr(net[0], "Info") and hasattr(net[0].Info, "HwAddress"):
                    result["mac"] = net[0].Info.HwAddress
            except Exception:
                pass

            # Hostname
            try:
                h = devicemgmt.GetHostname()
                result["hostname"] = getattr(h, "Name", None)
            except Exception:
                pass

            return result

        except Exception as e:
            err = str(e)
            if "authorized" in err.lower() or "auth" in err.lower():
                continue  # try next credential
            return {"error": err[:100]}

    return {"error": "auth_failed_all_credentials"}


def probe_rtsp(ip, port=554, path="", username="", password="", timeout=10):
    """Test RTSP connection via ffmpeg. Returns resolution or error."""
    url = f"rtsp://{username}:{password}@{ip}:{port}/{path}" if username else f"rtsp://{ip}:{port}/{path}"
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-rtsp_transport", "tcp",
             "-i", url, "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, timeout=timeout
        )
        stderr = proc.stderr.decode("utf-8", errors="replace")
        m = re.search(r'(\d{3,5})x(\d{3,5})', stderr)
        if proc.returncode == 0 and m:
            return {"success": True, "resolution": f"{m.group(1)}x{m.group(2)}"}
        else:
            # Look for clues in stderr
            if "401" in stderr:
                return {"success": False, "error": "401_unauthorized"}
            elif "Connection refused" in stderr:
                return {"success": False, "error": "connection_refused"}
            else:
                return {"success": False, "error": stderr[-200:].strip()}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timeout"}
    except FileNotFoundError:
        return {"success": False, "error": "ffmpeg_not_found"}


# ============================================================================
# COMMANDS
# ============================================================================

def cmd_phase1(args):
    """Phase 1: No credentials. Broadcast + port scan + HTTP fingerprint."""
    conn = init_db()
    subnet = args.subnet
    now = datetime.now().isoformat()

    print(f"\n=== PHASE 1: No-credential discovery ({subnet}.0/24) ===\n")

    # Step 1: WS-Discovery broadcast
    print("[1/3] WS-Discovery broadcast")
    ws_ips = scan_ws_discovery(timeout=args.timeout)
    print(f"  Found {len(ws_ips)} ONVIF responders: {ws_ips}\n")

    for ip in ws_ips:
        record_device(conn, ip)
        record_probe(conn, ip, "ws_discovery", True)
        record_fact(conn, ip, "onvif_broadcast", "responded", "ws_discovery")

    # Step 2: Port scan
    print("[2/3] Port scan (80, 554)")
    port_results = scan_ports_subnet(subnet, ports=(80, 554), timeout=1.5)
    print(f"  Found {len(port_results)} devices with open ports\n")

    for ip, ports in sorted(port_results.items()):
        record_device(conn, ip)
        record_probe(conn, ip, "port_scan", True, {"open_ports": ports})
        for p in ports:
            record_fact(conn, ip, f"port_{p}", "open", "port_scan")

    # Step 3: HTTP fingerprint (anything with port 80 open)
    http_ips = [ip for ip, ports in port_results.items() if 80 in ports]
    print(f"[3/3] HTTP fingerprint ({len(http_ips)} devices with port 80)")
    for ip in sorted(http_ips):
        fp = fingerprint_http(ip)
        has_info = fp.get("server") or fp.get("title")
        record_probe(conn, ip, "http_fingerprint", bool(has_info), fp)
        if fp.get("server"):
            record_fact(conn, ip, "http_server", fp["server"], "http_fingerprint")
        if fp.get("title"):
            record_fact(conn, ip, "http_title", fp["title"], "http_fingerprint")
        label = fp.get("server") or fp.get("title") or fp.get("error", "?")
        print(f"  {ip:16s} {label}")

    conn.commit()
    conn.close()

    print(f"\n=== Phase 1 complete. Results in {DB_PATH} ===\n")


def cmd_phase2(args):
    """Phase 2: Try credentials against discovered devices."""
    conn = init_db()

    # Parse credentials
    creds = []
    for c in (args.credentials or []):
        if ":" in c:
            u, p = c.split(":", 1)
            creds.append((u, p))
        else:
            creds.append((c, ""))
    if not creds:
        print("No credentials provided. Use --credentials user:pass [user2:pass2 ...]")
        return

    # Get all devices that responded to ws_discovery (ONVIF candidates)
    cursor = conn.execute(
        "SELECT DISTINCT ip FROM device_facts WHERE key = 'onvif_broadcast'"
    )
    onvif_ips = [row[0] for row in cursor.fetchall()]

    if not onvif_ips:
        print("No ONVIF devices found. Run phase1 first.")
        return

    print(f"\n=== PHASE 2: ONVIF login with {len(creds)} credential set(s) against {len(onvif_ips)} devices ===\n")

    for ip in sorted(onvif_ips):
        print(f"  {ip:16s} ", end="", flush=True)
        result = probe_onvif(ip, creds)

        if "error" in result:
            print(f"FAIL: {result['error']}")
            record_probe(conn, ip, "onvif_login", False, result)
        else:
            model = result.get("model", "?")
            mac = result.get("mac", "?")
            print(f"OK  model={model}  mac={mac}")
            record_probe(conn, ip, "onvif_login", True, result)
            for key in ("mac", "model", "manufacturer", "serial", "hostname"):
                if result.get(key):
                    record_fact(conn, ip, key, result[key], "onvif_login")

    conn.commit()
    conn.close()

    print(f"\n=== Phase 2 complete. Results in {DB_PATH} ===\n")


def cmd_report(args):
    """Show everything we've discovered so far."""
    if not DB_PATH.exists():
        print("No discovery.db found. Run phase1 first.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    devices = conn.execute("SELECT * FROM devices ORDER BY ip").fetchall()

    print(f"\n=== Discovery Report: {len(devices)} devices ===\n")

    for dev in devices:
        ip = dev["ip"]
        facts = conn.execute(
            "SELECT key, value, source FROM device_facts WHERE ip = ? ORDER BY key",
            (ip,)
        ).fetchall()

        fact_dict = {}
        for f in facts:
            fact_dict[f["key"]] = f["value"]

        ports = []
        if fact_dict.get("port_80") == "open":
            ports.append("80")
        if fact_dict.get("port_554") == "open":
            ports.append("554")

        onvif = "yes" if fact_dict.get("onvif_broadcast") else "no"
        model = fact_dict.get("model", "")
        mac = fact_dict.get("mac", "")
        server = fact_dict.get("http_server", "")
        title = fact_dict.get("http_title", "")

        print(f"  {ip:16s}  ports=[{','.join(ports):7s}]  onvif={onvif:3s}  model={model:20s}  mac={mac}")
        if server:
            print(f"  {'':16s}  http_server={server}")
        if title:
            print(f"  {'':16s}  http_title={title}")

    conn.close()
    print()


def cmd_reset(args):
    """Delete discovery.db and start fresh."""
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Deleted {DB_PATH}")
    else:
        print("No discovery.db to delete.")


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Network camera discovery — progressive, credential-aware",
    )
    sub = parser.add_subparsers(dest="command")

    p1 = sub.add_parser("phase1", help="No-credential discovery: broadcast + port scan + HTTP fingerprint")
    p1.add_argument("--subnet", default="192.168.0", help="Subnet to scan (default: 192.168.0)")
    p1.add_argument("--timeout", type=int, default=10, help="WS-Discovery timeout (default: 10)")

    p2 = sub.add_parser("phase2", help="Try credentials against discovered ONVIF devices")
    p2.add_argument("--credentials", nargs="+", help="Credentials as user:pass pairs")

    sub.add_parser("report", help="Show everything discovered so far")
    sub.add_parser("reset", help="Delete discovery.db and start fresh")

    args = parser.parse_args()

    if args.command == "phase1":
        cmd_phase1(args)
    elif args.command == "phase2":
        cmd_phase2(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "reset":
        cmd_reset(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
