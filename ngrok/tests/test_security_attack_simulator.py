import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "security_attack_simulator.py"
SPEC = importlib.util.spec_from_file_location("security_attack_simulator", MODULE_PATH)
SIM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SIM)


class SecurityAttackSimulatorTests(unittest.TestCase):
    def test_is_private_or_loopback_host(self):
        self.assertTrue(SIM.is_private_or_loopback_host("http://127.0.0.1:8000"))
        self.assertTrue(SIM.is_private_or_loopback_host("http://192.168.1.10"))
        self.assertFalse(SIM.is_private_or_loopback_host("https://example.com"))

    def test_normalized_join_url(self):
        self.assertEqual(SIM.join_url("http://127.0.0.1:8000/", "/api/stats"), "http://127.0.0.1:8000/api/stats")

    def test_build_probe_cases_has_required_profiles(self):
        quick = SIM.build_probe_cases("quick", entry_path="/index.html")
        standard = SIM.build_probe_cases("standard", entry_path="/index.html")
        aggressive = SIM.build_probe_cases("aggressive", entry_path="/index.html")
        self.assertGreater(len(standard), len(quick))
        self.assertGreater(len(aggressive), len(standard))
        self.assertEqual(quick[0].path, "/index.html")

    def test_build_invasive_cases_use_entry_path(self):
        cases = SIM.build_invasive_cases("/cai/index.html", profile="aggressive")
        self.assertTrue(any(case.path.startswith("/cai/index.html?") for case in cases))
        self.assertTrue(any(case.method == "POST" for case in cases))

    def test_parse_target_scope_accepts_full_page_url(self):
        base, entry = SIM.parse_target_scope("https://example.com/path/page.html?x=1")
        self.assertEqual(base, "https://example.com")
        self.assertEqual(entry, "/path/page.html")

    def test_is_same_origin_url(self):
        self.assertTrue(SIM.is_same_origin_url("https://example.com/a", "https://example.com"))
        self.assertFalse(SIM.is_same_origin_url("https://other.com/a", "https://example.com"))

    def test_path_bloom_filter_tracks_membership(self):
        bloom = SIM.PathBloomFilter(size=256, hash_count=3)
        self.assertNotIn("/cai/index.html", bloom)
        bloom.add("/cai/index.html")
        self.assertIn("/cai/index.html", bloom)

    def test_build_candidate_ports_includes_default_and_common_ports(self):
        ports = SIM.build_candidate_ports("https://example.com/path")
        self.assertIn(443, ports)
        self.assertIn(80, ports)
        self.assertIn(8000, ports)

    def test_extract_link_paths_from_html(self):
        html = '<a href="/a.html">A</a><script src="js/app.js"></script><a href="https://x.com">X</a>'
        links = SIM.extract_link_paths(html, current_path="/")
        self.assertIn("/a.html", links)
        self.assertIn("/js/app.js", links)

    def test_extract_link_paths_resolves_relative_from_directory(self):
        html = '<a href="index.html">Index</a><a href="file.txt">File</a>'
        links = SIM.extract_link_paths(html, current_path="/cai/")
        self.assertIn("/cai/index.html", links)
        self.assertIn("/cai/file.txt", links)

    def test_extract_comment_js_and_meta_paths(self):
        html = """
        <!-- hidden: /admin/panel.html -->
        <meta http-equiv="refresh" content="0; url=/login.html">
        <script>
          fetch('/api/internal/status');
          const hidden = '/assets/app.js';
        </script>
        """
        comment_paths = SIM.extract_html_comment_paths(html, current_path="/")
        meta_paths = SIM.extract_meta_refresh_paths(html, current_path="/")
        js_paths = SIM.extract_js_hint_paths(html, current_path="/")
        self.assertIn("/admin/panel.html", comment_paths)
        self.assertIn("/login.html", meta_paths)
        self.assertIn("/api/internal/status", js_paths)
        self.assertIn("/assets/app.js", js_paths)

    def test_build_file_variants_and_parameter_candidates(self):
        variants = SIM.build_file_variants("/index.php", extensions=["bak", "txt"])
        params = SIM.build_parameter_fuzz_candidates("/profile.php", parameter_names=["id", "debug"], parameter_values=["1", "true"])
        self.assertIn("/index.php.bak", variants)
        self.assertIn("/index.txt", variants)
        self.assertIn("/profile.php?id=1", params)
        self.assertIn("/profile.php?debug=true", params)

    def test_get_discovery_profile_options_changes_by_profile(self):
        quick = SIM.get_discovery_profile_options("quick")
        standard = SIM.get_discovery_profile_options("standard")
        aggressive = SIM.get_discovery_profile_options("aggressive")
        self.assertLess(quick["max_pages"], aggressive["max_pages"])
        self.assertTrue(aggressive["enable_passive_intel"])
        self.assertTrue(aggressive["enable_vhost"])
        self.assertFalse(quick["enable_vhost"])

    def test_normalize_crawl_path(self):
        self.assertEqual(SIM.normalize_crawl_path("js/app.js#x"), "/js/app.js")
        self.assertEqual(SIM.normalize_crawl_path("/"), "/")

    def test_normalize_discovery_token_rejects_metadata_noise(self):
        self.assertIsNone(SIM.normalize_discovery_token("utf-8", current_path="/"))
        self.assertIsNone(SIM.normalize_discovery_token("viewport", current_path="/"))
        self.assertIsNone(SIM.normalize_discovery_token("width=device-width, initial-scale=1", current_path="/"))
        self.assertIsNone(SIM.normalize_discovery_token("text/javascript", current_path="/"))

    def test_extract_discovery_tokens_ignores_generic_content_attribute(self):
        html = '<meta name="viewport" content="width=device-width, initial-scale=1">'
        tokens = SIM.extract_discovery_tokens(html, current_path="/")
        self.assertEqual(tokens, [])

    def test_classify_finding_flags_unexpected_status(self):
        case = SIM.ProbeCase("sensitive-path", "probe", "GET", "/admin", 400, 599)
        finding = SIM.classify_finding(case, {"status": 200})
        self.assertIsNotNone(finding)
        self.assertEqual(finding["severity"], "high")

    def test_classify_finding_status_zero_has_network_message(self):
        case = SIM.ProbeCase("methods", "probe", "TRACE", "/", 400, 599)
        finding = SIM.classify_finding(case, {"status": 0, "sample": "Remote end closed connection"})
        self.assertIn("No response", finding["message"])

    def test_summarize_includes_burst_finding(self):
        report = {
            "probe_suite": {
                "findings": [
                    {"severity": "medium", "category": "x", "name": "a", "path": "/", "method": "GET", "message": "m"}
                ]
            },
            "rate_limit_burst": {
                "finding": {"severity": "high", "category": "rate-limit", "name": "burst", "path": "/", "method": "GET", "message": "m"}
            },
        }
        summary = SIM.summarize(report)
        self.assertEqual(summary["total_findings"], 2)
        self.assertEqual(summary["severity"]["high"], 1)

    def test_generate_directory_candidates(self):
        candidates = SIM.generate_directory_candidates("/cai")
        self.assertIn("/cai/index.html", candidates)
        self.assertIn("/cai/default.html", candidates)
        self.assertTrue(all(c.startswith("/cai/") for c in candidates))
        self.assertTrue(len(candidates) >= 5)

    def test_build_file_tree_lines(self):
        files = [
            {"path": "/"},
            {"path": "/Rhino8_cheat_sheet_timestamps_interactive.html"},
            {"path": "/cai/"},
            {"path": "/cai/CAI_%20Collision%20Awareness%20Indicator.html"},
            {"path": "/cai/CAI_%20Collision%20Awareness%20Indicator_files/css2"},
        ]
        lines = SIM.build_file_tree_lines(files)
        self.assertEqual(lines[0], "/")
        self.assertTrue(any("cai" in line for line in lines))
        self.assertTrue(any("css2" in line for line in lines))

    def test_build_file_tree_lines_falls_back_to_entry_path(self):
        lines = SIM.build_file_tree_lines([], entry_path="/Rhino8_cheat_sheet_timestamps_interactive.html")
        self.assertEqual(lines[0], "/")
        self.assertTrue(any("Rhino8_cheat_sheet_timestamps_interactive.html" in line for line in lines))

    def test_build_file_tree_lines_uses_hints_when_tree_collapses(self):
        lines = SIM.build_file_tree_lines(
            [{"path": "/Rhino8_cheat_sheet_timestamps_interactive.html"}],
            entry_path="/Rhino8_cheat_sheet_timestamps_interactive.html",
            extra_paths=["/cai/", "/assets/", "/non_interactive/"],
        )
        self.assertTrue(any("cai" in line for line in lines))
        self.assertTrue(any("assets" in line for line in lines))
        self.assertTrue(any("non_interactive" in line for line in lines))

    def test_build_file_tree_lines_uses_hints_when_only_root_and_entry_present(self):
        lines = SIM.build_file_tree_lines(
            [
                {"path": "/"},
                {"path": "/Rhino8_cheat_sheet_timestamps_interactive.html"},
            ],
            entry_path="/Rhino8_cheat_sheet_timestamps_interactive.html",
            extra_paths=["/cai/", "/non_interactive/"],
        )
        self.assertTrue(any("cai" in line for line in lines))
        self.assertTrue(any("non_interactive" in line for line in lines))

    def test_build_discovery_path_hints_includes_known_folders(self):
        hints = SIM.build_discovery_path_hints("/Rhino8_cheat_sheet_timestamps_interactive.html")
        self.assertIn("/Rhino8_cheat_sheet_timestamps_interactive.html", hints)
        self.assertIn("/cai/", hints)
        self.assertIn("/non_interactive/", hints)

    def test_build_discovery_path_hints_includes_local_tree_children(self):
        hints = SIM.build_discovery_path_hints("/Rhino8_cheat_sheet_timestamps_interactive.html")
        self.assertIn("/non_interactive/Rhino8_cheat_sheet_timestamps.html", hints)
        self.assertIn("/non_interactive/rhino8_hotkeys.html", hints)
        self.assertIn("/non_interactive/rhino8_hotkeys.pdf", hints)
        self.assertIn("/cai/CAI_ Collision Awareness Indicator.html", hints)
        self.assertIn("/cai/CAI_ Collision Awareness Indicator_files/", hints)
        self.assertIn("/cai/CAI_ Collision Awareness Indicator_files/css2", hints)

    def test_build_discovery_path_hints_skips_local_sources_for_public_scan(self):
        hints = SIM.build_discovery_path_hints(
            "/Rhino8_cheat_sheet_timestamps_interactive.html",
            include_local_sources=False,
        )
        self.assertEqual(hints, ["/Rhino8_cheat_sheet_timestamps_interactive.html"])

    def test_build_discovery_path_hints_detects_and_removes_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            non_interactive = root / "non_interactive"
            non_interactive.mkdir(parents=True, exist_ok=True)
            temp_file = non_interactive / "temp_discovery_check.html"
            temp_file.write_text("<html><body>temp</body></html>", encoding="utf-8")

            hints_with_file = SIM.build_discovery_path_hints(
                "/Rhino8_cheat_sheet_timestamps_interactive.html",
                workspace_root=root,
                include_local_sources=True,
            )
            self.assertIn("/non_interactive/temp_discovery_check.html", hints_with_file)

            temp_file.unlink()

            hints_after_delete = SIM.build_discovery_path_hints(
                "/Rhino8_cheat_sheet_timestamps_interactive.html",
                workspace_root=root,
                include_local_sources=True,
            )
            self.assertNotIn("/non_interactive/temp_discovery_check.html", hints_after_delete)

    def test_build_network_notes_and_risk_summary(self):
        addresses = ["3.125.102.39", "18.158.249.75"]
        ports = [
            {"port": 80, "state": "open"},
            {"port": 443, "state": "open"},
            {"port": 8091, "state": "closed"},
        ]
        files = [{"path": "/cai/"}, {"path": "/index.html"}]
        notes = SIM.build_network_notes(addresses, ports)
        risks = SIM.build_risk_summary(files, addresses, ports)

        self.assertTrue(any("load balancing" in note for note in notes))
        self.assertTrue(any("Port 80" in note for note in notes))
        self.assertTrue(any(row["type"] == "port" and row["value"] == "80" for row in risks))

    def test_build_network_notes_dedupes(self):
        notes = SIM.build_network_notes(["3.125.102.39", "3.125.102.39"], [{"port": 80, "state": "open"}])
        self.assertEqual(len(notes), len(set(notes)))

    def test_response_signature_normalizes_whitespace(self):
        sig1 = SIM.response_signature(" Hello\n\nWorld  ")
        sig2 = SIM.response_signature("hello world")
        self.assertEqual(sig1, sig2)

    def test_discover_available_files_keeps_link_discovered_path_even_with_soft_404_signature(self):
        original_send_request = SIM.send_request

        def fake_send_request(url, **kwargs):
            path = url.split(".dev", 1)[-1]
            if "/__discovery_probe_" in path:
                return {"status": 200, "sample": "GENERIC_PAGE", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            if path == "/":
                return {
                    "status": 200,
                    "sample": '<a href="/cai/">cai</a>',
                    "headers": {"Content-Type": "text/html"},
                    "latency_ms": 1.0,
                }
            if path == "/cai/":
                return {"status": 200, "sample": "GENERIC_PAGE", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            if path == "/Rhino8_cheat_sheet_timestamps_interactive.html":
                return {"status": 200, "sample": "entry", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            return {"status": 404, "sample": "nope", "headers": {"Content-Type": "text/plain"}, "latency_ms": 1.0}

        SIM.send_request = fake_send_request
        try:
            rows = SIM.discover_available_files(
                "https://example.dev",
                "/Rhino8_cheat_sheet_timestamps_interactive.html",
                timeout=1.0,
                max_pages=20,
            )
        finally:
            SIM.send_request = original_send_request

        paths = {row["path"] for row in rows}
        self.assertIn("/cai/", paths)
        self.assertIn("/Rhino8_cheat_sheet_timestamps_interactive.html", paths)

    def test_discover_available_files_expands_directory_candidates(self):
        original_send_request = SIM.send_request

        def fake_send_request(url, **kwargs):
            path = url.split(".dev", 1)[-1]
            if "/__discovery_probe_" in path:
                return {"status": 404, "sample": "probe", "headers": {"Content-Type": "text/plain"}, "latency_ms": 1.0}
            if path == "/":
                return {
                    "status": 200,
                    "sample": '<a href="/cai/">cai</a>',
                    "headers": {"Content-Type": "text/html"},
                    "latency_ms": 1.0,
                }
            if path == "/cai/":
                return {
                    "status": 200,
                    "sample": "Directory listing for /cai/",
                    "headers": {"Content-Type": "text/html"},
                    "latency_ms": 1.0,
                }
            if path == "/cai/index.html":
                return {
                    "status": 200,
                    "sample": "cai index",
                    "headers": {"Content-Type": "text/html"},
                    "latency_ms": 1.0,
                }
            if path == "/Rhino8_cheat_sheet_timestamps_interactive.html":
                return {"status": 200, "sample": "entry", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            return {"status": 404, "sample": "not found", "headers": {"Content-Type": "text/plain"}, "latency_ms": 1.0}

        SIM.send_request = fake_send_request
        try:
            rows = SIM.discover_available_files(
                "https://example.dev",
                "/Rhino8_cheat_sheet_timestamps_interactive.html",
                timeout=1.0,
                max_pages=20,
            )
        finally:
            SIM.send_request = original_send_request

        paths = {row["path"] for row in rows}
        self.assertIn("/cai/index.html", paths)

    def test_discover_available_files_filters_non_link_soft_404_paths(self):
        original_send_request = SIM.send_request

        def fake_send_request(url, **kwargs):
            path = url.split(".dev", 1)[-1]
            if "/__discovery_probe_" in path:
                return {"status": 200, "sample": "GENERIC_PAGE", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            if path in {"/", "/Rhino8_cheat_sheet_timestamps_interactive.html"}:
                return {"status": 200, "sample": "ENTRY_PAGE", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            return {"status": 200, "sample": "GENERIC_PAGE", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}

        SIM.send_request = fake_send_request
        try:
            rows = SIM.discover_available_files(
                "https://example.dev",
                "/Rhino8_cheat_sheet_timestamps_interactive.html",
                timeout=1.0,
                max_pages=20,
            )
        finally:
            SIM.send_request = original_send_request

        paths = {row["path"] for row in rows}
        self.assertIn("/Rhino8_cheat_sheet_timestamps_interactive.html", paths)
        self.assertNotIn("/robots.txt", paths)
        self.assertNotIn("/sitemap.xml", paths)

    def test_discover_available_files_keeps_entry_path_when_no_2xx_paths(self):
        original_send_request = SIM.send_request

        def fake_send_request(url, **kwargs):
            path = url.split(".dev", 1)[-1]
            if "/__discovery_probe_" in path:
                return {"status": 200, "sample": "GENERIC_PAGE", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            if path == "/Rhino8_cheat_sheet_timestamps_interactive.html":
                return {"status": 404, "sample": "not found", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            return {"status": 404, "sample": "not found", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}

        SIM.send_request = fake_send_request
        try:
            rows = SIM.discover_available_files(
                "https://example.dev",
                "/Rhino8_cheat_sheet_timestamps_interactive.html",
                timeout=1.0,
                max_pages=20,
            )
        finally:
            SIM.send_request = original_send_request

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], "/Rhino8_cheat_sheet_timestamps_interactive.html")
        self.assertEqual(rows[0]["status"], 404)

    def test_soft_404_filter_does_not_drop_html_with_links(self):
        original_send_request = SIM.send_request

        def fake_send_request(url, **kwargs):
            path = url.split(".dev", 1)[-1]
            if "/__discovery_probe_" in path:
                return {"status": 200, "sample": "GENERIC_PAGE", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            if path == "/":
                return {
                    "status": 200,
                    "sample": "GENERIC_PAGE<a href='/cai/'>cai</a>",
                    "headers": {"Content-Type": "text/html"},
                    "latency_ms": 1.0,
                }
            if path == "/cai/":
                return {"status": 200, "sample": "cai listing", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            if path == "/Rhino8_cheat_sheet_timestamps_interactive.html":
                return {"status": 200, "sample": "entry", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            return {"status": 404, "sample": "not found", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}

        SIM.send_request = fake_send_request
        try:
            rows = SIM.discover_available_files(
                "https://example.dev",
                "/Rhino8_cheat_sheet_timestamps_interactive.html",
                timeout=1.0,
                max_pages=20,
            )
        finally:
            SIM.send_request = original_send_request

        paths = {row["path"] for row in rows}
        self.assertIn("/", paths)
        self.assertIn("/cai/", paths)

    def test_discover_available_files_keeps_hinted_directory_if_not_soft404(self):
        original_send_request = SIM.send_request

        def fake_send_request(url, **kwargs):
            path = url.split(".dev", 1)[-1]
            if "/__discovery_probe_" in path:
                return {"status": 200, "sample": "GENERIC_PAGE", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            if path == "/cai/":
                return {"status": 200, "sample": "Directory listing for /cai/", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            if path == "/Rhino8_cheat_sheet_timestamps_interactive.html":
                return {"status": 200, "sample": "entry", "headers": {"Content-Type": "text/html"}, "latency_ms": 1.0}
            return {"status": 404, "sample": "not found", "headers": {"Content-Type": "text/plain"}, "latency_ms": 1.0}

        SIM.send_request = fake_send_request
        try:
            rows = SIM.discover_available_files(
                "https://example.dev",
                "/Rhino8_cheat_sheet_timestamps_interactive.html",
                timeout=1.0,
                max_pages=20,
            )
        finally:
            SIM.send_request = original_send_request

        paths = {row["path"] for row in rows}
        self.assertIn("/cai/", paths)

    def test_run_discovery_uses_only_entry_hint_for_public_target(self):
        original_discover_available_files = SIM.discover_available_files
        original_discover_addresses = SIM.discover_addresses
        original_discover_ports = SIM.discover_ports
        original_discover_confirmed_public_paths = SIM.discover_confirmed_public_paths
        try:
            SIM.discover_available_files = lambda *args, **kwargs: [{"path": "/Rhino8_cheat_sheet_timestamps_interactive.html", "status": 200, "latency_ms": 1.0}]
            SIM.discover_addresses = lambda *args, **kwargs: []
            SIM.discover_ports = lambda *args, **kwargs: []
            SIM.discover_confirmed_public_paths = lambda *args, **kwargs: []

            discovery = SIM.run_discovery(
                "https://example.ngrok.dev/Rhino8_cheat_sheet_timestamps_interactive.html",
                "https://example.ngrok.dev",
                "/Rhino8_cheat_sheet_timestamps_interactive.html",
                timeout=1.0,
            )
        finally:
            SIM.discover_available_files = original_discover_available_files
            SIM.discover_addresses = original_discover_addresses
            SIM.discover_ports = original_discover_ports
            SIM.discover_confirmed_public_paths = original_discover_confirmed_public_paths

        self.assertEqual(discovery["path_hints"], ["/Rhino8_cheat_sheet_timestamps_interactive.html"])

    def test_run_discovery_public_adds_confirmed_public_paths(self):
        original_discover_available_files = SIM.discover_available_files
        original_discover_addresses = SIM.discover_addresses
        original_discover_ports = SIM.discover_ports
        original_discover_confirmed_public_paths = SIM.discover_confirmed_public_paths
        try:
            SIM.discover_available_files = lambda *args, **kwargs: [{"path": "/Rhino8_cheat_sheet_timestamps_interactive.html", "status": 200, "latency_ms": 1.0}]
            SIM.discover_addresses = lambda *args, **kwargs: []
            SIM.discover_ports = lambda *args, **kwargs: []
            SIM.discover_confirmed_public_paths = lambda *args, **kwargs: [
                {"path": "/Rhino 8 Interactive Cheat Sheet Manual.pdf", "status": 200, "latency_ms": 1.0},
                {"path": "/cai/CAI_ Collision Awareness Indicator.html", "status": 200, "latency_ms": 1.0},
            ]

            discovery = SIM.run_discovery(
                "https://example.ngrok.dev/Rhino8_cheat_sheet_timestamps_interactive.html",
                "https://example.ngrok.dev",
                "/Rhino8_cheat_sheet_timestamps_interactive.html",
                timeout=1.0,
            )
        finally:
            SIM.discover_available_files = original_discover_available_files
            SIM.discover_addresses = original_discover_addresses
            SIM.discover_ports = original_discover_ports
            SIM.discover_confirmed_public_paths = original_discover_confirmed_public_paths

        hint_set = set(discovery["path_hints"])
        file_set = {row["path"] for row in discovery["available_files"]}
        self.assertIn("/Rhino 8 Interactive Cheat Sheet Manual.pdf", hint_set)
        self.assertIn("/cai/CAI_ Collision Awareness Indicator.html", hint_set)
        self.assertIn("/Rhino 8 Interactive Cheat Sheet Manual.pdf", file_set)
        self.assertIn("/cai/CAI_ Collision Awareness Indicator.html", file_set)

    def test_execute_scan_runs_discovery_before_probe_suite(self):
        order = []
        original_discovery = SIM.run_discovery
        original_probe_suite = SIM.run_probe_suite
        original_rate_limit = SIM.run_rate_limit_burst
        original_invasive_suite = SIM.run_invasive_suite

        def fake_discovery(target_url, base_url, entry_path, timeout, **kwargs):
            order.append("discovery")
            return {"addresses": [], "ports": [], "available_files": [{"path": entry_path}]}

        def fake_probe_suite(base_url, profile, timeout, entry_path="/", **kwargs):
            order.append("probe")
            return {"findings": []}

        def fake_rate_limit(base_url, path, requests_count, concurrency, timeout, **kwargs):
            order.append("burst")
            return {"finding": None}

        def fake_invasive_suite(base_url, profile, timeout, entry_path="/", **kwargs):
            order.append("invasive")
            return {"finding": None, "findings": [], "count": 0}

        SIM.run_discovery = fake_discovery
        SIM.run_probe_suite = fake_probe_suite
        SIM.run_rate_limit_burst = fake_rate_limit
        SIM.run_invasive_suite = fake_invasive_suite
        try:
            report = SIM.execute_scan(
                target="https://example.com/a/b.html",
                profile="standard",
                timeout=8,
                burst_path="/",
                burst_requests=20,
                burst_concurrency=4,
            )
        finally:
            SIM.run_discovery = original_discovery
            SIM.run_probe_suite = original_probe_suite
            SIM.run_rate_limit_burst = original_rate_limit
            SIM.run_invasive_suite = original_invasive_suite

        self.assertEqual(order[:3], ["discovery", "probe", "burst"])
        self.assertIn("discovery", report)
        self.assertIsNone(report.get("invasive_suite"))

    def test_execute_scan_can_run_invasive_after_scan(self):
        order = []
        original_discovery = SIM.run_discovery
        original_probe_suite = SIM.run_probe_suite
        original_rate_limit = SIM.run_rate_limit_burst
        original_invasive_suite = SIM.run_invasive_suite

        def fake_discovery(target_url, base_url, entry_path, timeout, **kwargs):
            order.append("discovery")
            return {"addresses": [], "ports": [], "available_files": [{"path": entry_path}], "path_hints": [entry_path]}

        def fake_probe_suite(base_url, profile, timeout, entry_path="/", **kwargs):
            order.append("probe")
            return {"findings": [], "count": 0}

        def fake_rate_limit(base_url, path, requests_count, concurrency, timeout, **kwargs):
            order.append("burst")
            return {"finding": None}

        def fake_invasive_suite(base_url, profile, timeout, entry_path="/", **kwargs):
            order.append("invasive")
            return {"findings": [{"severity": "medium"}], "count": 2}

        SIM.run_discovery = fake_discovery
        SIM.run_probe_suite = fake_probe_suite
        SIM.run_rate_limit_burst = fake_rate_limit
        SIM.run_invasive_suite = fake_invasive_suite
        try:
            report = SIM.execute_scan(
                target="https://example.com/a/b.html",
                profile="aggressive",
                timeout=8,
                burst_path="/",
                burst_requests=20,
                burst_concurrency=4,
                run_invasive_after_scan=True,
            )
        finally:
            SIM.run_discovery = original_discovery
            SIM.run_probe_suite = original_probe_suite
            SIM.run_rate_limit_burst = original_rate_limit
            SIM.run_invasive_suite = original_invasive_suite

        self.assertEqual(order[:4], ["discovery", "probe", "burst", "invasive"])
        self.assertIsNotNone(report.get("invasive_suite"))
        self.assertEqual(report["runtime"]["invasive_after_scan"], True)

    def test_execute_scan_includes_hinted_tree_when_files_collapse(self):
        original_discovery = SIM.run_discovery
        original_probe_suite = SIM.run_probe_suite
        original_rate_limit = SIM.run_rate_limit_burst

        def fake_discovery(target_url, base_url, entry_path, timeout, **kwargs):
            return {
                "addresses": [],
                "ports": [],
                "available_files": [{"path": entry_path}],
                "path_hints": SIM.build_discovery_path_hints(entry_path),
            }

        def fake_probe_suite(base_url, profile, timeout, entry_path="/", **kwargs):
            return {"findings": []}

        def fake_rate_limit(base_url, path, requests_count, concurrency, timeout, **kwargs):
            return {"finding": None}

        SIM.run_discovery = fake_discovery
        SIM.run_probe_suite = fake_probe_suite
        SIM.run_rate_limit_burst = fake_rate_limit
        try:
            report = SIM.execute_scan(
                target="https://example.com/Rhino8_cheat_sheet_timestamps_interactive.html",
                profile="quick",
                timeout=6,
                burst_path="/",
                burst_requests=10,
                burst_concurrency=2,
            )
        finally:
            SIM.run_discovery = original_discovery
            SIM.run_probe_suite = original_probe_suite
            SIM.run_rate_limit_burst = original_rate_limit

        tree = report["discovery"]["file_tree_lines"]
        self.assertTrue(any("cai" in line for line in tree))
        self.assertTrue(any("non_interactive" in line for line in tree))

    def test_to_console_safe_replaces_tree_glyphs_for_cp1252(self):
        text = "├── folder\n│   └── file"
        safe = SIM.to_console_safe(text, encoding="cp1252")
        self.assertNotIn("├", safe)
        self.assertNotIn("└", safe)
        self.assertNotIn("│", safe)
        self.assertIn("+-- folder", safe)

    def test_to_console_safe_handles_non_encodable_characters(self):
        text = "snowman: \u2603"
        safe = SIM.to_console_safe(text, encoding="cp1252")
        self.assertIn("snowman:", safe)


if __name__ == "__main__":
    unittest.main()
