# Lodge Warehouse Data Schema Proposal
# Author: modestocat gen-9
# Date: 2026-01-22
# Based on: First principles discussion from gen-5

## First Principles

| Layer | Example | Stable? |
|-------|---------|---------|
| **Food name (Mount)** | bagel | ✓ Yes - position in warehouse |
| **Physical camera** | MAC/serial | ✓ Yes - hardware identity |
| **NVR + channel** | nvr1_ch07 | ✗ No - rewiring changes this |
| **IP address** | 192.168.0.77 | ✗ No - DHCP |

**Key insight:** Food name = Mount position (fixed in warehouse). The food name isn't the camera — it's WHERE the camera goes.

---

## Entity Definitions

### 1. Mounts (stable - rarely changes)

A mount is a fixed position in the warehouse where a camera can be installed.

```yaml
# mounts.yaml
mounts:
  bagel:
    position: { x: 185, y: 350, z: 12 }  # ModelT coords (feet)
    wall: mercury_perimeter_east
    facing: west
    zone: packing_line_2
    description: "Packing Line 2, East wall, 50ft from south"

  bacon:
    position: { x: 185, y: 320, z: 12 }
    wall: mercury_perimeter_east
    facing: northwest
    zone: packing_line_2
    description: "Packing Line 2, East wall, 50ft from south"
```

**Source of truth for:** Where cameras go in the warehouse
**Changes when:** New mount installed, mount relocated (rare)

---

### 2. Physical Cameras (stable - hardware identity)

A physical camera is a piece of hardware with a permanent MAC address.

```yaml
# cameras.yaml
cameras:
  - mac: "F0:00:00:77:2D:8D"
    model: YM600F_AF
    serial: null  # if discoverable
    resolution: 3072x2048
    acquired: 2025-08-15
    notes: "GW Security dome camera"

  - mac: "F0:00:00:77:2E:EB"
    model: YM600F_AF
    resolution: 3072x2048

  - mac: "f4:00:00:01:a8:bb"
    model: N802-IRC-GW
    resolution: 3840x2160
```

**Source of truth for:** What hardware we own
**Changes when:** Camera purchased, retired, or RMA'd

---

### 3. NVRs (stable - infrastructure)

```yaml
# nvrs.yaml
nvrs:
  nvr1:
    brand: GW Security
    ip: 192.168.0.165
    mac: "00:23:63:6e:bc:7f"
    channels: 32
    protocol: rtsp
    pathFormat: "ch{channel:02d}/0"
    credentials:
      username: admin
      password: ""

  nvr2:
    brand: UNIVIEW
    model: XVR302-16Q3
    ip: 192.168.0.134
    mac: "c4:79:05:e4:f5:0f"
    channels: 24
    protocol: rtsp
    pathFormat: "unicast/c{channel}/s0/live"
    credentials:
      username: admin
      password: "Dad5eeeee!"
```

**Source of truth for:** Recording infrastructure
**Changes when:** NVR added/replaced

---

### 4. Linkages (dynamic - the mapping)

This is the ONLY file that changes when cameras are moved/rewired.

```yaml
# linkages.yaml
# Maps: Mount <-> Physical Camera <-> NVR Channel
# Updated when: camera swapped, channel rewired, new camera installed

linkages:
  - mount: bagel
    camera_mac: "F0:00:00:77:2D:8D"
    nvr: nvr1
    channel: 1
    verified: 2026-01-17
    verified_by: novicat

  - mount: bacon
    camera_mac: "F0:00:00:77:2E:EB"
    nvr: nvr1
    channel: 2
    verified: 2026-01-17
    verified_by: novicat

  - mount: burger
    camera_mac: "f4:00:00:01:a8:bb"
    nvr: nvr1
    channel: 3
    verified: 2026-01-17
    verified_by: novicat

  # Unverified linkages (MAC unknown)
  - mount: biscuit
    camera_mac: null  # ONVIF didn't find it
    nvr: nvr1
    channel: 7
    verified: null
    notes: "Channel works but camera not discovered via ONVIF"
```

**Source of truth for:** Current wiring state
**Changes when:** Camera swapped, channel rewired, physical camera moved

---

## Update Scenarios

### Scenario 1: Physical camera dies at mount "bagel"

1. Technician swaps hardware
2. Update `linkages.yaml`:
   - Change `camera_mac` from old MAC to new MAC
   - Update `verified` timestamp
3. Mount stays "bagel" — position unchanged
4. Old camera entry stays in `cameras.yaml` (marked retired)

### Scenario 2: Camera moved from channel 7 to channel 12

1. Update `linkages.yaml`:
   - Find entry with `channel: 7`
   - Change to `channel: 12`
2. Mounts unchanged
3. Physical cameras unchanged

### Scenario 3: New camera installed at new mount "waffle"

1. Add mount to `mounts.yaml` (position, wall, zone)
2. Add camera to `cameras.yaml` (MAC, model)
3. Add linkage to `linkages.yaml` (mount, mac, nvr, channel)

---

## Derived Views (computed, not stored)

For tools that need the flat view:

```yaml
# COMPUTED: full_camera_view (don't store this)
bagel:
  # From mount
  position: { x: 185, y: 350, z: 12 }
  wall: mercury_perimeter_east
  zone: packing_line_2
  # From physical camera (via linkage)
  mac: "F0:00:00:77:2D:8D"
  model: YM600F_AF
  resolution: 3072x2048
  # From NVR + linkage
  rtspUrl: "rtsp://admin:@192.168.0.165:554/ch01/0"
  # Computed
  verified: true
```

Tools can JOIN these tables at runtime. The source files stay normalized.

---

## Migration Path

Current state: `combined.yaml` has everything flat

1. Extract mounts from `combined.yaml` → `mounts.yaml`
2. Extract physical cameras (where MAC known) → `cameras.yaml`
3. Keep NVRs as-is → `nvrs.yaml`
4. Build linkages from current mappings → `linkages.yaml`
5. Mark unverified linkages (8 cameras with `mac: null`)

---

## File Locations

```
warehouses/lodge/
├── mounts.yaml           # Fixed positions (food names)
├── cameras.yaml          # Physical hardware (MACs)
├── nvrs.yaml             # Recording infrastructure
├── linkages.yaml         # Current wiring (the mapping)
├── fiducial-placement.json  # Calibration reference (existing)
└── lodge.modelT.json     # Warehouse geometry (existing)
```

---

## Questions for George

1. Should `cameras.yaml` include cameras not yet installed (inventory)?
2. Should linkages include a `status` field (active, disconnected, testing)?
3. Should we version linkages.yaml or keep history of changes?
4. Is YAML the right format, or should this be JSON for tool compatibility?
