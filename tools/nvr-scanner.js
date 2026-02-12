#!/usr/bin/env node
/**
 * NVR Scanner Tool
 * Multi-method discovery: ONVIF + port scan + fingerprinting
 * Ground truth → lodge.db pipeline
 *
 * Usage:
 *   node nvr-scanner.js scan [--subnet 192.168.0.0/24]
 *   node nvr-scanner.js probe <ip>
 *   node nvr-scanner.js sync [--dry-run]
 *
 * Author: novicat gen-3, gen-4 (async spawn fix)
 */

const { execSync, spawn } = require('child_process');
const path = require('path');
const net = require('net');
const http = require('http');
const Database = require('better-sqlite3');

const DB_PATH = path.join(__dirname, '..', 'warehouses', 'lodge', 'lodge.db');
const ONVIF_SCAN = path.join(__dirname, 'onvif-scan.py');
const ONVIF_INFO = path.join(__dirname, 'onvif-info.py');

// Common NVR ports
const NVR_PORTS = [80, 443, 554, 8000, 8080, 37777, 9000];

// Quick discovery ports (if any of these are open, device is worth probing)
const DISCOVERY_PORTS = [80, 554];

// MAC prefixes for known manufacturers
const MAC_PREFIXES = {
  '00:23:63': { manufacturer: 'GW Security', likely: 'nvr' },
  'e4:f1:4c': { manufacturer: 'DigitalWatchdog', likely: 'nvr' },
  'c4:79:05': { manufacturer: 'UNIVIEW', likely: 'nvr' },
  '00:0F:3A': { manufacturer: 'DigitalWatchdog', likely: 'camera' },
  'F0:00:00': { manufacturer: 'Generic Chinese', likely: 'camera' },
  'f4:00:00': { manufacturer: 'Generic Chinese', likely: 'camera' },
};

// Model name patterns
const NVR_MODEL_PATTERNS = ['NVR', 'DVR', 'XVR', 'Recorder'];
const CAMERA_MODEL_PATTERNS = ['IPC', 'Camera', 'Cam', 'Bullet', 'Dome'];

function getDb() {
  return new Database(DB_PATH);
}

/**
 * Check if a port is open on a host
 */
function checkPort(host, port, timeout = 2000) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(timeout);

    socket.on('connect', () => {
      socket.destroy();
      resolve(true);
    });

    socket.on('timeout', () => {
      socket.destroy();
      resolve(false);
    });

    socket.on('error', () => {
      socket.destroy();
      resolve(false);
    });

    socket.connect(port, host);
  });
}

/**
 * Scan multiple ports on a host
 */
async function scanPorts(host, ports = NVR_PORTS) {
  const results = {};
  const promises = ports.map(async (port) => {
    results[port] = await checkPort(host, port);
  });
  await Promise.all(promises);
  return results;
}

/**
 * Generate all IPs in a /24 subnet
 */
function generateSubnetIPs(subnet) {
  // Parse subnet like "192.168.0.0/24" or "192.168.0.0" or "192.168.0"
  // Extract first 3 octets
  const cleaned = subnet.replace(/\/24$/, '');
  const parts = cleaned.split('.');
  const base = parts.slice(0, 3).join('.');
  const ips = [];
  for (let i = 1; i < 255; i++) {
    ips.push(`${base}.${i}`);
  }
  return ips;
}

/**
 * Quick check if an IP has any camera/NVR ports open
 * Returns true if device is worth probing further
 */
async function quickCheck(ip, timeout = 1000) {
  for (const port of DISCOVERY_PORTS) {
    if (await checkPort(ip, port, timeout)) {
      return true;
    }
  }
  return false;
}

/**
 * Scan subnet for devices with camera/NVR ports open
 * Much more reliable than WS-Discovery multicast
 */
async function scanSubnet(subnet, options = {}) {
  const ips = generateSubnetIPs(subnet);
  const concurrency = options.concurrency || 20;
  const timeout = options.timeout || 1000;
  const found = [];

  console.log(`      Scanning ${ips.length} IPs (concurrency: ${concurrency})...`);

  // Process in batches for controlled concurrency
  for (let i = 0; i < ips.length; i += concurrency) {
    const batch = ips.slice(i, i + concurrency);
    const results = await Promise.all(
      batch.map(async (ip) => {
        const hasPort = await quickCheck(ip, timeout);
        return hasPort ? ip : null;
      })
    );
    found.push(...results.filter(Boolean));

    // Progress indicator
    const pct = Math.round(((i + batch.length) / ips.length) * 100);
    process.stdout.write(`\r      Progress: ${pct}% (${found.length} found)`);
  }
  console.log(''); // newline after progress

  return found;
}

/**
 * Run ONVIF scan and parse results (async to handle Windows subprocess issues)
 */
function runOnvifScan(timeout = 15) {
  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';
    let resolved = false;

    const proc = spawn('python', [ONVIF_SCAN, '--json', '--timeout', String(timeout)], {
      stdio: ['pipe', 'pipe', 'pipe']
    });

    proc.stdout.on('data', (data) => {
      stdout += data.toString();
    });

    proc.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    const timer = setTimeout(() => {
      if (!resolved) {
        resolved = true;
        proc.kill();
        console.error('ONVIF scan timed out after', timeout + 10, 'seconds');
        resolve([]);
      }
    }, (timeout + 10) * 1000);

    proc.on('close', (code) => {
      clearTimeout(timer);
      if (resolved) return;
      resolved = true;

      if (code !== 0 && stdout.length === 0) {
        console.error('ONVIF scan failed with code', code);
        if (stderr) console.error('  stderr:', stderr.slice(0, 200));
        resolve([]);
        return;
      }

      // Extract JSON from output (may have warnings before it)
      const jsonStart = stdout.indexOf('[');
      if (jsonStart === -1) {
        console.error('ONVIF scan returned no JSON');
        resolve([]);
        return;
      }

      try {
        resolve(JSON.parse(stdout.slice(jsonStart)));
      } catch (err) {
        console.error('ONVIF scan JSON parse error:', err.message);
        resolve([]);
      }
    });

    proc.on('error', (err) => {
      clearTimeout(timer);
      if (resolved) return;
      resolved = true;
      console.error('ONVIF scan spawn error:', err.message);
      resolve([]);
    });
  });
}

/**
 * Query single device via ONVIF
 */
function queryOnvif(ip, username = 'admin', password = '') {
  try {
    const result = execSync(
      `python "${ONVIF_INFO}" ${ip} -u ${username} -p "${password}" --json`,
      { encoding: 'utf-8', timeout: 15000, stdio: ['pipe', 'pipe', 'pipe'] }
    );
    return JSON.parse(result);
  } catch (err) {
    return { ip, status: 'error', error: err.message };
  }
}

/**
 * Probe device using Dahua CGI protocol (GW Security, Dahua, Amcrest)
 * Also detects GW Security XML info page format
 * Returns device info if Dahua-protocol or GW Security is detected
 */
function probeDahuaCGI(ip, username = 'admin', password = '', timeout = 5000) {
  return new Promise((resolve) => {
    const auth = Buffer.from(`${username}:${password}`).toString('base64');

    const options = {
      hostname: ip,
      port: 80,
      path: '/cgi-bin/configManager.cgi?action=getConfig&name=Network',
      method: 'GET',
      timeout,
      headers: {
        'Authorization': `Basic ${auth}`
      }
    };

    const req = http.request(options, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        // Check for GW Security XML info page format
        if (res.statusCode === 200 && data.includes('<custom>GW</custom>')) {
          const info = {
            ip,
            protocol: 'gw-security',
            status: 'ok',
            manufacturer: 'GW Security'
          };

          // Parse XML for additional info
          const portMatch = data.match(/<port>(\d+)<\/port>/);
          if (portMatch) info.sdkPort = parseInt(portMatch[1]);

          const devtypeMatch = data.match(/<devtype>(\d+)<\/devtype>/);
          if (devtypeMatch) info.devtype = devtypeMatch[1];

          // GW Security with SDK port 9000 is typically an NVR
          if (info.sdkPort === 9000) {
            info.likelyType = 'nvr';
          }

          resolve(info);
          return;
        }

        // Check for standard Dahua key=value config format
        if (res.statusCode === 200 && data.length > 400 && data.includes('table.')) {
          const info = {
            ip,
            protocol: 'dahua-cgi',
            status: 'ok',
            manufacturer: 'Dahua-compatible',
            configSize: data.length
          };

          // Parse config for model/hostname hints
          const hostnameMatch = data.match(/table\.Network\.HostName=([^\r\n]+)/);
          if (hostnameMatch) info.hostname = hostnameMatch[1];

          // Try to determine if it's an NVR by checking for multiple video inputs
          const videoMatch = data.match(/table\.Network\.Interfaces\[(\d+)\]/g);
          if (videoMatch && videoMatch.length > 4) {
            info.likelyType = 'nvr';
          }

          resolve(info);
        } else if (res.statusCode === 401) {
          // Auth required but endpoint exists - still Dahua protocol
          resolve({
            ip,
            protocol: 'dahua-cgi',
            status: 'auth_required',
            manufacturer: 'Dahua-compatible'
          });
        } else {
          resolve(null);
        }
      });
    });

    req.on('error', () => resolve(null));
    req.on('timeout', () => { req.destroy(); resolve(null); });
    req.end();
  });
}

/**
 * Check if device can serve snapshots (confirms NVR with cameras)
 */
function checkDahuaSnapshot(ip, username = 'admin', password = '', channel = 0, timeout = 5000) {
  return new Promise((resolve) => {
    const auth = Buffer.from(`${username}:${password}`).toString('base64');

    const options = {
      hostname: ip,
      port: 80,
      path: `/cgi-bin/snapshot.cgi?channel=${channel}`,
      method: 'GET',
      timeout,
      headers: {
        'Authorization': `Basic ${auth}`
      }
    };

    const req = http.request(options, (res) => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        const data = Buffer.concat(chunks);
        // Check for JPEG magic bytes: 0xFF 0xD8 0xFF
        if (res.statusCode === 200 && data.length > 1000 &&
            data[0] === 0xFF && data[1] === 0xD8 && data[2] === 0xFF) {
          resolve({ hasSnapshot: true, channel, size: data.length });
        } else {
          resolve({ hasSnapshot: false });
        }
      });
    });

    req.on('error', () => resolve({ hasSnapshot: false }));
    req.on('timeout', () => { req.destroy(); resolve({ hasSnapshot: false }); });
    req.end();
  });
}

/**
 * Classify device as NVR or camera based on multiple signals
 * Priority: ONVIF type > profile count > model name > MAC prefix > port pattern
 */
function classifyDevice(device, portScan = {}) {
  const signals = {
    type: null,
    confidence: 'low',
    reasons: []
  };

  // HIGHEST PRIORITY: Check ONVIF scopes for device type
  if (device.scopes) {
    const scopeStr = device.scopes.join(' ').toLowerCase();
    if (scopeStr.includes('net_video_recorder') || scopeStr.includes('hybrid_nvr')) {
      signals.type = 'nvr';
      signals.confidence = 'high';
      signals.reasons.push('ONVIF scope: Net_Video_Recorder');
      return signals; // Definitive — stop here
    } else if (scopeStr.includes('video_encoder') || scopeStr.includes('network_video_transmitter')) {
      signals.type = 'camera';
      signals.confidence = 'high';
      signals.reasons.push('ONVIF scope: video_encoder');
      return signals; // Definitive — stop here
    }
  }

  // Check profile/channel count (NVRs have many profiles)
  if (device.profiles && device.profiles.length > 4) {
    signals.type = 'nvr';
    signals.confidence = 'medium';
    signals.reasons.push(`${device.profiles.length} media profiles (multi-channel)`);
    return signals;
  }

  // Check model name
  if (device.model) {
    const modelUpper = device.model.toUpperCase();
    if (NVR_MODEL_PATTERNS.some(p => modelUpper.includes(p))) {
      signals.type = 'nvr';
      signals.confidence = 'medium';
      signals.reasons.push(`Model name contains NVR pattern: ${device.model}`);
      return signals;
    } else if (CAMERA_MODEL_PATTERNS.some(p => modelUpper.includes(p))) {
      signals.type = 'camera';
      signals.confidence = 'medium';
      signals.reasons.push(`Model name contains camera pattern: ${device.model}`);
      return signals;
    }
  }

  // Check MAC prefix (manufacturer hint)
  if (device.mac) {
    const macNorm = device.mac.toUpperCase().replace(/[:-]/g, '');
    for (const [macPrefix, info] of Object.entries(MAC_PREFIXES)) {
      const prefixNorm = macPrefix.toUpperCase().replace(/[:-]/g, '');
      if (macNorm.startsWith(prefixNorm)) {
        signals.type = info.likely;
        signals.confidence = 'low';
        signals.reasons.push(`MAC prefix ${macPrefix}: ${info.manufacturer} (typically ${info.likely})`);
        return signals;
      }
    }
  }

  // LOWEST PRIORITY: Port pattern (many cameras also have SDK ports)
  // Only use this if nothing else matched
  const openPorts = Object.entries(portScan).filter(([_, open]) => open).map(([p]) => parseInt(p));
  const hasMultipleSDKPorts = [8000, 37777, 9000].filter(p => openPorts.includes(p)).length >= 2;
  if (hasMultipleSDKPorts && openPorts.includes(554)) {
    signals.type = 'nvr';
    signals.confidence = 'low';
    signals.reasons.push(`Multiple SDK ports: ${openPorts.join(', ')}`);
    return signals;
  }

  // Default to camera if still unknown
  signals.type = 'camera';
  signals.confidence = 'low';
  signals.reasons.push('Default classification (no strong NVR signals)');

  return signals;
}

/**
 * Full scan: subnet scan + ONVIF probe + classification
 */
async function fullScan(options = {}) {
  const subnet = options.subnet || '192.168.0';
  console.log('Starting multi-method NVR scan...\n');

  // Step 1: Subnet scan (find all devices with camera/NVR ports)
  console.log('[1/4] Subnet scan (finding devices with ports 80/554)...');
  const subnetDevices = await scanSubnet(subnet, { concurrency: 30, timeout: 1000 });
  console.log(`      Found ${subnetDevices.length} devices with camera/NVR ports\n`);

  // Step 2: Also try WS-Discovery (catches some devices faster, provides initial metadata)
  console.log('[2/4] WS-Discovery (ONVIF multicast)...');
  const wsDevices = await runOnvifScan(options.timeout || 10);
  const wsIPs = new Set(wsDevices.filter(d => d.status === 'ok').map(d => d.ip));
  console.log(`      Found ${wsIPs.size} ONVIF-responding devices\n`);

  // Step 3: Merge and probe each device
  console.log('[3/4] Probing devices via ONVIF...');
  const allIPs = [...new Set([...subnetDevices, ...wsIPs])].sort((a, b) => {
    const aParts = a.split('.').map(Number);
    const bParts = b.split('.').map(Number);
    return aParts[3] - bParts[3];
  });
  console.log(`      ${allIPs.length} unique IPs to probe\n`);

  const results = {
    nvrs: [],
    cameras: [],
    unknown: []
  };

  for (const ip of allIPs) {
    // Check if we already have WS-Discovery data for this IP
    let device = wsDevices.find(d => d.ip === ip && d.status === 'ok');

    // If not, probe directly
    if (!device) {
      device = queryOnvif(ip, 'admin', '');
      if (device.status !== 'ok') {
        device = queryOnvif(ip, 'admin', 'Dad5eeeee!');
      }
    }

    // Port scan
    const portScan = await scanPorts(ip);

    if (device.status !== 'ok') {
      // ONVIF failed - try Dahua CGI protocol
      const dahuaInfo = await probeDahuaCGI(ip, 'admin', '');

      if (dahuaInfo && dahuaInfo.status === 'ok') {
        // Dahua/GW Security protocol detected
        let classification;

        if (dahuaInfo.likelyType === 'nvr') {
          // SDK port 9000 strongly indicates NVR
          classification = {
            type: 'nvr',
            confidence: 'high',
            reasons: [`${dahuaInfo.protocol} with SDK port ${dahuaInfo.sdkPort}`]
          };
        } else {
          // Check for snapshot capability to confirm NVR vs camera
          const snapshot = await checkDahuaSnapshot(ip, 'admin', '', 0);
          classification = {
            type: snapshot.hasSnapshot ? 'nvr' : 'camera',
            confidence: snapshot.hasSnapshot ? 'high' : 'medium',
            reasons: [snapshot.hasSnapshot ? `${dahuaInfo.protocol} + snapshot` : dahuaInfo.protocol]
          };
        }

        const enriched = {
          ip,
          protocol: dahuaInfo.protocol,
          manufacturer: dahuaInfo.manufacturer,
          hostname: dahuaInfo.hostname || null,
          sdkPort: dahuaInfo.sdkPort || null,
          ports: portScan,
          classification
        };

        if (classification.type === 'nvr') {
          results.nvrs.push(enriched);
          console.log(`      ${ip} → NVR (${classification.confidence}): ${dahuaInfo.manufacturer}`);
        } else {
          results.cameras.push(enriched);
          console.log(`      ${ip} → CAM (${classification.confidence}): ${dahuaInfo.manufacturer}`);
        }
        continue;
      }

      // Neither ONVIF nor Dahua - truly unknown
      results.unknown.push({
        ip,
        ports: portScan,
        classification: { type: 'unknown', confidence: 'low', reasons: ['No ONVIF or Dahua response'] }
      });
      console.log(`      ${ip} → ??? (no ONVIF/Dahua, ports: ${Object.keys(portScan).filter(p => portScan[p]).join(',')})`);
      continue;
    }

    // Classify
    const classification = classifyDevice(device, portScan);

    const enriched = {
      ...device,
      ports: portScan,
      classification
    };

    if (classification.type === 'nvr') {
      results.nvrs.push(enriched);
    } else {
      results.cameras.push(enriched);
    }

    const indicator = classification.type === 'nvr' ? 'NVR' : 'CAM';
    console.log(`      ${ip} → ${indicator} (${classification.confidence}): ${device.model || device.manufacturer || 'unknown'}`);
  }

  // Step 4: Summary
  console.log('\n[4/4] Scan complete\n');
  console.log('='.repeat(60));
  console.log(`NVRs found:     ${results.nvrs.length}`);
  console.log(`Cameras found:  ${results.cameras.length}`);
  console.log(`Unknown:        ${results.unknown.length} (have ports but no ONVIF)`);
  console.log('='.repeat(60));

  if (results.nvrs.length > 0) {
    console.log('\nNVRs:');
    for (const nvr of results.nvrs) {
      console.log(`  ${nvr.ip} - ${nvr.manufacturer || 'Unknown'} ${nvr.model || ''}`);
    }
  }

  if (results.cameras.length > 0) {
    console.log('\nCameras:');
    for (const cam of results.cameras) {
      console.log(`  ${cam.ip} - ${cam.mac || 'no-mac'} - ${cam.model || 'unknown'}`);
    }
  }

  if (results.unknown.length > 0) {
    console.log('\nUnknown (no ONVIF):');
    for (const unk of results.unknown) {
      const ports = Object.keys(unk.ports).filter(p => unk.ports[p]).join(',');
      console.log(`  ${unk.ip} - ports: ${ports}`);
    }
  }

  return results;
}

/**
 * Probe a single IP for NVR/camera details
 */
async function probeDevice(ip, credentials = []) {
  console.log(`Probing ${ip}...\n`);

  // Port scan
  console.log('[1/3] Port scan...');
  const ports = await scanPorts(ip);
  const openPorts = Object.entries(ports).filter(([_, open]) => open).map(([p]) => p);
  console.log(`      Open ports: ${openPorts.length > 0 ? openPorts.join(', ') : 'none'}\n`);

  // ONVIF query with different credentials
  console.log('[2/3] ONVIF query...');
  const defaultCreds = [
    { user: 'admin', pass: '' },
    { user: 'admin', pass: 'admin' },
    { user: 'admin', pass: 'Dad5eeeee!' },
    ...credentials
  ];

  let onvifResult = null;
  for (const cred of defaultCreds) {
    const result = queryOnvif(ip, cred.user, cred.pass);
    if (result.status === 'ok') {
      onvifResult = result;
      console.log(`      Success with ${cred.user}:${cred.pass ? '***' : '(empty)'}`);
      break;
    }
  }

  if (!onvifResult) {
    console.log('      ONVIF failed (no working credentials or not supported)');
    onvifResult = { ip, status: 'error', error: 'no_onvif' };
  }

  // Classify
  console.log('\n[3/3] Classification...');
  const classification = classifyDevice(onvifResult, ports);
  console.log(`      Type: ${classification.type} (${classification.confidence})`);
  console.log(`      Reasons: ${classification.reasons.join(', ')}`);

  return {
    ...onvifResult,
    ports,
    classification
  };
}

/**
 * Sync discovered devices to lodge.db
 */
async function syncToDb(options = {}) {
  const dryRun = options.dryRun || false;

  console.log(`Syncing to lodge.db${dryRun ? ' (DRY RUN)' : ''}...\n`);

  // Run scan
  const results = await fullScan(options);

  if (dryRun) {
    console.log('\n[DRY RUN] Would sync:');
    console.log(`  - ${results.nvrs.length} NVRs to nvrs table`);
    console.log(`  - ${results.cameras.length} cameras to cameras table`);
    return;
  }

  const db = getDb();

  // Sync NVRs
  const upsertNvr = db.prepare(`
    INSERT INTO nvrs (id, brand, model, ip, mac, serial, onvif_supported, ownership)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'DISCOVERED')
    ON CONFLICT(id) DO UPDATE SET
      model = excluded.model,
      ip = excluded.ip,
      mac = excluded.mac,
      serial = excluded.serial,
      onvif_supported = excluded.onvif_supported
  `);

  let nvrCount = 0;
  for (const nvr of results.nvrs) {
    const id = `nvr_${nvr.ip.replace(/\./g, '_')}`;
    try {
      upsertNvr.run(
        id,
        nvr.manufacturer || 'Unknown',
        nvr.model || null,
        nvr.ip,
        nvr.mac || null,
        nvr.serial || null,
        nvr.status === 'ok' ? 1 : 0
      );
      nvrCount++;
    } catch (err) {
      console.error(`  Failed to sync NVR ${nvr.ip}: ${err.message}`);
    }
  }

  // Sync cameras
  const upsertCamera = db.prepare(`
    INSERT INTO cameras (mac, model, manufacturer, serial, ip, firmware)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(mac) DO UPDATE SET
      model = excluded.model,
      manufacturer = excluded.manufacturer,
      serial = excluded.serial,
      ip = excluded.ip,
      firmware = excluded.firmware
  `);

  let camCount = 0;
  for (const cam of results.cameras) {
    if (!cam.mac) continue; // Skip cameras without MAC
    try {
      upsertCamera.run(
        cam.mac,
        cam.model || null,
        cam.manufacturer || null,
        cam.serial || null,
        cam.ip,
        cam.firmware || null
      );
      camCount++;
    } catch (err) {
      console.error(`  Failed to sync camera ${cam.ip}: ${err.message}`);
    }
  }

  console.log(`\nSync complete: ${nvrCount} NVRs, ${camCount} cameras`);
}

// CLI helper to parse --key value args
function parseArgs(args) {
  const result = { _: [] };
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith('--')) {
      const key = args[i].slice(2);
      const next = args[i + 1];
      if (next && !next.startsWith('--')) {
        result[key] = next;
        i++;
      } else {
        result[key] = true;
      }
    } else {
      result._.push(args[i]);
    }
  }
  return result;
}

// CLI
const args = process.argv.slice(2);
const parsed = parseArgs(args);
const command = parsed._[0];

switch (command) {
  case 'scan':
    const scanOpts = {
      subnet: parsed.subnet || '192.168.0',
      timeout: parseInt(parsed.timeout) || 15
    };
    fullScan(scanOpts).then(results => {
      if (parsed.json) {
        console.log(JSON.stringify(results, null, 2));
      }
    });
    break;

  case 'probe':
    const ip = parsed._[1];
    if (!ip) {
      console.error('Usage: node nvr-scanner.js probe <ip>');
      process.exit(1);
    }
    probeDevice(ip).then(result => {
      if (parsed.json) {
        console.log(JSON.stringify(result, null, 2));
      }
    });
    break;

  case 'sync':
    syncToDb({ dryRun: parsed['dry-run'], subnet: parsed.subnet || '192.168.0' });
    break;

  default:
    console.log(`NVR Scanner Tool

Usage:
  node nvr-scanner.js scan [--subnet 192.168.0] [--json]
      Subnet scan + ONVIF probe + classification
      Finds ALL devices with ports 80/554 open, then probes each

  node nvr-scanner.js probe <ip> [--json]
      Probe single device for details

  node nvr-scanner.js sync [--subnet 192.168.0] [--dry-run]
      Scan and sync discovered devices to lodge.db

Author: novicat gen-3, gen-4 (subnet scan)`);
}
