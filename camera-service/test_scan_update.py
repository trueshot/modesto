#!/usr/bin/env python3
"""
Test script for scan-and-update functionality
Demonstrates how to scan NVR and automatically update config.json
"""

import requests
import json

# API base URL
BASE_URL = "http://localhost:8001"

def test_scan_and_update(facility="lodge"):
    """Test scanning and updating facility config"""

    print(f"Testing scan-and-update for facility: {facility}")
    print("="*70)

    # Call scan-and-update endpoint
    url = f"{BASE_URL}/api/cameras/{facility}/scan-and-update"
    params = {
        "quick": True,  # Use quick scan (faster)
        "max_channels": 32,
        "preserve_modelt_info": True  # Keep existing camera names
    }

    print(f"\nCalling: POST {url}")
    print(f"Parameters: {json.dumps(params, indent=2)}")
    print("\nScanning NVR...")

    response = requests.post(url, params=params)

    if response.status_code == 200:
        result = response.json()
        print("\n✓ SUCCESS!")
        print("="*70)
        print(f"Facility:           {result['facility']}")
        print(f"NVR IP:             {result['nvr_ip']}")
        print(f"Channels Found:     {result['channels_found']}")
        print(f"Channels Updated:   {result['channels_updated']}")
        print(f"Preserved Info:     {result['preserved_modelt_info']}")
        print(f"\nMessage: {result['message']}")
        print("="*70)

        # Show current camera list
        print("\nFetching updated camera list...")
        cameras_response = requests.get(f"{BASE_URL}/api/cameras/{facility}")
        if cameras_response.status_code == 200:
            cameras_data = cameras_response.json()
            print(f"\nTotal cameras: {cameras_data['count']}")
            print("\nCameras:")
            for cam in cameras_data['cameras']:
                print(f"  - Ch{cam['channel']:02d}: {cam['name']} ({cam['id']}) - {cam['resolution']}")

    else:
        print(f"\n✗ ERROR: {response.status_code}")
        print(response.text)

def test_regular_scan():
    """Test regular scan without saving"""

    print("\n\nTesting regular scan (no save)...")
    print("="*70)

    url = f"{BASE_URL}/api/scan"
    data = {
        "nvr_ip": "192.168.0.165",
        "username": "admin",
        "password": "",
        "port": 554,
        "max_channels": 32,
        "quick": True
    }

    print(f"Calling: POST {url}")
    response = requests.post(url, json=data)

    if response.status_code == 200:
        result = response.json()
        print(f"\n✓ Scan complete: {result['channels_found']} channels found")
        print("(Note: These results are NOT saved to config.json)")
    else:
        print(f"✗ ERROR: {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    print("Camera Service - Scan and Update Test")
    print("="*70)

    # Test the new scan-and-update endpoint
    test_scan_and_update("lodge")

    # Show comparison with regular scan
    test_regular_scan()

    print("\n" + "="*70)
    print("Test complete!")
    print("\nThe scan-and-update endpoint:")
    print("  • Scans the NVR")
    print("  • Updates config.json with discovered cameras")
    print("  • Preserves existing ModelT camera names/locations")
    print("  • Adds new cameras with default names")
    print("="*70)
