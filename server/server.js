require('dotenv').config();

const express = require('express');
const cors = require('cors');
const path = require('path');
const WebSocket = require('ws');
const config = require('./config/config');
const errorHandler = require('./middleware/errorHandler');

// Import API routes
const warehousesRouter = require('./routes/api/warehouses');
const cameraRouter = require('./routes/api/camera');
const svgRouter = require('./routes/api/svg');
const nvrRouter = require('./routes/api/nvr');
const discoveryRouter = require('./routes/api/discovery');

const app = express();

// Shared state for marked positions
const markedPositions = new Map(); // Map of warehouseId -> marked position

// Middleware
app.use(cors(config.corsOptions));
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Serve camera screenshots from the cameras folder
app.use('/cameras', express.static(path.join(__dirname, '..', 'cameras')));

// Serve warehouse static files (thumbnails, etc.)
app.use('/warehouses', express.static(path.join(__dirname, '..', 'warehouses')));

// Serve warehouse3d.html as index
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// NVR dashboard
app.get('/nvr', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'nvr.html'));
});

// API Routes
app.use('/api/warehouses', warehousesRouter);
app.use('/api/warehouses', cameraRouter);
app.use('/api/warehouses', svgRouter);
app.use('/api/nvrs', nvrRouter);
app.use('/api/discovery', discoveryRouter);

// Marked position endpoint (for CLI access)
app.get('/api/warehouses/:id/marked-position', (req, res) => {
  const warehouseId = req.params.id;
  const position = markedPositions.get(warehouseId);

  if (position) {
    res.json({
      success: true,
      warehouseId: warehouseId,
      position: position
    });
  } else {
    res.status(404).json({
      success: false,
      error: 'No marked position found for this warehouse'
    });
  }
});

// Health check
app.get('/api/health', (req, res) => {
  res.json({ status: 'OK', environment: config.env });
});

// Restart route — batch file loop will restart the process
app.post('/api/restart', (req, res) => {
  res.json({ status: 'restarting' });
  setTimeout(() => process.exit(0), 500);
});

// 404 handler
app.use((req, res) => {
  res.status(404).json({ error: 'Not Found' });
});

// Error handler (must be last)
app.use(errorHandler);

// Start server
const server = app.listen(config.port, () => {
  console.log(`
╔══════════════════════════════════════════╗
║     ModelT Warehouse Server               ║
╚══════════════════════════════════════════╝

  Environment: ${config.env}
  Port:        ${config.port}
  Warehouses:  ${config.warehousesPath}

  Available APIs:
  • GET  /api/warehouses              (list all)
  • GET  /api/warehouses/:id          (get spec + metadata)
  • GET  /api/warehouses/:id/metadata (metadata only)
  • GET  /api/warehouses/:id/cameras  (all cameras)
  • GET  /api/warehouses/:id/svg      (SVG file)
  • GET  /api/warehouses/:id/svg-data (SVG as JSON)

  Viewer:
  • http://localhost:${config.port}

  Health Check:
  • GET  /api/health

  WebSocket:
  • ws://localhost:8080 (real-time queries)
  `);
});

// WebSocket server for real-time queries
const wss = new WebSocket.Server({ port: 8080 });
const clients = new Map(); // Map of warehouseId -> Set of connected clients

// Forward a message verbatim to every OPEN browser registered for its
// warehouse. Returns the count actually sent. Used by the test / reload /
// door-state / query relay branches so the "fan out to this warehouse's
// browsers" logic lives in exactly one place. — modestomulti gen-6
function forwardToWarehouse(data) {
  const warehouseClients = clients.get(data.warehouseId);
  if (!warehouseClients || warehouseClients.size === 0) return 0;
  let sent = 0;
  warehouseClients.forEach(client => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(JSON.stringify(data));
      sent++;
    }
  });
  return sent;
}

// --- modelt-cli invocation (no shell) ---------------------------------------
// Run modelt-cli with an ARGUMENT ARRAY via execFileSync — node is spawned
// directly, no shell is involved, so warehouse/door/camera ids and numeric
// fields taken from WS payloads cannot inject shell commands. This replaces the
// old execSync string-building, which interpolated those values raw into a
// shell command. Returns the CLI's stdout. — modestomulti gen-6
const { execFileSync } = require('child_process');
function runModeltCli(args) {
  const cliPath = path.join(__dirname, '..', 'tools', 'modelt-cli.js');
  return execFileSync(process.execPath, [cliPath, ...args], { encoding: 'utf8' });
}
// Append `--flag value` to args iff value is set. numeric=true coerces and
// drops a non-finite value (garbage number skipped, never passed as text).
function pushFlag(args, flag, value, numeric) {
  if (value === undefined || value === null) return;
  if (numeric) {
    const n = Number(value);
    if (!Number.isFinite(n)) return;
    args.push(flag, String(n));
  } else {
    args.push(flag, String(value));
  }
}

wss.on('connection', (ws) => {
  console.log('WebSocket client connected (unidentified)');
  let clientWarehouseId = null;
  let clientType = 'unknown'; // 'browser', 'claude-cli', or 'unknown'

  ws.on('message', (message) => {
    try {
      const data = JSON.parse(message);

      // Handle registration
      if (data.type === 'register') {
        clientType = 'browser';
        clientWarehouseId = data.warehouseId;
        if (!clients.has(clientWarehouseId)) {
          clients.set(clientWarehouseId, new Set());
        }
        clients.get(clientWarehouseId).add(ws);
        console.log(`🌐 BROWSER client registered for warehouse: ${clientWarehouseId}`);
        ws.send(JSON.stringify({ type: 'registered', warehouseId: clientWarehouseId }));
      }

      // Handle test message from Claude
      else if (data.type === 'test') {
        clientType = 'claude-cli';
        const sent = forwardToWarehouse(data);
        console.log(`🤖 CLAUDE test message for ${data.warehouseId}: "${data.message}" → ${sent} browser client(s)`);
      }

      // Handle reload warehouse command from Claude
      else if (data.type === 'reload-warehouse') {
        clientType = 'claude-cli';
        const sent = forwardToWarehouse(data);
        console.log(`🤖 CLAUDE reload command for ${data.warehouseId} → ${sent} browser client(s)`);
      }

      // Door open/closed state from the sensing loop -> every browser viewing this warehouse
      //   {type:'door-state',  warehouseId, doorId, state:'open'|'closed'|0..1}
      //   {type:'door-states', warehouseId, doors:[{doorId, state}, ...]}
      // — modeltbabylon gen-11, relay via forwardToWarehouse — modestomulti gen-6
      else if (data.type === 'door-state' || data.type === 'door-states') {
        const n = data.type === 'door-states' ? (data.doors || []).length : 1;
        const sent = forwardToWarehouse(data);
        console.log(`🚪 door-state (${n}) for ${data.warehouseId} → ${sent} browser client(s)`);
      }

      // Handle query from Claude (via modelt-query.js)
      else if (data.type === 'query') {
        clientType = 'claude-cli';
        const sent = forwardToWarehouse(data);
        console.log(`🤖 CLAUDE query for ${data.warehouseId}: ${data.command} → ${sent} browser client(s)`);
      }

      // Handle response from browser
      else if (data.type === 'query-response') {
        // Broadcast response back to all connected clients (including CLI)
        wss.clients.forEach(client => {
          if (client.readyState === WebSocket.OPEN) {
            client.send(JSON.stringify(data));
          }
        });
      }

      // Handle save door from browser
      else if (data.type === 'save-door') {
        console.log(`💾 Saving door ${data.doorId} for ${data.warehouseId}:`, data.updates);

        try {
          const args = [String(data.warehouseId), 'update-door', String(data.doorId)];
          pushFlag(args, '--x', data.updates.x, true);
          pushFlag(args, '--y', data.updates.y, true);
          pushFlag(args, '--type', data.updates.type);
          pushFlag(args, '--orientation', data.updates.orientation);
          pushFlag(args, '--facing', data.updates.facing);
          pushFlag(args, '--width', data.updates.width, true);
          pushFlag(args, '--height', data.updates.height, true);
          pushFlag(args, '--bayWidth', data.updates.bayWidth, true);
          pushFlag(args, '--doorWidth', data.updates.doorWidth, true);
          pushFlag(args, '--portal', data.updates.portal);

          console.log(`   Running: modelt-cli ${args.join(' ')}`);
          const result = runModeltCli(args);
          console.log(`   ✓ Door saved successfully`);

          ws.send(JSON.stringify({
            type: 'save-confirmed',
            warehouseId: data.warehouseId,
            doorId: data.doorId,
            success: true
          }));
        } catch (error) {
          console.error(`   ✗ Save failed:`, error.message);
          ws.send(JSON.stringify({
            type: 'save-error',
            warehouseId: data.warehouseId,
            doorId: data.doorId,
            error: error.message
          }));
        }
      }

      // Handle save camera from browser
      else if (data.type === 'save-camera') {
        console.log(`💾 Saving camera ${data.cameraId} for ${data.warehouseId}:`, data.updates);

        try {
          const args = [String(data.warehouseId), 'update-camera', String(data.cameraId)];
          // x/y: mount position from the viewer's Move Mount drag-snap (modeltbabylon
          // gen-16). All numeric fields are finite-coerced by pushFlag; execFileSync
          // takes them as array args, so none of this touches a shell. — modestomulti gen-6
          pushFlag(args, '--x', data.updates.x, true);
          pushFlag(args, '--y', data.updates.y, true);
          pushFlag(args, '--elevation', data.updates.elevation, true);
          pushFlag(args, '--direction', data.updates.direction, true);
          pushFlag(args, '--tilt', data.updates.tilt, true);
          pushFlag(args, '--roll', data.updates.roll, true);
          pushFlag(args, '--viewingAngle', data.updates.viewingAngle, true);

          console.log(`   Running: modelt-cli ${args.join(' ')}`);
          const result = runModeltCli(args);
          console.log(`   ✓ Camera saved successfully`);

          ws.send(JSON.stringify({
            type: 'save-confirmed',
            warehouseId: data.warehouseId,
            cameraId: data.cameraId,
            success: true
          }));
        } catch (error) {
          console.error(`   ✗ Save failed:`, error.message);
          ws.send(JSON.stringify({
            type: 'save-error',
            warehouseId: data.warehouseId,
            cameraId: data.cameraId,
            error: error.message
          }));
        }
      }

      // Handle create camera from browser (camera placement mode)
      else if (data.type === 'create-camera') {
        console.log(`📷 Creating camera for ${data.warehouseId}:`, data.camera);

        try {
          // add-camera requires a slab; use first slab (mercury) by default
          const cam = data.camera;
          const args = [String(data.warehouseId), 'add-camera', '--slab', 'mercury'];
          pushFlag(args, '--x', cam.x, true);
          pushFlag(args, '--y', cam.y, true);
          pushFlag(args, '--elevation', cam.elevation, true);
          pushFlag(args, '--direction', cam.direction, true);
          pushFlag(args, '--tilt', cam.tilt, true);
          pushFlag(args, '--viewingAngle', cam.viewingAngle, true);
          pushFlag(args, '--range', cam.range, true);

          console.log(`   Running: modelt-cli ${args.join(' ')}`);
          const result = runModeltCli(args);
          console.log(`   ✓ Camera created successfully`);

          // Parse result to get new camera ID
          let newCameraId = null;
          try {
            const resultJson = JSON.parse(result);
            newCameraId = resultJson.camera?.id;
          } catch (e) {}

          ws.send(JSON.stringify({
            type: 'create-confirmed',
            warehouseId: data.warehouseId,
            cameraId: newCameraId || cam.id,
            success: true
          }));

          // Broadcast reload to all clients viewing this warehouse
          const warehouseClients = clients.get(data.warehouseId);
          if (warehouseClients) {
            warehouseClients.forEach(client => {
              if (client.readyState === WebSocket.OPEN) {
                client.send(JSON.stringify({
                  type: 'reload-warehouse',
                  warehouseId: data.warehouseId
                }));
              }
            });
            console.log(`   ✓ Reload sent to ${warehouseClients.size} browser client(s)`);
          }
        } catch (error) {
          console.error(`   ✗ Create failed:`, error.message);
          ws.send(JSON.stringify({
            type: 'create-error',
            warehouseId: data.warehouseId,
            error: error.message
          }));
        }
      }

      // Handle camera mapping: mount (food name) -> camera IP
      else if (data.type === 'save-camera-mapping') {
        try {
          const Database = require('better-sqlite3');
          const dbPath = path.join(config.warehousesPath, data.warehouseId, `${data.warehouseId}.db`);
          const db = new Database(dbPath);

          // Find camera MAC by IP
          const camera = db.prepare('SELECT mac FROM cameras WHERE ip = ?').get(data.cameraIp);

          if (camera) {
            // Check if linkage exists for this mount
            const existing = db.prepare('SELECT id FROM linkages WHERE mount_id = ?').get(data.mountId);
            if (existing) {
              db.prepare('UPDATE linkages SET camera_mac = ?, verified_by = ?, confidence = ?, verified_at = CURRENT_TIMESTAMP WHERE mount_id = ?')
                .run(camera.mac, 'manual', 'verified', data.mountId);
            } else {
              db.prepare('INSERT INTO linkages (mount_id, camera_mac, verified_by, confidence, verified_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)')
                .run(data.mountId, camera.mac, 'manual', 'verified');
            }
            console.log(`📷 Mapped mount "${data.mountId}" → ${data.cameraIp} (MAC: ${camera.mac})`);
          } else {
            console.log(`📷 Mapped mount "${data.mountId}" → ${data.cameraIp} (no MAC in db, IP-only)`);
          }

          db.close();

          ws.send(JSON.stringify({
            type: 'mapping-confirmed',
            mountId: data.mountId,
            cameraIp: data.cameraIp
          }));
        } catch (err) {
          console.error('Failed to save camera mapping:', err.message);
        }
      }

      // Handle marked position from browser
      else if (data.type === 'mark-position') {
        // Store both camera and position data
        markedPositions.set(data.warehouseId, {
          camera: data.camera,
          position: data.position,
          timestamp: data.timestamp
        });

        const cameraInfo = data.camera && data.camera.name ? ` from camera ${data.camera.name} (#${data.camera.number})` : '';
        console.log(`📍 Position marked for ${data.warehouseId}${cameraInfo}:`);
        console.log(`   → (${data.position.x}, ${data.position.y}) on ${data.position.surface}`);

        // Send confirmation back to browser
        ws.send(JSON.stringify({
          type: 'mark-confirmed',
          warehouseId: data.warehouseId,
          camera: data.camera,
          position: data.position
        }));
      }
    } catch (error) {
      console.error('WebSocket message error:', error);
      ws.send(JSON.stringify({ type: 'error', error: error.message }));
    }
  });

  ws.on('close', () => {
    if (clientWarehouseId && clients.has(clientWarehouseId)) {
      clients.get(clientWarehouseId).delete(ws);
      if (clients.get(clientWarehouseId).size === 0) {
        clients.delete(clientWarehouseId);
      }
    }

    // Log disconnect with client type
    if (clientType === 'browser') {
      console.log(`🌐 BROWSER client disconnected${clientWarehouseId ? ` (was viewing ${clientWarehouseId})` : ''}`);
    } else if (clientType === 'claude-cli') {
      console.log(`🤖 CLAUDE CLI client disconnected`);
    } else {
      console.log(`❓ Unknown client disconnected`);
    }
  });
});

console.log('WebSocket server listening on port 8080');

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('SIGTERM received, shutting down gracefully...');
  server.close(() => {
    console.log('Server closed');
    process.exit(0);
  });
});
