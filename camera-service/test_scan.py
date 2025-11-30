#!/usr/bin/env python3
"""
Test NVR scanning endpoint
"""

import requests
import json

# Scan request
scan_request = {
    "nvr_ip": "192.168.0.165",
    "username": "admin",
    "password": "",
    "port": 554,
    "max_channels": 16,
    "quick": True  # Quick scan (faster)
}

print("="*70)
print("SCANNING NVR FOR CAMERAS")
print("="*70)
print(f"NVR IP: {scan_request['nvr_ip']}")
print(f"Scan mode: {'Quick' if scan_request['quick'] else 'Full'}")
print()

# Call scan endpoint
response = requests.post(
    'http://localhost:8001/api/scan',
    json=scan_request
)

if response.status_code == 200:
    result = response.json()

    print("="*70)
    print("SCAN RESULTS")
    print("="*70)
    print(f"Channels found: {result['channels_found']}")
    print()

    for i, channel in enumerate(result['channels'], 1):
        print(f"Channel {i}:")
        print(f"  Path: {channel['path']}")
        print(f"  Resolution: {channel['resolution']}")
        if 'channel' in channel and channel['channel']:
            print(f"  Channel #: {channel['channel']}")
        print()

    # Save to file
    with open('scanned_channels.json', 'w') as f:
        json.dump(result, f, indent=2)

    print(f"Results saved to: scanned_channels.json")
    print("="*70)
else:
    print(f"Error: {response.status_code}")
    print(response.text)
