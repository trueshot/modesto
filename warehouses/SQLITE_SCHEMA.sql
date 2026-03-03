-- Lodge Warehouse Infrastructure Schema
-- Author: modestocat gen-9
-- Date: 2026-01-22
--
-- This is NOT the source of truth for what happened.
-- This reflects RELATIONSHIPS between infrastructure components.
-- Source of truth: Reality (video), Observations (Neo4j graph)

-- ============================================================================
-- SPATIAL LAYER (from ModelT)
-- ============================================================================

-- Zones are areas of the warehouse (dock, cooler, packing, staging)
CREATE TABLE zones (
    id TEXT PRIMARY KEY,              -- 'dock_east', 'cooler_1', 'packing_line_2'
    name TEXT NOT NULL,
    zone_type TEXT,                   -- 'dock', 'cooler', 'packing', 'staging', 'office'
    notes TEXT
);

-- Mounts are fixed positions where cameras go (food names)
CREATE TABLE mounts (
    id TEXT PRIMARY KEY,              -- 'bagel', 'bacon', 'burger' (food name)
    x REAL NOT NULL,                  -- ModelT X coordinate (feet)
    y REAL NOT NULL,                  -- ModelT Y coordinate (feet)
    z REAL NOT NULL,                  -- Elevation (feet)
    wall TEXT,                        -- 'mercury_perimeter_east', 'abigail'
    facing TEXT,                      -- 'north', 'south', 'east', 'west', 'northwest'
    zone_id TEXT REFERENCES zones(id),
    description TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- HARDWARE LAYER (physical devices)
-- ============================================================================

-- NVRs are recording infrastructure
CREATE TABLE nvrs (
    id TEXT PRIMARY KEY,              -- 'nvr1', 'nvr2'
    brand TEXT,                       -- 'GW Security', 'UNIVIEW'
    model TEXT,
    ip TEXT,
    mac TEXT,
    max_channels INTEGER,
    protocol TEXT DEFAULT 'rtsp',     -- 'rtsp', 'onvif'
    path_format TEXT,                 -- 'ch{channel:02d}/0'
    username TEXT,
    password TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Channels are slots on an NVR
CREATE TABLE channels (
    id TEXT PRIMARY KEY,              -- 'nvr1_ch01', 'nvr2_ch05'
    nvr_id TEXT NOT NULL REFERENCES nvrs(id),
    channel_number INTEGER NOT NULL,
    rtsp_path TEXT,                   -- computed or overridden
    status TEXT DEFAULT 'unknown',    -- 'active', 'empty', 'dead', 'unknown'
    last_probed TEXT,
    UNIQUE(nvr_id, channel_number)
);

-- Physical cameras are hardware devices (identified by MAC)
CREATE TABLE cameras (
    mac TEXT PRIMARY KEY,             -- 'F0:00:00:77:2D:8D'
    model TEXT,
    manufacturer TEXT,
    serial TEXT,
    resolution TEXT,                  -- '3072x2048', '3840x2160'
    ip TEXT,                          -- current IP (can change via DHCP)
    firmware TEXT,
    acquired_date TEXT,
    retired_date TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- LINKAGE LAYER (current wiring state)
-- ============================================================================

-- Links mount <-> camera <-> channel
-- This is the ONLY table that changes when cameras are moved/rewired
CREATE TABLE linkages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mount_id TEXT NOT NULL REFERENCES mounts(id),
    camera_mac TEXT REFERENCES cameras(mac),  -- NULL if unknown
    channel_id TEXT REFERENCES channels(id),  -- NULL if not connected to NVR
    verified_at TEXT,
    verified_by TEXT,                 -- 'novicat', 'manual', 'onvif_discovery'
    confidence TEXT DEFAULT 'assumed', -- 'verified', 'assumed', 'unverified'
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(mount_id)                  -- one linkage per mount
);

-- ============================================================================
-- FIDUCIALS (calibration reference points)
-- ============================================================================

CREATE TABLE fiducials (
    tag_id INTEGER PRIMARY KEY,       -- AprilTag ID (1, 2, 10, 20, etc.)
    family TEXT DEFAULT 'tag36h11',
    x REAL NOT NULL,                  -- ModelT X coordinate
    y REAL NOT NULL,                  -- ModelT Y coordinate
    z REAL NOT NULL,                  -- Elevation
    wall TEXT,                        -- which wall it's mounted on
    facing TEXT,                      -- 'north', 'south', etc.
    size_inches REAL DEFAULT 5.5,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Which mounts can see which fiducials
CREATE TABLE mount_fiducial_visibility (
    mount_id TEXT REFERENCES mounts(id),
    fiducial_id INTEGER REFERENCES fiducials(tag_id),
    expected INTEGER DEFAULT 1,       -- 1 = should be visible, 0 = maybe
    PRIMARY KEY (mount_id, fiducial_id)
);

-- ============================================================================
-- HISTORY (optional - track changes over time)
-- ============================================================================

CREATE TABLE linkage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mount_id TEXT NOT NULL,
    camera_mac TEXT,
    channel_id TEXT,
    action TEXT,                      -- 'installed', 'removed', 'rewired'
    changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    changed_by TEXT,
    reason TEXT
);

-- ============================================================================
-- VIEWS (computed for convenience)
-- ============================================================================

-- Full camera view (what the API returns)
CREATE VIEW camera_full AS
SELECT
    m.id AS mount_id,
    m.x, m.y, m.z,
    m.wall, m.facing,
    m.zone_id,
    m.description,
    c.mac AS camera_mac,
    c.model AS camera_model,
    c.resolution,
    c.ip AS camera_ip,
    ch.id AS channel_id,
    ch.nvr_id,
    ch.channel_number,
    n.ip AS nvr_ip,
    n.protocol,
    n.path_format,
    n.username,
    n.password,
    l.verified_at,
    l.confidence
FROM mounts m
LEFT JOIN linkages l ON m.id = l.mount_id
LEFT JOIN cameras c ON l.camera_mac = c.mac
LEFT JOIN channels ch ON l.channel_id = ch.id
LEFT JOIN nvrs n ON ch.nvr_id = n.id;

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX idx_mounts_zone ON mounts(zone_id);
CREATE INDEX idx_channels_nvr ON channels(nvr_id);
CREATE INDEX idx_linkages_camera ON linkages(camera_mac);
CREATE INDEX idx_linkages_channel ON linkages(channel_id);
CREATE INDEX idx_fiducials_wall ON fiducials(wall);
