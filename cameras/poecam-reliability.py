#!/usr/bin/env python3
"""
PoE-CAM reliability monitor — probe every N seconds, log results.

Usage:
    python poecam-reliability.py                  # default: 30s interval
    python poecam-reliability.py --interval 60    # 60s interval
    python poecam-reliability.py --ip 192.168.0.7 # different IP
    python poecam-reliability.py --report         # summarize existing log

Author: modeltcamerascat gen-41
"""

import argparse
import json
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
import http.client

LOG_PATH = Path(__file__).parent / "poecam-reliability.jsonl"


def probe(ip: str, timeout: float = 5.0, conn: http.client.HTTPConnection = None) -> dict:
    """Probe the PoE-CAM. Returns result dict.
    If conn is provided, reuses persistent connection (Connection: keep-alive)."""
    ts = time.time()
    dt = datetime.now(timezone.utc).isoformat()

    try:
        if conn is not None:
            conn.request("GET", "/capture")
            resp = conn.getresponse()
            body = resp.read()
            elapsed = round(time.time() - ts, 3)
            return {
                "ts": dt, "ip": ip, "ok": True,
                "status": resp.status, "bytes": len(body),
                "content_type": resp.getheader("Content-Type", ""),
                "elapsed_s": elapsed, "mode": "persistent",
            }
        else:
            req = Request(f"http://{ip}/capture")
            resp = urlopen(req, timeout=timeout)
            body = resp.read()
            elapsed = round(time.time() - ts, 3)
            return {
                "ts": dt, "ip": ip, "ok": True,
                "status": resp.status, "bytes": len(body),
                "content_type": resp.headers.get("Content-Type", ""),
                "elapsed_s": elapsed, "mode": "new-conn",
            }
    except Exception as e:
        elapsed = round(time.time() - ts, 3)
        err = str(e.reason) if hasattr(e, 'reason') else str(e)
        return {
            "ts": dt, "ip": ip, "ok": False,
            "error": err, "elapsed_s": elapsed,
            "mode": "persistent" if conn is not None else "new-conn",
        }


def make_persistent_conn(ip: str, timeout: float = 5.0) -> http.client.HTTPConnection:
    """Create a persistent HTTP connection."""
    conn = http.client.HTTPConnection(ip, timeout=timeout)
    return conn


def report():
    """Summarize the log file."""
    if not LOG_PATH.exists():
        print("No log file yet.")
        return

    all_lines = []
    for line in LOG_PATH.read_text().splitlines():
        if line.strip():
            all_lines.append(json.loads(line))

    if not all_lines:
        print("Log is empty.")
        return

    # Separate probes from stage markers
    entries = [e for e in all_lines if "ok" in e]
    stages = [e for e in all_lines if e.get("type", "").startswith("stage_")]

    if not entries:
        print("No probe data.")
        return

    total = len(entries)
    ok = sum(1 for e in entries if e["ok"])
    fail = total - ok

    print(f"PoE-CAM Reliability Report")
    print(f"{'=' * 50}")
    print(f"Log: {LOG_PATH}")
    print(f"Period: {entries[0]['ts']} to {entries[-1]['ts']}")
    print(f"Probes: {total}  OK: {ok}  FAIL: {fail}  Rate: {ok/total*100:.1f}%")
    print()

    if ok > 0:
        times = [e["elapsed_s"] for e in entries if e["ok"]]
        sizes = [e["bytes"] for e in entries if e["ok"]]
        print(f"Response time: min={min(times):.3f}s  max={max(times):.3f}s  avg={sum(times)/len(times):.3f}s")
        print(f"Frame size: min={min(sizes)}B  max={max(sizes)}B  avg={sum(sizes)//len(sizes)}B")
        print()

    # Per-stage breakdown if stages exist
    stage_names = sorted(set(e.get("stage", "default") for e in entries))
    if len(stage_names) > 1:
        print(f"Per-stage breakdown:")
        print(f"{'-' * 50}")
        for sname in stage_names:
            sdata = [e for e in entries if e.get("stage", "default") == sname]
            sok = sum(1 for e in sdata if e["ok"])
            sfail = len(sdata) - sok
            pct = sok / len(sdata) * 100 if sdata else 0
            stimes = [e["elapsed_s"] for e in sdata if e["ok"]]
            avg_t = f"{sum(stimes)/len(stimes):.3f}s" if stimes else "n/a"
            print(f"  {sname:12s}  {sok}/{len(sdata)} OK ({pct:5.1f}%)  avg={avg_t}")
        print()

    # Show failure streaks
    consecutive = []
    count = 0
    start_ts = None
    for e in entries:
        if not e["ok"]:
            if count == 0:
                start_ts = e["ts"]
            count += 1
        else:
            if count > 0:
                consecutive.append((count, start_ts, e.get("stage", "default")))
            count = 0
    if count > 0:
        consecutive.append((count, start_ts, entries[-1].get("stage", "default")))

    if consecutive:
        print(f"Failure streaks: {len(consecutive)}")
        for cnt, ts, stage in consecutive:
            print(f"  {cnt} consecutive fails at {ts} (stage: {stage})")
    else:
        print("No failures recorded.")


def main():
    parser = argparse.ArgumentParser(description="PoE-CAM reliability monitor")
    parser.add_argument("--ip", default="192.168.0.7", help="Camera IP")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between probes")
    parser.add_argument("--report", action="store_true", help="Show report from existing log")
    parser.add_argument("--ramp", action="store_true", help="Staged ramp-up: 30s -> 1fps -> 2fps -> 3fps")
    parser.add_argument("--start-stage", default=None,
                        help="Skip stages before this name (e.g. '1 fps', '2 fps', '3 fps')")
    parser.add_argument("--persistent", action="store_true", help="Reuse single TCP connection (like camera service mediator)")
    parser.add_argument("--stream", action="store_true",
                        help="Connect once to /stream (MJPEG multipart) and log frames indefinitely")
    parser.add_argument("--stream-path", default="/stream", help="URL path for MJPEG stream (default /stream)")
    args = parser.parse_args()

    if args.report:
        report()
        return

    if args.stream:
        run_stream(args.ip, args.stream_path)
    elif args.ramp:
        run_ramp(args.ip, args.persistent, start_stage=args.start_stage)
    else:
        mode = "persistent" if args.persistent else "new-conn"
        print(f"Probing {args.ip} every {args.interval}s ({mode}). Log: {LOG_PATH}")
        print(f"Ctrl+C to stop, then run with --report to summarize.")
        print()
        run_fixed(args.ip, args.interval, args.persistent)


def run_fixed(ip: str, interval: float, persistent: bool = False):
    """Run at a fixed interval until Ctrl+C."""
    conn = make_persistent_conn(ip) if persistent else None
    with open(LOG_PATH, "a", buffering=1) as f:
        while True:
            result = probe(ip, conn=conn)
            log_result(result, f)

            # Reconnect on failure if persistent
            if persistent and not result["ok"]:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = make_persistent_conn(ip)

            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                print("\nStopped.")
                if conn:
                    conn.close()
                break


def log_result(result: dict, f):
    """Print and write one probe result."""
    status = "OK" if result["ok"] else f"FAIL: {result.get('error', '?')}"
    line = f"{result['ts']}  {result['elapsed_s']:6.3f}s  {status}"
    if result["ok"]:
        line += f"  {result['bytes']}B"
    print(line)
    f.write(json.dumps(result) + "\n")


# Ramp stages: (name, interval_seconds, duration_seconds)
RAMP_STAGES = [
    ("baseline",  30.0,  3600),   # 30s for 1 hour
    ("1 fps",      1.0,  1800),   # 1 fps for 30 min
    ("2 fps",      0.5,  1800),   # 2 fps for 30 min
    ("3 fps",      0.33, 1800),   # 3 fps for 30 min
]


def run_ramp(ip: str, persistent: bool = False, start_stage=None):
    """Staged ramp-up: slow baseline, then increase if stable."""
    mode = "persistent" if persistent else "new-conn"

    stages = RAMP_STAGES
    if start_stage:
        names = [s[0] for s in stages]
        if start_stage not in names:
            print(f"Unknown stage {start_stage!r}. Available: {names}")
            return
        idx = names.index(start_stage)
        stages = stages[idx:]

    print(f"PoE-CAM ramp test — {len(stages)} stages ({mode})")
    print(f"Log: {LOG_PATH}")
    for name, interval, duration in stages:
        rate = f"{1/interval:.1f}/s" if interval < 1 else f"every {interval:.0f}s"
        print(f"  Stage: {name}  ({rate} for {duration//60}min)")

    print(f"\nTotal: ~{sum(d for _, _, d in stages) // 60} min. Ctrl+C to abort.\n")

    conn = make_persistent_conn(ip) if persistent else None

    with open(LOG_PATH, "a", buffering=1) as f:
        for stage_idx, (name, interval, duration) in enumerate(stages):
            rate = f"{1/interval:.1f}/s" if interval < 1 else f"every {interval:.0f}s"
            print(f"\n>>> Stage {stage_idx+1}/{len(stages)}: {name} ({rate})")

            marker = {"ts": datetime.now(timezone.utc).isoformat(), "stage": name,
                       "interval": interval, "type": "stage_start", "mode": mode}
            f.write(json.dumps(marker) + "\n")

            stage_start = time.time()
            ok_count = 0
            fail_count = 0
            consecutive_fails = 0

            while time.time() - stage_start < duration:
                result = probe(ip, conn=conn)
                result["stage"] = name
                log_result(result, f)

                if result["ok"]:
                    ok_count += 1
                    consecutive_fails = 0
                else:
                    fail_count += 1
                    consecutive_fails += 1
                    # Reconnect on failure if persistent
                    if persistent:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = make_persistent_conn(ip)

                if consecutive_fails >= 10:
                    print(f"\n!!! 10 consecutive failures at stage '{name}' — stopping ramp.")
                    marker = {"ts": datetime.now(timezone.utc).isoformat(), "stage": name,
                               "type": "stage_abort", "reason": "10 consecutive failures"}
                    f.write(json.dumps(marker) + "\n")
                    print(f"Run with --report to see results.")
                    if conn:
                        conn.close()
                    return

                try:
                    time.sleep(interval)
                except KeyboardInterrupt:
                    print("\nAborted by user.")
                    if conn:
                        conn.close()
                    return

            total = ok_count + fail_count
            pct = ok_count / total * 100 if total else 0
            print(f"--- Stage '{name}' done: {ok_count}/{total} OK ({pct:.1f}%)")

            marker = {"ts": datetime.now(timezone.utc).isoformat(), "stage": name,
                       "type": "stage_end", "ok": ok_count, "fail": fail_count}
            f.write(json.dumps(marker) + "\n")

    if conn:
        conn.close()
    print(f"\nAll stages complete. Run with --report to summarize.")


def _decode_color_stats(jpeg: bytes) -> dict:
    """Return mean R/G/B, brightness, stddev for a JPEG frame.
    Returns empty dict on decode failure."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        # Downscale for speed — we want stats, not pixel-perfect data.
        img.thumbnail((160, 160))
        import numpy as np
        arr = np.asarray(img, dtype=np.float32)
        r = arr[..., 0]
        g = arr[..., 1]
        b = arr[..., 2]
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return {
            "r": round(float(r.mean()), 1),
            "g": round(float(g.mean()), 1),
            "b": round(float(b.mean()), 1),
            "brightness": round(float(lum.mean()), 1),
            "std": round(float(arr.std()), 1),
        }
    except Exception as e:
        return {"decode_error": f"{type(e).__name__}: {e}"}


def run_stream(ip: str, path: str = "/stream"):
    """
    Connect once to MJPEG multipart stream, log frame stats.
    Tests the hypothesis that a single persistent stream connection survives
    the ~40-request lockup seen with repeated request/response.
    Samples every 30th frame for color stats to detect thermal drift.
    """
    import socket

    url = f"http://{ip}{path}"
    print(f"Stream test — {url}")
    print(f"Log: {LOG_PATH}")
    print(f"Logs every 10 frames + heartbeat every 30s. Ctrl+C to abort.\n")

    with open(LOG_PATH, "a", buffering=1) as f:
        start_ts = time.time()
        start_dt = datetime.now(timezone.utc).isoformat()

        marker = {"ts": start_dt, "type": "stream_start", "ip": ip, "url": url}
        f.write(json.dumps(marker) + "\n")
        print(f">>> Opening stream to {url}")

        try:
            conn = http.client.HTTPConnection(ip, timeout=10)
            conn.request("GET", path)
            resp = conn.getresponse()

            if resp.status != 200:
                marker = {"ts": datetime.now(timezone.utc).isoformat(),
                          "type": "stream_error", "error": f"HTTP {resp.status}", "ip": ip}
                f.write(json.dumps(marker) + "\n")
                print(f"!!! HTTP {resp.status}")
                return

            ctype = resp.getheader("Content-Type", "")
            if "multipart" not in ctype:
                # Not MJPEG — maybe single JPEG. Log and bail.
                body = resp.read()
                marker = {"ts": datetime.now(timezone.utc).isoformat(),
                          "type": "stream_not_mjpeg", "content_type": ctype,
                          "body_size": len(body)}
                f.write(json.dumps(marker) + "\n")
                print(f"!!! Not MJPEG: Content-Type={ctype}, body={len(body)}B")
                return

            # Extract boundary from Content-Type: multipart/x-mixed-replace;boundary=XXX
            boundary = None
            for part in ctype.split(";"):
                part = part.strip()
                if part.lower().startswith("boundary="):
                    boundary = part.split("=", 1)[1].strip().strip('"')
                    break
            if not boundary:
                print(f"!!! No boundary in Content-Type: {ctype}")
                return

            boundary_bytes = b"--" + boundary.encode()
            # Full inter-frame separator (CRLF + boundary + CRLF).
            # M5PoECAM firmware sends a bogus Content-Length, so we can't
            # trust it — scan for the next boundary instead.
            next_sep = b"\r\n" + boundary_bytes
            print(f"Stream open. boundary={boundary!r}, Content-Type={ctype}")

            raw = resp.fp  # buffered reader on the socket

            # Discard everything up to and including the first boundary line.
            buf = b""
            while True:
                chunk = raw.read1(4096)
                if not chunk:
                    print("!!! EOF before first boundary")
                    return
                buf += chunk
                idx = buf.find(boundary_bytes)
                if idx != -1:
                    # Advance past boundary + CRLF terminator
                    end = idx + len(boundary_bytes)
                    # Skip optional CRLF after boundary
                    if buf[end:end+2] == b"\r\n":
                        end += 2
                    buf = buf[end:]
                    break

            frame_count = 0
            byte_count = 0
            last_heartbeat = time.time()
            last_frame_sizes = []
            color_samples = []  # rolling color stats
            COLOR_SAMPLE_EVERY = 30  # sample 1 in N frames (~every 3s at 9 fps)
            COLOR_SAMPLE_WINDOW = 20  # keep last N samples for heartbeat average

            def read_more():
                chunk = raw.read1(8192)
                if not chunk:
                    return False
                return chunk

            while True:
                # Skip part headers (Content-Type, Content-Length, ...) terminated by \r\n\r\n
                while b"\r\n\r\n" not in buf:
                    chunk = read_more()
                    if not chunk:
                        raise ConnectionError("EOF while reading part headers")
                    buf += chunk
                hdr_end = buf.index(b"\r\n\r\n") + 4
                # (headers could be parsed here if needed; we ignore them since CL is bogus)
                buf = buf[hdr_end:]

                # Read JPEG payload until we find the next inter-frame separator.
                # The separator is CRLF + --boundary. Everything before it is the JPEG.
                sep_idx = buf.find(next_sep)
                while sep_idx == -1:
                    chunk = read_more()
                    if not chunk:
                        raise ConnectionError("EOF while reading frame body")
                    buf += chunk
                    sep_idx = buf.find(next_sep)

                jpeg_bytes = buf[:sep_idx]
                # Advance past separator + optional CRLF
                end = sep_idx + len(next_sep)
                if buf[end:end+2] == b"\r\n":
                    end += 2
                buf = buf[end:]

                frame_count += 1
                byte_count += len(jpeg_bytes)
                last_frame_sizes.append(len(jpeg_bytes))
                clen = len(jpeg_bytes)

                # Sample color stats periodically
                if frame_count % COLOR_SAMPLE_EVERY == 0:
                    stats = _decode_color_stats(jpeg_bytes)
                    if "decode_error" not in stats:
                        stats["frame_seq"] = frame_count
                        color_samples.append(stats)
                        if len(color_samples) > COLOR_SAMPLE_WINDOW:
                            color_samples.pop(0)

                # Log every 10th frame
                if frame_count % 10 == 0 or frame_count == 1:
                    elapsed = time.time() - start_ts
                    fps = frame_count / elapsed if elapsed > 0 else 0
                    rec = {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "type": "stream_frame",
                        "frame_seq": frame_count,
                        "bytes": clen,
                        "elapsed_s": round(elapsed, 1),
                        "fps": round(fps, 2),
                    }
                    f.write(json.dumps(rec) + "\n")
                    print(f"[{int(elapsed):5d}s] frame #{frame_count}  {clen}B  {fps:.1f} fps")

                # Heartbeat every 30s regardless of frame rate
                now = time.time()
                if now - last_heartbeat >= 30.0:
                    elapsed = now - start_ts
                    fps = frame_count / elapsed if elapsed > 0 else 0
                    avg_size = sum(last_frame_sizes[-50:]) // min(len(last_frame_sizes), 50)
                    rec = {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "type": "stream_heartbeat",
                        "frame_count": frame_count,
                        "byte_count": byte_count,
                        "elapsed_s": round(elapsed, 1),
                        "fps": round(fps, 2),
                        "avg_recent_size": avg_size,
                    }
                    # Rolling color stats from recent samples
                    if color_samples:
                        n = len(color_samples)
                        rec["color"] = {
                            "samples": n,
                            "r": round(sum(c["r"] for c in color_samples) / n, 1),
                            "g": round(sum(c["g"] for c in color_samples) / n, 1),
                            "b": round(sum(c["b"] for c in color_samples) / n, 1),
                            "brightness": round(sum(c["brightness"] for c in color_samples) / n, 1),
                            "std": round(sum(c["std"] for c in color_samples) / n, 1),
                        }
                    f.write(json.dumps(rec) + "\n")
                    last_heartbeat = now

        except KeyboardInterrupt:
            elapsed = time.time() - start_ts
            marker = {"ts": datetime.now(timezone.utc).isoformat(),
                      "type": "stream_aborted_by_user", "frame_count": frame_count,
                      "elapsed_s": round(elapsed, 1)}
            f.write(json.dumps(marker) + "\n")
            print(f"\nAborted. {frame_count} frames in {elapsed:.0f}s.")

        except (socket.timeout, ConnectionError, http.client.HTTPException, OSError) as e:
            elapsed = time.time() - start_ts
            marker = {"ts": datetime.now(timezone.utc).isoformat(),
                      "type": "stream_closed", "error": f"{type(e).__name__}: {e}",
                      "frame_count": frame_count, "elapsed_s": round(elapsed, 1)}
            f.write(json.dumps(marker) + "\n")
            print(f"\n!!! Stream closed after {frame_count} frames, {elapsed:.0f}s: {e}")

        else:
            # Loop exited via break (EOF, short read, etc.)
            elapsed = time.time() - start_ts
            marker = {"ts": datetime.now(timezone.utc).isoformat(),
                      "type": "stream_ended", "frame_count": frame_count,
                      "byte_count": byte_count, "elapsed_s": round(elapsed, 1)}
            f.write(json.dumps(marker) + "\n")
            print(f"\n!!! Stream ended after {frame_count} frames, {elapsed:.0f}s")


if __name__ == "__main__":
    main()
