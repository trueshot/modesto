/**
 * ModelT Editor
 * Core library for editing ModelT warehouse specifications
 * Handles CRUD operations for doors, cameras, walls, columns
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const NameManager = require('./name-manager');

class ModelTEditor {
  constructor(jsonPath) {
    this.jsonPath = jsonPath;
    this.data = JSON.parse(fs.readFileSync(jsonPath, 'utf-8'));
    this.nameManager = new NameManager();
    this.svgPath = jsonPath.replace('.json', '.svg');
  }

  /**
   * Find a slab by ID
   */
  findSlab(slabId) {
    if (!this.data.slabs || !Array.isArray(this.data.slabs)) {
      throw new Error('No slabs array found in ModelT file');
    }
    const slab = this.data.slabs.find(s => s.id === slabId);
    if (!slab) {
      throw new Error(`Slab "${slabId}" not found`);
    }
    return slab;
  }

  /**
   * Get all cameras across all slabs
   */
  getAllCameras() {
    const cameras = [];
    if (this.data.slabs) {
      this.data.slabs.forEach(slab => {
        if (slab.cameras) {
          cameras.push(...slab.cameras);
        }
      });
    }
    return cameras;
  }

  /**
   * Get all doors across all slabs
   */
  getAllDoors() {
    const doors = [];
    if (this.data.slabs) {
      this.data.slabs.forEach(slab => {
        if (slab.doors) {
          doors.push(...slab.doors);
        }
      });
    }
    return doors;
  }

  /**
   * Get all partition walls across all slabs
   */
  getAllPartitionWalls() {
    const walls = [];
    if (this.data.slabs) {
      this.data.slabs.forEach(slab => {
        if (slab.walls) {
          walls.push(...slab.walls.filter(w => w.type === 'partition'));
        }
      });
    }
    return walls;
  }

  /**
   * Get all columns across all slabs
   */
  getAllColumns() {
    const columns = [];
    if (this.data.slabs) {
      this.data.slabs.forEach(slab => {
        if (slab.columns) {
          columns.push(...slab.columns);
        }
      });
    }
    return columns;
  }

  /**
   * Get facility overview - high-level summary without loading full spec
   */
  getOverview() {
    const facility = {
      name: this.data.name || 'Unknown',
      location: this.data.location ? `${this.data.location.city}, ${this.data.location.state}` : 'Unknown',
      slabs: []
    };

    if (this.data.slabs) {
      this.data.slabs.forEach(slab => {
        // Calculate dimensions from corners
        let dimensions = 'Unknown';
        if (slab.corners && slab.corners.length > 0) {
          const xs = slab.corners.map(c => c.x);
          const ys = slab.corners.map(c => c.y);
          const width = Math.max(...xs) - Math.min(...xs);
          const height = Math.max(...ys) - Math.min(...ys);
          dimensions = `${Math.round(width)}x${Math.round(height)} ft`;
        }

        // Count components
        const components = {
          doors: slab.doors ? slab.doors.length : 0,
          cameras: slab.cameras ? slab.cameras.length : 0,
          walls: slab.walls ? slab.walls.filter(w => w.type === 'partition').length : 0,
          columns: slab.columns ? slab.columns.length : 0
        };

        facility.slabs.push({
          id: slab.id,
          name: slab.name || slab.id,
          dimensions,
          elevation: slab.elevation || 4,
          components
        });
      });
    }

    // Calculate totals
    facility.totals = {
      doors: facility.slabs.reduce((sum, s) => sum + s.components.doors, 0),
      cameras: facility.slabs.reduce((sum, s) => sum + s.components.cameras, 0),
      walls: facility.slabs.reduce((sum, s) => sum + s.components.walls, 0),
      columns: facility.slabs.reduce((sum, s) => sum + s.components.columns, 0)
    };

    return facility;
  }

  // ==================== CAMERAS ====================

  /**
   * Add a camera with auto-assigned name
   */
  addCamera(slabId, spec) {
    const slab = this.findSlab(slabId);
    if (!slab.cameras) slab.cameras = [];

    const allCameras = this.getAllCameras();
    // Accept an externally-allocated id (spec.id) so the caller can pass a name
    // vetted against BOTH rosters (lodge.db mounts + modelT). Without it the
    // generator only sees modelT's subset — that is the gen-15 'cookie' collision
    // (a name free in modelT but already taken in lodge.db). Endgame: the caller
    // is modeltcamerascat's allocator endpoint (rec C). — modeltbabylon gen-15
    let id;
    if (spec.id) {
      if (allCameras.some(c => c.id === spec.id)) {
        throw new Error(`Camera id "${spec.id}" already exists in modelT`);
      }
      id = spec.id;
    } else {
      id = this.nameManager.getNextCameraName(allCameras);
    }
    const name = this.nameManager.capitalize(id);
    const number = this.nameManager.getNextCameraNumber(allCameras);

    const camera = {
      id,
      name,
      number,
      x: spec.x,
      y: spec.y,
      elevation: spec.elevation || 12,
      direction: spec.direction || 0,
      tilt: spec.tilt || 20,
      roll: spec.roll || 0,
      viewingAngle: spec.viewingAngle || 90,
      range: spec.range || 50,
      location: spec.location || ''
    };

    slab.cameras.push(camera);
    return camera;
  }

  /**
   * Add a site feature (§5.14) — kinds: fence (linear) and driveway (area,
   * ruled 2026-08-31); parking reserved. Facility-level siteFeatures[].
   * Vertices rounded to WHOLE FEET (site features are traced off a 0.6 m/px
   * aerial, known to +/-2 ft). — modeltbabylon gen-15/gen-16
   */
  addSiteFeature(spec) {
    if (!this.data.siteFeatures) this.data.siteFeatures = [];
    const existing = this.data.siteFeatures;
    const id = spec.id || this.nameManager.getNextSiteFeatureName(existing);
    if (existing.some(f => f.id === id)) {
      throw new Error(`Site feature "${id}" already exists`);
    }
    const kind = spec.kind || 'fence';
    const path = (spec.path || []).map(p => ({ x: Math.round(p.x), y: Math.round(p.y) }));
    let feature;
    if (kind === 'fence') {
      if (!spec.material) throw new Error('material is required (wooden|chainlink|wire|block|other)');
      if (spec.height === undefined || spec.height === null) throw new Error('height is required (whole feet)');
      if (path.length < 2) throw new Error('fence path needs at least 2 vertices');
      feature = {
        id,
        kind,
        material: spec.material,
        height: Math.round(spec.height),
        path,
        closed: spec.closed === true,
        gaps: spec.gaps || [],
        source: spec.source || 'traced:aerial'
      };
    } else if (kind === 'driveway') {
      // AREA kind (5.14): closed free-geometry polygon, any angles. The
      // polygon is ALWAYS closed — no `closed` field; no height, no gaps.
      if (!spec.surface) throw new Error('surface is required (gravel|asphalt|concrete|dirt|other)');
      if (path.length < 3) throw new Error('driveway path needs at least 3 vertices (a 2-point area is a line)');
      feature = {
        id,
        kind,
        surface: spec.surface,
        path,
        source: spec.source || 'traced:aerial'
      };
    } else {
      throw new Error(`kind "${kind}" not implemented (parking reserved until traced)`);
    }
    existing.push(feature);
    return feature;
  }

  /**
   * Add a vantage (§5.15) — a virtual viewpoint, NOT a camera: separate
   * facility-level vantages[] array, bird-name id, pose above the DATUM,
   * may float anywhere on the property. Validation per 8.3: direction
   * [0,360), tilt [0,90] down, whole-foot x/y/elevation. Out-of-range pose
   * is REJECTED, never clamped — a silently altered aim is a false fact.
   * — modeltbabylon gen-16
   */
  addVantage(spec) {
    if (!this.data.vantages) this.data.vantages = [];
    const existing = this.data.vantages;
    let id = spec.id;
    if (!id) {
      if (typeof this.nameManager.getNextVantageName === 'function') {
        id = this.nameManager.getNextVantageName(existing);
      } else {
        throw new Error('bird-name pool not available yet — pass an explicit id (hawk, eagle, owl, ...)');
      }
    }
    if (existing.some(v => v.id === id)) {
      throw new Error(`Vantage "${id}" already exists`);
    }
    for (const k of ['x', 'y', 'elevation', 'direction', 'tilt']) {
      if (spec[k] === undefined || spec[k] === null || isNaN(parseFloat(spec[k]))) {
        throw new Error(`${k} is required (number)`);
      }
    }
    const rawDirection = parseFloat(spec.direction);
    if (rawDirection < 0 || rawDirection >= 360) throw new Error(`direction ${rawDirection} out of [0, 360)`);
    const direction = Math.round(rawDirection) % 360;   // 359.6 rounds to 360 -> wraps to 0
    const tilt = Math.round(parseFloat(spec.tilt));
    if (tilt < 0 || tilt > 90) throw new Error(`tilt ${tilt} out of [0, 90] (degrees DOWN; aim level or down, never up)`);
    const vantage = { id };
    if (spec.name) vantage.name = spec.name;
    vantage.x = Math.round(parseFloat(spec.x));
    vantage.y = Math.round(parseFloat(spec.y));
    vantage.elevation = Math.round(parseFloat(spec.elevation));
    vantage.direction = direction;
    vantage.tilt = tilt;
    if (spec.fov !== undefined && spec.fov !== null) vantage.fov = Math.round(parseFloat(spec.fov));
    if (spec.source) vantage.source = spec.source;
    existing.push(vantage);
    return vantage;
  }

  /**
   * Move a camera by delta
   */
  moveCamera(cameraId, deltaX, deltaY) {
    for (const slab of this.data.slabs) {
      if (slab.cameras) {
        const camera = slab.cameras.find(c => c.id === cameraId);
        if (camera) {
          camera.x += deltaX;
          camera.y += deltaY;
          return camera;
        }
      }
    }
    throw new Error(`Camera "${cameraId}" not found`);
  }

  /**
   * Update camera properties
   */
  updateCamera(cameraId, props) {
    for (const slab of this.data.slabs) {
      if (slab.cameras) {
        const camera = slab.cameras.find(c => c.id === cameraId);
        if (camera) {
          Object.assign(camera, props);
          return camera;
        }
      }
    }
    throw new Error(`Camera "${cameraId}" not found`);
  }

  /**
   * Delete a camera
   */
  deleteCamera(cameraId) {
    for (const slab of this.data.slabs) {
      if (slab.cameras) {
        const index = slab.cameras.findIndex(c => c.id === cameraId);
        if (index >= 0) {
          const deleted = slab.cameras.splice(index, 1)[0];
          return deleted;
        }
      }
    }
    throw new Error(`Camera "${cameraId}" not found`);
  }

  // ==================== DOORS ====================

  /**
   * Add a door with auto-assigned name
   * Supports all door types: bay, rollup, personnel, cooler, interior
   */
  addDoor(slabId, spec) {
    const slab = this.findSlab(slabId);
    if (!slab.doors) slab.doors = [];

    const allDoors = this.getAllDoors();
    const id = this.nameManager.getNextDoorName(allDoors);
    const type = spec.type || 'bay';

    // Core properties (all doors)
    const door = {
      id,
      wallId: spec.wallId,
      x: spec.x,
      y: spec.y,
      type,
      orientation: spec.orientation || 'horizontal',
      facing: spec.facing
    };

    // Opening dimensions - support both old and new naming
    door.openingWidth = spec.openingWidth || spec.bayWidth || spec.width || 10;
    door.openingHeight = spec.openingHeight || 10;

    // Optional core properties
    if (spec.hardwareSide) door.hardwareSide = spec.hardwareSide;
    if (spec.state) door.state = spec.state;

    // Legacy properties for backward compatibility
    if (spec.bayWidth) door.bayWidth = spec.bayWidth;
    if (spec.doorWidth) door.doorWidth = spec.doorWidth;
    if (spec.width) door.width = spec.width;

    // Type-specific properties
    switch (type) {
      case 'bay':
        // Loading dock door
        if (spec.hasDockSeal !== undefined) door.hasDockSeal = spec.hasDockSeal;
        if (spec.hasDockLeveler !== undefined) door.hasDockLeveler = spec.hasDockLeveler;
        if (spec.hasSafetyStriping !== undefined) door.hasSafetyStriping = spec.hasSafetyStriping;
        if (spec.dockSealWidth) door.dockSealWidth = spec.dockSealWidth;
        if (spec.dockSealHeight) door.dockSealHeight = spec.dockSealHeight;
        if (spec.levelerWidth) door.levelerWidth = spec.levelerWidth;
        if (spec.levelerDepth) door.levelerDepth = spec.levelerDepth;
        break;

      case 'rollup':
        // Standalone roll-up door
        if (spec.housingHeight) door.housingHeight = spec.housingHeight;
        if (spec.trackWidth) door.trackWidth = spec.trackWidth;
        break;

      case 'personnel':
        // Standard hinged door
        if (spec.frameWidth) door.frameWidth = spec.frameWidth;
        if (spec.swingDirection) door.swingDirection = spec.swingDirection;
        if (spec.hingePosition) door.hingePosition = spec.hingePosition;
        break;

      case 'cooler':
        // Insulated sliding door
        if (spec.insulation) door.insulation = spec.insulation;
        if (spec.slideDirection) door.slideDirection = spec.slideDirection;
        if (spec.trackPosition) door.trackPosition = spec.trackPosition;
        break;

      case 'interior':
        // Opening in partition wall
        if (spec.hasPhysicalDoor !== undefined) door.hasPhysicalDoor = spec.hasPhysicalDoor;
        // Interior doors don't need bayWidth/doorWidth
        delete door.bayWidth;
        delete door.doorWidth;
        break;
    }

    slab.doors.push(door);
    return door;
  }

  /**
   * Move a door by delta
   */
  moveDoor(doorId, deltaX, deltaY) {
    for (const slab of this.data.slabs) {
      if (slab.doors) {
        const door = slab.doors.find(d => d.id === doorId);
        if (door) {
          door.x += deltaX;
          door.y += deltaY;
          return door;
        }
      }
    }
    throw new Error(`Door "${doorId}" not found`);
  }

  /**
   * Update door properties
   */
  updateDoor(doorId, props) {
    for (const slab of this.data.slabs) {
      if (slab.doors) {
        const door = slab.doors.find(d => d.id === doorId);
        if (door) {
          Object.assign(door, props);
          return door;
        }
      }
    }
    throw new Error(`Door "${doorId}" not found`);
  }

  /**
   * Delete a door
   */
  deleteDoor(doorId) {
    for (const slab of this.data.slabs) {
      if (slab.doors) {
        const index = slab.doors.findIndex(d => d.id === doorId);
        if (index >= 0) {
          const deleted = slab.doors.splice(index, 1)[0];
          return deleted;
        }
      }
    }
    throw new Error(`Door "${doorId}" not found`);
  }

  // ==================== PARTITION WALLS ====================

  /**
   * Add a partition wall with auto-assigned name
   * segments format: [{direction: 'east', length: 50}, ...]
   */
  addPartitionWall(slabId, spec) {
    const slab = this.findSlab(slabId);
    if (!slab.walls) slab.walls = [];

    const allWalls = this.getAllPartitionWalls();
    const id = this.nameManager.getNextPartitionName(allWalls);

    const wall = {
      id,
      type: 'partition',
      start: spec.start,
      segments: spec.segments
    };

    slab.walls.push(wall);
    return wall;
  }

  /**
   * Delete a partition wall
   */
  deletePartitionWall(wallId) {
    for (const slab of this.data.slabs) {
      if (slab.walls) {
        const index = slab.walls.findIndex(w => w.id === wallId && w.type === 'partition');
        if (index >= 0) {
          const deleted = slab.walls.splice(index, 1)[0];
          return deleted;
        }
      }
    }
    throw new Error(`Partition wall "${wallId}" not found`);
  }

  // ==================== COLUMNS ====================

  /**
   * Add a column with auto-assigned name
   */
  addColumn(slabId, spec) {
    const slab = this.findSlab(slabId);
    if (!slab.columns) slab.columns = [];

    const allColumns = this.getAllColumns();
    const id = this.nameManager.getNextColumnName(allColumns);
    const name = this.nameManager.capitalize(id);

    const column = {
      id,
      name,
      x: spec.x,
      y: spec.y,
      height: spec.height || 15,
      size: spec.size || 1,
      location: spec.location || ''
    };

    slab.columns.push(column);
    return column;
  }

  /**
   * Move a column by delta
   */
  moveColumn(columnId, deltaX, deltaY) {
    for (const slab of this.data.slabs) {
      if (slab.columns) {
        const column = slab.columns.find(c => c.id === columnId);
        if (column) {
          column.x += deltaX;
          column.y += deltaY;
          return column;
        }
      }
    }
    throw new Error(`Column "${columnId}" not found`);
  }

  /**
   * Delete a column
   */
  deleteColumn(columnId) {
    for (const slab of this.data.slabs) {
      if (slab.columns) {
        const index = slab.columns.findIndex(c => c.id === columnId);
        if (index >= 0) {
          const deleted = slab.columns.splice(index, 1)[0];
          return deleted;
        }
      }
    }
    throw new Error(`Column "${columnId}" not found`);
  }

  // ==================== SAVE & REGENERATE ====================

  /**
   * Save the JSON file
   */
  save() {
    fs.writeFileSync(this.jsonPath, JSON.stringify(this.data, null, 2), 'utf-8');
  }

  /**
   * Regenerate SVG from JSON using the skill's generate-svg script
   */
  regenerateSVG() {
    const scriptPath = path.join(__dirname, '..', 'modelt', 'scripts', 'generate-svg.js'); // was a dead ~/.claude/skills path — modeltbabylon gen-10 2026-08-26
    const jsonFilename = path.basename(this.jsonPath);
    const svgFilename = path.basename(this.svgPath);
    const workingDir = path.dirname(this.jsonPath);

    try {
      execSync(
        `node "${scriptPath}" "${jsonFilename}" "${svgFilename}"`,
        { cwd: workingDir, stdio: 'inherit' }
      );
    } catch (error) {
      throw new Error(`Failed to regenerate SVG: ${error.message}`);
    }
  }

  /**
   * Save and regenerate SVG in one operation
   */
  commit() {
    this.save();
    try {
      this.regenerateSVG();
    } catch (e) {
      console.error('SVG regeneration skipped:', e.message);
    }
  }
}

module.exports = ModelTEditor;
