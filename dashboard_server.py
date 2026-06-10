"""
dashboard_server.py — Local server for Stock AI Bot dashboard

Serves the latest scan results as JSON so dashboard.html can display them.

Data sources (in priority order):
1. GitHub Actions artifacts — fetches latest run's JSON reports from GitHub API
   (works for public repos with no token; private repos need GITHUB_TOKEN in .env)
2. Local logs/ folder — fallback if GitHub fetch fails or repo is unavailable

Usage:
    python dashboard_server.py

Then open dashboard.html in your browser.
Default port: 7842
"""
from __future__ import annotations
import json
import math
import io
import os
import zipfile
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError

# ── Config ────────────────────────────────────────────────────────────────────
LOG_DIR    = Path(__file__).resolve().parent / "logs"
PORT       = 7842
GITHUB_REPO = "xel0irg/stock-ai-bot"

# Optional: set GITHUB_TOKEN in .env for private repo access
def _load_env():
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


# ── Sanitize ──────────────────────────────────────────────────────────────────
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


def _gh_request(url: str) -> bytes | None:
    """Make a GitHub API request, returns raw bytes or None on failure."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "stock-ai-bot-dashboard/1.0",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=10) as resp:
            return resp.read()
    except (URLError, Exception) as e:
        print(f"[GitHub API] Request failed: {e}")
        return None


# ── GitHub Artifacts ──────────────────────────────────────────────────────────
def fetch_from_github() -> list[dict]:
    """
    Fetch the latest analysis reports from GitHub Actions artifacts.
    Works on public repos without a token.
    Returns a list of report dicts, or empty list on failure.
    """
    # Step 1: Get list of artifacts for this repo
    artifacts_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/artifacts?per_page=10"
    raw = _gh_request(artifacts_url)
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    artifacts = data.get("artifacts", [])
    if not artifacts:
        return []

    # Step 2: Find the most recent analysis-reports artifact
    analysis_artifacts = [
        a for a in artifacts
        if a.get("name", "").startswith("analysis-reports")
        and not a.get("expired", False)
    ]
    if not analysis_artifacts:
        print("[GitHub] No valid analysis-reports artifacts found")
        return []

    # Sort by created_at descending, pick most recent
    latest = sorted(
        analysis_artifacts,
        key=lambda a: a.get("created_at", ""),
        reverse=True
    )[0]

    artifact_id   = latest["id"]
    artifact_name = latest["name"]
    created_at    = latest.get("created_at", "")
    print(f"[GitHub] Found artifact: {artifact_name} (created {created_at})")

    # Step 3: Download the artifact zip
    download_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/artifacts/{artifact_id}/zip"
    zip_bytes = _gh_request(download_url)
    if not zip_bytes:
        print("[GitHub] Failed to download artifact zip")
        return []

    # Step 4: Extract JSON files from the zip
    reports = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            json_files = [f for f in zf.namelist() if f.endswith(".json")]

            # Group by ticker, keep most recent per ticker
            ticker_files: dict[str, str] = {}
            for fname in json_files:
                parts = Path(fname).stem.split("_")
                if len(parts) < 3:
                    continue
                ticker = parts[0]
                # Use filename as sort key (YYYYMMDD_HHMMSS is lexicographically sortable)
                if ticker not in ticker_files or fname > ticker_files[ticker]:
                    ticker_files[ticker] = fname

            for ticker, fname in ticker_files.items():
                try:
                    raw_text = zf.read(fname).decode("utf-8")
                    raw_text = raw_text.replace(": NaN", ": null").replace(":NaN", ":null")
                    raw_text = raw_text.replace(": Infinity", ": null").replace(":Infinity", ":null")
                    raw_text = raw_text.replace(": -Infinity", ": null").replace(":-Infinity", ":null")
                    report = json.loads(raw_text)
                    reports.append(sanitize(report))
                except (json.JSONDecodeError, KeyError):
                    continue

    except zipfile.BadZipFile:
        print("[GitHub] Bad zip file received")
        return []

    reports.sort(key=lambda r: r.get("confluence_score") or 0, reverse=True)
    print(f"[GitHub] Loaded {len(reports)} reports from artifact")
    return reports


# ── Local fallback ────────────────────────────────────────────────────────────
def fetch_from_local() -> list[dict]:
    """Read the most recent .json report per ticker from local logs/ folder."""
    if not LOG_DIR.exists():
        return []

    latest: dict[str, Path] = {}
    for f in LOG_DIR.glob("*.json"):
        parts = f.stem.split("_")
        if len(parts) < 3:
            continue
        ticker = parts[0]
        if ticker not in latest or f.stat().st_mtime > latest[ticker].stat().st_mtime:
            latest[ticker] = f

    reports = []
    for ticker, path in latest.items():
        try:
            raw = path.read_text(encoding="utf-8")
            raw = raw.replace(": NaN", ": null").replace(":NaN", ":null")
            raw = raw.replace(": Infinity", ": null").replace(":Infinity", ":null")
            raw = raw.replace(": -Infinity", ": null").replace(":-Infinity", ":null")
            data = json.loads(raw)
            reports.append(sanitize(data))
        except (json.JSONDecodeError, OSError):
            continue

    reports.sort(key=lambda r: r.get("confluence_score") or 0, reverse=True)
    return reports


# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict = {"reports": [], "fetched_at": None, "source": "none"}
CACHE_TTL_SECONDS = 300  # 5 minutes


def get_reports() -> tuple[list[dict], str]:
    """Return reports and source label, using cache when fresh."""
    global _cache
    now = datetime.utcnow()
    age = (now - _cache["fetched_at"]).total_seconds() if _cache["fetched_at"] else 999

    if age < CACHE_TTL_SECONDS and _cache["reports"]:
        return _cache["reports"], _cache["source"]

    # Try GitHub first
    reports = fetch_from_github()
    source  = "github"

    # Fall back to local
    if not reports:
        reports = fetch_from_local()
        source  = "local"

    _cache = {"reports": reports, "fetched_at": now, "source": source}
    return reports, source


# ── HTTP Handler ──────────────────────────────────────────────────────────────
class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/latest", "/latest?force=1"):
            force = "force=1" in self.path
            if force:
                _cache["fetched_at"] = None  # invalidate cache
            reports, source = get_reports()
            payload = {"reports": reports, "source": source, "count": len(reports)}
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/health":
            reports, source = get_reports()
            body = json.dumps({"status": "ok", "reports": len(reports), "source": source}).encode()
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
        ts = datetime.now().strftime("%H:%M:%S")
        if args and "/latest" in str(args[0]):
            reports, source = get_reports()
            print(f"[{ts}] Dashboard refresh — {len(reports)} reports (source: {source})")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"""
╔══════════════════════════════════════════════╗
║        Stock AI Bot — Dashboard Server       ║
╚══════════════════════════════════════════════╝

  Repo:        {GITHUB_REPO}
  Local logs:  {LOG_DIR}
  Port:        {PORT}
  Data source: GitHub Actions artifacts (falls back to local)

  Open dashboard.html in your browser.
  Press Ctrl+C to stop.
""")
    server = HTTPServer(("localhost", PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server stopped]")


if __name__ == "__main__":
    main()
