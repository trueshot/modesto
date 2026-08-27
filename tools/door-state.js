#!/usr/bin/env node
// door-state.js — push a door's open/closed state into the 3D twin over the :8080 socket.
// Author: modeltbabylon gen-11
//
//   node tools/door-state.js <warehouseId> <doorId> <open|closed|0..1>
//   node tools/door-state.js lodge quincy open
//   node tools/door-state.js lodge quincy 0.5          # half open
//   node tools/door-state.js lodge --batch '{"quincy":"open","fillmore":"closed"}'
//
// The server relays {type:'door-state'|'door-states'} to every browser viewing
// that warehouse; cooler doors slide, bay/rollup curtains roll up into the housing.
// Env: MODELT_WS (default ws://localhost:8080)

const WebSocket = require(require('path').join(__dirname, '..', 'server', 'node_modules', 'ws'));

const [warehouseId, a, b] = process.argv.slice(2);
if (!warehouseId || !a) {
  console.error('usage: door-state.js <warehouseId> <doorId> <open|closed|0..1>');
  console.error('       door-state.js <warehouseId> --batch \'{"doorId":"open",...}\'');
  process.exit(1);
}

function parseState(s) {
  const n = parseFloat(s);
  return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : (String(s).toLowerCase() === 'open' ? 'open' : 'closed');
}

let msg;
if (a === '--batch') {
  const map = JSON.parse(b || '{}');
  msg = { type: 'door-states', warehouseId, doors: Object.entries(map).map(([doorId, s]) => ({ doorId, state: parseState(s) })) };
} else {
  msg = { type: 'door-state', warehouseId, doorId: a, state: parseState(b) };
}

const ws = new WebSocket(process.env.MODELT_WS || 'ws://localhost:8080');
ws.on('open', () => {
  ws.send(JSON.stringify(msg));
  console.log('sent', JSON.stringify(msg));
  setTimeout(() => ws.close(), 300);
});
ws.on('error', (e) => { console.error('socket error:', e.message); process.exit(2); });
