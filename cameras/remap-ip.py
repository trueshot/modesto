#!/usr/bin/env python3
"""
remap-ip.py — Rediscover cameras after IP changes

Scans subnet, matches MACs against lodge.db, reports/updates IP changes.

Usage:
    python remap-ip.py                    # Scan and report (dry-run)
    python remap-ip.py --apply            # Actually update lodge.db
    python remap-ip.py --subnet 10.0.0    # Different subnet (first 3 octets)

Author: modeltcamerascat gen-40
"""

import argparse
import subprocess
import sqlite3
import socket
import re
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PATH = Path(__file__).parent.parent / "warehouses" / "lodge" / "lodge.db"


def ping(ip: str, timeout: float = 0.5) -> bool:
    """Ping an IP, return True if alive."""
    try:
        # Windows ping: -n count, -w timeout in ms
        result = subprocess.run(
            ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip],
            capture_output=True,
            timeout=timeout + 1,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_arp_table() -> dict:
    """Parse ARP table, return {ip: mac} dict. MAC normalized to lowercase with colons."""
    try:
        result = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.splitlines()
    except Exception:
        return {}

    ip_mac = {}
    # Windows ARP format: "  192.168.0.1          00-1a-2b-3c-4d-5e     dynamic"
    pattern = re.compile(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]+)\s+dynamic")
    for line in lines:
        m = pattern.search(line)
        if m:
            ip = m.group(1)
            mac = m.group(2).lower().replace("-", ":")
            ip_mac[ip] = mac
    return ip_mac


def scan_subnet(subnet: str, workers: int = 50, db_ips: list = None) -> dict:
    """
    Ping sweep a subnet, return {ip: mac} for responding hosts.

    Args:
        subnet: First 3 octets, e.g., "192.168.0"
        workers: Thread pool size
        db_ips: Known IPs from DB — retried individually if missed by flood sweep
    """
    db_ips = db_ips or []
    print(f"Scanning {subnet}.1-254 ...")
    ips = [f"{subnet}.{i}" for i in range(1, 255)]
    alive = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(ping, ip): ip for ip in ips}
        for f in as_completed(futures):
            ip = futures[f]
            if f.result():
                alive.append(ip)

    print(f"Found {len(alive)} hosts alive")

    # Small delay for ARP table to populate
    time.sleep(0.5)

    # Get MACs from ARP table
    arp = get_arp_table()
    result = {}
    for ip in alive:
        mac = arp.get(ip)
        if mac:
            result[ip] = mac

    print(f"Resolved {len(result)} MAC addresses")

    # Retry missed DB cameras one-at-a-time (ESP32/PoE-CAM can't handle flood pings)
    if db_ips:
        missed = [ip for ip in db_ips if ip not in result and ip in ips]
        if missed:
            print(f"Retrying {len(missed)} known DB IPs individually...")
            for ip in missed:
                if ping(ip, timeout=1.0):
                    time.sleep(0.3)
                    arp2 = get_arp_table()
                    mac = arp2.get(ip)
                    if mac:
                        result[ip] = mac
                        print(f"  Found {ip} on retry")

    return result


def load_cameras(conn: sqlite3.Connection) -> dict:
    """Load cameras from DB, return {mac: {ip, model, ...}}."""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT mac, ip, model, notes FROM cameras WHERE mac IS NOT NULL")
    cameras = {}
    for row in cur.fetchall():
        cameras[row["mac"].lower()] = {
            "ip": row["ip"],
            "model": row["model"],
            "notes": row["notes"],
        }
    return cameras


def load_nvrs(conn: sqlite3.Connection) -> dict:
    """Load NVRs from DB, return {mac: {ip, brand, ...}}."""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, mac, ip, brand, model FROM nvrs WHERE mac IS NOT NULL")
    nvrs = {}
    for row in cur.fetchall():
        if row["mac"]:
            nvrs[row["mac"].lower()] = {
                "id": row["id"],
                "ip": row["ip"],
                "brand": row["brand"],
                "model": row["model"],
            }
    return nvrs


def identify_unknown(ip: str) -> str:
    """Try to identify an unknown device by probing ports."""
    # Check common camera/NVR ports
    results = []

    for port in [80, 554, 8080]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            if s.connect_ex((ip, port)) == 0:
                results.append(str(port))
            s.close()
        except Exception:
            pass

    if results:
        return f"ports:{','.join(results)}"
    return "unknown"


def main():
    parser = argparse.ArgumentParser(description="Remap camera IPs after DHCP changes")
    parser.add_argument("--subnet", default="192.168.0", help="First 3 octets (default: 192.168.0)")
    parser.add_argument("--apply", action="store_true", help="Actually update lodge.db (default: dry-run)")
    parser.add_argument("--show-unknown", action="store_true", help="Show devices not in DB")
    parser.add_argument("--workers", type=int, default=50, help="Parallel ping workers (default: 50)")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    # Load DB first so we can pass known IPs to scanner
    conn = sqlite3.connect(str(DB_PATH))
    cameras = load_cameras(conn)
    nvrs = load_nvrs(conn)

    # Collect known IPs for retry on fragile devices
    known_ips = [c["ip"] for c in cameras.values() if c["ip"]]
    known_ips += [n["ip"] for n in nvrs.values() if n["ip"]]

    # Scan network
    ip_mac = scan_subnet(args.subnet, args.workers, db_ips=known_ips)

    print()
    print("=" * 60)

    # Check for changes
    camera_changes = []
    nvr_changes = []
    unknown = []

    for ip, mac in sorted(ip_mac.items(), key=lambda x: [int(p) for p in x[0].split(".")]):
        if mac in cameras:
            cam = cameras[mac]
            if cam["ip"] != ip:
                camera_changes.append((mac, cam["ip"], ip, cam["model"]))
            # else: no change
        elif mac in nvrs:
            nvr = nvrs[mac]
            if nvr["ip"] != ip:
                nvr_changes.append((mac, nvr["ip"], ip, nvr["id"], nvr["brand"]))
        else:
            unknown.append((ip, mac))

    # Report camera changes
    if camera_changes:
        print(f"\nCAMERA IP CHANGES ({len(camera_changes)}):")
        print("-" * 60)
        for mac, old_ip, new_ip, model in camera_changes:
            print(f"  {mac}  {old_ip:15} -> {new_ip:15}  [{model}]")
    else:
        print("\nNo camera IP changes detected.")

    # Report NVR changes
    if nvr_changes:
        print(f"\nNVR IP CHANGES ({len(nvr_changes)}):")
        print("-" * 60)
        for mac, old_ip, new_ip, nvr_id, brand in nvr_changes:
            print(f"  {mac}  {old_ip:15} -> {new_ip:15}  [{nvr_id}: {brand}]")

    # Report unknown devices
    if args.show_unknown and unknown:
        print(f"\nUNKNOWN DEVICES ({len(unknown)}):")
        print("-" * 60)
        for ip, mac in unknown:
            info = identify_unknown(ip)
            print(f"  {ip:15}  {mac}  {info}")

    # Apply changes
    if args.apply and (camera_changes or nvr_changes):
        print()
        cur = conn.cursor()

        for mac, old_ip, new_ip, model in camera_changes:
            cur.execute("UPDATE cameras SET ip = ? WHERE mac = ?", (new_ip, mac))
            print(f"Updated camera {mac}: {old_ip} -> {new_ip}")

        for mac, old_ip, new_ip, nvr_id, brand in nvr_changes:
            cur.execute("UPDATE nvrs SET ip = ? WHERE mac = ?", (new_ip, mac))
            print(f"Updated NVR {nvr_id}: {old_ip} -> {new_ip}")

        conn.commit()
        print(f"\nApplied {len(camera_changes) + len(nvr_changes)} changes to lodge.db")
        print("Run snapshot-database.js to capture the changes.")
    elif camera_changes or nvr_changes:
        print()
        print("Dry-run mode. Use --apply to update lodge.db")

    conn.close()


if __name__ == "__main__":
    main()
