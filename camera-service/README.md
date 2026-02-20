# Camera Capture Service

FastAPI + ZMQ service for warehouse camera access. Port 8001.

## Running

```bash
cd c:\clients\modesto\camera-service
python api/server.py
```

- API docs: http://localhost:8001/docs
- Monitor UI: http://localhost:8001/monitor
- Camera viewer: http://localhost:8001/camera-viewer
- Health: http://localhost:8001/api/health

## Data Source

All camera/NVR config comes from `lodge.db` (SQLite):

```
../warehouses/lodge/lodge.db
```

Tables: `nvrs`, `channels`, `cameras`. No config.json.

## Interfaces

### REST API (port 8001)

**Health & discovery**
```
GET  /api/health                              # Service health + NVR connectivity
GET  /api/cameras/{facility}                  # List cameras (joined with channels)
GET  /api/cameras/{facility}/status           # Live status for all cameras
GET  /api/cameras/{facility}/unconfigured     # Channels without camera assignments
GET  /api/cameras/{facility}/thumbnails       # Cached thumbnails grid
GET  /api/nvrs                                # NVR list from DB
GET  /api/nvrs/{nvr_id}/channels              # Channel list for NVR
```

**Frame capture**
```
GET  /api/cameras/{facility}/{camera_id}/latest    # Cached frame (<30s, fast)
GET  /api/cameras/{facility}/{camera_id}/capture   # Live frame (always fresh)
POST /api/cameras/{facility}/batch                 # Batch capture (body: {camera_ids, use_cache})
POST /api/cameras/{facility}/capture-all           # Capture every camera
GET  /api/nvr/{nvr_id}/channel/{ch}/frame          # Raw NVR channel frame
GET  /api/nvr/{nvr_id}/channel/{ch}/info           # Channel resolution/codec info
```

**NVR scan**
```
POST /api/scan       # Scan NVR for channels (body: {nvr_ip, username, password, quick})
```

**Tag scan** (AprilTag detection across cameras)
```
POST /api/tag-scan/start              # Start scan (body: {cameras, scan_id, push_to})
GET  /api/tag-scan/{scan_id}/status   # Scan progress
POST /api/tag-scan/{scan_id}/stop     # Stop scan
```

**Cache**
```
DELETE /api/cache/{facility}/{camera_id}   # Invalidate one
DELETE /api/cache                          # Clear all
```

**Admin**
```
POST   /api/admin/restart                    # Restart service
POST   /api/admin/reload-config              # Reload lodge.db
GET    /api/admin/circuit-breaker            # View circuit breaker state
DELETE /api/admin/circuit-breaker/{nvr_ip}   # Reset breaker for NVR
GET    /api/admin/concurrency                # Current NVR concurrency limit
PUT    /api/admin/concurrency/{value}        # Set concurrency limit
GET    /api/admin/cooldown                   # Circuit breaker cooldown
PUT    /api/admin/cooldown/{value}           # Set cooldown seconds
```

**UI pages**
```
GET /monitor         # Live monitor dashboard
GET /camera-viewer   # Camera image viewer
GET /probe           # Redirects to nvr-service (port 7999)
```

### ZMQ Endpoints

**REP :5555** — Serial request/reply. One frame at a time. Simple but blocks on slow cameras.

```python
import zmq
ctx = zmq.Context()
sock = ctx.socket(zmq.REQ)
sock.connect("tcp://127.0.0.1:5555")
sock.send_json({"nvr": "nvr1", "channel": 1})
result = sock.recv_json()  # {success, image_base64, width, height, ...}
```

**ROUTER :5556** — Async with dedup and supersede. Multiple in-flight requests. If a new request arrives for the same camera while one is pending, the old one is superseded.

```python
sock = ctx.socket(zmq.DEALER)
sock.connect("tcp://127.0.0.1:5556")
sock.send_json({"nvr": "nvr1", "channel": 1, "request_id": "abc"})
# Non-blocking, can send more requests
result = sock.recv_json()  # includes request_id for correlation
```

**PUSH :5557** — Tag scan output. When a tag scan runs with `push_to: "tcp://127.0.0.1:5557"`, each frame result is pushed here for downstream consumers.

## Key Features

### Direct-to-Camera Auto-Upgrade

`resolve_capture_url()` checks if a channel has a linked camera with a direct IP. If so, it captures directly from the camera instead of routing through the NVR. Falls back to NVR path on failure.

### NvrGate (Concurrency Control)

Per-NVR semaphore limiting concurrent RTSP connections. Default 2 per NVR. Prevents overwhelming NVRs with simultaneous requests.

### Circuit Breaker

If an NVR is unreachable (TCP probe fails), it's marked down for a cooldown period. Subsequent requests skip the NVR and fail fast instead of timing out.

### Monitor Event Log

The `/monitor` UI and `/api/monitor` endpoint track every capture attempt with timing, source (NVR vs direct), resolution, and errors.

## NVRs

| NVR | IP | Credentials | RTSP Pattern |
|-----|-----|-------------|-------------|
| nvr1 | 192.168.0.165 | admin / (empty) | ch{nn}/0 |
| nvr2 | 192.168.0.75 | admin / Dad5eeeee! | unicast/c{n}/s0/live |

## Related Services

- **nvr-service** (port 7999) — Standalone NVR probe/validation tool. Run during maintenance to verify camera setup. See `../nvr-service/README.md`.
- **SAM3** (port 8000) — Visual segmentation
- **3D Viewer** (port 5173) — Digital twin with camera overlays
