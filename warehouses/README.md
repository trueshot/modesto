# Warehouse Specifications

ModelT warehouse facility definitions. Each warehouse is a folder containing
the facility spec, database, and generated outputs.

## Structure

```
warehouses/
  SQLITE_SCHEMA.sql       # Shared schema (all warehouses use this)
  SCHEMA_PROPOSAL.md      # Design rationale
  snapshot-database.js    # Dump .db to diffable .sql
  <name>/
    <name>.modelT.json    # Warehouse geometry (source of truth for space)
    <name>.db             # Infrastructure inventory (source of truth for things)
    <name>-snapshot.sql   # Git-friendly text dump of the .db
    build-database.js     # Initial bootstrap (warehouse-specific)
    fiducial-placement.json
```

## Adding a New Warehouse

1. Create a folder with the warehouse name (e.g., `denver/`, `atlanta/`)
2. Add `<name>.modelT.json` with ModelT specification
3. Generate SVG: `node ../modelt/scripts/generate-svg.js <name>.modelT.json <name>.svg`
4. Create `build-database.js` to seed `<name>.db` from SQLITE_SCHEMA.sql
5. Snapshot for git: `node ../snapshot-database.js <name>`

## Database

**File:** `<name>.db` (SQLite, gitignored)
**Diffable snapshot:** `<name>-snapshot.sql` (committed)

The database is the source of truth for infrastructure inventory -- the
relationships between mounts, cameras, NVRs, and channels.

It is NOT the source of truth for:
- What happened (that's Neo4j / observations)
- Warehouse geometry (that's <name>.modelT.json)
- Raw video (that's the NVR)

### First Principles

  Entity                 Identity                 Stable?
  ---------------------  -----------------------  -------
  Mount (food name)      Position in warehouse    Yes
  Physical Camera        MAC address              Yes
  NVR + Channel          Connection path          No -- changes when rewired

Key insight: "bagel" is WHERE a camera goes, not WHICH camera it is.
If the physical camera dies, you swap it out. Still bagel.

### Schema

  +-----------------------------+
  |       SPATIAL LAYER         |
  |  zones <-- mounts           |
  |  (food names = positions)   |
  +-------------+---------------+
                |
            mount_id
                |
  +-------------+---------------+
  |       LINKAGE LAYER         |
  |  mount <-> mac <-> channel  |
  |  ** only part that changes  |
  |     when rewired **         |
  +------+-------------+-------+
         |             |
     camera_mac    channel_id
         |             |
  +------+------+ +----+------------+
  |  HARDWARE   | | INFRASTRUCTURE  |
  |  cameras    | | channels <-- nvrs|
  |  (by MAC)   | |                 |
  +-------------+ +-----------------+

### Tables

  Table                       Description
  --------------------------  -------------------------------------------
  zones                       Areas: packing_line_1, main_floor, etc.
  nvrs                        NVR devices (brand, IP, credentials)
  channels                    NVR channel slots (nvr1_ch01, etc.)
  cameras                     Physical cameras with known MAC
  mounts                      Food names = fixed camera positions
  linkages                    Current wiring: mount <-> MAC <-> channel
  fiducials                   AprilTags for camera calibration
  mount_fiducial_visibility   Which mounts can see which tags

### Views

- camera_full -- JOINs everything into flat rows for API responses

### Querying

```javascript
const Database = require('better-sqlite3');
const db = new Database('warehouses/lodge/lodge.db');

// Get all cameras with full details
const cameras = db.prepare('SELECT * FROM camera_full').all();

// Get cameras in a zone
const packingCameras = db.prepare(`
  SELECT * FROM camera_full WHERE zone_id = 'packing_line_2'
`).all();

// Get verified vs assumed linkages
const verified = db.prepare(`
  SELECT mount_id, camera_mac FROM linkages WHERE confidence = 'verified'
`).all();
```

### Snapshots

Before committing, dump the database to a diffable text file:

```bash
node warehouses/snapshot-database.js lodge
```

This writes `lodge/lodge-snapshot.sql` -- plain SQL that git can diff.

### Update Scenarios

Camera dies at mount "bagel":
```sql
UPDATE linkages
SET camera_mac = 'AA:BB:CC:DD:EE:FF',
    verified_at = datetime('now'),
    verified_by = 'manual',
    confidence = 'verified'
WHERE mount_id = 'bagel';
```

Channel rewired:
```sql
UPDATE linkages SET channel_id = 'nvr1_ch15' WHERE mount_id = 'bacon';
```

New mount installed:
```sql
INSERT INTO mounts (id, x, y, z, wall, facing, zone_id)
VALUES ('waffle', 200, 250, 12, 'mercury_perimeter_east', 'west', 'main_floor');

INSERT INTO linkages (mount_id, camera_mac, channel_id, confidence)
VALUES ('waffle', 'NEW:MAC:HERE', 'nvr2_ch01', 'verified');
```

### Linkage Confidence

- **verified** -- MAC confirmed via ONVIF discovery or physical inspection
- **assumed** -- Channel works but physical camera identity unknown
- **unverified** -- No confirmation at all
