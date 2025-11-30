/**
 * ModelT Name Manager
 * Auto-assigns names from naming convention lists
 * Reads from ModelT warehouse builder skill naming files
 */

const fs = require('fs');
const path = require('path');

class NameManager {
  constructor(skillPath = 'C:\\Users\\wpssh\\.claude\\skills\\modelt-warehouse-builder') {
    this.skillPath = skillPath;
    this.cameraNames = this.loadList('CAMERA_NAMES.txt');
    this.doorNames = this.loadList('DOOR_NAMES.txt');
    this.partitionNames = this.loadList('PARTITION_WALL_NAMES.txt');
    this.columnNames = this.loadList('COLUMN_NAMES.txt');
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
   * Capitalize first letter (for display names)
   */
  capitalize(str) {
    return str.charAt(0).toUpperCase() + str.slice(1);
  }
}

module.exports = NameManager;
