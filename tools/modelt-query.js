#!/usr/bin/env node

/**
 * ModelT Query Tool
 * Queries the live BabylonJS scene via WebSocket
 *
 * Usage:
 *   node modelt-query.js <warehouse> <command> [args]
 *
 * Commands:
 *   get-camera-intersection <cameraId>  Get camera raycasting intersection
 *   get-current-view                    Get current camera view and target
 *
 * Examples:
 *   node modelt-query.js lodge get-camera-intersection brownie
 *   node modelt-query.js lodge get-current-view
 */

const WebSocket = require('ws');

// Parse command-line arguments
const args = process.argv.slice(2);

if (args.length < 2) {
  console.error('Usage: node modelt-query.js <warehouse> <command> [args]');
  console.error('');
  console.error('Commands:');
  console.error('  get-camera-intersection <cameraId>  Get camera raycasting intersection');
  console.error('  get-current-view                    Get current camera view and target');
  process.exit(1);
}

const warehouseName = args[0];
const command = args[1];
const commandArgs = args.slice(2);

// Generate unique query ID
const queryId = `query_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

// Connect to WebSocket server
const ws = new WebSocket('ws://localhost:8080');

let timeout = null;

ws.on('open', () => {
  // Send query
  const query = {
    type: 'query',
    queryId: queryId,
    warehouseId: warehouseName,
    command: command
  };

  // Add cameraId only if provided
  if (commandArgs[0]) {
    query.cameraId = commandArgs[0];
  }

  ws.send(JSON.stringify(query));

  // Set timeout for response
  timeout = setTimeout(() => {
    console.error('Query timeout - no response received');
    ws.close();
    process.exit(1);
  }, 5000);
});

ws.on('message', (data) => {
  try {
    const message = JSON.parse(data);

    // Only process query responses for our query
    if (message.type === 'query-response' && message.queryId === queryId) {
      clearTimeout(timeout);

      // Output the result as JSON
      console.log(JSON.stringify(message.result, null, 2));

      ws.close();
      process.exit(0);
    }
  } catch (error) {
    console.error('Error parsing WebSocket message:', error);
  }
});

ws.on('error', (error) => {
  clearTimeout(timeout);
  console.error('WebSocket error:', error.message);
  process.exit(1);
});

ws.on('close', () => {
  // Connection closed
});
