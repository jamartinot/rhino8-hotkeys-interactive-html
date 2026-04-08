import argparse
import base64
import math
import ipaddress
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urlparse, urlsplit
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
LOCAL_LOG = Path(os.environ.get("DASHBOARD_LOCAL_LOG", r"C:\ProgramData\localserver\server-8000.txt"))
NGROK_LOG = Path(os.environ.get("DASHBOARD_NGROK_LOG", r"C:\ProgramData\ngrok\ngrok-8000.txt"))
ATTACK_REPORT = Path(os.environ.get("DASHBOARD_ATTACK_REPORT", str(BASE_DIR / "attack-simulation-report.json")))
ATTACK_SCAN_SCRIPT = Path(os.environ.get("DASHBOARD_ATTACK_SCAN_SCRIPT", str(BASE_DIR / "security_attack_simulator.py")))
ATTACK_DEFAULT_TARGET = os.environ.get("DASHBOARD_ATTACK_TARGET", "").strip()
DEFAULT_PUBLIC_STATUS_TARGET = "https://extraterritorial-carlota-ironfisted.ngrok-free.dev/Rhino8_cheat_sheet_timestamps_interactive.html"
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
RATE_LIMIT_ENABLED = os.environ.get("DASHBOARD_RATE_LIMIT_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_REQUESTS_PER_WINDOW = 120
RATE_LIMIT_BAN_SECONDS = 300
BLOCK_BOT_USER_AGENTS = os.environ.get("DASHBOARD_BLOCK_BOT_UA", "1").strip().lower() in {"1", "true", "yes", "on"}
WATCH_LOCK = threading.Lock()
WATCH_CONDITION = threading.Condition(WATCH_LOCK)
WATCH_STATE = {"seq": 0, "snapshot": None}
CACHE_LOCK = threading.Lock()
TASK_STATUS_CACHE = {"ts": 0.0, "payload": None}
STATS_CACHE = {}
PARSE_ERROR_LOGGED = {}
REQUEST_GUARD_LOCK = threading.Lock()
REQUEST_GUARD = {}
MANUAL_IP_RULES_LOCK = threading.Lock()
MANUAL_IP_RULES = {}
ATTACK_SCAN_LOCK = threading.Lock()
ATTACK_SCAN_STATE = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_exit_code": None,
    "last_error": None,
    "last_target": None,
    "last_profile": None,
    "last_output": None,
}
PUBLIC_CONNECTION_TEST_PATHS = (
    ("Rhino8_cheat_sheet_timestamps_interactive.html", "/Rhino8_cheat_sheet_timestamps_interactive.html"),
    ("CAI_ Collision Awareness Indicator.html", "/cai/CAI_ Collision Awareness Indicator.html"),
    ("Rhino 8 Interactive Cheat Sheet Manual.pdf", "/Rhino 8 Interactive Cheat Sheet Manual.pdf"),
)
GEOIP_DB_PATH = Path(os.environ.get("DASHBOARD_GEOIP_DB", str(BASE_DIR / "geoip-local.json")))
GEOIP_CACHE = {"mtime": None, "rules": []}
ALERT_PROBE_RATE_THRESHOLD = float(os.environ.get("DASHBOARD_ALERT_PROBE_RATE", "35"))
ALERT_4XX_THRESHOLD = int(os.environ.get("DASHBOARD_ALERT_4XX", "250"))
ALERT_5XX_THRESHOLD = int(os.environ.get("DASHBOARD_ALERT_5XX", "20"))
ALERT_RATE_LIMIT_THRESHOLD = int(os.environ.get("DASHBOARD_ALERT_429", "20"))
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref",
    "ref_src",
    "source",
}
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
BOT_UA_PATTERNS = (
    "bot",
    "crawler",
    "spider",
    "scraper",
    "python-requests",
    "wget",
    "go-http-client",
    "httpclient",
)
BOT_PATH_PATTERNS = (
    "/api/",
    "/.git",
    "/.env",
    "/wp-admin",
    "/wp-login",
    "/phpmyadmin",
    "/cgi-bin",
    "/server-status",
    "/actuator",
    "/vendor",
    "/admin",
)


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


def parse_requested_csv(query, name: str):
    raw_value = query.get(name, [None])[0]
    if raw_value in (None, ""):
        return None
    values = [part.strip() for part in str(raw_value).split(",") if part.strip()]
    return values or None


def normalize_request_path(raw_path: str | None) -> str:
    if not raw_path:
        return "/"

    parts = urlsplit(raw_path)
    decoded_path = unquote(parts.path or "/")
    if not decoded_path.startswith("/"):
        decoded_path = f"/{decoded_path}"
    decoded_path = re.sub(r"/{2,}", "/", decoded_path)
    if len(decoded_path) > 1 and decoded_path.endswith("/"):
        decoded_path = decoded_path[:-1]

    kept_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.strip().lower()
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS:
            continue
        kept_query.append((key, value))

    if kept_query:
        return f"{decoded_path}?{urlencode(kept_query, doseq=True)}"
    return decoded_path


def normalize_site_value(path_value: str | None) -> str | None:
    if not path_value:
        return None
    normalized = normalize_request_path(path_value)
    return normalized.split("?", 1)[0] or "/"


def parse_status_family(status: str) -> str:
    if not status or len(status) < 1:
        return ""
    return f"{status[0]}xx"


def load_geoip_rules():
    try:
        stat = GEOIP_DB_PATH.stat()
    except OSError:
        with CACHE_LOCK:
            GEOIP_CACHE["mtime"] = None
            GEOIP_CACHE["rules"] = []
        return []

    with CACHE_LOCK:
        cached_mtime = GEOIP_CACHE.get("mtime")
        if cached_mtime == stat.st_mtime:
            return GEOIP_CACHE.get("rules", [])

    rules = []
    try:
        payload = json.loads(GEOIP_DB_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                cidr = item.get("cidr")
                if not cidr:
                    continue
                try:
                    network = ipaddress.ip_network(cidr, strict=False)
                except ValueError:
                    continue
                rules.append(
                    {
                        "network": network,
                        "country": str(item.get("country") or "--"),
                        "city": str(item.get("city") or "--"),
                    }
                )
    except (OSError, ValueError, TypeError) as exc:
        log_rate_limited("geoip-load", f"failed to load geoip db {GEOIP_DB_PATH}: {exc}")
        rules = []

    with CACHE_LOCK:
        GEOIP_CACHE["mtime"] = stat.st_mtime
        GEOIP_CACHE["rules"] = rules
    return rules


def resolve_geo(ip_text: str):
    if is_loopback_ip(ip_text):
        return {"country": "LOCAL", "city": "Loopback", "source": "builtin"}

    try:
        ip_obj = ipaddress.ip_address(ip_text)
    except ValueError:
        return {"country": "--", "city": "--", "source": "invalid"}

    if ip_obj.is_private:
        return {"country": "PRIVATE", "city": "RFC1918", "source": "builtin"}

    for item in load_geoip_rules():
        if ip_obj in item["network"]:
            return {"country": item["country"], "city": item["city"], "source": "local-db"}

    return {"country": "--", "city": "--", "source": "unmapped"}


def evaluate_alerts(stats):
    security = stats.get("security", {})
    families = {item.get("label"): int(item.get("value", 0)) for item in stats.get("status_families", [])}
    total = int(stats.get("total_requests") or 0)
    suspicious = int(security.get("suspicious_requests") or 0)
    probe_rate = (suspicious / total * 100.0) if total > 0 else 0.0
    alerts = []

    if probe_rate >= ALERT_PROBE_RATE_THRESHOLD:
        alerts.append(
            {
                "level": "critical",
                "code": "probe-rate",
                "message": f"Probe rate is {probe_rate:.1f}% ({suspicious}/{total})",
            }
        )

    if int(families.get("4xx", 0)) >= ALERT_4XX_THRESHOLD:
        alerts.append(
            {
                "level": "warning",
                "code": "high-4xx",
                "message": f"4xx responses reached {int(families.get('4xx', 0))}",
            }
        )

    if int(families.get("5xx", 0)) >= ALERT_5XX_THRESHOLD:
        alerts.append(
            {
                "level": "critical",
                "code": "high-5xx",
                "message": f"5xx responses reached {int(families.get('5xx', 0))}",
            }
        )

    rate_limited = int(security.get("rate_limited_requests") or 0)
    if rate_limited >= ALERT_RATE_LIMIT_THRESHOLD:
        alerts.append(
            {
                "level": "warning",
                "code": "high-429",
                "message": f"Rate-limited responses reached {rate_limited}",
            }
        )

    return alerts


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


def is_loopback_ip(ip: str | None) -> bool:
    if not ip:
        return False
    normalized = ip.strip().lower()
    return normalized in {"127.0.0.1", "::1", "localhost"}


def extract_client_ip(request_handler: BaseHTTPRequestHandler) -> str:
    forwarded = request_handler.headers.get("X-Forwarded-For") or ""
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    real_ip = request_handler.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request_handler.client_address and request_handler.client_address[0]:
        return request_handler.client_address[0]
    return "unknown"


def is_blocked_user_agent(user_agent: str | None) -> bool:
    if not user_agent:
        return False
    ua = user_agent.lower()
    return any(pattern in ua for pattern in BOT_UA_PATTERNS)


def check_request_guard(client_ip: str, user_agent: str | None):
    if is_loopback_ip(client_ip):
        return True, 200, "ok"

    now = time.monotonic()
    with MANUAL_IP_RULES_LOCK:
        rule = MANUAL_IP_RULES.get(client_ip)
        if rule and rule.get("until", 0.0) <= now:
            MANUAL_IP_RULES.pop(client_ip, None)
            rule = None

    if rule and rule.get("action") == "block":
        return False, 403, "IP blocked by dashboard rule"

    if rule and rule.get("action") == "rate-limit":
        custom_window = clamp_int(rule.get("window"), RATE_LIMIT_WINDOW_SECONDS, 10, 3600)
        custom_limit = clamp_int(rule.get("requests"), RATE_LIMIT_REQUESTS_PER_WINDOW, 1, 5000)
        custom_ban = clamp_int(rule.get("ban"), RATE_LIMIT_BAN_SECONDS, 10, 86400)
        ok, status, message = _apply_rate_limit(client_ip, custom_window, custom_limit, custom_ban)
        if not ok:
            return False, status, message
        return True, 200, "ok"

    if BLOCK_BOT_USER_AGENTS and is_blocked_user_agent(user_agent):
        return False, 403, "Blocked bot user-agent"

    if not RATE_LIMIT_ENABLED:
        return True, 200, "ok"

    ok, status, message = _apply_rate_limit(
        client_ip,
        RATE_LIMIT_WINDOW_SECONDS,
        RATE_LIMIT_REQUESTS_PER_WINDOW,
        RATE_LIMIT_BAN_SECONDS,
    )
    if not ok:
        return False, status, message

    return True, 200, "ok"


def _apply_rate_limit(client_ip: str, window_seconds: int, max_requests: int, ban_seconds: int):
    now = time.monotonic()
    with REQUEST_GUARD_LOCK:
        bucket = REQUEST_GUARD.get(client_ip)
        if not bucket:
            bucket = {"hits": deque(), "ban_until": 0.0}
            REQUEST_GUARD[client_ip] = bucket

        if bucket["ban_until"] > now:
            return False, 429, "Too many requests"

        hits = bucket["hits"]
        while hits and now - hits[0] > window_seconds:
            hits.popleft()

        hits.append(now)
        if len(hits) > max_requests:
            bucket["ban_until"] = now + ban_seconds
            log_rate_limited(
                f"rate-limit-{client_ip}",
                f"rate limit triggered for {client_ip}; requests={len(hits)} in {window_seconds}s",
                level=logging.WARNING,
                interval_seconds=15.0,
            )
            return False, 429, "Too many requests"
    return True, 200, "ok"


def set_ip_rule(action: str, ip_text: str, seconds: int = 3600, requests: int | None = None, window: int | None = None, ban: int | None = None):
    try:
        ipaddress.ip_address(ip_text)
    except ValueError:
        return {"ok": False, "error": "Invalid IP address"}

    expires = time.monotonic() + clamp_int(seconds, 3600, 30, 604800)
    rule = {"action": action, "until": expires}
    if action == "rate-limit":
        rule["requests"] = clamp_int(requests, 20, 1, 5000)
        rule["window"] = clamp_int(window, 60, 10, 3600)
        rule["ban"] = clamp_int(ban, 300, 10, 86400)

    with MANUAL_IP_RULES_LOCK:
        MANUAL_IP_RULES[ip_text] = rule

    return {
        "ok": True,
        "rule": {
            "ip": ip_text,
            "action": action,
            "seconds": int(expires - time.monotonic()),
            "requests": rule.get("requests"),
            "window": rule.get("window"),
            "ban": rule.get("ban"),
        },
    }


def clear_ip_rule(ip_text: str):
    with MANUAL_IP_RULES_LOCK:
        existed = MANUAL_IP_RULES.pop(ip_text, None)
    return {"ok": True, "removed": bool(existed), "ip": ip_text}


def list_ip_rules():
    now = time.monotonic()
    rows = []
    with MANUAL_IP_RULES_LOCK:
        expired = [ip for ip, rule in MANUAL_IP_RULES.items() if rule.get("until", 0.0) <= now]
        for ip in expired:
            MANUAL_IP_RULES.pop(ip, None)
        for ip, rule in MANUAL_IP_RULES.items():
            rows.append(
                {
                    "ip": ip,
                    "action": rule.get("action"),
                    "seconds_left": max(0, int(rule.get("until", 0.0) - now)),
                    "requests": rule.get("requests"),
                    "window": rule.get("window"),
                    "ban": rule.get("ban"),
                }
            )
    rows.sort(key=lambda item: (item.get("action") or "", item.get("ip") or ""))
    return rows


def is_bot_probe_path(req_path: str) -> bool:
    path = (req_path or "").lower()
    if path.startswith("/.") or ".." in path:
        return True
    return any(path.startswith(pattern) for pattern in BOT_PATH_PATTERNS)


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
        return "utf-16"

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
    methods_filter: list[str] | None = None,
    status_family_filter: str | None = None,
    text_filter: str | None = None,
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
    denied_requests = 0
    forbidden_requests = 0
    unauthorized_requests = 0
    not_found_requests = 0
    rate_limited_requests = 0
    server_error_requests = 0
    bot_probe_requests = 0
    api_probe_requests = 0
    suspicious_requests = 0
    bot_probe_ips = Counter()
    api_probe_ips = Counter()

    top = clamp_int(top, 20, 1, MAX_TOP_RESULTS)
    ip_sort = normalize_choice(ip_sort, ALLOWED_IP_SORTS, "requests")
    ip_order = normalize_choice(ip_order, ALLOWED_IP_ORDERS, "desc")
    site_filter = normalize_site_value(site_filter)
    status_family_filter = normalize_choice(status_family_filter, {"2xx", "3xx", "4xx", "5xx"}, "") or None
    methods_filter_set = {method.upper() for method in (methods_filter or []) if method}
    text_filter_norm = (text_filter or "").strip().lower() or None

    if not path.exists():
        return {
            "total_requests": 0,
            "unique_files": 0,
            "top_files": [],
            "status_codes": [],
            "top_ips": [],
            "security": {
                "denied_requests": 0,
                "forbidden_requests": 0,
                "unauthorized_requests": 0,
                "not_found_requests": 0,
                "rate_limited_requests": 0,
                "server_error_requests": 0,
                "bot_probe_requests": 0,
                "api_probe_requests": 0,
                "suspicious_requests": 0,
                "unique_probe_ips": 0,
                "top_probe_ips": [],
                "top_api_probe_ips": [],
            },
            "hourly": [],
        }

    encoding = detect_text_encoding(path)
    try:
        with path.open("r", encoding=encoding, errors="replace") as f:
            for line in f:
                m = ACCESS_RE.match(line)
                if not m:
                    continue

                normalized_path = normalize_request_path(m.group("path"))
                req_path = normalized_path.split("?", 1)[0] or "/"
                ip = m.group("ip")
                status = m.group("status")
                method = m.group("method")
                family = parse_status_family(status)

                if ip_filter and ip != ip_filter:
                    continue
                if site_filter and req_path != site_filter:
                    continue
                if status_filter and status != status_filter:
                    continue
                if methods_filter_set and method.upper() not in methods_filter_set:
                    continue
                if status_family_filter and family != status_family_filter:
                    continue
                if text_filter_norm:
                    haystack = f"{ip} {method} {status} {req_path}".lower()
                    if text_filter_norm not in haystack:
                        continue

                total += 1
                files[req_path] += 1
                statuses[status] += 1
                methods[method] += 1

                if status == "401":
                    unauthorized_requests += 1
                if status == "403":
                    forbidden_requests += 1
                if status == "404":
                    not_found_requests += 1
                if status == "429":
                    rate_limited_requests += 1
                if status.startswith("5"):
                    server_error_requests += 1

                if status in {"401", "403"}:
                    denied_requests += 1

                is_api_probe = req_path.lower().startswith("/api/")
                if is_api_probe:
                    api_probe_requests += 1
                    api_probe_ips[ip] += 1

                is_bot_probe = is_bot_probe_path(req_path)
                if is_bot_probe:
                    bot_probe_requests += 1
                    bot_probe_ips[ip] += 1

                if is_api_probe or is_bot_probe:
                    suspicious_requests += 1

                families[family] += 1
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
                    "geo": resolve_geo(ip),
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
            "security": {
                "denied_requests": 0,
                "forbidden_requests": 0,
                "unauthorized_requests": 0,
                "not_found_requests": 0,
                "rate_limited_requests": 0,
                "server_error_requests": 0,
                "bot_probe_requests": 0,
                "api_probe_requests": 0,
                "suspicious_requests": 0,
                "unique_probe_ips": 0,
                "top_probe_ips": [],
                "top_api_probe_ips": [],
            },
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
            "methods": sorted(methods_filter_set),
            "status_family": status_family_filter,
            "q": text_filter_norm,
        },
        "status_explanations": {code: STATUS_EXPLANATIONS.get(code, "Standard HTTP status code.") for code in statuses.keys()},
        "security": {
            "denied_requests": denied_requests,
            "forbidden_requests": forbidden_requests,
            "unauthorized_requests": unauthorized_requests,
            "not_found_requests": not_found_requests,
            "rate_limited_requests": rate_limited_requests,
            "server_error_requests": server_error_requests,
            "bot_probe_requests": bot_probe_requests,
            "api_probe_requests": api_probe_requests,
            "suspicious_requests": suspicious_requests,
            "unique_probe_ips": len(set(bot_probe_ips.keys()) | set(api_probe_ips.keys())),
            "top_probe_ips": [{"label": ip, "value": count} for ip, count in bot_probe_ips.most_common(10)],
            "top_api_probe_ips": [{"label": ip, "value": count} for ip, count in api_probe_ips.most_common(5)],
        },
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
    methods_filter: list[str] | None = None,
    status_family_filter: str | None = None,
    text_filter: str | None = None,
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
            "methods_filter": sorted({item.upper() for item in (methods_filter or []) if item}),
            "status_family_filter": status_family_filter,
            "text_filter": (text_filter or "").strip().lower() or None,
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
        methods_filter=methods_filter,
        status_family_filter=status_family_filter,
        text_filter=text_filter,
    )
    cache_store(cache_key, payload)
    return payload


def parse_dimensions(path: Path):
    ips = set()
    sites = set()
    statuses = set()
    methods = set()

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
            sites.add(normalize_request_path(m.group("path")).split("?", 1)[0] or "/")
            statuses.add(m.group("status"))
            methods.add(m.group("method").upper())

    ordered_statuses = sorted(statuses)
    return {
        "ips": sorted(ips),
        "sites": sorted(sites),
        "statuses": ordered_statuses,
        "methods": sorted(methods),
        "status_families": ["2xx", "3xx", "4xx", "5xx"],
        "status_explanations": {code: STATUS_EXPLANATIONS.get(code, "Standard HTTP status code.") for code in ordered_statuses},
        "status_explanations_all": STATUS_EXPLANATIONS,
    }


def parse_log_rows(path: Path, lines: int = 120, text_filter: str | None = None):
    text_filter_norm = (text_filter or "").strip().lower() or None
    output = []
    for raw in read_tail(path, lines=lines):
        line = raw.rstrip("\r\n")
        if not line:
            continue

        match = ACCESS_RE.match(line)
        if match:
            ip = match.group("ip")
            method = match.group("method")
            status = match.group("status")
            normalized_path = normalize_request_path(match.group("path"))
            row = {
                "dt": match.group("dt"),
                "ip": ip,
                "method": method,
                "path": normalized_path,
                "status": status,
                "status_family": parse_status_family(status),
                "geo": resolve_geo(ip),
                "raw": line,
            }
        else:
            row = {
                "dt": "",
                "ip": "",
                "method": "",
                "path": "",
                "status": "",
                "status_family": "",
                "geo": {"country": "--", "city": "--", "source": "unparsed"},
                "raw": line,
            }

        if text_filter_norm:
            haystack = f"{row.get('dt','')} {row.get('ip','')} {row.get('method','')} {row.get('path','')} {row.get('status','')} {row.get('raw','')}".lower()
            if text_filter_norm not in haystack:
                continue
        output.append(row)
    return output


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
    stats = get_access_stats_cached(LOCAL_LOG, top=10)
    return {
        "local_log": get_file_signature(LOCAL_LOG),
        "ngrok_log": get_file_signature(NGROK_LOG),
        "attack_report": get_file_signature(ATTACK_REPORT),
        "attack_scan": get_attack_scan_state(),
        "task_status": get_task_status(),
        "alerts": evaluate_alerts(stats),
    }


def load_attack_report(path: Path):
    if not path.exists():
        return {"ok": False, "available": False, "error": "Attack report not found"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "available": False, "error": f"Failed to read attack report: {exc}"}

    summary = payload.get("summary") if isinstance(payload, dict) else None
    discovery = payload.get("discovery") if isinstance(payload, dict) else None
    if isinstance(discovery, dict):
        discovery = dict(discovery)
        discovery["tree_nodes"] = build_tree_nodes(discovery.get("file_tree_lines"), str(payload.get("target") or ATTACK_DEFAULT_TARGET or ""))
    return {
        "ok": True,
        "available": True,
        "path": str(path),
        "summary": summary or {},
        "discovery": discovery or {},
        "target": payload.get("target") if isinstance(payload, dict) else None,
        "profile": payload.get("profile") if isinstance(payload, dict) else None,
        "started_at_epoch": payload.get("started_at_epoch") if isinstance(payload, dict) else None,
    }


def build_public_target_url(target: str, path: str):
    parsed = urlsplit((target or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    normalized_path = "/" + "/".join(quote(part, safe="") for part in path.lstrip("/").split("/"))
    return f"{parsed.scheme}://{parsed.netloc}{normalized_path}"


def build_tree_nodes(file_tree_lines, target: str):
    lines = [str(line or "") for line in (file_tree_lines or []) if str(line or "").strip()]
    if not lines:
        return []

    nodes = []
    path_stack = []
    parsed_lines = []
    for raw_line in lines:
        if raw_line.strip() == "/":
            parsed_lines.append({"line": raw_line, "depth": 0, "name": "/", "path": "/", "is_root": True})
            continue

        match = re.match(r"^((?:│   |    )*)([├└])──\s*(.*)$", raw_line)
        if not match:
            continue
        indent = match.group(1) or ""
        depth = len(indent) // 4
        name = (match.group(3) or "").strip()
        if not name:
            continue

        while len(path_stack) > depth:
            path_stack.pop()
        path_stack = path_stack[:depth]
        path_stack.append(name)
        path = "/" + "/".join(path_stack)
        parsed_lines.append({"line": raw_line, "depth": depth, "name": name, "path": path, "is_root": False})

    for index, item in enumerate(parsed_lines):
        if item.get("is_root"):
            nodes.append({**item, "is_folder": False, "url": None})
            continue

        next_item = parsed_lines[index + 1] if index + 1 < len(parsed_lines) else None
        is_folder = bool(next_item and next_item.get("depth", -1) > item["depth"])
        path = item["path"] + ("/" if is_folder and not item["path"].endswith("/") else "")
        url = build_public_target_url(target, path)
        nodes.append({**item, "is_folder": is_folder, "path": path, "url": url})

    return nodes


def test_public_connection(target: str | None = None, timeout_seconds: float = 5.0):
    candidate_target = (target or ATTACK_DEFAULT_TARGET or "").strip()
    if not candidate_target:
        report = load_attack_report(ATTACK_REPORT)
        candidate_target = str(report.get("target") or "").strip()
    if not candidate_target:
        candidate_target = DEFAULT_PUBLIC_STATUS_TARGET

    if not candidate_target:
        return {"ok": False, "connected": False, "error": "public target is not configured", "checks": []}

    parsed = urlsplit(candidate_target)
    if not parsed.scheme or not parsed.netloc:
        return {"ok": False, "connected": False, "error": "public target is invalid", "checks": []}

    checks = []
    connected_count = 0
    for label, path in PUBLIC_CONNECTION_TEST_PATHS:
        url = build_public_target_url(candidate_target, path)
        if not url:
            checks.append({"label": label, "path": path, "url": None, "ok": False, "status": None, "error": "invalid url"})
            continue

        started = time.perf_counter()
        try:
            request = Request(
                url,
                method="GET",
                headers={
                    "User-Agent": "Mozilla/5.0 Dashboard Health Check",
                    "Accept": "text/html,application/pdf,*/*",
                },
            )
            with urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
                status = int(getattr(response, "status", response.getcode()))
                ok = 200 <= status < 400
                if ok:
                    connected_count += 1
                checks.append({
                    "label": label,
                    "path": path,
                    "url": url,
                    "status": status,
                    "ok": ok,
                    "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                })
        except HTTPError as exc:
            checks.append({
                "label": label,
                "path": path,
                "url": url,
                "status": int(getattr(exc, "code", 0) or 0),
                "ok": False,
                "error": str(exc),
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            })
        except URLError as exc:
            checks.append({
                "label": label,
                "path": path,
                "url": url,
                "status": None,
                "ok": False,
                "error": str(getattr(exc, "reason", exc)),
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            })
        except Exception as exc:
            checks.append({
                "label": label,
                "path": path,
                "url": url,
                "status": None,
                "ok": False,
                "error": str(exc),
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            })

    return {
        "ok": connected_count == len(PUBLIC_CONNECTION_TEST_PATHS),
        "connected": connected_count == len(PUBLIC_CONNECTION_TEST_PATHS),
        "checked": connected_count,
        "required": len(PUBLIC_CONNECTION_TEST_PATHS),
        "target": candidate_target,
        "checks": checks,
    }


def get_attack_scan_state():
    with ATTACK_SCAN_LOCK:
        return dict(ATTACK_SCAN_STATE)


def build_attack_scan_command(
    target: str,
    profile: str,
    output_path: Path,
    burst_requests: int,
    burst_concurrency: int,
    timeout_seconds: float,
    allow_public_target: bool,
):
    cmd = [
        sys.executable,
        str(ATTACK_SCAN_SCRIPT),
        "--target",
        target,
        "--profile",
        profile,
        "--burst-requests",
        str(clamp_int(burst_requests, 80, 1, 5000)),
        "--burst-concurrency",
        str(clamp_int(burst_concurrency, 16, 1, 200)),
        "--timeout",
        str(max(1.0, min(float(timeout_seconds), 60.0))),
        "--output",
        str(output_path),
    ]
    if allow_public_target:
        cmd.append("--allow-public-target")
    return cmd


def compute_attack_scan_process_timeout(timeout_seconds: float, burst_requests: int, burst_concurrency: int, profile: str) -> int:
    timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
    burst_requests = clamp_int(burst_requests, 80, 1, 5000)
    burst_concurrency = clamp_int(burst_concurrency, 16, 1, 200)
    profile = normalize_choice(profile, {"quick", "standard", "aggressive"}, "standard")

    profile_multiplier = {"quick": 1.0, "standard": 1.4, "aggressive": 1.9}[profile]
    burst_batches = max(1, math.ceil(burst_requests / max(1, burst_concurrency)))

    # Budget for discovery + probes + burst + jitter.
    base_budget = 35 + int(timeout_seconds * 4)
    burst_budget = int(burst_batches * timeout_seconds * profile_multiplier)
    total = base_budget + burst_budget + 20
    return max(60, min(total, 900))


def run_attack_scan_async(target: str, profile: str, burst_requests: int, burst_concurrency: int, timeout_seconds: float, allow_public_target: bool):
    profile = normalize_choice(profile, {"quick", "standard", "aggressive"}, "standard")
    target = (target or "").strip()
    if not target:
        return {"ok": False, "error": "target is required"}
    if not ATTACK_SCAN_SCRIPT.exists():
        return {"ok": False, "error": f"scan script not found: {ATTACK_SCAN_SCRIPT}"}

    with ATTACK_SCAN_LOCK:
        if ATTACK_SCAN_STATE["running"]:
            return {"ok": False, "error": "scan already running", "state": dict(ATTACK_SCAN_STATE)}
        ATTACK_SCAN_STATE["running"] = True
        ATTACK_SCAN_STATE["last_started"] = datetime.now().isoformat()
        ATTACK_SCAN_STATE["last_target"] = target
        ATTACK_SCAN_STATE["last_profile"] = profile
        ATTACK_SCAN_STATE["last_error"] = None

    cmd = build_attack_scan_command(
        target=target,
        profile=profile,
        output_path=ATTACK_REPORT,
        burst_requests=burst_requests,
        burst_concurrency=burst_concurrency,
        timeout_seconds=timeout_seconds,
        allow_public_target=allow_public_target,
    )
    process_timeout = compute_attack_scan_process_timeout(timeout_seconds, burst_requests, burst_concurrency, profile)

    def worker():
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=process_timeout,
            )
            output = (proc.stdout or "").strip()
            error = (proc.stderr or "").strip()
            with ATTACK_SCAN_LOCK:
                ATTACK_SCAN_STATE["running"] = False
                ATTACK_SCAN_STATE["last_finished"] = datetime.now().isoformat()
                ATTACK_SCAN_STATE["last_exit_code"] = int(proc.returncode)
                ATTACK_SCAN_STATE["last_output"] = (output[-2000:] if output else None)
                ATTACK_SCAN_STATE["last_error"] = (error[-2000:] if error else None)
            set_watch_state_snapshot(build_watch_snapshot())
        except subprocess.TimeoutExpired:
            with ATTACK_SCAN_LOCK:
                ATTACK_SCAN_STATE["running"] = False
                ATTACK_SCAN_STATE["last_finished"] = datetime.now().isoformat()
                ATTACK_SCAN_STATE["last_exit_code"] = -1
                ATTACK_SCAN_STATE["last_error"] = "scan timed out"
            set_watch_state_snapshot(build_watch_snapshot())
        except Exception as exc:
            with ATTACK_SCAN_LOCK:
                ATTACK_SCAN_STATE["running"] = False
                ATTACK_SCAN_STATE["last_finished"] = datetime.now().isoformat()
                ATTACK_SCAN_STATE["last_exit_code"] = -1
                ATTACK_SCAN_STATE["last_error"] = str(exc)
            set_watch_state_snapshot(build_watch_snapshot())

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "state": get_attack_scan_state()}


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

    def _client_ip(self):
        return extract_client_ip(self)

    def _enforce_request_guard(self):
        client_ip = self._client_ip()
        user_agent = self.headers.get("User-Agent")
        allowed, status, message = check_request_guard(client_ip, user_agent)
        if allowed:
            return True

        log_rate_limited(
            f"blocked-{client_ip}",
            f"blocked request ip={client_ip} status={status} reason={message} ua={user_agent or '-'}",
            level=logging.WARNING,
            interval_seconds=10.0,
        )
        self._json_error(status, message)
        return False

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

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return None, "Invalid Content-Length"

        if length <= 0:
            return {}, None

        try:
            raw = self.rfile.read(length)
        except OSError:
            return None, "Failed to read request body"

        try:
            return json.loads(raw.decode("utf-8")), None
        except (ValueError, UnicodeDecodeError):
            return None, "Invalid JSON body"

    def do_GET(self):
        if not self._enforce_request_guard():
            return

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

        if route == "/api/connection-test":
            target = query.get("target", [None])[0]
            result = test_public_connection(target=target, timeout_seconds=5.0)
            # Keep dashboard polling/boot resilient: health can be degraded without failing the endpoint itself.
            self._json(200, result)
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

        if route == "/api/log/local/rows":
            try:
                lines = parse_requested_int(query, "tail", 150, 1, MAX_TAIL_LINES)
            except ValueError as exc:
                LOG.warning("invalid local rows tail query: %s", exc)
                self._json_error(400, str(exc))
                return
            search_text = query.get("q", [None])[0]
            rows = parse_log_rows(LOCAL_LOG, lines=lines, text_filter=search_text)
            self._json(200, {"ok": True, "rows": rows})
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

        if route == "/api/log/ngrok/rows":
            try:
                lines = parse_requested_int(query, "tail", 150, 1, MAX_TAIL_LINES)
            except ValueError as exc:
                LOG.warning("invalid ngrok rows tail query: %s", exc)
                self._json_error(400, str(exc))
                return
            search_text = query.get("q", [None])[0]
            rows = parse_log_rows(NGROK_LOG, lines=lines, text_filter=search_text)
            self._json(200, {"ok": True, "rows": rows})
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
            methods_filter = parse_requested_csv(query, "methods")
            status_family_filter = query.get("status_family", [None])[0] or None
            text_filter = query.get("q", [None])[0] or None
            stats_payload = get_access_stats_cached(
                LOCAL_LOG,
                top=top,
                ip_sort=ip_sort,
                ip_order=ip_order,
                ip_filter=ip_filter,
                site_filter=site_filter,
                status_filter=status_filter,
                methods_filter=methods_filter,
                status_family_filter=status_family_filter,
                text_filter=text_filter,
            )
            alerts = evaluate_alerts(stats_payload)
            self._json(
                200,
                {
                    "ok": True,
                    "stats": stats_payload,
                    "alerts": alerts,
                    "rules": list_ip_rules(),
                    "attack_report": load_attack_report(ATTACK_REPORT),
                    "attack_scan": get_attack_scan_state(),
                    "attack_default_target": ATTACK_DEFAULT_TARGET,
                },
            )
            return

        if route == "/api/dimensions":
            self._json(200, {"ok": True, "dimensions": parse_dimensions(LOCAL_LOG)})
            return

        if route == "/api/attack/latest":
            report = load_attack_report(ATTACK_REPORT)
            self._json(200 if report.get("ok") else 404, report)
            return

        if route == "/api/attack/status":
            self._json(200, {"ok": True, "state": get_attack_scan_state(), "report": load_attack_report(ATTACK_REPORT)})
            return

        if route == "/api/security/rules":
            self._json(200, {"ok": True, "rules": list_ip_rules()})
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
        if not self._enforce_request_guard():
            return

        if not self._require_auth():
            return

        parsed = urlparse(self.path)
        route = parsed.path

        if route.startswith("/api/tasks/"):
            action = route.replace("/api/tasks/", "", 1)
            result = run_task_action(action)
            self._json(200 if result.get("ok") else 400, result)
            return

        if route == "/api/security/block-ip":
            body, err = self._read_json_body()
            if err:
                self._json_error(400, err)
                return
            ip = str((body or {}).get("ip") or "").strip()
            seconds = (body or {}).get("seconds", 3600)
            result = set_ip_rule("block", ip, seconds=seconds)
            self._json(200 if result.get("ok") else 400, result)
            return

        if route == "/api/security/rate-limit-ip":
            body, err = self._read_json_body()
            if err:
                self._json_error(400, err)
                return
            ip = str((body or {}).get("ip") or "").strip()
            seconds = (body or {}).get("seconds", 3600)
            requests = (body or {}).get("requests", 20)
            window = (body or {}).get("window", 60)
            ban = (body or {}).get("ban", 300)
            result = set_ip_rule("rate-limit", ip, seconds=seconds, requests=requests, window=window, ban=ban)
            self._json(200 if result.get("ok") else 400, result)
            return

        if route == "/api/security/unblock-ip":
            body, err = self._read_json_body()
            if err:
                self._json_error(400, err)
                return
            ip = str((body or {}).get("ip") or "").strip()
            if not ip:
                self._json_error(400, "ip is required")
                return
            self._json(200, clear_ip_rule(ip))
            return

        if route == "/api/attack/run":
            body, err = self._read_json_body()
            if err:
                self._json_error(400, err)
                return
            target = str((body or {}).get("target") or ATTACK_DEFAULT_TARGET or "").strip()
            profile = str((body or {}).get("profile") or "standard").strip().lower()
            burst_requests = (body or {}).get("burst_requests", 80)
            burst_concurrency = (body or {}).get("burst_concurrency", 16)
            timeout_seconds = (body or {}).get("timeout", 8)
            allow_public_target = bool((body or {}).get("allow_public_target", True))

            result = run_attack_scan_async(
                target=target,
                profile=profile,
                burst_requests=burst_requests,
                burst_concurrency=burst_concurrency,
                timeout_seconds=timeout_seconds,
                allow_public_target=allow_public_target,
            )
            self._json(200 if result.get("ok") else 400, result)
            return

        self._text(404, "Not found")


def main():
    global LOCAL_LOG, NGROK_LOG, ATTACK_REPORT, ATTACK_SCAN_SCRIPT, ATTACK_DEFAULT_TARGET, TASK_NAMES, SERVER_LOG, AUTH_TOKEN, AUTH_USER, AUTH_PASSWORD, ALLOWLIST, DEBUG_TASK_OUTPUT
    global RATE_LIMIT_ENABLED, RATE_LIMIT_WINDOW_SECONDS, RATE_LIMIT_REQUESTS_PER_WINDOW, RATE_LIMIT_BAN_SECONDS, BLOCK_BOT_USER_AGENTS

    parser = argparse.ArgumentParser(description="Local services dashboard")
    parser.add_argument("--host", default=os.environ.get("DASHBOARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT", "8091")))
    parser.add_argument("--local-log", default=str(LOCAL_LOG))
    parser.add_argument("--ngrok-log", default=str(NGROK_LOG))
    parser.add_argument("--attack-report", default=str(ATTACK_REPORT))
    parser.add_argument("--attack-script", default=str(ATTACK_SCAN_SCRIPT))
    parser.add_argument("--attack-target", default=ATTACK_DEFAULT_TARGET)
    parser.add_argument("--task-name-local", default=TASK_NAMES[0])
    parser.add_argument("--task-name-ngrok", default=TASK_NAMES[1])
    parser.add_argument("--server-log", default=str(SERVER_LOG))
    parser.add_argument("--auth-token", default=AUTH_TOKEN)
    parser.add_argument("--auth-user", default=AUTH_USER)
    parser.add_argument("--auth-password", default=AUTH_PASSWORD)
    parser.add_argument("--allowlist", default=",".join(ALLOWLIST))
    parser.add_argument("--debug-task-output", action="store_true", default=DEBUG_TASK_OUTPUT)
    parser.add_argument(
        "--rate-limit-enabled",
        dest="rate_limit_enabled",
        action="store_true",
        default=RATE_LIMIT_ENABLED,
    )
    parser.add_argument(
        "--disable-rate-limit",
        dest="rate_limit_enabled",
        action="store_false",
    )
    parser.add_argument("--rate-limit-window", type=int, default=int(os.environ.get("DASHBOARD_RATE_LIMIT_WINDOW_SECONDS", RATE_LIMIT_WINDOW_SECONDS)))
    parser.add_argument("--rate-limit-requests", type=int, default=int(os.environ.get("DASHBOARD_RATE_LIMIT_REQUESTS", RATE_LIMIT_REQUESTS_PER_WINDOW)))
    parser.add_argument("--rate-limit-ban", type=int, default=int(os.environ.get("DASHBOARD_RATE_LIMIT_BAN_SECONDS", RATE_LIMIT_BAN_SECONDS)))
    parser.add_argument(
        "--block-bot-ua",
        dest="block_bot_ua",
        action="store_true",
        default=BLOCK_BOT_USER_AGENTS,
    )
    parser.add_argument(
        "--disable-bot-ua-block",
        dest="block_bot_ua",
        action="store_false",
    )
    args = parser.parse_args()

    LOCAL_LOG = Path(args.local_log)
    NGROK_LOG = Path(args.ngrok_log)
    ATTACK_REPORT = Path(args.attack_report)
    ATTACK_SCAN_SCRIPT = Path(args.attack_script)
    ATTACK_DEFAULT_TARGET = (args.attack_target or "").strip()
    TASK_NAMES = [args.task_name_local, args.task_name_ngrok]
    SERVER_LOG = Path(args.server_log)
    AUTH_TOKEN = args.auth_token or None
    AUTH_USER = args.auth_user or None
    AUTH_PASSWORD = args.auth_password or None
    ALLOWLIST = tuple(filter(None, (item.strip() for item in (args.allowlist or "").split(","))))
    DEBUG_TASK_OUTPUT = bool(args.debug_task_output)
    RATE_LIMIT_ENABLED = bool(args.rate_limit_enabled)
    RATE_LIMIT_WINDOW_SECONDS = clamp_int(args.rate_limit_window, 60, 10, 3600)
    RATE_LIMIT_REQUESTS_PER_WINDOW = clamp_int(args.rate_limit_requests, 120, 10, 5000)
    RATE_LIMIT_BAN_SECONDS = clamp_int(args.rate_limit_ban, 300, 10, 86400)
    BLOCK_BOT_USER_AGENTS = bool(args.block_bot_ua)

    configure_logging(SERVER_LOG)
    LOG.info(
        "starting dashboard host=%s port=%s local_log=%s ngrok_log=%s rate_limit_enabled=%s limit=%s/%ss ban=%ss block_bot_ua=%s",
        args.host,
        args.port,
        LOCAL_LOG,
        NGROK_LOG,
        RATE_LIMIT_ENABLED,
        RATE_LIMIT_REQUESTS_PER_WINDOW,
        RATE_LIMIT_WINDOW_SECONDS,
        RATE_LIMIT_BAN_SECONDS,
        BLOCK_BOT_USER_AGENTS,
    )

    watcher = threading.Thread(target=watch_for_changes, daemon=True)
    watcher.start()

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
