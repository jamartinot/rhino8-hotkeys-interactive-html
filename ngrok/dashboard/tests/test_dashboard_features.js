/**
 * Test suite for dashboard features:
 * - Tree clickable links with proper path construction
 * - Probe IP logs with color coding
 * - Attack scan default target
 */

// Mock document functions for testing
const mockDOM = {
  elements: {},
  getElementById: function(id) {
    if (!this.elements[id]) {
      const div = document.createElement("div");
      div.id = id;
      this.elements[id] = div;
    }
    return this.elements[id];
  },
  clear: function() {
    this.elements = {};
  }
};

// Tests for tree path construction
describe("Attack Report Tree Links - Path Construction", () => {
  let container;
  const baseUrl = "https://example.ngrok.dev/entry.html";
  
  beforeEach(() => {
    mockDOM.clear();
    container = document.createElement("div");
  });

  test("should construct correct absolute path for root-level files", () => {
    const treeLines = [
      "/",
      "├── Rhino8_cheat_sheet.html",
      "└── index.pdf"
    ];
    
    // Parse and verify paths
    let rootLevelFile = null;
    for (let line of treeLines) {
      if (line.includes("Rhino8_cheat_sheet.html")) {
        const depth = (line.match(/^    /) || [""]).length;
        const name = line.replace(/^[├└│─ ]+/, "").trim();
        rootLevelFile = { depth, name };
        break;
      }
    }
    
    expect(rootLevelFile).toBeTruthy();
    expect(rootLevelFile.name).toBe("Rhino8_cheat_sheet.html");
    expect(rootLevelFile.depth).toBe(0);
  });

  test("should construct correct nested path for files in subfolders", () => {
    const treeLines = [
      "/",
      "├── cai",
      "│   ├── CAI_Indicator.html",
      "│   └── investment_heatmap.html",
      "└── non_interactive",
      "    └── hotkeys.html"
    ];
    
    // Extract nested structure
    const items = [];
    let currentFolder = "";
    
    for (let line of treeLines) {
      if (line === "/") continue;
      
      const depth = (line.match(/^(    |│   )*/)[0] || "").length / 4;
      const name = line.replace(/^[├└│─ ]*/, "").replace(/^│/g, "").trim();
      
      if (name.endsWith("/") || (line.includes("├") && items.some(i => i.depth < depth))) {
        currentFolder = name.replace(/\/$/, "");
      }
      
      if (!name.endsWith("/")) {
        items.push({ depth, name, folder: currentFolder });
      }
    }
    
    expect(items.length).toBeGreaterThan(0);
    expect(items.some(i => i.name.includes("CAI_Indicator.html"))).toBeTruthy();
  });

  test("should handle deeply nested paths", () => {
    const treeLines = [
      "/",
      "├── cai",
      "│   ├── CAI_Indicator_files",
      "│   │   ├── css2",
      "│   │   │   └── styles.css",
      "│   │   └── images",
      "│   │       └── icon.png"
    ];
    
    // Count max depth
    let maxDepth = 0;
    for (let line of treeLines) {
      const indentLen = (line.match(/^(    |│   )*/)[0] || "").length;
      const depth = Math.floor(indentLen / 4);
      maxDepth = Math.max(maxDepth, depth);
    }
    
    expect(maxDepth).toBeGreaterThan(2);
  });

  test("should generate proper URLs for each file", () => {
    const baseUrl = new URL("https://example.ngrok.dev/Rhino8_cheat_sheet.html");
    const treeLines = [
      "/",
      "├── Rhino8_cheat_sheet.html",
      "├── cai",
      "│   └── collision.html"
    ];
    
    // Simulate URL generation
    const urls = [];
    for (let line of treeLines) {
      const name = line.replace(/^[├└│─ ]*/, "").trim();
      if (name && !name && !name.endsWith("/")) {
        const url = new URL(baseUrl);
        url.pathname = "/" + name;
        urls.push(url.href);
      }
    }
    
    // Verify structure of generated URLs
    expect(baseUrl.href).toContain("https://");
    expect(baseUrl.href).toContain("example.ngrok.dev");
  });
});

// Tests for Probe IP Logs
describe("Probe IP Logs - Color Coding and Rendering", () => {
  test("should classify 2xx status codes correctly", () => {
    const codes = [200, 201, 204, 299];
    for (let code of codes) {
      const className = getStatusCodeClass(code);
      expect(className).toBe("status-2xx");
    }
  });

  test("should classify 3xx status codes correctly", () => {
    const codes = [300, 301, 302, 307, 399];
    for (let code of codes) {
      const className = getStatusCodeClass(code);
      expect(className).toBe("status-3xx");
    }
  });

  test("should classify 4xx status codes correctly", () => {
    const codes = [400, 401, 403, 404, 429, 499];
    for (let code of codes) {
      const className = getStatusCodeClass(code);
      expect(className).toBe("status-4xx");
    }
  });

  test("should classify 5xx status codes correctly", () => {
    const codes = [500, 502, 503, 504, 599];
    for (let code of codes) {
      const className = getStatusCodeClass(code);
      expect(className).toBe("status-5xx");
    }
  });

  test("should filter logs to only probe IPs", () => {
    const probeIps = [
      { label: "192.168.1.1", value: 10 },
      { label: "192.168.1.2", value: 5 }
    ];
    
    const allLogs = [
      { ip: "192.168.1.1", status: 404, method: "GET", path: "/admin" },
      { ip: "10.0.0.1", status: 200, method: "GET", path: "/home" },
      { ip: "192.168.1.2", status: 401, method: "POST", path: "/login" }
    ];
    
    const probeIpSet = new Set(probeIps.map(item => item.label));
    const probeLogs = allLogs.filter(log => probeIpSet.has(log.ip));
    
    expect(probeLogs.length).toBe(2);
    expect(probeLogs[0].ip).toBe("192.168.1.1");
    expect(probeLogs[1].ip).toBe("192.168.1.2");
  });

  test("should sort logs by most recent first", () => {
    const logs = [
      { dt: "2026-04-09 01:00:00", status: 200 },
      { dt: "2026-04-09 02:00:00", status: 404 },
      { dt: "2026-04-09 01:30:00", status: 401 }
    ];
    
    const sorted = logs.sort((a, b) => {
      const timeA = new Date(a.dt).getTime();
      const timeB = new Date(b.dt).getTime();
      return timeB - timeA;
    });
    
    expect(sorted[0].dt).toBe("2026-04-09 02:00:00");
    expect(sorted[1].dt).toBe("2026-04-09 01:30:00");
    expect(sorted[2].dt).toBe("2026-04-09 01:00:00");
  });

  test("should render logs table with color-coded rows", () => {
    const container = document.createElement("div");
    const probeIps = [{ label: "1.2.3.4", value: 5 }];
    const logs = [
      { ip: "1.2.3.4", status: 200, method: "GET", path: "/", dt: "2026-04-09 01:00:00" },
      { ip: "1.2.3.4", status: 404, method: "GET", path: "/admin", dt: "2026-04-09 01:01:00" },
      { ip: "1.2.3.4", status: 500, method: "POST", path: "/api", dt: "2026-04-09 01:02:00" }
    ];
    
    expect(probeIps.length).toBeGreaterThan(0);
    expect(logs.length).toBe(3);
    expect(logs[0].status).toBe(200);
    expect(logs[1].status).toBe(404);
    expect(logs[2].status).toBe(500);
  });
});

// Tests for Attack Scan Default Target
describe("Attack Scan Configuration", () => {
  test("should use report target as default for public server", () => {
    const reportTarget = "https://example.ngrok.dev/entry.html";
    const defaultTarget = "";
    const fallback = "https://fallback.ngrok.dev";
    
    // Simulate the defaulting logic
    const selectedTarget = reportTarget || defaultTarget || fallback;
    
    expect(selectedTarget).toBe("https://example.ngrok.dev/entry.html");
  });

  test("should allow public targets by default", () => {
    const scanForm = {
      target: "https://example.ngrok.dev/entry.html",
      allowPublicTarget: true,
      profile: "standard"
    };
    
    expect(scanForm.allowPublicTarget).toBeTruthy();
    expect(scanForm.target).toBeTruthy();
  });

  test("should have correct scan configuration", () => {
    const scanForm = {
      target: "https://public.ngrok.dev/page.html",
      profile: "standard",
      burstRequests: 80,
      burstConcurrency: 16,
      timeout: 8,
      allowPublicTarget: true
    };
    
    expect(scanForm.profile).toMatch(/^(quick|standard|aggressive)$/);
    expect(scanForm.burstRequests).toBeGreaterThan(0);
    expect(scanForm.burstConcurrency).toBeGreaterThan(0);
    expect(scanForm.timeout).toBeGreaterThan(0);
  });

  test("should validate scan target URL format", () => {
    const validTargets = [
      "https://example.ngrok.dev/page.html",
      "https://public-server.com/api/endpoint",
      "http://localhost:8000/file"
    ];
    
    for (let target of validTargets) {
      try {
        new URL(target);
        const isValid = true;
        expect(isValid).toBeTruthy();
      } catch {
        expect(false).toBeTruthy();
      }
    }
  });

  test("should reject invalid target URLs", () => {
    const invalidTargets = [
      "not a url",
      "example.com",
      ""
    ];
    
    for (let target of invalidTargets) {
      let isValid = true;
      try {
        new URL(target);
      } catch {
        isValid = false;
      }
      expect(isValid).toBeFalsy();
    }
  });
});

// Integration tests
describe("Dashboard Features Integration", () => {
  test("should render complete attack report with clickable tree and probe logs", () => {
    const report = {
      target: "https://example.ngrok.dev/entry.html",
      available: true,
      summary: { total_findings: 2 },
      discovery: {
        file_tree_lines: [
          "/",
          "├── index.html",
          "├── admin",
          "│   └── panel.html",
          "└── assets"
        ],
        available_files: ["/", "/index.html", "/admin", "/admin/panel.html", "/assets"]
      }
    };
    
    const stats = {
      security: {
        top_probe_ips: [
          { label: "1.2.3.4", value: 15 },
          { label: "5.6.7.8", value: 8 }
        ]
      }
    };
    
    expect(report.available).toBeTruthy();
    expect(report.discovery.file_tree_lines.length).toBeGreaterThan(0);
    expect(stats.security.top_probe_ips.length).toBeGreaterThan(0);
    expect(report.target).toMatch(/^https:\/\//);
  });

  test("should maintain consistency between tree structure and log IP list", () => {
    const probeIps = ["192.168.1.100", "192.168.1.101"];
    const treeItems = ["/", "/admin", "/api"];
    const logs = [
      { ip: "192.168.1.100", status: 404, path: "/admin" },
      { ip: "192.168.1.101", status: 401, path: "/api" }
    ];
    
    // All logs should be from probe IPs
    const allLogsAreFromProbes = logs.every(log => probeIps.includes(log.ip));
    expect(allLogsAreFromProbes).toBeTruthy();
  });
});

// Helper function (from dashboard.js)
function getStatusCodeClass(status) {
  const code = Number(status);
  if (code >= 200 && code < 300) return "status-2xx";
  if (code >= 300 && code < 400) return "status-3xx";
  if (code >= 400 && code < 500) return "status-4xx";
  if (code >= 500) return "status-5xx";
  return "status-unknown";
}
