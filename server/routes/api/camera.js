const express = require('express');
const fs = require('fs');
const path = require('path');
const config = require('../../config/config');

const router = express.Router();

/**
 * GET /api/warehouses/:id/cameras
 * Get all cameras for a warehouse
 */
router.get('/:id/cameras', (req, res) => {
  try {
    const { id } = req.params;
    const warehouseSpecPath = path.join(config.warehousesPath, id, 'warehouse.json');

    if (!fs.existsSync(warehouseSpecPath)) {
      return res.status(404).json({ error: `Warehouse "${id}" not found` });
    }

    const spec = JSON.parse(fs.readFileSync(warehouseSpecPath, 'utf8'));

    // Extract cameras from spec
    const cameras = [];

    if (spec.slabs && Array.isArray(spec.slabs)) {
      // V2 format: cameras are in slabs
      spec.slabs.forEach((slab, slabIndex) => {
        if (slab.cameras && Array.isArray(slab.cameras)) {
          slab.cameras.forEach(cam => {
            cameras.push({
              ...cam,
              slabId: slab.id,
              slabElevation: slab.elevation || 4
            });
          });
        }
      });
    } else if (spec.cameras && Array.isArray(spec.cameras)) {
      // V1 format: cameras at root
      spec.cameras.forEach(cam => {
        cameras.push({
          ...cam,
          slabId: 'default',
          slabElevation: 4
        });
      });
    }

    res.json({
      warehouseId: id,
      count: cameras.length,
      cameras
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

/**
 * GET /api/warehouses/:id/cameras/:cameraId
 * Get specific camera details
 */
router.get('/:id/cameras/:cameraId', (req, res) => {
  try {
    const { id, cameraId } = req.params;
    const warehouseSpecPath = path.join(config.warehousesPath, id, 'warehouse.json');

    if (!fs.existsSync(warehouseSpecPath)) {
      return res.status(404).json({ error: `Warehouse "${id}" not found` });
    }

    const spec = JSON.parse(fs.readFileSync(warehouseSpecPath, 'utf8'));

    let foundCamera = null;
    let slabInfo = {};

    if (spec.slabs && Array.isArray(spec.slabs)) {
      // V2 format
      for (const slab of spec.slabs) {
        if (slab.cameras) {
          const cam = slab.cameras.find(c => c.id === cameraId);
          if (cam) {
            foundCamera = { ...cam, slabId: slab.id, slabElevation: slab.elevation || 4 };
            slabInfo = { id: slab.id, name: slab.name, elevation: slab.elevation };
            break;
          }
        }
      }
    } else if (spec.cameras) {
      // V1 format
      foundCamera = spec.cameras.find(c => c.id === cameraId);
      if (foundCamera) {
        foundCamera = { ...foundCamera, slabId: 'default', slabElevation: 4 };
        slabInfo = { id: 'default', elevation: 4 };
      }
    }

    if (!foundCamera) {
      return res.status(404).json({ error: `Camera "${cameraId}" not found in warehouse "${id}"` });
    }

    res.json({
      warehouseId: id,
      slab: slabInfo,
      camera: foundCamera
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;
