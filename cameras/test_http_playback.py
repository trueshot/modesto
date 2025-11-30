#!/usr/bin/env python
"""
Test HTTP API Playback from GW5532 NVR (Dahua-based firmware)
Tests various CGI endpoints for downloading historical recordings.

Usage: python test_http_playback.py [hours_ago]
Example: python test_http_playback.py 1
"""

import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
import sys
from datetime import datetime, timedelta
import os

# NVR Configuration
NVR_IP = "192.168.0.165"
NVR_USER = "admin"
NVR_PASS = ""  # Add password if required
NVR_HTTP_PORT = 80  # Try 80, 8080, or 443

# Timezone offset (EST = 5, CST = 6, etc.)
UTC_OFFSET_HOURS = 5


def test_endpoint(url, auth=None, description="", stream=False):
    """Test an HTTP endpoint"""
    print(f"\n  Testing: {description}")
    print(f"    URL: {url[:100]}...")

    try:
        if stream:
            response = requests.get(url, auth=auth, timeout=15, stream=True)
            # Read first chunk to see if it's returning data
            content = b""
            for chunk in response.iter_content(chunk_size=8192):
                content += chunk
                if len(content) > 50000:  # Got enough to save
                    break
            response.close()

            if len(content) > 1000:
                print(f"    ✅ SUCCESS! Got {len(content)} bytes (streaming)")
                return True, content
            else:
                print(f"    ❌ Only got {len(content)} bytes")
                if content:
                    print(f"    Response: {content[:200]}")
                return False, None
        else:
            response = requests.get(url, auth=auth, timeout=15)
            print(f"    Status: {response.status_code}")

            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '')
                content_len = len(response.content)
                print(f"    Content-Type: {content_type}")
                print(f"    Content-Length: {content_len}")

                if content_len > 1000:
                    print(f"    ✅ SUCCESS! Got data")
                    return True, response.content
                else:
                    print(f"    Response: {response.text[:500]}")
                    return False, None
            elif response.status_code == 401:
                print(f"    ⚠️  Authentication required")
                return False, None
            else:
                print(f"    ❌ Failed: {response.text[:200]}")
                return False, None

    except requests.exceptions.Timeout:
        print(f"    ❌ Timeout")
        return False, None
    except requests.exceptions.ConnectionError as e:
        print(f"    ❌ Connection error: {e}")
        return False, None
    except Exception as e:
        print(f"    ❌ Error: {e}")
        return False, None


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
    utc_start = local_target + timedelta(hours=UTC_OFFSET_HOURS)
    utc_end = utc_start + timedelta(minutes=1)

    # Format times for different APIs
    dahua_start = utc_start.strftime("%Y-%m-%d %H:%M:%S")
    dahua_end = utc_end.strftime("%Y-%m-%d %H:%M:%S")

    dahua_start_encoded = utc_start.strftime("%Y-%m-%d%%20%H:%M:%S")
    dahua_end_encoded = utc_end.strftime("%Y-%m-%d%%20%H:%M:%S")

    print("=" * 70)
    print("GW5532 NVR HTTP API Playback Test")
    print("=" * 70)
    print(f"NVR IP:        {NVR_IP}:{NVR_HTTP_PORT}")
    print(f"Target Time:   {local_target.strftime('%Y-%m-%d %H:%M:%S')} ({hours_ago}h ago)")
    print(f"UTC Range:     {dahua_start} to {dahua_end}")
    print("=" * 70)

    base_url = f"http://{NVR_IP}:{NVR_HTTP_PORT}"

    # Auth methods to try
    auth_methods = [
        (None, "No auth"),
        (HTTPDigestAuth(NVR_USER, NVR_PASS), "Digest auth"),
        (HTTPBasicAuth(NVR_USER, NVR_PASS), "Basic auth"),
    ]

    # First, test basic connectivity
    print("\n[1/6] Testing basic connectivity...")
    for auth, auth_desc in auth_methods:
        success, _ = test_endpoint(
            f"{base_url}/",
            auth=auth,
            description=f"Root page ({auth_desc})"
        )
        if success:
            break

    # Test snapshot endpoint (to verify API access works)
    print("\n[2/6] Testing snapshot endpoint...")
    for auth, auth_desc in auth_methods:
        success, data = test_endpoint(
            f"{base_url}/cgi-bin/snapshot.cgi?channel=1",
            auth=auth,
            description=f"Snapshot ({auth_desc})"
        )
        if success and data:
            with open("test_snapshot.jpg", "wb") as f:
                f.write(data)
            print(f"    📸 Saved: test_snapshot.jpg")
            break

    # Test loadfile.cgi for historical download
    print("\n[3/6] Testing loadfile.cgi (Dahua recording download)...")
    for auth, auth_desc in auth_methods:
        success, data = test_endpoint(
            f"{base_url}/cgi-bin/loadfile.cgi?action=startLoad&channel=1&startTime={dahua_start_encoded}&endTime={dahua_end_encoded}&subtype=0",
            auth=auth,
            description=f"loadfile.cgi ({auth_desc})",
            stream=True
        )
        if success and data:
            with open("test_historical.dav", "wb") as f:
                f.write(data)
            print(f"    📼 Saved: test_historical.dav")
            break

    # Test mediaFileFind.cgi to search for recordings
    print("\n[4/6] Testing mediaFileFind.cgi (find recordings)...")
    for auth, auth_desc in auth_methods:
        # First create a search
        success, data = test_endpoint(
            f"{base_url}/cgi-bin/mediaFileFind.cgi?action=factory.create",
            auth=auth,
            description=f"Create search session ({auth_desc})"
        )
        if success:
            # Try to find files
            success2, data2 = test_endpoint(
                f"{base_url}/cgi-bin/mediaFileFind.cgi?action=findFile&object=0&condition.Channel=0&condition.StartTime={dahua_start_encoded}&condition.EndTime={dahua_end_encoded}",
                auth=auth,
                description=f"Find files ({auth_desc})"
            )
            if success2:
                print(f"    Search result: {data2[:500] if data2 else 'empty'}")
            break

    # Test RPC endpoints
    print("\n[5/6] Testing RPC endpoints...")
    for auth, auth_desc in auth_methods:
        success, _ = test_endpoint(
            f"{base_url}/RPC2",
            auth=auth,
            description=f"RPC2 endpoint ({auth_desc})"
        )
        if success:
            break

    # Test web interface endpoints
    print("\n[6/6] Testing web interface...")
    web_endpoints = [
        "/doc/page/login.asp",
        "/web/",
        "/index.html",
        "/login.htm",
    ]
    for endpoint in web_endpoints:
        success, _ = test_endpoint(
            f"{base_url}{endpoint}",
            description=f"Web UI: {endpoint}"
        )
        if success:
            print(f"    → Web interface found at: {base_url}{endpoint}")
            break

    print("\n" + "=" * 70)
    print("NEXT STEPS")
    print("=" * 70)
    print("""
If snapshot.cgi worked but loadfile.cgi didn't:
  → Try accessing NVR web UI in browser: http://192.168.0.165/
  → Look for "Playback" or "Download" section
  → Check Network settings for API/CGI enable options

If authentication failed (401 errors):
  → Add your NVR password to NVR_PASS variable
  → Check if web login uses different credentials

If nothing worked:
  → Try different HTTP port (80, 8080, 443, 37777)
  → NVR may use proprietary protocol (check GW Security software)
""")
    print("=" * 70)


if __name__ == "__main__":
    main()
