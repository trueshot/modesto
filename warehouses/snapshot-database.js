#!/usr/bin/env node
/**
 * Snapshot a warehouse .db to a diffable .sql text dump.
 *
 * Run as-needed before committing to produce a git-friendly snapshot.
 *
 * Usage: node snapshot-database.js <warehouse>
 * Example: node snapshot-database.js lodge
 * Output: warehouses/lodge/lodge-snapshot.sql
 *
 * Author: modestocat gen-14
 */

const Database = require('better-sqlite3');
const fs = require('fs');
const path = require('path');

const warehouse = process.argv[2];
if (!warehouse) {
  console.error('Usage: node snapshot-database.js <warehouse>');
  process.exit(1);
}

const warehouseDir = path.join(__dirname, warehouse);
const dbPath = path.join(warehouseDir, `${warehouse}.db`);
const outPath = path.join(warehouseDir, `${warehouse}-snapshot.sql`);

if (!fs.existsSync(dbPath)) {
  console.error(`Database not found: ${dbPath}`);
  process.exit(1);
}

const db = new Database(dbPath, { readonly: true });

const lines = [];
lines.push(`-- ${warehouse}.db snapshot`);
lines.push(`-- Generated: ${new Date().toISOString()}`);
lines.push('');
lines.push('BEGIN TRANSACTION;');
lines.push('');

// Get all schema objects in creation order
const objects = db.prepare(`
  SELECT type, name, sql FROM sqlite_master
  WHERE sql IS NOT NULL
  ORDER BY CASE type
    WHEN 'table' THEN 1
    WHEN 'index' THEN 2
    WHEN 'view' THEN 3
    WHEN 'trigger' THEN 4
  END, name
`).all();

for (const obj of objects) {
  lines.push(`${obj.sql};`);
  lines.push('');

  // Dump rows for tables (not views/indexes)
  if (obj.type === 'table') {
    const rows = db.prepare(`SELECT * FROM "${obj.name}"`).all();
    for (const row of rows) {
      const cols = Object.keys(row);
      const vals = cols.map(c => {
        const v = row[c];
        if (v === null) return 'NULL';
        if (typeof v === 'number') return v;
        return `'${String(v).replace(/'/g, "''")}'`;
      });
      lines.push(`INSERT INTO "${obj.name}" VALUES(${vals.join(',')});`);
    }
    if (rows.length > 0) lines.push('');
  }
}

lines.push('COMMIT;');
lines.push('');

fs.writeFileSync(outPath, lines.join('\n'));
db.close();

console.log(`Snapshot written to ${outPath} (${lines.length} lines)`);
