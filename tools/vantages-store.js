/**
 * Vantages companion store — warehouses/<facility>/vantages.json
 *
 * A vantage is a virtual viewpoint (a drone view), NOT physical plant.
 * George ruled 2026-08-31: it does NOT live in the normative .modelT.json —
 * this companion file is its PROVISIONAL home (ultimate home = the coming
 * operating layer). Writes are UNGATED and self-service: the viewer and the
 * CLI write here directly — no merge gate, no SVG regen, nobody in the loop.
 * Spec 5.15 guarantees only the FORMAT so the data survives migration:
 *   { vantages: [ { id, name?, x, y, elevation, direction, tilt, fov?, source? } ] }
 *
 * Validation enforcement lives HERE (the single writer): bird-name ids from
 * the shared pool, whole-foot pose above the DATUM, direction [0,360) 0=N cw,
 * tilt [0,90] down. Out-of-range pose is REJECTED, never clamped — a silently
 * altered aim is a false fact.
 *
 * Author: modeltbabylon gen-16
 */

const fs = require('fs');
const path = require('path');
const NameManager = require('./name-manager');

function fileFor(facilityDir) {
  return path.join(facilityDir, 'vantages.json');
}

function load(facilityDir) {
  const file = fileFor(facilityDir);
  if (!fs.existsSync(file)) return { vantages: [] };
  const data = JSON.parse(fs.readFileSync(file, 'utf-8'));
  if (!Array.isArray(data.vantages)) data.vantages = [];
  return data;
}

function save(facilityDir, data) {
  // Atomic write (modestomulti's review, 2026-08-31): multiple writers hit
  // this file (viewer POST + CLI), and a crash mid-writeFileSync would leave
  // truncated JSON that breaks every later load(). tmp + rename is atomic on
  // the same volume and closes that window.
  const file = fileFor(facilityDir);
  const tmp = file + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2) + '\n');
  fs.renameSync(tmp, file);
}

/**
 * Validate + append one vantage. Returns the stored vantage.
 * Throws Error with a human message on any invalid field.
 */
function add(facilityDir, spec) {
  const data = load(facilityDir);
  const existing = data.vantages;

  let id = spec.id;
  if (!id) {
    const nm = new NameManager();
    if (typeof nm.getNextVantageName === 'function') {
      id = nm.getNextVantageName(existing);
    } else {
      throw new Error('bird-name pool not available — pass an explicit id (hawk, eagle, owl, ...)');
    }
  }
  if (existing.some(v => v.id === id)) {
    throw new Error(`Vantage "${id}" already exists`);
  }

  for (const k of ['x', 'y', 'elevation', 'direction', 'tilt']) {
    if (spec[k] === undefined || spec[k] === null || isNaN(parseFloat(spec[k]))) {
      throw new Error(`${k} is required (number)`);
    }
  }
  const rawDirection = parseFloat(spec.direction);
  if (rawDirection < 0 || rawDirection >= 360) throw new Error(`direction ${rawDirection} out of [0, 360)`);
  const direction = Math.round(rawDirection) % 360;   // 359.6 rounds to 360 -> wraps to 0
  const tilt = Math.round(parseFloat(spec.tilt));
  if (tilt < 0 || tilt > 90) throw new Error(`tilt ${tilt} out of [0, 90] (degrees DOWN; aim level or down, never up)`);

  const vantage = { id };
  if (spec.name) vantage.name = String(spec.name).slice(0, 120);
  vantage.x = Math.round(parseFloat(spec.x));
  vantage.y = Math.round(parseFloat(spec.y));
  vantage.elevation = Math.round(parseFloat(spec.elevation));
  vantage.direction = direction;
  vantage.tilt = tilt;
  if (spec.fov !== undefined && spec.fov !== null && !isNaN(parseFloat(spec.fov))) {
    vantage.fov = Math.round(parseFloat(spec.fov));
  }
  if (spec.source) vantage.source = String(spec.source).slice(0, 120);

  existing.push(vantage);
  save(facilityDir, data);
  return vantage;
}

/** Remove a vantage by id. Returns the removed vantage or throws. */
function remove(facilityDir, id) {
  const data = load(facilityDir);
  const i = data.vantages.findIndex(v => v.id === id);
  if (i === -1) throw new Error(`Vantage "${id}" not found`);
  const [gone] = data.vantages.splice(i, 1);
  save(facilityDir, data);
  return gone;
}

module.exports = { load, add, remove, fileFor };
