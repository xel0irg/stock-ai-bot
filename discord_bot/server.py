"""
discord_bot/server.py — Discord HTTP Interactions endpoint for /scan command

This is a small FastAPI service deployed to Render.com (free tier).
It does NOT maintain a persistent Discord Gateway connection — Discord
calls this HTTP endpoint directly whenever someone types /scan in the
server, which is exactly what lets this run on a free, sleep-when-idle
hosting tier instead of needing a 24/7 process.

Flow:
  1. Discord sends a PING to verify the endpoint is alive -> respond PONG
  2. Discord sends the /scan interaction -> we verify its signature,
     immediately reply with a "thinking..." deferred response (required
     within 3 seconds), then run the full analysis pipeline in the
     background and PATCH the real result back within Discord's
     15-minute interaction token window.

Required environment variables (set in Render dashboard, not .env):
  DISCORD_PUBLIC_KEY   — from Discord Developer Portal > General Information
  DISCORD_APPLICATION_ID — same page
  ANTHROPIC_API_KEY, and all the other secrets main.py needs
"""
from __future__ import annotations
import os
import sys
import asyncio
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import requests
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

# Make the main bot package importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logger import get_logger

log = get_logger("DiscordBot")

app = FastAPI(title="Stock AI Bot — Discord Commands")

DISCORD_PUBLIC_KEY     = os.environ.get("DISCORD_PUBLIC_KEY", "")
DISCORD_APPLICATION_ID = os.environ.get("DISCORD_APPLICATION_ID", "")

# Discord interaction type constants
TYPE_PING                              = 1
TYPE_APPLICATION_COMMAND                = 2
RESPONSE_PONG                          = 1
RESPONSE_DEFERRED_CHANNEL_MESSAGE      = 5
RESPONSE_CHANNEL_MESSAGE_WITH_SOURCE   = 4


def verify_signature(signature: str, timestamp: str, body: bytes) -> bool:
    """Verify the request actually came from Discord using Ed25519."""
    if not DISCORD_PUBLIC_KEY:
        log.error("DISCORD_PUBLIC_KEY not configured — cannot verify requests")
        return False
    try:
        verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))
        verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False


@app.get("/")
async def health():
    """Simple health check — also what keeps cold-start checks happy."""
    return {"status": "ok", "service": "stock-ai-bot-discord"}


@app.post("/interactions")
async def interactions(request: Request, background_tasks: BackgroundTasks):
    signature = request.headers.get("X-Signature-Ed25519", "")
    timestamp = request.headers.get("X-Signature-Timestamp", "")
    body      = await request.body()

    if not verify_signature(signature, timestamp, body):
        raise HTTPException(status_code=401, detail="Invalid request signature")

    data = await request.json()
    interaction_type = data.get("type")

    # Discord's verification ping
    if interaction_type == TYPE_PING:
        return JSONResponse({"type": RESPONSE_PONG})

    # Slash command invoked
    if interaction_type == TYPE_APPLICATION_COMMAND:
        command_name = data.get("data", {}).get("name", "")

        if command_name == "scan":
            options = data.get("data", {}).get("options", [])
            ticker  = next((o["value"] for o in options if o["name"] == "ticker"), None)

            if not ticker:
                return JSONResponse({
                    "type": RESPONSE_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {"content": "⚠️ Please specify a ticker, e.g. `/scan ticker:NVDA`"},
                })

            ticker = ticker.strip().upper()
            interaction_token = data.get("token")

            # Defer immediately (must respond within 3 seconds) — Discord
            # shows "Stock AI Bot is thinking..." while we run the analysis
            background_tasks.add_task(run_scan_and_respond, ticker, interaction_token)
            return JSONResponse({"type": RESPONSE_DEFERRED_CHANNEL_MESSAGE})

        return JSONResponse({
            "type": RESPONSE_CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {"content": f"Unknown command: {command_name}"},
        })

    return JSONResponse({"type": RESPONSE_PONG})


def run_scan_and_respond(ticker: str, interaction_token: str):
    """
    Runs the full analysis pipeline and PATCHes the result back to Discord
    as a followup to the deferred response. Runs in a background task so
    the initial /interactions request can return within Discord's 3s limit.
    """
    try:
        log.info(f"Discord /scan {ticker} — starting analysis...")
        from main import analyze_ticker
        result = analyze_ticker(ticker)

        if result.get("error"):
            content = f"⚠️ Could not complete analysis for **{ticker}**: {result['error']}"
        else:
            content = _format_discord_summary(ticker, result)

        _send_followup(interaction_token, content)
        log.info(f"Discord /scan {ticker} — completed and sent")

    except Exception as e:
        log.error(f"Discord /scan {ticker} failed: {e}")
        _send_followup(interaction_token, f"⚠️ Scan for **{ticker}** failed: {e}")


def _format_discord_summary(ticker: str, result: dict) -> str:
    """Build a compact Discord message summarizing the on-demand scan."""
    ai          = result.get("ai", {})
    trade_setup = ai.get("trade_setup", {}) or {}
    score       = ai.get("confluence_score", "N/A")
    bias        = ai.get("suggested_bias", "N/A")
    contract    = trade_setup.get("contract_type", "NONE")

    emoji = "🟢" if contract == "CALL" else "🔴" if contract == "PUT" else "⚪"

    lines = [
        f"{emoji} **{ticker} — On-Demand Scan**",
        f"Confluence: **{score}/100** | Bias: **{bias}**",
        "",
    ]

    if contract in ("CALL", "PUT"):
        lines.append(f"**{contract}** | {trade_setup.get('expiry', 'N/A')} | Strike: ${trade_setup.get('strike', 'N/A')}")
        lines.append(f"Stock target: ${trade_setup.get('stock_target', 'N/A')}")
        lines.append(f"Entry: {trade_setup.get('entry_condition', 'N/A')}")
        lines.append(f"Stop: {trade_setup.get('stop_rule', 'N/A')}")
    else:
        lines.append("No trade — score below threshold or signals mixed.")

    return "\n".join(lines)


def _send_followup(interaction_token: str, content: str):
    """Send the actual result as a followup message to the deferred interaction."""
    if not DISCORD_APPLICATION_ID:
        log.error("DISCORD_APPLICATION_ID not configured — cannot send followup")
        return

    # Discord embeds/messages have a 2000 char limit on content
    if len(content) > 1900:
        content = content[:1900] + "..."

    url = f"https://discord.com/api/v10/webhooks/{DISCORD_APPLICATION_ID}/{interaction_token}"
    try:
        resp = requests.post(url, json={"content": content}, timeout=15)
        if resp.status_code not in (200, 204):
            log.error(f"Discord followup failed: {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        log.error(f"Discord followup send error: {e}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
