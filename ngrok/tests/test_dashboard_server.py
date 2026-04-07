import importlib.util
import io
import json
import time
import tempfile
import unittest
from pathlib import Path

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

    def write_bytes_log(self, content: bytes) -> Path:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
        tmp.close()
        path = Path(tmp.name)
        path.write_bytes(content)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def make_handler(self, path: str):
        handler = object.__new__(DASHBOARD.DashboardHandler)
        handler.path = path
        handler.wfile = io.BytesIO()
        handler.headers = {}
        handler.sent_status = None

        def send_response(status):
            handler.sent_status = status

        def send_header(name, value):
            handler.headers[name] = value

        handler.send_response = send_response
        handler.send_header = send_header
        handler.end_headers = lambda: None
        return handler

    def read_handler_json(self, handler):
        handler.wfile.seek(0)
        return json.loads(handler.wfile.read().decode("utf-8"))

    def restore_attr(self, obj, attr, value):
        original = getattr(obj, attr)
        setattr(obj, attr, value)
        self.addCleanup(lambda: setattr(obj, attr, original))

    def test_parse_access_stats_ignores_malformed_lines(self):
        log = self.write_log(
            "\n"
            "garbage line\n"
            "::1 - - [07/Apr/2026 08:00:01] \"GET /index.html HTTP/1.1\" 200 -\n"
            "::1 - - [07/Apr/2026 08:00:01] \"BREW /tea HTTP/1.1\" 418 -\n"
            "broken \"GET /oops\" 500\n"
            "::1 - - [07/Apr/2026 08:00:01] \"GET /partial\"\n"
            "::1 - - [07/Apr/2026 08:00:02] \"GET /index.html HTTP/1.1\" 304 -\n"
        )
        stats = DASHBOARD.parse_access_stats(log, top=10)
        self.assertEqual(stats["total_requests"], 3)
        self.assertEqual(stats["top_files"][0]["label"], "/index.html")
        self.assertIn("BREW", [item["label"] for item in stats["methods"]])

    def test_parse_access_stats_empty_file(self):
        log = self.write_log("")
        stats = DASHBOARD.parse_access_stats(log, top=10)
        self.assertEqual(stats["total_requests"], 0)
        self.assertEqual(stats["top_files"], [])
        self.assertEqual(stats["recent_requests"], [])

    def test_parse_access_stats_utf8_bom(self):
        log = self.write_log("::1 - - [07/Apr/2026 08:00:01] \"GET /utf8bom.html HTTP/1.1\" 200 -\n", encoding="utf-8-sig")
        stats = DASHBOARD.parse_access_stats(log, top=10)
        self.assertEqual(stats["total_requests"], 1)
        self.assertEqual(stats["top_files"][0]["label"], "/utf8bom.html")

    def test_parse_access_stats_utf16_log(self):
        log = self.write_log(
            "::1 - - [07/Apr/2026 08:00:01] \"GET /utf16.html HTTP/1.1\" 200 -\n",
            encoding="utf-16",
        )
        stats = DASHBOARD.parse_access_stats(log, top=10)
        self.assertEqual(stats["total_requests"], 1)
        self.assertEqual(stats["top_files"][0]["label"], "/utf16.html")

    def test_parse_access_stats_utf16_without_bom(self):
        encoded = "::1 - - [07/Apr/2026 08:00:01] \"GET /utf16le.html HTTP/1.1\" 200 -\n".encode("utf-16-le")
        log = self.write_bytes_log(encoded)
        stats = DASHBOARD.parse_access_stats(log, top=10)
        self.assertEqual(stats["total_requests"], 1)
        self.assertEqual(stats["top_files"][0]["label"], "/utf16le.html")

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

    def test_sort_ip_records_requests_and_sites_asc_desc(self):
        records = [
            {"label": "10.0.0.3", "requests": 2, "sites": 5},
            {"label": "10.0.0.1", "requests": 10, "sites": 1},
            {"label": "10.0.0.2", "requests": 7, "sites": 3},
        ]

        by_requests_desc = DASHBOARD.sort_ip_records(records, sort_by="requests", order="desc")
        by_requests_asc = DASHBOARD.sort_ip_records(records, sort_by="requests", order="asc")
        by_sites_desc = DASHBOARD.sort_ip_records(records, sort_by="sites", order="desc")
        by_sites_asc = DASHBOARD.sort_ip_records(records, sort_by="sites", order="asc")

        self.assertEqual([row["label"] for row in by_requests_desc], ["10.0.0.1", "10.0.0.2", "10.0.0.3"])
        self.assertEqual([row["label"] for row in by_requests_asc], ["10.0.0.3", "10.0.0.2", "10.0.0.1"])
        self.assertEqual([row["label"] for row in by_sites_desc], ["10.0.0.3", "10.0.0.2", "10.0.0.1"])
        self.assertEqual([row["label"] for row in by_sites_asc], ["10.0.0.1", "10.0.0.2", "10.0.0.3"])

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

    def test_parse_access_stats_filter_combinations_and_no_matches(self):
        log = self.write_log(
            "10.0.0.1 - - [07/Apr/2026 08:00:01] \"GET /a HTTP/1.1\" 200 -\n"
            "10.0.0.1 - - [07/Apr/2026 08:00:02] \"GET /b HTTP/1.1\" 404 -\n"
            "10.0.0.2 - - [07/Apr/2026 08:00:03] \"GET /a HTTP/1.1\" 200 -\n"
        )

        only_ip = DASHBOARD.parse_access_stats(log, ip_filter="10.0.0.1")
        only_site = DASHBOARD.parse_access_stats(log, site_filter="/a")
        only_status = DASHBOARD.parse_access_stats(log, status_filter="404")
        combo = DASHBOARD.parse_access_stats(log, ip_filter="10.0.0.1", site_filter="/b", status_filter="404")
        no_matches = DASHBOARD.parse_access_stats(log, ip_filter="10.0.0.9")

        self.assertEqual(only_ip["total_requests"], 2)
        self.assertEqual(only_site["total_requests"], 2)
        self.assertEqual(only_status["total_requests"], 1)
        self.assertEqual(combo["total_requests"], 1)
        self.assertEqual(no_matches["total_requests"], 0)
        self.assertEqual(no_matches["top_files"], [])

    def test_parse_dimensions_collects_values_and_explanations(self):
        log = self.write_log(
            "10.0.0.1 - - [07/Apr/2026 08:00:01] \"GET /a HTTP/1.1\" 200 -\n"
            "10.0.0.2 - - [07/Apr/2026 08:00:02] \"GET /b HTTP/1.1\" 404 -\n"
        )
        dims = DASHBOARD.parse_dimensions(log)
        self.assertEqual(dims["ips"], ["10.0.0.1", "10.0.0.2"])
        self.assertEqual(dims["sites"], ["/a", "/b"])
        self.assertIn("404", dims["status_explanations"])

    def test_parse_dimensions_collects_unique_values(self):
        log = self.write_log(
            "10.0.0.1 - - [07/Apr/2026 08:00:01] \"GET /a HTTP/1.1\" 200 -\n"
            "10.0.0.1 - - [07/Apr/2026 08:00:02] \"GET /a HTTP/1.1\" 404 -\n"
            "10.0.0.2 - - [07/Apr/2026 08:00:03] \"GET /b HTTP/1.1\" 500 -\n"
        )
        dims = DASHBOARD.parse_dimensions(log)
        self.assertEqual(dims["ips"], ["10.0.0.1", "10.0.0.2"])
        self.assertEqual(dims["sites"], ["/a", "/b"])
        self.assertEqual(dims["statuses"], ["200", "404", "500"])
        self.assertEqual(dims["status_explanations"]["500"], DASHBOARD.STATUS_EXPLANATIONS["500"])

    def test_stats_integrity_and_snapshot_payload(self):
        log = self.write_log(
            "10.0.0.1 - - [07/Apr/2026 08:00:01] \"GET /a HTTP/1.1\" 200 -\n"
            "10.0.0.1 - - [07/Apr/2026 08:00:02] \"GET /b HTTP/1.1\" 404 -\n"
            "10.0.0.2 - - [07/Apr/2026 08:00:03] \"GET /b HTTP/1.1\" 404 -\n"
            "10.0.0.3 - - [07/Apr/2026 08:00:04] \"GET /c HTTP/1.1\" 500 -\n"
        )
        stats = DASHBOARD.parse_access_stats(log, top=10, ip_sort="requests", ip_order="desc")
        self.assertEqual(stats["total_requests"], 4)
        self.assertEqual(sum(item["value"] for item in stats["top_files"]), 4)
        self.assertEqual(stats["top_files"][0]["label"], "/b")
        self.assertEqual(stats["status_families"], [{"label": "2xx", "value": 1}, {"label": "4xx", "value": 2}, {"label": "5xx", "value": 1}])

    def test_recent_requests_are_limited_and_newest_first(self):
        lines = []
        for index in range(30):
            second = index % 60
            lines.append(f"10.0.0.{index % 3 + 1} - - [07/Apr/2026 08:00:{second:02d}] \"GET /req{index}.html HTTP/1.1\" 200 -")
        log = self.write_log("\n".join(lines) + "\n")
        stats = DASHBOARD.parse_access_stats(log, top=10)
        recent = stats["recent_requests"]
        self.assertEqual(len(recent), 25)
        self.assertEqual(recent[0]["path"], "/req29.html")
        self.assertEqual(recent[-1]["path"], "/req5.html")
        self.assertEqual(set(recent[0].keys()), {"ip", "dt", "method", "path", "status"})

    def test_large_log_input_parses_without_crashing(self):
        lines = [
            f"10.0.0.{index % 5 + 1} - - [07/Apr/2026 08:{(index // 60) % 60:02d}:{index % 60:02d}] \"GET /bulk{index % 11}.html HTTP/1.1\" 200 -"
            for index in range(50000)
        ]
        log = self.write_log("\n".join(lines) + "\n")
        start = time.perf_counter()
        stats = DASHBOARD.parse_access_stats(log, top=10)
        elapsed = time.perf_counter() - start
        self.assertEqual(stats["total_requests"], 50000)
        self.assertLess(elapsed, 10.0)

    def test_api_stats_route_returns_expected_json_structure(self):
        original = DASHBOARD.parse_access_stats
        self.addCleanup(lambda: setattr(DASHBOARD, "parse_access_stats", original))
        DASHBOARD.parse_access_stats = lambda *args, **kwargs: {
            "total_requests": 1,
            "unique_files": 1,
            "top_files": [{"label": "/a", "value": 1}],
            "status_codes": [{"label": "200", "value": 1}],
            "status_families": [{"label": "2xx", "value": 1}],
            "top_ips": [{"label": "10.0.0.1", "value": 1}],
            "sites_per_ip": [{"label": "10.0.0.1", "value": 1}],
            "ip_stats": [{"label": "10.0.0.1", "requests": 1, "sites": 1}],
            "ip_sort": "requests",
            "ip_order": "desc",
            "filters": {"ip": None, "site": None, "status": None},
            "status_explanations": {"200": "OK - request succeeded."},
            "hourly": [{"label": "2026-Apr-07 08:00", "value": 1}],
            "recent_requests": [{"ip": "10.0.0.1", "dt": "07/Apr/2026:08:00:01", "method": "GET", "path": "/a", "status": "200"}],
        }

        handler = self.make_handler("/api/stats?top=10")
        DASHBOARD.DashboardHandler.do_GET(handler)
        payload = self.read_handler_json(handler)

        self.assertEqual(handler.sent_status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("stats", payload)
        self.assertIn("total_requests", payload["stats"])
        self.assertIn("recent_requests", payload["stats"])

    def test_api_log_and_dimensions_routes_return_expected_structure(self):
        original_read_tail = DASHBOARD.read_tail
        original_parse_dimensions = DASHBOARD.parse_dimensions
        self.addCleanup(lambda: setattr(DASHBOARD, "read_tail", original_read_tail))
        self.addCleanup(lambda: setattr(DASHBOARD, "parse_dimensions", original_parse_dimensions))
        DASHBOARD.read_tail = lambda path, lines=80: ["line-1\n", "line-2\n"]
        DASHBOARD.parse_dimensions = lambda path: {
            "ips": ["10.0.0.1"],
            "sites": ["/a"],
            "statuses": ["200"],
            "status_explanations": {"200": "OK - request succeeded."},
            "status_explanations_all": DASHBOARD.STATUS_EXPLANATIONS,
        }

        log_handler = self.make_handler("/api/log/local?tail=2")
        DASHBOARD.DashboardHandler.do_GET(log_handler)
        self.assertEqual(log_handler.sent_status, 200)
        self.assertEqual(log_handler.wfile.getvalue().decode("utf-8"), "line-1\nline-2\n")

        dimensions_handler = self.make_handler("/api/dimensions")
        DASHBOARD.DashboardHandler.do_GET(dimensions_handler)
        payload = self.read_handler_json(dimensions_handler)
        self.assertEqual(dimensions_handler.sent_status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["dimensions"]["ips"], ["10.0.0.1"])


if __name__ == "__main__":
    unittest.main()
