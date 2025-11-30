#!/usr/bin/env python
"""
Test Historical Playback from GW5532 NVR
Tries multiple RTSP playback URL formats to find which one works.

Usage: python test_historical_playback.py [hours_ago]
Example: python test_historical_playback.py 1
"""

import cv2
import sys
from datetime import datetime, timedelta

# NVR Configuration
NVR_IP = "192.168.0.165"
NVR_USER = "admin"
NVR_PASS = ""
NVR_PORT = 554
TEST_CHANNEL = 1

# Timezone offset (EST = 5, CST = 6, etc.)
UTC_OFFSET_HOURS = 5  # Adjust for your timezone

def test_url(url, timeout_ms=10000):
    """Try to connect and grab a frame from the URL"""
    print(f"  Testing: {url[:80]}...")

    # Set timeout via environment or cap properties
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms)

    if not cap.isOpened():
        print("    ❌ Failed to connect")
        return False, None

    ret, frame = cap.read()
    cap.release()

    if ret and frame is not None:
        h, w = frame.shape[:2]
        print(f"    ✅ SUCCESS! Got frame {w}x{h}")
        return True, frame
    else:
        print("    ❌ Connected but no frame")
        return False, None


def generate_playback_urls(channel, start_time, end_time):
    """Generate various playback URL formats to test"""

    # Format times in different styles
    dahua_start = start_time.strftime("%Y_%m_%d_%H_%M_%S")
    dahua_end = end_time.strftime("%Y_%m_%d_%H_%M_%S")

    iso_start = start_time.strftime("%Y%m%dT%H%M%S")
    iso_end = end_time.strftime("%Y%m%dT%H%M%S")

    iso_z_start = start_time.strftime("%Y%m%dT%H%M%SZ")

    # Auth string
    auth = f"{NVR_USER}:{NVR_PASS}@" if NVR_USER else ""
    base = f"rtsp://{auth}{NVR_IP}:{NVR_PORT}"

    # Channel formats
    ch_padded = f"{channel:02d}"

    urls = [
        # Dahua-style (most likely for GW Security)
        (f"{base}/cam/playback?channel={channel}&starttime={dahua_start}&endtime={dahua_end}",
         "Dahua style (underscore dates)"),

        # Dahua with subtype
        (f"{base}/cam/playback?channel={channel}&subtype=0&starttime={dahua_start}&endtime={dahua_end}",
         "Dahua with subtype=0"),

        # ISO style without Z
        (f"{base}/cam/playback?channel={channel}&starttime={iso_start}&endtime={iso_end}",
         "ISO style (no Z)"),

        # Hikvision tracks style
        (f"{base}/Streaming/tracks/{channel}01?starttime={iso_z_start}",
         "Hikvision tracks style"),

        # Alternative Hikvision
        (f"{base}/Streaming/Channels/{channel}01?starttime={iso_z_start}",
         "Hikvision channels style"),

        # ONVIF replay style
        (f"{base}/onvif-media/media.amp?profile=Profile_{channel}&starttime={iso_z_start}",
         "ONVIF media style"),

        # Generic with ch prefix
        (f"{base}/ch{ch_padded}/playback?starttime={dahua_start}&endtime={dahua_end}",
         "Generic ch## playback"),

        # NVR playback path
        (f"{base}/Playback/Channels/{channel}01?starttime={iso_z_start}",
         "Playback/Channels style"),
    ]

    return urls


def main():
    hours_ago = 1
    if len(sys.argv) > 1:
        try:
            hours_ago = float(sys.argv[1])
        except ValueError:
            print(f"Invalid hours: {sys.argv[1]}, using 1")

    # Calculate times
    local_now = datetime.now()
    local_target = local_now - timedelta(hours=hours_ago)

    # Convert to UTC for NVR
    utc_start = local_target + timedelta(hours=UTC_OFFSET_HOURS)
    utc_end = utc_start + timedelta(seconds=30)

    print("=" * 70)
    print("GW5532 NVR Historical Playback Test")
    print("=" * 70)
    print(f"NVR IP:        {NVR_IP}")
    print(f"Test Channel:  {TEST_CHANNEL}")
    print(f"Local Now:     {local_now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target Time:   {local_target.strftime('%Y-%m-%d %H:%M:%S')} ({hours_ago}h ago)")
    print(f"UTC Start:     {utc_start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"UTC End:       {utc_end.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()

    # Generate and test URLs
    urls = generate_playback_urls(TEST_CHANNEL, utc_start, utc_end)

    successful = []

    for i, (url, description) in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] {description}")
        success, frame = test_url(url)

        if success:
            # Save the successful frame
            filename = f"historical_ch{TEST_CHANNEL}_{description.replace(' ', '_')}.jpg"
            cv2.imwrite(filename, frame)
            print(f"    📸 Saved: {filename}")
            successful.append((description, url))

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if successful:
        print(f"\n✅ {len(successful)} format(s) worked:\n")
        for desc, url in successful:
            print(f"  • {desc}")
            print(f"    {url}\n")
    else:
        print("\n❌ No formats worked. Possible issues:")
        print("  1. NVR doesn't support RTSP playback (check web UI for playback API)")
        print("  2. No recording exists for that time period")
        print("  3. Different authentication required")
        print("  4. RTSP playback disabled in NVR settings")
        print("\nTry:")
        print("  - Check NVR web interface for API/SDK documentation")
        print("  - Enable ONVIF/ISAPI in NVR settings")
        print("  - Try a time you know has recorded footage")

    print("=" * 70)

    return 0 if successful else 1


if __name__ == "__main__":
    exit(main())
