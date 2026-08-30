#!/usr/bin/env python3
"""
ONVIF Camera Information Tool

Retrieves detailed device information from a single ONVIF-compatible camera or NVR.

Part of the ModelT warehouse onboarding toolkit.

Prerequisites:
    pip install onvif-zeep

Usage:
    python onvif-info.py <ip> [options]

Examples:
    python onvif-info.py 192.168.0.77                      # Query camera
    python onvif-info.py 192.168.0.75 -u admin -p secret   # With credentials
    python onvif-info.py 192.168.0.77 --json               # JSON output

Author: modeltcamerascat gen-3
"""

import argparse
import json
import sys
import logging

try:
    from onvif import ONVIFCamera
except ImportError:
    print("ERROR: Required package not installed.")
    print("Run: pip install onvif-zeep")
    sys.exit(1)

# Suppress noisy logs
logging.basicConfig(level=logging.WARNING)


def get_device_info(ip: str, port: int, username: str, password: str) -> dict:
    """
    Query device for all available ONVIF information.

    Args:
        ip: Device IP address
        port: ONVIF port
        username: ONVIF username
        password: ONVIF password

    Returns:
        Dict with device metadata
    """
    result = {
        'ip': ip,
        'port': port,
        'mac': None,
        'serial': None,
        'model': None,
        'manufacturer': None,
        'firmware': None,
        'hardware_id': None,
        'hostname': None,
        'scopes': [],
        'services': [],
        'profiles': [],
        'status': 'unknown',
        'error': None
    }

    try:
        cam = ONVIFCamera(ip, port, username, password)
        devicemgmt = cam.create_devicemgmt_service()

        # Basic device information
        info = devicemgmt.GetDeviceInformation()
        result['manufacturer'] = getattr(info, 'Manufacturer', None)
        result['model'] = getattr(info, 'Model', None)
        result['firmware'] = getattr(info, 'FirmwareVersion', None)
        result['serial'] = getattr(info, 'SerialNumber', None)
        result['hardware_id'] = getattr(info, 'HardwareId', None)
        result['status'] = 'ok'

        # Hostname
        try:
            hostname_info = devicemgmt.GetHostname()
            result['hostname'] = getattr(hostname_info, 'Name', None)
        except Exception:
            pass

        # MAC address from network interfaces
        try:
            network_info = devicemgmt.GetNetworkInterfaces()
            if network_info and len(network_info) > 0:
                iface = network_info[0]
                if hasattr(iface, 'Info') and hasattr(iface.Info, 'HwAddress'):
                    result['mac'] = iface.Info.HwAddress
        except Exception:
            pass

        # Scopes
        try:
            scopes = devicemgmt.GetScopes()
            result['scopes'] = [str(s.ScopeItem) for s in scopes] if scopes else []
        except Exception:
            pass

        # Media profiles (for cameras)
        try:
            media = cam.create_media_service()
            profiles = media.GetProfiles()
            result['profiles'] = [
                {'name': p.Name, 'token': p.token}
                for p in profiles[:5]  # Limit to first 5
            ]
        except Exception:
            pass

    except Exception as e:
        result['status'] = 'error'
        error_str = str(e)
        if 'Authorized' in error_str or 'auth' in error_str.lower():
            result['error'] = 'Authentication failed - check credentials'
        elif 'timed out' in error_str.lower() or 'timeout' in error_str.lower():
            result['error'] = 'Connection timeout'
        elif 'Connection refused' in error_str:
            result['error'] = 'Connection refused - ONVIF may not be supported'
        else:
            result['error'] = error_str[:100]

    return result


def format_output(info: dict) -> str:
    """Format device info as human-readable text."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"ONVIF Device Information: {info['ip']}")
    lines.append("=" * 60)

    if info['status'] == 'error':
        lines.append(f"\nERROR: {info['error']}")
        return '\n'.join(lines)

    lines.append(f"\n  Manufacturer:  {info.get('manufacturer') or 'N/A'}")
    lines.append(f"  Model:         {info.get('model') or 'N/A'}")
    lines.append(f"  Serial:        {info.get('serial') or 'N/A'}")
    lines.append(f"  Hardware ID:   {info.get('hardware_id') or 'N/A'}")
    lines.append(f"  Firmware:      {info.get('firmware') or 'N/A'}")
    lines.append(f"  Hostname:      {info.get('hostname') or 'N/A'}")
    lines.append(f"  MAC Address:   {info.get('mac') or 'N/A'}")

    if info.get('profiles'):
        lines.append(f"\n  Media Profiles ({len(info['profiles'])}):")
        for p in info['profiles']:
            lines.append(f"    - {p['name']}")

    if info.get('scopes'):
        lines.append(f"\n  Scopes ({len(info['scopes'])}):")
        for s in info['scopes'][:5]:  # Show first 5
            lines.append(f"    - {s}")
        if len(info['scopes']) > 5:
            lines.append(f"    ... and {len(info['scopes']) - 5} more")

    lines.append("\n" + "=" * 60)
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Get detailed ONVIF device information',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s 192.168.0.77                    Query with default credentials
  %(prog)s 192.168.0.75 -u admin -p pass   Specify credentials
  %(prog)s 192.168.0.77 --json             Output as JSON
  %(prog)s 192.168.0.77 --port 8000        Use non-standard port

Prerequisites:
  pip install onvif-zeep
        """
    )
    parser.add_argument('ip', help='IP address of the ONVIF device')
    parser.add_argument('-u', '--username', default='admin',
                        help='ONVIF username (default: admin)')
    parser.add_argument('-p', '--password', default='',
                        help='ONVIF password (default: empty)')
    parser.add_argument('--port', type=int, default=80,
                        help='ONVIF port (default: 80)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')

    args = parser.parse_args()

    # Query device
    info = get_device_info(args.ip, args.port, args.username, args.password)

    # Output
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(format_output(info))

    # Exit with error code if failed
    if info['status'] == 'error':
        sys.exit(1)


if __name__ == '__main__':
    main()
