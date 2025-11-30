#!/usr/bin/env python
"""
Explore GW Security NVR Web Interface
Finds all available pages and API endpoints.

Usage: python explore_nvr_web.py
"""

import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
import re

NVR_IP = "192.168.0.165"
NVR_USER = "admin"
NVR_PASS = ""
NVR_PORT = 80

# Common NVR web paths to check
WEB_PATHS = [
    # Main pages
    "/",
    "/index.html",
    "/index.htm",
    "/login.html",
    "/login.htm",
    "/web/",
    "/doc/",
    "/doc/page/login.asp",

    # Playback/Recording pages
    "/playback.html",
    "/playback.htm",
    "/record.html",
    "/download.html",
    "/web/playback.html",
    "/doc/page/playback.asp",

    # API endpoints
    "/cgi-bin/",
    "/cgi-bin/configManager.cgi",
    "/cgi-bin/devVideoInput.cgi",
    "/cgi-bin/recordFinder.cgi",
    "/cgi-bin/mediaFileFind.cgi",
    "/cgi-bin/snapshot.cgi",
    "/RPC2",
    "/RPC2_Login",

    # Device info
    "/device.rsp",
    "/device/info",
    "/api/device/info",
    "/ISAPI/System/deviceInfo",

    # GW Security specific
    "/gw/",
    "/sdk/",
    "/NetSDK/",

    # Static resources (might reveal structure)
    "/js/",
    "/css/",
    "/scripts/",
    "/web/js/",
]


def check_path(base_url, path, auth=None):
    """Check if a path exists and what it returns"""
    url = f"{base_url}{path}"
    try:
        resp = requests.get(url, auth=auth, timeout=5, allow_redirects=False)
        return {
            "status": resp.status_code,
            "length": len(resp.content),
            "content_type": resp.headers.get("Content-Type", ""),
            "redirect": resp.headers.get("Location", ""),
            "snippet": resp.text[:300] if resp.text else ""
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def extract_links(html):
    """Extract links and script sources from HTML"""
    links = set()

    # href links
    for match in re.findall(r'href=["\']([^"\']+)["\']', html):
        if not match.startswith(('http://', 'https://', 'javascript:', '#')):
            links.add(match)

    # src links
    for match in re.findall(r'src=["\']([^"\']+)["\']', html):
        if not match.startswith(('http://', 'https://')):
            links.add(match)

    # action links
    for match in re.findall(r'action=["\']([^"\']+)["\']', html):
        links.add(match)

    return links


def main():
    base_url = f"http://{NVR_IP}:{NVR_PORT}"

    print("=" * 70)
    print(f"GW Security NVR Web Explorer - {base_url}")
    print("=" * 70)

    # Try different auth methods
    auth_methods = [
        (None, "No auth"),
        (HTTPDigestAuth(NVR_USER, NVR_PASS), "Digest"),
        (HTTPBasicAuth(NVR_USER, NVR_PASS), "Basic"),
    ]

    # Find working auth
    working_auth = None
    print("\n[1] Finding authentication method...")
    for auth, name in auth_methods:
        result = check_path(base_url, "/", auth)
        if result.get("status") == 200:
            print(f"  ✅ {name} auth works")
            working_auth = auth
            break
        elif result.get("status") == 401:
            print(f"  ❌ {name} - needs auth")
        else:
            print(f"  ? {name} - status {result.get('status')}")

    print(f"\n[2] Scanning {len(WEB_PATHS)} known paths...")

    found_pages = []
    found_apis = []
    all_links = set()

    for path in WEB_PATHS:
        result = check_path(base_url, path, working_auth)

        if result.get("status") == 200:
            content_type = result.get("content_type", "")
            length = result.get("length", 0)

            if "html" in content_type or path.endswith((".html", ".htm", ".asp")):
                found_pages.append((path, length))
                # Extract links from HTML
                links = extract_links(result.get("snippet", ""))
                all_links.update(links)
                print(f"  ✅ {path:40} [HTML, {length} bytes]")
            elif length > 0:
                found_apis.append((path, content_type, length))
                print(f"  ✅ {path:40} [{content_type}, {length} bytes]")

        elif result.get("status") == 302:
            redirect = result.get("redirect", "")
            print(f"  → {path:40} [Redirect to {redirect}]")

    # Check discovered links
    print(f"\n[3] Checking {len(all_links)} discovered links...")
    for link in sorted(all_links)[:20]:  # Limit to first 20
        if link.startswith("/"):
            result = check_path(base_url, link, working_auth)
            if result.get("status") == 200:
                print(f"  ✅ {link}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(f"\nFound {len(found_pages)} HTML pages:")
    for path, length in found_pages:
        print(f"  • {base_url}{path}")

    print(f"\nFound {len(found_apis)} API/data endpoints:")
    for path, ctype, length in found_apis:
        print(f"  • {base_url}{path} ({ctype})")

    print(f"\nDiscovered links from HTML:")
    for link in sorted(all_links)[:15]:
        print(f"  • {link}")

    print("\n" + "=" * 70)
    print("NEXT: Open web UI in browser and check Network tab during playback")
    print(f"      {base_url}/")
    print("=" * 70)


if __name__ == "__main__":
    main()
