#!/usr/bin/env python3
"""
GW Security NVR Historical Access via HTTP CGI (Dahua-based)
- Authenticates with Digest (primary) or Basic (fallback)
- Tests live snapshot
- Searches recordings and grabs historical frame

Usage: python historical_nvr_access.py [channel] [hours_ago]
Example: python historical_nvr_access.py 1 1  # Channel 1, 1 hour ago
"""

import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
import sys
from datetime import datetime, timedelta
import os

NVR_IP = "192.168.0.165"
USERNAME = "admin"
PASSWORD = ""  # Empty as per your setup
UTC_OFFSET = 5  # EST = 5, CST = 6, PST = 8


def get_session():
    """Login and return authenticated session (tries Digest, then Basic)"""
    session = requests.Session()

    # Try Digest Auth first (standard for Dahua)
    print("  Trying Digest Auth...")
    session.auth = HTTPDigestAuth(USERNAME, PASSWORD)
    try:
        resp = session.get(
            f"http://{NVR_IP}/cgi-bin/configManager.cgi?action=getConfig&name=Network",
            timeout=10
        )
        if resp.status_code == 200 and len(resp.content) > 400:
            print("  ✅ Digest Auth works")
            return session, "digest"
    except Exception as e:
        print(f"  Digest error: {e}")

    # Try Basic Auth
    print("  Trying Basic Auth...")
    session.auth = HTTPBasicAuth(USERNAME, PASSWORD)
    try:
        resp = session.get(
            f"http://{NVR_IP}/cgi-bin/configManager.cgi?action=getConfig&name=Network",
            timeout=10
        )
        if resp.status_code == 200 and len(resp.content) > 400:
            print("  ✅ Basic Auth works")
            return session, "basic"
    except Exception as e:
        print(f"  Basic error: {e}")

    # Try no auth (anonymous)
    print("  Trying No Auth...")
    session.auth = None
    try:
        resp = session.get(
            f"http://{NVR_IP}/cgi-bin/configManager.cgi?action=getConfig&name=Network",
            timeout=10
        )
        if resp.status_code == 200 and len(resp.content) > 400:
            print("  ✅ Anonymous access works")
            return session, "none"
    except Exception as e:
        print(f"  No auth error: {e}")

    print("  ❌ All auth methods failed")
    return None, None


def test_live_snapshot(session, channel):
    """Grab live JPEG from channel (0-based internally)"""
    print(f"\n[Live Snapshot] Channel {channel+1}...")

    url = f"http://{NVR_IP}/cgi-bin/snapshot.cgi?channel={channel}"
    try:
        resp = session.get(url, timeout=15)
        print(f"  Status: {resp.status_code}, Size: {len(resp.content)} bytes")

        if resp.status_code == 200 and len(resp.content) > 1000:
            # Check if it's actually JPEG
            if resp.content[:3] == b'\xff\xd8\xff':
                filename = f"live_ch{channel+1}.jpg"
                with open(filename, "wb") as f:
                    f.write(resp.content)
                print(f"  ✅ Saved: {filename} ({len(resp.content)/1024:.1f} KB)")
                return True
            else:
                print(f"  ⚠️ Not JPEG data: {resp.content[:50]}")
        else:
            print(f"  ❌ Failed: {resp.text[:200]}")
    except Exception as e:
        print(f"  ❌ Error: {e}")

    return False


def search_recordings(session, channel, start_time, end_time):
    """Search for recordings using recordFinder.cgi and mediaFileFind.cgi"""
    print(f"\n[Search Recordings]")
    print(f"  Channel: {channel+1}")
    print(f"  Range: {start_time} to {end_time}")

    # URL encode the times (space -> %20)
    start_enc = start_time.replace(" ", "%20")
    end_enc = end_time.replace(" ", "%20")

    # Try different search endpoints
    search_urls = [
        # recordFinder.cgi variants
        f"http://{NVR_IP}/cgi-bin/recordFinder.cgi?action=factory.create",
        f"http://{NVR_IP}/cgi-bin/mediaFileFind.cgi?action=factory.create",
    ]

    for create_url in search_urls:
        print(f"\n  Trying: {create_url.split('/')[-1].split('?')[0]}...")

        try:
            # Step 1: Create finder
            resp = session.get(create_url, timeout=10)
            print(f"    Create: {resp.status_code}")

            if resp.status_code == 200 and "result=0" not in resp.text.lower():
                # Extract object ID if present
                object_id = 0
                if "object=" in resp.text:
                    import re
                    match = re.search(r'object=(\d+)', resp.text)
                    if match:
                        object_id = int(match.group(1))

                # Step 2: Find files
                find_url = create_url.replace("factory.create", "findFile")
                find_url += f"&object={object_id}"
                find_url += f"&condition.Channel={channel}"
                find_url += f"&condition.StartTime={start_enc}"
                find_url += f"&condition.EndTime={end_enc}"
                find_url += "&condition.Types[0]=dav&condition.Types[1]=mp4"

                resp = session.get(find_url, timeout=15)
                print(f"    Find: {resp.status_code}, {len(resp.content)} bytes")

                if resp.status_code == 200:
                    text = resp.text
                    if "FilePath" in text or "found" in text.lower():
                        print(f"    ✅ Found recordings!")
                        print(f"    {text[:500]}")
                        return text

                    print(f"    Response: {text[:300]}")

        except Exception as e:
            print(f"    Error: {e}")

    return None


def get_historical_snapshot(session, channel, timestamp):
    """Try to get snapshot at specific historical time"""
    print(f"\n[Historical Snapshot] {timestamp}...")

    timestamp_enc = timestamp.replace(" ", "%20")

    # Try various historical snapshot methods
    methods = [
        # Method 1: mediaFileFind getSnapshot
        f"http://{NVR_IP}/cgi-bin/mediaFileFind.cgi?action=getSnapshot&channel={channel}&time={timestamp_enc}",

        # Method 2: snapshot.cgi with time parameter
        f"http://{NVR_IP}/cgi-bin/snapshot.cgi?channel={channel}&time={timestamp_enc}",

        # Method 3: playback.cgi snapshot
        f"http://{NVR_IP}/cgi-bin/playBack.cgi?action=getSnapshot&channel={channel}&time={timestamp_enc}",
    ]

    for url in methods:
        method_name = url.split("/")[-1].split("?")[0]
        print(f"\n  Trying {method_name}...")

        try:
            resp = session.get(url, timeout=15)
            print(f"    Status: {resp.status_code}, Size: {len(resp.content)} bytes")

            if resp.status_code == 200 and len(resp.content) > 1000:
                # Check if JPEG
                if resp.content[:3] == b'\xff\xd8\xff':
                    filename = f"historical_ch{channel+1}_{timestamp.replace(':', '').replace(' ', '_')}.jpg"
                    with open(filename, "wb") as f:
                        f.write(resp.content)
                    print(f"    ✅ Saved: {filename}")
                    return True
                else:
                    print(f"    Not JPEG: {resp.content[:50]}")
            else:
                print(f"    Response: {resp.text[:200]}")

        except Exception as e:
            print(f"    Error: {e}")

    return False


def download_by_time(session, channel, start_time, end_time):
    """Download recording segment via loadfile.cgi"""
    print(f"\n[Download by Time] {start_time} to {end_time}...")

    start_enc = start_time.replace(" ", "%20")
    end_enc = end_time.replace(" ", "%20")

    url = (f"http://{NVR_IP}/cgi-bin/loadfile.cgi"
           f"?action=startLoad&channel={channel}"
           f"&startTime={start_enc}&endTime={end_enc}&subtype=0")

    print(f"  URL: {url[:80]}...")

    try:
        resp = session.get(url, timeout=30, stream=True)
        print(f"  Status: {resp.status_code}")
        print(f"  Content-Type: {resp.headers.get('Content-Type', 'N/A')}")

        if resp.status_code == 200:
            data = b""
            for chunk in resp.iter_content(chunk_size=8192):
                data += chunk
                if len(data) > 500000:
                    break
            resp.close()

            if len(data) > 1000:
                print(f"  ✅ Downloaded {len(data)} bytes")

                # Try to extract frame
                import cv2
                temp_file = "temp_download.dav"
                with open(temp_file, "wb") as f:
                    f.write(data)

                cap = cv2.VideoCapture(temp_file)
                ret, frame = cap.read()
                cap.release()

                if ret and frame is not None:
                    filename = f"historical_ch{channel+1}_{start_time.replace(':', '').replace(' ', '_')}.jpg"
                    cv2.imwrite(filename, frame)
                    os.remove(temp_file)
                    print(f"  ✅ Extracted frame: {filename}")
                    return True
                else:
                    print(f"  ⚠️ Couldn't decode video. Saved raw: {temp_file}")
            else:
                print(f"  Only got {len(data)} bytes: {data[:100]}")

    except Exception as e:
        print(f"  Error: {e}")

    return False


def main():
    if len(sys.argv) < 3:
        print("Usage: python historical_nvr_access.py <channel> <hours_ago> [minutes_ago]")
        print("Example: python historical_nvr_access.py 1 1")
        sys.exit(1)

    channel = int(sys.argv[1]) - 1  # Convert to 0-based
    hours_ago = float(sys.argv[2])
    minutes_ago = float(sys.argv[3]) if len(sys.argv) > 3 else 0

    # Calculate times
    now = datetime.now()
    target_local = now - timedelta(hours=hours_ago, minutes=minutes_ago)
    target_utc = target_local + timedelta(hours=UTC_OFFSET)

    print("=" * 70)
    print("GW Security NVR - Historical Access")
    print("=" * 70)
    print(f"NVR:          {NVR_IP}")
    print(f"Channel:      {channel + 1}")
    print(f"Target (local): {target_local.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target (UTC):   {target_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # Step 1: Authenticate
    print("\n[1] Authenticating...")
    session, auth_type = get_session()
    if not session:
        print("\n❌ Could not authenticate. Check credentials.")
        sys.exit(1)

    # Step 2: Test live snapshot
    print("\n[2] Testing live snapshot...")
    live_ok = test_live_snapshot(session, channel)

    # Step 3: Search recordings (use UTC time for NVR)
    print("\n[3] Searching for recordings...")
    start_time = (target_utc - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    end_time = (target_utc + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    recordings = search_recordings(session, channel, start_time, end_time)

    # Step 4: Try historical snapshot
    print("\n[4] Attempting historical snapshot...")
    timestamp = target_utc.strftime("%Y-%m-%d %H:%M:%S")
    historical_ok = get_historical_snapshot(session, channel, timestamp)

    if not historical_ok:
        # Step 5: Try download by time
        print("\n[5] Attempting time-based download...")
        end_dl = (target_utc + timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
        historical_ok = download_by_time(session, channel, timestamp, end_dl)

    # Summary
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"Auth:              {'✅ ' + auth_type if auth_type else '❌ Failed'}")
    print(f"Live Snapshot:     {'✅ Success' if live_ok else '❌ Failed'}")
    print(f"Recording Search:  {'✅ Found' if recordings else '❌ None found'}")
    print(f"Historical Frame:  {'✅ Success' if historical_ok else '❌ Failed'}")
    print("=" * 70)

    if not historical_ok:
        print("""
Next steps:
1. Check NVR web UI (http://192.168.0.165) for playback/download
2. Open browser DevTools (F12) and watch Network tab during playback
3. The NVR may require the proprietary port 9000 SDK for historical
""")

    session.close()
    return 0 if historical_ok else 1


if __name__ == "__main__":
    sys.exit(main())
