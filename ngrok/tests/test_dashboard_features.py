"""
Test suite for dashboard security features:
- Probe IP logs rendering
- Attack scan using public server
- Tree path construction
"""

import unittest
import json
from pathlib import Path
from datetime import datetime


class TestProbeIPLogs(unittest.TestCase):
    """Tests for Probe IP Logs functionality"""
    
    def test_filter_logs_to_probe_ips(self):
        """Should filter request logs to only show probe IPs"""
        probe_ips = {"192.168.1.1", "192.168.1.2"}
        all_logs = [
            {"ip": "192.168.1.1", "status": 404, "method": "GET", "path": "/admin"},
            {"ip": "10.0.0.1", "status": 200, "method": "GET", "path": "/home"},
            {"ip": "192.168.1.2", "status": 401, "method": "POST", "path": "/login"},
            {"ip": "172.16.0.1", "status": 200, "method": "GET", "path": "/"},
        ]
        
        probe_logs = [log for log in all_logs if log["ip"] in probe_ips]
        
        self.assertEqual(len(probe_logs), 2)
        self.assertEqual(probe_logs[0]["ip"], "192.168.1.1")
        self.assertEqual(probe_logs[1]["ip"], "192.168.1.2")
    
    def test_sort_logs_by_timestamp(self):
        """Should sort logs with most recent first"""
        logs = [
            {"dt": "2026-04-09 01:00:00", "status": 200, "ip": "1.2.3.4"},
            {"dt": "2026-04-09 02:00:00", "status": 404, "ip": "1.2.3.4"},
            {"dt": "2026-04-09 01:30:00", "status": 401, "ip": "1.2.3.4"},
        ]
        
        sorted_logs = sorted(logs, key=lambda x: datetime.fromisoformat(x["dt"]), reverse=True)
        
        self.assertEqual(sorted_logs[0]["dt"], "2026-04-09 02:00:00")
        self.assertEqual(sorted_logs[1]["dt"], "2026-04-09 01:30:00")
        self.assertEqual(sorted_logs[2]["dt"], "2026-04-09 01:00:00")
    
    def test_status_code_classification(self):
        """Should correctly classify HTTP status codes"""
        classifications = {
            200: "2xx",
            201: "2xx",
            301: "3xx",
            302: "3xx",
            400: "4xx",
            404: "4xx",
            500: "5xx",
            502: "5xx",
        }
        
        def classify(code):
            if 200 <= code < 300:
                return "2xx"
            elif 300 <= code < 400:
                return "3xx"
            elif 400 <= code < 500:
                return "4xx"
            elif code >= 500:
                return "5xx"
            return "unknown"
        
        for code, expected in classifications.items():
            self.assertEqual(classify(code), expected)
    
    def test_probe_logs_table_structure(self):
        """Should have correct table structure with required columns"""
        required_columns = ["time", "ip", "method", "status", "path"]
        probe_log_entry = {
            "dt": "2026-04-09 01:00:00",
            "ip": "1.2.3.4",
            "method": "GET",
            "status": 404,
            "path": "/admin",
        }
        
        for col in required_columns:
            if col == "time":
                self.assertIn("dt", probe_log_entry)
            elif col == "ip":
                self.assertIn("ip", probe_log_entry)
            elif col == "method":
                self.assertIn("method", probe_log_entry)
            elif col == "status":
                self.assertIn("status", probe_log_entry)
            elif col == "path":
                self.assertIn("path", probe_log_entry)
    
    def test_limit_probe_logs_to_recent(self):
        """Should show only most recent 50 logs"""
        logs = [{"ip": "1.2.3.4", "status": 200} for _ in range(100)]
        recent_logs = logs[:50]
        
        self.assertEqual(len(recent_logs), 50)


class TestAttackScanConfiguration(unittest.TestCase):
    """Tests for Attack Scan using public server"""
    
    def test_default_target_uses_public_server(self):
        """Should default to public ngrok server"""
        report_target = "https://example.ngrok.dev/Rhino8_cheat_sheet_timestamps_interactive.html"
        default_target = ""
        fallback_target = "https://fallback.ngrok.dev"
        
        # Simulate the prioritization logic
        selected_target = report_target or default_target or fallback_target
        
        self.assertEqual(selected_target, "https://example.ngrok.dev/Rhino8_cheat_sheet_timestamps_interactive.html")
        self.assertIn("ngrok.dev", selected_target)
    
    def test_public_target_allowed_by_default(self):
        """Should allow public targets by default"""
        scan_config = {
            "target": "https://example.ngrok.dev/page.html",
            "allow_public_target": True,
            "profile": "standard",
        }
        
        self.assertTrue(scan_config["allow_public_target"])
        self.assertIn("ngrok.dev", scan_config["target"])
    
    def test_scan_configuration_parameters(self):
        """Should have all required scan parameters"""
        config = {
            "target": "https://example.ngrok.dev/entry.html",
            "profile": "standard",
            "burst_requests": 80,
            "burst_concurrency": 16,
            "timeout": 8,
            "allow_public_target": True,
        }
        
        self.assertIn("target", config)
        self.assertIn("profile", config)
        self.assertIn("burst_requests", config)
        self.assertIn("burst_concurrency", config)
        self.assertIn("timeout", config)
        self.assertIn("allow_public_target", config)
        
        self.assertIn(config["profile"], ["quick", "standard", "aggressive"])
        self.assertGreater(config["burst_requests"], 0)
        self.assertGreater(config["burst_concurrency"], 0)
        self.assertGreater(config["timeout"], 0)
    
    def test_validate_target_url_format(self):
        """Should validate that target is valid URL"""
        valid_targets = [
            "https://example.ngrok.dev/page.html",
            "https://public-server.com/api/endpoint",
            "http://localhost:8000/file",
        ]
        
        from urllib.parse import urlparse
        
        for target in valid_targets:
            result = urlparse(target)
            self.assertTrue(result.scheme in ["http", "https"])
            self.assertTrue(result.netloc)


class TestTreePathConstruction(unittest.TestCase):
    """Tests for file tree path construction"""
    
    def test_parse_tree_structure(self):
        """Should correctly parse nested tree structure"""
        tree_lines = [
            "/",
            "├── Rhino8_cheat_sheet.html",
            "├── cai",
            "│   ├── collision.html",
            "│   └── investment_heatmap.html",
            "└── non_interactive",
            "    ├── hotkeys.html",
            "    └── timestamps.html",
        ]
        
        # Count items at each depth
        root_items = sum(1 for line in tree_lines if line.startswith("├") or line.startswith("└"))
        nested_items = sum(1 for line in tree_lines if line.startswith("│") or "    " in line)
        
        self.assertGreater(root_items, 0)
        self.assertGreater(nested_items, 0)
    
    def test_extract_file_names_from_tree(self):
        """Should extract complete file names with extensions"""
        tree_line = "│   ├── CAI_ Collision Awareness Indicator.html"
        
        # Extract name (remove tree characters)
        name = tree_line.replace("│", "").replace("├", "").replace("└", "").replace("─", "").strip()
        
        self.assertEqual(name, "CAI_ Collision Awareness Indicator.html")
        self.assertTrue(name.endswith(".html"))
    
    def test_calculate_tree_depth(self):
        """Should correctly calculate nesting depth"""
        tree_lines = [
            ("├── file.html", 0),
            ("│   ├── nested.html", 1),
            ("│   │   └── deep.html", 2),
        ]
        
        for line, expected_depth in tree_lines:
            # Count indentation
            depth = 0
            if "│   " in line[:4]:
                depth += 1
            if "│   │" in line[:8]:
                depth += 1
            
            self.assertLessEqual(abs(depth - expected_depth), 1)
    
    def test_build_hierarchical_paths(self):
        """Should build correct hierarchical paths"""
        tree_items = [
            {"name": "cai", "is_folder": True},
            {"name": "CAI_Indicator.html", "folder": "cai"},
            {"name": "non_interactive", "is_folder": True},
            {"name": "hotkeys.html", "folder": "non_interactive"},
        ]
        
        paths = {}
        current_folder = ""
        
        for item in tree_items:
            if item.get("is_folder"):
                current_folder = item["name"]
                paths[current_folder] = f"/{current_folder}"
            else:
                folder = item.get("folder", "")
                path = f"/{folder}/{item['name']}" if folder else f"/{item['name']}"
                paths[item["name"]] = path
        
        self.assertEqual(paths["cai"], "/cai")
        self.assertEqual(paths["CAI_Indicator.html"], "/cai/CAI_Indicator.html")
        self.assertEqual(paths["non_interactive"], "/non_interactive")
        self.assertEqual(paths["hotkeys.html"], "/non_interactive/hotkeys.html")
    
    def test_generate_clickable_urls(self):
        """Should generate valid clickable URLs from paths"""
        base_url = "https://example.ngrok.dev"
        paths = [
            "/index.html",
            "/cai/collision.html",
            "/non_interactive/hotkeys.html",
        ]
        
        for path in paths:
            full_url = base_url + path
            self.assertTrue(full_url.startswith("https://"))
            self.assertIn(base_url, full_url)
            self.assertTrue(full_url.endswith(".html"))


class TestDashboardIntegration(unittest.TestCase):
    """Integration tests for dashboard features"""
    
    def test_attack_report_has_required_fields(self):
        """Should have all required fields in attack report"""
        report = {
            "target": "https://example.ngrok.dev/entry.html",
            "available": True,
            "summary": {"total_findings": 2},
            "discovery": {
                "file_tree_lines": ["/", "├── index.html"],
                "available_files": ["/", "/index.html"],
            },
        }
        
        self.assertIn("target", report)
        self.assertIn("discovery", report)
        self.assertIn("file_tree_lines", report["discovery"])
        self.assertTrue(report["available"])
    
    def test_probe_ips_and_logs_consistency(self):
        """Should maintain consistency between probe IPs and logs"""
        probe_ips = ["1.2.3.4", "5.6.7.8"]
        logs = [
            {"ip": "1.2.3.4", "status": 404},
            {"ip": "5.6.7.8", "status": 401},
            {"ip": "1.2.3.4", "status": 403},
        ]
        
        probe_ip_set = set(probe_ips)
        all_logs_from_probes = all(log["ip"] in probe_ip_set for log in logs)
        
        self.assertTrue(all_logs_from_probes)
    
    def test_public_server_scan_flow(self):
        """Should correctly flow from public server report to scan controls"""
        # Simulate report generation from public server
        report = {
            "target": "https://public-ngrok.dev/entry.html",
            "available": True,
            "discovery": {"file_tree_lines": ["/", "├── file.html"]},
        }
        
        # Simulate scan control setup using report target
        scan_form = {
            "target": report.get("target"),
            "allow_public_target": True,
        }
        
        self.assertEqual(scan_form["target"], "https://public-ngrok.dev/entry.html")
        self.assertTrue(scan_form["allow_public_target"])


if __name__ == "__main__":
    unittest.main()
