# ONVIF Camera Discovery Tools

Part of the ModelT warehouse onboarding toolkit.

## Overview

These tools discover and query ONVIF-compatible cameras and NVRs on a network. Use them to:
- Find all cameras during warehouse setup
- Get camera serial numbers and MAC addresses for inventory
- Verify camera connectivity and credentials
- Track camera identity across channel reassignments

## Prerequisites

```bash
pip install onvif-zeep wsdiscovery
```

## Tools

### onvif-scan.py — Network Discovery

Discovers all ONVIF devices on the local network and retrieves their metadata.

**Usage:**
```bash
python onvif-scan.py [options]
```

**Options:**
- `--json` — Output as JSON (default: table)
- `--timeout N` — Discovery timeout in seconds (default: 10)
- `--exclude IP [IP...]` — Skip specific IPs (e.g., NVR IPs)
- `--credentials user:pass` — Add custom credentials to try
- `--quiet` — Suppress progress output

**Examples:**
```bash
# Basic scan
python onvif-scan.py

# Output as JSON
python onvif-scan.py --json > cameras.json

# Longer timeout for large networks
python onvif-scan.py --timeout 20

# Skip NVR, only scan cameras
python onvif-scan.py --exclude 192.168.0.75 192.168.0.165
```

**Sample Output:**
```
==========================================================================================
IP               MAC                  Serial             Model                     Status
==========================================================================================
192.168.0.77     00:0F:3A:04:07:6C    000F3A04076C       DWC-MT94Wi28T             ok
192.168.0.78     00:0F:3A:04:06:AF    000F3A0406AF       DWC-MT94Wi28T             ok
192.168.0.129    -                    -                  -                         error: auth_failed
==========================================================================================

Total: 28 devices, 25 successful, 3 failed
```

---

### onvif-info.py — Single Device Query

Gets detailed information from a specific ONVIF device.

**Usage:**
```bash
python onvif-info.py <ip> [options]
```

**Options:**
- `-u, --username` — ONVIF username (default: admin)
- `-p, --password` — ONVIF password (default: empty)
- `--port N` — ONVIF port (default: 80)
- `--json` — Output as JSON

**Examples:**
```bash
# Query camera with default credentials
python onvif-info.py 192.168.0.77

# Query NVR with password
python onvif-info.py 192.168.0.75 -p "MyPassword"

# JSON output
python onvif-info.py 192.168.0.75 -p "MyPassword" --json
```

**Sample Output:**
```
============================================================
ONVIF Device Information: 192.168.0.75
============================================================

  Manufacturer:  NONE
  Model:         NVR3232
  Serial:        210235X1EY321B000672
  Hardware ID:   1
  Firmware:      NVR-B3601.27.30.C19046.210819
  Hostname:      NVR3232
  MAC Address:   e4:f1:4c:45:2b:5c

  Media Profiles (5):
    - token:16/0/0/3/1/s0
    - token:17/0/10/1/10/s0
    ...

============================================================
```

---

## Retrieved Fields

| Field | Description | Use Case |
|-------|-------------|----------|
| MAC Address | Hardware address | Permanent camera identifier |
| Serial Number | Manufacturer serial | Inventory tracking |
| Model | Camera/NVR model | Hardware specs lookup |
| Manufacturer | Device brand | Firmware updates |
| Firmware | Firmware version | Security/compatibility |
| Hostname | Device hostname | Human-readable identifier |

## Common Credentials

| Device Type | Username | Password |
|-------------|----------|----------|
| DigitalWatchdog cameras | admin | Dad5eeeee! |
| GW Security NVR | admin | (empty) |
| Generic default | admin | admin |

## Troubleshooting

**"Authentication failed"**
- Try different credentials from the table above
- Check NVR web interface for camera credentials

**"Connection timeout"**
- Verify IP is reachable: `ping <ip>`
- Check if ONVIF is enabled on the device

**"Connection refused"**
- Device may not support ONVIF
- Try different ports: 80, 8000, 8080

**No devices found**
- Increase timeout: `--timeout 20`
- Check firewall rules for UDP multicast (WS-Discovery)

## Integration

These tools output JSON for integration with other systems:

```bash
# Scan and process with jq
python onvif-scan.py --json | jq '.[] | select(.status == "ok")'

# Save to file for later use
python onvif-scan.py --json > /path/to/cameras.json
```

---

Author: modeltcamerascat gen-3
