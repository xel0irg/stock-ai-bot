"""
discord_bot/github_helper.py — Shared GitHub API helpers for Discord commands

Used by /status (reads latest scan artifacts) and /watchlist (reads/writes
the WATCHLIST repository variable). Requires a GitHub Personal Access
Token with repo scope, set as GITHUB_TOKEN in this service's environment
(separate from the bot's own ANTHROPIC_API_KEY etc).

This mirrors the same artifact-fetching logic already used by
dashboard_server.py, kept here as its own module so the Discord service
doesn't need to import the dashboard script directly.
"""
from __future__ import annotations
import os
import json
import io
import zipfile
import math
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from core.logger import get_logger

log = get_logger("GitHubHelper")

GITHUB_REPO  = os.environ.get("GITHUB_REPO", "xel0irg/stock-ai-bot")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


def _gh_request(url: str, method: str = "GET", body: dict | None = None) -> tuple[int, bytes]:
    """Make an authenticated GitHub API request. Returns (status_code, raw_bytes)."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "stock-ai-bot-discord/1.0",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"

    req = Request(url, headers=headers, method=method, data=data)
    try:
        with urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except HTTPError as e:
        return e.code, e.read()
    except URLError as e:
        log.error(f"GitHub API request failed: {e}")
        return 0, b""


def _download_artifact_zip(artifact_id: int) -> bytes | None:
    """
    Download an artifact's zip archive. The GitHub API's /zip endpoint
    returns a 302 redirect to an Azure Blob Storage URL — that redirected
    request must NOT carry the GitHub Authorization header, or Azure
    rejects it with 401 AuthenticationFailed. Python's urlopen follows
    redirects automatically while keeping the original headers, which
    triggers exactly that failure, so this is handled manually instead.
    """
    import urllib.request

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/artifacts/{artifact_id}/zip"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "stock-ai-bot-discord/1.0",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
    }

    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None  # Don't auto-follow — we'll fetch newurl ourselves, auth-free

    opener = urllib.request.build_opener(NoRedirectHandler)
    req = Request(url, headers=headers, method="GET")

    try:
        try:
            resp = opener.open(req, timeout=15)
            # No redirect occurred (unlikely for this endpoint, but handle it)
            return resp.read()
        except HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                redirect_url = e.headers.get("Location")
                if not redirect_url:
                    log.error("Artifact download redirected but no Location header found")
                    return None
                # Fetch the redirect target with NO auth header at all
                plain_req = Request(redirect_url, method="GET")
                with urlopen(plain_req, timeout=20) as resp2:
                    return resp2.read()
            log.error(f"Artifact download failed: {e.code} — {e.read()[:200]}")
            return None
    except URLError as e:
        log.error(f"Artifact download network error: {e}")
        return None


def _sanitize(obj):
    """Recursively replace NaN/Inf floats with None so JSON stays valid."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    return obj


def fetch_latest_reports() -> list[dict]:
    """
    Fetch the latest analysis-reports artifact from GitHub Actions and
    return one report dict per ticker (most recent per ticker).
    Same logic as dashboard_server.py's fetch_from_github().
    """
    status, raw = _gh_request(
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/artifacts?per_page=10"
    )
    if status != 200:
        log.warning(f"Artifact list fetch failed: {status}")
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    artifacts = [
        a for a in data.get("artifacts", [])
        if a.get("name", "").startswith("analysis-reports") and not a.get("expired", False)
    ]
    if not artifacts:
        return []

    latest = sorted(artifacts, key=lambda a: a.get("created_at", ""), reverse=True)[0]
    zip_bytes = _download_artifact_zip(latest["id"])
    if not zip_bytes:
        log.warning("Artifact download failed — see previous log line for detail")
        return []

    reports = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            json_files = [f for f in zf.namelist() if f.endswith(".json")]
            ticker_files: dict[str, str] = {}
            for fname in json_files:
                parts = Path(fname).stem.split("_")
                if len(parts) < 3:
                    continue
                ticker = parts[0]
                if ticker not in ticker_files or fname > ticker_files[ticker]:
                    ticker_files[ticker] = fname

            for ticker, fname in ticker_files.items():
                try:
                    raw_text = zf.read(fname).decode("utf-8")
                    raw_text = raw_text.replace(": NaN", ": null").replace(":NaN", ":null")
                    report = json.loads(raw_text)
                    reports.append(_sanitize(report))
                except (json.JSONDecodeError, KeyError):
                    continue
    except zipfile.BadZipFile:
        return []

    reports.sort(key=lambda r: r.get("confluence_score") or 0, reverse=True)
    return reports


def get_watchlist_variable() -> tuple[str | None, bool]:
    """
    Read the current WATCHLIST repository variable value.
    Returns (value, ok) where:
      - (value, True)  — successfully read an existing value
      - (None, True)   — variable doesn't exist yet (404) — NOT an error,
                          just means it's never been created/migrated yet
      - (None, False)  — a real failure (auth, network, permissions, etc)
    """
    status, raw = _gh_request(
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/variables/WATCHLIST"
    )
    if status == 404:
        log.info("WATCHLIST variable doesn't exist yet — will be created on first write")
        return None, True

    if status != 200:
        log.warning(f"WATCHLIST variable read failed: {status} — {raw[:200]}")
        return None, False

    try:
        return json.loads(raw).get("value"), True
    except json.JSONDecodeError:
        return None, False


def set_watchlist_variable(new_value: str) -> bool:
    """Update the WATCHLIST repository variable. Creates it if it doesn't exist yet."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/variables/WATCHLIST"
    status, raw = _gh_request(url, method="PATCH", body={"name": "WATCHLIST", "value": new_value})

    if status == 404:
        # Variable doesn't exist yet — create it instead
        create_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/variables"
        status, raw = _gh_request(create_url, method="POST", body={"name": "WATCHLIST", "value": new_value})

    if status in (200, 201, 204):
        return True

    log.error(f"WATCHLIST variable write failed: {status} — {raw[:200]}")
    return False
