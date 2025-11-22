const express = require('express');
const fs = require('fs');
const path = require('path');
const config = require('../../config/config');

const router = express.Router();

/**
 * GET /api/warehouses/:id/svg
 * Get warehouse SVG file
 */
router.get('/:id/svg', (req, res) => {
  try {
    const { id } = req.params;
    const svgPath = path.join(config.warehousesPath, id, 'warehouse.svg');

    if (!fs.existsSync(svgPath)) {
      return res.status(404).json({ error: `SVG for warehouse "${id}" not found` });
    }

    const svgContent = fs.readFileSync(svgPath, 'utf8');
    res.setHeader('Content-Type', 'image/svg+xml');
    res.send(svgContent);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

/**
 * GET /api/warehouses/:id/svg-data
 * Get SVG as JSON (for loading in viewer without CORS issues)
 */
router.get('/:id/svg-data', (req, res) => {
  try {
    const { id } = req.params;
    const svgPath = path.join(config.warehousesPath, id, 'warehouse.svg');

    if (!fs.existsSync(svgPath)) {
      return res.status(404).json({ error: `SVG for warehouse "${id}" not found` });
    }

    const svgContent = fs.readFileSync(svgPath, 'utf8');
    res.json({
      warehouseId: id,
      svg: svgContent
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;
