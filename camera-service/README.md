# Camera Capture Service

FastAPI microservice for warehouse camera access and image delivery.

## Overview

This service provides REST API access to warehouse cameras connected through NVR systems. It handles:
- Live frame capture from NVR cameras
- In-memory image caching (30 sec TTL)
- Camera metadata from ModelT configurations
- Batch capture operations
- Health monitoring

## Architecture

```
Camera Service (Python FastAPI, port 8001)
├── Reads: warehouses/{facility}/cameras/config.json
├── Captures: RTSP streams from NVR via OpenCV
├── Caches: Recent frames in memory
└── Serves: REST API for image delivery

Consumers:
├── Web App (3D Digital Twin)
├── SAM3 (Visual Segmentation)
├── OpenCV Scripts (AprilTag Detection)
└── AI Agents (Inventory Tracking)
```

## Installation

```bash
cd c:\clients\modesto\camera-service

# Install dependencies
pip install -r requirements.txt
```

## Running the Service

```bash
# Start server on port 8001
python api/server.py
```

Server will be available at:
- API: http://localhost:8001
- Docs: http://localhost:8001/docs
- Health: http://localhost:8001/api/health

## API Endpoints

### Discovery

**POST /api/scan**
Scan NVR for available camera channels

Modes:
- `"quick": true` - Fast scan using common pattern (ch01/0, ch02/0, etc.)
- `"quick": false` - Full scan testing all NVR patterns (Hikvision, Dahua, generic)

```bash
curl -X POST http://localhost:8001/api/scan \
  -H "Content-Type: application/json" \
  -d '{
    "nvr_ip": "192.168.0.165",
    "username": "admin",
    "password": "",
    "port": 554,
    "max_channels": 16,
    "quick": true
  }'
```

Response:
```json
{
  "nvr_ip": "192.168.0.165",
  "channels_found": 14,
  "channels": [
    {
      "path": "ch01/0",
      "channel": 1,
      "width": 3072,
      "height": 2048,
      "resolution": "3072x2048",
      "url": "rtsp://admin:@192.168.0.165:554/ch01/0"
    }
  ]
}
```

### Health & Info

**GET /api/health**
```bash
curl http://localhost:8001/api/health?facility=lodge
```

**GET /api/cameras/{facility}**
List all cameras
```bash
curl http://localhost:8001/api/cameras/lodge
```

**GET /api/cameras/{facility}/{camera_id}/info**
Get camera metadata
```bash
curl http://localhost:8001/api/cameras/lodge/bagel/info
```

### Image Capture

**GET /api/cameras/{facility}/{camera_id}/latest**
Get cached frame (fast, <30s old)
```bash
# As JPEG image
curl http://localhost:8001/api/cameras/lodge/bagel/latest > bagel.jpg

# As base64 JSON
curl "http://localhost:8001/api/cameras/lodge/bagel/latest?format=base64"
```

**GET /api/cameras/{facility}/{camera_id}/capture**
Capture live frame (always fresh)
```bash
curl http://localhost:8001/api/cameras/lodge/bacon/capture > bacon.jpg
```

**POST /api/cameras/{facility}/batch**
Batch capture multiple cameras
```bash
curl -X POST http://localhost:8001/api/cameras/lodge/batch \
  -H "Content-Type: application/json" \
  -d '{
    "camera_ids": ["bagel", "bacon", "beef"],
    "use_cache": true
  }'
```

**POST /api/cameras/{facility}/capture-all**
Capture all cameras in facility
```bash
curl -X POST http://localhost:8001/api/cameras/lodge/capture-all
```

### Cache Management

**DELETE /api/cache/{facility}/{camera_id}**
Invalidate specific camera cache
```bash
curl -X DELETE http://localhost:8001/api/cache/lodge/bagel
```

**DELETE /api/cache**
Clear entire cache
```bash
curl -X DELETE http://localhost:8001/api/cache
```

## Usage Examples

### Python Agent Tool

```python
import requests
import base64
from PIL import Image
from io import BytesIO

# Get latest frame (cached)
response = requests.get('http://localhost:8001/api/cameras/lodge/bagel/latest')
image = Image.open(BytesIO(response.content))

# Get live frame
response = requests.get('http://localhost:8001/api/cameras/lodge/bagel/capture')
image = Image.open(BytesIO(response.content))

# Batch capture with base64 encoding
response = requests.post(
    'http://localhost:8001/api/cameras/lodge/batch',
    json={'camera_ids': ['bagel', 'bacon'], 'use_cache': True}
)
data = response.json()
for camera_id, result in data['results'].items():
    if result['success']:
        image_data = base64.b64decode(result['image'])
        image = Image.open(BytesIO(image_data))
```

### JavaScript/Web App

```javascript
// Get latest frame as image
const img = document.createElement('img');
img.src = 'http://localhost:8001/api/cameras/lodge/bagel/latest';
document.body.appendChild(img);

// Get frame as base64
const response = await fetch(
  'http://localhost:8001/api/cameras/lodge/bagel/latest?format=base64'
);
const data = await response.json();
const imgElement = document.createElement('img');
imgElement.src = `data:image/jpeg;base64,${data.image}`;
```

### OpenCV Script

```python
import cv2
import requests
import numpy as np

# Get frame from camera service
response = requests.get('http://localhost:8001/api/cameras/lodge/bagel/latest')

# Convert to OpenCV image
nparr = np.frombuffer(response.content, np.uint8)
image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

# Process with OpenCV
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
# ... AprilTag detection, etc.
```

## Configuration

Camera configurations are read from:
```
warehouses/{facility}/cameras/config.json
```

Example: `warehouses/lodge/cameras/config.json`

The service automatically loads camera configurations including:
- NVR connection details (IP, credentials, RTSP URLs)
- ModelT camera IDs and names
- Camera locations and metadata

## Caching Behavior

- **Default TTL:** 30 seconds
- **Strategy:** Write-through cache
- **Invalidation:** Automatic on expiry, manual via DELETE endpoints
- **Memory:** Images stored as JPEG bytes in memory

When to use:
- **`/latest`** - For dashboards, monitoring (uses cache)
- **`/capture`** - For analysis, ground truth (always fresh)
- **`/batch`** - For processing multiple cameras efficiently

## Service Management

### Check if running
```bash
curl http://localhost:8001/api/health
```

### Stop service
Press `Ctrl+C` in terminal

### Restart service
```bash
python api/server.py
```

## Integration with Other Services

### SAM3 (Port 8000)
```python
# Get image from camera service
cam_response = requests.get('http://localhost:8001/api/cameras/lodge/bagel/latest')

# Send to SAM3 for segmentation
files = {'image': cam_response.content}
sam_response = requests.post(
    'http://localhost:8000/detect',
    files=files,
    data={'prompt': 'boxes on pallet'}
)
```

### Node.js 3D Viewer (Port 5173)
```javascript
// In viewer, fetch camera image for ground truth overlay
fetch('http://localhost:8001/api/cameras/lodge/bagel/latest')
  .then(r => r.blob())
  .then(blob => {
    // Display in 3D viewer at camera position
  });
```

## Architecture Benefits

✅ **Independent** - Doesn't affect SAM3 or other services
✅ **Cacheable** - Reduces NVR load, improves response time
✅ **Tool-friendly** - Simple REST API for AI agents
✅ **Scalable** - Can run multiple instances
✅ **Observable** - Health checks, cache stats

## Troubleshooting

### Cannot connect to NVR
- Check NVR is reachable: `ping 192.168.0.165`
- Verify RTSP credentials in config.json
- Check firewall settings

### Camera not found
- Verify camera exists: `GET /api/cameras/{facility}`
- Check ModelT camera ID spelling
- Ensure config.json is loaded

### Slow responses
- Use `/latest` instead of `/capture` for cached access
- Check NVR network latency
- Consider reducing image quality in capture.py

## Development

Run with auto-reload:
```bash
uvicorn api.server:app --reload --port 8001
```

View interactive API docs:
```
http://localhost:8001/docs
```

## Next Steps

Future enhancements:
- AprilTag detection endpoint
- Image preprocessing (resize, crop, enhance)
- Streaming endpoints (MJPEG, WebRTC)
- Prometheus metrics
- Background polling mode
