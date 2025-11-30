#!/usr/bin/env python
"""
Probe GW Security NVR ports and protocols
Discover what's running on port 9000 and other potential API ports.

Usage: python probe_nvr_ports.py
"""

import socket
import requests
from requests.auth import HTTPDigestAuth
import struct

NVR_IP = "192.168.0.165"
NVR_USER = "admin"
NVR_PASS = ""

# Common NVR ports to check
PORTS_TO_CHECK = [
    (80, "HTTP Web UI"),
    (443, "HTTPS"),
    (554, "RTSP"),
    (8000, "Hikvision SDK"),
    (8080, "Alt HTTP"),
    (9000, "GW Proprietary?"),
    (37777, "Dahua SDK"),
    (37778, "Dahua Data"),
    (34567, "XMEye/Generic Chinese NVR"),
    (34568, "XMEye Data"),
]


def check_port(ip, port, timeout=3):
    """Check if a port is open"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex((ip, port))
        if result == 0:
            return True
        return False
    except:
        return False
    finally:
        sock.close()


def probe_port(ip, port, timeout=5):
    """Try to identify what protocol is running on a port"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)

    try:
        sock.connect((ip, port))

        # Try to receive banner/greeting
        sock.setblocking(0)
        import select
        ready = select.select([sock], [], [], 2)
        banner = b""
        if ready[0]:
            banner = sock.recv(1024)

        if banner:
            return f"Banner: {banner[:100]}"

        # No banner, try sending some probes

        # Try HTTP
        sock.send(b"GET / HTTP/1.0\r\n\r\n")
        response = b""
        sock.setblocking(1)
        sock.settimeout(3)
        try:
            response = sock.recv(2048)
        except:
            pass

        if b"HTTP" in response:
            return f"HTTP: {response[:200].decode('utf-8', errors='ignore')}"

        # Try Dahua binary protocol (login request)
        # Dahua SDK uses a binary protocol starting with specific magic bytes
        sock.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))

        # Send Dahua-style probe
        dahua_probe = bytes([0xa0, 0x00, 0x00, 0x60])  # Dahua magic
        sock.send(dahua_probe)
        try:
            response = sock.recv(1024)
            if response:
                return f"Binary response: {response[:50].hex()}"
        except:
            pass

        # Try XMEye/Sofia protocol
        sock.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))

        # XMEye login probe
        xmeye_header = struct.pack('<BBHI', 0xff, 0x00, 0x00, 0x00)
        sock.send(xmeye_header)
        try:
            response = sock.recv(1024)
            if response:
                return f"XMEye-style response: {response[:50].hex()}"
        except:
            pass

        return "Open but no identifiable protocol"

    except socket.timeout:
        return "Timeout"
    except ConnectionRefusedError:
        return "Connection refused"
    except Exception as e:
        return f"Error: {e}"
    finally:
        sock.close()


def main():
    print("=" * 70)
    print(f"GW Security NVR Port Probe - {NVR_IP}")
    print("=" * 70)

    print("\n[1] Checking open ports...\n")

    open_ports = []
    for port, description in PORTS_TO_CHECK:
        status = "✅ OPEN" if check_port(NVR_IP, port) else "❌ closed"
        print(f"  Port {port:5} ({description:20}): {status}")
        if "OPEN" in status:
            open_ports.append((port, description))

    print(f"\n[2] Probing {len(open_ports)} open ports...\n")

    for port, description in open_ports:
        print(f"\n  Port {port} ({description}):")
        result = probe_port(NVR_IP, port)
        print(f"    {result}")

    # Special test for port 9000
    if any(p[0] == 9000 for p in open_ports):
        print("\n[3] Deep probe of port 9000...\n")

        # Try various HTTP paths on 9000
        http_paths = [
            "/",
            "/RPC2",
            "/cgi-bin/",
            "/api/",
            "/sdk/",
            "/device/info",
        ]

        for path in http_paths:
            try:
                url = f"http://{NVR_IP}:9000{path}"
                print(f"  GET {url}")
                resp = requests.get(url, timeout=5, auth=HTTPDigestAuth(NVR_USER, NVR_PASS))
                print(f"    Status: {resp.status_code}, Length: {len(resp.content)}")
                if resp.content:
                    print(f"    Content: {resp.text[:200]}")
            except Exception as e:
                print(f"    Error: {e}")

    print("\n" + "=" * 70)
    print("Analysis complete. Key findings to report:")
    print("  - Which ports are open")
    print("  - What protocol each port uses")
    print("  - Port 9000 response details")
    print("=" * 70)


if __name__ == "__main__":
    main()
