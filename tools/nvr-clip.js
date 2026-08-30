#!/usr/bin/env node
/**
 * nvr-clip.js - Capture a continuous CLIP (not a single frame) from UNIVIEW NVR
 * recorded video, saved as .mp4 (H.265 stream-copied, no re-encode).
 *
 * Same protocol as nvr-frame.js (LAPI login → KeepAlive → RecordURL → WebSocket
 * RTSP), but keeps collecting RTP until the requested window ends (server
 * ANNOUNCE) or a wall-clock deadline, then depacketizes to Annex B and muxes.
 *
 * Usage:
 *   node nvr-clip.js --nvr nvr2 --channel 10 --time "2026-08-30 09:00:00" [--duration 30]
 *                    [--scale 1] [--dir C:\clips] [--debug]
 *
 * Output: ~/Videos/nvr-clips/<nvr>_ch<channel>_<timestamp>_<duration>s.mp4
 * Prints measured fps (from RTP timestamps) and transfer speed vs realtime.
 *
 * --scale: RTSP Scale header, honored by UNIVIEW. Verified 2026-08-30:
 *   1 → realtime, full fps      4 → 4x realtime, FULL fps (recommended)
 *   8 → 8x realtime, but the NVR thins frames to ~65% of recorded fps
 * --time: use a Z suffix ("2026-08-30T09:50:00Z"); a bare timestamp is parsed
 *   as this machine's local time, not the warehouse's.
 *
 * Author: novicat gen-18 (derived from nvr-frame.js, gen-8/9)
 */

const WebSocket = require('ws');
const crypto = require('crypto');
const http = require('http');
const { spawn, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// Load .env
const envPath = path.join(__dirname, '..', '.env');
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (trimmed && !trimmed.startsWith('#') && trimmed.includes('=')) {
      const [k, ...rest] = trimmed.split('=');
      if (!process.env[k.trim()]) process.env[k.trim()] = rest.join('=').trim();
    }
  }
}

function findFfmpeg() {
  try { execSync('where ffmpeg', { stdio: 'pipe' }); return 'ffmpeg'; } catch (e) {}
  const wingetLinks = path.join(process.env.LOCALAPPDATA || '', 'Microsoft/WinGet/Links/ffmpeg.exe');
  if (fs.existsSync(wingetLinks)) return wingetLinks;
  return 'ffmpeg';
}
const FFMPEG = findFfmpeg();

const args = process.argv.slice(2);
function getArg(name) {
  const idx = args.indexOf(`--${name}`);
  return idx !== -1 && args[idx + 1] ? args[idx + 1] : null;
}

const nvrId = getArg('nvr');
const channel = getArg('channel');
const timeStr = getArg('time');
const duration = parseInt(getArg('duration') || '30', 10);
const scale = getArg('scale') || '1';
const outDir = getArg('dir');
const debug = args.includes('--debug');

if (!nvrId || !channel || !timeStr) {
  console.error('Usage: node nvr-clip.js --nvr <nvr_id> --channel <num> --time <timestamp> [--duration <sec>] [--scale <n>] [--dir <path>] [--debug]');
  process.exit(1);
}

const videosDir = outDir || path.join(process.env.USERPROFILE || process.env.HOME, 'Videos', 'nvr-clips');
fs.mkdirSync(videosDir, { recursive: true });
const safeTime = timeStr.replace(/[:/ ]/g, '-');
const output = path.join(videosDir, `${nvrId}_ch${channel}_${safeTime}_${duration}s.mp4`);
const rawFile = output.replace(/\.mp4$/, '.h265');

const db = require('better-sqlite3')(path.join(__dirname, '../warehouses/lodge/lodge.db'));
const nvr = db.prepare('SELECT * FROM nvrs WHERE id = ?').get(nvrId);
if (!nvr) { console.error(`NVR '${nvrId}' not found in lodge.db`); process.exit(1); }
if (nvr.brand !== 'UNIVIEW') { console.error(`NVR '${nvrId}' is ${nvr.brand} - WebSocket playback only supported on UNIVIEW`); process.exit(1); }

const NVR_IP = nvr.ip;
const envPrefix = nvrId.toUpperCase();
const NVR_USER = process.env[`${envPrefix}_USER`] || 'admin';
const NVR_PASS = process.env[`${envPrefix}_PASS`] || '';

const timestamp = new Date(timeStr);
if (isNaN(timestamp.getTime())) { console.error(`Invalid timestamp: ${timeStr}`); process.exit(1); }
const epochBegin = Math.floor(timestamp.getTime() / 1000);
const epochEnd = epochBegin + duration;

console.log(`NVR: ${nvrId} (${NVR_IP})  Channel: ${channel}`);
console.log(`Window: ${timestamp.toISOString()} + ${duration}s (epoch ${epochBegin}-${epochEnd})  Scale: ${scale}`);
console.log(`Output: ${output}`);

// --- Digest / HTTP helpers (identical to nvr-frame.js) ---

function md5(str) { return crypto.createHash('md5').update(str).digest('hex'); }

function buildDigestHeader(username, password, realm, nonce, qop, method, uri) {
  const nc = '00000001';
  const cnonce = Math.floor(Math.random() * 2000000000).toString();
  const ha1 = md5(`${username}:${realm}:${password}`);
  const ha2 = md5(`${method}:${uri}`);
  const response = md5(`${ha1}:${nonce}:${nc}:${cnonce}:${qop}:${ha2}`);
  return `Digest username="${username}",realm="${realm}",qop="${qop}", nonce="${nonce}",algorithm="MD5",cnonce="${cnonce}",nc="${nc}",uri="${uri}",response="${response}"`;
}

function httpRequest(method, urlPath, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: NVR_IP, port: 80, path: urlPath, method,
      headers: { 'Cookie': 'WebLoginHandle=10081124', 'Accept': 'application/json', ...headers },
      timeout: 10000
    }, (res) => {
      let body = '';
      res.on('data', c => body += c);
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }));
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('HTTP timeout')); });
    req.end();
  });
}

async function lapiLogin() {
  const loginPath = '/LAPI/V1.0/System/Security/Login';
  const res1 = await httpRequest('PUT', loginPath);
  const wwwAuth = res1.headers['www-authenticate'];
  if (!wwwAuth) throw new Error('No WWW-Authenticate header in login response');
  const m = wwwAuth.match(/nonce="([^"]+)"/);
  if (!m) throw new Error('No nonce in WWW-Authenticate: ' + wwwAuth);
  const loginNonce = m[1];
  const res2 = await httpRequest('PUT', loginPath, { 'Authorization': buildDigestHeader(NVR_USER, NVR_PASS, 'NVRDVR', loginNonce, 'auth', 'PUT', loginPath) });
  let data; try { data = JSON.parse(res2.body); } catch (e) { throw new Error('Login response not JSON: ' + res2.body); }
  if (data.Response?.ResponseCode !== 0) throw new Error('Login failed: ' + data.Response?.ResponseString);
  return loginNonce;
}

async function lapiKeepAlive(nonce) {
  const kaPath = '/LAPI/V1.0/System/Security/KeepAlive';
  const res = await httpRequest('PUT', kaPath, { 'Authorization': buildDigestHeader(NVR_USER, NVR_PASS, 'NVRDVR', nonce, 'auth', 'PUT', kaPath) });
  let data; try { data = JSON.parse(res.body); } catch (e) { throw new Error('KeepAlive response not JSON: ' + res.body); }
  if (data.Response?.ResponseCode !== 0) throw new Error('KeepAlive failed: ' + data.Response?.ResponseString);
  const nextNonce = data.Response?.Data?.NextNonce;
  if (!nextNonce) throw new Error('No NextNonce in KeepAlive response');
  return String(nextNonce);
}

async function lapiRecordURL(nonce) {
  const recordPath = `/LAPI/V1.0/Channels/${channel}/Media/Video/Streams/RecordURL?Begin=${epochBegin}&End=${epochEnd}`;
  const res = await httpRequest('GET', recordPath, { 'Authorization': buildDigestHeader(NVR_USER, NVR_PASS, 'NVRDVR', nonce, 'auth', 'GET', recordPath) });
  let data; try { data = JSON.parse(res.body); } catch (e) { throw new Error('RecordURL response not JSON: ' + res.body); }
  if (data.Response?.ResponseCode !== 0) throw new Error('RecordURL failed: ' + data.Response?.ResponseString);
  const rtspUrl = data.Response?.Data?.URL;
  if (!rtspUrl) throw new Error('No URL in RecordURL response');
  if (debug) console.log('RTSP URL:', rtspUrl);
  return rtspUrl;
}

// --- Capture ---

function captureClip(nextNonce, rtspUrl) {
  return new Promise((resolve, reject) => {
    const wsUrl = `ws://${NVR_IP}/webSocketServer`;
    const wsAuthUri = `ws://${NVR_IP}:/webSocketServer`;
    const basicAuth = Buffer.from(`${NVR_USER}:${NVR_PASS}`).toString('base64');
    const ws = new WebSocket(wsUrl, ['rtsp'], {
      headers: { 'Origin': `http://${NVR_IP}`, 'Cookie': 'WebLoginHandle=10081124', 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36' }
    });

    let cseq = 0, sessionId = null, wsAuthed = false, rtspState = 'WAIT_AUTH';
    const chunks = [];
    let bytes = 0, packets = 0, firstBinaryAt = 0, lastBinaryAt = 0, finished = false;
    let playResponse = '';

    // RTP timing stats (partial packets at chunk boundaries are tolerated — stats only)
    const rtp = { firstTs: null, lastTs: null, frames: 0, lastSeq: null, lost: 0 };
    function scanChunk(buf) {
      let off = 0;
      while (off + 4 < buf.length) {
        if (buf[off] !== 0x24) { off++; continue; }
        const ch = buf[off + 1];
        const len = buf.readUInt16BE(off + 2);
        const s = off + 4;
        if (s + len > buf.length) break;
        off = s + len;
        if (ch !== 0 || len < 12) continue;
        const seq = buf.readUInt16BE(s + 2);
        const ts = buf.readUInt32BE(s + 4);
        const marker = (buf[s + 1] & 0x80) !== 0;
        if (rtp.firstTs === null) rtp.firstTs = ts;
        rtp.lastTs = ts;
        if (marker) rtp.frames++;
        if (rtp.lastSeq !== null) {
          const expect = (rtp.lastSeq + 1) & 0xffff;
          if (seq !== expect) rtp.lost += ((seq - expect) & 0xffff);
        }
        rtp.lastSeq = seq;
      }
    }

    function finish(reason) {
      if (finished) return;
      finished = true;
      const wall = (lastBinaryAt - firstBinaryAt) / 1000;
      const media = rtp.firstTs !== null ? ((rtp.lastTs - rtp.firstTs) >>> 0) / 90000 : 0;
      const fps = media > 0 ? rtp.frames / media : 0;
      console.log(`\nStream ended (${reason}): ${packets} packets, ${(bytes / 1048576).toFixed(2)} MB, ${rtp.frames} frames`);
      console.log(`Media time: ${media.toFixed(1)}s  Wall time: ${wall.toFixed(1)}s  Speed: ${wall > 0 ? (media / wall).toFixed(2) : '?'}x realtime`);
      console.log(`Measured fps: ${fps.toFixed(2)}  RTP loss: ${rtp.lost} packets`);
      if (playResponse && debug) console.log('PLAY response:\n' + playResponse);
      cseq++;
      try { ws.send(`TEARDOWN ${rtspUrl} RTSP/1.0\r\nSession: ${sessionId}\r\nCSeq: ${cseq}\r\nUser-Agent: SFRtsp 0.3\r\nAuthorization: Basic ${basicAuth}\r\n\r\n`); } catch (e) {}
      ws.close();
      setTimeout(() => ws.terminate(), 500).unref();
      resolve({ buffer: Buffer.concat(chunks), fps: fps || 25, frames: rtp.frames, mediaSeconds: media });
    }

    ws.on('message', (data, isBinary) => {
      if (isBinary) {
        if (rtspState === 'PLAY' || rtspState === 'STREAMING') {
          rtspState = 'STREAMING';
          const chunk = Buffer.isBuffer(data) ? data : Buffer.from(data);
          chunks.push(chunk); bytes += chunk.length; packets++;
          const now = Date.now();
          if (!firstBinaryAt) firstBinaryAt = now;
          lastBinaryAt = now;
          scanChunk(chunk);
          if (debug && packets % 500 === 0) console.log(`  ${packets} pkts, ${(bytes / 1048576).toFixed(1)} MB, ${rtp.frames} frames`);
        }
        return;
      }
      const msg = data.toString();
      if (debug) console.log('<<<', msg.substring(0, 200).replace(/\r\n/g, ' | '));

      if (msg.includes('"errorCode"')) {
        let parsed; try { parsed = JSON.parse(msg); } catch (e) { return; }
        if (parsed.errorCode === 401 && !wsAuthed) {
          ws.send(`Authorization=${buildDigestHeader(NVR_USER, NVR_PASS, 'NVRDVR', nextNonce, 'auth', 'GET', wsAuthUri)}\r\n`);
          wsAuthed = true; return;
        }
        if (parsed.errorCode === 0) {
          rtspState = 'OPTIONS'; cseq++;
          ws.send(`OPTIONS ${rtspUrl} RTSP/1.0\r\nCSeq: ${cseq}\r\nUser-Agent: SFRtsp 0.3\r\n\r\n`);
          return;
        }
        ws.close(); reject(new Error('WebSocket auth failed: errorCode=' + parsed.errorCode)); return;
      }

      if (!msg.startsWith('RTSP/1.0')) {
        if (msg.includes('ANNOUNCE')) finish('server ANNOUNCE — end of window');
        return;
      }
      const sm = msg.match(/RTSP\/1\.0 (\d+)/);
      if (!sm) return;
      const code = parseInt(sm[1]);

      const sendSetup = () => {
        rtspState = 'SETUP'; cseq++;
        const setupUrl = rtspUrl.endsWith('/') ? `${rtspUrl}video` : `${rtspUrl}/video`;
        ws.send(`SETUP ${setupUrl} RTSP/1.0\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\nCSeq: ${cseq}\r\nUser-Agent: SFRtsp 0.3\r\nAuthorization: Basic ${basicAuth}\r\n\r\n`);
      };

      switch (rtspState) {
        case 'OPTIONS':
          if (code === 200) { rtspState = 'DESCRIBE'; cseq++; ws.send(`DESCRIBE ${rtspUrl} RTSP/1.0\r\nAccept: application/sdp\r\nCSeq: ${cseq}\r\nUser-Agent: SFRtsp 0.3\r\n\r\n`); }
          break;
        case 'DESCRIBE':
          if (code === 401) { rtspState = 'DESCRIBE_AUTH'; cseq++; ws.send(`DESCRIBE ${rtspUrl} RTSP/1.0\r\nAccept: application/sdp\r\nCSeq: ${cseq}\r\nUser-Agent: SFRtsp 0.3\r\nAuthorization: Basic ${basicAuth}\r\n\r\n`); }
          else if (code === 200) sendSetup();
          break;
        case 'DESCRIBE_AUTH':
          if (code === 200) sendSetup(); else { ws.close(); reject(new Error('RTSP DESCRIBE failed: ' + code)); }
          break;
        case 'SETUP':
          if (code === 200) {
            const s = msg.match(/Session:\s*(\S+)/);
            if (s) {
              sessionId = s[1]; rtspState = 'PLAY'; cseq++;
              const scaleHdr = scale !== '1' ? `Scale: ${scale}\r\n` : '';
              ws.send(`PLAY ${rtspUrl} RTSP/1.0\r\nSession: ${sessionId}\r\nCSeq: ${cseq}\r\n${scaleHdr}User-Agent: SFRtsp 0.3\r\nAuthorization: Basic ${basicAuth}\r\n\r\n`);
            }
          } else { ws.close(); reject(new Error('RTSP SETUP failed: ' + code)); }
          break;
        case 'PLAY':
        case 'STREAMING':
          if (code === 200) playResponse = msg;
          break;
      }
    });

    ws.on('error', (err) => { if (!finished) reject(err); });
    ws.on('close', () => { if (!finished) { if (packets > 0) finish('WS closed'); else reject(new Error('WebSocket closed before frames. State: ' + rtspState)); } });

    // Deadline: window length / scale + slack. Also idle guard: no data for 8s after data started.
    const deadline = setTimeout(() => finish('deadline'), (duration / parseFloat(scale) + 20) * 1000);
    deadline.unref();
    const idle = setInterval(() => {
      if (firstBinaryAt && Date.now() - lastBinaryAt > 8000) finish('idle 8s');
      if (!firstBinaryAt && Date.now() - startedAt > 30000) { clearInterval(idle); ws.close(); reject(new Error('No frames after 30s. State: ' + rtspState)); }
    }, 1000);
    idle.unref();
    const startedAt = Date.now();
  });
}

// --- Depacketize (same as nvr-frame.js) ---

function depacketizeH265(buffer) {
  const START = Buffer.from([0, 0, 0, 1]);
  const out = []; let off = 0; let fu = null;
  while (off + 4 < buffer.length) {
    if (buffer[off] !== 0x24) { off++; continue; }
    const ch = buffer[off + 1], len = buffer.readUInt16BE(off + 2), s = off + 4;
    if (s + len > buffer.length) break;
    off = s + len;
    if (ch !== 0 || len < 14) continue;
    const b0 = buffer[s], cc = b0 & 0x0f, ext = (b0 >> 4) & 1;
    let p = s + 12 + cc * 4;
    if (ext && p + 4 <= s + len) p += 4 + buffer.readUInt16BE(p + 2) * 4;
    if (p >= s + len) continue;
    const payload = buffer.slice(p, s + len);
    if (payload.length < 2) continue;
    const t = (payload[0] >> 1) & 0x3f;
    if (t === 49) {
      if (payload.length < 3) continue;
      const h = payload[2], st = (h >> 7) & 1, en = (h >> 6) & 1, ft = h & 0x3f;
      if (st) { const nh = Buffer.alloc(2); nh[0] = (payload[0] & 0x81) | (ft << 1); nh[1] = payload[1]; fu = [nh, payload.slice(3)]; }
      else if (fu) { fu.push(payload.slice(3)); if (en) { out.push(START, Buffer.concat(fu)); fu = null; } }
    } else if (t === 48) {
      let a = 2;
      while (a + 2 < payload.length) { const n = payload.readUInt16BE(a); a += 2; if (a + n <= payload.length) out.push(START, payload.slice(a, a + n)); a += n; }
    } else out.push(START, payload);
  }
  return Buffer.concat(out);
}

function muxMp4(h265, fps) {
  return new Promise((resolve) => {
    fs.writeFileSync(rawFile, h265);
    const fpsArg = fps.toFixed(3);
    const ff = spawn(FFMPEG, ['-y', '-f', 'hevc', '-r', fpsArg, '-i', rawFile, '-c', 'copy', '-movflags', '+faststart', output], { stdio: debug ? 'inherit' : 'pipe' });
    let err = '';
    if (!debug) ff.stderr.on('data', d => err += d.toString());
    ff.on('close', (code) => {
      if (code === 0 && fs.existsSync(output)) {
        const st = fs.statSync(output);
        console.log(`\nClip saved: ${output} (${(st.size / 1048576).toFixed(2)} MB, muxed at ${fpsArg} fps)`);
        try { fs.unlinkSync(rawFile); } catch (e) {}
        resolve(output);
      } else {
        console.log(`ffmpeg failed (code ${code}). Raw H.265 kept at: ${rawFile}`);
        if (!debug) console.log(err.slice(-600));
        resolve(rawFile);
      }
    });
    ff.on('error', (e) => { console.log('ffmpeg error:', e.message, '— raw H.265 at', rawFile); resolve(rawFile); });
  });
}

async function main() {
  try {
    const t0 = Date.now();
    const loginNonce = await lapiLogin();
    const nextNonce = await lapiKeepAlive(loginNonce);
    const rtspUrl = await lapiRecordURL(nextNonce);
    console.log(`LAPI ready in ${Date.now() - t0}ms, streaming...`);
    const cap = await captureClip(nextNonce, rtspUrl);
    const h265 = depacketizeH265(cap.buffer);
    console.log(`Depacketized: ${(cap.buffer.length / 1048576).toFixed(2)} MB RTP → ${(h265.length / 1048576).toFixed(2)} MB H.265`);
    if (!h265.length) throw new Error('No H.265 data');
    await muxMp4(h265, cap.fps);
    console.log(`Total: ${((Date.now() - t0) / 1000).toFixed(1)}s`);
    process.exit(0);
  } catch (err) {
    console.error('\nFailed:', err.message);
    if (debug) console.error(err.stack);
    process.exit(1);
  }
}

main();
