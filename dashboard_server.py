"""
dashboard_server.py — Degėnic dashboard server

Serves the latest scan results as JSON so dashboard.html can display them.
Runs locally AND on Render.com (FastAPI/uvicorn, port from $PORT env var).

Data sources (in priority order):
1. GitHub Actions artifacts — fetches latest run's JSON reports from GitHub API
2. Local logs/ folder — fallback if GitHub fetch fails

Local usage:
    python dashboard_server.py
    Then open http://localhost:7842 in your browser.

Render.com:
    Deployed automatically via render.yaml as a web service.
    Set GITHUB_TOKEN in Render environment variables.
"""
from __future__ import annotations

import io
import json
import math
import os
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

# ── Config ────────────────────────────────────────────────────────────────────
LOG_DIR     = Path(__file__).resolve().parent / "logs"
PORT        = int(os.environ.get("PORT", 7842))
GITHUB_REPO = os.environ.get("GITHUB_REPO", "xel0irg/stock-ai-bot")
ARTIFACT_LOOKBACK = int(os.environ.get("ARTIFACT_LOOKBACK", 30))

def _load_env():
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Degėnic Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# ── Helpers ───────────────────────────────────────────────────────────────────
def sanitize(obj):
    """Recursively replace NaN/Inf floats with None for browser-safe JSON."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(i) for i in obj]
    return obj


def _gh_request(url: str) -> bytes | None:
    headers = {
        "Accept":     "application/vnd.github+json",
        "User-Agent": "degenic-dashboard/1.0",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read()
    except Exception as e:
        print(f"[GitHub API] Request failed: {e}")
        return None


def _download_artifact_zip(artifact_id) -> bytes | None:
    """
    Download artifact zip. GitHub redirects to Azure Blob — the redirect
    must NOT carry the Authorization header or Azure rejects it.
    """
    from urllib.error import HTTPError

    url = (f"https://api.github.com/repos/{GITHUB_REPO}"
           f"/actions/artifacts/{artifact_id}/zip")
    headers = {
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "degenic-dashboard/1.0",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
    }

    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirectHandler)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        try:
            resp = opener.open(req, timeout=15)
            return resp.read()
        except HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                redirect_url = e.headers.get("Location")
                if not redirect_url:
                    return None
                plain_req = urllib.request.Request(redirect_url, method="GET")
                with urllib.request.urlopen(plain_req, timeout=20) as resp2:
                    return resp2.read()
            print(f"[GitHub API] Artifact download failed: {e.code}")
            return None
    except Exception as e:
        print(f"[GitHub API] Network error: {e}")
        return None


def fetch_from_github() -> list[dict]:
    artifacts_url = (f"https://api.github.com/repos/{GITHUB_REPO}"
                     f"/actions/artifacts?per_page=10")
    raw = _gh_request(artifacts_url)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    artifacts = [
        a for a in data.get("artifacts", [])
        if a.get("name", "").startswith("analysis-reports")
        and not a.get("expired", False)
    ]
    if not artifacts:
        print("[GitHub] No valid analysis-reports artifacts found")
        return []

    # Try up to ARTIFACT_LOOKBACK most recent artifacts — skip ones with no
    # ticker reports. The lookback must span quiet stretches (weekends,
    # pre-10:30 and post-2PM scans) where every artifact is empty, so it
    # needs to be generous — 5 was not enough to reach past a weekend.
    sorted_artifacts = sorted(artifacts, key=lambda a: a.get("created_at", ""), reverse=True)
    zip_bytes = None
    latest = None
    skipped = 0
    for candidate in sorted_artifacts[:ARTIFACT_LOOKBACK]:

        zb = _download_artifact_zip(candidate["id"])
        if not zb:
            continue
        # Peek inside — skip if no ticker JSON files
        try:
            import zipfile as _zf, io as _io
            with _zf.ZipFile(_io.BytesIO(zb)) as z:
                has_ticker = any(
                    not Path(f).stem.startswith("SCAN_SUMMARY")
                    and len(Path(f).stem.split("_")) >= 3
                    and f.endswith(".json")
                    for f in z.namelist()
                )
            if has_ticker:
                zip_bytes = zb
                latest = candidate
                break
            skipped += 1
        except Exception:
            zip_bytes = zb
            latest = candidate
            break

    if not zip_bytes or not latest:
        print(f"[GitHub] No artifact with ticker reports in last "
              f"{len(sorted_artifacts[:ARTIFACT_LOOKBACK])} artifacts")
        return []
    print(f"[GitHub] Using artifact: {latest['name']} "
          f"({latest.get('created_at','')}) — skipped {skipped} empty")

    reports = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            json_files = [f for f in zf.namelist() if f.endswith(".json")]
            ticker_files: dict[str, str] = {}
            for fname in json_files:
                stem = Path(fname).stem
                # Skip scan summary files — not ticker reports
                if stem.startswith("SCAN_SUMMARY"):
                    continue
                parts = stem.split("_")
                if len(parts) < 3:
                    continue
                ticker = parts[0]
                if ticker not in ticker_files or fname > ticker_files[ticker]:
                    ticker_files[ticker] = fname
            for ticker, fname in ticker_files.items():
                try:
                    raw_text = zf.read(fname).decode("utf-8")
                    for bad, good in ((": NaN", ": null"), (":NaN", ":null"),
                                      (": Infinity", ": null"), (":Infinity", ":null"),
                                      (": -Infinity", ": null"), (":-Infinity", ":null")):
                        raw_text = raw_text.replace(bad, good)
                    reports.append(sanitize(json.loads(raw_text)))
                except Exception:
                    continue
    except zipfile.BadZipFile:
        print("[GitHub] Bad zip file")
        return []

    reports.sort(key=lambda r: r.get("confluence_score") or 0, reverse=True)
    print(f"[GitHub] Loaded {len(reports)} reports")
    return reports


def fetch_from_local() -> list[dict]:
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
    for path in latest.values():
        try:
            raw = path.read_text(encoding="utf-8")
            for bad, good in ((": NaN", ": null"), (":NaN", ":null")):
                raw = raw.replace(bad, good)
            reports.append(sanitize(json.loads(raw)))
        except Exception:
            continue
    reports.sort(key=lambda r: r.get("confluence_score") or 0, reverse=True)
    return reports


# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict = {"reports": [], "fetched_at": None, "source": "none"}
CACHE_TTL = 300  # 5 minutes


def get_reports(force: bool = False) -> tuple[list[dict], str]:
    global _cache
    now = datetime.utcnow()
    age = (now - _cache["fetched_at"]).total_seconds() if _cache["fetched_at"] else 999
    if not force and age < CACHE_TTL and _cache["reports"]:
        return _cache["reports"], _cache["source"]

    reports = fetch_from_github()
    source  = "github"

    # If GitHub returned nothing but we have a previous good result,
    # keep serving it rather than dropping to the local fallback.
    # A quiet scan period should not blank out the dashboard.
    if not reports and _cache.get("reports") and _cache.get("source") == "github":
        print("[Cache] GitHub returned no reports — serving last known good data")
        _cache["fetched_at"] = now
        return _cache["reports"], "github"

    if not reports:
        reports = fetch_from_local()
        source  = "local"

    _cache = {"reports": reports, "fetched_at": now, "source": source}
    return reports, source


# ── Routes ────────────────────────────────────────────────────────────────────
DASHBOARD_HTML = Path(__file__).resolve().parent / "dashboard.html"


@app.get("/")
async def root():
    """Serve the dashboard HTML."""
    if DASHBOARD_HTML.exists():
        return FileResponse(DASHBOARD_HTML, media_type="text/html")
    return JSONResponse({"error": "dashboard.html not found"}, status_code=404)


@app.get("/latest")
async def latest(force: bool = Query(False)):
    """Return latest scan reports as JSON."""
    reports, source = get_reports(force=force)
    return {"reports": reports, "source": source, "count": len(reports)}


@app.get("/health")
async def health():
    """Health check — used by Render to confirm service is alive."""
    reports, source = get_reports()
    return {"status": "ok", "reports": len(reports), "source": source,
            "repo": GITHUB_REPO, "token_set": bool(GITHUB_TOKEN)}


# ── Local dev entry point ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print(f"""
╔══════════════════════════════════════════════╗
║        Degėnic — Dashboard Server           ║
╚══════════════════════════════════════════════╝
  Repo:   {GITHUB_REPO}
  Port:   {PORT}
  URL:    http://localhost:{PORT}
  Token:  {'✅ set' if GITHUB_TOKEN else '❌ not set — set GITHUB_TOKEN in .env'}
""")
    uvicorn.run("dashboard_server:app", host="0.0.0.0", port=PORT, reload=False)
