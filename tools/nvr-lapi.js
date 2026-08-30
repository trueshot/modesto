#!/usr/bin/env node
/**
 * nvr-lapi.js - Authenticated GET against a UNIVIEW NVR's LAPI.
 *
 * Plain `curl --digest` gets "Not Authorized" (ResponseCode 3) on most LAPI
 * endpoints: the NVR wants the 2-step Login PUT first, then requests signed
 * with the session nonce. This does that and prints the JSON response.
 *
 * Usage:
 *   node nvr-lapi.js <nvr_id> <lapi_path> [--raw]
 *   node nvr-lapi.js nvr2 /LAPI/V1.0/Channels/Media/Record/Earliest
 *   node nvr-lapi.js nvr2 "/LAPI/V1.0/Channels/10/Media/Video/Streams/0/Records/DailyDistribution?Year=2026&Month=8"
 *
 * Known useful paths (from web UI HAR, 2026-02):
 *   Channels/Media/Record/Earliest | Latest | EstimatedDays     retention
 *   Channels/System/RecordStatus                                per-channel recording state
 *   Storage/Containers/DetailInfos, Channels/Storage/Quota      disks / quota
 *   Channels/<ch>/Media/Video/Streams/0/Records/DailyDistribution?Year=&Month=
 *   Channels/Media/Backup/AllRecords, Media/Backup/Records      native clip export
 *   System/DeviceInfo, Channels/System/DeviceInfos, Channels/System/ChannelDetailInfos
 *
 * Author: novicat gen-18
 */

const crypto = require('crypto');
const http = require('http');
const fs = require('fs');
const path = require('path');

const envPath = path.join(__dirname, '..', '.env');
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const t = line.trim();
    if (t && !t.startsWith('#') && t.includes('=')) {
      const [k, ...rest] = t.split('=');
      if (!process.env[k.trim()]) process.env[k.trim()] = rest.join('=').trim();
    }
  }
}

const args = process.argv.slice(2);
const nvrId = args[0];
let lapiPath = args[1];
const raw = args.includes('--raw');
if (!nvrId || !lapiPath) {
  console.error('Usage: node nvr-lapi.js <nvr_id> <lapi_path> [--raw]');
  process.exit(1);
}
if (!lapiPath.startsWith('/')) lapiPath = '/LAPI/V1.0/' + lapiPath;

const db = require('better-sqlite3')(path.join(__dirname, '../warehouses/lodge/lodge.db'));
const nvr = db.prepare('SELECT * FROM nvrs WHERE id = ?').get(nvrId);
if (!nvr) { console.error(`NVR '${nvrId}' not found in lodge.db`); process.exit(1); }
if (nvr.brand !== 'UNIVIEW') { console.error(`NVR '${nvrId}' is ${nvr.brand} — LAPI is UNIVIEW-only`); process.exit(1); }

const NVR_IP = nvr.ip;
const USER = process.env[`${nvrId.toUpperCase()}_USER`] || 'admin';
const PASS = process.env[`${nvrId.toUpperCase()}_PASS`] || '';

const md5 = s => crypto.createHash('md5').update(s).digest('hex');
function digest(method, uri, nonce) {
  const nc = '00000001', cnonce = String(Math.floor(Math.random() * 2e9));
  const ha1 = md5(`${USER}:NVRDVR:${PASS}`), ha2 = md5(`${method}:${uri}`);
  const resp = md5(`${ha1}:${nonce}:${nc}:${cnonce}:auth:${ha2}`);
  return `Digest username="${USER}",realm="NVRDVR",qop="auth", nonce="${nonce}",algorithm="MD5",cnonce="${cnonce}",nc="${nc}",uri="${uri}",response="${resp}"`;
}
function req(method, urlPath, headers = {}) {
  return new Promise((resolve, reject) => {
    const r = http.request({ hostname: NVR_IP, port: 80, path: urlPath, method,
      headers: { 'Cookie': 'WebLoginHandle=10081124', 'Accept': 'application/json', ...headers }, timeout: 10000 },
      res => { let b = ''; res.on('data', c => b += c); res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: b })); });
    r.on('error', reject); r.on('timeout', () => { r.destroy(); reject(new Error('HTTP timeout')); }); r.end();
  });
}

(async () => {
  try {
    const login = '/LAPI/V1.0/System/Security/Login';
    const r1 = await req('PUT', login);
    const m = (r1.headers['www-authenticate'] || '').match(/nonce="([^"]+)"/);
    if (!m) throw new Error('No nonce from ' + NVR_IP);
    const nonce = m[1];
    const r2 = await req('PUT', login, { Authorization: digest('PUT', login, nonce) });
    const l = JSON.parse(r2.body);
    if (l.Response?.ResponseCode !== 0) throw new Error('Login failed: ' + l.Response?.ResponseString);

    const ka = '/LAPI/V1.0/System/Security/KeepAlive';
    const r3 = await req('PUT', ka, { Authorization: digest('PUT', ka, nonce) });
    const k = JSON.parse(r3.body);
    const next = String(k.Response?.Data?.NextNonce || nonce);

    const r4 = await req('GET', lapiPath, { Authorization: digest('GET', lapiPath, next) });
    if (raw) { console.log(r4.body); return; }
    let j; try { j = JSON.parse(r4.body); } catch (e) { console.log(r4.body); return; }
    if (j.Response?.ResponseCode !== 0) console.error(`LAPI ${j.Response?.ResponseCode}: ${j.Response?.ResponseString} (HTTP ${j.Response?.StatusCode})`);
    console.log(JSON.stringify(j.Response?.Data ?? j, null, 2));
  } catch (e) {
    console.error('Failed:', e.message);
    process.exit(1);
  }
})();
