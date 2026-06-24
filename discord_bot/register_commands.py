"""
discord_bot/register_commands.py — One-time setup to register /scan with Discord

Run this once (and again any time you change the command definition) to
tell Discord that your application supports /scan. This uses Discord's
REST API directly — registers a GUILD command (instant) rather than a
global command (which can take up to an hour to propagate).

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
    "description": "Run an on-demand Stock AI Bot analysis for a ticker",
    "options": [
        {
            "name": "ticker",
            "description": "Stock ticker to analyze, e.g. NVDA",
            "type": 3,  # STRING
            "required": True,
        }
    ],
}


def register():
    if not all([DISCORD_BOT_TOKEN, DISCORD_APPLICATION_ID, DISCORD_GUILD_ID]):
        print("❌ Missing required env vars: DISCORD_BOT_TOKEN, DISCORD_APPLICATION_ID, DISCORD_GUILD_ID")
        return

    url = (
        f"https://discord.com/api/v10/applications/{DISCORD_APPLICATION_ID}"
        f"/guilds/{DISCORD_GUILD_ID}/commands"
    )
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}

    resp = requests.post(url, headers=headers, json=SCAN_COMMAND, timeout=15)

    if resp.status_code in (200, 201):
        print("✅ /scan command registered successfully!")
        print(resp.json())
    else:
        print(f"❌ Registration failed: {resp.status_code}")
        print(resp.text)


if __name__ == "__main__":
    register()
