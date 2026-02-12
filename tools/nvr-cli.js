#!/usr/bin/env node
/**
 * NVR CLI Tool
 * Manage NVRs in lodge.db
 *
 * Usage:
 *   node nvr-cli.js list [--ownership OURS|NOT_OURS]
 *   node nvr-cli.js add <id> --brand <brand> --ip <ip> [options]
 *   node nvr-cli.js update <id> [options]
 *   node nvr-cli.js remove <id>
 *   node nvr-cli.js show <id>
 *
 * Author: novicat gen-2
 */

const path = require('path');
const Database = require('better-sqlite3');

const DB_PATH = path.join(__dirname, '..', 'warehouses', 'lodge', 'lodge.db');

function getDb() {
  return new Database(DB_PATH);
}

function list(args) {
  const db = getDb();
  let sql = 'SELECT id, brand, model, ip, mac, ownership, max_channels, onvif_supported FROM nvrs';
  const params = [];

  if (args.ownership) {
    sql += ' WHERE ownership = ?';
    params.push(args.ownership.toUpperCase());
  }

  sql += ' ORDER BY id';

  const rows = db.prepare(sql).all(...params);

  if (args.json) {
    console.log(JSON.stringify(rows, null, 2));
  } else {
    if (rows.length === 0) {
      console.log('No NVRs found.');
      return;
    }
    console.log('');
    console.log('ID          Brand            Model             IP               Ownership  Channels  ONVIF');
    console.log('─'.repeat(100));
    for (const row of rows) {
      console.log(
        `${(row.id || '').padEnd(11)} ` +
        `${(row.brand || '').padEnd(16)} ` +
        `${(row.model || '-').padEnd(17)} ` +
        `${(row.ip || '').padEnd(16)} ` +
        `${(row.ownership || '').padEnd(10)} ` +
        `${String(row.max_channels || 0).padEnd(9)} ` +
        `${row.onvif_supported ? 'Yes' : 'No'}`
      );
    }
    console.log('');
    console.log(`Total: ${rows.length} NVR(s)`);
  }

  db.close();
}

function add(args) {
  if (!args.id || !args.brand || !args.ip) {
    console.error('Error: --id, --brand, and --ip are required');
    console.error('Usage: node nvr-cli.js add <id> --brand <brand> --ip <ip> [options]');
    process.exit(1);
  }

  const db = getDb();

  const existing = db.prepare('SELECT id FROM nvrs WHERE id = ?').get(args.id);
  if (existing) {
    console.error(`Error: NVR with id '${args.id}' already exists. Use 'update' instead.`);
    db.close();
    process.exit(1);
  }

  const stmt = db.prepare(`
    INSERT INTO nvrs (id, brand, model, ip, mac, serial, max_channels, ownership, onvif_supported, protocol, path_format, username, password, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  stmt.run(
    args.id,
    args.brand,
    args.model || null,
    args.ip,
    args.mac || null,
    args.serial || null,
    parseInt(args.maxChannels) || 16,
    (args.ownership || 'OURS').toUpperCase(),
    args.onvif ? 1 : 0,
    args.protocol || 'rtsp',
    args.pathFormat || null,
    args.username || 'admin',
    args.password || '',
    args.notes || null
  );

  console.log(`Added NVR '${args.id}' (${args.brand} @ ${args.ip})`);
  db.close();
}

function update(args) {
  if (!args.id) {
    console.error('Error: id is required');
    process.exit(1);
  }

  const db = getDb();

  const existing = db.prepare('SELECT * FROM nvrs WHERE id = ?').get(args.id);
  if (!existing) {
    console.error(`Error: NVR with id '${args.id}' not found.`);
    db.close();
    process.exit(1);
  }

  const updates = [];
  const params = [];

  const fields = {
    brand: 'brand',
    model: 'model',
    ip: 'ip',
    mac: 'mac',
    serial: 'serial',
    maxChannels: 'max_channels',
    ownership: 'ownership',
    onvif: 'onvif_supported',
    protocol: 'protocol',
    pathFormat: 'path_format',
    username: 'username',
    password: 'password',
    notes: 'notes'
  };

  for (const [arg, col] of Object.entries(fields)) {
    if (args[arg] !== undefined) {
      updates.push(`${col} = ?`);
      if (arg === 'onvif') {
        params.push(args[arg] ? 1 : 0);
      } else if (arg === 'maxChannels') {
        params.push(parseInt(args[arg]));
      } else if (arg === 'ownership') {
        params.push(args[arg].toUpperCase());
      } else {
        params.push(args[arg]);
      }
    }
  }

  if (updates.length === 0) {
    console.error('Error: No fields to update. Provide at least one field option.');
    db.close();
    process.exit(1);
  }

  params.push(args.id);
  const sql = `UPDATE nvrs SET ${updates.join(', ')} WHERE id = ?`;
  db.prepare(sql).run(...params);

  console.log(`Updated NVR '${args.id}'`);
  db.close();
}

function remove(args) {
  if (!args.id) {
    console.error('Error: id is required');
    process.exit(1);
  }

  const db = getDb();

  const existing = db.prepare('SELECT id, brand, ip FROM nvrs WHERE id = ?').get(args.id);
  if (!existing) {
    console.error(`Error: NVR with id '${args.id}' not found.`);
    db.close();
    process.exit(1);
  }

  db.prepare('DELETE FROM nvrs WHERE id = ?').run(args.id);
  console.log(`Removed NVR '${args.id}' (${existing.brand} @ ${existing.ip})`);
  db.close();
}

function show(args) {
  if (!args.id) {
    console.error('Error: id is required');
    process.exit(1);
  }

  const db = getDb();
  const row = db.prepare('SELECT * FROM nvrs WHERE id = ?').get(args.id);

  if (!row) {
    console.error(`Error: NVR with id '${args.id}' not found.`);
    db.close();
    process.exit(1);
  }

  if (args.json) {
    console.log(JSON.stringify(row, null, 2));
  } else {
    console.log('');
    console.log(`NVR: ${row.id}`);
    console.log('─'.repeat(40));
    console.log(`  Brand:          ${row.brand || '-'}`);
    console.log(`  Model:          ${row.model || '-'}`);
    console.log(`  IP:             ${row.ip}`);
    console.log(`  MAC:            ${row.mac || '-'}`);
    console.log(`  Serial:         ${row.serial || '-'}`);
    console.log(`  Max Channels:   ${row.max_channels}`);
    console.log(`  Ownership:      ${row.ownership}`);
    console.log(`  ONVIF:          ${row.onvif_supported ? 'Yes' : 'No'}`);
    console.log(`  Protocol:       ${row.protocol || '-'}`);
    console.log(`  Path Format:    ${row.path_format || '-'}`);
    console.log(`  Username:       ${row.username || '-'}`);
    console.log(`  Password:       ${row.password ? '***' : '(empty)'}`);
    console.log(`  Notes:          ${row.notes || '-'}`);
    console.log(`  Created:        ${row.created_at || '-'}`);
    console.log('');
  }

  db.close();
}

// ═══════════════════════════════════════════════════════════════════════════
// CHANNEL COMMANDS
// ═══════════════════════════════════════════════════════════════════════════

function channelList(args) {
  const db = getDb();
  let sql = `SELECT c.id, c.nvr_id, c.channel_number, c.camera_id, c.status, c.recording, c.resolution
             FROM channels c`;
  const params = [];

  if (args.nvr) {
    sql += ' WHERE c.nvr_id = ?';
    params.push(args.nvr);
  }

  sql += ' ORDER BY c.nvr_id, c.channel_number';

  const rows = db.prepare(sql).all(...params);

  if (args.json) {
    console.log(JSON.stringify(rows, null, 2));
  } else {
    if (rows.length === 0) {
      console.log('No channels found.');
      return;
    }
    console.log('');
    console.log('ID              NVR      Ch   Camera        Status   Recording  Resolution');
    console.log('─'.repeat(85));
    for (const row of rows) {
      console.log(
        `${(row.id || '').padEnd(15)} ` +
        `${(row.nvr_id || '').padEnd(8)} ` +
        `${String(row.channel_number).padEnd(4)} ` +
        `${(row.camera_id || '-').padEnd(13)} ` +
        `${(row.status || '-').padEnd(8)} ` +
        `${(row.recording ? 'Yes' : 'No').padEnd(10)} ` +
        `${row.resolution || '-'}`
      );
    }
    console.log('');
    console.log(`Total: ${rows.length} channel(s)`);
  }

  db.close();
}

function channelShow(args) {
  if (!args.id) {
    console.error('Error: channel id is required');
    process.exit(1);
  }

  const db = getDb();
  const row = db.prepare('SELECT * FROM channels WHERE id = ?').get(args.id);

  if (!row) {
    console.error(`Error: Channel '${args.id}' not found.`);
    db.close();
    process.exit(1);
  }

  if (args.json) {
    console.log(JSON.stringify(row, null, 2));
  } else {
    console.log('');
    console.log(`Channel: ${row.id}`);
    console.log('─'.repeat(40));
    console.log(`  NVR ID:         ${row.nvr_id}`);
    console.log(`  Channel #:      ${row.channel_number}`);
    console.log(`  Camera ID:      ${row.camera_id || '-'}`);
    console.log(`  Status:         ${row.status || '-'}`);
    console.log(`  Recording:      ${row.recording ? 'Yes' : 'No'}`);
    console.log(`  Resolution:     ${row.resolution || '-'}`);
    console.log(`  RTSP Path:      ${row.rtsp_path || '(auto)'}`);
    console.log(`  Last Probed:    ${row.last_probed || '-'}`);
    console.log('');
  }

  db.close();
}

function channelAdd(args) {
  if (!args.nvr || !args.channel) {
    console.error('Error: --nvr and --channel are required');
    console.error('Usage: node nvr-cli.js channel add --nvr <nvr_id> --channel <num> [options]');
    process.exit(1);
  }

  const db = getDb();

  // Verify NVR exists
  const nvr = db.prepare('SELECT id FROM nvrs WHERE id = ?').get(args.nvr);
  if (!nvr) {
    console.error(`Error: NVR '${args.nvr}' not found.`);
    db.close();
    process.exit(1);
  }

  const chNum = parseInt(args.channel);
  const id = args.id || `${args.nvr}_ch${String(chNum).padStart(2, '0')}`;

  const existing = db.prepare('SELECT id FROM channels WHERE id = ?').get(id);
  if (existing) {
    console.error(`Error: Channel '${id}' already exists. Use 'channel update' instead.`);
    db.close();
    process.exit(1);
  }

  const stmt = db.prepare(`
    INSERT INTO channels (id, nvr_id, channel_number, camera_id, status, recording, resolution, rtsp_path)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `);

  stmt.run(
    id,
    args.nvr,
    chNum,
    args.cameraId || null,
    args.status || 'unknown',
    args.recording ? 1 : 0,
    args.resolution || null,
    args.rtspPath || null
  );

  console.log(`Added channel '${id}' (${args.nvr} ch${chNum})`);
  db.close();
}

function channelUpdate(args) {
  if (!args.id) {
    console.error('Error: channel id is required');
    process.exit(1);
  }

  const db = getDb();

  const existing = db.prepare('SELECT * FROM channels WHERE id = ?').get(args.id);
  if (!existing) {
    console.error(`Error: Channel '${args.id}' not found.`);
    db.close();
    process.exit(1);
  }

  const updates = [];
  const params = [];

  const fields = {
    cameraId: 'camera_id',
    status: 'status',
    recording: 'recording',
    resolution: 'resolution',
    rtspPath: 'rtsp_path'
  };

  for (const [arg, col] of Object.entries(fields)) {
    if (args[arg] !== undefined) {
      updates.push(`${col} = ?`);
      if (arg === 'recording') {
        params.push(args[arg] ? 1 : 0);
      } else {
        params.push(args[arg]);
      }
    }
  }

  if (updates.length === 0) {
    console.error('Error: No fields to update.');
    db.close();
    process.exit(1);
  }

  params.push(args.id);
  const sql = `UPDATE channels SET ${updates.join(', ')} WHERE id = ?`;
  db.prepare(sql).run(...params);

  console.log(`Updated channel '${args.id}'`);
  db.close();
}

function channelRemove(args) {
  if (!args.id) {
    console.error('Error: channel id is required');
    process.exit(1);
  }

  const db = getDb();

  const existing = db.prepare('SELECT id, nvr_id, channel_number FROM channels WHERE id = ?').get(args.id);
  if (!existing) {
    console.error(`Error: Channel '${args.id}' not found.`);
    db.close();
    process.exit(1);
  }

  db.prepare('DELETE FROM channels WHERE id = ?').run(args.id);
  console.log(`Removed channel '${args.id}' (${existing.nvr_id} ch${existing.channel_number})`);
  db.close();
}

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg.startsWith('--')) {
      const key = arg.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      const next = argv[i + 1];
      if (next && !next.startsWith('--')) {
        args[key] = next;
        i++;
      } else {
        args[key] = true;
      }
    } else {
      args._.push(arg);
    }
  }
  return args;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = args._[0];
  const subcommand = args._[1];
  args.id = args._[1] || args.id;

  // Handle "channel" subcommands
  if (command === 'channel') {
    args.id = args._[2] || args.id;
    switch (subcommand) {
      case 'list':
        channelList(args);
        break;
      case 'show':
        channelShow(args);
        break;
      case 'add':
        channelAdd(args);
        break;
      case 'update':
        channelUpdate(args);
        break;
      case 'remove':
      case 'delete':
        channelRemove(args);
        break;
      default:
        console.log('Channel Commands:');
        console.log('  node nvr-cli.js channel list [--nvr <nvr_id>] [--json]');
        console.log('  node nvr-cli.js channel show <id> [--json]');
        console.log('  node nvr-cli.js channel add --nvr <nvr_id> --channel <num> [options]');
        console.log('  node nvr-cli.js channel update <id> [options]');
        console.log('  node nvr-cli.js channel remove <id>');
        break;
    }
    return;
  }

  switch (command) {
    case 'list':
      list(args);
      break;
    case 'add':
      add(args);
      break;
    case 'update':
      update(args);
      break;
    case 'remove':
    case 'delete':
      remove(args);
      break;
    case 'show':
    case 'get':
      show(args);
      break;
    default:
      console.log('NVR CLI Tool - Manage NVRs and Channels in lodge.db');
      console.log('');
      console.log('NVR Commands:');
      console.log('  node nvr-cli.js list [--ownership OURS|NOT_OURS] [--json]');
      console.log('  node nvr-cli.js show <id> [--json]');
      console.log('  node nvr-cli.js add <id> --brand <brand> --ip <ip> [options]');
      console.log('  node nvr-cli.js update <id> [options]');
      console.log('  node nvr-cli.js remove <id>');
      console.log('');
      console.log('Channel Commands:');
      console.log('  node nvr-cli.js channel list [--nvr <nvr_id>] [--json]');
      console.log('  node nvr-cli.js channel show <id> [--json]');
      console.log('  node nvr-cli.js channel add --nvr <nvr_id> --channel <num> [options]');
      console.log('  node nvr-cli.js channel update <id> [options]');
      console.log('  node nvr-cli.js channel remove <id>');
      console.log('');
      console.log('NVR Options:');
      console.log('  --brand, --model, --ip, --mac, --serial, --max-channels');
      console.log('  --ownership, --onvif, --protocol, --path-format');
      console.log('  --username, --password, --notes');
      console.log('');
      console.log('Channel Options:');
      console.log('  --camera-id, --status, --recording, --resolution, --rtsp-path');
      console.log('');
      console.log('Examples:');
      console.log('  node nvr-cli.js list --ownership OURS');
      console.log('  node nvr-cli.js channel list --nvr nvr2');
      console.log('  node nvr-cli.js channel show nvr2_ch02');
      console.log('  node nvr-cli.js channel update nvr2_ch02 --recording --status active');
      break;
  }
}

main();
