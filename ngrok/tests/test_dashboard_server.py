import importlib.util
import subprocess
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "dashboard_server.py"
SPEC = importlib.util.spec_from_file_location("dashboard_server", MODULE_PATH)
DASHBOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DASHBOARD)


class DashboardServerRobustnessTests(unittest.TestCase):
    def write_log(self, content: str, encoding: str = "utf-8") -> Path:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
        tmp.close()
        path = Path(tmp.name)
        path.write_text(content, encoding=encoding)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def test_parse_access_stats_ignores_malformed_lines(self):
        log = self.write_log(
            "garbage line\n"
            "::1 - - [07/Apr/2026 08:00:01] \"GET /index.html HTTP/1.1\" 200 -\n"
            "broken \"GET /oops\" 500\n"
            "::1 - - [07/Apr/2026 08:00:02] \"GET /index.html HTTP/1.1\" 304 -\n"
        )
        stats = DASHBOARD.parse_access_stats(log, top=10)
        self.assertEqual(stats["total_requests"], 2)
        self.assertEqual(stats["top_files"][0]["label"], "/index.html")

    def test_parse_access_stats_utf16_log(self):
        log = self.write_log(
            "::1 - - [07/Apr/2026 08:00:01] \"GET /utf16.html HTTP/1.1\" 200 -\n",
            encoding="utf-16",
        )
        stats = DASHBOARD.parse_access_stats(log, top=10)
        self.assertEqual(stats["total_requests"], 1)
        self.assertEqual(stats["top_files"][0]["label"], "/utf16.html")

    def test_sort_ip_records_by_ip_asc(self):
        records = [
            {"label": "10.0.0.3", "requests": 2, "sites": 1},
            {"label": "10.0.0.1", "requests": 100, "sites": 3},
            {"label": "10.0.0.2", "requests": 50, "sites": 2},
        ]
        sorted_records = DASHBOARD.sort_ip_records(records, sort_by="ip", order="asc")
        labels = [item["label"] for item in sorted_records]
        self.assertEqual(labels, ["10.0.0.1", "10.0.0.2", "10.0.0.3"])

    def test_sort_ip_records_invalid_sort_falls_back_to_requests(self):
        records = [
            {"label": "A", "requests": 1, "sites": 100},
            {"label": "B", "requests": 10, "sites": 1},
        ]
        sorted_records = DASHBOARD.sort_ip_records(records, sort_by="not-a-key", order="desc")
        self.assertEqual(sorted_records[0]["label"], "B")

    def test_parse_access_stats_ip_sort_by_sites(self):
        log = self.write_log(
            "10.0.0.1 - - [07/Apr/2026 08:00:01] \"GET /a HTTP/1.1\" 200 -\n"
            "10.0.0.1 - - [07/Apr/2026 08:00:02] \"GET /b HTTP/1.1\" 200 -\n"
            "10.0.0.2 - - [07/Apr/2026 08:00:03] \"GET /a HTTP/1.1\" 200 -\n"
            "10.0.0.2 - - [07/Apr/2026 08:00:04] \"GET /a HTTP/1.1\" 304 -\n"
        )
        stats = DASHBOARD.parse_access_stats(log, top=10, ip_sort="sites", ip_order="desc")
        self.assertEqual(stats["top_ips"][0]["label"], "10.0.0.1")
        self.assertEqual(stats["sites_per_ip"][0]["value"], 2)

    def test_parse_access_stats_filters_ip_site_status(self):
        log = self.write_log(
            "10.0.0.1 - - [07/Apr/2026 08:00:01] \"GET /a HTTP/1.1\" 200 -\n"
            "10.0.0.1 - - [07/Apr/2026 08:00:02] \"GET /b HTTP/1.1\" 404 -\n"
            "10.0.0.2 - - [07/Apr/2026 08:00:03] \"GET /a HTTP/1.1\" 200 -\n"
        )
        stats = DASHBOARD.parse_access_stats(
            log,
            top=10,
            ip_filter="10.0.0.1",
            site_filter="/b",
            status_filter="404",
        )
        self.assertEqual(stats["total_requests"], 1)
        self.assertEqual(stats["top_files"][0]["label"], "/b")
        self.assertIn("404", stats["status_explanations"])

    def test_parse_requested_int_enforces_bounds(self):
        with self.assertRaises(ValueError):
            DASHBOARD.parse_requested_int({"top": ["0"]}, "top", 20, 1, 100)
        with self.assertRaises(ValueError):
            DASHBOARD.parse_requested_int({"tail": ["501"]}, "tail", 80, 1, 500)

    def test_get_access_stats_cached_avoids_repeated_parsing(self):
        log = self.write_log(
            "10.0.0.1 - - [07/Apr/2026 08:00:01] \"GET /a HTTP/1.1\" 200 -\n"
            "10.0.0.2 - - [07/Apr/2026 08:00:02] \"GET /b HTTP/1.1\" 404 -\n"
        )
        calls = {"count": 0}

        def fake_parse(*args, **kwargs):
            calls["count"] += 1
            return {"ok": True, "marker": calls["count"]}

        with patch.object(DASHBOARD, "parse_access_stats", side_effect=fake_parse):
            first = DASHBOARD.get_access_stats_cached(log, top=10)
            second = DASHBOARD.get_access_stats_cached(log, top=10)

        self.assertEqual(first, second)
        self.assertEqual(calls["count"], 1)

    def test_get_task_status_is_throttled(self):
        responses = [
            '{"TaskName":"A","State":"Ready"}',
        ]

        def fake_run_powershell(command, timeout=20):
            return type("Proc", (), {"returncode": 0, "stdout": responses[0], "stderr": ""})()

        with DASHBOARD.CACHE_LOCK:
            DASHBOARD.TASK_STATUS_CACHE["ts"] = 0.0
            DASHBOARD.TASK_STATUS_CACHE["payload"] = None

        with patch.object(DASHBOARD, "run_powershell", side_effect=fake_run_powershell) as mock_run:
            first = DASHBOARD.get_task_status()
            second = DASHBOARD.get_task_status()

        self.assertEqual(first, second)
        self.assertEqual(mock_run.call_count, 1)

    def test_parse_dimensions_collects_values_and_explanations(self):
        log = self.write_log(
            "10.0.0.1 - - [07/Apr/2026 08:00:01] \"GET /a HTTP/1.1\" 200 -\n"
            "10.0.0.2 - - [07/Apr/2026 08:00:02] \"GET /b HTTP/1.1\" 404 -\n"
        )
        dims = DASHBOARD.parse_dimensions(log)
        self.assertEqual(dims["ips"], ["10.0.0.1", "10.0.0.2"])
        self.assertEqual(dims["sites"], ["/a", "/b"])
        self.assertIn("404", dims["status_explanations"])

    def test_is_bot_probe_path_detects_api_and_hidden_paths(self):
        self.assertTrue(DASHBOARD.is_bot_probe_path("/api/stats"))
        self.assertTrue(DASHBOARD.is_bot_probe_path("/.git/config"))
        self.assertFalse(DASHBOARD.is_bot_probe_path("/Rhino8_cheat_sheet_timestamps_interactive.html"))

    def test_parse_access_stats_security_metrics_include_rate_limit_and_probes(self):
        log = self.write_log(
            "10.0.0.1 - - [07/Apr/2026 08:00:01] \"GET /api/stats HTTP/1.1\" 404 -\n"
            "10.0.0.1 - - [07/Apr/2026 08:00:02] \"GET /.git/config HTTP/1.1\" 403 -\n"
            "10.0.0.2 - - [07/Apr/2026 08:00:03] \"GET /index.html HTTP/1.1\" 429 -\n"
            "10.0.0.3 - - [07/Apr/2026 08:00:04] \"GET /index.html HTTP/1.1\" 500 -\n"
        )
        stats = DASHBOARD.parse_access_stats(log, top=10)
        security = stats["security"]

        self.assertEqual(security["api_probe_requests"], 1)
        self.assertEqual(security["bot_probe_requests"], 2)
        self.assertEqual(security["suspicious_requests"], 2)
        self.assertEqual(security["rate_limited_requests"], 1)
        self.assertEqual(security["forbidden_requests"], 1)
        self.assertEqual(security["unauthorized_requests"], 0)
        self.assertEqual(security["server_error_requests"], 1)
        self.assertEqual(security["not_found_requests"], 1)
        self.assertEqual(security["unique_probe_ips"], 1)
        self.assertTrue(any(item["label"] == "10.0.0.1" for item in security["top_api_probe_ips"]))

    def test_normalize_request_path_decodes_and_strips_tracking_query(self):
        path = "/Op.fi%20verkkopalvelu%20_%20OP.html?utm_source=x&gclid=abc&lang=fi"
        normalized = DASHBOARD.normalize_request_path(path)
        self.assertEqual(normalized, "/Op.fi verkkopalvelu _ OP.html?lang=fi")

    def test_parse_access_stats_consolidates_encoded_url_variants(self):
        log = self.write_log(
            "10.0.0.1 - - [07/Apr/2026 08:00:01] \"GET /Op.fi%20verkkopalvelu%20_%20OP.html?utm_source=x HTTP/1.1\" 200 -\n"
            "10.0.0.1 - - [07/Apr/2026 08:00:02] \"GET /Op.fi verkkopalvelu _ OP.html?gclid=1 HTTP/1.1\" 200 -\n"
        )
        stats = DASHBOARD.parse_access_stats(log, top=10)
        self.assertEqual(stats["unique_files"], 1)
        self.assertEqual(stats["top_files"][0]["label"], "/Op.fi verkkopalvelu _ OP.html")

    def test_parse_log_rows_returns_structured_rows(self):
        log = self.write_log(
            "10.0.0.1 - - [07/Apr/2026 08:00:01] \"GET /a?utm_source=z HTTP/1.1\" 404 -\n"
        )
        rows = DASHBOARD.parse_log_rows(log, lines=50)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "404")
        self.assertEqual(rows[0]["path"], "/a")
        self.assertIn("geo", rows[0])

    def test_set_ip_rule_and_clear_ip_rule(self):
        result = DASHBOARD.set_ip_rule("block", "203.0.113.2", seconds=120)
        self.assertTrue(result["ok"])
        rules = DASHBOARD.list_ip_rules()
        self.assertTrue(any(item["ip"] == "203.0.113.2" for item in rules))
        cleared = DASHBOARD.clear_ip_rule("203.0.113.2")
        self.assertTrue(cleared["ok"])

    def test_set_ip_rule_rejects_invalid_ip(self):
        result = DASHBOARD.set_ip_rule("block", "invalid-ip", seconds=120)
        self.assertFalse(result["ok"])

    def test_build_attack_scan_command_contains_expected_flags(self):
        cmd = DASHBOARD.build_attack_scan_command(
            target="https://example.com/page.html",
            profile="standard",
            output_path=Path("report.json"),
            burst_requests=90,
            burst_concurrency=12,
            timeout_seconds=9,
            allow_public_target=True,
        )
        self.assertIn("--target", cmd)
        self.assertIn("https://example.com/page.html", cmd)
        self.assertIn("--allow-public-target", cmd)

    def test_compute_attack_scan_process_timeout_scales_with_burst(self):
        low = DASHBOARD.compute_attack_scan_process_timeout(8, burst_requests=20, burst_concurrency=20, profile="quick")
        high = DASHBOARD.compute_attack_scan_process_timeout(8, burst_requests=500, burst_concurrency=10, profile="aggressive")
        self.assertGreaterEqual(low, 60)
        self.assertGreater(high, low)

    def test_run_attack_scan_async_uses_computed_timeout(self):
        completed = threading.Event()
        observed = {"timeout": None}

        class FakeProc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        expected_timeout = DASHBOARD.compute_attack_scan_process_timeout(
            8,
            burst_requests=80,
            burst_concurrency=16,
            profile="standard",
        )

        def fake_run(cmd, capture_output, text, check, timeout):
            observed["timeout"] = timeout
            completed.set()
            return FakeProc()

        with patch.object(DASHBOARD, "ATTACK_SCAN_SCRIPT", MODULE_PATH.parent / "security_attack_simulator.py"):
            with patch.object(DASHBOARD.subprocess, "run", side_effect=fake_run):
                result = DASHBOARD.run_attack_scan_async(
                    target="http://127.0.0.1:8000/index.html",
                    profile="standard",
                    burst_requests=80,
                    burst_concurrency=16,
                    timeout_seconds=8,
                    allow_public_target=False,
                )

        self.assertTrue(result["ok"])
        self.assertTrue(completed.wait(1.0))
        self.assertEqual(observed["timeout"], expected_timeout)

    def test_run_attack_scan_async_timeout_sets_error_state(self):
        timeout_exc = subprocess.TimeoutExpired(cmd=["python", "scan.py"], timeout=3)

        with DASHBOARD.ATTACK_SCAN_LOCK:
            DASHBOARD.ATTACK_SCAN_STATE.update(
                {
                    "running": False,
                    "last_started": None,
                    "last_finished": None,
                    "last_exit_code": None,
                    "last_error": None,
                    "last_target": None,
                    "last_profile": None,
                    "last_output": None,
                }
            )

        with patch.object(DASHBOARD, "ATTACK_SCAN_SCRIPT", MODULE_PATH.parent / "security_attack_simulator.py"):
            with patch.object(DASHBOARD.subprocess, "run", side_effect=timeout_exc):
                result = DASHBOARD.run_attack_scan_async(
                    target="http://127.0.0.1:8000/index.html",
                    profile="standard",
                    burst_requests=80,
                    burst_concurrency=16,
                    timeout_seconds=8,
                    allow_public_target=False,
                )

        self.assertTrue(result["ok"])

        deadline = time.time() + 2.0
        while time.time() < deadline:
            state = DASHBOARD.get_attack_scan_state()
            if not state.get("running"):
                break
            time.sleep(0.01)

        state = DASHBOARD.get_attack_scan_state()
        self.assertFalse(state["running"])
        self.assertEqual(state["last_exit_code"], -1)
        self.assertEqual(state["last_error"], "scan timed out")
        self.assertIsNotNone(state["last_finished"])

    def test_run_attack_scan_async_requires_target(self):
        result = DASHBOARD.run_attack_scan_async(
            target="",
            profile="standard",
            burst_requests=80,
            burst_concurrency=16,
            timeout_seconds=8,
            allow_public_target=True,
        )
        self.assertFalse(result["ok"])

    def test_run_attack_scan_async_reports_missing_script(self):
        original = DASHBOARD.ATTACK_SCAN_SCRIPT
        try:
            DASHBOARD.ATTACK_SCAN_SCRIPT = Path("missing-simulator.py")
            result = DASHBOARD.run_attack_scan_async(
                target="http://127.0.0.1:8000",
                profile="standard",
                burst_requests=80,
                burst_concurrency=16,
                timeout_seconds=8,
                allow_public_target=False,
            )
        finally:
            DASHBOARD.ATTACK_SCAN_SCRIPT = original

        self.assertFalse(result["ok"])

    def test_request_guard_allows_loopback(self):
        allowed, status, _ = DASHBOARD.check_request_guard("127.0.0.1", "python-requests/2.31")
        self.assertTrue(allowed)
        self.assertEqual(status, 200)

    def test_request_guard_blocks_bot_user_agent(self):
        original_block = DASHBOARD.BLOCK_BOT_USER_AGENTS
        try:
            DASHBOARD.BLOCK_BOT_USER_AGENTS = True
            allowed, status, message = DASHBOARD.check_request_guard("203.0.113.10", "Googlebot/2.1")
        finally:
            DASHBOARD.BLOCK_BOT_USER_AGENTS = original_block

        self.assertFalse(allowed)
        self.assertEqual(status, 403)
        self.assertIn("Blocked", message)

    def test_request_guard_rate_limits_when_threshold_exceeded(self):
        original_enabled = DASHBOARD.RATE_LIMIT_ENABLED
        original_limit = DASHBOARD.RATE_LIMIT_REQUESTS_PER_WINDOW
        original_window = DASHBOARD.RATE_LIMIT_WINDOW_SECONDS
        original_ban = DASHBOARD.RATE_LIMIT_BAN_SECONDS
        client_ip = "203.0.113.44"

        try:
            DASHBOARD.RATE_LIMIT_ENABLED = True
            DASHBOARD.RATE_LIMIT_REQUESTS_PER_WINDOW = 2
            DASHBOARD.RATE_LIMIT_WINDOW_SECONDS = 60
            DASHBOARD.RATE_LIMIT_BAN_SECONDS = 60
            with DASHBOARD.REQUEST_GUARD_LOCK:
                DASHBOARD.REQUEST_GUARD.pop(client_ip, None)

            first = DASHBOARD.check_request_guard(client_ip, "Mozilla/5.0")
            second = DASHBOARD.check_request_guard(client_ip, "Mozilla/5.0")
            third = DASHBOARD.check_request_guard(client_ip, "Mozilla/5.0")
        finally:
            DASHBOARD.RATE_LIMIT_ENABLED = original_enabled
            DASHBOARD.RATE_LIMIT_REQUESTS_PER_WINDOW = original_limit
            DASHBOARD.RATE_LIMIT_WINDOW_SECONDS = original_window
            DASHBOARD.RATE_LIMIT_BAN_SECONDS = original_ban
            with DASHBOARD.REQUEST_GUARD_LOCK:
                DASHBOARD.REQUEST_GUARD.pop(client_ip, None)

        self.assertTrue(first[0])
        self.assertTrue(second[0])
        self.assertFalse(third[0])
        self.assertEqual(third[1], 429)

    def test_build_public_target_url_encodes_spaces(self):
        url = DASHBOARD.build_public_target_url(
            "https://example.ngrok.dev/Rhino8_cheat_sheet_timestamps_interactive.html",
            "/cai/CAI_ Collision Awareness Indicator.html",
        )
        self.assertEqual(
            url,
            "https://example.ngrok.dev/cai/CAI_%20Collision%20Awareness%20Indicator.html",
        )

    def test_build_tree_nodes_links_nested_paths(self):
        nodes = DASHBOARD.build_tree_nodes(
            [
                "/",
                "├── Rhino8_cheat_sheet_timestamps_interactive.html",
                "├── cai",
                "│   ├── CAI_ Collision Awareness Indicator.html",
                "│   ├── CAI_ Collision Awareness Indicator_files",
                "│   │   └── css2",
                "│   └── investment_heatmap.html",
                "└── non_interactive",
                "    ├── Rhino8_cheat_sheet.html",
                "    └── rhino8_hotkeys.pdf",
            ],
            "https://example.ngrok.dev/Rhino8_cheat_sheet_timestamps_interactive.html",
        )

        cai_folder = next(item for item in nodes if item.get("name") == "cai")
        nested_file = next(item for item in nodes if item.get("name") == "CAI_ Collision Awareness Indicator.html")
        pdf_file = next(item for item in nodes if item.get("name") == "rhino8_hotkeys.pdf")

        self.assertTrue(cai_folder["is_folder"])
        self.assertEqual(cai_folder["url"], "https://example.ngrok.dev/cai/")
        self.assertEqual(nested_file["url"], "https://example.ngrok.dev/cai/CAI_%20Collision%20Awareness%20Indicator.html")
        self.assertEqual(pdf_file["url"], "https://example.ngrok.dev/non_interactive/rhino8_hotkeys.pdf")

    def test_public_connection_checks_three_public_files(self):
        requested_urls = []

        class FakeResponse:
            def __init__(self, status=200):
                self.status = status

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def getcode(self):
                return self.status

        def fake_urlopen(request, timeout=5.0):
            requested_urls.append(request.full_url)
            return FakeResponse(200)

        with patch.object(DASHBOARD, "urlopen", side_effect=fake_urlopen):
            result = DASHBOARD.test_public_connection(
                "https://example.ngrok.dev/Rhino8_cheat_sheet_timestamps_interactive.html"
            )

        self.assertTrue(result["connected"])
        self.assertEqual(result["required"], 3)
        self.assertEqual(len(result["checks"]), 3)
        self.assertEqual(len(requested_urls), 3)
        self.assertTrue(requested_urls[0].endswith("/Rhino8_cheat_sheet_timestamps_interactive.html"))
        self.assertIn("/cai/CAI_%20Collision%20Awareness%20Indicator.html", requested_urls[1])
        self.assertIn("/Rhino%208%20Interactive%20Cheat%20Sheet%20Manual.pdf", requested_urls[2])

    def test_public_connection_uses_default_target_when_none_provided(self):
        requested_urls = []

        class FakeResponse:
            def __init__(self, status=200):
                self.status = status

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def getcode(self):
                return self.status

        def fake_urlopen(request, timeout=5.0):
            requested_urls.append(request.full_url)
            return FakeResponse(200)

        with patch.object(DASHBOARD, "ATTACK_DEFAULT_TARGET", ""):
            with patch.object(DASHBOARD, "load_attack_report", return_value={"target": ""}):
                with patch.object(DASHBOARD, "urlopen", side_effect=fake_urlopen):
                    result = DASHBOARD.test_public_connection(target=None)

        self.assertTrue(result["connected"])
        self.assertIn("extraterritorial-carlota-ironfisted.ngrok-free.dev", result["target"])


if __name__ == "__main__":
    unittest.main()
