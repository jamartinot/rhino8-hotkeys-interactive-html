import argparse
import difflib
import http.client
import hashlib
import json
import random
import re
import socket
import string
import sys
import time
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlsplit, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


DEFAULT_TIMEOUT = 8.0
DEFAULT_OUTPUT = Path("attack-simulation-report.json")
PUBLIC_DISCOVERY_PATH_CANDIDATES = [
    "/Rhino 8 Interactive Cheat Sheet Manual.pdf",
    "/cai/",
    "/cai/CAI_ Collision Awareness Indicator.html",
    "/cai/investment_heatmap.html",
    "/cai/CAI_ Collision Awareness Indicator_files/",
    "/cai/CAI_ Collision Awareness Indicator_files/css2",
]
COMMON_DIRECTORY_WORDS = [
    "admin",
    "api",
    "assets",
    "backup",
    "bin",
    "cache",
    "cgi-bin",
    "config",
    "content",
    "data",
    "dev",
    "docs",
    "images",
    "includes",
    "js",
    "lib",
    "logs",
    "old",
    "private",
    "scripts",
    "src",
    "stage",
    "staging",
    "static",
    "temp",
    "tmp",
    "test",
    "uploads",
]
COMMON_FILE_WORDS = [
    "index",
    "home",
    "main",
    "default",
    "readme",
    "robots",
    "sitemap",
    "manifest",
    "login",
    "dashboard",
    "config",
    "backup",
    "db",
    "database",
]
COMMON_EXTENSIONS = [
    "html",
    "htm",
    "php",
    "asp",
    "aspx",
    "jsp",
    "json",
    "xml",
    "txt",
    "js",
    "map",
    "bak",
    "old",
    "orig",
    "swp",
    "tmp",
    "zip",
    "sql",
]
COMMON_PARAMETER_NAMES = ["id", "page", "view", "file", "path", "debug", "lang", "mode"]
COMMON_PARAMETER_VALUES = ["1", "true", "debug", "admin", "test"]
COMMON_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
]
COMMON_VHOST_PREFIXES = ["admin", "dev", "stage", "staging", "test", "beta", "api", "app", "portal", "internal"]


@dataclass
class ProbeCase:
    category: str
    name: str
    method: str
    path: str
    expected_min_status: int = 400
    expected_max_status: int = 599
    headers: dict[str, str] | None = None
    body: bytes | None = None


class PathBloomFilter:
    def __init__(self, size: int = 4096, hash_count: int = 4):
        self.size = max(64, int(size))
        self.hash_count = max(2, int(hash_count))
        self.bits = bytearray((self.size + 7) // 8)

    def _indexes(self, value: str):
        key = str(value or "").lower()
        base = key.encode("utf-8", errors="ignore")
        for seed in range(self.hash_count):
            digest = int.from_bytes(hashlib.blake2b(base, digest_size=8, person=f"bf{seed}".encode("ascii")).digest(), "big")
            yield digest % self.size

    def add(self, value: str) -> None:
        for index in self._indexes(value):
            self.bits[index // 8] |= 1 << (index % 8)

    def __contains__(self, value: str) -> bool:
        return all((self.bits[index // 8] >> (index % 8)) & 1 for index in self._indexes(value))


def is_private_or_loopback_host(target_url: str) -> bool:
    parsed = urlparse(target_url)
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    if host.startswith("10."):
        return True
    if host.startswith("192.168."):
        return True
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) > 1 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return True
    return False


def join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def parse_target_scope(target_url: str) -> tuple[str, str]:
    parsed = urlparse(target_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("target must be a full URL like https://example.com/path")
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    entry_path = parsed.path or "/"
    if not entry_path.startswith("/"):
        entry_path = f"/{entry_path}"
    return base_url, entry_path


def normalize_origin(url: str) -> str:
    parsed = urlsplit((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def is_same_origin_url(url: str, allowed_origin: str) -> bool:
    if not allowed_origin:
        return True
    return normalize_origin(url) == normalize_origin(allowed_origin)


class SameOriginRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_origin: str):
        super().__init__()
        self.allowed_origin = normalize_origin(allowed_origin)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.allowed_origin and not is_same_origin_url(newurl, self.allowed_origin):
            raise HTTPError(newurl, code, f"Blocked redirect outside origin: {self.allowed_origin}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def emit_status_line(phase: str, progress: int, message: str, **extra):
    payload = {
        "phase": str(phase),
        "progress": max(0, min(100, int(progress))),
        "message": str(message),
        "time": int(time.time()),
    }
    payload.update(extra or {})
    print(f"[[STATUS]] {json.dumps(payload, separators=(',', ':'))}", flush=True)


def build_candidate_ports(target_url: str) -> list[int]:
    parsed = urlparse(target_url)
    ports = set()
    if parsed.port:
        ports.add(int(parsed.port))
    elif parsed.scheme == "https":
        ports.add(443)
    else:
        ports.add(80)

    ports.update({80, 443, 8000, 8091, 4040})
    return sorted(port for port in ports if 1 <= port <= 65535)


def send_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    read_limit: int = 1024,
    retries: int = 1,
    allowed_origin: str | None = None,
    allow_outside_origin: bool = False,
    user_agents: list[str] | None = None,
    jitter_seconds: tuple[float, float] | None = None,
) -> dict[str, Any]:
    last_error = "network error"
    last_latency = 0.0
    attempts = max(1, int(retries))

    for attempt in range(attempts):
        apply_request_jitter(jitter_seconds)
        if allowed_origin and not allow_outside_origin and not is_same_origin_url(url, allowed_origin):
            return {
                "ok": False,
                "status": 0,
                "latency_ms": 0.0,
                "sample": f"blocked request outside origin: {url}",
                "headers": {},
            }

        request_headers = dict(headers or {})
        if "User-Agent" not in request_headers:
            request_headers["User-Agent"] = choose_user_agent(user_agents)
        req = Request(url=url, method=method.upper(), headers=request_headers, data=body)
        start = time.perf_counter()
        try:
            if allowed_origin and not allow_outside_origin:
                opener = build_opener(SameOriginRedirectHandler(allowed_origin))
                response_context = opener.open(req, timeout=timeout)
            else:
                response_context = urlopen(req, timeout=timeout)

            with response_context as response:
                latency_ms = (time.perf_counter() - start) * 1000.0
                payload = response.read(max(128, int(read_limit)))
                return {
                    "ok": True,
                    "status": int(response.status),
                    "latency_ms": round(latency_ms, 2),
                    "sample": payload.decode("utf-8", errors="replace"),
                    "headers": dict(response.headers.items()),
                }
        except HTTPError as exc:
            blocked_redirect = "Blocked redirect outside origin" in str(exc)
            if blocked_redirect:
                latency_ms = (time.perf_counter() - start) * 1000.0
                return {
                    "ok": False,
                    "status": 0,
                    "latency_ms": round(latency_ms, 2),
                    "sample": str(exc),
                    "headers": {},
                }
            latency_ms = (time.perf_counter() - start) * 1000.0
            sample = b""
            try:
                sample = exc.read(max(128, int(read_limit)))
            except Exception:
                pass
            return {
                "ok": False,
                "status": int(exc.code),
                "latency_ms": round(latency_ms, 2),
                "sample": sample.decode("utf-8", errors="replace"),
                "headers": dict(getattr(exc, "headers", {}).items()) if getattr(exc, "headers", None) else {},
            }
        except URLError as exc:
            last_latency = (time.perf_counter() - start) * 1000.0
            last_error = str(exc.reason)
        except http.client.RemoteDisconnected as exc:
            last_latency = (time.perf_counter() - start) * 1000.0
            last_error = str(exc)
        except (ConnectionResetError, TimeoutError, ssl.SSLError) as exc:
            last_latency = (time.perf_counter() - start) * 1000.0
            last_error = str(exc)

        if attempt < attempts - 1:
            time.sleep(0.15 * (attempt + 1))

    return {
        "ok": False,
        "status": 0,
        "latency_ms": round(last_latency, 2),
        "sample": last_error,
        "headers": {},
    }


def discover_addresses(hostname: str) -> list[str]:
    addresses = set()
    if not hostname:
        return []
    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
            if family in {socket.AF_INET, socket.AF_INET6} and sockaddr:
                addresses.add(str(sockaddr[0]))
    except socket.gaierror:
        return []
    return sorted(addresses)


def discover_ports(hostname: str, ports: list[int], timeout: float, status_hook: Callable[..., None] | None = None) -> list[dict[str, Any]]:
    rows = []
    if not hostname:
        return rows
    for port in ports:
        start = time.perf_counter()
        state = "closed"
        detail = "timed out"
        try:
            with socket.create_connection((hostname, int(port)), timeout=max(0.3, timeout)):
                state = "open"
                detail = "connected"
        except Exception as exc:
            detail = str(exc) or detail
        latency_ms = (time.perf_counter() - start) * 1000.0
        rows.append(
            {
                "port": int(port),
                "state": state,
                "latency_ms": round(latency_ms, 2),
                "detail": detail,
            }
        )
        if status_hook:
            status_hook(
                "discovery",
                16,
                f"Port {port} checked",
                port=int(port),
                state=state,
                latency_ms=round(latency_ms, 2),
                detail=detail,
            )
    return rows


def extract_link_paths(html_text: str, current_path: str = "/") -> list[str]:
    if not html_text:
        return []
    matches = re.findall(r"(?:href|src)=['\"]([^'\"]+)['\"]", html_text, flags=re.IGNORECASE)
    out = []
    for value in matches:
        if value.startswith("http://") or value.startswith("https://") or value.startswith("data:"):
            continue
        if not value.startswith("/"):
            base_dir = current_path if current_path.endswith("/") else current_path.rsplit("/", 1)[0]
            if not base_dir.endswith("/"):
                base_dir += "/"
            value = base_dir + value.lstrip("./")

        parts = value.split("/")
        encoded_parts = [quote(part, safe="") if part and "%" not in part else part for part in parts]
        value = "/".join(encoded_parts)
        out.append(value)
    return sorted(set(out))


def normalize_crawl_path(path: str) -> str:
    p = (path or "").strip()
    if not p:
        return "/"
    if not p.startswith("/"):
        p = "/" + p
    p = p.split("#", 1)[0]
    return p


def response_signature(sample: str) -> str:
    text = re.sub(r"\s+", " ", str(sample or "")).strip().lower()
    return text[:240]


def response_fingerprint(sample: str) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", str(sample or "")).strip().lower()
    return {
        "length": len(text),
        "sha1": hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest(),
        "prefix": text[:240],
    }


def is_soft_404_like(candidate_sample: str, probe_fingerprint: dict[str, Any] | None) -> bool:
    if not probe_fingerprint:
        return False
    candidate_text = re.sub(r"\s+", " ", str(candidate_sample or "")).strip().lower()
    candidate_fp = response_fingerprint(candidate_text)
    if candidate_fp["sha1"] == probe_fingerprint.get("sha1"):
        return True

    probe_prefix = str(probe_fingerprint.get("prefix") or "")
    if not probe_prefix:
        return False

    ratio = difflib.SequenceMatcher(None, candidate_fp["prefix"], probe_prefix).ratio()
    if ratio >= 0.95:
        return True

    probe_length = int(probe_fingerprint.get("length") or 0)
    if probe_length > 0:
        length_delta = abs(candidate_fp["length"] - probe_length)
        if length_delta <= max(20, int(probe_length * 0.05)) and ratio >= 0.90:
            return True

    return False


def is_path_like(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith(("http://", "https://", "data:", "mailto:", "javascript:")):
        return False
    return "/" in text or bool(re.search(r"\.[A-Za-z0-9]{1,8}(?:$|[?#])", text))


def normalize_discovery_token(token: str, current_path: str = "/") -> str | None:
    candidate = str(token or "").strip().strip('"\'`<>[](){};,')
    if not candidate:
        return None
    if candidate.startswith(("http://", "https://", "data:", "mailto:", "javascript:", "#", "?")):
        return None
    if candidate.startswith("//"):
        return None
    # Drop MIME-like tokens that are often present in inline scripts/metadata.
    normalized_raw = candidate.split("#", 1)[0].split("?", 1)[0].strip().lower()
    if re.fullmatch(r"[a-z][a-z0-9+.-]*/[a-z0-9.+-]+", normalized_raw):
        return None

    relative_candidate = candidate
    if not candidate.startswith("/"):
        # Ignore bare words like "utf-8" or "viewport" that are not path-like.
        relative_candidate = candidate.lstrip("./")
        relative_no_query = relative_candidate.split("#", 1)[0].split("?", 1)[0].strip()
        if "/" not in relative_no_query and "." not in relative_no_query:
            return None

        # Ignore metadata fragments that are not URL paths (e.g. width=device-width).
        if "=" in relative_no_query and "/" not in relative_no_query and "." not in relative_no_query:
            return None

        first_segment = relative_no_query.split("/", 1)[0].lower()
        if first_segment in {"text", "image", "font", "audio", "video", "application", "multipart", "message", "model"} and "." not in relative_no_query:
            return None

        base_dir = current_path if current_path.endswith("/") else current_path.rsplit("/", 1)[0]
        if not base_dir.endswith("/"):
            base_dir += "/"
        candidate = base_dir + relative_candidate
    candidate = re.sub(r"/{2,}", "/", candidate.split("#", 1)[0].split("?", 1)[0])
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    parts_no_empty = [part for part in candidate.split("/") if part]
    if len(parts_no_empty) > 10:
        return None
    if not is_path_like(candidate):
        return None
    parts = candidate.split("/")
    encoded_parts = [quote(part, safe="") if part and "%" not in part else part for part in parts]
    return "/".join(encoded_parts)


def extract_discovery_tokens(text: str, current_path: str = "/") -> list[str]:
    if not text:
        return []

    tokens: set[str] = set()
    patterns = [
        r"(?:href|src|action|data-url|data-src)=['\"]([^'\"]+)['\"]",
        r"(?:fetch|open|location\.(?:href|assign)|import\()\s*['\"]([^'\"]+)['\"]",
        r"['\"](\/(?:[^'\"`<>\s]+))['\"]",
        r"\/(?:[A-Za-z0-9._~%+-]+\/)*[A-Za-z0-9._~%+-]+(?:\.[A-Za-z0-9]{1,8})?(?:\?[A-Za-z0-9=&_%\-.,]+)?",
    ]

    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            if isinstance(match, tuple):
                for item in match:
                    candidate = normalize_discovery_token(item, current_path=current_path)
                    if candidate:
                        tokens.add(candidate)
            else:
                candidate = normalize_discovery_token(match, current_path=current_path)
                if candidate:
                    tokens.add(candidate)

    return sorted(tokens)


def extract_html_comment_paths(html_text: str, current_path: str = "/") -> list[str]:
    tokens: set[str] = set()
    for comment in re.findall(r"<!--(.*?)-->", html_text or "", flags=re.DOTALL):
        for token in extract_discovery_tokens(comment, current_path=current_path):
            tokens.add(token)
    return sorted(tokens)


def extract_meta_refresh_paths(html_text: str, current_path: str = "/") -> list[str]:
    tokens: set[str] = set()
    refresh_matches = re.findall(r"<meta[^>]+http-equiv=['\"]refresh['\"][^>]+content=['\"][^>]*url=([^'\"\s>]+)", html_text or "", flags=re.IGNORECASE)
    for match in refresh_matches:
        candidate = normalize_discovery_token(match, current_path=current_path)
        if candidate:
            tokens.add(candidate)
    return sorted(tokens)


def extract_header_hints(headers: dict[str, Any] | None, base_url: str, current_path: str = "/") -> tuple[list[str], list[str]]:
    hints: set[str] = set()
    notes: list[str] = []
    if not headers:
        return [], []

    link_header = str(headers.get("Link") or headers.get("link") or "")
    for raw_link in re.findall(r"<([^>]+)>", link_header):
        candidate = normalize_discovery_token(raw_link, current_path=current_path)
        if candidate:
            hints.add(candidate)

    for key in ("Location", "Content-Location"):
        value = str(headers.get(key) or headers.get(key.lower()) or "")
        candidate = normalize_discovery_token(value, current_path=current_path)
        if candidate:
            hints.add(candidate)

    tech_headers = []
    for key in ("X-Powered-By", "X-Generator", "Server"):
        value = str(headers.get(key) or headers.get(key.lower()) or "").strip()
        if value:
            tech_headers.append(f"{key}: {value}")
    if tech_headers:
        notes.extend(tech_headers)

    return sorted(hints), notes


def extract_js_hint_paths(js_text: str, current_path: str = "/") -> list[str]:
    tokens: set[str] = set()
    if not js_text:
        return []

    for candidate in re.findall(r"(?:['\"`])([^'\"`]+(?:\.(?:php|html?|js|json|xml|txt|map))?[^'\"`]*)['\"`]", js_text):
        normalized = normalize_discovery_token(candidate, current_path=current_path)
        if normalized:
            tokens.add(normalized)

    for candidate in re.findall(r"\b(?:fetch|axios\.(?:get|post|put|delete)|open)\s*\(\s*['\"]([^'\"]+)['\"]", js_text, flags=re.IGNORECASE):
        normalized = normalize_discovery_token(candidate, current_path=current_path)
        if normalized:
            tokens.add(normalized)

    return sorted(tokens)


def get_discovery_profile_options(profile: str) -> dict[str, Any]:
    normalized = str(profile or "standard").lower()
    if normalized not in {"quick", "standard", "aggressive"}:
        normalized = "standard"

    base_options = {
        "profile": normalized,
        "max_pages": 80,
        "directory_words": COMMON_DIRECTORY_WORDS[:8],
        "file_words": COMMON_FILE_WORDS[:6],
        "extensions": COMMON_EXTENSIONS[:8],
        "parameter_names": COMMON_PARAMETER_NAMES[:4],
        "parameter_values": COMMON_PARAMETER_VALUES[:3],
        "user_agents": COMMON_USER_AGENTS[:2],
        "jitter_seconds": (0.0, 0.03),
        "enable_comments": True,
        "enable_js": True,
        "enable_headers": True,
        "enable_passive_intel": False,
        "enable_vhost": False,
        "enable_parameter_fuzzing": True,
    }

    if normalized == "quick":
        base_options.update(
            {
                "max_pages": 48,
                "directory_words": COMMON_DIRECTORY_WORDS[:5],
                "file_words": COMMON_FILE_WORDS[:4],
                "extensions": COMMON_EXTENSIONS[:6],
                "parameter_names": COMMON_PARAMETER_NAMES[:3],
                "parameter_values": COMMON_PARAMETER_VALUES[:2],
                "user_agents": COMMON_USER_AGENTS[:1],
                "jitter_seconds": (0.0, 0.02),
                "enable_passive_intel": False,
                "enable_vhost": False,
                "enable_parameter_fuzzing": True,
            }
        )
    elif normalized == "aggressive":
        base_options.update(
            {
                "max_pages": 180,
                "directory_words": COMMON_DIRECTORY_WORDS,
                "file_words": COMMON_FILE_WORDS,
                "extensions": COMMON_EXTENSIONS,
                "parameter_names": COMMON_PARAMETER_NAMES,
                "parameter_values": COMMON_PARAMETER_VALUES,
                "user_agents": COMMON_USER_AGENTS,
                "jitter_seconds": (0.01, 0.08),
                "enable_passive_intel": True,
                "enable_vhost": True,
                "enable_parameter_fuzzing": True,
            }
        )

    return base_options


def choose_user_agent(user_agents: list[str] | None = None) -> str:
    pool = [str(item or "").strip() for item in (user_agents or COMMON_USER_AGENTS) if str(item or "").strip()]
    if not pool:
        return "Mozilla/5.0"
    return random.choice(pool)


def apply_request_jitter(jitter_seconds: tuple[float, float] | None = None):
    if not jitter_seconds:
        return
    low, high = jitter_seconds
    low = max(0.0, float(low))
    high = max(low, float(high))
    if high <= 0:
        return
    time.sleep(random.uniform(low, high))


def build_directory_wordlist_candidates(directory_path: str, words: list[str] | None = None) -> list[str]:
    base = directory_path if directory_path.endswith("/") else f"{directory_path.rstrip('/')}/"
    results = []
    for word in words or COMMON_DIRECTORY_WORDS:
        candidate = normalize_crawl_path(base + str(word).strip().strip("/"))
        if candidate not in results:
            results.append(candidate)
    return results


def build_file_variants(path: str, extensions: list[str] | None = None) -> list[str]:
    cleaned = normalize_crawl_path(path)
    if cleaned.endswith("/"):
        return []
    base, _, ext = cleaned.rpartition(".")
    stem = base if base and "/" in base else cleaned
    stem_no_ext = cleaned.rsplit(".", 1)[0] if "." in cleaned.rsplit("/", 1)[-1] else cleaned
    suffixes = [".bak", ".old", ".orig", ".swp", "~", ".tmp"]
    results = set()

    if ext:
        for suffix in suffixes:
            results.add(normalize_crawl_path(f"{cleaned}{suffix}"))
            results.add(normalize_crawl_path(f"{stem_no_ext}{suffix}"))
        for extra_ext in extensions or COMMON_EXTENSIONS:
            if extra_ext != ext:
                results.add(normalize_crawl_path(f"{stem_no_ext}.{extra_ext}"))
    else:
        for extra_ext in extensions or COMMON_EXTENSIONS:
            results.add(normalize_crawl_path(f"{cleaned}.{extra_ext}"))

    return sorted(results)


def build_parameter_fuzz_candidates(
    path: str,
    parameter_names: list[str] | None = None,
    parameter_values: list[str] | None = None,
    limit: int = 4,
) -> list[str]:
    cleaned = normalize_crawl_path(path)
    if cleaned.endswith("/"):
        return []
    names = [str(item).strip() for item in (parameter_names or COMMON_PARAMETER_NAMES) if str(item).strip()]
    values = [str(item).strip() for item in (parameter_values or COMMON_PARAMETER_VALUES) if str(item).strip()]
    results = set()
    for name in names:
        for value in values:
            results.add(f"{cleaned}?{urlencode({name: value})}")
            if len(results) >= max(1, int(limit)):
                return sorted(results)
    return sorted(results)


def build_vhost_candidates(hostname: str) -> list[str]:
    host = str(hostname or "").strip().lower()
    if not host or "." not in host:
        return []
    parts = host.split(".")
    suffix = ".".join(parts[-2:]) if len(parts) >= 2 else host
    candidates = []
    for prefix in COMMON_VHOST_PREFIXES:
        candidate = f"{prefix}.{suffix}"
        if candidate != host and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def discover_passive_archive_paths(target_url: str, timeout: float, status_hook: Callable[..., None] | None = None) -> list[str]:
    parsed = urlparse(target_url)
    host = (parsed.netloc or "").strip()
    if not host:
        return []

    cdx_url = f"https://web.archive.org/cdx/search/cdx?{urlencode({'url': f'{host}/*', 'output': 'json', 'fl': 'original', 'collapse': 'urlkey', 'limit': '200'})}"
    try:
        resp = send_request(
            cdx_url,
            method="GET",
            timeout=max(1.0, min(float(timeout), 8.0)),
            read_limit=65536,
            retries=1,
            allowed_origin="https://web.archive.org",
            allow_outside_origin=True,
        )
    except Exception:
        return []

    if int(resp.get("status", 0)) not in range(200, 300):
        return []

    discovered: list[str] = []
    try:
        payload = json.loads(resp.get("sample") or "[]")
    except (ValueError, TypeError):
        payload = []

    if isinstance(payload, list):
        for row in payload[1:]:
            if not row:
                continue
            original = str(row[0] if isinstance(row, (list, tuple)) else row).strip()
            parsed_original = urlsplit(original)
            path = normalize_crawl_path(parsed_original.path or "/")
            if path and path not in discovered:
                discovered.append(path)
                if status_hook:
                    status_hook("discovery", 23, "Archive path discovered", path=path)
    return discovered


def discover_virtual_hosts(target_url: str, timeout: float, status_hook: Callable[..., None] | None = None, limit: int = 10) -> list[dict[str, Any]]:
    parsed = urlsplit(target_url)
    host = (parsed.hostname or "").strip().lower()
    scheme = parsed.scheme or "https"
    if not host or "." not in host:
        return []

    baseline = send_request(
        f"{scheme}://{parsed.netloc}{parsed.path or '/'}",
        method="GET",
        timeout=max(1.0, float(timeout)),
        read_limit=4096,
        retries=1,
        allowed_origin=f"{scheme}://{parsed.netloc}",
        allow_outside_origin=False,
    )
    baseline_fp = response_fingerprint(baseline.get("sample", ""))

    findings: list[dict[str, Any]] = []
    for candidate_host in build_vhost_candidates(host)[: max(0, int(limit))]:
        resp = send_request(
            f"{scheme}://{parsed.netloc}{parsed.path or '/'}",
            method="GET",
            headers={"Host": candidate_host},
            timeout=max(1.0, float(timeout)),
            read_limit=4096,
            retries=1,
            allowed_origin=f"{scheme}://{parsed.netloc}",
            allow_outside_origin=False,
        )
        if int(resp.get("status", 0)) in range(200, 400) and not is_soft_404_like(resp.get("sample", ""), baseline_fp):
            findings.append(
                {
                    "host": candidate_host,
                    "status": int(resp.get("status", 0)),
                    "latency_ms": resp.get("latency_ms"),
                    "sample_signature": response_signature(resp.get("sample", "")),
                }
            )
            if status_hook:
                status_hook("discovery", 24, "Vhost responded differently", host=candidate_host, status=resp.get("status"))
    return findings


def has_discovery_hints(sample_text: str, content_type: str, current_path: str) -> bool:
    if "html" not in str(content_type or "").lower():
        return False
    return len(extract_link_paths(sample_text, current_path=current_path)) > 0


def generate_directory_candidates(dir_path: str) -> list[str]:
    """Generate candidate filenames to try within a discovered directory."""
    if not dir_path.endswith("/"):
        dir_path = dir_path + "/"
    candidates = []
    common_names = [
        "index.html",
        "default.html",
        "readme.html",
        "readme.htm",
        "home.html",
        "main.html",
    ]
    for name in common_names:
        candidates.append(normalize_crawl_path(dir_path + name))
    return candidates


def discover_available_files(
    base_url: str,
    entry_path: str,
    timeout: float,
    max_pages: int = 80,
    allow_outside_origin: bool = False,
    status_hook: Callable[..., None] | None = None,
    profile_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    profile_options = dict(profile_options or {})
    max_pages = max(5, int(profile_options.get("max_pages", max_pages)))
    directory_words = list(profile_options.get("directory_words") or COMMON_DIRECTORY_WORDS)
    file_words = list(profile_options.get("file_words") or COMMON_FILE_WORDS)
    extensions = list(profile_options.get("extensions") or COMMON_EXTENSIONS)
    parameter_names = list(profile_options.get("parameter_names") or COMMON_PARAMETER_NAMES)
    parameter_values = list(profile_options.get("parameter_values") or COMMON_PARAMETER_VALUES)
    user_agents = list(profile_options.get("user_agents") or COMMON_USER_AGENTS)
    jitter_seconds = tuple(profile_options.get("jitter_seconds") or (0.0, 0.0))
    enable_comments = bool(profile_options.get("enable_comments", True))
    enable_js = bool(profile_options.get("enable_js", True))
    enable_headers = bool(profile_options.get("enable_headers", True))

    entry_parent = "/"
    trimmed = entry_path.strip("/")
    if "/" in trimmed:
        entry_parent = "/" + trimmed.rsplit("/", 1)[0] + "/"
    seed_paths = [
        "/",
        entry_path,
        entry_parent,
        "/robots.txt",
        "/sitemap.xml",
        "/favicon.ico",
        "/cai/",
        "/assets/",
        "/non_interactive/",
    ]
    seed_paths.extend(build_directory_wordlist_candidates(entry_parent, directory_words[: min(6, len(directory_words))]))
    for word in file_words[: min(6, len(file_words))]:
        for ext in extensions[: min(4, len(extensions))]:
            seed_paths.append(normalize_crawl_path(f"{entry_parent.rstrip('/')}/{word}.{ext}"))
    seed_paths.extend(build_file_variants(entry_path, extensions=extensions[: min(8, len(extensions))]))
    seed_paths.extend(build_parameter_fuzz_candidates(entry_path, parameter_names=parameter_names[:2], parameter_values=parameter_values[:2], limit=4))
    queue = [normalize_crawl_path(path) for path in seed_paths]
    seen = set()
    rows = []
    directory_cache = set()
    link_discovered_paths = set()
    seen_bloom = PathBloomFilter(size=max(4096, max_pages * 64), hash_count=4)
    queued_bloom = PathBloomFilter(size=max(4096, max_pages * 64), hash_count=4)
    entry_observation = None
    queued_directory_candidates = set()
    crawl_headers_template = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "close",
    }
    soft_404_fingerprint: dict[str, Any] | None = None
    probe_path = f"/__discovery_probe_{int(time.time())}_{random.randint(1000, 9999)}.txt"
    probe_resp = send_request(
        join_url(base_url, probe_path),
        method="GET",
        headers={**crawl_headers_template, "User-Agent": choose_user_agent(user_agents)},
        timeout=timeout,
        read_limit=4096,
        retries=2,
        allowed_origin=base_url,
        allow_outside_origin=allow_outside_origin,
        user_agents=user_agents,
        jitter_seconds=jitter_seconds,
    )
    if int(probe_resp.get("status", 0)) in range(200, 400):
        soft_404_fingerprint = response_fingerprint(probe_resp.get("sample", ""))
    if status_hook:
        status_hook("discovery", 11, "Seed queue prepared", seed_count=len(queue), entry_path=entry_path)

    def enqueue_candidate(
        candidate: str,
        source_path: str | None = None,
        message: str = "Queued candidate",
        *,
        directory_child: bool = False,
        append_only: bool = False,
    ):
        if not candidate or candidate in seen_bloom or candidate in queued_bloom or candidate in queue:
            return
        if append_only or source_path in {"/", entry_parent}:
            queue.append(candidate)
        elif directory_child:
            queue.insert(0, candidate)
        else:
            queue.insert(0, candidate)
        queued_bloom.add(candidate)
        if status_hook:
            kwargs = {"source": source_path, "path": candidate} if source_path else {"path": candidate}
            status_hook("discovery", 19, message, **kwargs)

    while queue and len(seen) < max(5, int(max_pages)):
        path = normalize_crawl_path(queue.pop(0))
        if path in seen or path in seen_bloom:
            continue
        seen_bloom.add(path)
        seen.add(path)

        apply_request_jitter(jitter_seconds)

        request_headers = {**crawl_headers_template, "User-Agent": choose_user_agent(user_agents)}

        resp = send_request(
            join_url(base_url, path),
            method="GET",
            headers=request_headers,
            timeout=timeout,
            read_limit=262144,
            retries=3,
            allowed_origin=base_url,
            allow_outside_origin=allow_outside_origin,
            user_agents=user_agents,
            jitter_seconds=jitter_seconds,
        )
        status = int(resp.get("status", 0))
        if path == entry_path:
            entry_observation = {
                "path": entry_path,
                "status": status,
                "latency_ms": resp.get("latency_ms"),
            }
        if status in range(200, 400):
            sample_text = resp.get("sample", "")
            headers = resp.get("headers") or {}
            content_type = str(headers.get("Content-Type") or headers.get("content-type") or "").lower()
            if (
                soft_404_fingerprint
                and is_soft_404_like(sample_text, soft_404_fingerprint)
                and path not in link_discovered_paths
                and path not in {"/", entry_path, entry_parent}
                and not has_discovery_hints(sample_text, content_type, path)
            ):
                continue

            rows.append({"path": path, "status": status, "latency_ms": resp.get("latency_ms")})
            if status_hook:
                status_hook(
                    "discovery",
                    18,
                    "Discovered path",
                    path=path,
                    status=status,
                    latency_ms=resp.get("latency_ms"),
                )

            if enable_headers:
                header_hints, header_notes = extract_header_hints(headers, base_url, current_path=path)
                if header_notes:
                    for note in header_notes:
                        if status_hook:
                            status_hook("discovery", 18, "Header hint", path=path, note=note)
                for candidate in header_hints:
                    enqueue_candidate(candidate, source_path=path, message="Queued header hint")

            if "html" in content_type:
                link_sources = extract_link_paths(sample_text, current_path=path)
                if enable_comments:
                    link_sources.extend(extract_html_comment_paths(sample_text, current_path=path))
                    link_sources.extend(extract_meta_refresh_paths(sample_text, current_path=path))
                if enable_js:
                    link_sources.extend(extract_js_hint_paths(sample_text, current_path=path))

                for linked in sorted(set(link_sources)):
                    candidate = normalize_crawl_path(linked)
                    if not candidate:
                        continue
                    link_discovered_paths.add(candidate)
                    enqueue_candidate(candidate, source_path=path, message="Queued linked path")

            if "javascript" in content_type or path.endswith(".js"):
                for linked in extract_js_hint_paths(sample_text, current_path=path):
                    candidate = normalize_crawl_path(linked)
                    if candidate:
                        enqueue_candidate(candidate, source_path=path, message="Queued JS hint")

            if path.endswith("/") and path not in directory_cache:
                directory_cache.add(path)
                for candidate in generate_directory_candidates(path):
                    candidate = normalize_crawl_path(candidate)
                    if candidate not in queued_directory_candidates:
                        queued_directory_candidates.add(candidate)
                        enqueue_candidate(candidate, source_path=path, message="Queued directory candidate", directory_child=True)

                for candidate in build_directory_wordlist_candidates(path, directory_words):
                    if candidate not in queued_directory_candidates:
                        queued_directory_candidates.add(candidate)
                        enqueue_candidate(candidate, source_path=path, message="Queued directory wordlist candidate", append_only=True)

            if not path.endswith("/"):
                for candidate in build_file_variants(path, extensions=extensions):
                    enqueue_candidate(candidate, source_path=path, message="Queued file variant", append_only=True)

                if bool(profile_options.get("enable_parameter_fuzzing", True)):
                    for candidate in build_parameter_fuzz_candidates(path, parameter_names=parameter_names[:2], parameter_values=parameter_values[:2], limit=4):
                        enqueue_candidate(candidate, source_path=path, message="Queued parameter fuzz candidate", append_only=True)

    rows.sort(key=lambda item: item["path"])
    if not rows and entry_observation:
        rows.append(entry_observation)
    return rows


def discover_confirmed_public_paths(
    base_url: str,
    timeout: float,
    candidates: list[str] | None = None,
    allow_outside_origin: bool = False,
    status_hook: Callable[..., None] | None = None,
) -> list[dict[str, Any]]:
    confirmed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in (candidates or PUBLIC_DISCOVERY_PATH_CANDIDATES):
        path = normalize_crawl_path(raw_path)
        if path in seen:
            continue
        seen.add(path)
        encoded_path = "/" + "/".join(quote(part, safe="") for part in path.lstrip("/").split("/"))
        resp = send_request(
            join_url(base_url, encoded_path),
            method="GET",
            timeout=max(1.0, timeout),
            read_limit=4096,
            retries=2,
            allowed_origin=base_url,
            allow_outside_origin=allow_outside_origin,
            user_agents=[choose_user_agent() if allow_outside_origin else COMMON_USER_AGENTS[0]],
        )
        status = int(resp.get("status", 0))
        if status in range(200, 400):
            confirmed.append({"path": path, "status": status, "latency_ms": resp.get("latency_ms")})
            if status_hook:
                status_hook(
                    "discovery",
                    22,
                    "Confirmed public path",
                    path=path,
                    status=status,
                    latency_ms=resp.get("latency_ms"),
                )
    return confirmed


def run_discovery(
    target_url: str,
    base_url: str,
    entry_path: str,
    timeout: float,
    allow_outside_origin: bool = False,
    status_hook: Callable[..., None] | None = None,
    profile_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile_options = dict(profile_options or get_discovery_profile_options("standard"))
    parsed = urlparse(target_url)
    host = parsed.hostname or ""
    addresses = discover_addresses(host) if host else []
    ports = discover_ports(host, build_candidate_ports(target_url), timeout=max(0.5, min(timeout, 3.0)), status_hook=status_hook) if host else []
    files = discover_available_files(
        base_url,
        entry_path,
        timeout=max(1.0, timeout),
        allow_outside_origin=allow_outside_origin,
        status_hook=status_hook,
        profile_options=profile_options,
    )
    include_local_hints = is_private_or_loopback_host(target_url)
    hints = build_discovery_path_hints(entry_path, include_local_sources=include_local_hints)

    if status_hook and addresses:
        for address in addresses:
            status_hook("discovery", 14, "Resolved address", address=address)

    if not include_local_hints:
        # Public scans can still enrich discovery with confirmed-known public pages.
        confirmed = discover_confirmed_public_paths(
            base_url,
            timeout=max(1.0, timeout),
            allow_outside_origin=allow_outside_origin,
            status_hook=status_hook,
        )
        file_map = {row.get("path"): row for row in files}
        for row in confirmed:
            path = row.get("path")
            if path and path not in file_map:
                files.append(row)
                file_map[path] = row
            if path and path not in hints:
                hints.append(path)

    if profile_options.get("enable_passive_intel") and not include_local_hints:
        archive_paths = discover_passive_archive_paths(target_url, timeout=max(1.0, timeout), status_hook=status_hook)
        file_map = {row.get("path"): row for row in files}
        for path in archive_paths:
            if path not in file_map:
                row = {"path": path, "status": 0, "latency_ms": 0.0, "source": "archive"}
                files.append(row)
                file_map[path] = row
            if path not in hints:
                hints.append(path)

    vhost_results = []
    if profile_options.get("enable_vhost") and host:
        vhost_results = discover_virtual_hosts(target_url, timeout=max(1.0, timeout), status_hook=status_hook)

    return {
        "host": host,
        "addresses": addresses,
        "ports": ports,
        "available_files": sorted(files, key=lambda item: str(item.get("path", ""))),
        "path_hints": hints,
        "vhost_results": vhost_results,
        "header_notes": [],
    }


def collect_local_tree_paths(root_dir: Path, url_prefix: str) -> list[str]:
    if not root_dir.exists():
        return []

    results: list[str] = []

    def visit(current: Path, prefix: str) -> None:
        if current.is_dir():
            if prefix:
                directory_path = prefix if prefix.endswith("/") else f"{prefix}/"
                if directory_path not in results:
                    results.append(directory_path)
            for child in sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
                child_prefix = f"{prefix.rstrip('/')}/{child.name}" if prefix else f"/{child.name}"
                visit(child, child_prefix)
        else:
            file_path = prefix or f"/{current.name}"
            if file_path not in results:
                results.append(file_path)

    visit(root_dir, url_prefix)
    return results


def build_discovery_path_hints(entry_path: str, workspace_root: Path | None = None, include_local_sources: bool = True) -> list[str]:
    hints = []
    normalized_entry = normalize_crawl_path(entry_path)
    if normalized_entry:
        hints.append(normalized_entry)

    if not include_local_sources:
        return hints

    workspace_root = workspace_root or Path(__file__).resolve().parents[1]
    hint_sources = [
        (workspace_root / "ngrok_tunneling_this_has_port_to_INTERNET", "/"),
        (workspace_root / "non_interactive", "/non_interactive"),
    ]

    for root_dir, prefix in hint_sources:
        for path in collect_local_tree_paths(root_dir, prefix):
            normalized = normalize_crawl_path(path)
            if normalized not in hints:
                hints.append(normalized)
    return hints


def build_file_tree_lines(files: list[dict[str, Any]], entry_path: str = "/", extra_paths: list[str] | None = None) -> list[str]:
    paths = sorted({str(item.get("path", "")).strip() for item in files if item.get("path")})
    if not paths:
        if entry_path and entry_path != "/":
            paths = [entry_path]
        else:
            return ["/"]
    normalized_entry = normalize_crawl_path(entry_path) if entry_path else "/"
    collapsed_to_root_and_entry = len(paths) <= 2 and all(path in {"/", normalized_entry} for path in paths)
    if extra_paths and (len(paths) <= 1 or collapsed_to_root_and_entry):
        paths = sorted(set(paths + [str(path).strip() for path in extra_paths if str(path).strip()]))
    if paths == ["/"] and entry_path and entry_path != "/":
        paths = sorted(set(paths + [entry_path]))

    tree: dict[str, Any] = {}
    for path in paths:
        cleaned = path.strip("/")
        if not cleaned:
            continue
        node = tree
        for part in [chunk for chunk in cleaned.split("/") if chunk]:
            node = node.setdefault(part, {})

    lines = ["/"]

    def emit(node: dict[str, Any], prefix: str) -> None:
        keys = sorted(node.keys())
        for idx, key in enumerate(keys):
            is_last = idx == len(keys) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{key}")
            child_prefix = f"{prefix}{'    ' if is_last else '│   '}"
            emit(node[key], child_prefix)

    emit(tree, "")
    return lines


def build_network_notes(addresses: list[str], ports: list[dict[str, Any]]) -> list[str]:
    open_ports = [int(item.get("port", 0)) for item in ports if str(item.get("state", "")).lower() == "open"]
    notes = []
    if addresses:
        notes.append("Those IPs are normal for ngrok/public edge infrastructure.")
        notes.append("Multiple IPs for one hostname is expected because of load balancing and failover.")
        if any(addr.startswith("3.") or addr.startswith("18.") for addr in addresses):
            notes.append("The 3.x and 18.x ranges are commonly cloud-hosted edge addresses (often AWS region pools).")
    if 80 in open_ports:
        notes.append("Port 80 means HTTP is reachable.")
    if 443 in open_ports:
        notes.append("Port 443 means HTTPS is reachable.")
    if 80 in open_ports or 443 in open_ports:
        notes.append("For an internet-exposed tunnel, seeing 80 and 443 open is expected behavior.")
    deduped = []
    for note in notes:
        if note not in deduped:
            deduped.append(note)
    return deduped


def build_risk_summary(files: list[dict[str, Any]], addresses: list[str], ports: list[dict[str, Any]]) -> list[dict[str, str]]:
    open_ports = [int(item.get("port", 0)) for item in ports if str(item.get("state", "")).lower() == "open"]
    rows: list[dict[str, str]] = []
    for file_row in files[:15]:
        path = str(file_row.get("path") or "")
        risk = "low"
        note = "Static content path discovered."
        if path.endswith("/"):
            risk = "medium"
            note = "Directory listing or index path is reachable; review what is exposed."
        if ".git" in path or ".env" in path:
            risk = "high"
            note = "Potential sensitive configuration path."
        rows.append({"type": "path", "value": path, "risk": risk, "note": note})

    for addr in addresses[:10]:
        rows.append(
            {
                "type": "ip",
                "value": addr,
                "risk": "low",
                "note": "Edge address used by CDN/tunnel infrastructure.",
            }
        )

    for port in open_ports:
        note = "Encrypted web endpoint." if port == 443 else "Web endpoint is reachable without TLS."
        rows.append(
            {
                "type": "port",
                "value": str(port),
                "risk": "medium" if port == 80 else "low",
                "note": note,
            }
        )
    return rows


def build_probe_cases(profile: str, entry_path: str = "/") -> list[ProbeCase]:
    sensitive_paths = [
        "/.git/config",
        "/.env",
        "/wp-admin",
        "/phpmyadmin",
        "/admin",
        "/api/stats",
        "/api/status",
    ]
    traversal_paths = [
        "/..%2f..%2f..%2fwindows/win.ini",
        "/..%2f..%2f..%2fetc/passwd",
        "/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
        "/..\\..\\..\\windows\\system32\\drivers\\etc\\hosts",
    ]

    cases: list[ProbeCase] = [
        ProbeCase("baseline", "homepage", "GET", entry_path, 200, 399),
        ProbeCase("methods", "trace-root", "TRACE", entry_path, 400, 599),
        ProbeCase("methods", "put-root", "PUT", entry_path, 400, 599, body=b"test"),
        ProbeCase("methods", "delete-root", "DELETE", entry_path, 400, 599),
    ]

    for path in sensitive_paths:
        cases.append(ProbeCase("sensitive-path", f"probe-{path}", "GET", path, 400, 599))

    for path in traversal_paths:
        cases.append(ProbeCase("path-traversal", f"traversal-{path}", "GET", path, 400, 599))

    if profile in {"standard", "aggressive"}:
        long_value = "A" * 3000
        cases.extend(
            [
                ProbeCase(
                    "headers",
                    "oversized-header",
                    "GET",
                    entry_path,
                    200,
                    599,
                    headers={"X-Audit-Long": long_value},
                ),
                ProbeCase(
                    "headers",
                    "bot-user-agent",
                    "GET",
                    entry_path,
                    400,
                    599,
                    headers={"User-Agent": "python-requests/2.31.0"},
                ),
            ]
        )

    if profile == "aggressive":
        cases.extend(
            [
                ProbeCase("methods", "head-root", "HEAD", entry_path, 200, 599),
                ProbeCase("methods", "options-root", "OPTIONS", entry_path, 200, 599),
                ProbeCase("headers", "cache-control-root", "GET", entry_path, 200, 599, headers={"Cache-Control": "no-cache"}),
            ]
        )

    return cases


def build_invasive_cases(entry_path: str = "/", profile: str = "standard") -> list[ProbeCase]:
    target_path = normalize_crawl_path(entry_path) or "/"
    sql_like = f"{target_path}?id=1%27%20OR%201%3D1--"
    xss_like = f"{target_path}?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
    cases: list[ProbeCase] = [
        ProbeCase("injection-pattern", "sql-like-query", "GET", sql_like, 200, 599),
        ProbeCase("injection-pattern", "xss-like-query", "GET", xss_like, 200, 599),
    ]

    if profile == "aggressive":
        cases.extend(
            [
                ProbeCase("injection-pattern", "sql-like-post", "POST", target_path, 200, 599, body=b"id=1' OR '1'='1"),
                ProbeCase("injection-pattern", "json-injection-like", "POST", target_path, 200, 599, headers={"Content-Type": "application/json"}, body=b'{"q":"<script>alert(1)</script>"}'),
            ]
        )

    return cases


def classify_finding(case: ProbeCase, response: dict[str, Any]) -> dict[str, Any] | None:
    status = int(response.get("status", 0))
    if case.expected_min_status <= status <= case.expected_max_status:
        return None

    severity = "medium"
    if case.category in {"sensitive-path", "path-traversal", "methods"}:
        severity = "high"

    message = f"Unexpected status {status} for {case.method} {case.path}"
    if status == 0:
        details = str(response.get("sample") or "network error")
        message = f"No response for {case.method} {case.path}: {details}"

    return {
        "severity": severity,
        "category": case.category,
        "name": case.name,
        "path": case.path,
        "method": case.method,
        "status": status,
        "expected": f"{case.expected_min_status}-{case.expected_max_status}",
        "message": message,
    }


def run_probe_suite(
    base_url: str,
    profile: str,
    timeout: float,
    entry_path: str = "/",
    allow_outside_origin: bool = False,
    status_hook: Callable[..., None] | None = None,
    profile_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile_options = dict(profile_options or get_discovery_profile_options(profile))
    user_agents = list(profile_options.get("user_agents") or COMMON_USER_AGENTS)
    jitter_seconds = tuple(profile_options.get("jitter_seconds") or (0.0, 0.0))
    cases = build_probe_cases(profile, entry_path=entry_path)
    results = []
    findings = []

    total = max(1, len(cases))

    for idx, case in enumerate(cases, start=1):
        url = join_url(base_url, case.path)
        response = send_request(
            url,
            method=case.method,
            headers=case.headers,
            body=case.body,
            timeout=timeout,
            allowed_origin=base_url,
            allow_outside_origin=allow_outside_origin,
            user_agents=user_agents,
            jitter_seconds=jitter_seconds,
        )
        result = {
            "category": case.category,
            "name": case.name,
            "method": case.method,
            "path": case.path,
            "url": url,
            "status": response.get("status"),
            "latency_ms": response.get("latency_ms"),
            "error": response.get("sample") if int(response.get("status", 0)) == 0 else None,
        }
        results.append(result)
        finding = classify_finding(case, response)
        if finding:
            findings.append(finding)
        if status_hook:
            progress = 50 + int((idx / total) * 25)
            status_hook(
                "probe",
                progress,
                f"Probe {idx}/{total}: {case.method} {case.path}",
                category=case.category,
                name=case.name,
                path=case.path,
                status=response.get("status"),
                latency_ms=response.get("latency_ms"),
                finding=bool(finding),
            )

    return {
        "count": len(results),
        "results": results,
        "findings": findings,
    }


def run_invasive_suite(
    base_url: str,
    profile: str,
    timeout: float,
    entry_path: str = "/",
    allow_outside_origin: bool = False,
    status_hook: Callable[..., None] | None = None,
) -> dict[str, Any]:
    cases = build_invasive_cases(entry_path=entry_path, profile=profile)
    results = []
    findings = []

    total = max(1, len(cases))

    for idx, case in enumerate(cases, start=1):
        url = join_url(base_url, case.path)
        response = send_request(
            url,
            method=case.method,
            headers=case.headers,
            body=case.body,
            timeout=timeout,
            allowed_origin=base_url,
            allow_outside_origin=allow_outside_origin,
        )
        result = {
            "category": case.category,
            "name": case.name,
            "method": case.method,
            "path": case.path,
            "url": url,
            "status": response.get("status"),
            "latency_ms": response.get("latency_ms"),
            "error": response.get("sample") if int(response.get("status", 0)) == 0 else None,
        }
        results.append(result)
        finding = classify_finding(case, response)
        if finding:
            findings.append(finding)
        if status_hook:
            progress = 90 + int((idx / total) * 8)
            status_hook(
                "invasive",
                progress,
                f"Invasive {idx}/{total}: {case.method} {case.path}",
                category=case.category,
                name=case.name,
                path=case.path,
                status=response.get("status"),
                latency_ms=response.get("latency_ms"),
                finding=bool(finding),
            )

    return {
        "count": len(results),
        "results": results,
        "findings": findings,
    }


def run_rate_limit_burst(
    base_url: str,
    path: str,
    requests_count: int,
    concurrency: int,
    timeout: float,
    allow_outside_origin: bool = False,
    status_hook: Callable[..., None] | None = None,
    profile_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile_options = dict(profile_options or {})
    user_agents = list(profile_options.get("user_agents") or COMMON_USER_AGENTS)
    jitter_seconds = tuple(profile_options.get("jitter_seconds") or (0.0, 0.0))
    url = join_url(base_url, path)
    statuses: list[int] = []

    def do_one(_: int):
        response = send_request(
            url,
            method="GET",
            headers={"User-Agent": choose_user_agent(user_agents)},
            timeout=timeout,
            allowed_origin=base_url,
            allow_outside_origin=allow_outside_origin,
            user_agents=user_agents,
            jitter_seconds=jitter_seconds,
        )
        return int(response.get("status", 0))

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(do_one, idx) for idx in range(requests_count)]
        completed = 0
        progress_step = max(1, requests_count // 10)
        for future in as_completed(futures):
            statuses.append(future.result())
            completed += 1
            if status_hook and (completed == 1 or completed == requests_count or completed % progress_step == 0):
                progress = 80 + int((completed / max(1, requests_count)) * 15)
                status_hook("burst", progress, f"Burst progress {completed}/{requests_count}", completed=completed, total=requests_count)

    histogram: dict[str, int] = {}
    for status in statuses:
        key = str(status)
        histogram[key] = histogram.get(key, 0) + 1

    rate_limited = histogram.get("429", 0)
    finding = None
    if rate_limited == 0:
        finding = {
            "severity": "medium",
            "category": "rate-limit",
            "name": "burst-no-throttling",
            "path": path,
            "method": "GET",
            "status": 200,
            "expected": "at least one 429",
            "message": "Burst test did not trigger any 429 responses.",
        }

    return {
        "url": url,
        "requests": requests_count,
        "concurrency": concurrency,
        "status_histogram": histogram,
        "finding": finding,
    }


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    findings = list(report["probe_suite"].get("findings", []))
    invasive_suite = report.get("invasive_suite") or {}
    invasive_findings = invasive_suite.get("findings", []) if isinstance(invasive_suite, dict) else []
    findings.extend(list(invasive_findings or []))
    burst_finding = report["rate_limit_burst"].get("finding")
    if burst_finding:
        findings.append(burst_finding)

    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for item in findings:
        level = str(item.get("severity", "low")).lower()
        severity_counts[level] = severity_counts.get(level, 0) + 1

    return {
        "total_findings": len(findings),
        "severity": severity_counts,
        "findings": findings,
    }


def print_human_summary(summary: dict[str, Any]):
    print("\n=== Penetration Tool Summary ===")
    print(f"Findings: {summary['total_findings']}")
    print(
        "Severity: "
        f"high={summary['severity'].get('high', 0)} "
        f"medium={summary['severity'].get('medium', 0)} "
        f"low={summary['severity'].get('low', 0)}"
    )

    if not summary["findings"]:
        print("No unexpected responses detected.")
        return

    for idx, item in enumerate(summary["findings"], start=1):
        print(
            f"{idx}. [{item.get('severity','low').upper()}] {item.get('category')} "
            f"{item.get('method')} {item.get('path')} -> {item.get('message')}"
        )


def to_console_safe(text: str, encoding: str | None = None) -> str:
    raw = str(text)
    # Keep tree readability on non-Unicode Windows consoles.
    translated = (
        raw.replace("├", "+")
        .replace("└", "\\")
        .replace("│", "|")
        .replace("─", "-")
    )
    target_encoding = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        translated.encode(target_encoding)
        return translated
    except UnicodeEncodeError:
        return translated.encode(target_encoding, errors="replace").decode(target_encoding, errors="replace")


def print_discovery_summary(discovery: dict[str, Any]):
    print("\n=== Discovery Visualization ===")
    tree_lines = discovery.get("file_tree_lines") or ["/"]
    for line in tree_lines:
        print(to_console_safe(line))

    addresses = discovery.get("addresses") or []
    if addresses:
        print("\nResolved IP Addresses:")
        for addr in addresses:
            print(to_console_safe(f"- {addr}"))

    ports = discovery.get("ports") or []
    if ports:
        print("\nPort Map:")
        for item in ports:
            port = item.get("port", "-")
            state = item.get("state", "unknown")
            detail = item.get("detail") or ""
            print(to_console_safe(f"- {port}: {state}{f' ({detail})' if detail else ''}"))

    notes = discovery.get("network_notes") or []
    if notes:
        print("\nNetwork Explanation:")
        for note in notes:
            print(to_console_safe(f"- {note}"))


def execute_scan(
    target: str,
    profile: str,
    timeout: float,
    burst_path: str,
    burst_requests: int,
    burst_concurrency: int,
    allow_outside_origin: bool = False,
    run_invasive_after_scan: bool = False,
) -> dict[str, Any]:
    base_url, entry_path = parse_target_scope(target)
    started = time.time()

    emit_status_line(
        "init",
        2,
        "Penetration tool initialized",
        target=target,
        restrict_to_origin=not bool(allow_outside_origin),
        origin=base_url,
    )
    emit_status_line("discovery", 10, "Discovery started")

    # Discovery first: reduces impact from later probe/rate-limit noise on crawl quality.
    discovery = run_discovery(
        target,
        base_url,
        entry_path,
        timeout,
        allow_outside_origin=allow_outside_origin,
        status_hook=emit_status_line,
        profile_options=get_discovery_profile_options(profile),
    )
    discovery["file_tree_lines"] = build_file_tree_lines(
        discovery.get("available_files", []),
        entry_path=entry_path,
        extra_paths=discovery.get("path_hints", []),
    )
    discovery["network_notes"] = build_network_notes(discovery.get("addresses", []), discovery.get("ports", []))
    discovery["risk_summary"] = build_risk_summary(
        discovery.get("available_files", []),
        discovery.get("addresses", []),
        discovery.get("ports", []),
    )

    emit_status_line(
        "discovery",
        45,
        "Discovery completed",
        discovered_paths=len(discovery.get("available_files", [])),
        addresses=len(discovery.get("addresses", [])),
    )

    def status_hook(phase: str, progress: int, message: str, **extra):
        emit_status_line(phase, progress, message, **extra)

    emit_status_line("probe", 50, "Probe suite started", total_cases=len(build_probe_cases(profile, entry_path=entry_path)))

    probe_suite = run_probe_suite(
        base_url,
        profile,
        timeout,
        entry_path=entry_path,
        allow_outside_origin=allow_outside_origin,
        status_hook=status_hook,
        profile_options=get_discovery_profile_options(profile),
    )
    emit_status_line(
        "probe",
        75,
        "Probe suite completed",
        probes=probe_suite.get("count", 0),
        findings=len(probe_suite.get("findings", [])),
    )

    emit_status_line("burst", 80, "Rate-limit burst started")
    burst = run_rate_limit_burst(
        base_url,
        path=burst_path or entry_path,
        requests_count=max(1, int(burst_requests)),
        concurrency=max(1, int(burst_concurrency)),
        timeout=timeout,
        allow_outside_origin=allow_outside_origin,
        status_hook=status_hook,
        profile_options=get_discovery_profile_options(profile),
    )
    invasive_suite = None
    if run_invasive_after_scan:
        emit_status_line("invasive", 88, "Invasive suite started", total_cases=len(build_invasive_cases(entry_path=entry_path, profile=profile)))
        invasive_suite = run_invasive_suite(
            base_url,
            profile,
            timeout,
            entry_path=entry_path,
            allow_outside_origin=allow_outside_origin,
            status_hook=status_hook,
        )
        emit_status_line(
            "invasive",
            98,
            "Invasive suite completed",
            probes=invasive_suite.get("count", 0),
            findings=len(invasive_suite.get("findings", [])),
        )
    emit_status_line("finalize", 95, "Compiling report")

    report = {
        "target": target,
        "target_origin": base_url,
        "target_entry_path": entry_path,
        "profile": profile,
        "started_at_epoch": int(started),
        "probe_suite": probe_suite,
        "discovery": discovery,
        "rate_limit_burst": burst,
        "invasive_suite": invasive_suite,
        "runtime": {
            "restrict_to_origin": not bool(allow_outside_origin),
            "allow_outside_origin": bool(allow_outside_origin),
            "duration_seconds": round(time.time() - started, 2),
            "discovered_paths": len(discovery.get("available_files", [])),
            "probe_cases": int(probe_suite.get("count", 0) or 0),
            "probe_findings": len(probe_suite.get("findings", [])),
            "invasive_cases": int(invasive_suite.get("count", 0) if isinstance(invasive_suite, dict) else 0),
            "invasive_findings": len(invasive_suite.get("findings", [])) if isinstance(invasive_suite, dict) else 0,
            "burst_requests": max(1, int(burst_requests)),
            "burst_concurrency": max(1, int(burst_concurrency)),
            "invasive_after_scan": bool(run_invasive_after_scan),
        },
        "nonce": "".join(random.choices(string.ascii_lowercase + string.digits, k=12)),
    }
    report["summary"] = summarize(report)
    emit_status_line("complete", 100, "Penetration tool finished", findings=report["summary"].get("total_findings", 0))
    return report


def main():
    parser = argparse.ArgumentParser(description="Defensive website penetration tool (non-destructive).")
    parser.add_argument("--target", required=True, help="Base target URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--profile", choices=["quick", "standard", "aggressive"], default="standard")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--burst-path", default="/")
    parser.add_argument("--burst-requests", type=int, default=60)
    parser.add_argument("--burst-concurrency", type=int, default=12)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--allow-public-target", action="store_true", help="Allow scanning non-private targets you own.")
    parser.add_argument(
        "--run-invasive-after-scan",
        action="store_true",
        help="Run invasive injection checks after the normal scan phase.",
    )
    parser.add_argument(
        "--allow-outside-origin",
        action="store_true",
        help="Allow requests and redirects outside the target origin (disabled by default).",
    )
    args = parser.parse_args()

    if not is_private_or_loopback_host(args.target) and not args.allow_public_target:
        raise SystemExit("Refusing to scan non-private target without --allow-public-target")

    report = execute_scan(
        target=args.target,
        profile=args.profile,
        timeout=args.timeout,
        burst_path=args.burst_path,
        burst_requests=args.burst_requests,
        burst_concurrency=args.burst_concurrency,
        allow_outside_origin=args.allow_outside_origin,
        run_invasive_after_scan=args.run_invasive_after_scan,
    )

    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_human_summary(report["summary"])
    print_discovery_summary(report.get("discovery", {}))
    print(f"\nPenetration report written: {output_path.resolve()}")


if __name__ == "__main__":
    main()
