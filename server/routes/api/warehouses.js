const express = require('express');
const fs = require('fs');
const path = require('path');
const config = require('../../config/config');

const router = express.Router();

// Warehouse ids are directory names under warehousesPath. Reject anything that
// isn't a plain slug so ':id' can't be used to traverse out of that folder
// (path safety for every :id route below). — modestomulti gen-6
const VALID_ID = /^[A-Za-z0-9_-]+$/;
function badId(id, res) {
  if (VALID_ID.test(id)) return false;
  res.status(400).json({ error: 'invalid warehouse id' });
  return true;
}

/**
 * GET /api/warehouses
 * List all available warehouses
 */
router.get('/', (req, res) => {
  try {
    const warehousesPath = config.warehousesPath;

    // Read all directories in warehouses folder
    const warehouses = fs.readdirSync(warehousesPath)
      .filter(file => {
        const fullPath = path.join(warehousesPath, file);
        return fs.statSync(fullPath).isDirectory();
      })
      .map(dir => {
        const metadataPath = path.join(warehousesPath, dir, 'metadata.json');
        let metadata = { id: dir, name: dir };

        if (fs.existsSync(metadataPath)) {
          try {
            metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8'));
          } catch (err) {
            console.error(`Error reading metadata for ${dir}:`, err);
          }
        }

        return metadata;
      });

    res.json({
      count: warehouses.length,
      warehouses
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

/**
 * GET /api/warehouses/:id
 * Get warehouse specification by ID
 */
router.get('/:id', (req, res) => {
  try {
    const { id } = req.params;
    if (badId(id, res)) return;
    const warehouseSpecPath = path.join(config.warehousesPath, id, 'warehouse.json');
    const metadataPath = path.join(config.warehousesPath, id, 'metadata.json');

    if (!fs.existsSync(warehouseSpecPath)) {
      return res.status(404).json({ error: `Warehouse "${id}" not found` });
    }

    // Read spec
    const spec = JSON.parse(fs.readFileSync(warehouseSpecPath, 'utf8'));

    // Read metadata if available
    let metadata = { id };
    if (fs.existsSync(metadataPath)) {
      try {
        metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8'));
      } catch (err) {
        console.error(`Error reading metadata:`, err);
      }
    }

    res.json({
      id,
      metadata,
      spec
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

/**
 * GET /api/warehouses/:id/thumbnails
 * List all camera thumbnail files for a warehouse
 * Query params:
 *   ?nvr=nvr2  - Filter to only show cameras from specific NVR
 */
router.get('/:id/thumbnails', (req, res) => {
  try {
    const { id } = req.params;
    if (badId(id, res)) return;
    const { nvr } = req.query;
    const thumbnailsPath = path.join(config.warehousesPath, id, 'cameras', 'thumb');
    const configPath = path.join(config.warehousesPath, id, 'cameras', 'config.json');

    if (!fs.existsSync(thumbnailsPath)) {
      return res.status(404).json({ error: `Thumbnails folder for warehouse "${id}" not found` });
    }

    // Load camera config to filter by NVR
    let cameraConfig = null;
    let allowedCameraIds = null;
    if (fs.existsSync(configPath)) {
      cameraConfig = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      // If nvr filter specified, get list of allowed camera IDs
      if (nvr && cameraConfig.channels) {
        allowedCameraIds = new Set(
          cameraConfig.channels
            .filter(ch => {
              // nvr2 cameras have explicit nvrId, nvr1 cameras don't
              if (nvr === 'nvr2') return ch.nvrId === 'nvr2';
              if (nvr === 'nvr1') return !ch.nvrId || ch.nvrId === 'nvr1';
              return ch.nvrId === nvr;
            })
            .map(ch => ch.modelTCameraId)
        );
      }
    }

    const files = fs.readdirSync(thumbnailsPath)
      .filter(file => /\.(jpg|jpeg|png|gif)$/i.test(file))
      .map(file => {
        const name = file.replace(/\.(jpg|jpeg|png|gif)$/i, '');
        return {
          id: name,
          filename: file,
          url: `/warehouses/${id}/cameras/thumb/${file}`
        };
      })
      .filter(t => {
        // If NVR filter active, only include cameras from that NVR
        if (allowedCameraIds) return allowedCameraIds.has(t.id);
        return true;
      });

    res.json({
      count: files.length,
      warehouseId: id,
      nvrFilter: nvr || null,
      thumbnails: files
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

/**
 * GET /api/warehouses/:id/metadata
 * Get warehouse metadata only
 */
router.get('/:id/metadata', (req, res) => {
  try {
    const { id } = req.params;
    if (badId(id, res)) return;
    const metadataPath = path.join(config.warehousesPath, id, 'metadata.json');

    if (!fs.existsSync(metadataPath)) {
      return res.status(404).json({ error: `Metadata for warehouse "${id}" not found` });
    }

    const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8'));
    res.json(metadata);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// ---------------------------------------------------------------------------
// Mount -> camera linkages (the persisted result of the viewer's Map Camera)
// GET /api/warehouses/:id/linkages
//   -> { linkages: { <mountId>: { ip, mac, channel, confidence } } }
// Source of truth is <id>.db (linkages JOIN cameras). The viewer loads this
// on warehouse load so mappings work in ANY browser, not just the one that
// made them. — modeltbabylon gen-10, 2026-08-26 (George: Show Image blank)
// ---------------------------------------------------------------------------
router.get('/:id/linkages', (req, res) => {
  const { id } = req.params;
  if (badId(id, res)) return;
  const dbPath = path.join(config.warehousesPath, id, `${id}.db`);
  if (!fs.existsSync(dbPath)) return res.json({ linkages: {} });
  try {
    const Database = require('better-sqlite3');
    const db = new Database(dbPath, { readonly: true });
    const rows = db.prepare(`
      SELECT l.mount_id, l.camera_mac, l.channel_id, l.confidence, c.ip
      FROM linkages l LEFT JOIN cameras c ON c.mac = l.camera_mac
    `).all();
    db.close();
    const linkages = {};
    for (const r of rows) {
      if (!r.ip) continue;
      linkages[r.mount_id] = { ip: r.ip, mac: r.camera_mac, channel: r.channel_id, confidence: r.confidence };
    }
    res.json({ linkages });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ---------------------------------------------------------------------------
// Live camera frame, proxied through this server.
// GET /api/warehouses/:id/live/:ip  -> image/jpeg (no-store)
// camera-service (127.0.0.1:8001) is only reachable ON lodge_cat; browsers
// over the VPN reach this server, so this route is the bridge. Same pattern
// as modeltcamerascat's MEP /api/snapshot/:ip. — modeltbabylon gen-10
// ---------------------------------------------------------------------------
const CAMERA_SERVICE = process.env.CAMERA_SERVICE_URL || 'http://127.0.0.1:8001';
router.get('/:id/live/:ip', async (req, res) => {
  const ip = req.params.ip;
  if (!/^[0-9]{1,3}([.][0-9]{1,3}){3}$/.test(ip)) return res.status(400).json({ error: 'bad ip' });
  const url = `${CAMERA_SERVICE}/api/camera/${ip}/frame?encoding=jpeg&timeout=8`;
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(12000) });
    if (!r.ok) {
      const text = await r.text().catch(() => '');
      return res.status(r.status).json({ error: `camera-service ${r.status}`, detail: text.slice(0, 200) });
    }
    const buf = Buffer.from(await r.arrayBuffer());
    res.set('Content-Type', r.headers.get('content-type') || 'image/jpeg');
    res.set('Cache-Control', 'no-store');
    res.send(buf);
  } catch (e) {
    res.status(504).json({ error: e.name === 'TimeoutError' ? 'capture timed out' : e.message });
  }
});

// ---------------------------------------------------------------------------
// GET /api/warehouses/:id/views/:ip  -> latest saved-view transform (JSON)
// modeltcamerascat saves George's aligned camera framings to
// warehouses/:id/cameras/views/{ip}_{stamp}.json. This same-origin route hands
// the viewer the latest one so the image overlay can reproduce his framing
// (rotate/scale about center_image_px). Returns 404 if none saved yet.
// — modeltbabylon gen-15
// ---------------------------------------------------------------------------
router.get('/:id/views/:ip', (req, res) => {
  const { id, ip } = req.params;
  if (badId(id, res)) return;
  if (!/^[0-9]{1,3}([.][0-9]{1,3}){3}$/.test(ip)) return res.status(400).json({ error: 'bad ip' });
  const dir = path.join(config.warehousesPath, id, 'cameras', 'views');
  try {
    if (!fs.existsSync(dir)) return res.status(404).json({ error: 'no views for warehouse' });
    const files = fs.readdirSync(dir)
      .filter(f => f.startsWith(`${ip}_`) && f.endsWith('.json'))
      .sort();  // stamp is ISO -> lexical sort = chronological
    if (files.length === 0) return res.status(404).json({ error: `no saved view for ${ip}` });
    const latest = files[files.length - 1];
    const data = JSON.parse(fs.readFileSync(path.join(dir, latest), 'utf8'));
    res.set('Cache-Control', 'no-store');
    res.json({ file: latest, saved_at: data.saved_at, view: data.view });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ---------------------------------------------------------------------------
// Vantages companion — warehouses/<id>/vantages.json (5.15, George ruling
// 2026-08-31: a vantage is a drone view, NOT plant; lives OUTSIDE the
// normative .modelT.json, writes ungated + self-service — the viewer's Save
// Vantage posts here directly, nobody in the loop). Validation lives in
// tools/vantages-store.js (the single writer). — modeltbabylon gen-16
// ---------------------------------------------------------------------------
const vantagesStore = require('../../../tools/vantages-store');

router.get('/:id/vantages', (req, res) => {
  const { id } = req.params;
  if (badId(id, res)) return;
  try {
    const dir = path.join(config.warehousesPath, id);
    if (!fs.existsSync(dir)) return res.status(404).json({ error: 'warehouse not found' });
    res.set('Cache-Control', 'no-store');
    res.json(vantagesStore.load(dir));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

router.post('/:id/vantages', (req, res) => {
  const { id } = req.params;
  if (badId(id, res)) return;
  try {
    const dir = path.join(config.warehousesPath, id);
    if (!fs.existsSync(dir)) return res.status(404).json({ error: 'warehouse not found' });
    const vantage = vantagesStore.add(dir, req.body || {});
    console.log(`👁 vantage saved: ${id}/${vantage.id}`, vantage);
    res.status(201).json({ success: true, vantage });
  } catch (e) {
    res.status(400).json({ error: e.message });   // validation message, human-readable
  }
});

router.delete('/:id/vantages/:vid', (req, res) => {
  const { id, vid } = req.params;
  if (badId(id, res)) return;
  if (!VALID_ID.test(vid)) return res.status(400).json({ error: 'invalid vantage id' });
  try {
    const dir = path.join(config.warehousesPath, id);
    const gone = vantagesStore.remove(dir, vid);
    res.json({ success: true, removed: gone });
  } catch (e) {
    res.status(404).json({ error: e.message });
  }
});

module.exports = router;
