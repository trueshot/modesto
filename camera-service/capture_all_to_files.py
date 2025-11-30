#!/usr/bin/env python3
"""
Capture all cameras to individual image files
Helps identify which camera corresponds to which channel/location
"""

import requests
import os
from pathlib import Path
from datetime import datetime

# API base URL
BASE_URL = "http://localhost:8001"

def capture_all_cameras_to_files(facility="lodge", output_dir="camera_captures"):
    """Capture all cameras and save to individual files"""

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    # Add timestamp subdirectory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = output_path / timestamp
    session_dir.mkdir(exist_ok=True)

    print("="*70)
    print("Camera Capture - Save All to Files")
    print("="*70)
    print(f"Facility: {facility}")
    print(f"Output:   {session_dir.absolute()}")
    print("="*70)

    # Get camera list
    print("\nFetching camera list...")
    response = requests.get(f"{BASE_URL}/api/cameras/{facility}")

    if response.status_code != 200:
        print(f"✗ Error getting camera list: {response.status_code}")
        return

    cameras_data = response.json()
    cameras = cameras_data['cameras']
    total = len(cameras)

    print(f"Found {total} cameras")
    print("\nCapturing images...\n")

    successful = 0
    failed = 0

    for idx, camera in enumerate(cameras, 1):
        camera_id = camera['id']
        camera_name = camera['name']
        channel = camera['channel']
        resolution = camera['resolution']

        # Create filename
        filename = f"ch{channel:02d}_{camera_id}_{camera_name.replace(' ', '_')}.jpg"
        filepath = session_dir / filename

        print(f"[{idx}/{total}] Ch{channel:02d}: {camera_name:20s} ({camera_id:15s}) {resolution:12s} ... ", end="", flush=True)

        try:
            # Capture image
            img_response = requests.get(
                f"{BASE_URL}/api/cameras/{facility}/{camera_id}/capture",
                params={"format": "image", "refresh_cache": True},
                timeout=10
            )

            if img_response.status_code == 200:
                # Save to file
                with open(filepath, 'wb') as f:
                    f.write(img_response.content)

                file_size = len(img_response.content) / 1024  # KB
                print(f"OK ({file_size:.1f} KB)")
                successful += 1
            else:
                print(f"FAIL HTTP {img_response.status_code}")
                failed += 1

        except Exception as e:
            print(f"FAIL {str(e)}")
            failed += 1

    # Summary
    print("\n" + "="*70)
    print("Capture Complete!")
    print("="*70)
    print(f"Total cameras:  {total}")
    print(f"Successful:     {successful}")
    print(f"Failed:         {failed}")
    print(f"\nImages saved to: {session_dir.absolute()}")
    print("="*70)

    # Create index HTML for easy viewing
    create_html_index(session_dir, cameras, facility)
    print(f"\nView in browser: file:///{session_dir.absolute()}/index.html")
    print("="*70)

def create_html_index(output_dir, cameras, facility):
    """Create an HTML index page to view all cameras"""

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Camera Capture - {facility}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            background: #f5f5f5;
        }}
        h1 {{
            color: #333;
        }}
        .info {{
            background: white;
            padding: 15px;
            margin-bottom: 20px;
            border-radius: 5px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 20px;
        }}
        .camera {{
            background: white;
            padding: 15px;
            border-radius: 5px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .camera img {{
            width: 100%;
            height: auto;
            border: 1px solid #ddd;
            border-radius: 3px;
        }}
        .camera h3 {{
            margin: 10px 0 5px 0;
            color: #333;
        }}
        .camera .details {{
            font-size: 12px;
            color: #666;
            margin: 5px 0;
        }}
        .needs-config {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
        }}
        .configured {{
            background: #d4edda;
            border-left: 4px solid #28a745;
        }}
    </style>
</head>
<body>
    <h1>Camera Capture - {facility.title()}</h1>
    <div class="info">
        <strong>Capture Time:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}<br>
        <strong>Total Cameras:</strong> {len(cameras)}<br>
        <strong>Instructions:</strong> Review each camera image to identify its location, then update the config.json file with proper names and locations.
    </div>

    <div class="grid">
"""

    for camera in cameras:
        camera_id = camera['id']
        camera_name = camera['name']
        channel = camera['channel']
        resolution = camera['resolution']
        location = camera['location']

        # Determine if needs configuration
        needs_config = "needs configuration" in location.lower()
        css_class = "needs-config" if needs_config else "configured"

        filename = f"ch{channel:02d}_{camera_id}_{camera_name.replace(' ', '_')}.jpg"

        html += f"""
        <div class="camera {css_class}">
            <h3>Channel {channel}: {camera_name}</h3>
            <div class="details">
                <strong>ID:</strong> {camera_id}<br>
                <strong>Resolution:</strong> {resolution}<br>
                <strong>Location:</strong> {location}
            </div>
            <img src="{filename}" alt="Camera {channel}">
        </div>
"""

    html += """
    </div>
</body>
</html>
"""

    # Write HTML file
    html_path = output_dir / "index.html"
    with open(html_path, 'w') as f:
        f.write(html)

if __name__ == "__main__":
    capture_all_cameras_to_files("lodge")
