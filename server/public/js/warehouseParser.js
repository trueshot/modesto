/**
 * ModelT Warehouse Parser
 * Reads ModelT SVG specification and converts to 3D Babylon.js scene
 *
 * ModelT Format:
 * - SVG contains embedded JSON specification in <script type="application/json" id="modelt-schema">
 * - Components: slab, walls, partitionWalls, columns, doors, cameras
 * - Coordinate system: Origin at NW corner, X=East, Y=South (feet)
 * - Babylon conversion: X→X, Y→-Z, elevation→Y
 */

class ModelTParser {
    constructor(svgDoc) {
        this.svgDoc = svgDoc;
        this.spec = null;
    }

    /**
     * Parse the embedded JSON specification
     */
    parse() {
        // Find the embedded JSON schema
        const scriptElement = this.svgDoc.getElementById('modelt-schema');
        if (!scriptElement) {
            throw new Error('No modelt-schema found in SVG');
        }

        // Parse the JSON
        this.spec = JSON.parse(scriptElement.textContent);

        return this.spec;
    }

    /**
     * Get warehouse specification
     */
    getSpec() {
        return this.spec;
    }
}

/**
 * Convert ModelT specification to Babylon.js 3D scene
 * Supports both v1 (flat) and v2 (multi-slab) formats
 */
class ModelTBuilder {
    constructor(scene, spec) {
        this.scene = scene;
        this.spec = spec;
        this.meshes = {
            slabs: [],
            walls: [],
            partitionWalls: [],
            columns: [],
            doors: [],
            cameras: [],
            boxes: []
        };
    }

    /**
     * Build the entire facility
     */
    build() {
        // Detect v2 format (slabs array)
        const isV2 = this.spec.slabs && Array.isArray(this.spec.slabs);

        if (isV2) {
            // V2 format: process each slab
            this.spec.slabs.forEach(slab => {
                this.buildSlabV2(slab);
            });
        } else {
            // V1 format: single slab
            this.buildSlab();
            this.buildWalls();
            this.buildPartitionWalls();
            this.buildColumns();
            this.buildDoors();
            this.buildCameras();
        }

        return this.meshes;
    }

    /**
     * Build a single slab (v2 format)
     */
    buildSlabV2(slab) {
        const { id, name, elevation = 4, corners, variant, walls = [], columns = [], doors = [], cameras = [], boxes = [] } = slab;

        // Build slab footprint
        if (corners && corners.length > 0) {
            this.buildSlabMesh(corners, elevation, id);
        }

        // Build walls only if not a pavement slab
        // Pavement slabs (variant: "pavement") are outdoor surfaces with no walls
        if (variant !== 'pavement') {
            walls.forEach(wall => {
                if (wall.type === 'slabPerimeter' || wall.type === 'perimeter') {
                    this.buildWallMesh(wall, elevation, id, doors);
                } else if (wall.type === 'partition') {
                    this.buildPartitionWallMesh(wall, elevation, id, doors);
                }
            });
        }

        // Build columns
        columns.forEach(column => {
            this.buildColumnMesh(column, elevation, id);
        });

        // Build doors
        doors.forEach(door => {
            this.buildDoorMesh(door, elevation, id);
        });

        // Build cameras
        cameras.forEach(camera => {
            this.buildCameraMesh(camera, elevation, id);
        });

        // Build boxes
        boxes.forEach(box => {
            this.buildBoxMesh(box, elevation, id);
        });
    }

    /**
     * Convert SVG coordinates to Babylon.js
     * SVG: origin NW, X=East, Y=South
     * Babylon: X=East, Y=Up, Z=North (so -Z=South)
     */
    svgToBabylon(x, y, elevation = 0) {
        return new BABYLON.Vector3(x, elevation, -y);
    }

    /**
     * Build the slab from corners (v1 format - backward compatibility)
     */
    buildSlab() {
        if (!this.spec.slab || !this.spec.slab.corners) return;
        this.buildSlabMesh(this.spec.slab.corners, 4, "slab");
    }

    /**
     * Build a slab mesh from corners
     * Creates an extruded polygon for the floor
     */
    buildSlabMesh(corners, elevation, slabId) {
        const slabThickness = 4.0;  // 4 feet thick

        // Convert corners to Babylon Vector3 (XZ plane at Y=0)
        const shape = corners.map(corner =>
            new BABYLON.Vector3(corner.x, 0, -corner.y)
        );

        // Create extruded polygon for the slab
        // ExtrudePolygon extrudes DOWNWARD (negative Y direction) by default
        const slab = BABYLON.MeshBuilder.ExtrudePolygon(
            `slab_${slabId}`,
            {
                shape: shape,
                depth: slabThickness,
                sideOrientation: BABYLON.Mesh.DOUBLESIDE
            },
            this.scene
        );

        // Position at elevation so it extrudes down to (elevation - thickness)
        slab.position.y = elevation;

        const material = new BABYLON.StandardMaterial(`slabMat_${slabId}`, this.scene);
        material.diffuseColor = new BABYLON.Color3(0.5, 0.5, 0.55);  // Gray concrete
        material.specularColor = new BABYLON.Color3(0.2, 0.2, 0.2);
        slab.material = material;

        this.meshes.slabs.push(slab);
    }

    /**
     * Build a wall mesh for slabPerimeter or perimeter wall types (v2)
     * Creates walls with door cutouts
     */
    buildWallMesh(wall, slabElevation, slabId, doors = []) {
        if (!wall.corners) return;

        const corners = wall.corners;
        const slabTop = slabElevation;
        const wallHeight = 15.0;
        const wallThickness = 0.5;

        const wallMaterial = new BABYLON.StandardMaterial(`wallMat_${slabId}_${wall.id}`, this.scene);
        wallMaterial.diffuseColor = new BABYLON.Color3(0, 0.27, 0.62);

        // Filter doors that belong to this wall
        const wallDoors = doors.filter(door => door.wallId === wall.id);

        for (let i = 0; i < corners.length; i++) {
            const p1 = corners[i];
            const p2 = corners[(i + 1) % corners.length];

            const dx = p2.x - p1.x;
            const dy = p2.y - p1.y;
            const length = Math.sqrt(dx * dx + dy * dy);

            if (length < 0.1) continue;

            const isHorizontal = Math.abs(dy) < 0.1;
            const isVertical = Math.abs(dx) < 0.1;

            // Find doors on this wall segment
            const segmentDoors = this.findDoorsOnSegment(wallDoors, p1, p2, isHorizontal, isVertical);

            if (segmentDoors.length === 0) {
                // No doors - create simple box wall
                this.createSimpleWallSegment(slabId, wall.id, i, p1, p2, dx, dy, length,
                    isHorizontal, isVertical, wallHeight, wallThickness, slabTop, wallMaterial, 'walls');
            } else {
                // Has doors - create wall with cutouts
                this.createWallSegmentWithDoors(slabId, wall.id, i, p1, p2, dx, dy, length,
                    isHorizontal, isVertical, wallHeight, wallThickness, slabTop, segmentDoors, wallMaterial, 'walls');
            }
        }
    }

    /**
     * Find doors that are positioned on a specific wall segment
     */
    findDoorsOnSegment(doors, p1, p2, isHorizontal, isVertical) {
        const tolerance = 5; // 5 foot tolerance for door position matching
        const segmentDoors = [];

        for (const door of doors) {
            if (isHorizontal) {
                // Check if door is on this horizontal segment
                const onSegment = Math.abs(door.y - p1.y) < tolerance &&
                                door.x >= Math.min(p1.x, p2.x) - tolerance &&
                                door.x <= Math.max(p1.x, p2.x) + tolerance;
                if (onSegment) {
                    segmentDoors.push(door);
                }
            } else if (isVertical) {
                // Check if door is on this vertical segment
                const onSegment = Math.abs(door.x - p1.x) < tolerance &&
                                door.y >= Math.min(p1.y, p2.y) - tolerance &&
                                door.y <= Math.max(p1.y, p2.y) + tolerance;
                if (onSegment) {
                    segmentDoors.push(door);
                }
            }
        }

        return segmentDoors;
    }

    /**
     * Create a simple wall segment without doors
     */
    createSimpleWallSegment(slabId, wallId, segIndex, p1, p2, dx, dy, length,
                           isHorizontal, isVertical, wallHeight, wallThickness, slabTop, material, meshType = 'walls') {
        const wallCenterY = slabTop + wallHeight / 2;
        const centerX = (p1.x + p2.x) / 2;
        const centerZ = (p1.y + p2.y) / 2;

        let wallBox;
        if (isHorizontal) {
            wallBox = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${wallId}_seg${segIndex}`,
                { width: length, height: wallHeight, depth: wallThickness },
                this.scene
            );
            wallBox.position = this.svgToBabylon(centerX, centerZ, wallCenterY);
        } else if (isVertical) {
            wallBox = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${wallId}_seg${segIndex}`,
                { width: wallThickness, height: wallHeight, depth: length },
                this.scene
            );
            wallBox.position = this.svgToBabylon(centerX, centerZ, wallCenterY);
        } else {
            const angle = Math.atan2(dy, dx);
            wallBox = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${wallId}_seg${segIndex}`,
                { width: length, height: wallHeight, depth: wallThickness },
                this.scene
            );
            wallBox.position = this.svgToBabylon(centerX, centerZ, wallCenterY);
            wallBox.rotation.y = -angle;
        }

        wallBox.material = material;
        this.meshes[meshType].push(wallBox);
    }

    /**
     * Create a wall segment with door cutouts using ExtrudePolygon
     */
    createWallSegmentWithDoors(slabId, wallId, segIndex, p1, p2, dx, dy, length,
                               isHorizontal, isVertical, wallHeight, wallThickness, slabTop, doors, material, meshType = 'walls') {
        // Generate 2D polygon profile with door cutouts
        const profile = this.generateWallProfileWithDoors(p1, p2, length, wallHeight, doors, isHorizontal, isVertical);

        if (!profile || profile.length === 0) {
            // Fallback to simple wall if profile generation fails
            this.createSimpleWallSegment(slabId, wallId, segIndex, p1, p2, dx, dy, length,
                isHorizontal, isVertical, wallHeight, wallThickness, slabTop, material, meshType);
            return;
        }

        // Create the wall mesh by extruding the profile
        const wallMesh = BABYLON.MeshBuilder.ExtrudePolygon(
            `${slabId}_${wallId}_seg${segIndex}`,
            {
                shape: profile,
                depth: wallThickness,
                sideOrientation: BABYLON.Mesh.DOUBLESIDE
            },
            this.scene
        );

        // Position and orient to match simple walls
        if (isHorizontal) {
            // Horizontal wall: runs east-west
            wallMesh.rotation.x = -Math.PI / 2;

            if (dx > 0) {
                // Going EAST (positive X direction)
                // Profile goes from 0 to length in +X direction
                wallMesh.position.x = p1.x;
                wallMesh.position.y = slabTop;
                wallMesh.position.z = -p1.y;
            } else {
                // Going WEST (negative X direction)
                // Need to flip the wall 180° around Y axis and position at p1
                wallMesh.rotation.y = Math.PI;
                wallMesh.position.x = p1.x;
                wallMesh.position.y = slabTop;
                wallMesh.position.z = -p1.y;
            }

        } else if (isVertical) {
            // Vertical wall: runs north-south
            wallMesh.rotation.x = -Math.PI / 2;

            if (dy > 0) {
                // Going SOUTH (positive Y direction in SVG, negative Z in Babylon)
                wallMesh.rotation.y = Math.PI / 2;
                wallMesh.position.x = p1.x;
                wallMesh.position.y = slabTop;
                wallMesh.position.z = -p1.y;
            } else {
                // Going NORTH (negative Y direction in SVG, positive Z in Babylon)
                // Need to flip
                wallMesh.rotation.y = -Math.PI / 2;
                wallMesh.position.x = p1.x;
                wallMesh.position.y = slabTop;
                wallMesh.position.z = -p1.y;
            }
        }

        wallMesh.material = material;
        this.meshes[meshType].push(wallMesh);
    }

    /**
     * Generate a 2D polygon profile for a wall with door cutouts
     * Returns an array of Vector3 points representing the wall cross-section
     * Traces a closed outline like drawing on a chalkboard
     * Profile is in XZ plane: X = horizontal length, Z = vertical height
     */
    generateWallProfileWithDoors(p1, p2, segmentLength, wallHeight, doors, isHorizontal, isVertical) {
        const doorHeight = 10.0; // Standard door height
        const profile = [];

        // Sort doors by absolute position along the segment (0 to length)
        const sortedDoors = doors.slice().sort((a, b) => {
            const posA = isHorizontal ? Math.abs(a.x - p1.x) : Math.abs(a.y - p1.y);
            const posB = isHorizontal ? Math.abs(b.x - p1.x) : Math.abs(b.y - p1.y);
            return posA - posB;
        });

        // Start at bottom left (0, 0, 0) - using XZ plane where X=length, Z=height
        profile.push(new BABYLON.Vector3(0, 0, 0));

        // Trace along the bottom, up and over doors, then along the top
        let currentPos = 0;

        for (const door of sortedDoors) {
            // Calculate door position and width along the segment
            // Use absolute distance along the segment (always positive)
            const doorPos = isHorizontal ? Math.abs(door.x - p1.x) : Math.abs(door.y - p1.y);
            const doorWidth = door.bayWidth || 10;
            const doorStart = doorPos - doorWidth / 2;
            const doorEnd = doorPos + doorWidth / 2;

            // If there's wall before this door, add bottom segment
            if (doorStart > currentPos + 0.1) {
                // Move right along floor to door
                profile.push(new BABYLON.Vector3(doorStart, 0, 0));
            }

            // Go up to door height (use Z for height)
            profile.push(new BABYLON.Vector3(doorStart, 0, doorHeight));

            // Go right across top of door opening
            profile.push(new BABYLON.Vector3(doorEnd, 0, doorHeight));

            // Go back down to floor level
            profile.push(new BABYLON.Vector3(doorEnd, 0, 0));

            currentPos = doorEnd;
        }

        // Complete the bottom edge to the end
        profile.push(new BABYLON.Vector3(segmentLength, 0, 0));

        // Go up the right side to full wall height
        profile.push(new BABYLON.Vector3(segmentLength, 0, wallHeight));

        // Trace back along the top
        profile.push(new BABYLON.Vector3(0, 0, wallHeight));

        // Back to start (closes the polygon)
        profile.push(new BABYLON.Vector3(0, 0, 0));

        return profile;
    }

    /**
     * Build a partition wall mesh (v2)
     * Uses same ExtrudePolygon approach as perimeter walls
     */
    buildPartitionWallMesh(wall, slabElevation, slabId, doors = []) {
        const slabTop = slabElevation;
        const wallHeight = 15.0;
        const wallThickness = 0.5;

        const partitionMaterial = new BABYLON.StandardMaterial(`partitionMat_${slabId}_${wall.id}`, this.scene);
        partitionMaterial.diffuseColor = new BABYLON.Color3(0, 0.27, 0.62);

        // Filter doors that belong to this wall
        // Match either "beatrice" or "mercury_beatrice" format
        const wallDoors = doors.filter(d =>
            d.wallId === wall.id ||
            d.wallId === `${slabId}_${wall.id}`
        );

        let currentX = wall.start.x;
        let currentY = wall.start.y;

        wall.segments.forEach((segment, segIdx) => {
            const length = segment.length;
            const direction = segment.direction;

            // Calculate segment endpoints
            let p1 = { x: currentX, y: currentY };
            let p2 = { x: currentX, y: currentY };

            switch (direction) {
                case 'east':
                    p2.x = currentX + length;
                    break;
                case 'west':
                    p2.x = currentX - length;
                    break;
                case 'south':
                    p2.y = currentY + length;
                    break;
                case 'north':
                    p2.y = currentY - length;
                    break;
            }

            const dx = p2.x - p1.x;
            const dy = p2.y - p1.y;
            const isHorizontal = Math.abs(dy) < 0.1;
            const isVertical = Math.abs(dx) < 0.1;

            // Find doors on this segment
            const segmentDoors = this.findDoorsOnSegment(wallDoors, p1, p2, isHorizontal, isVertical);

            if (segmentDoors.length === 0) {
                // No doors - create simple box wall
                this.createSimplePartitionSegment(slabId, wall.id, segIdx, p1, p2, dx, dy, length,
                    isHorizontal, isVertical, wallHeight, wallThickness, slabTop, partitionMaterial);
            } else {
                // Has doors - create wall with cutouts using same method as perimeter walls
                this.createWallSegmentWithDoors(slabId, wall.id, segIdx, p1, p2, dx, dy, length,
                    isHorizontal, isVertical, wallHeight, wallThickness, slabTop, segmentDoors, partitionMaterial, 'partitionWalls');
            }

            // Update current position for next segment
            currentX = p2.x;
            currentY = p2.y;
        });
    }

    /**
     * Create a simple partition wall segment without doors
     */
    createSimplePartitionSegment(slabId, wallId, segIndex, p1, p2, dx, dy, length,
                                 isHorizontal, isVertical, wallHeight, wallThickness, slabTop, material) {
        const wallCenterY = slabTop + wallHeight / 2;
        const centerX = (p1.x + p2.x) / 2;
        const centerZ = (p1.y + p2.y) / 2;

        let wallBox;
        if (isHorizontal) {
            wallBox = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${wallId}_seg${segIndex}`,
                { width: length, height: wallHeight, depth: wallThickness },
                this.scene
            );
            wallBox.position = this.svgToBabylon(centerX, centerZ, wallCenterY);
        } else if (isVertical) {
            wallBox = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${wallId}_seg${segIndex}`,
                { width: wallThickness, height: wallHeight, depth: length },
                this.scene
            );
            wallBox.position = this.svgToBabylon(centerX, centerZ, wallCenterY);
        } else {
            const angle = Math.atan2(dy, dx);
            wallBox = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${wallId}_seg${segIndex}`,
                { width: length, height: wallHeight, depth: wallThickness },
                this.scene
            );
            wallBox.position = this.svgToBabylon(centerX, centerZ, wallCenterY);
            wallBox.rotation.y = -angle;
        }

        wallBox.material = material;
        this.meshes.partitionWalls.push(wallBox);
    }

    /**
     * Build a column mesh (v2)
     */
    buildColumnMesh(column, slabElevation, slabId) {
        const slabTop = slabElevation;
        const height = column.height || 15;
        const size = column.size || 1;
        const columnBase = slabTop;
        const columnCenterY = columnBase + height / 2;

        const columnMaterial = new BABYLON.StandardMaterial(`columnMat_${slabId}_${column.id}`, this.scene);
        columnMaterial.diffuseColor = new BABYLON.Color3(0.29, 0.29, 0.29);

        const columnMesh = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${column.id}`,
            { width: size, height: height, depth: size },
            this.scene
        );

        columnMesh.position = this.svgToBabylon(
            column.x,
            column.y,
            columnCenterY
        );

        columnMesh.material = columnMaterial;

        columnMesh.metadata = {
            name: column.name,
            location: column.location,
            type: 'column',
            slabId: slabId
        };

        this.meshes.columns.push(columnMesh);
    }

    /**
     * Build a door mesh (v2)
     */
    buildDoorMesh(door, slabElevation, slabId) {
        const slabTop = slabElevation;
        const doorHeight = 10.0;
        const doorBase = slabTop;
        const doorCenterY = doorBase + doorHeight / 2;

        const doorMaterial = new BABYLON.StandardMaterial(`doorMat_${slabId}_${door.id}`, this.scene);
        doorMaterial.diffuseColor = new BABYLON.Color3(0.6, 0.4, 0.2);
        doorMaterial.alpha = 0.2;  // More transparent (was 0.5)

        // Interior doors use 'width' field, exterior use 'bayWidth'
        const bayWidth = door.bayWidth || door.width || 10;
        const doorWidth = door.doorWidth ? (door.doorWidth / 12) : (bayWidth * 0.9);

        const doorFrame = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}`,
            { width: bayWidth, height: doorHeight, depth: 0.2 },
            this.scene
        );

        doorFrame.position = this.svgToBabylon(
            door.x,
            door.y,
            doorCenterY
        );

        if (door.orientation === 'vertical') {
            doorFrame.rotation.y = Math.PI / 2;
        }

        doorFrame.material = doorMaterial;

        doorFrame.metadata = {
            type: door.type,
            facing: door.facing,
            bayWidth: bayWidth,
            slabId: slabId,
            portal: door.portal || null
        };

        this.meshes.doors.push(doorFrame);
    }

    /**
     * Build a Gaylord box mesh (v2)
     * Creates low-poly octagonal box: 48" x 40" x 24" (4ft x 3.33ft x 2ft)
     * Octagon has 4 chamfered corners (1ft each side)
     */
    buildBoxMesh(box, slabElevation, slabId) {
        const slabTop = slabElevation;
        const boxWidth = box.width || 4;      // 48" = 4ft
        const boxDepth = box.depth || 3.33;   // 40" = 3.33ft
        const boxHeight = box.height || 2;    // 24" = 2ft
        const chamfer = 1;                     // 1ft chamfer on corners
        const boxBase = slabTop;
        const boxCenterY = boxBase + boxHeight / 2;

        // Create octagonal profile (top-down view)
        // Starting from top-left, going clockwise
        const halfWidth = boxWidth / 2;
        const halfDepth = boxDepth / 2;

        const profile = [
            new BABYLON.Vector3(-halfWidth + chamfer, 0, halfDepth),      // Top edge, left of chamfer
            new BABYLON.Vector3(halfWidth - chamfer, 0, halfDepth),       // Top edge, right of chamfer
            new BABYLON.Vector3(halfWidth, 0, halfDepth - chamfer),       // Top-right chamfer
            new BABYLON.Vector3(halfWidth, 0, -halfDepth + chamfer),      // Right edge, top of chamfer
            new BABYLON.Vector3(halfWidth - chamfer, 0, -halfDepth),      // Bottom edge, right of chamfer
            new BABYLON.Vector3(-halfWidth + chamfer, 0, -halfDepth),     // Bottom edge, left of chamfer
            new BABYLON.Vector3(-halfWidth, 0, -halfDepth + chamfer),     // Bottom-left chamfer
            new BABYLON.Vector3(-halfWidth, 0, halfDepth - chamfer)       // Left edge, bottom of chamfer
        ];

        // Extrude the octagon upward to create the box
        // NOTE: ExtrudePolygon extrudes along -Y by default, so we need to flip it
        const boxMesh = BABYLON.MeshBuilder.ExtrudePolygon(
            `${slabId}_${box.id}`,
            {
                shape: profile,
                depth: boxHeight,
                sideOrientation: BABYLON.Mesh.DOUBLESIDE
            },
            this.scene
        );

        // Position the box on the slab
        // ExtrudePolygon creates mesh with extrusion going DOWN (-Y)
        // So position at top of box and it extrudes down to slab surface
        boxMesh.position = this.svgToBabylon(
            box.x,
            box.y,
            boxBase + boxHeight  // Position at TOP, extrudes down
        );

        // Rotate 180° around X to flip upside-up (extrusion was going down)
        boxMesh.rotation.x = Math.PI;

        // Rotate around Y for orientation if specified
        if (box.orientation) {
            boxMesh.rotation.y = box.orientation * Math.PI / 180;
        }

        // Cardboard material
        const boxMaterial = new BABYLON.StandardMaterial(`boxMat_${slabId}_${box.id}`, this.scene);
        boxMaterial.diffuseColor = new BABYLON.Color3(0.65, 0.5, 0.35);  // Cardboard brown
        boxMaterial.specularColor = new BABYLON.Color3(0.1, 0.1, 0.1);   // Low shininess
        boxMesh.material = boxMaterial;

        boxMesh.metadata = {
            type: box.type,
            id: box.id,
            slabId: slabId
        };

        this.meshes.boxes.push(boxMesh);
    }

    /**
     * Build a camera mesh (v2)
     */
    buildCameraMesh(camera, slabElevation, slabId) {
        const slabTop = slabElevation;
        const elevation = camera.elevation || 12;
        const cameraY = slabTop + elevation;
        const direction = camera.direction || 0;
        const tilt = camera.tilt || 30;
        const viewingAngle = camera.viewingAngle || 90;
        const range = camera.range || 50;

        const cameraMaterial = new BABYLON.StandardMaterial(`cameraMat_${slabId}_${camera.id}`, this.scene);
        cameraMaterial.diffuseColor = new BABYLON.Color3(1, 0, 0);
        cameraMaterial.emissiveColor = new BABYLON.Color3(0.5, 0, 0);

        const cameraBody = BABYLON.MeshBuilder.CreateSphere(
            `${slabId}_${camera.id}`,
            { diameter: 0.5 },
            this.scene
        );

        cameraBody.position = this.svgToBabylon(
            camera.x,
            camera.y,
            cameraY
        );

        cameraBody.material = cameraMaterial;

        cameraBody.metadata = {
            name: camera.name,
            number: camera.number,
            direction: direction,
            tilt: tilt,
            viewingAngle: viewingAngle,
            type: 'camera',
            slabId: slabId
        };

        this.meshes.cameras.push(cameraBody);

        // Create viewing frustum with tilt
        const tiltRad = tilt * Math.PI / 180;

        // Direction conversion - match the camera view direction
        // Direction: 0=North(-Z), 90=East(+X), 180=South(+Z), 270=West(-X)
        // We need to convert to SVG coordinate deltas
        const dirRad = -direction * Math.PI / 180;  // Negative to match camera rotation

        // SIMPLE: Just use tilt directly to calculate end point
        // Start at camera position (camera.x, camera.y, cameraY)
        // End at a point 'range' distance away, angled down by 'tilt'

        const horizontalDistance = range * Math.cos(tiltRad);
        const verticalDistance = range * Math.sin(tiltRad);

        // Horizontal position in SVG coordinates
        // Sin/Cos because we're in SVG top-down view where Y goes south
        const endX = camera.x + horizontalDistance * Math.sin(dirRad);
        const endY = camera.y - horizontalDistance * Math.cos(dirRad);  // Negative because north is negative Y

        // Vertical position - going DOWN from camera
        const endZ = cameraY - verticalDistance;

        const startPoint = this.svgToBabylon(camera.x, camera.y, cameraY);
        const endPoint = this.svgToBabylon(endX, endY, endZ);

        const frustumLine = BABYLON.MeshBuilder.CreateLines(
            `${slabId}_${camera.id}_frustum`,
            {
                points: [startPoint, endPoint]
            },
            this.scene
        );

        frustumLine.color = new BABYLON.Color3(1, 0, 0);
        this.meshes.cameras.push(frustumLine);

        // Perform raycasting to find intersection point
        const cameraPos = this.svgToBabylon(camera.x, camera.y, cameraY);
        const directionRad = -direction * Math.PI / 180;  // NEGATIVE to match frustum line!
        const tiltRadians = tilt * Math.PI / 180;

        // Calculate direction vector (accounting for tilt)
        const horizontalDist = Math.cos(tiltRadians);
        const verticalDist = -Math.sin(tiltRadians); // Negative because tilting down

        const rayDirection = new BABYLON.Vector3(
            horizontalDist * Math.sin(directionRad),
            verticalDist,
            horizontalDist * Math.cos(directionRad)  // Positive cos to match frustum
        );

        // Create ray from camera position
        const ray = new BABYLON.Ray(cameraPos, rayDirection, range);

        // Perform raycast picking
        const hit = this.scene.pickWithRay(ray, (mesh) => {
            // Only pick walls (segments), doors, slabs (not cameras, lines, etc.)
            const isPick = mesh.name && (
                mesh.name.includes('_seg') ||  // Wall segments like mercury_mercury_perimeter_seg0
                mesh.name.includes('door') ||
                mesh.name.includes('slab') ||
                mesh.name.includes('partition')
            );
            return isPick;
        });

        // If hit, create a bullseye marker at the intersection point
        if (hit && hit.hit) {
            // Create bullseye disc
            const bullseye = BABYLON.MeshBuilder.CreateDisc(
                `${slabId}_${camera.id}_target`,
                { radius: 1.5, tessellation: 32 },
                this.scene
            );

            bullseye.position = hit.pickedPoint;

            // Orient the disc to face the camera
            const normal = hit.getNormal(true);
            if (normal) {
                bullseye.lookAt(cameraPos);
            }

            // Create bullseye material
            const bullseyeMaterial = new BABYLON.StandardMaterial(`targetMat_${slabId}_${camera.id}`, this.scene);
            bullseyeMaterial.diffuseColor = new BABYLON.Color3(1, 1, 0); // Yellow
            bullseyeMaterial.emissiveColor = new BABYLON.Color3(0.5, 0.5, 0); // Glowing yellow
            bullseyeMaterial.alpha = 0.8;
            bullseye.material = bullseyeMaterial;

            // Create inner red circle for center of bullseye
            const center = BABYLON.MeshBuilder.CreateDisc(
                `${slabId}_${camera.id}_target_center`,
                { radius: 0.5, tessellation: 32 },
                this.scene
            );

            center.position = hit.pickedPoint.add(normal ? normal.scale(0.01) : new BABYLON.Vector3(0, 0.01, 0));

            if (normal) {
                center.lookAt(cameraPos);
            }

            const centerMaterial = new BABYLON.StandardMaterial(`targetCenterMat_${slabId}_${camera.id}`, this.scene);
            centerMaterial.diffuseColor = new BABYLON.Color3(1, 0, 0); // Red
            centerMaterial.emissiveColor = new BABYLON.Color3(0.5, 0, 0); // Glowing red
            center.material = centerMaterial;

            this.meshes.cameras.push(bullseye);
            this.meshes.cameras.push(center);
        }
    }

    /**
     * Build perimeter walls from corners
     */
    buildWalls() {
        if (!this.spec.walls || !this.spec.walls.corners) return;

        const corners = this.spec.walls.corners;
        const slabTop = 4.0;  // Top of slab (Y=4)
        const wallHeight = 15.0;  // 15 feet high
        const wallThickness = 0.5;  // 6 inches thick
        const wallBase = slabTop;  // Wall bottom sits on slab top
        const wallCenterY = wallBase + wallHeight / 2;  // Center Y position

        const wallMaterial = new BABYLON.StandardMaterial("wallMat", this.scene);
        wallMaterial.diffuseColor = new BABYLON.Color3(0, 0.27, 0.62);  // #00449e blue

        // Create wall segments between consecutive corners
        for (let i = 0; i < corners.length; i++) {
            const p1 = corners[i];
            const p2 = corners[(i + 1) % corners.length];

            // Calculate segment length and angle
            const dx = p2.x - p1.x;
            const dy = p2.y - p1.y;
            const length = Math.sqrt(dx * dx + dy * dy);

            if (length < 0.1) continue;  // Skip tiny segments

            // Determine if horizontal or vertical
            const isHorizontal = Math.abs(dy) < 0.1;
            const isVertical = Math.abs(dx) < 0.1;

            // Calculate center point in XZ plane (works for any direction)
            const centerX = (p1.x + p2.x) / 2;
            const centerZ = (p1.y + p2.y) / 2;

            let wallBox;
            if (isHorizontal) {
                // Horizontal wall (runs east-west)
                wallBox = BABYLON.MeshBuilder.CreateBox(
                    `wall_${i}`,
                    { width: length, height: wallHeight, depth: wallThickness },
                    this.scene
                );
                wallBox.position = this.svgToBabylon(centerX, centerZ, wallCenterY);
            } else if (isVertical) {
                // Vertical wall (runs north-south)
                wallBox = BABYLON.MeshBuilder.CreateBox(
                    `wall_${i}`,
                    { width: wallThickness, height: wallHeight, depth: length },
                    this.scene
                );
                wallBox.position = this.svgToBabylon(centerX, centerZ, wallCenterY);
            } else {
                // Angled wall - use rotation
                const angle = Math.atan2(dy, dx);
                wallBox = BABYLON.MeshBuilder.CreateBox(
                    `wall_${i}`,
                    { width: length, height: wallHeight, depth: wallThickness },
                    this.scene
                );
                wallBox.position = this.svgToBabylon(centerX, centerZ, wallCenterY);
                wallBox.rotation.y = -angle;  // Rotate around Y axis
            }

            wallBox.material = wallMaterial;
            this.meshes.walls.push(wallBox);
        }
    }

    /**
     * Build partition walls using turtle graphics
     */
    buildPartitionWalls() {
        if (!this.spec.partitionWalls) return;

        const slabTop = 4.0;  // Top of slab (Y=4)
        const wallHeight = 10.0;  // Partition walls are shorter
        const wallThickness = 0.5;
        const wallBase = slabTop;  // Wall bottom sits on slab top
        const wallCenterY = wallBase + wallHeight / 2;  // Center Y position

        const partitionMaterial = new BABYLON.StandardMaterial("partitionMat", this.scene);
        partitionMaterial.diffuseColor = new BABYLON.Color3(0, 0.27, 0.62);  // Same as perimeter walls

        this.spec.partitionWalls.forEach(wall => {
            let currentX = wall.start.x;
            let currentY = wall.start.y;

            wall.segments.forEach((segment, segIdx) => {
                const length = segment.length;
                let wallBox;

                switch (segment.direction) {
                    case 'east':
                        wallBox = BABYLON.MeshBuilder.CreateBox(
                            `${wall.id}_${segIdx}`,
                            { width: length, height: wallHeight, depth: wallThickness },
                            this.scene
                        );
                        wallBox.position = this.svgToBabylon(
                            currentX + length / 2,
                            currentY,
                            wallCenterY
                        );
                        currentX += length;
                        break;

                    case 'west':
                        wallBox = BABYLON.MeshBuilder.CreateBox(
                            `${wall.id}_${segIdx}`,
                            { width: length, height: wallHeight, depth: wallThickness },
                            this.scene
                        );
                        wallBox.position = this.svgToBabylon(
                            currentX - length / 2,
                            currentY,
                            wallCenterY
                        );
                        currentX -= length;
                        break;

                    case 'south':
                        wallBox = BABYLON.MeshBuilder.CreateBox(
                            `${wall.id}_${segIdx}`,
                            { width: wallThickness, height: wallHeight, depth: length },
                            this.scene
                        );
                        wallBox.position = this.svgToBabylon(
                            currentX,
                            currentY + length / 2,
                            wallCenterY
                        );
                        currentY += length;
                        break;

                    case 'north':
                        wallBox = BABYLON.MeshBuilder.CreateBox(
                            `${wall.id}_${segIdx}`,
                            { width: wallThickness, height: wallHeight, depth: length },
                            this.scene
                        );
                        wallBox.position = this.svgToBabylon(
                            currentX,
                            currentY - length / 2,
                            wallCenterY
                        );
                        currentY -= length;
                        break;
                }

                if (wallBox) {
                    wallBox.material = partitionMaterial;
                    this.meshes.partitionWalls.push(wallBox);
                }
            });
        });
    }

    /**
     * Build structural columns (H-beams)
     */
    buildColumns() {
        if (!this.spec.columns) return;

        const slabTop = 4.0;  // Top of slab (Y=4)
        const columnMaterial = new BABYLON.StandardMaterial("columnMat", this.scene);
        columnMaterial.diffuseColor = new BABYLON.Color3(0.29, 0.29, 0.29);  // Dark gray

        this.spec.columns.forEach(column => {
            const height = column.height || 15;
            const size = column.size || 1;
            const columnBase = slabTop;  // Column bottom sits on slab top
            const columnCenterY = columnBase + height / 2;  // Center Y position

            // Create simple box for now (could be improved to H-beam shape)
            const columnMesh = BABYLON.MeshBuilder.CreateBox(
                column.id,
                { width: size, height: height, depth: size },
                this.scene
            );

            columnMesh.position = this.svgToBabylon(
                column.x,
                column.y,
                columnCenterY
            );

            columnMesh.material = columnMaterial;

            // Store metadata
            columnMesh.metadata = {
                name: column.name,
                location: column.location,
                type: 'column'
            };

            this.meshes.columns.push(columnMesh);
        });
    }

    /**
     * Build doors (openings in walls)
     */
    buildDoors() {
        if (!this.spec.doors) return;

        const slabTop = 4.0;  // Top of slab (Y=4)
        const doorHeight = 10.0;  // Standard door height
        const doorBase = slabTop;  // Door bottom sits on slab top
        const doorCenterY = doorBase + doorHeight / 2;  // Center Y position

        const doorMaterial = new BABYLON.StandardMaterial("doorMat", this.scene);
        doorMaterial.diffuseColor = new BABYLON.Color3(0.6, 0.4, 0.2);  // Brown
        doorMaterial.alpha = 0.2;  // More transparent (was 0.5)

        this.spec.doors.forEach(door => {
            // Interior doors use 'width' field, exterior use 'bayWidth'
            const bayWidth = door.bayWidth || door.width || 10;
            const doorWidth = door.doorWidth ? (door.doorWidth / 12) : (bayWidth * 0.9);  // Convert inches to feet, or 90% of bay

            // Create door frame outline
            const doorFrame = BABYLON.MeshBuilder.CreateBox(
                door.id,
                { width: bayWidth, height: doorHeight, depth: 0.2 },
                this.scene
            );

            // Position based on orientation
            doorFrame.position = this.svgToBabylon(
                door.x,
                door.y,
                doorCenterY
            );

            // Rotate doors on N-S walls (vertical orientation in SVG)
            if (door.orientation === 'vertical') {
                // Door on N-S wall (vertical in SVG) - needs 90° rotation in 3D
                doorFrame.rotation.y = Math.PI / 2;
            }
            // Horizontal orientation doors on E-W walls need no rotation

            doorFrame.material = doorMaterial;

            // Store metadata
            doorFrame.metadata = {
                type: door.type,
                facing: door.facing,
                bayWidth: bayWidth
            };

            this.meshes.doors.push(doorFrame);
        });
    }

    /**
     * Build cameras with viewing frustums
     */
    buildCameras() {
        if (!this.spec.cameras) return;

        const slabTop = 4.0;  // Top of slab (Y=4)

        const cameraMaterial = new BABYLON.StandardMaterial("cameraMat", this.scene);
        cameraMaterial.diffuseColor = new BABYLON.Color3(1, 0, 0);  // Red
        cameraMaterial.emissiveColor = new BABYLON.Color3(0.5, 0, 0);

        const frustumMaterial = new BABYLON.StandardMaterial("frustumMat", this.scene);
        frustumMaterial.diffuseColor = new BABYLON.Color3(1, 0, 0);
        frustumMaterial.alpha = 0.1;
        frustumMaterial.wireframe = true;

        this.spec.cameras.forEach(camera => {
            const elevation = camera.elevation || 12;  // Height above slab top
            const cameraY = slabTop + elevation;  // Absolute Y position
            const direction = camera.direction || 0;  // 0=N, 90=E, 180=S, 270=W
            const tilt = camera.tilt || 30;
            const viewingAngle = camera.viewingAngle || 90;
            const range = camera.range || 50;

            // Create camera body (small sphere)
            const cameraBody = BABYLON.MeshBuilder.CreateSphere(
                camera.id,
                { diameter: 0.5 },
                this.scene
            );

            cameraBody.position = this.svgToBabylon(
                camera.x,
                camera.y,
                cameraY
            );

            cameraBody.material = cameraMaterial;

            // Store metadata
            cameraBody.metadata = {
                name: camera.name,
                number: camera.number,
                direction: direction,
                tilt: tilt,
                viewingAngle: viewingAngle,
                type: 'camera'
            };

            this.meshes.cameras.push(cameraBody);

            // Create viewing frustum (simplified cone)
            const tiltRad = tilt * Math.PI / 180;
            const effectiveRange = elevation > 0 && tilt > 0
                ? Math.min(range, elevation / Math.tan(tiltRad))
                : range;

            // Convert direction to radians (0°=North=-Z, 90°=East=+X)
            const dirRad = (direction - 90) * Math.PI / 180;

            // Create direction line
            const endX = camera.x + effectiveRange * Math.cos(dirRad);
            const endY = camera.y + effectiveRange * Math.sin(dirRad);

            const dirLine = BABYLON.MeshBuilder.CreateLines(
                `${camera.id}_dir`,
                {
                    points: [
                        this.svgToBabylon(camera.x, camera.y, cameraY),
                        this.svgToBabylon(endX, endY, slabTop)
                    ]
                },
                this.scene
            );
            dirLine.color = new BABYLON.Color3(1, 0, 0);
            this.meshes.cameras.push(dirLine);
        });
    }
}

// Legacy compatibility - keep old classes for existing SVGs
class WarehouseParser {
    constructor(svgDoc) {
        this.svgDoc = svgDoc;
        this.components = {
            slab: null,
            walls: [],
            racks: [],
            coolers: [],
            centerLines: [],
            intersections: []
        };
    }

    parse() {
        this.parseSlab();
        this.parseWalls();
        this.parseRacks();
        this.parseCoolers();
        this.parseCenterLines();
        this.parseIntersections();
        return this.components;
    }

    parseSlab() {
        const slabElement = this.svgDoc.getElementById('slab');
        if (slabElement) {
            this.components.slab = {
                id: 'slab',
                x: parseFloat(slabElement.getAttribute('x')),
                y: parseFloat(slabElement.getAttribute('y')),
                width: parseFloat(slabElement.getAttribute('width')),
                height: parseFloat(slabElement.getAttribute('height')),
                type: 'slab'
            };
        }
    }

    parseWalls() {
        const wallsGroup = this.svgDoc.getElementById('walls');
        if (!wallsGroup) return;
        const wallGroups = wallsGroup.querySelectorAll('g[id^="g"]');
        wallGroups.forEach(group => {
            const wall = this.parseWallGroup(group);
            if (wall) this.components.walls.push(wall);
        });
    }

    parseWallGroup(group) {
        const id = group.getAttribute('id');
        const transform = group.getAttribute('transform');
        const matrix = this.parseTransform(transform);
        if (!matrix) return null;

        const rects = group.querySelectorAll('rect');
        const components = [];

        rects.forEach(rect => {
            const x = parseFloat(rect.getAttribute('x'));
            const y = parseFloat(rect.getAttribute('y'));
            const width = parseFloat(rect.getAttribute('width'));
            const height = parseFloat(rect.getAttribute('height'));
            const transformed = this.applyMatrix(matrix, x, y);
            components.push({
                rectId: rect.getAttribute('id'),
                x: transformed.x,
                y: transformed.y,
                width: width,
                height: height,
                originalX: x,
                originalY: y
            });
        });

        return { id, transform, matrix, components, type: 'wall' };
    }

    parseTransform(transformStr) {
        if (!transformStr) return null;

        const matrixMatch = transformStr.match(/matrix\(([^)]+)\)/);
        if (matrixMatch) {
            const values = matrixMatch[1].split(/[\s,]+/).map(parseFloat);
            return {
                a: values[0], b: values[1],
                c: values[2], d: values[3],
                e: values[4], f: values[5]
            };
        }

        const translateMatch = transformStr.match(/translate\(([^)]+)\)/);
        if (translateMatch) {
            const values = translateMatch[1].split(/[\s,]+/).map(parseFloat);
            return {
                a: 1, b: 0,
                c: 0, d: 1,
                e: values[0] || 0,
                f: values[1] || 0
            };
        }

        return null;
    }

    applyMatrix(matrix, x, y) {
        return {
            x: matrix.a * x + matrix.c * y + matrix.e,
            y: matrix.b * x + matrix.d * y + matrix.f
        };
    }

    parseRacks() {
        const rackElements = this.svgDoc.querySelectorAll('rect[id^="rack_"]');
        rackElements.forEach(rect => {
            const id = rect.getAttribute('id');
            const parts = id.split('_');
            this.components.racks.push({
                id, name: parts[1],
                accessDirection: parts[2],
                location: parts[3],
                x: parseFloat(rect.getAttribute('x')),
                y: parseFloat(rect.getAttribute('y')),
                width: parseFloat(rect.getAttribute('width')),
                height: parseFloat(rect.getAttribute('height')),
                type: 'rack'
            });
        });
    }

    parseCoolers() {
        const coolerElements = this.svgDoc.querySelectorAll('rect[id^="cooler_"]');
        coolerElements.forEach(rect => {
            this.components.coolers.push({
                id: rect.getAttribute('id'),
                x: parseFloat(rect.getAttribute('x')),
                y: parseFloat(rect.getAttribute('y')),
                width: parseFloat(rect.getAttribute('width')),
                height: parseFloat(rect.getAttribute('height')),
                type: 'cooler'
            });
        });
    }

    parseCenterLines() {
        const lineElements = this.svgDoc.querySelectorAll('line[id^="centerLine_"]');
        lineElements.forEach(line => {
            this.components.centerLines.push({
                id: line.getAttribute('id'),
                x1: parseFloat(line.getAttribute('x1')),
                y1: parseFloat(line.getAttribute('y1')),
                x2: parseFloat(line.getAttribute('x2')),
                y2: parseFloat(line.getAttribute('y2')),
                type: 'centerLine'
            });
        });
    }

    parseIntersections() {
        const intElements = this.svgDoc.querySelectorAll('rect[id^="Int_"]');
        intElements.forEach(rect => {
            const id = rect.getAttribute('id');
            const parts = id.split('_');
            this.components.intersections.push({
                id, line1: parts[1], line2: parts[2],
                x: parseFloat(rect.getAttribute('x')),
                y: parseFloat(rect.getAttribute('y')),
                width: parseFloat(rect.getAttribute('width')),
                height: parseFloat(rect.getAttribute('height')),
                type: 'intersection'
            });
        });
    }

    svgToBabylon(x, y) {
        return { x: x, z: -y };
    }
}

class WarehouseBuilder {
    constructor(scene, warehouseData) {
        this.scene = scene;
        this.data = warehouseData;
        this.meshes = { slab: null, walls: [], racks: [], coolers: [] };
    }

    build() {
        this.buildSlab();
        this.buildWalls();
        this.buildRacks();
        this.buildCoolers();
        this.buildCenterLines();
        this.buildIntersections();
        return this.meshes;
    }

    buildSlab() {
        if (!this.data.slab) return;
        const slab = this.data.slab;
        const slabThickness = 4.0;
        const centerX = slab.x + slab.width / 2;
        const centerY = slabThickness / 2;
        const centerZ = -(slab.y + slab.height / 2);

        const slabMesh = BABYLON.MeshBuilder.CreateBox("slab", {
            width: slab.width,
            height: slabThickness,
            depth: slab.height
        }, this.scene);

        slabMesh.position = new BABYLON.Vector3(centerX, centerY, centerZ);
        const material = new BABYLON.StandardMaterial("slabMat", this.scene);
        material.diffuseColor = new BABYLON.Color3(0.5, 0.5, 0.55);
        material.specularColor = new BABYLON.Color3(0.2, 0.2, 0.2);
        slabMesh.material = material;
        this.meshes.slab = slabMesh;
    }

    buildWalls() {
        const slabElevation = 4.0;
        const wallHeight = 10;
        const wallMaterial = new BABYLON.StandardMaterial("wallMat", this.scene);
        wallMaterial.diffuseColor = new BABYLON.Color3(0, 0.27, 0.62);

        this.data.walls.forEach((wall, index) => {
            wall.components.forEach((component, compIndex) => {
                const pos = this.svgToBabylon(component.x, component.y);
                const wallMesh = BABYLON.MeshBuilder.CreateBox(
                    `wall_${index}_${compIndex}`,
                    { width: component.width, height: wallHeight, depth: component.height },
                    this.scene
                );
                wallMesh.position = new BABYLON.Vector3(
                    pos.x + component.width / 2,
                    slabElevation + wallHeight / 2,
                    pos.z - component.height / 2
                );
                wallMesh.material = wallMaterial;
                this.meshes.walls.push(wallMesh);
            });
        });
    }

    buildRacks() {
        const slabElevation = 4.0;
        const rackHeight = 1 / 12;
        const rackMaterial = new BABYLON.StandardMaterial("rackMat", this.scene);
        rackMaterial.diffuseColor = new BABYLON.Color3(0.8, 0.6, 0.2);
        rackMaterial.alpha = 0.8;

        this.data.racks.forEach(rack => {
            const pos = this.svgToBabylon(rack.x, rack.y);
            const rackMesh = BABYLON.MeshBuilder.CreateBox(
                rack.id,
                { width: rack.width, height: rackHeight, depth: rack.height },
                this.scene
            );
            rackMesh.position = new BABYLON.Vector3(
                pos.x + rack.width / 2,
                slabElevation + rackHeight / 2,
                pos.z - rack.height / 2
            );
            rackMesh.material = rackMaterial;
            rackMesh.metadata = {
                name: rack.name,
                accessDirection: rack.accessDirection,
                location: rack.location,
                type: 'rack'
            };
            this.meshes.racks.push(rackMesh);
        });
    }

    buildCoolers() {}

    buildCenterLines() {
        const slabElevation = 4.0;
        const lineHeight = 1 / 12;

        this.data.centerLines.forEach(line => {
            const pos1 = this.svgToBabylon(line.x1, line.y1);
            const pos2 = this.svgToBabylon(line.x2, line.y2);
            const centerLine = BABYLON.MeshBuilder.CreateLines(
                line.id,
                {
                    points: [
                        new BABYLON.Vector3(pos1.x, slabElevation + lineHeight, pos1.z),
                        new BABYLON.Vector3(pos2.x, slabElevation + lineHeight, pos2.z)
                    ]
                },
                this.scene
            );
            centerLine.color = new BABYLON.Color3(1, 1, 0);
        });
    }

    buildIntersections() {
        const slabElevation = 4.0;
        const markerHeight = 1 / 12;
        const markerMaterial = new BABYLON.StandardMaterial("intMat", this.scene);
        markerMaterial.diffuseColor = new BABYLON.Color3(1, 0, 1);
        markerMaterial.emissiveColor = new BABYLON.Color3(0.5, 0, 0.5);

        this.data.intersections.forEach(intersection => {
            const pos = this.svgToBabylon(intersection.x, intersection.y);
            const marker = BABYLON.MeshBuilder.CreateBox(
                intersection.id,
                { width: intersection.width, height: markerHeight, depth: intersection.height },
                this.scene
            );
            marker.position = new BABYLON.Vector3(
                pos.x + intersection.width / 2,
                slabElevation + markerHeight / 2,
                pos.z - intersection.height / 2
            );
            marker.material = markerMaterial;
        });
    }

    svgToBabylon(x, y) {
        return { x: x, z: -y };
    }
}

// Export for use in HTML
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ModelTParser, ModelTBuilder, WarehouseParser, WarehouseBuilder };
}
