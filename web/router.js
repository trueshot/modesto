// web/router.js — Marshal Explainer Page for modestomulti (billet: modestomultiServer)
// Interactive, live view of the ModelT server infrastructure I own:
//   • services.yaml port map (with live up/down probes)
//   • every Express REST endpoint (parsed live from the route source)
//   • every WebSocket message type (parsed live from server.js)
//   • a health board that probes each service port on demand
//
// Runs INSIDE the Marshal Gateway process (gateway provides express).
// No own server, no npm install — core Node modules only, so it resolves
// regardless of which node_modules the gateway injects.
// Author: modestomulti gen-6
'use strict';

const express = require('express');
const fs = require('fs');
const path = require('path');
const net = require('net');

const router = express.Router();

// Absolute roots on lodge_cat (this MEP runs where the ModelT code lives).
const MODESTO = 'c:/clients/modesto';
const SERVER_JS = path.join(MODESTO, 'server', 'server.js');
const ROUTES_DIR = path.join(MODESTO, 'server', 'routes', 'api');
const SERVICES_YAML = path.join(MODESTO, 'services.yaml');

// Who owns each service/domain — helps George see who to ask for new abilities.
const OWNERS = {
  'modelt-server':   'modestomulti (billet: modestomultiServer)',
  'nvr-service':     'NVR probe (python) — routes: modestomulti',
  'asksam':          'AskSAM',
  'camera-service':  'modeltcamerascat',
  'detection':       'AprilTag detection',
  'fasttag':         'FastTag',
  'marshal-gateway': 'marshalltown (persistence: modestomulti)',
};

// ---- tiny purpose-built YAML reader (fixed 2-space shape, zero deps) --------
function readServices() {
  const out = [];
  let text;
  try { text = fs.readFileSync(SERVICES_YAML, 'utf8'); }
  catch (e) { return out; }
  let cur = null;
  for (const raw of text.split(/\r?\n/)) {
    if (!raw.trim() || raw.trim().startsWith('#')) continue;
    const svc = raw.match(/^ {2}([A-Za-z0-9_-]+):\s*$/);      // "  key:"
    if (svc) { cur = { key: svc[1] }; out.push(cur); continue; }
    const field = raw.match(/^ {4}([A-Za-z0-9_]+):\s*(.+?)\s*$/); // "    field: value"
    if (field && cur) {
      let v = field[2];
      if (v === 'true') v = true; else if (v === 'false') v = false;
      else if (/^\d+$/.test(v)) v = parseInt(v, 10);
      cur[field[1]] = v;
    }
  }
  return out;
}

// ---- live probe: is something LISTENING on this port? -----------------------
function probePort(port, timeout = 600) {
  return new Promise((resolve) => {
    if (!port) return resolve(false);
    const sock = new net.Socket();
    let done = false;
    const finish = (up) => { if (done) return; done = true; sock.destroy(); resolve(up); };
    sock.setTimeout(timeout);
    sock.once('connect', () => finish(true));
    sock.once('timeout', () => finish(false));
    sock.once('error', () => finish(false));
    sock.connect(port, '127.0.0.1');
  });
}

// ---- live parse: mount prefixes + every REST endpoint ----------------------
function parseRoutes() {
  const endpoints = [];
  let serverSrc = '';
  try { serverSrc = fs.readFileSync(SERVER_JS, 'utf8'); } catch (e) { return endpoints; }

  // varName -> ./routes/api/<file>   e.g. const cameraRouter = require('./routes/api/camera');
  const varToFile = {};
  const reReq = /const\s+(\w+)\s*=\s*require\(['"]\.\/routes\/api\/(\w+)['"]\)/g;
  let m;
  while ((m = reReq.exec(serverSrc))) varToFile[m[1]] = m[2] + '.js';

  // varName -> mount prefix          e.g. app.use('/api/warehouses', cameraRouter);
  const fileMounts = {}; // file -> [prefixes]
  const reMount = /app\.use\(\s*['"]([^'"]+)['"]\s*,\s*(\w+)\s*\)/g;
  while ((m = reMount.exec(serverSrc))) {
    const file = varToFile[m[2]];
    if (!file) continue;
    (fileMounts[file] = fileMounts[file] || []).push(m[1]);
  }

  // inline app.METHOD routes defined directly in server.js
  const reInline = /app\.(get|post|put|delete|patch)\(\s*['"]([^'"]+)['"]/g;
  while ((m = reInline.exec(serverSrc))) {
    endpoints.push({ method: m[1].toUpperCase(), path: m[2], source: 'server.js', mount: '(root)' });
  }

  // each route file's router.METHOD, prefixed by its mount(s)
  for (const [file, prefixes] of Object.entries(fileMounts)) {
    let src = '';
    try { src = fs.readFileSync(path.join(ROUTES_DIR, file), 'utf8'); } catch (e) { continue; }
    const reRoute = /router\.(get|post|put|delete|patch)\(\s*['"]([^'"]+)['"]/g;
    let r;
    while ((r = reRoute.exec(src))) {
      for (const pfx of prefixes) {
        const sub = r[2] === '/' ? '' : r[2];
        endpoints.push({ method: r[1].toUpperCase(), path: (pfx + sub) || '/', source: file, mount: pfx });
      }
    }
  }
  endpoints.sort((a, b) => (a.path + a.method).localeCompare(b.path + b.method));
  return endpoints;
}

// ---- live parse: WebSocket message types handled by the server -------------
function parseWebSocket() {
  let src = '';
  try { src = fs.readFileSync(SERVER_JS, 'utf8'); } catch (e) { return []; }
  const types = new Set();
  const re = /data\.type\s*===\s*['"]([\w-]+)['"]/g;
  let m;
  while ((m = re.exec(src))) types.add(m[1]);
  // direction: what the server emits back (best-effort, informational)
  const emitted = new Set();
  const reEmit = /type:\s*['"]([\w-]+)['"]/g;
  while ((m = reEmit.exec(src))) emitted.add(m[1]);
  return {
    port: 8080,
    inbound: [...types].sort(),
    outbound: [...emitted].filter(t => !types.has(t)).sort(),
  };
}

// ============================ API =========================================
router.get('/api/services', async (req, res) => {
  const svcs = readServices();
  const rows = await Promise.all(svcs.map(async (s) => ({
    key: s.key,
    name: s.name || s.key,
    port: s.port || null,
    command: s.command || '',
    cwd: s.cwd || '.',
    autostart: !!s.autostart,
    owner: OWNERS[s.key] || '',
    up: s.port ? await probePort(s.port) : null,
  })));
  res.json(rows);
});

router.get('/api/routes', (req, res) => res.json(parseRoutes()));

router.get('/api/websocket', (req, res) => res.json(parseWebSocket()));

router.get('/api/health', async (req, res) => {
  const svcs = readServices();
  const checks = await Promise.all(svcs.map(async (s) => ({
    key: s.key, name: s.name || s.key, port: s.port || null,
    up: s.port ? await probePort(s.port) : null,
  })));
  const upCount = checks.filter(c => c.up).length;
  res.json({ status: 'ok', checkedAt: new Date().toISOString(),
    up: upCount, total: checks.length, services: checks });
});

// ---- page (relative api paths per the MEP standard) ------------------------
router.get('/', (req, res) => {
  if (!req.originalUrl.endsWith('/')) return res.redirect(req.originalUrl + '/');
  res.sendFile('index.html', { root: __dirname });
});

module.exports = router;
