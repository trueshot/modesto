/**
 * Discovery Tools API
 * Endpoints to run NVR discovery CLI tools and return results
 *
 * Author: modestomulti gen-2
 */

const express = require('express');
const { spawn } = require('child_process');
const path = require('path');

const router = express.Router();

const TOOLS_PATH = path.join(__dirname, '..', '..', '..', 'tools');

/**
 * Execute a CLI command and stream output
 */
function runCommand(cmd, args, res) {
  res.setHeader('Content-Type', 'text/plain; charset=utf-8');
  res.setHeader('Transfer-Encoding', 'chunked');

  const proc = spawn(cmd, args, {
    cwd: TOOLS_PATH,
    shell: true
  });

  proc.stdout.on('data', (data) => {
    res.write(data);
  });

  proc.stderr.on('data', (data) => {
    res.write(data);
  });

  proc.on('close', (code) => {
    res.write(`\n[Exit code: ${code}]`);
    res.end();
  });

  proc.on('error', (err) => {
    res.write(`\nError: ${err.message}`);
    res.end();
  });
}

/**
 * POST /api/discovery/scan
 * Run NVR network scan
 */
router.post('/scan', (req, res) => {
  const { subnet } = req.body;
  const args = ['nvr-scanner.js', 'scan'];
  if (subnet) {
    args.push('--subnet', subnet);
  }
  runCommand('node', args, res);
});

/**
 * POST /api/discovery/probe
 * Probe a specific IP address
 */
router.post('/probe', (req, res) => {
  const { ip } = req.body;
  if (!ip) {
    return res.status(400).json({ error: 'IP address is required' });
  }
  runCommand('node', ['nvr-scanner.js', 'probe', ip], res);
});

/**
 * POST /api/discovery/sync
 * Sync discovered devices to lodge.db
 */
router.post('/sync', (req, res) => {
  const { dryRun } = req.body;
  const args = ['nvr-scanner.js', 'sync'];
  if (dryRun) {
    args.push('--dry-run');
  }
  runCommand('node', args, res);
});

/**
 * GET /api/discovery/channels
 * List all channels from database
 */
router.get('/channels', (req, res) => {
  const { nvr } = req.query;
  const args = ['nvr-cli.js', 'channel', 'list', '--json'];
  if (nvr) {
    args.push('--nvr', nvr);
  }
  runCommand('node', args, res);
});

module.exports = router;
