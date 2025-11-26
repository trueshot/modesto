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
    const id = this.nameManager.getNextCameraName(allCameras);
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
   */
  addDoor(slabId, spec) {
    const slab = this.findSlab(slabId);
    if (!slab.doors) slab.doors = [];

    const allDoors = this.getAllDoors();
    const id = this.nameManager.getNextDoorName(allDoors);

    const door = {
      id,
      wallId: spec.wallId,
      x: spec.x,
      y: spec.y,
      bayWidth: spec.bayWidth || 10,
      doorWidth: spec.doorWidth || 104,
      type: spec.type || 'bay',
      orientation: spec.orientation || 'horizontal',
      facing: spec.facing
    };

    // Interior doors use 'width' instead of 'bayWidth'
    if (spec.type === 'interior' && spec.width) {
      door.width = spec.width;
      delete door.bayWidth;
      delete door.doorWidth;
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
    const scriptPath = 'C:\\Users\\georg\\.claude\\skills\\modelt-warehouse-builder\\scripts\\generate-svg.js';
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
    this.regenerateSVG();
  }
}

module.exports = ModelTEditor;
