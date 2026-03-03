const express = require('express');
const fs = require('fs');
const path = require('path');
const config = require('../../config/config');

const router = express.Router();

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
    const { nvr } = req.query;
    const thumbnailsPath = path.join(config.warehousesPath, id, 'cameras', 'thumbnails');
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
          url: `/warehouses/${id}/cameras/thumbnails/${file}`
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

module.exports = router;
