"""
dashboard_server.py — Local server for Stock AI Bot dashboard

Serves the latest scan results as JSON so dashboard.html can display them.
Reads the most recent report per ticker from the logs/ folder.

Usage:
    python dashboard_server.py

Then open dashboard.html in your browser.
Default port: 7842
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

LOG_DIR = Path(__file__).resolve().parent / "logs"
PORT = 7842


def sanitize(obj):
    """Recursively replace NaN/Inf floats with None so JSON is browser-safe."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(i) for i in obj]
    return obj


def get_latest_reports() -> list[dict]:
    """
    For each ticker, find the most recent .json report and return it.
    Returns a list of report dicts sorted by confluence score descending.
    """
    if not LOG_DIR.exists():
        return []

    # Group files by ticker
    latest: dict[str, Path] = {}
    for f in LOG_DIR.glob("*.json"):
        # Filename format: TICKER_YYYYMMDD_HHMMSS.json
        parts = f.stem.split("_")
        if len(parts) < 3:
            continue
        ticker = parts[0]
        # Keep the most recent file per ticker
        if ticker not in latest or f.stat().st_mtime > latest[ticker].stat().st_mtime:
            latest[ticker] = f

    reports = []
    for ticker, path in latest.items():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read()
                # Replace NaN/Infinity before parsing — Python writes these
                # but browsers can't parse them as valid JSON
                raw = raw.replace(': NaN', ': null').replace(':NaN', ':null')
                raw = raw.replace(': Infinity', ': null').replace(':Infinity', ':null')
                raw = raw.replace(': -Infinity', ': null').replace(':-Infinity', ':null')
                data = json.loads(raw)
                reports.append(sanitize(data))
        except (json.JSONDecodeError, OSError):
            continue

    # Sort by confluence score descending
    reports.sort(key=lambda r: r.get("confluence_score") or 0, reverse=True)
    return reports


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # CORS headers so the HTML file can fetch from localhost
        if self.path == "/latest":
            data = get_latest_reports()
            body = json.dumps(data, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/health":
            body = json.dumps({"status": "ok", "reports": len(get_latest_reports())}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def log_message(self, format, *args):
        # Suppress default request logging — keep terminal clean
        ts = datetime.now().strftime("%H:%M:%S")
        if args and "/latest" in str(args[0]):
            print(f"[{ts}] Dashboard refreshed — serving {len(get_latest_reports())} tickers")


def main():
    print(f"""
╔══════════════════════════════════════════════╗
║        Stock AI Bot — Dashboard Server       ║
╚══════════════════════════════════════════════╝

  Serving reports from: {LOG_DIR}
  Dashboard URL:        Open dashboard.html in your browser
  API endpoint:         http://localhost:{PORT}/latest
  Health check:         http://localhost:{PORT}/health

  Press Ctrl+C to stop.
""")
    server = HTTPServer(("localhost", PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server stopped]")


if __name__ == "__main__":
    main()
