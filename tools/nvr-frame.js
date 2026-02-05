#!/usr/bin/env node
/**
 * nvr-frame.js - Capture a single frame from UNIVIEW NVR at specified timestamp
 *
 * Protocol (reverse-engineered from UNIVIEW web UI):
 *   1. LAPI Login (2-step digest) → get session nonce
 *   2. LAPI KeepAlive → get NextNonce
 *   3. LAPI RecordURL → get RTSP playback URL
 *   4. WebSocket connect (subprotocol "rtsp") + digest auth with NextNonce
 *   5. RTSP-over-WebSocket: OPTIONS → DESCRIBE → SETUP → PLAY
 *   6. Collect binary RTP frames → extract image with ffmpeg
 *
 * Usage:
 *   node nvr-frame.js --nvr nvr2 --channel 2 --time "2026-02-02T14:30:00"
 *   node nvr-frame.js --nvr nvr2 --channel 2 --time "2026-02-02T14:30:00" --dir C:\frames
 *
 * Output: ~/Pictures/nvr-frames/<nvr>_ch<channel>_<timestamp>.jpg by default
 *
 * Author: novicat gen-8, gen-9
 */

const WebSocket = require('ws');
const crypto = require('crypto');
const http = require('http');
const { spawn, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// Find ffmpeg — check PATH first, then WinGet Links
function findFfmpeg() {
  try { execSync('where ffmpeg', { stdio: 'pipe' }); return 'ffmpeg'; } catch (e) {}
  const wingetLinks = path.join(process.env.LOCALAPPDATA || '', 'Microsoft/WinGet/Links/ffmpeg.exe');
  if (fs.existsSync(wingetLinks)) return wingetLinks;
  return 'ffmpeg'; // fallback, let it fail with clear error
}
const FFMPEG = findFfmpeg();

// Parse arguments
const args = process.argv.slice(2);
function getArg(name) {
  const idx = args.indexOf(`--${name}`);
  return idx !== -1 && args[idx + 1] ? args[idx + 1] : null;
}

const nvrId = getArg('nvr');
const channel = getArg('channel');
const timeStr = getArg('time');
const outDir = getArg('dir');
const debug = args.includes('--debug');

if (!nvrId || !channel || !timeStr) {
  console.error('Usage: node nvr-frame.js --nvr <nvr_id> --channel <num> --time <timestamp> [--dir <path>] [--debug]');
  console.error('Example: node nvr-frame.js --nvr nvr2 --channel 2 --time "2026-02-02T14:30:00"');
  process.exit(1);
}

// Build output path: <dir>/<nvr>_ch<channel>_<timestamp>.jpg
const picturesDir = outDir || path.join(process.env.USERPROFILE || process.env.HOME, 'Pictures', 'nvr-frames');
fs.mkdirSync(picturesDir, { recursive: true });
const safeTime = timeStr.replace(/[:/]/g, '-');
const output = path.join(picturesDir, `${nvrId}_ch${channel}_${safeTime}.jpg`);

// Load NVR config from lodge.db
const db = require('better-sqlite3')(path.join(__dirname, '../warehouses/lodge/lodge.db'));
const nvr = db.prepare('SELECT * FROM nvrs WHERE id = ?').get(nvrId);

if (!nvr) {
  console.error(`NVR '${nvrId}' not found in lodge.db`);
  process.exit(1);
}

if (nvr.brand !== 'UNIVIEW') {
  console.error(`NVR '${nvrId}' is ${nvr.brand} - WebSocket playback only supported on UNIVIEW`);
  process.exit(1);
}

const NVR_IP = nvr.ip;
const NVR_USER = nvr.username || 'admin';
const NVR_PASS = nvr.password || '';

// Parse timestamp to epoch
const timestamp = new Date(timeStr);
if (isNaN(timestamp.getTime())) {
  console.error(`Invalid timestamp: ${timeStr}`);
  process.exit(1);
}
const epochBegin = Math.floor(timestamp.getTime() / 1000);
const epochEnd = epochBegin + 60; // 1 minute window

console.log(`NVR: ${nvrId} (${NVR_IP})`);
console.log(`Channel: ${channel}`);
console.log(`Time: ${timestamp.toISOString()} (epoch: ${epochBegin})`);
console.log(`Output: ${output}`);

// --- Digest Auth Helpers ---

function md5(str) {
  return crypto.createHash('md5').update(str).digest('hex');
}

function buildDigestHeader(username, password, realm, nonce, qop, method, uri) {
  const nc = '00000001';
  const cnonce = Math.floor(Math.random() * 2000000000).toString();
  const ha1 = md5(`${username}:${realm}:${password}`);
  const ha2 = md5(`${method}:${uri}`);
  const response = md5(`${ha1}:${nonce}:${nc}:${cnonce}:${qop}:${ha2}`);
  return `Digest username="${username}",realm="${realm}",qop="${qop}", nonce="${nonce}",algorithm="MD5",cnonce="${cnonce}",nc="${nc}",uri="${uri}",response="${response}"`;
}

// --- HTTP Request Helper ---

function httpRequest(method, urlPath, headers = {}) {
  return new Promise((resolve, reject) => {
    const opts = {
      hostname: NVR_IP,
      port: 80,
      path: urlPath,
      method: method,
      headers: {
        'Cookie': 'WebLoginHandle=10081124',
        'Accept': 'application/json',
        ...headers
      },
      timeout: 10000
    };

    const req = http.request(opts, (res) => {
      let body = '';
      res.on('data', chunk => body += chunk);
      res.on('end', () => {
        resolve({
          status: res.statusCode,
          headers: res.headers,
          body: body
        });
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('HTTP timeout')); });
    req.end();
  });
}

// --- Step 1: LAPI Login ---

async function lapiLogin() {
  if (debug) console.log('\n--- Step 1: LAPI Login ---');

  // First request: unauthenticated → get nonce from WWW-Authenticate
  const loginPath = '/LAPI/V1.0/System/Security/Login';
  const res1 = await httpRequest('PUT', loginPath);
  if (debug) console.log('Login challenge:', res1.status, res1.body.substring(0, 200));

  const wwwAuth = res1.headers['www-authenticate'];
  if (!wwwAuth) throw new Error('No WWW-Authenticate header in login response');

  const nonceMatch = wwwAuth.match(/nonce="([^"]+)"/);
  if (!nonceMatch) throw new Error('No nonce in WWW-Authenticate: ' + wwwAuth);
  const loginNonce = nonceMatch[1];
  if (debug) console.log('Login nonce:', loginNonce);

  // Second request: authenticated
  const authHeader = buildDigestHeader(NVR_USER, NVR_PASS, 'NVRDVR', loginNonce, 'auth', 'PUT', loginPath);
  const res2 = await httpRequest('PUT', loginPath, { 'Authorization': authHeader });

  let loginData;
  try { loginData = JSON.parse(res2.body); } catch (e) { throw new Error('Login response not JSON: ' + res2.body); }

  if (loginData.Response?.ResponseCode !== 0) {
    throw new Error('Login failed: ' + loginData.Response?.ResponseString);
  }
  if (debug) console.log('Login OK');

  return loginNonce;
}

// --- Step 2: LAPI KeepAlive → NextNonce ---

async function lapiKeepAlive(nonce) {
  if (debug) console.log('\n--- Step 2: LAPI KeepAlive ---');

  const kaPath = '/LAPI/V1.0/System/Security/KeepAlive';
  const authHeader = buildDigestHeader(NVR_USER, NVR_PASS, 'NVRDVR', nonce, 'auth', 'PUT', kaPath);
  const res = await httpRequest('PUT', kaPath, { 'Authorization': authHeader });

  let data;
  try { data = JSON.parse(res.body); } catch (e) { throw new Error('KeepAlive response not JSON: ' + res.body); }

  if (data.Response?.ResponseCode !== 0) {
    throw new Error('KeepAlive failed: ' + data.Response?.ResponseString);
  }

  const nextNonce = data.Response?.Data?.NextNonce;
  if (!nextNonce) throw new Error('No NextNonce in KeepAlive response');
  if (debug) console.log('NextNonce:', nextNonce);

  return String(nextNonce);
}

// --- Step 3: LAPI RecordURL ---

async function lapiRecordURL(nonce) {
  if (debug) console.log('\n--- Step 3: LAPI RecordURL ---');

  const recordPath = `/LAPI/V1.0/Channels/${channel}/Media/Video/Streams/RecordURL?Begin=${epochBegin}&End=${epochEnd}`;
  const authHeader = buildDigestHeader(NVR_USER, NVR_PASS, 'NVRDVR', nonce, 'auth', 'GET', recordPath);
  const res = await httpRequest('GET', recordPath, { 'Authorization': authHeader });

  let data;
  try { data = JSON.parse(res.body); } catch (e) { throw new Error('RecordURL response not JSON: ' + res.body); }

  if (data.Response?.ResponseCode !== 0) {
    throw new Error('RecordURL failed: ' + data.Response?.ResponseString);
  }

  const rtspUrl = data.Response?.Data?.URL;
  if (!rtspUrl) throw new Error('No URL in RecordURL response');
  if (debug) console.log('RTSP URL:', rtspUrl);

  return rtspUrl;
}

// --- Steps 4-6: WebSocket + RTSP + Frame Capture ---

function captureFrame(nextNonce, rtspUrl) {
  return new Promise((resolve, reject) => {
    if (debug) console.log('\n--- Step 4: WebSocket Connect ---');

    const wsUrl = `ws://${NVR_IP}/webSocketServer`;
    // URI format matches browser exactly (note colon with empty port)
    const wsAuthUri = `ws://${NVR_IP}:/webSocketServer`;
    const basicAuth = Buffer.from(`${NVR_USER}:${NVR_PASS}`).toString('base64');

    const ws = new WebSocket(wsUrl, ['rtsp'], {
      headers: {
        'Origin': `http://${NVR_IP}`,
        'Cookie': 'WebLoginHandle=10081124',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
      }
    });

    let cseq = 0;
    let sessionId = null;
    let wsAuthed = false;
    let rtspState = 'WAIT_AUTH'; // WAIT_AUTH → OPTIONS → DESCRIBE → DESCRIBE_AUTH → SETUP → PLAY → STREAMING
    const binaryChunks = [];
    let frameCount = 0;
    let closing = false;
    let firstBinaryAt = 0;

    // NAL type tracker — stop as soon as we have a decodable keyframe
    const nal = { hasVPS: false, hasSPS: false, hasPPS: false, idrStarted: false, idrComplete: false, extraAfterIDR: 0 };
    let scanOffset = 0; // where we left off scanning the accumulated buffer

    function scanAccumulated() {
      // Concat full buffer and scan from where we left off
      const buf = Buffer.concat(binaryChunks);
      let off = scanOffset;
      while (off + 4 < buf.length) {
        if (buf[off] !== 0x24) { off++; continue; }
        const ch = buf[off + 1];
        const pktLen = buf.readUInt16BE(off + 2);
        const pktStart = off + 4;
        if (pktStart + pktLen > buf.length) break; // incomplete packet, wait for more data
        off = pktStart + pktLen;
        if (ch !== 0) continue; // skip RTCP (channel 1)
        if (pktLen < 14) continue;

        // Parse RTP header
        const rtpByte0 = buf[pktStart];
        const csrcCount = rtpByte0 & 0x0f;
        const extension = (rtpByte0 >> 4) & 1;
        let payOff = pktStart + 12 + csrcCount * 4;
        if (extension && payOff + 4 <= pktStart + pktLen) {
          payOff += 4 + buf.readUInt16BE(payOff + 2) * 4;
        }
        if (payOff + 2 > pktStart + pktLen) continue;

        const nalType = (buf[payOff] >> 1) & 0x3f;
        if (nalType === 32) nal.hasVPS = true;
        else if (nalType === 33) nal.hasSPS = true;
        else if (nalType === 34) nal.hasPPS = true;
        else if (nalType === 19 || nalType === 20) { nal.idrComplete = true; }
        else if (nalType === 49 && payOff + 3 <= pktStart + pktLen) {
          const fuType = buf[payOff + 2] & 0x3f;
          if (fuType === 19 || fuType === 20) {
            if ((buf[payOff + 2] >> 7) & 1) nal.idrStarted = true;
            if (((buf[payOff + 2] >> 6) & 1) && nal.idrStarted) nal.idrComplete = true;
          }
        }
      }
      scanOffset = off;
    }

    function haveKeyframe() {
      return nal.hasVPS && nal.hasSPS && nal.hasPPS && nal.idrComplete;
    }

    ws.on('open', () => {
      if (debug) console.log('WebSocket connected, waiting for auth challenge...');
    });

    ws.on('message', (data, isBinary) => {
      // Binary frame — RTP video data
      if (isBinary) {
        if (rtspState === 'STREAMING' || rtspState === 'PLAY') {
          rtspState = 'STREAMING';
          const chunk = Buffer.isBuffer(data) ? data : Buffer.from(data);
          binaryChunks.push(chunk);
          frameCount++;
          if (!firstBinaryAt) firstBinaryAt = Date.now();

          scanAccumulated();

          if (haveKeyframe()) nal.extraAfterIDR++;

          if (debug && frameCount % 20 === 0) {
            console.log(`  ${frameCount} packets | VPS:${nal.hasVPS} SPS:${nal.hasSPS} PPS:${nal.hasPPS} IDR:${nal.idrComplete}`);
          }

          if (haveKeyframe() && nal.extraAfterIDR >= 3 && !closing) {
            closing = true;
            if (debug) console.log(`Got keyframe after ${frameCount} packets (${Date.now() - firstBinaryAt}ms since first packet), closing...`);
            cseq++;
            try {
              ws.send(`TEARDOWN ${rtspUrl} RTSP/1.0\r\nSession: ${sessionId}\r\nCSeq: ${cseq}\r\nUser-Agent: SFRtsp 0.3\r\nAuthorization: Basic ${basicAuth}\r\n\r\n`);
            } catch (e) {}
            ws.close();
          }
        }
        return;
      }

      const msg = data.toString();
      if (debug) console.log('<<<', msg.substring(0, 300));

      // --- WebSocket-level auth ---
      if (msg.includes('"errorCode"')) {
        let parsed;
        try { parsed = JSON.parse(msg); } catch (e) { return; }

        if (parsed.errorCode === 401 && !wsAuthed) {
          if (debug) console.log('\n--- Step 4b: WebSocket Auth (using NextNonce) ---');

          // Use NextNonce from KeepAlive, NOT the nonce in this 401 challenge
          const authStr = buildDigestHeader(NVR_USER, NVR_PASS, 'NVRDVR', nextNonce, 'auth', 'GET', wsAuthUri);
          const authMsg = `Authorization=${authStr}\r\n`;
          if (debug) console.log('>>>', authMsg.substring(0, 200));
          ws.send(authMsg);
          wsAuthed = true;
          return;
        }

        if (parsed.errorCode === 0) {
          if (debug) console.log('WebSocket authenticated!');
          rtspState = 'OPTIONS';

          // Send OPTIONS
          if (debug) console.log('\n--- Step 5: RTSP State Machine ---');
          cseq++;
          const optionsMsg = `OPTIONS ${rtspUrl} RTSP/1.0\r\nCSeq: ${cseq}\r\nUser-Agent: SFRtsp 0.3\r\n\r\n`;
          if (debug) console.log('>>>', optionsMsg.trim());
          ws.send(optionsMsg);
          return;
        }

        // Other error
        console.error('WebSocket auth error:', parsed);
        ws.close();
        reject(new Error('WebSocket auth failed: errorCode=' + parsed.errorCode));
        return;
      }

      // --- RTSP responses ---
      if (!msg.startsWith('RTSP/1.0')) {
        // Could be an ANNOUNCE (end of playback) or other server message
        if (msg.includes('ANNOUNCE')) {
          if (debug) console.log('Server ANNOUNCE — playback ended');
          ws.close();
        }
        return;
      }

      const statusMatch = msg.match(/RTSP\/1\.0 (\d+)/);
      if (!statusMatch) return;
      const statusCode = parseInt(statusMatch[1]);

      switch (rtspState) {
        case 'OPTIONS':
          if (statusCode === 200) {
            // Send DESCRIBE
            rtspState = 'DESCRIBE';
            cseq++;
            const descMsg = `DESCRIBE ${rtspUrl} RTSP/1.0\r\nAccept: application/sdp\r\nCSeq: ${cseq}\r\nUser-Agent: SFRtsp 0.3\r\n\r\n`;
            if (debug) console.log('>>>', descMsg.trim());
            ws.send(descMsg);
          }
          break;

        case 'DESCRIBE':
          if (statusCode === 401) {
            // RTSP auth required — resend with Basic auth (matches browser behavior)
            rtspState = 'DESCRIBE_AUTH';
            cseq++;
            const descAuthMsg = `DESCRIBE ${rtspUrl} RTSP/1.0\r\nAccept: application/sdp\r\nCSeq: ${cseq}\r\nUser-Agent: SFRtsp 0.3\r\nAuthorization: Basic ${basicAuth}\r\n\r\n`;
            if (debug) console.log('>>> (with Basic auth)', descAuthMsg.trim().substring(0, 150));
            ws.send(descAuthMsg);
          } else if (statusCode === 200) {
            // No auth needed, go to SETUP
            sendSetup();
          }
          break;

        case 'DESCRIBE_AUTH':
          if (statusCode === 200) {
            sendSetup();
          } else {
            console.error('DESCRIBE failed after auth:', statusCode);
            ws.close();
            reject(new Error('RTSP DESCRIBE failed: ' + statusCode));
          }
          break;

        case 'SETUP':
          if (statusCode === 200) {
            // Extract session ID
            const sessMatch = msg.match(/Session:\s*(\S+)/);
            if (sessMatch) {
              sessionId = sessMatch[1];
              // Send PLAY
              rtspState = 'PLAY';
              cseq++;
              const playMsg = `PLAY ${rtspUrl} RTSP/1.0\r\nSession: ${sessionId}\r\nCSeq: ${cseq}\r\nUser-Agent: SFRtsp 0.3\r\nAuthorization: Basic ${basicAuth}\r\n\r\n`;
              if (debug) console.log('>>>', playMsg.trim().substring(0, 150));
              ws.send(playMsg);
              if (debug) console.log('\n--- Step 6: Waiting for video frames ---');
            }
          } else {
            console.error('SETUP failed:', statusCode);
            ws.close();
            reject(new Error('RTSP SETUP failed: ' + statusCode));
          }
          break;

        case 'PLAY':
        case 'STREAMING':
          if (statusCode === 200 && msg.includes('Scale:')) {
            if (debug) console.log('PLAY accepted, streaming...');
          }
          break;
      }

      function sendSetup() {
        rtspState = 'SETUP';
        cseq++;
        const setupUrl = rtspUrl.endsWith('/') ? `${rtspUrl}video` : `${rtspUrl}/video`;
        const setupMsg = `SETUP ${setupUrl} RTSP/1.0\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\nCSeq: ${cseq}\r\nUser-Agent: SFRtsp 0.3\r\nAuthorization: Basic ${basicAuth}\r\n\r\n`;
        if (debug) console.log('>>>', setupMsg.trim().substring(0, 200));
        ws.send(setupMsg);
      }
    });

    ws.on('error', (err) => {
      console.error('WebSocket error:', err.message);
      reject(err);
    });

    ws.on('close', () => {
      if (debug) console.log(`WebSocket closed (${frameCount} frames received)`);
      if (frameCount > 0) {
        extractFrame(Buffer.concat(binaryChunks), resolve, reject);
      } else if (rtspState !== 'STREAMING') {
        reject(new Error('WebSocket closed before receiving frames. State: ' + rtspState));
      }
    });

    // Timeout — unref so it doesn't keep the process alive after successful capture
    const wsTimeout = setTimeout(() => {
      if (frameCount === 0) {
        console.error('Timeout: no frames received after 30s. State:', rtspState);
        ws.close();
      }
    }, 30000);
    wsTimeout.unref();
  });
}

// --- Frame Extraction ---

// Depacketize RTSP-interleaved RTP into raw H.265 Annex B bitstream
function depacketizeH265(buffer) {
  const START_CODE = Buffer.from([0x00, 0x00, 0x00, 0x01]);
  const chunks = [];
  let offset = 0;
  let fuBuffer = null; // accumulator for fragmentation unit reassembly

  while (offset + 4 < buffer.length) {
    // RTSP interleaved framing: $ (0x24) + channel + 2-byte length
    if (buffer[offset] !== 0x24) { offset++; continue; }
    const channel = buffer[offset + 1];
    const pktLen = buffer.readUInt16BE(offset + 2);
    const pktStart = offset + 4;
    if (pktStart + pktLen > buffer.length) break;
    offset = pktStart + pktLen;

    if (channel !== 0) continue; // skip RTCP (channel 1)
    if (pktLen < 14) continue;   // too short for RTP + HEVC header

    // RTP header: 12 bytes minimum
    const rtpByte0 = buffer[pktStart];
    const csrcCount = rtpByte0 & 0x0f;
    const extension = (rtpByte0 >> 4) & 1;
    let payloadOffset = pktStart + 12 + csrcCount * 4;

    // Skip RTP header extension if present
    if (extension && payloadOffset + 4 <= pktStart + pktLen) {
      const extLen = buffer.readUInt16BE(payloadOffset + 2);
      payloadOffset += 4 + extLen * 4;
    }

    if (payloadOffset >= pktStart + pktLen) continue;
    const payload = buffer.slice(payloadOffset, pktStart + pktLen);
    if (payload.length < 2) continue;

    // HEVC NAL unit header (2 bytes): F(1) Type(6) LayerId(6) TID(3)
    const nalType = (payload[0] >> 1) & 0x3f;

    if (nalType === 49) {
      // Fragmentation Unit (FU) — RFC 7798 Section 4.4.3
      if (payload.length < 3) continue;
      const fuHeader = payload[2];
      const startBit = (fuHeader >> 7) & 1;
      const endBit = (fuHeader >> 6) & 1;
      const fuType = fuHeader & 0x3f;

      if (startBit) {
        // Reconstruct NAL header from FU type
        const nalHeader = Buffer.alloc(2);
        nalHeader[0] = (payload[0] & 0x81) | (fuType << 1);
        nalHeader[1] = payload[1];
        fuBuffer = [nalHeader, payload.slice(3)];
      } else if (fuBuffer) {
        fuBuffer.push(payload.slice(3));
        if (endBit) {
          chunks.push(START_CODE, Buffer.concat(fuBuffer));
          fuBuffer = null;
        }
      }
    } else if (nalType === 48) {
      // Aggregation Packet (AP) — RFC 7798 Section 4.4.2
      let apOffset = 2;
      while (apOffset + 2 < payload.length) {
        const naluSize = payload.readUInt16BE(apOffset);
        apOffset += 2;
        if (apOffset + naluSize <= payload.length) {
          chunks.push(START_CODE, payload.slice(apOffset, apOffset + naluSize));
        }
        apOffset += naluSize;
      }
    } else {
      // Single NAL unit — pass through with start code
      chunks.push(START_CODE, payload);
    }
  }

  return Buffer.concat(chunks);
}

function extractFrame(buffer, resolve, reject) {
  if (debug) console.log(`\nExtracting frame from ${buffer.length} bytes of stream data...`);

  // Depacketize RTP → raw H.265
  const h265Data = depacketizeH265(buffer);
  if (debug) console.log(`Depacketized: ${buffer.length} bytes RTP → ${h265Data.length} bytes H.265`);

  if (h265Data.length === 0) {
    console.error('No H.265 data found in stream');
    reject(new Error('Depacketization produced no data'));
    return;
  }

  const rawFile = output.replace(/\.[^.]+$/, '.h265');
  fs.writeFileSync(rawFile, h265Data);

  // Use ffmpeg to extract frame from raw H.265
  const ffmpeg = spawn(FFMPEG, [
    '-y',
    '-f', 'hevc',
    '-i', rawFile,
    '-vframes', '1',
    '-q:v', '2',
    output
  ], { stdio: debug ? 'inherit' : 'pipe' });

  ffmpeg.on('close', (code) => {
    if (code === 0 && fs.existsSync(output)) {
      const stats = fs.statSync(output);
      console.log(`\nFrame saved: ${output} (${stats.size} bytes)`);
      try { fs.unlinkSync(rawFile); } catch (e) {}
      resolve(output);
    } else {
      console.log('ffmpeg could not extract frame');
      console.log(`Raw H.265 bitstream available at: ${rawFile}`);
      resolve(rawFile);
    }
  });

  ffmpeg.on('error', (err) => {
    if (err.code === 'ENOENT') {
      console.log('ffmpeg not found — install ffmpeg for JPEG output');
      console.log(`Raw H.265 bitstream available at: ${rawFile}`);
      resolve(rawFile);
    } else {
      console.error('ffmpeg error:', err.message);
      reject(err);
    }
  });
}

// --- Main ---

async function main() {
  try {
    const t0 = Date.now();
    const loginNonce = await lapiLogin();
    const t1 = Date.now();
    const nextNonce = await lapiKeepAlive(loginNonce);
    const t2 = Date.now();
    const rtspUrl = await lapiRecordURL(nextNonce);
    const t3 = Date.now();
    await captureFrame(nextNonce, rtspUrl);
    const t4 = Date.now();
    if (debug) {
      console.log(`\n--- Timing ---`);
      console.log(`Login: ${t1-t0}ms | KeepAlive: ${t2-t1}ms | RecordURL: ${t3-t2}ms | Capture: ${t4-t3}ms | Total: ${t4-t0}ms`);
    }
  } catch (err) {
    console.error('\nFailed:', err.message);
    if (debug) console.error(err.stack);
    process.exit(1);
  }
}

main();
