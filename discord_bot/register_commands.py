"""
discord_bot/register_commands.py — One-time setup to register Discord commands

Run this once (and again any time you change a command definition) to
tell Discord which slash commands your application supports. This uses
Discord's REST API directly — registers GUILD commands (instant) rather
than global commands (which can take up to an hour to propagate).

Usage:
    python -m discord_bot.register_commands

Requires these environment variables to be set:
    DISCORD_BOT_TOKEN       — from Developer Portal > Bot > Token
    DISCORD_APPLICATION_ID  — from Developer Portal > General Information
    DISCORD_GUILD_ID        — right-click your Discord server icon while
                               in Developer Mode > Copy Server ID
"""
from __future__ import annotations
import os
import requests

DISCORD_BOT_TOKEN      = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_APPLICATION_ID = os.environ.get("DISCORD_APPLICATION_ID", "")
DISCORD_GUILD_ID       = os.environ.get("DISCORD_GUILD_ID", "")

SCAN_COMMAND = {
    "name": "scan",
    "description": "Run an on-demand Degënic$ analysis for a ticker",
    "options": [
        {
            "name": "ticker",
            "description": "Stock ticker to analyze, e.g. NVDA",
            "type": 3,  # STRING
            "required": True,
        }
    ],
}

STATUS_COMMAND = {
    "name": "status",
    "description": "Show the latest scan results at a glance (no AI call, instant)",
    "options": [],
}

WATCHLIST_COMMAND = {
    "name": "watchlist",
    "description": "View or update the ticker watchlist (no AI call)",
    "options": [
        {
            "name": "action",
            "description": "What to do with the watchlist",
            "type": 3,  # STRING
            "required": False,
            "choices": [
                {"name": "view",   "value": "view"},
                {"name": "add",    "value": "add"},
                {"name": "remove", "value": "remove"},
                {"name": "set",    "value": "set"},
            ],
        },
        {
            "name": "tickers",
            "description": "Comma-separated tickers, e.g. GOOGL,AMD (required for add/remove/set)",
            "type": 3,  # STRING
            "required": False,
        },
    ],
}

ALL_COMMANDS = [SCAN_COMMAND, STATUS_COMMAND, WATCHLIST_COMMAND]


def register():
    if not all([DISCORD_BOT_TOKEN, DISCORD_APPLICATION_ID, DISCORD_GUILD_ID]):
        print("❌ Missing required env vars: DISCORD_BOT_TOKEN, DISCORD_APPLICATION_ID, DISCORD_GUILD_ID")
        return

    url = (
        f"https://discord.com/api/v10/applications/{DISCORD_APPLICATION_ID}"
        f"/guilds/{DISCORD_GUILD_ID}/commands"
    )
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}

    for command in ALL_COMMANDS:
        resp = requests.post(url, headers=headers, json=command, timeout=15)
        if resp.status_code in (200, 201):
            print(f"✅ /{command['name']} registered successfully!")
        else:
            print(f"❌ /{command['name']} registration failed: {resp.status_code}")
            print(resp.text)


if __name__ == "__main__":
    register()
