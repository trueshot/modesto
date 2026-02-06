"""
UNIVIEW NVR Historic Frame Playback

Captures a single frame from NVR recorded video at a specified timestamp.
Ported from nvr-frame.js (novicat gen-8/9) by modeltcamerascat gen-12.

Protocol (reverse-engineered from UNIVIEW web UI):
  1. LAPI Login (2-step digest) → get session nonce
  2. LAPI KeepAlive → get NextNonce
  3. LAPI RecordURL → get RTSP playback URL
  4. WebSocket connect (subprotocol "rtsp") + digest auth with NextNonce
  5. RTSP-over-WebSocket: OPTIONS → DESCRIBE → SETUP → PLAY
  6. Collect binary RTP frames → depacketize H.265 → extract JPEG via ffmpeg

Only works with UNIVIEW NVRs.
"""

import hashlib
import struct
import base64
import json
import logging
import subprocess
import shutil
import threading
import queue
import time
import random
from typing import Optional
from io import BytesIO

import requests
from requests.auth import HTTPDigestAuth
import websocket

logger = logging.getLogger(__name__)

# Locate ffmpeg
def _find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    # WinGet default location
    import os
    winget = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "ffmpeg.exe")
    if os.path.isfile(winget):
        return winget
    return "ffmpeg"

FFMPEG = _find_ffmpeg()


# --- Digest Auth Helpers ---

def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _build_digest_header(username: str, password: str, realm: str, nonce: str,
                          qop: str, method: str, uri: str) -> str:
    nc = "00000001"
    cnonce = str(random.randint(0, 2000000000))
    ha1 = _md5(f"{username}:{realm}:{password}")
    ha2 = _md5(f"{method}:{uri}")
    response = _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return (
        f'Digest username="{username}",realm="{realm}",qop="{qop}",'
        f' nonce="{nonce}",algorithm="MD5",cnonce="{cnonce}",'
        f'nc="{nc}",uri="{uri}",response="{response}"'
    )


# --- LAPI Steps ---

def _lapi_login(ip: str, username: str, password: str) -> str:
    """Step 1: LAPI Login → returns login nonce."""
    login_url = f"http://{ip}/LAPI/V1.0/System/Security/Login"
    headers = {"Cookie": "WebLoginHandle=10081124", "Accept": "application/json"}

    # First request: get nonce from 401
    r1 = requests.put(login_url, headers=headers, timeout=10)
    www_auth = r1.headers.get("WWW-Authenticate", "")
    import re
    m = re.search(r'nonce="([^"]+)"', www_auth)
    if not m:
        raise RuntimeError(f"No nonce in LAPI login response: {www_auth}")
    login_nonce = m.group(1)

    # Second request: authenticated
    auth_header = _build_digest_header(username, password, "NVRDVR", login_nonce, "auth", "PUT",
                                        "/LAPI/V1.0/System/Security/Login")
    headers["Authorization"] = auth_header
    r2 = requests.put(login_url, headers=headers, timeout=10)
    data = r2.json()

    if data.get("Response", {}).get("ResponseCode") != 0:
        raise RuntimeError(f"LAPI login failed: {data.get('Response', {}).get('ResponseString')}")

    return login_nonce


def _lapi_keepalive(ip: str, username: str, password: str, nonce: str) -> str:
    """Step 2: LAPI KeepAlive → returns NextNonce."""
    ka_url = f"http://{ip}/LAPI/V1.0/System/Security/KeepAlive"
    ka_path = "/LAPI/V1.0/System/Security/KeepAlive"
    auth_header = _build_digest_header(username, password, "NVRDVR", nonce, "auth", "PUT", ka_path)
    headers = {
        "Cookie": "WebLoginHandle=10081124",
        "Accept": "application/json",
        "Authorization": auth_header,
    }
    r = requests.put(ka_url, headers=headers, timeout=10)
    data = r.json()

    if data.get("Response", {}).get("ResponseCode") != 0:
        raise RuntimeError(f"LAPI KeepAlive failed: {data.get('Response', {}).get('ResponseString')}")

    next_nonce = data["Response"]["Data"]["NextNonce"]
    if next_nonce is None:
        raise RuntimeError("No NextNonce in KeepAlive response")
    return str(next_nonce)


def _lapi_record_url(ip: str, username: str, password: str, nonce: str,
                      channel: int, epoch_begin: int, epoch_end: int) -> str:
    """Step 3: LAPI RecordURL → returns RTSP playback URL."""
    record_path = f"/LAPI/V1.0/Channels/{channel}/Media/Video/Streams/RecordURL?Begin={epoch_begin}&End={epoch_end}"
    record_url = f"http://{ip}{record_path}"
    auth_header = _build_digest_header(username, password, "NVRDVR", nonce, "auth", "GET", record_path)
    headers = {
        "Cookie": "WebLoginHandle=10081124",
        "Accept": "application/json",
        "Authorization": auth_header,
    }
    r = requests.get(record_url, headers=headers, timeout=10)
    data = r.json()

    if data.get("Response", {}).get("ResponseCode") != 0:
        raise RuntimeError(f"LAPI RecordURL failed: {data.get('Response', {}).get('ResponseString')}")

    rtsp_url = data["Response"]["Data"]["URL"]
    if not rtsp_url:
        raise RuntimeError("No URL in RecordURL response")
    return rtsp_url


# --- H.265 Depacketization ---

def _depacketize_h265(buf: bytes) -> bytes:
    """
    Depacketize RTSP-interleaved RTP into raw H.265 Annex B bitstream.
    Handles FU (type 49) and AP (type 48) per RFC 7798.
    """
    START_CODE = b'\x00\x00\x00\x01'
    chunks = []
    offset = 0
    fu_buffer = None
    length = len(buf)

    while offset + 4 < length:
        if buf[offset] != 0x24:
            offset += 1
            continue
        ch = buf[offset + 1]
        pkt_len = struct.unpack('>H', buf[offset + 2:offset + 4])[0]
        pkt_start = offset + 4
        if pkt_start + pkt_len > length:
            break
        offset = pkt_start + pkt_len

        if ch != 0:
            continue  # skip RTCP
        if pkt_len < 14:
            continue

        # RTP header
        rtp_byte0 = buf[pkt_start]
        csrc_count = rtp_byte0 & 0x0F
        extension = (rtp_byte0 >> 4) & 1
        pay_off = pkt_start + 12 + csrc_count * 4

        if extension and pay_off + 4 <= pkt_start + pkt_len:
            ext_len = struct.unpack('>H', buf[pay_off + 2:pay_off + 4])[0]
            pay_off += 4 + ext_len * 4

        if pay_off >= pkt_start + pkt_len:
            continue
        payload = buf[pay_off:pkt_start + pkt_len]
        if len(payload) < 2:
            continue

        nal_type = (payload[0] >> 1) & 0x3F

        if nal_type == 49:
            # Fragmentation Unit (FU)
            if len(payload) < 3:
                continue
            fu_header = payload[2]
            start_bit = (fu_header >> 7) & 1
            end_bit = (fu_header >> 6) & 1
            fu_type = fu_header & 0x3F

            if start_bit:
                # Reconstruct NAL header
                nal_h0 = (payload[0] & 0x81) | (fu_type << 1)
                nal_h1 = payload[1]
                fu_buffer = [bytes([nal_h0, nal_h1]), payload[3:]]
            elif fu_buffer is not None:
                fu_buffer.append(payload[3:])
                if end_bit:
                    chunks.append(START_CODE)
                    chunks.append(b''.join(fu_buffer))
                    fu_buffer = None

        elif nal_type == 48:
            # Aggregation Packet (AP)
            ap_off = 2
            while ap_off + 2 < len(payload):
                nalu_size = struct.unpack('>H', payload[ap_off:ap_off + 2])[0]
                ap_off += 2
                if ap_off + nalu_size <= len(payload):
                    chunks.append(START_CODE)
                    chunks.append(payload[ap_off:ap_off + nalu_size])
                ap_off += nalu_size

        else:
            # Single NAL unit
            chunks.append(START_CODE)
            chunks.append(payload)

    return b''.join(chunks)


# --- WebSocket + RTSP Capture ---

def _ws_capture_frame(nvr_ip: str, username: str, password: str,
                       next_nonce: str, rtsp_url: str, timeout: int = 25) -> Optional[bytes]:
    """
    Steps 4-6: Connect WebSocket, authenticate, run RTSP state machine,
    collect RTP packets until keyframe, depacketize, extract JPEG via ffmpeg.

    Returns JPEG bytes or None.
    """
    ws_url = f"ws://{nvr_ip}/webSocketServer"
    ws_auth_uri = f"ws://{nvr_ip}:/webSocketServer"
    basic_auth = base64.b64encode(f"{username}:{password}".encode()).decode()

    result_q = queue.Queue()
    binary_chunks = []
    state = {"rtsp": "WAIT_AUTH", "cseq": 0, "session_id": None, "ws_authed": False,
             "frame_count": 0, "closing": False, "scan_offset": 0,
             "has_vps": False, "has_sps": False, "has_pps": False,
             "idr_started": False, "idr_complete": False, "extra_after_idr": 0}

    def have_keyframe():
        return state["has_vps"] and state["has_sps"] and state["has_pps"] and state["idr_complete"]

    def scan_accumulated():
        """Scan accumulated RTP for NAL types to detect complete keyframe."""
        buf = b''.join(binary_chunks)
        off = state["scan_offset"]
        length = len(buf)
        while off + 4 < length:
            if buf[off] != 0x24:
                off += 1
                continue
            pkt_len = struct.unpack('>H', buf[off + 2:off + 4])[0]
            pkt_start = off + 4
            if pkt_start + pkt_len > length:
                break
            ch = buf[off + 1]
            off = pkt_start + pkt_len
            if ch != 0 or pkt_len < 14:
                continue

            rtp_byte0 = buf[pkt_start]
            csrc_count = rtp_byte0 & 0x0F
            extension = (rtp_byte0 >> 4) & 1
            pay_off = pkt_start + 12 + csrc_count * 4
            if extension and pay_off + 4 <= pkt_start + pkt_len:
                pay_off += 4 + struct.unpack('>H', buf[pay_off + 2:pay_off + 4])[0] * 4
            if pay_off + 2 > pkt_start + pkt_len:
                continue

            nal_type = (buf[pay_off] >> 1) & 0x3F
            if nal_type == 32:
                state["has_vps"] = True
            elif nal_type == 33:
                state["has_sps"] = True
            elif nal_type == 34:
                state["has_pps"] = True
            elif nal_type in (19, 20):
                state["idr_complete"] = True
            elif nal_type == 49 and pay_off + 3 <= pkt_start + pkt_len:
                fu_type = buf[pay_off + 2] & 0x3F
                if fu_type in (19, 20):
                    if (buf[pay_off + 2] >> 7) & 1:
                        state["idr_started"] = True
                    if ((buf[pay_off + 2] >> 6) & 1) and state["idr_started"]:
                        state["idr_complete"] = True
        state["scan_offset"] = off

    def extract_jpeg(raw_data: bytes) -> Optional[bytes]:
        """Depacketize H.265 and extract single JPEG frame via ffmpeg."""
        h265_data = _depacketize_h265(raw_data)
        if not h265_data:
            logger.error("Depacketization produced no H.265 data")
            return None

        try:
            proc = subprocess.run(
                [FFMPEG, "-y", "-f", "hevc", "-i", "pipe:0",
                 "-vframes", "1", "-q:v", "2", "-f", "mjpeg", "pipe:1"],
                input=h265_data,
                capture_output=True,
                timeout=15
            )
            if proc.returncode == 0 and len(proc.stdout) > 0:
                return proc.stdout
            else:
                logger.error(f"ffmpeg failed (rc={proc.returncode}): {proc.stderr[:500]}")
                return None
        except subprocess.TimeoutExpired:
            logger.error("ffmpeg timed out")
            return None
        except FileNotFoundError:
            logger.error("ffmpeg not found")
            return None

    def on_message(ws_conn, data, opcode, _):
        # opcode 2 = binary, 1 = text
        is_binary = (opcode == websocket.ABNF.OPCODE_BINARY)

        if is_binary:
            if state["rtsp"] in ("STREAMING", "PLAY"):
                state["rtsp"] = "STREAMING"
                binary_chunks.append(data if isinstance(data, bytes) else bytes(data))
                state["frame_count"] += 1

                scan_accumulated()

                if have_keyframe():
                    state["extra_after_idr"] += 1

                if have_keyframe() and state["extra_after_idr"] >= 3 and not state["closing"]:
                    state["closing"] = True
                    logger.info(f"Keyframe captured after {state['frame_count']} packets, extracting...")

                    raw = b''.join(binary_chunks)
                    jpeg = extract_jpeg(raw)
                    result_q.put(jpeg)

                    # TEARDOWN
                    state["cseq"] += 1
                    try:
                        ws_conn.send(
                            f"TEARDOWN {rtsp_url} RTSP/1.0\r\n"
                            f"Session: {state['session_id']}\r\n"
                            f"CSeq: {state['cseq']}\r\n"
                            f"User-Agent: SFRtsp 0.3\r\n"
                            f"Authorization: Basic {basic_auth}\r\n\r\n"
                        )
                    except Exception:
                        pass
                    ws_conn.close()
            return

        # Text message
        msg = data if isinstance(data, str) else data.decode("utf-8", errors="replace")

        # --- WebSocket-level auth ---
        if '"errorCode"' in msg:
            try:
                parsed = json.loads(msg)
            except json.JSONDecodeError:
                return

            if parsed.get("errorCode") == 401 and not state["ws_authed"]:
                auth_str = _build_digest_header(username, password, "NVRDVR", next_nonce, "auth", "GET", ws_auth_uri)
                ws_conn.send(f"Authorization={auth_str}\r\n")
                state["ws_authed"] = True
                return

            if parsed.get("errorCode") == 0:
                state["rtsp"] = "OPTIONS"
                state["cseq"] += 1
                ws_conn.send(
                    f"OPTIONS {rtsp_url} RTSP/1.0\r\n"
                    f"CSeq: {state['cseq']}\r\n"
                    f"User-Agent: SFRtsp 0.3\r\n\r\n"
                )
                return

            logger.error(f"WS auth error: {parsed}")
            ws_conn.close()
            result_q.put(None)
            return

        # --- RTSP responses ---
        if not msg.startswith("RTSP/1.0"):
            if "ANNOUNCE" in msg:
                logger.info("Server ANNOUNCE — playback ended")
                ws_conn.close()
            return

        import re
        status_match = re.match(r"RTSP/1\.0 (\d+)", msg)
        if not status_match:
            return
        status_code = int(status_match.group(1))

        def send_setup():
            state["rtsp"] = "SETUP"
            state["cseq"] += 1
            setup_url = f"{rtsp_url}/video" if not rtsp_url.endswith("/") else f"{rtsp_url}video"
            ws_conn.send(
                f"SETUP {setup_url} RTSP/1.0\r\n"
                f"Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n"
                f"CSeq: {state['cseq']}\r\n"
                f"User-Agent: SFRtsp 0.3\r\n"
                f"Authorization: Basic {basic_auth}\r\n\r\n"
            )

        if state["rtsp"] == "OPTIONS" and status_code == 200:
            state["rtsp"] = "DESCRIBE"
            state["cseq"] += 1
            ws_conn.send(
                f"DESCRIBE {rtsp_url} RTSP/1.0\r\n"
                f"Accept: application/sdp\r\n"
                f"CSeq: {state['cseq']}\r\n"
                f"User-Agent: SFRtsp 0.3\r\n\r\n"
            )

        elif state["rtsp"] == "DESCRIBE":
            if status_code == 401:
                state["rtsp"] = "DESCRIBE_AUTH"
                state["cseq"] += 1
                ws_conn.send(
                    f"DESCRIBE {rtsp_url} RTSP/1.0\r\n"
                    f"Accept: application/sdp\r\n"
                    f"CSeq: {state['cseq']}\r\n"
                    f"User-Agent: SFRtsp 0.3\r\n"
                    f"Authorization: Basic {basic_auth}\r\n\r\n"
                )
            elif status_code == 200:
                send_setup()

        elif state["rtsp"] == "DESCRIBE_AUTH":
            if status_code == 200:
                send_setup()
            else:
                logger.error(f"DESCRIBE failed after auth: {status_code}")
                ws_conn.close()
                result_q.put(None)

        elif state["rtsp"] == "SETUP" and status_code == 200:
            import re as _re
            sess_match = _re.search(r"Session:\s*(\S+)", msg)
            if sess_match:
                state["session_id"] = sess_match.group(1)
                state["rtsp"] = "PLAY"
                state["cseq"] += 1
                ws_conn.send(
                    f"PLAY {rtsp_url} RTSP/1.0\r\n"
                    f"Session: {state['session_id']}\r\n"
                    f"CSeq: {state['cseq']}\r\n"
                    f"User-Agent: SFRtsp 0.3\r\n"
                    f"Authorization: Basic {basic_auth}\r\n\r\n"
                )

    def on_error(ws_conn, error):
        logger.error(f"WS error: {error}")
        if not state["closing"]:
            result_q.put(None)

    def on_close(ws_conn, close_status_code, close_msg):
        if not state["closing"]:
            if state["frame_count"] > 0:
                raw = b''.join(binary_chunks)
                jpeg = extract_jpeg(raw)
                result_q.put(jpeg)
            else:
                result_q.put(None)

    def on_open(ws_conn):
        pass  # Wait for auth challenge from server

    ws = websocket.WebSocketApp(
        ws_url,
        subprotocols=["rtsp"],
        header={
            "Origin": f"http://{nvr_ip}",
            "Cookie": "WebLoginHandle=10081124",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
        on_open=on_open,
        on_data=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    # Run WebSocket in a thread
    ws_thread = threading.Thread(target=lambda: ws.run_forever(), daemon=True)
    ws_thread.start()

    try:
        jpeg = result_q.get(timeout=timeout)
        return jpeg
    except queue.Empty:
        logger.warning(f"WS capture timed out after {timeout}s, state={state['rtsp']}")
        ws.close()
        return None


# --- Public API ---

def capture_playback_frame(nvr_ip: str, username: str, password: str,
                            channel: int, epoch_begin: int, epoch_end: int,
                            timeout: int = 30) -> Optional[bytes]:
    """
    Capture a single JPEG frame from UNIVIEW NVR recorded video.

    Args:
        nvr_ip: NVR IP address
        username: NVR username
        password: NVR password
        channel: NVR channel number
        epoch_begin: Start timestamp (Unix epoch seconds)
        epoch_end: End timestamp (Unix epoch seconds)
        timeout: Hard timeout in seconds (default 30)

    Returns:
        JPEG bytes or None on failure
    """
    result_q = queue.Queue()

    def _do_capture():
        try:
            t0 = time.time()
            login_nonce = _lapi_login(nvr_ip, username, password)
            t1 = time.time()
            next_nonce = _lapi_keepalive(nvr_ip, username, password, login_nonce)
            t2 = time.time()
            rtsp_url = _lapi_record_url(nvr_ip, username, password, next_nonce, channel, epoch_begin, epoch_end)
            t3 = time.time()

            logger.info(f"Playback LAPI: login={int((t1-t0)*1000)}ms keepalive={int((t2-t1)*1000)}ms record={int((t3-t2)*1000)}ms")
            logger.info(f"Playback RTSP URL: {rtsp_url}")

            # WS capture gets its own sub-timeout (leave room for LAPI steps)
            ws_timeout = max(timeout - int(t3 - t0) - 2, 10)
            jpeg = _ws_capture_frame(nvr_ip, username, password, next_nonce, rtsp_url, timeout=ws_timeout)
            result_q.put(jpeg)
        except Exception as e:
            logger.error(f"Playback capture error: {e}")
            result_q.put(None)

    thread = threading.Thread(target=_do_capture, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        logger.warning(f"Playback hard timeout ({timeout}s) for ch{channel}")
        return None

    try:
        return result_q.get_nowait()
    except queue.Empty:
        return None
