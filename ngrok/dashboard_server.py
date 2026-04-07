import argparse
import json
import os
import re
import subprocess
from collections import Counter, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
LOCAL_LOG = Path(r"C:\ProgramData\localserver\server-8000.txt")
NGROK_LOG = Path(r"C:\ProgramData\ngrok\ngrok-8000.txt")
TASK_NAMES = ["LocalStaticServer-8000", "Ngrok-8000"]

ACCESS_RE = re.compile(
    r"^(?P<ip>\S+)\s+-\s+-\s+\[(?P<dt>[^\]]+)\]\s+\"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/\d(?:\.\d)?\"\s+(?P<status>\d{3})\s+"
)


def run_powershell(command: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


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

    with path.open("r", encoding="utf-8", errors="replace") as f:
        return list(deque(f, maxlen=lines))


def parse_access_stats(path: Path, top: int = 20):
    files = Counter()
    statuses = Counter()
    ips = Counter()
    hourly = Counter()
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

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = ACCESS_RE.match(line)
            if not m:
                continue

            total += 1
            req_path = m.group("path").split("?", 1)[0] or "/"
            files[req_path] += 1
            statuses[m.group("status")] += 1
            ips[m.group("ip")] += 1

            dt_text = m.group("dt")
            hour_match = re.match(r"^(\d{2})/([A-Za-z]{3})/(\d{4})\s+(\d{2})", dt_text)
            if hour_match:
                day, mon, year, hour = hour_match.groups()
                hourly[f"{year}-{mon}-{day} {hour}:00"] += 1

    return {
        "total_requests": total,
        "unique_files": len(files),
        "top_files": [{"label": k, "value": v} for k, v in files.most_common(top)],
        "status_codes": [{"label": k, "value": v} for k, v in sorted(statuses.items())],
        "top_ips": [{"label": k, "value": v} for k, v in ips.most_common(10)],
        "hourly": [{"label": k, "value": v} for k, v in sorted(hourly.items())],
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


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _json(self, status: int, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _text(self, status: int, payload: str):
        data = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
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
            self._json(200, {"ok": True, "stats": parse_access_stats(LOCAL_LOG, top=top)})
            return

        self._text(404, "Not found")

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
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
