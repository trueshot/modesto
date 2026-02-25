"""IP scan CLI — thin wrapper around detection-service /ip-scan endpoints."""
import sys, json, subprocess, os

BASE = "http://127.0.0.1:8002"
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def curl_json(method, path):
    args = ["curl", "-s"]
    if method == "POST":
        args += ["-X", "POST"]
    args.append(f"{BASE}{path}")
    r = subprocess.run(args, capture_output=True, text=True)
    if not r.stdout.strip():
        print("Error: detection service unreachable")
        sys.exit(1)
    return json.loads(r.stdout)


def cmd_start(ip):
    d = curl_json("POST", f"/ip-scan/{ip}/start")
    if d.get("error"):
        print(f"Error: {d['error']}")
        sys.exit(1)
    if d.get("status") == "already_running":
        print(d.get("log_path", "already running"))
    else:
        print(d.get("log_path", "started"))


def cmd_stop(ip):
    d = curl_json("POST", f"/ip-scan/{ip}/stop")
    if d.get("error"):
        print(f"Error: {d['error']}")
        sys.exit(1)
    print(f"{ip} stopped")


def cmd_stopall():
    d = curl_json("GET", "/ip-scan/status")
    ips = [k for k, v in d.items() if k != "_detector" and v.get("running")]
    if not ips:
        print("No active scans.")
        return
    for ip in ips:
        curl_json("POST", f"/ip-scan/{ip}/stop")
        print(f"  {ip} stopped")
    print(f"Stopped {len(ips)} scan(s).")


def cmd_status():
    d = curl_json("GET", "/ip-scan/status")
    det = d.pop("_detector", {})
    scans = {k: v for k, v in d.items()}
    if not scans:
        print("  No scans.")
    else:
        for ip in sorted(scans):
            v = scans[ip]
            state = "RUNNING" if v["running"] else "stopped"
            print(f"  {ip:16s} {state:8s} {v['frames']}f  {v.get('fps', 0)}fps  {v['unique_tags']}tags  {v.get('elapsed_s', 0)}s")
    if det:
        print(f"  Detector: {det.get('utilization_pct', 0)}%")


def cmd_tail(ip):
    # Find latest log file for this IP
    if not os.path.isdir(LOG_DIR):
        print(f"No logs directory")
        sys.exit(1)
    files = sorted(
        [f for f in os.listdir(LOG_DIR) if f.startswith(f"ipscan_{ip}_") and f.endswith(".jsonl")],
        reverse=True
    )
    if not files:
        print(f"No log files for {ip}")
        sys.exit(1)
    path = os.path.join(LOG_DIR, files[0])
    print(f"Tailing {path}  (Ctrl+C to stop)")
    os.system(f'tail -f "{path}"')


def usage():
    print()
    print("  ip-scan start <ip>    Start scanning camera, returns JSONL path")
    print("  ip-scan stop <ip>     Stop scanning camera")
    print("  ip-scan stop-all      Stop all active scans")
    print("  ip-scan status        Show active scans + detector utilization")
    print("  ip-scan list          Alias for status")
    print("  ip-scan tail <ip>     Tail the JSONL log for an IP")
    print()


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else None
    ip = args[1] if len(args) > 1 else None

    if cmd in ("start",) and not ip:
        print("Error: ip required"); sys.exit(1)
    if cmd in ("stop",) and not ip:
        print("Error: ip required"); sys.exit(1)
    if cmd in ("tail",) and not ip:
        print("Error: ip required"); sys.exit(1)

    if cmd == "start":      cmd_start(ip)
    elif cmd == "stop":     cmd_stop(ip)
    elif cmd == "stop-all": cmd_stopall()
    elif cmd == "status":   cmd_status()
    elif cmd == "list":     cmd_status()
    elif cmd == "tail":     cmd_tail(ip)
    else:                   usage()
