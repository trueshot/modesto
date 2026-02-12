const express = require('express');
const path = require('path');
const Database = require('better-sqlite3');

const router = express.Router();

// Path to SQLite database
const DB_PATH = path.join(__dirname, '..', '..', '..', 'warehouses', 'lodge', 'lodge.db');

/**
 * GET /api/nvrs
 * List all NVRs with optional ownership filter
 */
router.get('/', (req, res) => {
  try {
    const { ownership } = req.query;
    const db = new Database(DB_PATH, { readonly: true });

    let sql = 'SELECT id, brand, model, ip, mac, max_channels, protocol, path_format, notes, ownership, onvif_supported, serial FROM nvrs';
    let params = [];

    if (ownership) {
      sql += ' WHERE ownership = ?';
      params.push(ownership);
    }

    const nvrs = db.prepare(sql).all(...params);
    db.close();

    // Format response (exclude credentials)
    const summary = nvrs.map(nvr => ({
      id: nvr.id,
      brand: nvr.brand,
      model: nvr.model,
      ip: nvr.ip,
      mac: nvr.mac,
      maxChannels: nvr.max_channels,
      ownership: nvr.ownership,
      onvifSupported: nvr.onvif_supported === 1,
      notes: nvr.notes,
      serial: nvr.serial
    }));

    res.json({
      count: summary.length,
      nvrs: summary
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

/**
 * GET /api/nvrs/:id
 * Get details for a specific NVR by ID
 */
router.get('/:id', (req, res) => {
  try {
    const { id } = req.params;
    const db = new Database(DB_PATH, { readonly: true });

    const nvr = db.prepare('SELECT * FROM nvrs WHERE id = ?').get(id);
    db.close();

    if (!nvr) {
      return res.status(404).json({ error: `NVR "${id}" not found` });
    }

    // Return details (exclude credentials)
    res.json({
      id: nvr.id,
      brand: nvr.brand,
      model: nvr.model,
      ip: nvr.ip,
      mac: nvr.mac,
      maxChannels: nvr.max_channels,
      ownership: nvr.ownership,
      onvifSupported: nvr.onvif_supported === 1,
      protocol: nvr.protocol,
      pathFormat: nvr.path_format,
      notes: nvr.notes,
      serial: nvr.serial
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

/**
 * GET /api/nvrs/:id/channels
 * Get all channels for a specific NVR
 */
router.get('/:id/channels', (req, res) => {
  try {
    const { id } = req.params;
    const db = new Database(DB_PATH, { readonly: true });

    // Verify NVR exists
    const nvr = db.prepare('SELECT id, brand FROM nvrs WHERE id = ?').get(id);
    if (!nvr) {
      db.close();
      return res.status(404).json({ error: `NVR "${id}" not found` });
    }

    // Get channels for this NVR
    const channels = db.prepare(`
      SELECT id, channel_number, camera_id, status, recording, resolution
      FROM channels
      WHERE nvr_id = ?
      ORDER BY channel_number
    `).all(id);
    db.close();

    res.json({
      nvrId: id,
      nvrBrand: nvr.brand,
      count: channels.length,
      channels: channels.map(ch => ({
        id: ch.id,
        channelNumber: ch.channel_number,
        cameraId: ch.camera_id,
        status: ch.status,
        recording: ch.recording === 1,
        resolution: ch.resolution
      }))
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;
