require('dotenv').config();

const express = require('express');
const cors = require('cors');
const path = require('path');
const config = require('./config/config');
const errorHandler = require('./middleware/errorHandler');

// Import API routes
const warehousesRouter = require('./routes/api/warehouses');
const cameraRouter = require('./routes/api/camera');
const svgRouter = require('./routes/api/svg');

const app = express();

// Middleware
app.use(cors(config.corsOptions));
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Serve warehouse3d.html as index
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// API Routes
app.use('/api/warehouses', warehousesRouter);
app.use('/api/warehouses', cameraRouter);
app.use('/api/warehouses', svgRouter);

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
  `);
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('SIGTERM received, shutting down gracefully...');
  server.close(() => {
    console.log('Server closed');
    process.exit(0);
  });
});
