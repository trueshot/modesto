/**
 * ModelT Name Manager
 * Auto-assigns names from naming convention lists
 * Reads from ModelT warehouse builder skill naming files
 */

const fs = require('fs');
const path = require('path');

class NameManager {
  // Default was a dead ~/.claude/skills path; the lists live in the repo's modelt/ folder.
  // modeltbabylon gen-12, 2026-08-28
  constructor(skillPath = path.join(__dirname, '..', 'modelt')) {
    this.skillPath = skillPath;
    this.cameraNames = this.loadList('CAMERA_NAMES.txt');
    this.doorNames = this.loadList('DOOR_NAMES.txt');
    this.partitionNames = this.loadList('PARTITION_WALL_NAMES.txt');
    this.columnNames = this.loadList('COLUMN_NAMES.txt');
    // Packing lines (river pool) + truck wells (lake pool), §6.2: no .txt files — load from
    // the authoritative NAMING_CONVENTIONS.json so each pool has a single source of truth.
    this.packingLineNames = this.loadConventionPool('packingLines');
    this.truckWellNames = this.loadConventionPool('truckWells');
    // Site features (mountain pool, §5.14): ONE pool shared by all kinds (fence/driveway/parking).
    this.siteFeatureNames = this.loadConventionPool('siteFeatures');
    // Vantages (bird pool, §5.15): virtual viewpoints, NOT cameras.
    this.vantageNames = this.loadConventionPool('vantages');
    // Printing platforms (herb pool, §5.16): slab-bound plant anchors for printer devices.
    this.printingPlatformNames = this.loadConventionPool('printingPlatforms');
  }

  /**
   * Load a naming pool from NAMING_CONVENTIONS.json (the authoritative source).
   */
  loadConventionPool(category) {
    try {
      const p = path.join(this.skillPath, 'NAMING_CONVENTIONS.json');
      const conv = JSON.parse(fs.readFileSync(p, 'utf-8'));
      return (conv.namingConventions && conv.namingConventions[category]) || [];
    } catch (error) {
      console.warn(`Warning: Could not load pool "${category}" from NAMING_CONVENTIONS.json`);
      return [];
    }
  }

  /**
   * Load a naming list from the skill directory
   */
  loadList(filename) {
    try {
      const filePath = path.join(this.skillPath, filename);
      const content = fs.readFileSync(filePath, 'utf-8');
      return content
        .split('\n')
        .map(line => line.trim())
        .filter(line => line && !line.startsWith('#'));
    } catch (error) {
      console.warn(`Warning: Could not load ${filename} from skill directory`);
      return [];
    }
  }

  /**
   * Get the next available camera name
   */
  getNextCameraName(existingCameras) {
    const usedNames = new Set(existingCameras.map(c => c.id));
    const availableName = this.cameraNames.find(name => !usedNames.has(name));

    if (!availableName) {
      throw new Error('No available camera names remaining in CAMERA_NAMES.txt');
    }

    return availableName;
  }

  /**
   * Get the next camera number (auto-increment)
   */
  getNextCameraNumber(existingCameras) {
    const maxNumber = Math.max(...existingCameras.map(c => c.number || 0), 0);
    return maxNumber + 1;
  }

  /**
   * Get the next available door name
   */
  getNextDoorName(existingDoors) {
    const usedNames = new Set(existingDoors.map(d => d.id));
    const availableName = this.doorNames.find(name => !usedNames.has(name));

    if (!availableName) {
      throw new Error('No available door names remaining in DOOR_NAMES.txt');
    }

    return availableName;
  }

  /**
   * Get the next available partition wall name
   */
  getNextPartitionName(existingWalls) {
    const usedNames = new Set(existingWalls.map(w => w.id));
    const availableName = this.partitionNames.find(name => !usedNames.has(name));

    if (!availableName) {
      throw new Error('No available partition names remaining in PARTITION_WALL_NAMES.txt');
    }

    return availableName;
  }

  /**
   * Get the next available column name
   */
  getNextColumnName(existingColumns) {
    const usedNames = new Set(existingColumns.map(c => c.id));
    const availableName = this.columnNames.find(name => !usedNames.has(name));

    if (!availableName) {
      throw new Error('No available column names remaining in COLUMN_NAMES.txt');
    }

    return availableName;
  }

  /**
   * Get the next available packing-line name (river pool)
   */
  getNextPackingLineName(existingLines) {
    const usedNames = new Set(existingLines.map(l => l.id));
    const availableName = this.packingLineNames.find(name => !usedNames.has(name));

    if (!availableName) {
      throw new Error('No available packing-line names remaining in the packingLines pool');
    }

    return availableName;
  }

  /**
   * Get the next available printing-platform name (herb pool, §5.16)
   */
  getNextPrintingPlatformName(existingPlatforms) {
    const usedNames = new Set(existingPlatforms.map(p => p.id));
    const availableName = this.printingPlatformNames.find(name => !usedNames.has(name));

    if (!availableName) {
      throw new Error('No available printing-platform names remaining in the printingPlatforms pool');
    }

    return availableName;
  }

  /**
   * Get the next available vantage name (bird pool, §5.15)
   */
  getNextVantageName(existingVantages) {
    const usedNames = new Set(existingVantages.map(v => v.id));
    const availableName = this.vantageNames.find(name => !usedNames.has(name));

    if (!availableName) {
      throw new Error('No available vantage names remaining in the vantages pool');
    }

    return availableName;
  }

  /**
   * Get the next available site-feature name (mountain pool, §5.14 — shared by all kinds)
   */
  getNextSiteFeatureName(existingFeatures) {
    const usedNames = new Set(existingFeatures.map(f => f.id));
    const availableName = this.siteFeatureNames.find(name => !usedNames.has(name));

    if (!availableName) {
      throw new Error('No available site-feature names remaining in the siteFeatures pool');
    }

    return availableName;
  }

  /**
   * Get the next available truck-well name (lake pool)
   */
  getNextTruckWellName(existingWells) {
    const usedNames = new Set(existingWells.map(w => w.id));
    const availableName = this.truckWellNames.find(name => !usedNames.has(name));

    if (!availableName) {
      throw new Error('No available truck-well names remaining in the truckWells pool');
    }

    return availableName;
  }

  /**
   * Capitalize first letter (for display names)
   */
  capitalize(str) {
    return str.charAt(0).toUpperCase() + str.slice(1);
  }
}

module.exports = NameManager;
