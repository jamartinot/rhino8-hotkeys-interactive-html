import argparse
import base64
import json
import logging
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
LOCAL_LOG = Path(os.environ.get("DASHBOARD_LOCAL_LOG", r"C:\ProgramData\localserver\server-8000.txt"))
NGROK_LOG = Path(os.environ.get("DASHBOARD_NGROK_LOG", r"C:\ProgramData\ngrok\ngrok-8000.txt"))
TASK_NAMES = [
    os.environ.get("DASHBOARD_TASK_NAME_LOCAL", "LocalStaticServer-8000"),
    os.environ.get("DASHBOARD_TASK_NAME_NGROK", "Ngrok-8000"),
]
SERVER_LOG = Path(os.environ.get("DASHBOARD_SERVER_LOG", str(BASE_DIR / "dashboard_server.log")))
AUTH_TOKEN = os.environ.get("DASHBOARD_AUTH_TOKEN") or None
AUTH_USER = os.environ.get("DASHBOARD_AUTH_USER") or None
AUTH_PASSWORD = os.environ.get("DASHBOARD_AUTH_PASSWORD") or None
ALLOWLIST = tuple(filter(None, (item.strip() for item in os.environ.get("DASHBOARD_ALLOWLIST", "").split(","))))
DEBUG_TASK_OUTPUT = os.environ.get("DASHBOARD_DEBUG_TASK_OUTPUT", "").strip().lower() in {"1", "true", "yes", "on"}
TASK_STATUS_TTL_SECONDS = 10.0
STATS_CACHE_TTL_SECONDS = 3.0
MAX_TAIL_LINES = 500
MAX_TOP_RESULTS = 100
WATCH_LOCK = threading.Lock()
WATCH_CONDITION = threading.Condition(WATCH_LOCK)
WATCH_STATE = {"seq": 0, "snapshot": None}
CACHE_LOCK = threading.Lock()
TASK_STATUS_CACHE = {"ts": 0.0, "payload": None}
STATS_CACHE = {}
PARSE_ERROR_LOGGED = {}
LOG = logging.getLogger("dashboard_server")

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

ALLOWED_IP_SORTS = {"ip", "requests", "sites"}
ALLOWED_IP_ORDERS = {"asc", "desc"}
ALLOWED_ACTIONS = {"start-all", "stop-all", "restart-all"}
DENIED_SUFFIXES = {".py", ".ps1", ".psm1", ".bat", ".cmd", ".exe", ".dll", ".sh", ".env", ".log"}


def configure_logging(log_file: Path):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    LOG.handlers.clear()
    LOG.setLevel(logging.INFO)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    stream_handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)
    LOG.addHandler(file_handler)
    LOG.addHandler(stream_handler)


def log_rate_limited(key: str, message: str, level: int = logging.WARNING, interval_seconds: float = 30.0):
    now = time.monotonic()
    with CACHE_LOCK:
        last_logged = PARSE_ERROR_LOGGED.get(key, 0.0)
        if now - last_logged < interval_seconds:
            return
        PARSE_ERROR_LOGGED[key] = now
    LOG.log(level, message)


def clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def normalize_choice(value, allowed, default):
    if value is None:
        return default
    normalized = str(value).strip().lower()
    return normalized if normalized in allowed else default


def parse_basic_auth(header_value: str | None):
    if not header_value or not header_value.startswith("Basic "):
        return None, None
    token = header_value.split(" ", 1)[1].strip()
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None, None
    if ":" not in decoded:
        return None, None
    username, password = decoded.split(":", 1)
    return username, password


def is_authorized(request_handler: BaseHTTPRequestHandler) -> bool:
    if not any([AUTH_TOKEN, AUTH_USER, AUTH_PASSWORD]):
        return True

    remote_ip = request_handler.client_address[0] if request_handler.client_address else None
    if ALLOWLIST and remote_ip not in ALLOWLIST:
        return False

    authorization = request_handler.headers.get("Authorization")
    token_header = request_handler.headers.get("X-Dashboard-Token")
    if AUTH_TOKEN and token_header == AUTH_TOKEN:
        return True

    if AUTH_USER is not None and AUTH_PASSWORD is not None:
        username, password = parse_basic_auth(authorization)
        if username == AUTH_USER and password == AUTH_PASSWORD:
            return True

    return False


def cache_lookup(cache_key: str, ttl_seconds: float):
    with CACHE_LOCK:
        entry = STATS_CACHE.get(cache_key)
        if not entry:
            return None
        if time.monotonic() - entry["ts"] > ttl_seconds:
            STATS_CACHE.pop(cache_key, None)
            return None
        return entry["payload"]


def cache_store(cache_key: str, payload):
    with CACHE_LOCK:
        STATS_CACHE[cache_key] = {"ts": time.monotonic(), "payload": payload}


def parse_requested_int(query, name: str, default: int, minimum: int, maximum: int):
    raw_value = query.get(name, [None])[0]
    if raw_value in (None, ""):
        return default
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Invalid {name}: expected an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"Invalid {name}: must be between {minimum} and {maximum}")
    return parsed


def parse_requested_choice(query, name: str, allowed, default: str):
    raw_value = query.get(name, [None])[0]
    if raw_value in (None, ""):
        return default
    normalized = str(raw_value).strip().lower()
    if normalized not in allowed:
        raise ValueError(f"Invalid {name}: expected one of {', '.join(sorted(allowed))}")
    return normalized


def get_task_names():
    return TASK_NAMES[:2]


def get_watch_state_snapshot():
    with WATCH_CONDITION:
        return WATCH_STATE["seq"], WATCH_STATE["snapshot"]


def set_watch_state_snapshot(snapshot):
    with WATCH_CONDITION:
        WATCH_STATE["seq"] += 1
        WATCH_STATE["snapshot"] = snapshot
        WATCH_CONDITION.notify_all()
        return WATCH_STATE["seq"]


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
    now = time.monotonic()
    with CACHE_LOCK:
        cached = TASK_STATUS_CACHE["payload"]
        if cached is not None and now - TASK_STATUS_CACHE["ts"] < TASK_STATUS_TTL_SECONDS:
            return cached

    task_1, task_2 = get_task_names()
    script = rf'''
$tasks = "{task_1}","{task_2}"
$result = @()
foreach($name in $tasks){{
    try {{
        $t = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $i = $t | Get-ScheduledTaskInfo
        $result += [pscustomobject]@{{
            TaskName = $name
            State = [string]$t.State
            LastRunTime = $i.LastRunTime
            LastTaskResult = $i.LastTaskResult
            NextRunTime = $i.NextRunTime
        }}
    }}
    catch {{
        $result += [pscustomobject]@{{
            TaskName = $name
            Error = $_.Exception.Message
        }}
    }}
}}
$result | ConvertTo-Json -Depth 4
'''
    proc = run_powershell(script)
    if proc.returncode != 0:
        error_text = proc.stderr.strip() or proc.stdout.strip() or "Task status check failed"
        LOG.warning("task status failed: %s", error_text)
        payload = {"ok": False, "error": error_text}
        with CACHE_LOCK:
            TASK_STATUS_CACHE["ts"] = time.monotonic()
            TASK_STATUS_CACHE["payload"] = payload
        return payload

    payload = proc.stdout.strip()
    if not payload:
        return {"ok": True, "tasks": []}

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        error_text = f"Failed to parse task JSON: {payload}"
        log_rate_limited("task-status-json", error_text)
        result = {"ok": False, "error": error_text}
        with CACHE_LOCK:
            TASK_STATUS_CACHE["ts"] = time.monotonic()
            TASK_STATUS_CACHE["payload"] = result
        return result

    if isinstance(data, dict):
        data = [data]

    result = {"ok": True, "tasks": data}
    with CACHE_LOCK:
        TASK_STATUS_CACHE["ts"] = time.monotonic()
        TASK_STATUS_CACHE["payload"] = result
    return result


def read_tail(path: Path, lines: int = 80):
    lines = max(1, min(MAX_TAIL_LINES, lines))
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

    top = clamp_int(top, 20, 1, MAX_TOP_RESULTS)
    ip_sort = normalize_choice(ip_sort, ALLOWED_IP_SORTS, "requests")
    ip_order = normalize_choice(ip_order, ALLOWED_IP_ORDERS, "desc")

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
    try:
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
    except OSError as exc:
        log_rate_limited(f"stats-read-{path}", f"failed to read access log {path}: {exc}")
        return {
            "total_requests": 0,
            "unique_files": 0,
            "top_files": [],
            "status_codes": [],
            "top_ips": [],
            "hourly": [],
        }

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


def get_access_stats_cached(
    path: Path,
    top: int = 20,
    ip_sort: str = "requests",
    ip_order: str = "desc",
    ip_filter: str | None = None,
    site_filter: str | None = None,
    status_filter: str | None = None,
):
    file_state = get_file_signature(path)
    cache_key = json.dumps(
        {
            "path": str(path),
            "file_state": file_state,
            "top": clamp_int(top, 20, 1, MAX_TOP_RESULTS),
            "ip_sort": normalize_choice(ip_sort, ALLOWED_IP_SORTS, "requests"),
            "ip_order": normalize_choice(ip_order, ALLOWED_IP_ORDERS, "desc"),
            "ip_filter": ip_filter,
            "site_filter": site_filter,
            "status_filter": status_filter,
        },
        sort_keys=True,
    )
    cached = cache_lookup(cache_key, STATS_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    payload = parse_access_stats(
        path,
        top=top,
        ip_sort=ip_sort,
        ip_order=ip_order,
        ip_filter=ip_filter,
        site_filter=site_filter,
        status_filter=status_filter,
    )
    cache_store(cache_key, payload)
    return payload


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
    if action not in ALLOWED_ACTIONS:
        return {"ok": False, "error": "Unknown action"}

    task_1, task_2 = get_task_names()
    if action == "start-all":
        script = f"Start-ScheduledTask -TaskName '{task_1}'; Start-ScheduledTask -TaskName '{task_2}'"
    elif action == "stop-all":
        script = f"Stop-ScheduledTask -TaskName '{task_2}'; Stop-ScheduledTask -TaskName '{task_1}'"
    elif action == "restart-all":
        script = (
            f"Stop-ScheduledTask -TaskName '{task_2}'; "
            f"Stop-ScheduledTask -TaskName '{task_1}'; "
            f"Start-ScheduledTask -TaskName '{task_1}'; "
            f"Start-ScheduledTask -TaskName '{task_2}'"
        )
    else:
        return {"ok": False, "error": "Unknown action"}

    proc = run_powershell(script)
    if proc.returncode != 0:
        error_text = proc.stderr.strip() or proc.stdout.strip() or "Task action failed"
        LOG.warning("task action %s failed: %s", action, error_text)
        result = {"ok": False, "error": error_text}
        if DEBUG_TASK_OUTPUT:
            result["stdout"] = proc.stdout.strip()
            result["stderr"] = proc.stderr.strip()
        return result

    result = {"ok": True}
    if DEBUG_TASK_OUTPUT:
        result["stdout"] = proc.stdout.strip()
        result["stderr"] = proc.stderr.strip()
    return result


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

        if signature != last_signature:
            set_watch_state_snapshot(snapshot)
            last_signature = signature

        time.sleep(interval_seconds)


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _json_error(self, status: int, message: str, **extra):
        payload = {"ok": False, "error": message}
        if extra:
            payload.update(extra)
        self._json(status, payload)

    def _json(self, status: int, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _require_auth(self):
        if is_authorized(self):
            return True

        if AUTH_USER is not None and AUTH_PASSWORD is not None and not AUTH_TOKEN:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Dashboard"')
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            body = json.dumps({"ok": False, "error": "Unauthorized"}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False

        self._json_error(403, "Unauthorized")
        return False

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

        if any(part.startswith(".") for part in file_path.relative_to(BASE_DIR).parts):
            self._text(404, "Not found")
            return

        if file_path.suffix.lower() in DENIED_SUFFIXES:
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
        if not self._require_auth():
            return

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
            self._json(200 if status.get("ok") else 503, status)
            return

        if route == "/api/log/local":
            try:
                lines = parse_requested_int(query, "tail", 80, 1, MAX_TAIL_LINES)
            except ValueError as exc:
                LOG.warning("invalid local tail query: %s", exc)
                self._json_error(400, str(exc))
                return
            payload = "".join(read_tail(LOCAL_LOG, lines=lines))
            self._text(200, payload)
            return

        if route == "/api/log/ngrok":
            try:
                lines = parse_requested_int(query, "tail", 80, 1, MAX_TAIL_LINES)
            except ValueError as exc:
                LOG.warning("invalid ngrok tail query: %s", exc)
                self._json_error(400, str(exc))
                return
            payload = "".join(read_tail(NGROK_LOG, lines=lines))
            self._text(200, payload)
            return

        if route == "/api/stats":
            try:
                top = parse_requested_int(query, "top", 20, 1, MAX_TOP_RESULTS)
                ip_sort = parse_requested_choice(query, "ip_sort", ALLOWED_IP_SORTS, "requests")
                ip_order = parse_requested_choice(query, "ip_order", ALLOWED_IP_ORDERS, "desc")
            except ValueError as exc:
                LOG.warning("invalid stats query: %s", exc)
                self._json_error(400, str(exc))
                return
            ip_filter = query.get("ip", [None])[0] or None
            site_filter = query.get("site", [None])[0] or None
            status_filter = query.get("status", [None])[0] or None
            self._json(
                200,
                {
                    "ok": True,
                    "stats": get_access_stats_cached(
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

            seq, _ = get_watch_state_snapshot()

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
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError, ValueError):
                return
            except Exception as exc:
                LOG.warning("sse stream terminated: %s", exc)
                return

        self._text(404, f"Not found: {route}")

    def do_POST(self):
        if not self._require_auth():
            return

        parsed = urlparse(self.path)
        route = parsed.path

        if route.startswith("/api/tasks/"):
            action = route.replace("/api/tasks/", "", 1)
            result = run_task_action(action)
            self._json(200 if result.get("ok") else 400, result)
            return

        self._text(404, "Not found")


def main():
    global LOCAL_LOG, NGROK_LOG, TASK_NAMES, SERVER_LOG, AUTH_TOKEN, AUTH_USER, AUTH_PASSWORD, ALLOWLIST, DEBUG_TASK_OUTPUT

    parser = argparse.ArgumentParser(description="Local services dashboard")
    parser.add_argument("--host", default=os.environ.get("DASHBOARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT", "8091")))
    parser.add_argument("--local-log", default=str(LOCAL_LOG))
    parser.add_argument("--ngrok-log", default=str(NGROK_LOG))
    parser.add_argument("--task-name-local", default=TASK_NAMES[0])
    parser.add_argument("--task-name-ngrok", default=TASK_NAMES[1])
    parser.add_argument("--server-log", default=str(SERVER_LOG))
    parser.add_argument("--auth-token", default=AUTH_TOKEN)
    parser.add_argument("--auth-user", default=AUTH_USER)
    parser.add_argument("--auth-password", default=AUTH_PASSWORD)
    parser.add_argument("--allowlist", default=",".join(ALLOWLIST))
    parser.add_argument("--debug-task-output", action="store_true", default=DEBUG_TASK_OUTPUT)
    args = parser.parse_args()

    LOCAL_LOG = Path(args.local_log)
    NGROK_LOG = Path(args.ngrok_log)
    TASK_NAMES = [args.task_name_local, args.task_name_ngrok]
    SERVER_LOG = Path(args.server_log)
    AUTH_TOKEN = args.auth_token or None
    AUTH_USER = args.auth_user or None
    AUTH_PASSWORD = args.auth_password or None
    ALLOWLIST = tuple(filter(None, (item.strip() for item in (args.allowlist or "").split(","))))
    DEBUG_TASK_OUTPUT = bool(args.debug_task_output)

    configure_logging(SERVER_LOG)
    LOG.info("starting dashboard host=%s port=%s local_log=%s ngrok_log=%s", args.host, args.port, LOCAL_LOG, NGROK_LOG)

    watcher = threading.Thread(target=watch_for_changes, daemon=True)
    watcher.start()

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
