#!/usr/bin/env python
"""
Historical Snapshot from GW Security NVR
Tries multiple methods to grab a frame from recorded footage.

Methods attempted:
1. Dahua RPC API (port 80) - mediaFileFind + loadfile
2. Direct RTSP playback URL
3. HTTP loadfile.cgi

Usage: python historical_snapshot.py <channel> <hours_ago> [minutes_ago]
Example: python historical_snapshot.py 1 1        # channel 1, 1 hour ago
         python historical_snapshot.py 5 0 30    # channel 5, 30 minutes ago
"""

import sys
import os
import hashlib
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
from datetime import datetime, timedelta
import cv2
import json

# NVR Configuration
NVR_IP = "192.168.0.165"
NVR_USER = "admin"
NVR_PASS = ""
NVR_HTTP_PORT = 80
NVR_RTSP_PORT = 554

# Timezone offset (EST = 5, CST = 6, PST = 8, etc.)
UTC_OFFSET_HOURS = 5


class DahuaRPC:
    """Simple Dahua RPC client for port 80 JSON-RPC API"""

    def __init__(self, host, port=80, username="admin", password=""):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session_id = None
        self.base_url = f"http://{host}:{port}"

    def _md5(self, text):
        return hashlib.md5(text.encode()).hexdigest().upper()

    def login(self):
        """Login using Dahua's challenge-response auth"""
        # First request to get realm and random
        url = f"{self.base_url}/RPC2_Login"
        payload = {
            "method": "global.login",
            "params": {
                "userName": self.username,
                "password": "",
                "clientType": "Web3.0"
            },
            "id": 1
        }

        try:
            resp = self.session.post(url, json=payload, timeout=10)
            data = resp.json()

            if "params" in data and "random" in data["params"]:
                realm = data["params"].get("realm", "Login to " + self.host)
                random_str = data["params"]["random"]

                # Calculate password hash
                pwd_hash = self._md5(f"{self.username}:{realm}:{self.password}")
                final_hash = self._md5(f"{self.username}:{random_str}:{pwd_hash}")

                # Send actual login
                payload["params"]["password"] = final_hash
                payload["session"] = data.get("session", 0)
                payload["id"] = 2

                resp = self.session.post(url, json=payload, timeout=10)
                login_data = resp.json()

                if login_data.get("result"):
                    self.session_id = login_data.get("session")
                    print(f"  ✅ RPC Login successful (session: {self.session_id})")
                    return True
                else:
                    print(f"  ❌ RPC Login failed: {login_data}")
                    return False
            else:
                print(f"  ❌ Unexpected login response: {data}")
                return False

        except Exception as e:
            print(f"  ❌ RPC Login error: {e}")
            return False

    def request(self, method, params=None):
        """Make an RPC request"""
        url = f"{self.base_url}/RPC2"
        payload = {
            "method": method,
            "params": params or {},
            "id": 10,
            "session": self.session_id
        }

        try:
            resp = self.session.post(url, json=payload, timeout=30)
            return resp.json()
        except Exception as e:
            print(f"  RPC request error: {e}")
            return None

    def find_files(self, channel, start_time, end_time):
        """Find recorded files in time range"""
        # Create finder
        result = self.request("mediaFileFind.factory.create")
        if not result or not result.get("result"):
            print(f"  ❌ Failed to create file finder")
            return []

        finder_id = result.get("params", {}).get("object", 0)

        # Set search conditions
        condition = {
            "Channel": channel - 1,  # 0-based
            "StartTime": start_time,
            "EndTime": end_time,
            "Types": ["dav", "mp4"],
            "Flags": ["Event", "Timing"],
        }

        result = self.request("mediaFileFind.findFile", {
            "object": finder_id,
            "condition": condition
        })

        if not result:
            return []

        files = result.get("params", {}).get("items", [])
        print(f"  Found {len(files)} recording(s)")

        # Destroy finder
        self.request("mediaFileFind.destroy", {"object": finder_id})

        return files


def method1_dahua_rpc(channel, target_time):
    """Try Dahua RPC API to find and download recording"""
    print("\n[Method 1] Dahua RPC API (port 80)...")

    rpc = DahuaRPC(NVR_IP, NVR_HTTP_PORT, NVR_USER, NVR_PASS)

    if not rpc.login():
        return None

    # Search for files around target time
    start_time = (target_time - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    end_time = (target_time + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

    files = rpc.find_files(channel, start_time, end_time)

    if files:
        print(f"  Found files: {json.dumps(files[:2], indent=2)}")
        # TODO: Download file via RPC_Loadfile
        return files
    else:
        print("  No files found in time range")
        return None


def method2_rtsp_playback(channel, target_time):
    """Try RTSP playback URL"""
    print("\n[Method 2] RTSP Playback URL...")

    # Calculate UTC time
    utc_time = target_time + timedelta(hours=UTC_OFFSET_HOURS)
    utc_end = utc_time + timedelta(seconds=10)

    # Try various URL formats
    formats = [
        # Dahua underscore format
        (f"rtsp://{NVR_USER}:@{NVR_IP}:{NVR_RTSP_PORT}/cam/playback?channel={channel}"
         f"&starttime={utc_time.strftime('%Y_%m_%d_%H_%M_%S')}"
         f"&endtime={utc_end.strftime('%Y_%m_%d_%H_%M_%S')}",
         "Dahua underscore"),

        # ISO format
        (f"rtsp://{NVR_USER}:@{NVR_IP}:{NVR_RTSP_PORT}/cam/playback?channel={channel}"
         f"&starttime={utc_time.strftime('%Y%m%dT%H%M%S')}"
         f"&endtime={utc_end.strftime('%Y%m%dT%H%M%S')}",
         "ISO format"),
    ]

    for url, desc in formats:
        print(f"  Trying {desc}...")
        print(f"    URL: {url[:80]}...")

        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000)

        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                print(f"  ✅ Got frame via {desc}")
                return frame

        cap.release()
        print(f"    ❌ Failed")

    return None


def method3_http_loadfile(channel, target_time):
    """Try HTTP loadfile.cgi"""
    print("\n[Method 3] HTTP loadfile.cgi...")

    # Calculate UTC time
    utc_time = target_time + timedelta(hours=UTC_OFFSET_HOURS)
    utc_end = utc_time + timedelta(seconds=30)

    start_str = utc_time.strftime("%Y-%m-%d%%20%H:%M:%S")
    end_str = utc_end.strftime("%Y-%m-%d%%20%H:%M:%S")

    url = (f"http://{NVR_IP}:{NVR_HTTP_PORT}/cgi-bin/loadfile.cgi"
           f"?action=startLoad&channel={channel}&startTime={start_str}"
           f"&endTime={end_str}&subtype=0")

    print(f"  URL: {url[:80]}...")

    auth_methods = [
        (HTTPDigestAuth(NVR_USER, NVR_PASS), "Digest"),
        (HTTPBasicAuth(NVR_USER, NVR_PASS), "Basic"),
        (None, "None"),
    ]

    for auth, desc in auth_methods:
        try:
            resp = requests.get(url, auth=auth, timeout=15, stream=True)
            print(f"  {desc} auth: Status {resp.status_code}")

            if resp.status_code == 200:
                # Read some data
                data = b""
                for chunk in resp.iter_content(chunk_size=8192):
                    data += chunk
                    if len(data) > 100000:
                        break
                resp.close()

                if len(data) > 1000:
                    print(f"  ✅ Got {len(data)} bytes")
                    # Save as .dav and try to extract frame
                    with open("temp_recording.dav", "wb") as f:
                        f.write(data)

                    # Try to read frame from .dav
                    cap = cv2.VideoCapture("temp_recording.dav")
                    ret, frame = cap.read()
                    cap.release()

                    if ret and frame is not None:
                        os.remove("temp_recording.dav")
                        return frame
                    else:
                        print("  ⚠️  Got data but couldn't decode frame")
        except Exception as e:
            print(f"  {desc} auth error: {e}")

    return None


def method4_snapshot_at_time(channel, target_time):
    """Try RPC snapshot at specific time (if supported)"""
    print("\n[Method 4] RPC Snapshot at Time...")

    rpc = DahuaRPC(NVR_IP, NVR_HTTP_PORT, NVR_USER, NVR_PASS)

    if not rpc.login():
        return None

    # Try various snapshot methods
    methods_to_try = [
        ("snap.getSnapshot", {
            "channel": channel - 1,
            "time": target_time.strftime("%Y-%m-%d %H:%M:%S")
        }),
        ("mediaFileFind.getSnapshot", {
            "channel": channel - 1,
            "time": target_time.strftime("%Y-%m-%d %H:%M:%S")
        }),
    ]

    for method, params in methods_to_try:
        print(f"  Trying {method}...")
        result = rpc.request(method, params)
        if result:
            print(f"    Response: {str(result)[:200]}")
            if result.get("result") and "data" in result.get("params", {}):
                print("  ✅ Got snapshot data!")
                return result

    return None


def main():
    if len(sys.argv) < 3:
        print("Usage: python historical_snapshot.py <channel> <hours_ago> [minutes_ago]")
        print("Example: python historical_snapshot.py 1 1")
        sys.exit(1)

    channel = int(sys.argv[1])
    hours = float(sys.argv[2])
    minutes = float(sys.argv[3]) if len(sys.argv) > 3 else 0

    # Calculate target time (local)
    target_time = datetime.now() - timedelta(hours=hours, minutes=minutes)

    print("=" * 70)
    print("GW Security NVR Historical Snapshot")
    print("=" * 70)
    print(f"Channel:     {channel}")
    print(f"Target Time: {target_time.strftime('%Y-%m-%d %H:%M:%S')} ({hours}h {minutes}m ago)")
    print(f"NVR:         {NVR_IP}")
    print("=" * 70)

    frame = None

    # Try each method
    result = method1_dahua_rpc(channel, target_time)
    if result:
        print("  → RPC found files, but download not yet implemented")

    frame = method2_rtsp_playback(channel, target_time)
    if frame is not None:
        filename = f"historical_ch{channel}_{target_time.strftime('%Y%m%d_%H%M%S')}.jpg"
        cv2.imwrite(filename, frame)
        print(f"\n✅ SUCCESS! Saved: {filename}")
        return 0

    frame = method3_http_loadfile(channel, target_time)
    if frame is not None:
        filename = f"historical_ch{channel}_{target_time.strftime('%Y%m%d_%H%M%S')}.jpg"
        cv2.imwrite(filename, frame)
        print(f"\n✅ SUCCESS! Saved: {filename}")
        return 0

    method4_snapshot_at_time(channel, target_time)

    print("\n" + "=" * 70)
    print("❌ All methods failed")
    print("=" * 70)
    print("""
Next steps:
1. Check NVR web UI for playback/download options
2. Look at network traffic when using web playback
3. Contact GW Security for SDK documentation
4. Use surveillance_client to export clips manually
""")

    return 1


if __name__ == "__main__":
    sys.exit(main())
