#!/usr/bin/env node

/**
 * WebSocket Test Tool
 * Sends a test message to the browser via WebSocket server
 *
 * Usage:
 *   node test-websocket.js <warehouse> <message>
 *
 * Example:
 *   node test-websocket.js lodge "Hello from Claude!"
 */

const WebSocket = require('ws');

// Parse command-line arguments
const args = process.argv.slice(2);

if (args.length < 2) {
  console.error('Usage: node test-websocket.js <warehouse> <message>');
  process.exit(1);
}

const warehouseName = args[0];
const message = args[1];

// Connect to WebSocket server
const ws = new WebSocket('ws://localhost:8080');

let timeout = null;

ws.on('open', () => {
  console.log('Connected to WebSocket server');

  // Send test message
  const testMessage = {
    type: 'test',
    warehouseId: warehouseName,
    message: message,
    timestamp: Date.now()
  };

  console.log('Sending test message:', testMessage);
  ws.send(JSON.stringify(testMessage));

  // Wait a moment for message to be delivered, then close
  setTimeout(() => {
    console.log('Test message sent successfully');
    ws.close();
    process.exit(0);
  }, 1000);
});

ws.on('error', (error) => {
  console.error('WebSocket error:', error.message);
  process.exit(1);
});

ws.on('close', () => {
  // Connection closed
});
