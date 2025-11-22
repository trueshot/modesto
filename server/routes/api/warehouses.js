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
