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
    // Wall thickness, feet. RULED 2026-08-27 (George): data is whole feet; default wall = 1 ft
    // (= what the 2D plan draws). A per-wall walls[].thickness (integer ft) may override later.
    static WALL_T = 1.0;

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
        const wallThickness = ModelTBuilder.WALL_T;

        const wallMaterial = new BABYLON.StandardMaterial(`wallMat_${slabId}_${wall.id}`, this.scene);
        wallMaterial.diffuseColor = new BABYLON.Color3(0, 0.27, 0.62);

        // Filter doors that belong to this wall
        const wallDoors = doors.filter(door => door.wallId === wall.id);
        const orient = this.polygonOrientation(corners);

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

            const before = this.meshes.walls.length;
            if (segmentDoors.length === 0) {
                // No doors - create simple box wall
                this.createSimpleWallSegment(slabId, wall.id, i, p1, p2, dx, dy, length,
                    isHorizontal, isVertical, wallHeight, wallThickness, slabTop, wallMaterial, 'walls');
            } else {
                // Has doors - create wall with cutouts
                this.createWallSegmentWithDoors(slabId, wall.id, i, p1, p2, dx, dy, length,
                    isHorizontal, isVertical, wallHeight, wallThickness, slabTop, segmentDoors, wallMaterial, 'walls');
            }
            // Flush-inward band (spec 5.2): outward face on the boundary, body T inward
            const inw = this.inwardNormal(p1, p2, orient);
            const target = (isHorizontal ? inw.y : inw.x) * wallThickness / 2;
            this.newWallMeshes('walls', before).forEach(m => this.alignWallBand(m, p1, p2, isHorizontal, target));
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
            // Same precedence as the type builders (was bayWidth||10: a personnel door with
            // only openingWidth:3 got a 10ft hole) — modeltbabylon gen-11
            const doorWidth = door.openingWidth || door.bayWidth || door.width || 10;
            const doorHeight = door.openingHeight || 10;  // per-door cutout height (modeltbabylon gen-11)
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
        const wallThickness = ModelTBuilder.WALL_T;

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

            const beforeW = this.meshes.walls.length, beforeP = this.meshes.partitionWalls.length;
            if (segmentDoors.length === 0) {
                // No doors - create simple box wall
                this.createSimplePartitionSegment(slabId, wall.id, segIdx, p1, p2, dx, dy, length,
                    isHorizontal, isVertical, wallHeight, wallThickness, slabTop, partitionMaterial);
            } else {
                // Has doors - create wall with cutouts using same method as perimeter walls
                this.createWallSegmentWithDoors(slabId, wall.id, segIdx, p1, p2, dx, dy, length,
                    isHorizontal, isVertical, wallHeight, wallThickness, slabTop, segmentDoors, partitionMaterial, 'partitionWalls');
            }
            // Partition band centered on the turtle line (see alignWallBand note)
            [...this.newWallMeshes('walls', beforeW), ...this.newWallMeshes('partitionWalls', beforeP)]
                .forEach(m => this.alignWallBand(m, p1, p2, isHorizontal, 0));

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
     * Build a door mesh (v2) - dispatches to type-specific builders
     */
    buildDoorMesh(doorData, slabElevation, slabId) {
        const slabTop = slabElevation;
        // All hardware is placed relative to C, the door's point on the wall centerline
        // (spec 5.4.5, ruled 2026-08-27). See doorAtCenterline().
        const door = this.doorAtCenterline(doorData, slabId);

        // Dispatch to type-specific builder
        switch (door.type) {
            case 'cooler':
                this.buildCoolerDoor(door, slabTop, slabId);
                break;
            case 'bay':
                this.buildBayDoor(door, slabTop, slabId);
                break;
            case 'rollup':
                this.buildRollupDoor(door, slabTop, slabId);
                break;
            case 'personnel':
                this.buildPersonnelDoor(door, slabTop, slabId);
                break;
            case 'interior':
                // Interior openings have no physical door, just the wall cutout
                break;
            default:
                this.buildGenericDoor(door, slabTop, slabId);
        }

        // Create floor label for all door types
        this.createDoorLabel(door, slabTop, slabId);
    }

    /**
     * Build a cooler door: insulated sliding panel hung from an overhead track
     * on the facing (warm) side of the wall, plus a thin frame on each side.
     * Data fields: openingWidth/openingHeight (ft), insulation (in),
     * slideDirection ('left'|'right' AS SEEN FROM THE FACING SIDE — standing
     * outside the cooler, looking at the door). Panel renders CLOSED.
     * Modeled on lodge door quincy (cam photo 2025-11-22). — modeltbabylon gen-11
     */
    buildCoolerDoor(door, slabTop, slabId) {
        const openingWidth = door.openingWidth || door.bayWidth || door.width || 8;
        const openingHeight = door.openingHeight || 8;
        const doorBase = slabTop;
        const isVertical = door.orientation === 'vertical';

        const frameWidth = 4 / 12;      // 4 inches
        const frameThick = 1 / 12;      // 1 inch (sticks out from wall)
        const wallThickness = ModelTBuilder.WALL_T;      // wall thickness (ft)
        const wallHalf = wallThickness / 2;

        // Thin aluminum frame on both faces of the wall
        // (was orange/pink debug materials for exterior/interior — the panel side now shows facing)
        const frameMat = new BABYLON.StandardMaterial(`coolerFrameMat_${slabId}_${door.id}`, this.scene);
        frameMat.diffuseColor = new BABYLON.Color3(0.75, 0.75, 0.78);
        frameMat.specularColor = new BABYLON.Color3(0.3, 0.3, 0.3);

        const exteriorDist = wallHalf + frameThick / 2;
        const interiorDist = -(wallHalf + frameThick / 2);
        this.buildDoorFrame(door, slabId, 'exterior', openingWidth, openingHeight,
            doorBase, frameWidth, frameThick, exteriorDist, frameMat);
        this.buildDoorFrame(door, slabId, 'interior', openingWidth, openingHeight,
            doorBase, frameWidth, frameThick, interiorDist, frameMat);

        // --- Sliding panel on the facing side, closed over the opening ---
        const panelThick = (door.insulation || 4) / 12;  // insulation inches -> ft
        const panelGap = 1 / 12;                          // standoff from the wall face
        const panelOverlap = 0.5;                         // 6" past the opening each side + top
        const floorClear = 1 / 12;                        // hangs 1" above the slab
        const panelW = openingWidth + panelOverlap * 2;
        const panelH = openingHeight + panelOverlap;
        const panelBottom = doorBase + floorClear;
        const panelTop = panelBottom + panelH;
        const panelDist = wallHalf + panelGap + panelThick / 2;  // wall center -> panel center
        const panelPerp = this.getFacingOffset(door.facing, panelDist);

        const panelMat = new BABYLON.StandardMaterial(`coolerPanelMat_${slabId}_${door.id}`, this.scene);
        panelMat.diffuseColor = new BABYLON.Color3(0.93, 0.93, 0.91);  // white insulated panel
        panelMat.specularColor = new BABYLON.Color3(0.15, 0.15, 0.15);

        const panel = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_coolerdoor_panel`,
            {
                width: isVertical ? panelThick : panelW,
                height: panelH,
                depth: isVertical ? panelW : panelThick
            },
            this.scene
        );
        panel.position = this.svgToBabylon(
            door.x + panelPerp.x,
            door.y + panelPerp.y,
            panelBottom + panelH / 2
        );
        panel.material = panelMat;
        panel.metadata = { type: 'cooler', part: 'panel', doorId: door.id, slabId: slabId, facing: door.facing };
        this.meshes.doors.push(panel);

        // --- Overhead track: covers the opening plus a full panel width on the parked side ---
        const slideSign = this.getSlideSign(door);   // +1/-1 along the wall axis (SVG coords), 0 = unknown
        const sideKnown = slideSign !== 0;
        if (!sideKnown) console.warn(`Door ${door.id}: slideDirection missing — drawn neutral (no parked side), cannot animate`);
        const trackH = 0.25, trackD = 0.25;           // 3" x 3" rail
        const hangerH = 0.25;                         // trolley hanger between panel top and rail
        const trackLen = sideKnown ? panelW * 2 + 0.5 : panelW + 0.5;   // neutral: spans the opening only
        const trackCenterY = panelTop + hangerH + trackH / 2;
        const trackShift = slideSign * panelW / 2;    // rail center sits between closed and parked positions (0 when neutral)
        const trackParallel = isVertical ? { x: 0, y: trackShift } : { x: trackShift, y: 0 };

        const metalMat = new BABYLON.StandardMaterial(`coolerTrackMat_${slabId}_${door.id}`, this.scene);
        metalMat.diffuseColor = new BABYLON.Color3(0.55, 0.55, 0.58);  // galvanized
        metalMat.specularColor = new BABYLON.Color3(0.4, 0.4, 0.4);

        const track = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_coolerdoor_track`,
            {
                width: isVertical ? trackD : trackLen,
                height: trackH,
                depth: isVertical ? trackLen : trackD
            },
            this.scene
        );
        track.position = this.svgToBabylon(
            door.x + panelPerp.x + trackParallel.x,
            door.y + panelPerp.y + trackParallel.y,
            trackCenterY
        );
        track.material = metalMat;
        track.isPickable = false;
        this.meshes.doors.push(track);

        // Two trolley hangers tying the panel top to the rail
        const hangers = [];
        [-1, 1].forEach((side, i) => {
            const along = side * panelW / 4;
            const hp = isVertical ? { x: 0, y: along } : { x: along, y: 0 };
            const hanger = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${door.id}_coolerdoor_hanger${i}`,
                { width: 0.2, height: hangerH, depth: 0.2 },
                this.scene
            );
            hanger.position = this.svgToBabylon(
                door.x + panelPerp.x + hp.x,
                door.y + panelPerp.y + hp.y,
                panelTop + hangerH / 2
            );
            hanger.material = metalMat;
            hanger.isPickable = false;
            this.meshes.doors.push(hanger);
            hangers.push(hanger);
        });

        // --- Register as a twin door (kind slide) so the sensing loop can open/close it ---
        // Fully open = panel parked one panel-width toward the slide side (opening clear).
        const slideAxis = isVertical
            ? new BABYLON.Vector3(0, 0, -slideSign)   // SVG +y = Babylon -z
            : new BABYLON.Vector3(slideSign, 0, 0);
        const movers = [panel, ...hangers];
        this.twinDoors = this.twinDoors || {};
        this.twinDoors[door.id] = {
            doorId: door.id,
            slabId: slabId,
            kind: 'slide',
            movers: movers,
            closedPositions: movers.map(m => m.position.clone()),
            slideVector: slideAxis.scale(sideKnown ? panelW : 0),   // neutral door: state recorded, not drawn
            sideKnown: sideKnown,
            open: false,
            fraction: 0
        };
        panel.metadata.open = false;
        panel.metadata.twinDoor = true;
        panel.metadata.sideKnown = sideKnown;
        this.applyInitialState(door);
    }

    /**
     * Build a door frame (3 pieces: left, right, top) on one side of wall
     */
    buildDoorFrame(door, slabId, side, openingWidth, openingHeight, doorBase, frameWidth, frameThick, facingDist, material) {
        const leftRightHeight = openingHeight + frameWidth; // Extends 4" above opening
        const leftRightCenterY = doorBase + leftRightHeight / 2;
        const topWidth = openingWidth + frameWidth * 2; // Spans over left and right pieces
        const topCenterY = doorBase + openingHeight + frameWidth / 2;

        // Get offset perpendicular to wall (in facing direction)
        const perpOffset = this.getFacingOffset(door.facing, facingDist);

        // Get offset parallel to wall (left/right of opening)
        const leftDist = -(openingWidth / 2 + frameWidth / 2);
        const rightDist = (openingWidth / 2 + frameWidth / 2);

        // For horizontal doors (E-W wall), left/right is in X direction
        // For vertical doors (N-S wall), left/right is in Y direction
        const isVertical = door.orientation === 'vertical';

        // Left piece
        const leftParallel = isVertical
            ? { x: 0, y: leftDist }
            : { x: leftDist, y: 0 };

        const leftPiece = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_${side}_left`,
            {
                width: isVertical ? frameThick : frameWidth,
                height: leftRightHeight,
                depth: isVertical ? frameWidth : frameThick
            },
            this.scene
        );
        leftPiece.position = this.svgToBabylon(
            door.x + perpOffset.x + leftParallel.x,
            door.y + perpOffset.y + leftParallel.y,
            leftRightCenterY
        );
        leftPiece.material = material;
        leftPiece.isPickable = false;
        this.meshes.doors.push(leftPiece);

        // Right piece
        const rightParallel = isVertical
            ? { x: 0, y: rightDist }
            : { x: rightDist, y: 0 };

        const rightPiece = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_${side}_right`,
            {
                width: isVertical ? frameThick : frameWidth,
                height: leftRightHeight,
                depth: isVertical ? frameWidth : frameThick
            },
            this.scene
        );
        rightPiece.position = this.svgToBabylon(
            door.x + perpOffset.x + rightParallel.x,
            door.y + perpOffset.y + rightParallel.y,
            leftRightCenterY
        );
        rightPiece.material = material;
        rightPiece.isPickable = false;
        this.meshes.doors.push(rightPiece);

        // Top piece (sits on top of left and right)
        const topPiece = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_${side}_top`,
            {
                width: isVertical ? frameThick : topWidth,
                height: frameWidth,
                depth: isVertical ? topWidth : frameThick
            },
            this.scene
        );
        topPiece.position = this.svgToBabylon(
            door.x + perpOffset.x,
            door.y + perpOffset.y,
            topCenterY
        );
        topPiece.material = material;
        topPiece.isPickable = false;
        this.meshes.doors.push(topPiece);

        // Store metadata on left piece
        leftPiece.metadata = {
            type: 'cooler',
            facing: door.facing,
            side: side,
            doorId: door.id,
            slabId: slabId
        };
    }

    /**
     * Get offset in the facing direction
     */
    getFacingOffset(facing, distance) {
        switch (facing) {
            case 'north': return { x: 0, y: -distance };
            case 'south': return { x: 0, y: distance };
            case 'east': return { x: distance, y: 0 };
            case 'west': return { x: -distance, y: 0 };
            default: return { x: 0, y: 0 };
        }
    }

    /**
     * Get door rotation based on orientation and facing direction
     */
    getDoorRotation(door) {
        // Horizontal doors face north or south
        // Vertical doors face east or west
        switch (door.facing) {
            case 'north': return Math.PI;        // 180°
            case 'south': return 0;              // 0°
            case 'east': return Math.PI / 2;    // 90°
            case 'west': return -Math.PI / 2;   // -90°
            default:
                // Fallback to orientation-based
                return door.orientation === 'vertical' ? Math.PI / 2 : 0;
        }
    }

    /**
     * Get frame offset based on door facing
     */
    getFrameOffset(door, distance) {
        switch (door.facing) {
            case 'north':
            case 'south':
                return { x: distance, y: 0 };
            case 'east':
            case 'west':
                return { x: 0, y: distance };
            default:
                return door.orientation === 'vertical'
                    ? { x: 0, y: distance }
                    : { x: distance, y: 0 };
        }
    }

    /**
     * Sign (+1/-1) along the wall axis toward a sliding door's parked side.
     * slideDirection is 'left' | 'right' AS SEEN FROM THE FACING SIDE
     * (standing outside the cooler, looking at the door). SVG axes: +x east, +y south.
     * Returns 0 when slideDirection is absent — NO default: a guessed side would be
     * drawn as fact (ruled 2026-08-27). — modeltbabylon gen-11
     */
    getSlideSign(door) {
        if (door.slideDirection !== 'left' && door.slideDirection !== 'right') return 0;
        const left = door.slideDirection === 'left';
        let sign;
        switch (door.facing) {
            case 'north': sign = 1;  break;   // viewer looks south: left hand = east (+x)
            case 'south': sign = -1; break;   // viewer looks north: left = west (-x)
            case 'east':  sign = 1;  break;   // viewer looks west:  left = south (+y)
            case 'west':  sign = -1; break;   // viewer looks east:  left = north (-y)
            default:      sign = 1;
        }
        return left ? sign : -sign;
    }

    /**
     * Open/close a sliding (cooler) door in the twin.
     * state: true|'open', false|'closed', or a fraction 0..1 (0 = closed).
     * Slides the panel + hangers along the track; ~1.5 s eased when animate.
     * Returns the door record ({open, fraction, ...}) or null if unknown.
     * — modeltbabylon gen-11
     */
    setDoorState(doorId, state, animate = true) {
        const d = this.twinDoors && this.twinDoors[doorId];
        if (!d) return null;

        const fraction = typeof state === 'number'
            ? Math.max(0, Math.min(1, state))
            : (state === true || state === 'open' || state === 'opened') ? 1 : 0;
        d.open = fraction > 0.5;
        d.fraction = fraction;

        const ease = new BABYLON.CubicEase();
        ease.setEasingMode(BABYLON.EasingFunction.EASINGMODE_EASEINOUT);

        if (d.kind === 'swing') {
            // Hinged leaf: rotate the hinge pivot from closed (0) to openAngle (90° into the interior).
            const target = d.openAngle * fraction;
            if (animate) {
                BABYLON.Animation.CreateAndStartAnimation(`swing_${doorId}`, d.pivot, 'rotation.y', 60, 60,
                    d.pivot.rotation.y, target, BABYLON.Animation.ANIMATIONLOOPMODE_CONSTANT, ease);
            } else {
                d.pivot.rotation.y = target;
            }
            d.panel.metadata.open = d.open;
            return d;
        }

        if (d.kind === 'roll') {
            // Curtain rolls up into the housing: shrink from the bottom, top edge pinned.
            const visible = Math.max(0.03, 1 - fraction);
            const targetY = d.topY - d.panelHeight * visible / 2;
            const p = d.panel;
            if (animate) {
                BABYLON.Animation.CreateAndStartAnimation(`roll_${doorId}_s`, p, 'scaling.y', 60, 120,
                    p.scaling.y, visible, BABYLON.Animation.ANIMATIONLOOPMODE_CONSTANT, ease);
                BABYLON.Animation.CreateAndStartAnimation(`roll_${doorId}_y`, p, 'position.y', 60, 120,
                    p.position.y, targetY, BABYLON.Animation.ANIMATIONLOOPMODE_CONSTANT, ease);
            } else {
                p.scaling.y = visible;
                p.position.y = targetY;
            }
            p.metadata.open = d.open;
            return d;
        }

        d.movers.forEach((mesh, i) => {
            const target = d.closedPositions[i].add(d.slideVector.scale(fraction));
            if (animate) {
                BABYLON.Animation.CreateAndStartAnimation(
                    `slide_${doorId}_${i}`, mesh, 'position', 60, 90,
                    mesh.position.clone(), target,
                    BABYLON.Animation.ANIMATIONLOOPMODE_CONSTANT, ease
                );
            } else {
                mesh.position = target;
            }
        });
        d.movers[0].metadata.open = d.open;
        return d;
    }

    /**
     * Snapshot of every sliding door: { doorId: {open, fraction, slabId} }
     */
    getDoorStates() {
        const out = {};
        Object.values(this.twinDoors || {}).forEach(d => {
            out[d.doorId] = { open: d.open, fraction: d.fraction, kind: d.kind, slabId: d.slabId,
                              sideKnown: d.sideKnown !== false };
        });
        return out;
    }

    /**
     * Register a bay/rollup curtain so setDoorState can roll it up into the housing.
     * — modeltbabylon gen-11
     */
    registerRollingDoor(door, panel, slabId, panelHeight) {
        this.twinDoors = this.twinDoors || {};
        this.twinDoors[door.id] = {
            doorId: door.id,
            slabId: slabId,
            kind: 'roll',
            panel: panel,
            panelHeight: panelHeight,
            topY: panel.position.y + panelHeight / 2,
            open: false,
            fraction: 0
        };
        panel.metadata = Object.assign(panel.metadata || {}, { twinDoor: true, part: 'panel', open: false });
        panel.isPickable = true;   // click to toggle
        this.applyInitialState(door);
    }

    /**
     * Build a bay door - loading dock door with dock seal and leveler
     */
    buildBayDoor(door, slabTop, slabId) {
        const openingWidth = door.openingWidth || door.bayWidth || door.width || 10;
        const openingHeight = door.openingHeight || 12;
        const leafWidth = door.leafWidth || (openingWidth - 1);   // spec 5.4.7: L = leafWidth, default W-1
        const doorBase = slabTop;
        const doorCenterY = doorBase + openingHeight / 2;
        const sides = this.doorSides(door);
        this.buildJambs(door, slabId, openingWidth, leafWidth, openingHeight, doorBase);
        const wallHalf = ModelTBuilder.WALL_T / 2;

        // Dock seal (black rubber frame around door) — EXTERIOR face
        if (door.hasDockSeal !== false) {
            const sealMaterial = new BABYLON.StandardMaterial(`baySealMat_${slabId}_${door.id}`, this.scene);
            sealMaterial.diffuseColor = new BABYLON.Color3(0.1, 0.1, 0.1); // Black rubber

            const sealWidth = 1.0; // 1 foot wide seal
            const sealDepth = 1.5; // Sticks out 1.5 feet
            const sealPerp = sides.out(wallHalf + sealDepth / 2);   // against the exterior face

            // Left seal
            const leftSeal = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${door.id}_leftSeal`,
                { width: sealWidth, height: openingHeight, depth: sealDepth },
                this.scene
            );
            const leftOffset = door.orientation === 'vertical' ? { x: 0, y: -openingWidth / 2 - sealWidth / 2 } : { x: -openingWidth / 2 - sealWidth / 2, y: 0 };
            leftSeal.position = this.svgToBabylon(door.x + leftOffset.x + sealPerp.x, door.y + leftOffset.y + sealPerp.y, doorCenterY);
            if (door.orientation === 'vertical') leftSeal.rotation.y = Math.PI / 2;
            leftSeal.material = sealMaterial;
            leftSeal.isPickable = false;
            this.meshes.doors.push(leftSeal);

            // Right seal
            const rightSeal = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${door.id}_rightSeal`,
                { width: sealWidth, height: openingHeight, depth: sealDepth },
                this.scene
            );
            const rightOffset = door.orientation === 'vertical' ? { x: 0, y: openingWidth / 2 + sealWidth / 2 } : { x: openingWidth / 2 + sealWidth / 2, y: 0 };
            rightSeal.position = this.svgToBabylon(door.x + rightOffset.x + sealPerp.x, door.y + rightOffset.y + sealPerp.y, doorCenterY);
            if (door.orientation === 'vertical') rightSeal.rotation.y = Math.PI / 2;
            rightSeal.material = sealMaterial;
            rightSeal.isPickable = false;
            this.meshes.doors.push(rightSeal);

            // Top seal
            const topSeal = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${door.id}_topSeal`,
                { width: openingWidth + sealWidth * 2, height: sealWidth, depth: sealDepth },
                this.scene
            );
            topSeal.position = this.svgToBabylon(door.x + sealPerp.x, door.y + sealPerp.y, doorBase + openingHeight + sealWidth / 2);
            if (door.orientation === 'vertical') topSeal.rotation.y = Math.PI / 2;
            topSeal.material = sealMaterial;
            topSeal.isPickable = false;
            this.meshes.doors.push(topSeal);
        }

        // Roll-up door panel (metal slats)
        const panelMaterial = new BABYLON.StandardMaterial(`bayPanelMat_${slabId}_${door.id}`, this.scene);
        panelMaterial.diffuseColor = new BABYLON.Color3(0.7, 0.7, 0.7); // Light gray metal
        panelMaterial.specularColor = new BABYLON.Color3(0.3, 0.3, 0.3);   // opaque: twin shows real state

        const panel = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_panel`,
            { width: leafWidth, height: openingHeight - 0.5, depth: 0.15 },
            this.scene
        );
        const curtainPerp = sides.inn(wallHalf + 0.10);   // curtain runs just inside the wall
        panel.position = this.svgToBabylon(door.x + curtainPerp.x, door.y + curtainPerp.y, doorCenterY);
        if (door.orientation === 'vertical') panel.rotation.y = Math.PI / 2;
        panel.material = panelMaterial;
        panel.isPickable = false;
        this.meshes.doors.push(panel);

        // Guide tracks + roll housing — INTERIOR face (same as a standalone rollup)
        const bayFrameMat = new BABYLON.StandardMaterial(`bayFrameMat_${slabId}_${door.id}`, this.scene);
        bayFrameMat.diffuseColor = new BABYLON.Color3(0.35, 0.35, 0.35);
        const bayTrackW = door.trackWidth || 0.5;
        const trackPerp = sides.inn(wallHalf + 0.15);
        [-1, 1].forEach((side, i) => {
            const along = side * (leafWidth / 2 + bayTrackW / 2);   // tracks on the jambs, at the curtain edge
            const ao = door.orientation === 'vertical' ? { x: 0, y: along } : { x: along, y: 0 };
            const track = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${door.id}_${i === 0 ? 'left' : 'right'}Track`,
                { width: bayTrackW, height: openingHeight, depth: 0.3 },
                this.scene
            );
            track.position = this.svgToBabylon(door.x + ao.x + trackPerp.x, door.y + ao.y + trackPerp.y, doorCenterY);
            if (door.orientation === 'vertical') track.rotation.y = Math.PI / 2;
            track.material = bayFrameMat;
            track.isPickable = false;
            this.meshes.doors.push(track);
        });
        const bayHousingH = door.housingHeight || 2;
        const bayHousing = BABYLON.MeshBuilder.CreateCylinder(
            `${slabId}_${door.id}_housing`,
            { diameter: bayHousingH, height: leafWidth + bayTrackW * 2, tessellation: 16 },
            this.scene
        );
        const housingPerp = sides.inn(wallHalf + bayHousingH / 2);
        bayHousing.position = this.svgToBabylon(door.x + housingPerp.x, door.y + housingPerp.y, doorBase + openingHeight + bayHousingH / 2);
        bayHousing.rotation.z = Math.PI / 2;
        if (door.orientation === 'vertical') { bayHousing.rotation.z = 0; bayHousing.rotation.x = Math.PI / 2; }
        bayHousing.material = bayFrameMat;
        bayHousing.isPickable = false;
        this.meshes.doors.push(bayHousing);

        // Dock leveler (if enabled)
        if (door.hasDockLeveler !== false) {
            const levelerMaterial = new BABYLON.StandardMaterial(`bayLevelerMat_${slabId}_${door.id}`, this.scene);
            levelerMaterial.diffuseColor = new BABYLON.Color3(0.4, 0.4, 0.4); // Dark gray metal
            levelerMaterial.specularColor = new BABYLON.Color3(0.5, 0.5, 0.5);

            const levelerWidth = door.levelerWidth || 8;
            const levelerDepth = door.levelerDepth || 6;

            const leveler = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${door.id}_leveler`,
                { width: levelerWidth, height: 0.3, depth: levelerDepth },
                this.scene
            );
            // Position leveler on interior side of door
            const levelerOffset = this.getLevelerOffset(door, levelerDepth);
            leveler.position = this.svgToBabylon(door.x + levelerOffset.x, door.y + levelerOffset.y, slabTop + 0.15);
            if (door.orientation === 'vertical') leveler.rotation.y = Math.PI / 2;
            leveler.material = levelerMaterial;
            leveler.isPickable = false;
            this.meshes.doors.push(leveler);
        }

        panel.metadata = {
            type: door.type,
            facing: door.facing,
            openingWidth: openingWidth,
            slabId: slabId,
            doorId: door.id
        };
        this.registerRollingDoor(door, panel, slabId, openingHeight - 0.5);
    }

    /**
     * WALL BAND PLACEMENT — RULED 2026-08-27 (spec 5.2): a perimeter wall sits ON the slab,
     * outward face flush with the boundary, band extending T inward, never straddling.
     * Partition bands: centered on the turtle line (3D rule pending a spec ruling; 2D today
     * is right-of-travel for multi-segment partitions and +x for single vertical ones).
     * The segment builders extrude/center differently, so after creating a segment we MEASURE
     * its band and translate it to the target. — modeltbabylon gen-11
     */
    polygonOrientation(corners) {
        let a = 0;
        for (let i = 0; i < corners.length; i++) {
            const p = corners[i], q = corners[(i + 1) % corners.length];
            a += p.x * q.y - q.x * p.y;
        }
        return a > 0 ? 1 : -1;
    }

    /** Inward unit normal (SVG coords) of perimeter segment p1->p2 for polygon orientation o. */
    inwardNormal(p1, p2, o) {
        const dx = p2.x - p1.x, dy = p2.y - p1.y;
        const len = Math.hypot(dx, dy) || 1;
        return o > 0 ? { x: -dy / len, y: dx / len } : { x: dy / len, y: -dx / len };
    }

    /**
     * Translate a wall mesh so its band center sits targetOffset ft from the segment line,
     * measured along the SVG perpendicular axis (y for horizontal segments, x for vertical).
     */
    alignWallBand(mesh, p1, p2, isHorizontal, targetOffset) {
        mesh.computeWorldMatrix(true);
        const bb = mesh.getBoundingInfo().boundingBox;
        if (isHorizontal) {
            const nowY = -(bb.minimumWorld.z + bb.maximumWorld.z) / 2;   // Babylon z = -svgY
            mesh.position.z -= (p1.y + targetOffset) - nowY;
        } else {
            const nowX = (bb.minimumWorld.x + bb.maximumWorld.x) / 2;
            mesh.position.x += (p1.x + targetOffset) - nowX;
        }
        mesh.computeWorldMatrix(true);
    }

    /** Meshes appended to this.meshes[kind] since count. */
    newWallMeshes(kind, count) {
        return this.meshes[kind].slice(count);
    }

    /**
     * The door's point C on the WALL CENTERLINE — every hardware part is placed relative to C.
     * Spec 5.4.5 (ruled 2026-08-27): a perimeter door's (x,y) is the opening center on the
     * OUTWARD face, so C = (x,y) + in*T/2. Partition lines are centerlines in 3D, so C = foot
     * of (x,y) on the line. Implemented as a projection onto the wall's band centerline, so it
     * is robust to which face the data recorded. Returns a copy of the door with x,y = C.
     */
    doorAtCenterline(door, slabId) {
        const slab = this.spec.slabs && this.spec.slabs.find(s => s.id === slabId);
        if (!slab || !door.wallId) return door;
        const wall = (slab.walls || []).find(w => w.id === door.wallId || `${slabId}_${w.id}` === door.wallId);
        if (!wall) return door;
        const T = ModelTBuilder.WALL_T;
        let pts, closed, orient = 0;
        if (wall.corners) {
            pts = wall.corners; closed = true; orient = this.polygonOrientation(pts);
        } else if (wall.start && wall.segments) {
            pts = [{ x: wall.start.x, y: wall.start.y }];
            wall.segments.forEach(s => {
                const p = pts[pts.length - 1], d = s.direction;
                pts.push({ x: p.x + (d === 'east' ? s.length : d === 'west' ? -s.length : 0),
                           y: p.y + (d === 'south' ? s.length : d === 'north' ? -s.length : 0) });
            });
            closed = false;
        } else return door;

        let best = null;
        const n = closed ? pts.length : pts.length - 1;
        for (let i = 0; i < n; i++) {
            const p1 = pts[i], p2 = pts[(i + 1) % pts.length];
            const dx = p2.x - p1.x, dy = p2.y - p1.y, len2 = dx * dx + dy * dy;
            if (len2 < 0.01) continue;
            const t = Math.max(0, Math.min(1, ((door.x - p1.x) * dx + (door.y - p1.y) * dy) / len2));
            const fx = p1.x + t * dx, fy = p1.y + t * dy;
            const dist = Math.hypot(door.x - fx, door.y - fy);
            if (!best || dist < best.dist) best = { dist, fx, fy, p1, p2 };
        }
        if (!best || best.dist > 3) return door;   // not on this wall — leave as recorded
        let cx = best.fx, cy = best.fy;
        if (closed) {
            const inw = this.inwardNormal(best.p1, best.p2, orient);
            cx += inw.x * T / 2; cy += inw.y * T / 2;
        }
        return Object.assign({}, door, { x: cx, y: cy, recordedX: door.x, recordedY: door.y });
    }

    /**
     * Steel jambs narrowing a W-wide cutout to an L-wide leaf: one post each side,
     * full opening height, wall-deep, centered on the wall line. Spec 5.4.7 (2D draws
     * these as side walls). No-op when L >= W. — modeltbabylon gen-11
     */
    buildJambs(door, slabId, openingWidth, leafWidth, openingHeight, doorBase) {
        const jambW = (openingWidth - leafWidth) / 2;
        if (jambW <= 0.01) return;
        const isVertical = door.orientation === 'vertical';
        const mat = new BABYLON.StandardMaterial(`jambMat_${slabId}_${door.id}`, this.scene);
        mat.diffuseColor = new BABYLON.Color3(0.3, 0.3, 0.32);   // steel
        [-1, 1].forEach((side, i) => {
            const along = side * (openingWidth / 2 - jambW / 2);
            const ao = isVertical ? { x: 0, y: along } : { x: along, y: 0 };
            const jamb = BABYLON.MeshBuilder.CreateBox(
                `${slabId}_${door.id}_jamb${i}`,
                { width: isVertical ? ModelTBuilder.WALL_T : jambW, height: openingHeight, depth: isVertical ? jambW : ModelTBuilder.WALL_T },
                this.scene
            );
            jamb.position = this.svgToBabylon(door.x + ao.x, door.y + ao.y, doorBase + openingHeight / 2);
            jamb.material = mat;
            jamb.isPickable = false;
            this.meshes.doors.push(jamb);
        });
    }

    /**
     * Side vectors for a door, derived from facing (= outward normal).
     * Returns SVG-space offsets: out(d) = d ft toward the facing side (exterior),
     * inn(d) = d ft toward the interior. Missing facing -> zero offsets (straddle).
     * — modeltbabylon gen-11 (library/door-geometry-birds-eye.md)
     */
    doorSides(door) {
        return {
            out: (d) => this.getFacingOffset(door.facing, d),
            inn: (d) => this.getFacingOffset(door.facing, -d)
        };
    }

    /**
     * Get leveler offset based on door facing direction
     */
    getLevelerOffset(door, levelerDepth) {
        // Leveler pit is INSIDE the building: opposite the facing (outward) direction.
        // Was toward facing, which put every leveler outside on the ground (fixed gen-11).
        const offset = levelerDepth / 2 + 0.25;   // starts at the interior wall face
        return this.getFacingOffset(door.facing, -offset);
    }

    /**
     * Build a rollup door - standalone roll-up without dock equipment
     */
    buildRollupDoor(door, slabTop, slabId) {
        const openingWidth = door.openingWidth || door.bayWidth || door.width || 10;
        const openingHeight = door.openingHeight || 10;
        const leafWidth = door.leafWidth || (openingWidth - 1);   // spec 5.4.7: L = leafWidth, default W-1
        const doorBase = slabTop;
        const doorCenterY = doorBase + openingHeight / 2;
        const housingHeight = door.housingHeight || 2;
        const trackWidth = door.trackWidth || 0.5;
        this.buildJambs(door, slabId, openingWidth, leafWidth, openingHeight, doorBase);
        const sides = this.doorSides(door);
        const wallHalf = ModelTBuilder.WALL_T / 2;
        const trackPerp = sides.inn(wallHalf + 0.15);          // tracks on the interior face
        const housingPerp = sides.inn(wallHalf + housingHeight / 2);
        const curtainPerp = sides.inn(wallHalf + 0.10);

        // Metal frame
        const frameMaterial = new BABYLON.StandardMaterial(`rollupFrameMat_${slabId}_${door.id}`, this.scene);
        frameMaterial.diffuseColor = new BABYLON.Color3(0.35, 0.35, 0.35);

        // Guide tracks (left and right)
        const leftTrack = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_leftTrack`,
            { width: trackWidth, height: openingHeight, depth: 0.3 },
            this.scene
        );
        const leftOffset = door.orientation === 'vertical' ? { x: 0, y: -leafWidth / 2 - trackWidth / 2 } : { x: -leafWidth / 2 - trackWidth / 2, y: 0 };
        leftTrack.position = this.svgToBabylon(door.x + leftOffset.x + trackPerp.x, door.y + leftOffset.y + trackPerp.y, doorCenterY);
        if (door.orientation === 'vertical') leftTrack.rotation.y = Math.PI / 2;
        leftTrack.material = frameMaterial;
        leftTrack.isPickable = false;
        this.meshes.doors.push(leftTrack);

        const rightTrack = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_rightTrack`,
            { width: trackWidth, height: openingHeight, depth: 0.3 },
            this.scene
        );
        const rightOffset = door.orientation === 'vertical' ? { x: 0, y: leafWidth / 2 + trackWidth / 2 } : { x: leafWidth / 2 + trackWidth / 2, y: 0 };
        rightTrack.position = this.svgToBabylon(door.x + rightOffset.x + trackPerp.x, door.y + rightOffset.y + trackPerp.y, doorCenterY);
        if (door.orientation === 'vertical') rightTrack.rotation.y = Math.PI / 2;
        rightTrack.material = frameMaterial;
        rightTrack.isPickable = false;
        this.meshes.doors.push(rightTrack);

        // Roll housing (cylinder above door)
        const housing = BABYLON.MeshBuilder.CreateCylinder(
            `${slabId}_${door.id}_housing`,
            { diameter: housingHeight, height: leafWidth + trackWidth * 2, tessellation: 16 },
            this.scene
        );
        housing.position = this.svgToBabylon(door.x + housingPerp.x, door.y + housingPerp.y, doorBase + openingHeight + housingHeight / 2);
        housing.rotation.z = Math.PI / 2;
        if (door.orientation === 'vertical') {
            housing.rotation.z = 0;
            housing.rotation.x = Math.PI / 2;
        }
        housing.material = frameMaterial;
        housing.isPickable = false;
        this.meshes.doors.push(housing);

        // Roll-up panel (semi-transparent)
        const panelMaterial = new BABYLON.StandardMaterial(`rollupPanelMat_${slabId}_${door.id}`, this.scene);
        panelMaterial.diffuseColor = new BABYLON.Color3(0.6, 0.6, 0.6);
        panelMaterial.specularColor = new BABYLON.Color3(0.3, 0.3, 0.3);   // opaque: twin shows real state

        const panel = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_panel`,
            { width: leafWidth, height: openingHeight - 0.3, depth: 0.1 },
            this.scene
        );
        panel.position = this.svgToBabylon(door.x + curtainPerp.x, door.y + curtainPerp.y, doorCenterY);
        if (door.orientation === 'vertical') panel.rotation.y = Math.PI / 2;
        panel.material = panelMaterial;
        panel.isPickable = false;
        this.meshes.doors.push(panel);

        panel.metadata = {
            type: door.type,
            facing: door.facing,
            openingWidth: openingWidth,
            slabId: slabId,
            doorId: door.id
        };
        this.registerRollingDoor(door, panel, slabId, openingHeight - 0.3);
    }

    /**
     * Build a personnel door - standard hinged door
     */
    buildPersonnelDoor(door, slabTop, slabId) {
        const openingWidth = door.openingWidth || door.bayWidth || door.width || 3;
        const openingHeight = door.openingHeight || 7;
        const doorBase = slabTop;
        const doorCenterY = doorBase + openingHeight / 2;
        const frameWidth = door.frameWidth || 0.25;
        const hingePosition = door.hingePosition || 'left';

        // Door frame
        const frameMaterial = new BABYLON.StandardMaterial(`personnelFrameMat_${slabId}_${door.id}`, this.scene);
        frameMaterial.diffuseColor = new BABYLON.Color3(0.4, 0.35, 0.3);

        // Top frame
        const topFrame = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_topFrame`,
            { width: openingWidth + frameWidth * 2, height: frameWidth, depth: frameWidth },
            this.scene
        );
        topFrame.position = this.svgToBabylon(door.x, door.y, doorBase + openingHeight + frameWidth / 2);
        if (door.orientation === 'vertical') topFrame.rotation.y = Math.PI / 2;
        topFrame.material = frameMaterial;
        topFrame.isPickable = false;
        this.meshes.doors.push(topFrame);

        // Left frame
        const leftFrame = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_leftFrame`,
            { width: frameWidth, height: openingHeight, depth: frameWidth },
            this.scene
        );
        const leftOffset = door.orientation === 'vertical' ? { x: 0, y: -openingWidth / 2 - frameWidth / 2 } : { x: -openingWidth / 2 - frameWidth / 2, y: 0 };
        leftFrame.position = this.svgToBabylon(door.x + leftOffset.x, door.y + leftOffset.y, doorCenterY);
        if (door.orientation === 'vertical') leftFrame.rotation.y = Math.PI / 2;
        leftFrame.material = frameMaterial;
        leftFrame.isPickable = false;
        this.meshes.doors.push(leftFrame);

        // Right frame
        const rightFrame = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_rightFrame`,
            { width: frameWidth, height: openingHeight, depth: frameWidth },
            this.scene
        );
        const rightOffset = door.orientation === 'vertical' ? { x: 0, y: openingWidth / 2 + frameWidth / 2 } : { x: openingWidth / 2 + frameWidth / 2, y: 0 };
        rightFrame.position = this.svgToBabylon(door.x + rightOffset.x, door.y + rightOffset.y, doorCenterY);
        if (door.orientation === 'vertical') rightFrame.rotation.y = Math.PI / 2;
        rightFrame.material = frameMaterial;
        rightFrame.isPickable = false;
        this.meshes.doors.push(rightFrame);

        // Door leaf — HINGED (modeltbabylon gen-12, 2026-08-28, George: "open into the main room").
        // hingePosition = left|right AS SEEN FROM THE FACING SIDE (same rule as slideDirection).
        // The leaf swings AWAY from the facing side (spec 5.4.4: facing = the swing-away face),
        // i.e. toward the interior = -facing. No hingePosition => leaf drawn centered, cannot animate.
        const panelMaterial = new BABYLON.StandardMaterial(`personnelPanelMat_${slabId}_${door.id}`, this.scene);
        panelMaterial.diffuseColor = new BABYLON.Color3(0.5, 0.4, 0.3); // Wood brown

        const leafW = door.leafWidth || (openingWidth - 0.2);   // spec 5.4.7: personnel L = W-0.2
        const hingeSign = this.getSlideSign({ facing: door.facing, slideDirection: door.hingePosition });
        const sideKnown = hingeSign !== 0;
        if (!sideKnown) console.warn(`Door ${door.id}: hingePosition missing — leaf drawn centered, cannot swing`);
        const axis = door.orientation === 'vertical' ? { x: 0, y: 1 } : { x: 1, y: 0 };   // along the wall (svg)
        // Hinge sits on the hinge-side jamb, on the wall centerline.
        const hingeSvg = sideKnown
            ? { x: door.x + axis.x * hingeSign * openingWidth / 2, y: door.y + axis.y * hingeSign * openingWidth / 2 }
            : { x: door.x, y: door.y };
        const pivot = new BABYLON.TransformNode(`${slabId}_${door.id}_hinge`, this.scene);
        pivot.position = this.svgToBabylon(hingeSvg.x, hingeSvg.y, doorCenterY);

        const panel = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}_panel`,
            { width: leafW, height: openingHeight - 0.2, depth: 0.15 },
            this.scene
        );
        panel.parent = pivot;
        // Closed: leaf lies in the wall plane, extending from the hinge toward the latch side.
        const closedSvg = sideKnown ? { x: -axis.x * hingeSign, y: -axis.y * hingeSign } : axis;
        const closedDir = new BABYLON.Vector3(closedSvg.x, 0, -closedSvg.y);          // svg y -> babylon -z
        panel.position = closedDir.scale(sideKnown ? leafW / 2 : 0);
        panel.rotation.y = Math.atan2(closedDir.x, closedDir.z) - Math.PI / 2;        // box width along closedDir
        panel.material = panelMaterial;
        panel.isPickable = sideKnown;   // click to toggle
        this.meshes.doors.push(panel);

        panel.metadata = {
            type: door.type,
            facing: door.facing,
            openingWidth: openingWidth,
            slabId: slabId,
            doorId: door.id,
            twinDoor: sideKnown,
            sideKnown: sideKnown,
            open: false
        };

        // Open: leaf points into the interior (-facing). Signed angle from closed to open about +Y.
        const inSvg = this.getFacingOffset(door.facing, -1);
        const openDir = new BABYLON.Vector3(inSvg.x, 0, -inSvg.y);
        const openAngle = sideKnown
            ? Math.atan2(BABYLON.Vector3.Cross(closedDir, openDir).y, BABYLON.Vector3.Dot(closedDir, openDir))
            : 0;

        this.twinDoors = this.twinDoors || {};
        this.twinDoors[door.id] = {
            doorId: door.id, slabId: slabId, kind: 'swing',
            pivot: pivot, panel: panel, openAngle: openAngle,
            sideKnown: sideKnown, open: false, fraction: 0
        };
        this.applyInitialState(door);
    }

    /**
     * Apply a door's recorded ModelT state ({open: 0..1}) when the scene is built.
     * Spec 5.4.2 (ruled 2026-08-28): doors[].state = {open, updatedAt, source}.
     * — modeltbabylon gen-12
     */
    applyInitialState(door) {
        if (!door.state || door.state.open === undefined || door.state.open === null) return;
        this.setDoorState(door.id, Number(door.state.open), false);
    }

    /**
     * Build a generic door (fallback for unknown types)
     */
    buildGenericDoor(door, slabTop, slabId) {
        const doorHeight = door.openingHeight || 10.0;
        const doorBase = slabTop;
        const doorCenterY = doorBase + doorHeight / 2;

        const doorMaterial = new BABYLON.StandardMaterial(`doorMat_${slabId}_${door.id}`, this.scene);
        doorMaterial.diffuseColor = new BABYLON.Color3(0.6, 0.4, 0.2);
        doorMaterial.alpha = 0.2;

        const bayWidth = door.openingWidth || door.bayWidth || door.width || 10;

        const doorFrame = BABYLON.MeshBuilder.CreateBox(
            `${slabId}_${door.id}`,
            { width: bayWidth, height: doorHeight, depth: 0.2 },
            this.scene
        );

        doorFrame.position = this.svgToBabylon(door.x, door.y, doorCenterY);

        if (door.orientation === 'vertical') {
            doorFrame.rotation.y = Math.PI / 2;
        }

        doorFrame.material = doorMaterial;
        doorFrame.isPickable = false;

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
     * Create a clickable floor label for a door
     */
    createDoorLabel(door, slabTop, slabId) {
        const labelSize = 8; // Size of label plane
        const labelHeight = slabTop + 0.1; // Just above slab

        // Create dynamic texture for text
        const texture = new BABYLON.DynamicTexture(
            `doorLabelTex_${slabId}_${door.id}`,
            { width: 256, height: 64 },
            this.scene,
            false
        );
        texture.hasAlpha = true;

        const ctx = texture.getContext();
        ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
        ctx.fillRect(0, 0, 256, 64);
        ctx.fillStyle = '#FFA500'; // Orange text
        ctx.font = 'bold 28px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(door.id.toUpperCase(), 128, 32);
        texture.update();

        // Material with texture (shared by both plates)
        const labelMat = new BABYLON.StandardMaterial(`doorLabelMat_${slabId}_${door.id}`, this.scene);
        labelMat.diffuseTexture = texture;
        labelMat.emissiveTexture = texture;
        labelMat.opacityTexture = texture;
        labelMat.backFaceCulling = false;

        // Two plates: interior side (opposite facing) and facing side — George 2026-08-28:
        // "door labels on both sides of the door". modeltbabylon gen-12
        const facing0 = door.facing || (door.orientation === 'vertical' ? 'east' : 'south');
        const opposite = { north: 'south', south: 'north', east: 'west', west: 'east' };
        this.createDoorLabelPlate(door, slabTop, slabId, labelSize, labelMat, facing0, 'in');
        this.createDoorLabelPlate(door, slabTop, slabId, labelSize, labelMat, opposite[facing0] || 'north', 'out');
    }

    /**
     * One floor nameplate for a door. `facing` here is the side the plate is NOT on:
     * the plate sits on the opposite side, text-top pointing back at the door.
     * side: 'in' (interior nameplate, original) | 'out' (facing-side twin).
     */
    createDoorLabelPlate(door, slabTop, slabId, labelSize, labelMat, facing, side) {
        const labelHeight = slabTop + 0.1; // Just above slab
        const labelPlane = BABYLON.MeshBuilder.CreatePlane(
            side === 'in' ? `doorLabel_${slabId}_${door.id}` : `doorLabel_${slabId}_${door.id}_out`,
            { width: labelSize, height: labelSize / 4 },
            this.scene
        );

        // Place the label INSIDE the slab on the interior side of the door
        // (opposite 'facing', which is the exterior side), and turn the text so
        // it reads correctly when standing inside, facing the door.
        // modeltbabylon gen-10 — George 2026-08-26: labels straddled the wall
        // line (half hovering outside) and north-wall text was upside-down
        // because rotation.y was always 0.
        // Nameplate sits just past the dock leveler when the door has one (George 2026-08-27:
        // the leveler plates were covering the nameplates). Leveler spans T/2 .. T/2 + depth
        // from the centerline point C; the label is labelSize/4 deep.
        // Facing-side twin ('out'): clear the dock seal (1.5 ft off the exterior face) instead.
        const hasLeveler = side === 'in' && door.type === 'bay' && door.hasDockLeveler !== false;
        const hasSeal = side === 'out' && door.type === 'bay' && door.hasDockSeal !== false;
        const levelerDepth = door.levelerDepth || 6;
        const labelInset = hasLeveler
            ? ModelTBuilder.WALL_T / 2 + levelerDepth + 0.25 + labelSize / 8
            : hasSeal
                ? ModelTBuilder.WALL_T / 2 + 1.5 + 0.25 + labelSize / 8
                : 2.0; // ft from wall centerline to label center (label is 2ft deep)
        // Interior offset in BABYLON space (X east; Z = -SVG Y, so NORTH is +Z):
        //   faces north -> interior is south -> -Z
        //   faces south -> interior is north -> +Z
        //   faces east  -> interior is west  -> -X
        //   faces west  -> interior is east  -> +X
        const interior = {
            north: { dx: 0, dz: -labelInset },
            south: { dx: 0, dz:  labelInset },
            east:  { dx: -labelInset, dz: 0 },
            west:  { dx:  labelInset, dz: 0 }
        }[facing] || { dx: 0, dz: 0 };

        labelPlane.position = new BABYLON.Vector3(
            door.x + interior.dx,
            labelHeight,
            -door.y + interior.dz
        );
        labelPlane.rotation.x = Math.PI / 2; // Lay flat on floor
        // With rotation.x = PI/2 and rotation.y = 0, text-top points +Z (south).
        // Rotate so text-top points toward the door (its facing direction).
        labelPlane.rotation.y = {
            south: 0,
            north: Math.PI,
            east:  Math.PI / 2,
            west: -Math.PI / 2
        }[facing] || 0;

        labelPlane.material = labelMat;

        // Store door data for click handler
        labelPlane.metadata = {
            isDoorLabel: true,
            doorId: door.id,
            doorData: { ...door, slabId: slabId }
        };

        // Make clickable
        labelPlane.actionManager = new BABYLON.ActionManager(this.scene);
        labelPlane.actionManager.registerAction(
            new BABYLON.ExecuteCodeAction(
                BABYLON.ActionManager.OnPickTrigger,
                () => {
                    console.log('Door label clicked:', labelPlane.metadata.doorId);
                    if (window.showDoorPanel) {
                        window.showDoorPanel(labelPlane.metadata.doorData);
                    } else {
                        console.error('showDoorPanel not found on window');
                    }
                }
            )
        );

        this.meshes.doors.push(labelPlane);
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
        const wallThickness = ModelTBuilder.WALL_T;
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
        const wallThickness = ModelTBuilder.WALL_T;
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
