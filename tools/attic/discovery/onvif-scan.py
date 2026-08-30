#!/usr/bin/env python3
"""
ONVIF Network Camera Scanner

Discovers ONVIF-compatible cameras on a network and retrieves their metadata
(MAC address, serial number, model, manufacturer).

Part of the ModelT warehouse onboarding toolkit.

Prerequisites:
    pip install onvif-zeep wsdiscovery

Usage:
    python onvif-scan.py [options]

Examples:
    python onvif-scan.py                    # Scan local network, table output
    python onvif-scan.py --json             # Output as JSON
    python onvif-scan.py --json > cams.json # Save to file
    python onvif-scan.py --timeout 15       # Longer discovery timeout

Author: modeltcamerascat gen-3
"""

import argparse
import json
import re
import sys
import time
import logging
from typing import List, Dict, Any, Tuple

try:
    from onvif import ONVIFCamera
    from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery
except ImportError:
    print("ERROR: Required packages not installed.")
    print("Run: pip install onvif-zeep wsdiscovery")
    sys.exit(1)

# Suppress noisy logs
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Default credentials to try (add site-specific credentials here)
DEFAULT_CREDENTIALS = [
    ('admin', ''),           # Common default
    ('admin', 'admin'),      # Common default
    ('admin', 'Dad5eeeee!'), # DigitalWatchdog NVR
]


def discover_devices(timeout: int = 10) -> List[str]:
    """
    Use WS-Discovery to find ONVIF devices on the network.

    Args:
        timeout: Discovery timeout in seconds

    Returns:
        List of IP addresses with ONVIF support
    """
    wsd = WSDiscovery()
    wsd.start()

    time.sleep(2)  # Initial discovery time
    services = wsd.searchServices(timeout=timeout - 2)
    wsd.stop()

    # Extract unique camera IPs
    camera_ips = set()
    for service in services:
        for addr in service.getXAddrs():
            if 'onvif' in addr.lower():
                match = re.search(r'http://([0-9.]+)', addr)
                if match:
                    camera_ips.add(match.group(1))

    return sorted(camera_ips)


def get_camera_info(ip: str, port: int = 80,
                    credentials: List[Tuple[str, str]] = None,
                    timeout: int = 10) -> Dict[str, Any]:
    """
    Query a camera for device information via ONVIF.

    Args:
        ip: Camera IP address
        port: ONVIF port (usually 80)
        credentials: List of (username, password) tuples to try
        timeout: Connection timeout in seconds

    Returns:
        Dict with camera metadata
    """
    if credentials is None:
        credentials = DEFAULT_CREDENTIALS

    result = {
        'ip': ip,
        'port': port,
        'mac': None,
        'serial': None,
        'model': None,
        'manufacturer': None,
        'firmware': None,
        'hostname': None,
        'status': 'unknown',
        'error': None
    }

    for username, password in credentials:
        try:
            cam = ONVIFCamera(ip, port, username, password)
            devicemgmt = cam.create_devicemgmt_service()
            info = devicemgmt.GetDeviceInformation()

            result['manufacturer'] = getattr(info, 'Manufacturer', None)
            result['model'] = getattr(info, 'Model', None)
            result['firmware'] = getattr(info, 'FirmwareVersion', None)
            result['serial'] = getattr(info, 'SerialNumber', None)
            result['status'] = 'ok'
            result['error'] = None

            # Get hostname
            try:
                hostname_info = devicemgmt.GetHostname()
                result['hostname'] = getattr(hostname_info, 'Name', None)
            except Exception:
                pass

            # Get MAC address
            try:
                network_info = devicemgmt.GetNetworkInterfaces()
                if network_info and len(network_info) > 0:
                    iface = network_info[0]
                    if hasattr(iface, 'Info') and hasattr(iface.Info, 'HwAddress'):
                        result['mac'] = iface.Info.HwAddress
            except Exception:
                pass

            return result

        except Exception as e:
            error_str = str(e)
            if 'Authorized' in error_str or 'auth' in error_str.lower():
                result['error'] = 'auth_failed'
            elif 'timed out' in error_str.lower() or 'timeout' in error_str.lower():
                result['error'] = 'timeout'
            else:
                result['error'] = error_str[:80]
            continue

    result['status'] = 'error'
    return result


def format_table(results: List[Dict[str, Any]]) -> str:
    """Format results as a human-readable table."""
    lines = []

    # Header
    lines.append("=" * 90)
    lines.append(f"{'IP':<16} {'MAC':<20} {'Serial':<18} {'Model':<25} {'Status'}")
    lines.append("=" * 90)

    # Data rows
    for r in results:
        mac = r.get('mac') or '-'
        serial = r.get('serial') or '-'
        model = r.get('model') or '-'
        if len(model) > 24:
            model = model[:21] + '...'
        status = r.get('status', 'unknown')
        if status == 'error':
            status = f"error: {r.get('error', 'unknown')[:15]}"

        lines.append(f"{r['ip']:<16} {mac:<20} {serial:<18} {model:<25} {status}")

    lines.append("=" * 90)

    # Summary
    ok_count = sum(1 for r in results if r.get('status') == 'ok')
    lines.append(f"\nTotal: {len(results)} devices, {ok_count} successful, {len(results) - ok_count} failed")

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Discover ONVIF cameras on the network',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                     Scan network, show table
  %(prog)s --json              Output as JSON
  %(prog)s --timeout 20        Longer discovery timeout
  %(prog)s --exclude 192.168.0.75  Skip NVR IP

Prerequisites:
  pip install onvif-zeep wsdiscovery
        """
    )
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON instead of table')
    parser.add_argument('--timeout', type=int, default=10,
                        help='Discovery timeout in seconds (default: 10)')
    parser.add_argument('--exclude', nargs='*', default=[],
                        help='IP addresses to exclude (e.g., NVR IPs)')
    parser.add_argument('--credentials', nargs='*',
                        help='Additional credentials as user:pass pairs')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Suppress progress output')

    args = parser.parse_args()

    # Build credentials list
    credentials = list(DEFAULT_CREDENTIALS)
    if args.credentials:
        for cred in args.credentials:
            if ':' in cred:
                user, passwd = cred.split(':', 1)
                credentials.insert(0, (user, passwd))

    # Discover devices
    if not args.quiet and not args.json:
        print(f"Scanning for ONVIF devices (timeout: {args.timeout}s)...", file=sys.stderr)

    ips = discover_devices(timeout=args.timeout)

    # Filter excluded IPs
    ips = [ip for ip in ips if ip not in args.exclude]

    if not args.quiet and not args.json:
        print(f"Found {len(ips)} devices, querying...", file=sys.stderr)

    # Query each device
    results = []
    for i, ip in enumerate(ips):
        if not args.quiet and not args.json:
            print(f"  [{i+1}/{len(ips)}] {ip}...", file=sys.stderr, end=' ', flush=True)

        info = get_camera_info(ip, credentials=credentials)
        results.append(info)

        if not args.quiet and not args.json:
            if info['status'] == 'ok':
                print(f"{info.get('manufacturer', '?')} {info.get('model', '?')}", file=sys.stderr)
            else:
                print(f"ERROR: {info.get('error', 'unknown')}", file=sys.stderr)

    # Output results
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_table(results))


if __name__ == '__main__':
    main()
