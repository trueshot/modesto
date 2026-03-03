#!/usr/bin/env node
/**
 * Lodge Warehouse Database Builder
 *
 * Creates and populates lodge.db from:
 * - SQLITE_SCHEMA.sql (schema)
 * - combined.yaml (NVRs, cameras)
 * - fiducial-placement.json (calibration fiducials)
 *
 * Author: modestocat gen-9
 * Date: 2026-01-22
 *
 * Usage: node build-database.js
 */

const Database = require('better-sqlite3');
const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');

const WAREHOUSE_DIR = __dirname;
const DB_PATH = path.join(WAREHOUSE_DIR, 'lodge.db');
const SCHEMA_PATH = path.join(WAREHOUSE_DIR, 'SQLITE_SCHEMA.sql');
const COMBINED_YAML_PATH = path.join(WAREHOUSE_DIR, 'combined.yaml');
const FIDUCIALS_PATH = path.join(WAREHOUSE_DIR, 'fiducial-placement.json');

// Delete existing database
if (fs.existsSync(DB_PATH)) {
  fs.unlinkSync(DB_PATH);
  console.log('Deleted existing lodge.db');
}

// Create database
const db = new Database(DB_PATH);
console.log('Created new lodge.db');

// Enable foreign keys
db.pragma('foreign_keys = ON');

// Execute schema
const schema = fs.readFileSync(SCHEMA_PATH, 'utf-8');
db.exec(schema);
console.log('Applied schema from SQLITE_SCHEMA.sql');

// ============================================================================
// LOAD COMBINED.YAML
// ============================================================================

const combinedYaml = yaml.load(fs.readFileSync(COMBINED_YAML_PATH, 'utf-8'));
console.log(`\nLoaded combined.yaml: ${combinedYaml.facility.name}`);

// Insert zones (derived from camera locations)
const zones = [
  { id: 'packing_line_1', name: 'Packing Line 1', zone_type: 'packing' },
  { id: 'packing_line_2', name: 'Packing Line 2', zone_type: 'packing' },
  { id: 'main_floor', name: 'Main Floor', zone_type: 'staging' },
  { id: 'unknown', name: 'Unknown', zone_type: 'unknown' }
];

const insertZone = db.prepare(`
  INSERT INTO zones (id, name, zone_type) VALUES (?, ?, ?)
`);

for (const zone of zones) {
  insertZone.run(zone.id, zone.name, zone.zone_type);
}
console.log(`Inserted ${zones.length} zones`);

// Insert NVRs
const insertNvr = db.prepare(`
  INSERT INTO nvrs (id, brand, model, ip, mac, max_channels, protocol, path_format, username, password, notes)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
`);

for (const nvr of combinedYaml.nvrs) {
  insertNvr.run(
    nvr.id,
    nvr.brand,
    nvr.model || null,
    nvr.ip,
    nvr.identifiers?.mac || nvr.mac || null,
    nvr.maxChannels,
    nvr.protocol,
    nvr.pathFormat,
    nvr.username,
    nvr.password,
    nvr.notes || null
  );
  console.log(`Inserted NVR: ${nvr.id} (${nvr.brand})`);
}

// Insert channels and cameras from NVR1
const insertChannel = db.prepare(`
  INSERT INTO channels (id, nvr_id, channel_number, status)
  VALUES (?, ?, ?, ?)
`);

const insertCamera = db.prepare(`
  INSERT OR IGNORE INTO cameras (mac, model, resolution, ip)
  VALUES (?, ?, ?, ?)
`);

const insertMount = db.prepare(`
  INSERT INTO mounts (id, x, y, z, wall, facing, zone_id, description)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?)
`);

const insertLinkage = db.prepare(`
  INSERT INTO linkages (mount_id, camera_mac, channel_id, verified_at, verified_by, confidence)
  VALUES (?, ?, ?, ?, ?, ?)
`);

// Parse location to extract zone
function parseZone(location) {
  if (!location) return 'unknown';
  const loc = location.toLowerCase();
  if (loc.includes('packing line 1')) return 'packing_line_1';
  if (loc.includes('packing line 2')) return 'packing_line_2';
  if (loc.includes('main floor')) return 'main_floor';
  return 'unknown';
}

// Parse location to extract wall and facing (rough extraction)
function parseWallAndFacing(location) {
  if (!location) return { wall: null, facing: null };
  const loc = location.toLowerCase();

  let wall = null;
  if (loc.includes('east wall')) wall = 'mercury_perimeter_east';
  else if (loc.includes('west wall')) wall = 'mercury_perimeter_west';
  else if (loc.includes('north wall')) wall = 'mercury_perimeter_north';
  else if (loc.includes('south wall')) wall = 'mercury_perimeter_south';

  let facing = null;
  if (loc.includes('looking sw') || loc.includes('pointing sw')) facing = 'southwest';
  else if (loc.includes('looking nw') || loc.includes('pointing nw')) facing = 'northwest';
  else if (loc.includes('looking se') || loc.includes('pointing se')) facing = 'southeast';
  else if (loc.includes('looking ne') || loc.includes('pointing ne')) facing = 'northeast';
  else if (loc.includes('looking s') || loc.includes('pointing s')) facing = 'south';
  else if (loc.includes('looking n') || loc.includes('pointing n')) facing = 'north';
  else if (loc.includes('looking e') || loc.includes('pointing e')) facing = 'east';
  else if (loc.includes('looking w') || loc.includes('pointing w')) facing = 'west';

  return { wall, facing };
}

let cameraCount = 0;
let mountCount = 0;
let linkageCount = 0;

for (const nvr of combinedYaml.nvrs) {
  if (!nvr.cameras || nvr.cameras.length === 0) continue;

  for (const cam of nvr.cameras) {
    const channelId = `${nvr.id}_ch${String(cam.channel).padStart(2, '0')}`;

    // Insert channel
    insertChannel.run(channelId, nvr.id, cam.channel, 'active');

    // Insert physical camera if MAC known
    if (cam.mac) {
      insertCamera.run(cam.mac, null, cam.resolution, cam.ip);
      cameraCount++;
    }

    // Parse location for mount data
    const zone = parseZone(cam.location);
    const { wall, facing } = parseWallAndFacing(cam.location);

    // Insert mount (food name = position)
    // Note: We don't have x,y,z coordinates in combined.yaml, using 0,0,12 as placeholder
    insertMount.run(
      cam.id,           // food name
      0,                // x - placeholder, needs calibration
      0,                // y - placeholder, needs calibration
      12,               // z - typical ceiling mount height
      wall,
      facing,
      zone,
      cam.location || 'Unknown - needs configuration'
    );
    mountCount++;

    // Insert linkage
    const confidence = cam.mac ? 'verified' : 'assumed';
    const verifiedAt = cam.mac ? '2026-01-17' : null;
    const verifiedBy = cam.mac ? 'novicat' : null;

    insertLinkage.run(
      cam.id,           // mount_id (food name)
      cam.mac || null,  // camera_mac
      channelId,        // channel_id
      verifiedAt,
      verifiedBy,
      confidence
    );
    linkageCount++;
  }
}

console.log(`Inserted ${cameraCount} physical cameras (with known MAC)`);
console.log(`Inserted ${mountCount} mounts`);
console.log(`Inserted ${linkageCount} linkages`);

// ============================================================================
// LOAD FIDUCIALS
// ============================================================================

const fiducialsData = JSON.parse(fs.readFileSync(FIDUCIALS_PATH, 'utf-8'));
console.log(`\nLoaded fiducial-placement.json: ${fiducialsData.fiducials.length} fiducials`);

const insertFiducial = db.prepare(`
  INSERT INTO fiducials (tag_id, family, x, y, z, wall, facing, size_inches)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?)
`);

const insertVisibility = db.prepare(`
  INSERT OR IGNORE INTO mount_fiducial_visibility (mount_id, fiducial_id, expected)
  VALUES (?, ?, 1)
`);

for (const fid of fiducialsData.fiducials) {
  insertFiducial.run(
    fid.id,
    fiducialsData.family,
    fid.x,
    fid.y,
    fid.z,
    fid.wall,
    fid.facing,
    5.5  // standard size
  );

  // Insert visibility relationships
  if (fid.visible_from) {
    for (const mountId of fid.visible_from) {
      // Only insert if it's a food name (camera mount), not a door area
      if (!mountId.includes('_door_') && !mountId.includes('_area')) {
        try {
          insertVisibility.run(mountId, fid.id);
        } catch (e) {
          // Mount might not exist (e.g., door areas)
        }
      }
    }
  }
}

console.log(`Inserted ${fiducialsData.fiducials.length} fiducials`);

// ============================================================================
// VERIFY
// ============================================================================

console.log('\n--- VERIFICATION ---');

const counts = {
  zones: db.prepare('SELECT COUNT(*) as n FROM zones').get().n,
  nvrs: db.prepare('SELECT COUNT(*) as n FROM nvrs').get().n,
  channels: db.prepare('SELECT COUNT(*) as n FROM channels').get().n,
  cameras: db.prepare('SELECT COUNT(*) as n FROM cameras').get().n,
  mounts: db.prepare('SELECT COUNT(*) as n FROM mounts').get().n,
  linkages: db.prepare('SELECT COUNT(*) as n FROM linkages').get().n,
  fiducials: db.prepare('SELECT COUNT(*) as n FROM fiducials').get().n,
  visibility: db.prepare('SELECT COUNT(*) as n FROM mount_fiducial_visibility').get().n
};

console.log('Table counts:');
for (const [table, count] of Object.entries(counts)) {
  console.log(`  ${table}: ${count}`);
}

// Test the view
console.log('\nSample from camera_full view:');
const sample = db.prepare('SELECT mount_id, camera_mac, channel_id, confidence FROM camera_full LIMIT 3').all();
for (const row of sample) {
  console.log(`  ${row.mount_id}: MAC=${row.camera_mac || 'unknown'}, channel=${row.channel_id}, confidence=${row.confidence}`);
}

// Verified vs assumed linkages
const verified = db.prepare("SELECT COUNT(*) as n FROM linkages WHERE confidence = 'verified'").get().n;
const assumed = db.prepare("SELECT COUNT(*) as n FROM linkages WHERE confidence = 'assumed'").get().n;
console.log(`\nLinkage confidence: ${verified} verified, ${assumed} assumed`);

db.close();
console.log('\n✓ Database built successfully: lodge.db');
