import importlib.util
import tempfile
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


if __name__ == "__main__":
    unittest.main()
