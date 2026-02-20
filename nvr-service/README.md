# NVR Probe Service

Standalone NVR channel probing and infrastructure verification. Port 7999.

Run during maintenance to validate camera/NVR setup. Updates `lodge.db` with results.

## What It Probes

For each channel on an NVR, the probe tests:

1. **NVR-path RTSP** — connects through the NVR using `build_rtsp_url()` (the path camera-service uses by default)
2. **Direct-camera RTSP** — connects directly to the camera IP using credentials from `.env` and `rtsp_path` from the `cameras` table

This catches mismatches where one path works but the other doesn't (wrong credentials, wrong `rtsp_path` in DB, camera IP changed, etc).

## Running

```bash
cd c:\clients\modesto\nvr-service
python server.py
```

- Probe UI: http://localhost:7999/probe
- API docs: http://localhost:7999/docs

## Endpoints

```
GET  /api/nvrs                              # List NVRs
GET  /api/nvrs/{nvr_id}/channels            # List channels for NVR
POST /api/nvr/{nvr_id}/probe                # Start probe (body: {attempts: 3})
GET  /api/nvr/{nvr_id}/probe/{probe_id}     # Probe status + results
POST /api/nvr/{nvr_id}/probe/{probe_id}/stop  # Stop early
GET  /probe                                 # Probe UI
```

### Start a probe

```bash
curl -X POST http://localhost:7999/api/nvr/nvr1/probe \
  -H "Content-Type: application/json" \
  -d '{"attempts": 3}'
```

### Check status

```bash
curl http://localhost:7999/api/nvr/nvr1/probe/{probe_id}
```

## Per-Channel Results

Each channel reports:

| Field | Description |
|-------|-------------|
| `nvr_verdict` | active/inactive via NVR path |
| `direct_verdict` | active/inactive via direct camera IP |
| `verdict` | combined (active if either works) |
| `mismatch` | `nvr_only` or `direct_only` if paths disagree |
| `direct_ip` | Camera IP (from `cameras` table) |
| `direct_model` | Camera model |
| `thumbnail` | Base64 JPEG from first successful capture |

## DB Updates

On completion, the probe:
- Updates `channels.status` (active/inactive) and `channels.last_probed`
- Inserts new channel records if discovered
- Runs `node warehouses/snapshot-database.js lodge` to persist changes

## Configuration

- **lodge.db:** `../warehouses/lodge/lodge.db` (NVR info, channels, cameras)
- **Credentials:** `../.env` (CAM_* groups for direct camera access)
- **Workers:** 4 concurrent subprocesses (no NVR gate — runs during maintenance)
- **Timeout:** 10s per RTSP attempt, up to 5 frames read per attempt

## Relationship to Camera Service

This was extracted from camera-service (port 8001). It runs independently and does not communicate with camera-service. Run it when camera-service is idle to avoid competing for NVR connections.
