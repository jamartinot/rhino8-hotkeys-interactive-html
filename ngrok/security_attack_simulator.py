import argparse
import http.client
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
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, quote
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT = 8.0
DEFAULT_OUTPUT = Path("attack-simulation-report.json")


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
) -> dict[str, Any]:
    last_error = "network error"
    last_latency = 0.0
    attempts = max(1, int(retries))

    for attempt in range(attempts):
        req = Request(url=url, method=method.upper(), headers=headers or {}, data=body)
        start = time.perf_counter()
        try:
            with urlopen(req, timeout=timeout) as response:
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


def discover_ports(hostname: str, ports: list[int], timeout: float) -> list[dict[str, Any]]:
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


def discover_available_files(base_url: str, entry_path: str, timeout: float, max_pages: int = 80) -> list[dict[str, Any]]:
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
    queue = [normalize_crawl_path(path) for path in seed_paths]
    seen = set()
    rows = []
    directory_cache = set()
    link_discovered_paths = set()
    entry_observation = None
    crawl_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "close",
    }
    soft_404_signature = ""
    probe_path = f"/__discovery_probe_{int(time.time())}_{random.randint(1000, 9999)}.txt"
    probe_resp = send_request(
        join_url(base_url, probe_path),
        method="GET",
        headers=crawl_headers,
        timeout=timeout,
        read_limit=4096,
        retries=2,
    )
    if int(probe_resp.get("status", 0)) in range(200, 400):
        soft_404_signature = response_signature(probe_resp.get("sample", ""))

    while queue and len(seen) < max(5, int(max_pages)):
        path = normalize_crawl_path(queue.pop(0))
        if path in seen:
            continue
        seen.add(path)

        resp = send_request(
            join_url(base_url, path),
            method="GET",
            headers=crawl_headers,
            timeout=timeout,
            read_limit=262144,
            retries=3,
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
                soft_404_signature
                and response_signature(sample_text) == soft_404_signature
                and path not in link_discovered_paths
                and path not in {"/", entry_path, entry_parent}
                and not has_discovery_hints(sample_text, content_type, path)
            ):
                continue

            rows.append({"path": path, "status": status, "latency_ms": resp.get("latency_ms")})

            
            # If this is HTML, extract links (pass current path for relative URL resolution)
            if "html" in content_type:
                for linked in extract_link_paths(sample_text, current_path=path):
                    candidate = normalize_crawl_path(linked)
                    link_discovered_paths.add(candidate)
                    if candidate not in seen and candidate not in queue:
                        queue.append(candidate)
            
            # Directory paths are still captured in rows. Additional file guesses are avoided
            # here because many public hosts return a generic 200 fallback for unknown paths.
            if path.endswith("/") and path not in directory_cache:
                directory_cache.add(path)

    rows.sort(key=lambda item: item["path"])
    if not rows and entry_observation:
        rows.append(entry_observation)
    return rows


def run_discovery(target_url: str, base_url: str, entry_path: str, timeout: float) -> dict[str, Any]:
    parsed = urlparse(target_url)
    host = parsed.hostname or ""
    addresses = discover_addresses(host) if host else []
    ports = discover_ports(host, build_candidate_ports(target_url), timeout=max(0.5, min(timeout, 3.0))) if host else []
    files = discover_available_files(base_url, entry_path, timeout=max(1.0, timeout))
    return {
        "host": host,
        "addresses": addresses,
        "ports": ports,
        "available_files": files,
        "path_hints": build_discovery_path_hints(entry_path),
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


def build_discovery_path_hints(entry_path: str) -> list[str]:
    hints = []
    normalized_entry = normalize_crawl_path(entry_path)
    if normalized_entry:
        hints.append(normalized_entry)

    workspace_root = Path(__file__).resolve().parents[1]
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
        sql_like = "/?id=1%27%20OR%201%3D1--"
        xss_like = "/?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
        cases.append(ProbeCase("injection-pattern", "sql-like-query", "GET", sql_like, 200, 599))
        cases.append(ProbeCase("injection-pattern", "xss-like-query", "GET", xss_like, 200, 599))

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


def run_probe_suite(base_url: str, profile: str, timeout: float, entry_path: str = "/") -> dict[str, Any]:
    cases = build_probe_cases(profile, entry_path=entry_path)
    results = []
    findings = []

    for case in cases:
        url = join_url(base_url, case.path)
        response = send_request(url, method=case.method, headers=case.headers, body=case.body, timeout=timeout)
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

    return {
        "count": len(results),
        "results": results,
        "findings": findings,
    }


def run_rate_limit_burst(base_url: str, path: str, requests_count: int, concurrency: int, timeout: float) -> dict[str, Any]:
    url = join_url(base_url, path)
    statuses: list[int] = []

    def do_one(_: int):
        response = send_request(url, method="GET", headers={"User-Agent": "attack-simulator/1.0"}, timeout=timeout)
        return int(response.get("status", 0))

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(do_one, idx) for idx in range(requests_count)]
        for future in as_completed(futures):
            statuses.append(future.result())

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
    print("\n=== Attack Simulation Summary ===")
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
) -> dict[str, Any]:
    base_url, entry_path = parse_target_scope(target)
    started = time.time()

    # Discovery first: reduces impact from later probe/rate-limit noise on crawl quality.
    discovery = run_discovery(target, base_url, entry_path, timeout)
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

    probe_suite = run_probe_suite(base_url, profile, timeout, entry_path=entry_path)
    burst = run_rate_limit_burst(
        base_url,
        path=burst_path or entry_path,
        requests_count=max(1, int(burst_requests)),
        concurrency=max(1, int(burst_concurrency)),
        timeout=timeout,
    )

    report = {
        "target": target,
        "target_origin": base_url,
        "target_entry_path": entry_path,
        "profile": profile,
        "started_at_epoch": int(started),
        "probe_suite": probe_suite,
        "discovery": discovery,
        "rate_limit_burst": burst,
        "nonce": "".join(random.choices(string.ascii_lowercase + string.digits, k=12)),
    }
    report["summary"] = summarize(report)
    return report


def main():
    parser = argparse.ArgumentParser(description="Defensive website attack simulator (non-destructive).")
    parser.add_argument("--target", required=True, help="Base target URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--profile", choices=["quick", "standard", "aggressive"], default="standard")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--burst-path", default="/")
    parser.add_argument("--burst-requests", type=int, default=60)
    parser.add_argument("--burst-concurrency", type=int, default=12)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--allow-public-target", action="store_true", help="Allow scanning non-private targets you own.")
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
    )

    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_human_summary(report["summary"])
    print_discovery_summary(report.get("discovery", {}))
    print(f"\nReport written: {output_path.resolve()}")


if __name__ == "__main__":
    main()
