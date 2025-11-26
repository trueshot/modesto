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

const app = express();

// Shared state for marked positions
const markedPositions = new Map(); // Map of warehouseId -> marked position

// Middleware
app.use(cors(config.corsOptions));
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Serve camera screenshots from the cameras folder
app.use('/cameras', express.static(path.join(__dirname, '..', 'cameras')));

// Serve warehouse3d.html as index
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// API Routes
app.use('/api/warehouses', warehousesRouter);
app.use('/api/warehouses', cameraRouter);
app.use('/api/warehouses', svgRouter);

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
        console.log(`🤖 CLAUDE test message for ${data.warehouseId}: "${data.message}"`);
        // Forward test message to all clients viewing this warehouse
        const warehouseClients = clients.get(data.warehouseId);
        if (warehouseClients) {
          warehouseClients.forEach(client => {
            if (client.readyState === WebSocket.OPEN) {
              client.send(JSON.stringify(data));
            }
          });
          console.log(`   ✓ Forwarded to ${warehouseClients.size} browser client(s)`);
        } else {
          console.log(`   ✗ No browser clients connected for warehouse "${data.warehouseId}"`);
        }
      }

      // Handle reload warehouse command from Claude
      else if (data.type === 'reload-warehouse') {
        clientType = 'claude-cli';
        console.log(`🤖 CLAUDE reload command for ${data.warehouseId}`);
        // Forward reload command to all clients viewing this warehouse
        const warehouseClients = clients.get(data.warehouseId);
        if (warehouseClients) {
          warehouseClients.forEach(client => {
            if (client.readyState === WebSocket.OPEN) {
              client.send(JSON.stringify(data));
            }
          });
          console.log(`   ✓ Reload sent to ${warehouseClients.size} browser client(s)`);
        } else {
          console.log(`   ✗ No browser clients connected for warehouse "${data.warehouseId}"`);
        }
      }

      // Handle query from Claude (via modelt-query.js)
      else if (data.type === 'query') {
        clientType = 'claude-cli';
        console.log(`🤖 CLAUDE query for ${data.warehouseId}: ${data.command}`);
        // Forward query to all clients viewing this warehouse
        const warehouseClients = clients.get(data.warehouseId);
        if (warehouseClients) {
          warehouseClients.forEach(client => {
            if (client.readyState === WebSocket.OPEN) {
              client.send(JSON.stringify(data));
            }
          });
        }
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

        const { execSync } = require('child_process');
        const cliPath = path.join(__dirname, '..', 'tools', 'modelt-cli.js');

        try {
          // Build CLI command with updates
          let cmd = `node "${cliPath}" ${data.warehouseId} update-door ${data.doorId}`;
          if (data.updates.x !== undefined) cmd += ` --x ${data.updates.x}`;
          if (data.updates.y !== undefined) cmd += ` --y ${data.updates.y}`;
          if (data.updates.type !== undefined) cmd += ` --type ${data.updates.type}`;
          if (data.updates.orientation !== undefined) cmd += ` --orientation ${data.updates.orientation}`;
          if (data.updates.facing !== undefined) cmd += ` --facing ${data.updates.facing}`;
          if (data.updates.width !== undefined) cmd += ` --width ${data.updates.width}`;
          if (data.updates.height !== undefined) cmd += ` --height ${data.updates.height}`;
          if (data.updates.bayWidth !== undefined) cmd += ` --bayWidth ${data.updates.bayWidth}`;
          if (data.updates.doorWidth !== undefined) cmd += ` --doorWidth ${data.updates.doorWidth}`;
          if (data.updates.portal !== undefined) cmd += ` --portal ${data.updates.portal}`;

          console.log(`   Running: ${cmd}`);
          const result = execSync(cmd, { encoding: 'utf8' });
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

        const { execSync } = require('child_process');
        const cliPath = path.join(__dirname, '..', 'tools', 'modelt-cli.js');

        try {
          // Build CLI command with updates
          let cmd = `node "${cliPath}" ${data.warehouseId} update-camera ${data.cameraId}`;
          if (data.updates.direction !== undefined) cmd += ` --direction ${data.updates.direction}`;
          if (data.updates.tilt !== undefined) cmd += ` --tilt ${data.updates.tilt}`;
          if (data.updates.roll !== undefined) cmd += ` --roll ${data.updates.roll}`;
          if (data.updates.viewingAngle !== undefined) cmd += ` --viewingAngle ${data.updates.viewingAngle}`;

          console.log(`   Running: ${cmd}`);
          const result = execSync(cmd, { encoding: 'utf8' });
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
