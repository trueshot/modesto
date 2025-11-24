#!/usr/bin/env node

/**
 * Reload Warehouse Tool
 * Sends a reload command to the browser via WebSocket
 *
 * Usage:
 *   node reload-warehouse.js <warehouse>
 *
 * Example:
 *   node reload-warehouse.js lodge
 */

const WebSocket = require('ws');

// Parse command-line arguments
const args = process.argv.slice(2);

if (args.length < 1) {
  console.error('Usage: node reload-warehouse.js <warehouse>');
  process.exit(1);
}

const warehouseName = args[0];

// Connect to WebSocket server
const ws = new WebSocket('ws://localhost:8080');

ws.on('open', () => {
  // Send reload command
  const reloadCommand = {
    type: 'reload-warehouse',
    warehouseId: warehouseName,
    timestamp: Date.now()
  };

  ws.send(JSON.stringify(reloadCommand));

  // Wait a moment for message to be delivered, then close
  setTimeout(() => {
    ws.close();
    process.exit(0);
  }, 500);
});

ws.on('error', (error) => {
  console.error('WebSocket error:', error.message);
  process.exit(1);
});
