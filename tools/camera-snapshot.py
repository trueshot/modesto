#!/usr/bin/env python3
"""
Camera Snapshot Tool

Captures snapshots from ONVIF cameras (by IP) or NVR channels (by RTSP URL).
Used to match camera identity (MAC) to NVR channel assignment.

Usage:
    # Snapshot from ONVIF camera by IP
    python camera-snapshot.py onvif 192.168.0.133 -o camera_133.jpg

    # Snapshot from NVR channel by RTSP URL
    python camera-snapshot.py rtsp "rtsp://admin:@192.168.0.165:554/ch01/0" -o channel_01.jpg

    # Batch: all ONVIF cameras
    python camera-snapshot.py batch-onvif -o ./snapshots/onvif/

    # Batch: all NVR channels from config
    python camera-snapshot.py batch-nvr -c config.json -o ./snapshots/nvr/

Author: novicat gen-1
"""

import argparse
import json
import os
import subprocess
import sys
import time

try:
    from onvif import ONVIFCamera
    import requests
    ONVIF_AVAILABLE = True
except ImportError:
    ONVIF_AVAILABLE = False


def get_onvif_snapshot(ip: str, port: int = 80, username: str = 'admin',
                       password: str = '', output_path: str = None) -> dict:
    """Get snapshot from ONVIF camera."""
    if not ONVIF_AVAILABLE:
        return {'status': 'error', 'error': 'onvif-zeep not installed'}

    result = {'ip': ip, 'status': 'unknown', 'path': None, 'error': None}

    try:
        cam = ONVIFCamera(ip, port, username, password)
        media = cam.create_media_service()

        # Get first profile
        profiles = media.GetProfiles()
        if not profiles:
            result['status'] = 'error'
            result['error'] = 'No media profiles found'
            return result

        profile_token = profiles[0].token

        # Get snapshot URI
        snapshot_uri = media.GetSnapshotUri({'ProfileToken': profile_token})
        uri = snapshot_uri.Uri

        # Fetch snapshot
        response = requests.get(uri, auth=(username, password), timeout=10)
        if response.status_code == 200:
            if output_path is None:
                output_path = f"snapshot_{ip.replace('.', '_')}.jpg"

            with open(output_path, 'wb') as f:
                f.write(response.content)

            result['status'] = 'ok'
            result['path'] = output_path
            result['size'] = len(response.content)
        else:
            result['status'] = 'error'
            result['error'] = f'HTTP {response.status_code}'

    except Exception as e:
        result['status'] = 'error'
        result['error'] = str(e)[:100]

    return result


def get_rtsp_snapshot(rtsp_url: str, output_path: str = None) -> dict:
    """Get snapshot from RTSP stream using ffmpeg."""
    result = {'url': rtsp_url, 'status': 'unknown', 'path': None, 'error': None}

    if output_path is None:
        # Generate filename from URL
        safe_name = rtsp_url.split('/')[-1].replace('/', '_')
        output_path = f"snapshot_rtsp_{safe_name}.jpg"

    try:
        # Use ffmpeg to capture single frame
        cmd = [
            'ffmpeg',
            '-y',  # Overwrite
            '-rtsp_transport', 'tcp',
            '-i', rtsp_url,
            '-frames:v', '1',
            '-q:v', '2',
            output_path
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30
        )

        if proc.returncode == 0 and os.path.exists(output_path):
            result['status'] = 'ok'
            result['path'] = output_path
            result['size'] = os.path.getsize(output_path)
        else:
            result['status'] = 'error'
            result['error'] = proc.stderr.decode()[-200:] if proc.stderr else 'Unknown error'

    except subprocess.TimeoutExpired:
        result['status'] = 'error'
        result['error'] = 'Timeout (30s)'
    except FileNotFoundError:
        result['status'] = 'error'
        result['error'] = 'ffmpeg not found - install ffmpeg'
    except Exception as e:
        result['status'] = 'error'
        result['error'] = str(e)[:100]

    return result


def batch_onvif_snapshots(output_dir: str, camera_ips: list = None) -> list:
    """Capture snapshots from multiple ONVIF cameras."""
    os.makedirs(output_dir, exist_ok=True)
    results = []

    # If no IPs provided, try to discover or use known list
    if camera_ips is None:
        # Known GW Security camera IPs from ONVIF scan
        camera_ips = [
            '192.168.0.133', '192.168.0.170', '192.168.0.185', '192.168.0.224',  # N802-IRC-GW
            '192.168.0.151', '192.168.0.152', '192.168.0.156', '192.168.0.66',   # YM600F_AF
            '192.168.0.70'  # GW-GW5750MIPWIFI
        ]

    for ip in camera_ips:
        output_path = os.path.join(output_dir, f"onvif_{ip.replace('.', '_')}.jpg")
        print(f"Capturing {ip}...", end=' ', flush=True)
        result = get_onvif_snapshot(ip, output_path=output_path)
        print(result['status'])
        results.append(result)
        time.sleep(0.5)  # Brief delay between cameras

    return results


def batch_nvr_snapshots(config_path: str, output_dir: str) -> list:
    """Capture snapshots from all NVR channels in config."""
    os.makedirs(output_dir, exist_ok=True)
    results = []

    with open(config_path, 'r') as f:
        config = json.load(f)

    for channel in config.get('channels', []):
        rtsp_url = channel.get('rtspUrl')
        food_name = channel.get('modelTCameraId', 'unknown')
        ch_num = channel.get('channel', 0)

        if not rtsp_url:
            continue

        output_path = os.path.join(output_dir, f"ch{ch_num:02d}_{food_name}.jpg")
        print(f"Capturing ch{ch_num:02d} ({food_name})...", end=' ', flush=True)
        result = get_rtsp_snapshot(rtsp_url, output_path=output_path)
        result['channel'] = ch_num
        result['food_name'] = food_name
        print(result['status'])
        results.append(result)
        time.sleep(0.5)

    return results


def main():
    parser = argparse.ArgumentParser(
        description='Capture camera snapshots for identity matching',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='mode', required=True)

    # ONVIF single camera
    onvif_parser = subparsers.add_parser('onvif', help='Snapshot from ONVIF camera')
    onvif_parser.add_argument('ip', help='Camera IP address')
    onvif_parser.add_argument('-o', '--output', help='Output file path')
    onvif_parser.add_argument('-u', '--username', default='admin')
    onvif_parser.add_argument('-p', '--password', default='')
    onvif_parser.add_argument('--port', type=int, default=80)

    # RTSP single channel
    rtsp_parser = subparsers.add_parser('rtsp', help='Snapshot from RTSP URL')
    rtsp_parser.add_argument('url', help='RTSP URL')
    rtsp_parser.add_argument('-o', '--output', help='Output file path')

    # Batch ONVIF
    batch_onvif_parser = subparsers.add_parser('batch-onvif', help='Snapshot all known ONVIF cameras')
    batch_onvif_parser.add_argument('-o', '--output-dir', default='./snapshots/onvif')
    batch_onvif_parser.add_argument('--ips', nargs='+', help='Specific IPs to capture')

    # Batch NVR
    batch_nvr_parser = subparsers.add_parser('batch-nvr', help='Snapshot all NVR channels')
    batch_nvr_parser.add_argument('-c', '--config', required=True, help='Path to config.json')
    batch_nvr_parser.add_argument('-o', '--output-dir', default='./snapshots/nvr')

    args = parser.parse_args()

    if args.mode == 'onvif':
        result = get_onvif_snapshot(
            args.ip, args.port, args.username, args.password, args.output
        )
        print(json.dumps(result, indent=2))

    elif args.mode == 'rtsp':
        result = get_rtsp_snapshot(args.url, args.output)
        print(json.dumps(result, indent=2))

    elif args.mode == 'batch-onvif':
        results = batch_onvif_snapshots(args.output_dir, args.ips)
        ok = sum(1 for r in results if r['status'] == 'ok')
        print(f"\nCaptured {ok}/{len(results)} ONVIF snapshots to {args.output_dir}")

    elif args.mode == 'batch-nvr':
        results = batch_nvr_snapshots(args.config, args.output_dir)
        ok = sum(1 for r in results if r['status'] == 'ok')
        print(f"\nCaptured {ok}/{len(results)} NVR channel snapshots to {args.output_dir}")


if __name__ == '__main__':
    main()
