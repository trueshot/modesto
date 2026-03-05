// svc.js — Unified Service Manager TUI
// Author: modestomulti gen-3
// Created: 2026-02-28

const fs = require('fs');
const path = require('path');
const { spawn, execSync } = require('child_process');
const readline = require('readline');
const yaml = require('js-yaml');

if (process.argv.includes('--help')) {
  console.log(`svc.js — Unified Service Manager TUI

Usage: node svc.js [--help]

Reads services.yaml and shows a dashboard of managed services.
Adopts pre-existing processes by port. Spawned services survive TUI exit.

Controls:
  1-N          Select service
  Up/Down      Navigate
  K            Kick (kill + auto-restart)
  S            Stop (kill + keep down)
  R            Restore (re-enable + start)
  Q            Quit (services keep running)

Config: services.yaml (same directory as svc.js)`);
  process.exit(0);
}

const configPath = path.join(__dirname, 'services.yaml');
const config = yaml.load(fs.readFileSync(configPath, 'utf8'));

const services = [];
let selected = 0;

// Build service list from YAML
for (const [key, svc] of Object.entries(config.services)) {
  const cwd = svc.cwd
    ? path.resolve(__dirname, svc.cwd)
    : __dirname;
  services.push({
    key,
    name: svc.name,
    command: svc.command,
    cwd,
    port: svc.port || null,
    autostart: svc.autostart || false,
    status: 'STOPPED',   // RUNNING, STOPPED, DISABLED, STARTING
    proc: null,
    pid: null,
    restartTimer: null,
  });
}

// Check if a port is already in use, return the PID if so
function findPidOnPort(port) {
  if (!port) return null;
  try {
    const out = execSync(`netstat -ano | findstr :${port} | findstr LISTENING`, {
      encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore']
    });
    const lines = out.trim().split('\n');
    for (const line of lines) {
      const parts = line.trim().split(/\s+/);
      const addr = parts[1] || '';
      // Match exact port (not e.g. :51730)
      if (addr.endsWith(':' + port)) {
        return parseInt(parts[parts.length - 1]);
      }
    }
  } catch (e) {}
  return null;
}

function adoptExisting(svc) {
  const pid = findPidOnPort(svc.port);
  if (pid) {
    svc.pid = pid;
    svc.proc = null; // Not our child, but we track the PID
    svc.status = 'ADOPTED';
    return true;
  }
  return false;
}

function killProc(svc) {
  const pid = svc.proc ? svc.proc.pid : svc.pid;
  if (pid) {
    try {
      execSync(`taskkill /PID ${pid} /T /F`, { stdio: 'ignore' });
    } catch (e) {
      // Process may already be dead
    }
    svc.proc = null;
    svc.pid = null;
  }
}

function startService(svc) {
  if (svc.proc) return;
  if (svc.status === 'ADOPTED') return;

  // Check if something is already on this port
  if (adoptExisting(svc)) {
    draw();
    return;
  }
  if (svc.restartTimer) {
    clearTimeout(svc.restartTimer);
    svc.restartTimer = null;
  }

  svc.status = 'STARTING';
  draw();

  try {
    const child = spawn(svc.command, {
      cwd: svc.cwd,
      stdio: 'ignore',
      shell: true,
      detached: true,
    });

    svc.proc = child;
    svc.pid = child.pid;
    svc.status = 'RUNNING';
    child.unref();
    draw();
  } catch (e) {
    svc.status = 'STOPPED';
    draw();
  }
}

function kickService(svc) {
  if (svc.status === 'DISABLED') return;
  killProc(svc);
  if (svc.status === 'ADOPTED') {
    // Was pre-existing — kill it and spawn our own
    svc.status = 'STOPPED';
    setTimeout(() => startService(svc), 1000);
    draw();
  }
  // For managed processes, the exit handler will auto-restart
}

function stopService(svc) {
  if (svc.restartTimer) {
    clearTimeout(svc.restartTimer);
    svc.restartTimer = null;
  }
  killProc(svc);
  svc.status = 'DISABLED';
  draw();
}

function restoreService(svc) {
  if (svc.status !== 'DISABLED' && svc.status !== 'STOPPED') return;
  if (svc.restartTimer) {
    clearTimeout(svc.restartTimer);
    svc.restartTimer = null;
  }
  svc.status = 'STOPPED';
  startService(svc);
}

function pad(str, len) {
  str = String(str);
  while (str.length < len) str += ' ';
  return str;
}

function draw() {
  try {
    execSync('cls', { stdio: 'inherit', shell: true });
  } catch (e) {
    // Fallback: print newlines
    process.stdout.write('\n'.repeat(40));
  }

  console.log('');
  console.log('  === Modesto Services ===');
  console.log('');
  console.log('  ' + pad('#', 4) + pad('Service', 22) + pad('Port', 8) + pad('PID', 10) + 'Status');
  console.log('  ' + '-'.repeat(58));

  services.forEach((svc, i) => {
    const marker = i === selected ? '> ' : '  ';
    const num = String(i + 1);
    const port = svc.port ? String(svc.port) : '--';
    const pid = svc.pid ? String(svc.pid) : '--';
    const status = svc.status === 'ADOPTED' ? 'RUNNING*' : svc.status;
    console.log(marker + pad(num, 4) + pad(svc.name, 22) + pad(port, 8) + pad(pid, 10) + status);
  });

  console.log('');
  const hasAdopted = services.some(s => s.status === 'ADOPTED');
  if (hasAdopted) {
    console.log('  * = pre-existing process (not spawned by svc)');
    console.log('');
  }
  console.log('  [1-' + services.length + '] Select   [K]ick   [S]top   [R]estore   [Q]uit');
  console.log('');
}

// Set up raw keyboard input
readline.emitKeypressEvents(process.stdin);
if (process.stdin.isTTY) {
  process.stdin.setRawMode(true);
}

process.stdin.on('keypress', (str, key) => {
  if (!key) return;

  // Ctrl+C or Q to quit
  if ((key.ctrl && key.name === 'c') || key.name === 'q') {
    // Detach all running processes so they survive
    services.forEach(svc => {
      if (svc.restartTimer) clearTimeout(svc.restartTimer);
      if (svc.proc) {
        svc.proc.unref();
      }
    });
    console.log('  Services left running. Goodbye.');
    process.exit(0);
  }

  // Number keys to select
  const num = parseInt(str);
  if (num >= 1 && num <= services.length) {
    selected = num - 1;
    draw();
    return;
  }

  // Arrow keys to select
  if (key.name === 'up') {
    selected = Math.max(0, selected - 1);
    draw();
    return;
  }
  if (key.name === 'down') {
    selected = Math.min(services.length - 1, selected + 1);
    draw();
    return;
  }

  const svc = services[selected];

  if (key.name === 'k') {
    kickService(svc);
    return;
  }

  if (key.name === 's') {
    stopService(svc);
    return;
  }

  if (key.name === 'r') {
    restoreService(svc);
    return;
  }
});

// Check if a PID is still alive
function isAlive(pid) {
  try {
    execSync(`tasklist /FI "PID eq ${pid}" /NH`, {
      encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore']
    });
    // tasklist returns the process line if alive, "INFO: No tasks" if dead
    const out = execSync(`tasklist /FI "PID eq ${pid}" /NH`, {
      encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore']
    });
    return !out.includes('No tasks');
  } catch (e) {
    return false;
  }
}

// Poll adopted/running processes for liveness
setInterval(() => {
  let changed = false;
  services.forEach(svc => {
    if (svc.status === 'ADOPTED' && svc.pid) {
      if (!isAlive(svc.pid)) {
        svc.pid = null;
        svc.status = 'STOPPED';
        changed = true;
      }
    }
    // Also check managed processes that might have died without exit event
    if (svc.status === 'RUNNING' && svc.pid && !svc.proc) {
      if (!isAlive(svc.pid)) {
        svc.pid = null;
        svc.status = 'STOPPED';
        changed = true;
      }
    }
  });
  if (changed) draw();
}, 3000);

// On startup: adopt any already-running services, then start autostart ones
services.forEach(svc => {
  adoptExisting(svc);
});

draw();

services.forEach(svc => {
  if (svc.autostart && svc.status !== 'ADOPTED') {
    startService(svc);
  }
});
