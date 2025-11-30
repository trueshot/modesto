#!/usr/bin/env python
"""
GW Security NVR - Session-Based Historical Download
Logs into web UI first, then uses session cookie for CGI endpoints.

Usage: python nvr_session_download.py <channel> <hours_ago> [minutes_ago]
"""

import sys
import os
import hashlib
import requests
from datetime import datetime, timedelta
import cv2
import re
import json

# NVR Configuration
NVR_IP = "192.168.0.165"
NVR_USER = "admin"
NVR_PASS = ""  # Add password if needed
NVR_HTTP_PORT = 80

# Timezone offset
UTC_OFFSET_HOURS = 5


class GWSecurityNVR:
    """Client for GW Security NVR with session-based auth"""

    def __init__(self, ip, port=80, username="admin", password=""):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self.base_url = f"http://{ip}:{port}"
        self.session = requests.Session()
        self.logged_in = False

    def _md5(self, text):
        """MD5 hash helper"""
        return hashlib.md5(text.encode()).hexdigest()

    def login_web_ui(self):
        """Login via web interface to get session cookie"""
        print("  Attempting web UI login...")

        # First, get the login page to capture any tokens/cookies
        try:
            resp = self.session.get(f"{self.base_url}/", timeout=10)
            print(f"    Initial page: {resp.status_code}, cookies: {dict(self.session.cookies)}")
        except Exception as e:
            print(f"    Failed to load initial page: {e}")
            return False

        # Try various login endpoints and methods
        login_attempts = [
            # Method 1: JSON RPC login (Dahua style)
            {
                "url": f"{self.base_url}/RPC2_Login",
                "method": "json_rpc",
                "desc": "JSON RPC Login"
            },
            # Method 2: Form POST login
            {
                "url": f"{self.base_url}/cgi-bin/global.login",
                "method": "form",
                "desc": "CGI Form Login"
            },
            # Method 3: Direct login.cgi
            {
                "url": f"{self.base_url}/cgi-bin/login.cgi",
                "method": "form",
                "desc": "login.cgi"
            },
        ]

        for attempt in login_attempts:
            print(f"\n  Trying {attempt['desc']}...")

            if attempt["method"] == "json_rpc":
                success = self._json_rpc_login(attempt["url"])
            else:
                success = self._form_login(attempt["url"])

            if success:
                self.logged_in = True
                print(f"    ✅ Login successful!")
                print(f"    Session cookies: {dict(self.session.cookies)}")
                return True

        print("  ❌ All login methods failed")
        return False

    def _json_rpc_login(self, url):
        """Dahua-style JSON RPC login with challenge-response"""
        try:
            # Step 1: Initial request to get challenge
            payload = {
                "method": "global.login",
                "params": {
                    "userName": self.username,
                    "password": "",
                    "clientType": "Web3.0",
                    "loginType": "Direct"
                },
                "id": 1
            }

            resp = self.session.post(url, json=payload, timeout=10)
            data = resp.json()
            print(f"    Step 1 response: {str(data)[:200]}")

            if "error" in data and data["error"].get("code") == 268632079:
                # Need to do challenge-response
                params = data.get("params", {})
                realm = params.get("realm", "")
                random_str = params.get("random", "")
                session_id = data.get("session", 0)

                if random_str:
                    # Calculate password hash
                    pwd_phase1 = self._md5(f"{self.username}:{realm}:{self.password}")
                    pwd_final = self._md5(f"{self.username}:{random_str}:{pwd_phase1}")

                    # Step 2: Send hashed password
                    payload["params"]["password"] = pwd_final
                    payload["session"] = session_id
                    payload["id"] = 2

                    resp = self.session.post(url, json=payload, timeout=10)
                    data = resp.json()
                    print(f"    Step 2 response: {str(data)[:200]}")

                    if data.get("result"):
                        return True

            elif data.get("result"):
                return True

        except Exception as e:
            print(f"    JSON RPC error: {e}")

        return False

    def _form_login(self, url):
        """Traditional form-based login"""
        try:
            data = {
                "username": self.username,
                "password": self.password,
                "userName": self.username,  # Some use camelCase
            }

            resp = self.session.post(url, data=data, timeout=10)
            print(f"    Form response: {resp.status_code}, {resp.text[:200]}")

            # Check if we got a session cookie
            if "session" in dict(self.session.cookies) or resp.status_code == 200:
                # Verify by trying to access a protected page
                test = self.session.get(f"{self.base_url}/cgi-bin/configManager.cgi?action=getConfig&name=General", timeout=5)
                if test.status_code == 200 and len(test.content) > 400:
                    return True

        except Exception as e:
            print(f"    Form login error: {e}")

        return False

    def find_recordings(self, channel, start_time, end_time):
        """Search for recordings using recordFinder.cgi or mediaFileFind.cgi"""
        print(f"\n  Searching for recordings...")
        print(f"    Channel: {channel}")
        print(f"    Time range: {start_time} to {end_time}")

        # Format times for CGI
        start_str = start_time.strftime("%Y-%m-%d%%20%H:%M:%S")
        end_str = end_time.strftime("%Y-%m-%d%%20%H:%M:%S")

        # Try recordFinder.cgi
        endpoints = [
            # Method 1: recordFinder with factory.create pattern
            [
                (f"{self.base_url}/cgi-bin/recordFinder.cgi?action=factory.create", "Create finder"),
                (f"{self.base_url}/cgi-bin/recordFinder.cgi?action=findFile&object=0&condition.Channel={channel-1}&condition.StartTime={start_str}&condition.EndTime={end_str}", "Find files"),
            ],
            # Method 2: mediaFileFind
            [
                (f"{self.base_url}/cgi-bin/mediaFileFind.cgi?action=factory.create", "Create finder"),
                (f"{self.base_url}/cgi-bin/mediaFileFind.cgi?action=findFile&object=0&condition.Channel={channel-1}&condition.StartTime={start_str}&condition.EndTime={end_str}&condition.Types[0]=dav&condition.Types[1]=mp4", "Find files"),
            ],
        ]

        for endpoint_group in endpoints:
            print(f"\n    Trying {endpoint_group[0][1]}...")
            try:
                # Create finder
                resp = self.session.get(endpoint_group[0][0], timeout=10)
                print(f"      Create: {resp.status_code}, {resp.text[:200]}")

                if resp.status_code == 200 and "OK" in resp.text:
                    # Find files
                    resp = self.session.get(endpoint_group[1][0], timeout=10)
                    print(f"      Find: {resp.status_code}, {resp.text[:500]}")

                    if "FilePath" in resp.text or "found" in resp.text.lower():
                        return self._parse_file_list(resp.text)

            except Exception as e:
                print(f"      Error: {e}")

        return []

    def _parse_file_list(self, text):
        """Parse file list from CGI response"""
        files = []
        # Parse key=value format
        current_file = {}
        for line in text.split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip().split('.')[-1]  # Get last part of key
                value = value.strip()
                current_file[key] = value

                if key == "FilePath" and value:
                    files.append(current_file.copy())
                    current_file = {}

        return files

    def download_file(self, filepath):
        """Download a recording file via RPC_Loadfile"""
        print(f"\n  Downloading: {filepath}")

        url = f"{self.base_url}/cgi-bin/RPC_Loadfile{filepath}"
        print(f"    URL: {url[:80]}...")

        try:
            resp = self.session.get(url, timeout=30, stream=True)
            print(f"    Status: {resp.status_code}")

            if resp.status_code == 200:
                data = b""
                for chunk in resp.iter_content(chunk_size=8192):
                    data += chunk
                    if len(data) > 500000:  # 500KB should be enough for one frame
                        break
                resp.close()

                if len(data) > 1000:
                    print(f"    ✅ Downloaded {len(data)} bytes")
                    return data

        except Exception as e:
            print(f"    Download error: {e}")

        return None

    def download_by_time(self, channel, start_time, end_time):
        """Download recording directly by time range via loadfile.cgi"""
        print(f"\n  Downloading by time range...")

        start_str = start_time.strftime("%Y-%m-%d%%20%H:%M:%S")
        end_str = end_time.strftime("%Y-%m-%d%%20%H:%M:%S")

        url = (f"{self.base_url}/cgi-bin/loadfile.cgi"
               f"?action=startLoad&channel={channel-1}"
               f"&startTime={start_str}&endTime={end_str}&subtype=0")

        print(f"    URL: {url[:80]}...")

        try:
            resp = self.session.get(url, timeout=30, stream=True)
            print(f"    Status: {resp.status_code}")
            print(f"    Headers: {dict(resp.headers)}")

            if resp.status_code == 200:
                content_type = resp.headers.get('Content-Type', '')
                print(f"    Content-Type: {content_type}")

                data = b""
                for chunk in resp.iter_content(chunk_size=8192):
                    data += chunk
                    if len(data) > 500000:
                        break
                resp.close()

                if len(data) > 1000:
                    print(f"    ✅ Got {len(data)} bytes")
                    return data
                else:
                    print(f"    ⚠️ Only got {len(data)} bytes: {data[:200]}")

        except Exception as e:
            print(f"    Error: {e}")

        return None

    def get_snapshot(self, channel):
        """Get live snapshot (for testing session)"""
        url = f"{self.base_url}/cgi-bin/snapshot.cgi?channel={channel-1}"

        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200 and len(resp.content) > 1000:
                content_type = resp.headers.get('Content-Type', '')
                if 'image' in content_type or resp.content[:3] == b'\xff\xd8\xff':
                    return resp.content
        except Exception as e:
            print(f"    Snapshot error: {e}")

        return None


def extract_frame_from_dav(data, output_file):
    """Extract first frame from DAV data"""
    temp_file = "temp_download.dav"
    with open(temp_file, "wb") as f:
        f.write(data)

    cap = cv2.VideoCapture(temp_file)
    ret, frame = cap.read()
    cap.release()

    os.remove(temp_file)

    if ret and frame is not None:
        cv2.imwrite(output_file, frame)
        return True
    return False


def main():
    if len(sys.argv) < 3:
        print("Usage: python nvr_session_download.py <channel> <hours_ago> [minutes_ago]")
        sys.exit(1)

    channel = int(sys.argv[1])
    hours = float(sys.argv[2])
    minutes = float(sys.argv[3]) if len(sys.argv) > 3 else 0

    target_time = datetime.now() - timedelta(hours=hours, minutes=minutes)
    utc_target = target_time + timedelta(hours=UTC_OFFSET_HOURS)

    print("=" * 70)
    print("GW Security NVR - Session-Based Historical Download")
    print("=" * 70)
    print(f"Channel:     {channel}")
    print(f"Target Time: {target_time.strftime('%Y-%m-%d %H:%M:%S')} (local)")
    print(f"UTC Time:    {utc_target.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"NVR:         {NVR_IP}")
    print("=" * 70)

    nvr = GWSecurityNVR(NVR_IP, NVR_HTTP_PORT, NVR_USER, NVR_PASS)

    # Step 1: Login
    print("\n[1] Logging in to web UI...")
    if not nvr.login_web_ui():
        print("\n❌ Could not login. Check credentials or try web UI manually.")
        return 1

    # Step 2: Test with live snapshot
    print("\n[2] Testing session with live snapshot...")
    snapshot = nvr.get_snapshot(channel)
    if snapshot:
        print(f"    ✅ Live snapshot works ({len(snapshot)} bytes)")
        with open("test_live_snapshot.jpg", "wb") as f:
            f.write(snapshot)
        print("    Saved: test_live_snapshot.jpg")
    else:
        print("    ⚠️ Live snapshot failed, but continuing...")

    # Step 3: Search for recordings
    print("\n[3] Searching for recordings...")
    start_time = utc_target - timedelta(minutes=2)
    end_time = utc_target + timedelta(minutes=2)

    files = nvr.find_recordings(channel, start_time, end_time)
    if files:
        print(f"    Found {len(files)} file(s)")
        for f in files[:3]:
            print(f"      - {f}")

    # Step 4: Try direct time-based download
    print("\n[4] Attempting time-based download...")
    data = nvr.download_by_time(channel, utc_target, utc_target + timedelta(seconds=10))

    if data and len(data) > 1000:
        output_file = f"historical_ch{channel}_{target_time.strftime('%Y%m%d_%H%M%S')}.jpg"
        if extract_frame_from_dav(data, output_file):
            print(f"\n✅ SUCCESS! Saved: {output_file}")
            return 0
        else:
            # Save raw data for inspection
            with open("raw_download.dav", "wb") as f:
                f.write(data)
            print("    Saved raw data to raw_download.dav for inspection")

    print("\n" + "=" * 70)
    print("Session-based approach didn't yield historical footage.")
    print("The NVR may require the proprietary SDK on port 9000.")
    print("=" * 70)

    return 1


if __name__ == "__main__":
    sys.exit(main())
