import argparse
import json
import os
import re
import subprocess
import threading
import time
from collections import Counter, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
LOCAL_LOG = Path(r"C:\ProgramData\localserver\server-8000.txt")
NGROK_LOG = Path(r"C:\ProgramData\ngrok\ngrok-8000.txt")
TASK_NAMES = ["LocalStaticServer-8000", "Ngrok-8000"]
WATCH_LOCK = threading.Lock()
WATCH_CONDITION = threading.Condition(WATCH_LOCK)
WATCH_STATE = {"seq": 0, "snapshot": None}

ACCESS_RE = re.compile(
    r"^(?P<ip>\S+)\s+-\s+-\s+\[(?P<dt>[^\]]+)\]\s+\"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/\d(?:\.\d)?\"\s+(?P<status>\d{3})\s+"
)

STATUS_EXPLANATIONS = {
    "200": "OK - request succeeded.",
    "201": "Created - resource was created successfully.",
    "204": "No Content - success with no response body.",
    "301": "Moved Permanently - resource URL changed permanently.",
    "302": "Found - temporary redirect.",
    "304": "Not Modified - browser cache is still valid.",
    "400": "Bad Request - malformed request syntax.",
    "401": "Unauthorized - authentication is required.",
    "403": "Forbidden - request understood but blocked.",
    "404": "Not Found - requested path does not exist.",
    "429": "Too Many Requests - rate limited.",
    "500": "Internal Server Error - server-side failure.",
    "502": "Bad Gateway - invalid upstream response.",
    "503": "Service Unavailable - service temporarily unavailable.",
    "504": "Gateway Timeout - upstream timed out.",
}


def run_powershell(command: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def detect_text_encoding(path: Path) -> str:
    with path.open("rb") as f:
        sample = f.read(4096)

    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"

    if not sample:
        return "utf-8"

    null_ratio = sample.count(b"\x00") / len(sample)
    if null_ratio > 0.15:
        even_nulls = sample[0::2].count(0)
        odd_nulls = sample[1::2].count(0)
        if odd_nulls > even_nulls:
          return "utf-16-le"
        if even_nulls > odd_nulls:
            return "utf-16-be"
        return "utf-16-le"

    return "utf-8-sig"


def get_task_status():
    script = r'''
$tasks = "LocalStaticServer-8000","Ngrok-8000"
$result = @()
foreach($name in $tasks){
  try {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction Stop
    $i = $t | Get-ScheduledTaskInfo
    $result += [pscustomobject]@{
      TaskName = $name
      State = [string]$t.State
      LastRunTime = $i.LastRunTime
      LastTaskResult = $i.LastTaskResult
      NextRunTime = $i.NextRunTime
    }
  }
  catch {
    $result += [pscustomobject]@{
      TaskName = $name
      Error = $_.Exception.Message
    }
  }
}
$result | ConvertTo-Json -Depth 4
'''
    proc = run_powershell(script)
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip() or proc.stdout.strip()}

    payload = proc.stdout.strip()
    if not payload:
        return {"ok": True, "tasks": []}

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"Failed to parse task JSON: {payload}"}

    if isinstance(data, dict):
        data = [data]

    return {"ok": True, "tasks": data}


def read_tail(path: Path, lines: int = 80):
    if not path.exists():
        return [f"[missing] {path}"]

    encoding = detect_text_encoding(path)
    with path.open("r", encoding=encoding, errors="replace") as f:
        return list(reversed(list(deque(f, maxlen=lines))))


def sort_ip_records(records, sort_by="requests", order="desc"):
    key_map = {
        "ip": lambda item: item["label"].lower(),
        "requests": lambda item: item["requests"],
        "sites": lambda item: item["sites"],
    }
    sort_key = key_map.get(sort_by, key_map["requests"])
    reverse = order != "asc"
    return sorted(records, key=sort_key, reverse=reverse)


def parse_access_stats(
    path: Path,
    top: int = 20,
    ip_sort: str = "requests",
    ip_order: str = "desc",
    ip_filter: str | None = None,
    site_filter: str | None = None,
    status_filter: str | None = None,
):
    files = Counter()
    statuses = Counter()
    methods = Counter()
    families = Counter()
    ips = Counter()
    ip_sites = {}
    hourly = Counter()
    recent = deque(maxlen=25)
    total = 0

    if not path.exists():
        return {
            "total_requests": 0,
            "unique_files": 0,
            "top_files": [],
            "status_codes": [],
            "top_ips": [],
            "hourly": [],
        }

    encoding = detect_text_encoding(path)
    with path.open("r", encoding=encoding, errors="replace") as f:
        for line in f:
            m = ACCESS_RE.match(line)
            if not m:
                continue

            req_path = m.group("path").split("?", 1)[0] or "/"
            ip = m.group("ip")
            status = m.group("status")

            if ip_filter and ip != ip_filter:
                continue
            if site_filter and req_path != site_filter:
                continue
            if status_filter and status != status_filter:
                continue

            total += 1
            method = m.group("method")
            files[req_path] += 1
            statuses[status] += 1
            methods[method] += 1
            families[f"{status[0]}xx"] += 1
            ips[ip] += 1
            if ip not in ip_sites:
                ip_sites[ip] = set()
            ip_sites[ip].add(req_path)

            recent.appendleft({
                "ip": m.group("ip"),
                "dt": m.group("dt"),
                "method": method,
                "path": req_path,
                "status": status,
            })

            dt_text = m.group("dt")
            hour_match = re.match(r"^(\d{2})/([A-Za-z]{3})/(\d{4})\s+(\d{2})", dt_text)
            if hour_match:
                day, mon, year, hour = hour_match.groups()
                hourly[f"{year}-{mon}-{day} {hour}:00"] += 1

    ip_records = [
        {
            "label": ip,
            "requests": count,
            "sites": len(ip_sites.get(ip, set())),
        }
        for ip, count in ips.items()
    ]
    sorted_ip_records = sort_ip_records(ip_records, sort_by=ip_sort, order=ip_order)[:10]

    return {
        "total_requests": total,
        "unique_files": len(files),
        "top_files": [{"label": k, "value": v} for k, v in files.most_common(top)],
        "methods": [{"label": k, "value": v} for k, v in methods.most_common()],
        "status_codes": [{"label": k, "value": v} for k, v in sorted(statuses.items())],
        "status_families": [{"label": k, "value": v} for k, v in sorted(families.items())],
        "top_ips": [{"label": item["label"], "value": item["requests"]} for item in sorted_ip_records],
        "sites_per_ip": [{"label": item["label"], "value": item["sites"]} for item in sorted_ip_records],
        "ip_stats": sorted_ip_records,
        "ip_sort": ip_sort,
        "ip_order": ip_order,
        "filters": {
            "ip": ip_filter,
            "site": site_filter,
            "status": status_filter,
        },
        "status_explanations": {code: STATUS_EXPLANATIONS.get(code, "Standard HTTP status code.") for code in statuses.keys()},
        "hourly": [{"label": k, "value": v} for k, v in sorted(hourly.items())],
        "recent_requests": list(recent),
    }


def parse_dimensions(path: Path):
    ips = set()
    sites = set()
    statuses = set()

    if not path.exists():
        return {
            "ips": [],
            "sites": [],
            "statuses": [],
            "status_explanations": {},
            "status_explanations_all": STATUS_EXPLANATIONS,
        }

    encoding = detect_text_encoding(path)
    with path.open("r", encoding=encoding, errors="replace") as f:
        for line in f:
            m = ACCESS_RE.match(line)
            if not m:
                continue
            ips.add(m.group("ip"))
            sites.add(m.group("path").split("?", 1)[0] or "/")
            statuses.add(m.group("status"))

    ordered_statuses = sorted(statuses)
    return {
        "ips": sorted(ips),
        "sites": sorted(sites),
        "statuses": ordered_statuses,
        "status_explanations": {code: STATUS_EXPLANATIONS.get(code, "Standard HTTP status code.") for code in ordered_statuses},
        "status_explanations_all": STATUS_EXPLANATIONS,
    }


def run_task_action(action: str):
    if action == "start-all":
        script = "Start-ScheduledTask -TaskName 'LocalStaticServer-8000'; Start-ScheduledTask -TaskName 'Ngrok-8000'"
    elif action == "stop-all":
        script = "Stop-ScheduledTask -TaskName 'Ngrok-8000'; Stop-ScheduledTask -TaskName 'LocalStaticServer-8000'"
    elif action == "restart-all":
        script = (
            "Stop-ScheduledTask -TaskName 'Ngrok-8000'; "
            "Stop-ScheduledTask -TaskName 'LocalStaticServer-8000'; "
            "Start-ScheduledTask -TaskName 'LocalStaticServer-8000'; "
            "Start-ScheduledTask -TaskName 'Ngrok-8000'"
        )
    else:
        return {"ok": False, "error": "Unknown action"}

    proc = run_powershell(script)
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip() or proc.stdout.strip()}

    return {"ok": True}


def get_file_signature(path: Path):
    if not path.exists():
        return {"exists": False}

    stat = path.stat()
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def build_watch_snapshot():
    return {
        "local_log": get_file_signature(LOCAL_LOG),
        "ngrok_log": get_file_signature(NGROK_LOG),
        "task_status": get_task_status(),
    }


def snapshot_signature(snapshot):
    return json.dumps(snapshot, sort_keys=True, default=str)


def watch_for_changes(interval_seconds: int = 2):
    last_signature = None
    while True:
        snapshot = build_watch_snapshot()
        signature = snapshot_signature(snapshot)

        with WATCH_CONDITION:
            if signature != last_signature:
                WATCH_STATE["seq"] += 1
                WATCH_STATE["snapshot"] = snapshot
                WATCH_CONDITION.notify_all()
                last_signature = signature

        time.sleep(interval_seconds)


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _json(self, status: int, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _text(self, status: int, payload: str):
        data = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, rel_path: str):
        file_path = (BASE_DIR / rel_path).resolve()
        if not str(file_path).startswith(str(BASE_DIR)) or not file_path.exists():
            self._text(404, "Not found")
            return

        ext = file_path.suffix.lower()
        if ext == ".html":
            ctype = "text/html; charset=utf-8"
        elif ext == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif ext == ".css":
            ctype = "text/css; charset=utf-8"
        elif ext == ".json":
            ctype = "application/json; charset=utf-8"
        else:
            ctype = "application/octet-stream"

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if file_path.name in ("access-report.html", "index.html", "dashboard.js", "dashboard.css"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route == "/" or route == "/index.html":
            self._serve_file("dashboard/index.html")
            return
        if route == "/dashboard.js":
            self._serve_file("dashboard/dashboard.js")
            return
        if route == "/dashboard.css":
            self._serve_file("dashboard/dashboard.css")
            return
        if route == "/access-report.html":
            self._serve_file("access-report.html")
            return

        if route == "/api/health":
            self._json(200, {"ok": True, "time": datetime.now().isoformat()})
            return

        if route == "/api/status":
            status = get_task_status()
            self._json(200, status)
            return

        if route == "/api/log/local":
            lines = int(query.get("tail", [80])[0])
            payload = "".join(read_tail(LOCAL_LOG, lines=lines))
            self._text(200, payload)
            return

        if route == "/api/log/ngrok":
            lines = int(query.get("tail", [80])[0])
            payload = "".join(read_tail(NGROK_LOG, lines=lines))
            self._text(200, payload)
            return

        if route == "/api/stats":
            top = int(query.get("top", [20])[0])
            ip_sort = query.get("ip_sort", ["requests"])[0].lower()
            ip_order = query.get("ip_order", ["desc"])[0].lower()
            ip_filter = query.get("ip", [None])[0]
            site_filter = query.get("site", [None])[0]
            status_filter = query.get("status", [None])[0]
            self._json(
                200,
                {
                    "ok": True,
                    "stats": parse_access_stats(
                        LOCAL_LOG,
                        top=top,
                        ip_sort=ip_sort,
                        ip_order=ip_order,
                        ip_filter=ip_filter,
                        site_filter=site_filter,
                        status_filter=status_filter,
                    ),
                },
            )
            return

        if route == "/api/dimensions":
            self._json(200, {"ok": True, "dimensions": parse_dimensions(LOCAL_LOG)})
            return

        if "events" in route or route in ("/events", "/api/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            with WATCH_CONDITION:
                seq = WATCH_STATE["seq"]

            try:
                while True:
                    with WATCH_CONDITION:
                        WATCH_CONDITION.wait(timeout=15)
                        current_seq = WATCH_STATE["seq"]
                        snapshot = WATCH_STATE["snapshot"]

                    if current_seq != seq:
                        seq = current_seq
                        payload = {
                            "type": "update",
                            "seq": seq,
                            "time": datetime.now().isoformat(),
                            "snapshot": snapshot,
                        }
                        data = f"event: update\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
                        self.wfile.write(data)
                        self.wfile.flush()
                    else:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        self._text(404, f"Not found: {route}")

    def do_POST(self):
        parsed = urlparse(self.path)
        route = parsed.path

        if route.startswith("/api/tasks/"):
            action = route.replace("/api/tasks/", "", 1)
            result = run_task_action(action)
            self._json(200 if result.get("ok") else 400, result)
            return

        self._text(404, "Not found")


def main():
    parser = argparse.ArgumentParser(description="Local services dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()

    watcher = threading.Thread(target=watch_for_changes, daemon=True)
    watcher.start()

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
