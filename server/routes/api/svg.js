const express = require('express');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const config = require('../../config/config');

const router = express.Router();

/**
 * GET /api/warehouses/:id/svg
 * Get warehouse SVG - regenerated live from JSON specification
 */
router.get('/:id/svg', (req, res) => {
  try {
    const { id } = req.params;
    const warehouseDir = path.join(config.warehousesPath, id);

    // Look for .modelT.json (source of truth)
    const jsonPath = path.join(warehouseDir, `${id}.modelT.json`);

    if (!fs.existsSync(jsonPath)) {
      return res.status(404).json({ error: `Warehouse specification "${id}.modelT.json" not found` });
    }

    // Regenerate SVG from JSON on-demand
    const scriptPath = path.join(__dirname, '../../..', '.claude', 'skills', 'modelt-warehouse-builder', 'scripts', 'generate-svg.js');

    if (!fs.existsSync(scriptPath)) {
      // Fall back to reading cached SVG if generator script not found
      let svgPath = path.join(warehouseDir, `${id}.modelT.svg`);
      if (!fs.existsSync(svgPath)) {
        svgPath = path.join(warehouseDir, 'warehouse.svg');
      }
      if (fs.existsSync(svgPath)) {
        const svgContent = fs.readFileSync(svgPath, 'utf8');
        res.setHeader('Content-Type', 'image/svg+xml');
        res.send(svgContent);
        return;
      }
      return res.status(404).json({ error: 'SVG generator not found and no cached SVG available' });
    }

    // Use the generate-svg.js script to create SVG from JSON, outputting to stdout
    try {
      const command = `node "${scriptPath}" "${jsonPath}" -`;
      const svgContent = execSync(command, { encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });

      res.setHeader('Content-Type', 'image/svg+xml');
      res.send(svgContent);
    } catch (execError) {
      // If generation fails, fall back to reading cached SVG
      console.error(`SVG generation error for ${id}:`, execError.message);
      
      let svgPath = path.join(warehouseDir, `${id}.modelT.svg`);
      if (!fs.existsSync(svgPath)) {
        svgPath = path.join(warehouseDir, 'warehouse.svg');
      }

      if (fs.existsSync(svgPath)) {
        const svgContent = fs.readFileSync(svgPath, 'utf8');
        res.setHeader('Content-Type', 'image/svg+xml');
        res.send(svgContent);
      } else {
        res.status(500).json({ error: 'SVG generation failed: ' + execError.message });
      }
    }
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

/**
 * GET /api/warehouses/:id/svg-data
 * Get SVG as JSON (for loading in viewer without CORS issues)
 * Also regenerates from JSON on-demand
 */
router.get('/:id/svg-data', (req, res) => {
  try {
    const { id } = req.params;
    const warehouseDir = path.join(config.warehousesPath, id);

    // Look for .modelT.json (source of truth)
    const jsonPath = path.join(warehouseDir, `${id}.modelT.json`);

    if (!fs.existsSync(jsonPath)) {
      return res.status(404).json({ error: `Warehouse specification "${id}.modelT.json" not found` });
    }

    // Regenerate SVG from JSON on-demand
    const scriptPath = path.join(__dirname, '../../..', '.claude', 'skills', 'modelt-warehouse-builder', 'scripts', 'generate-svg.js');

    if (!fs.existsSync(scriptPath)) {
      // Fall back to reading cached SVG if generator script not found
      let svgPath = path.join(warehouseDir, `${id}.modelT.svg`);
      if (!fs.existsSync(svgPath)) {
        svgPath = path.join(warehouseDir, 'warehouse.svg');
      }
      if (fs.existsSync(svgPath)) {
        const svgContent = fs.readFileSync(svgPath, 'utf8');
        res.json({
          warehouseId: id,
          svg: svgContent
        });
        return;
      }
      return res.status(404).json({ error: 'SVG generator not found and no cached SVG available' });
    }

    // Use the generate-svg.js script to create SVG from JSON
    try {
      const command = `node "${scriptPath}" "${jsonPath}" -`;
      const svgContent = execSync(command, { encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });

      res.json({
        warehouseId: id,
        svg: svgContent
      });
    } catch (execError) {
      // If generation fails, fall back to reading cached SVG
      console.error(`SVG generation error for ${id}:`, execError.message);
      
      let svgPath = path.join(warehouseDir, `${id}.modelT.svg`);
      if (!fs.existsSync(svgPath)) {
        svgPath = path.join(warehouseDir, 'warehouse.svg');
      }

      if (fs.existsSync(svgPath)) {
        const svgContent = fs.readFileSync(svgPath, 'utf8');
        res.json({
          warehouseId: id,
          svg: svgContent
        });
      } else {
        res.status(500).json({ error: 'SVG generation failed: ' + execError.message });
      }
    }
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;
