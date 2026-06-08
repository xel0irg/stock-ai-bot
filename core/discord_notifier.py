"""
core/discord_notifier.py — Discord webhook alert system for Stock AI Bot
Sends rich embed alerts directly to your Discord channel.
"""
from __future__ import annotations
import requests
from datetime import datetime
from typing import Dict, Any

from core.logger import get_logger
from config.settings import CONFLUENCE_THRESHOLD

log = get_logger("Discord")


def _get_bias_color(bias: str) -> int:
    """Discord embed color as integer (hex)."""
    return {
        "BULLISH": 0x00C851,   # Green
        "BEARISH": 0xFF4444,   # Red
        "NEUTRAL": 0xFFBB33,   # Amber
    }.get(bias.upper(), 0x33B5E5)


def _get_bias_emoji(bias: str) -> str:
    return {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias.upper(), "⚪")


def _get_score_bar(score: int) -> str:
    filled = int(score / 100 * 10)
    empty  = 10 - filled
    return "█" * filled + "░" * empty


def _build_embed(
    ticker:  str,
    tech:    Dict[str, Any],
    sent:    Dict[str, Any],
    fund:    Dict[str, Any],
    ai:      Dict[str, Any],
) -> Dict:
    """Build a rich Discord embed from analysis data."""
    ta     = tech.get("technicals", {})
    opts   = tech.get("options_flow", {})
    short  = tech.get("short_interest", {})
    fund_d = fund.get("fundamentals", {})
    earn   = fund.get("earnings", {})
    score  = ai.get("confluence_score", 50)
    bias   = ai.get("suggested_bias", "NEUTRAL")
    emoji  = _get_bias_emoji(bias)
    color  = _get_bias_color(bias)
    bar    = _get_score_bar(score)
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    # Earnings warning
    earn_warning = ""
    if earn.get("earnings_imminent"):
        earn_warning = f"\n⚡ **EARNINGS IN {earn.get('days_to_earnings')} DAYS**"

    # Sentiment
    sent_label = sent.get("overall_label", "neutral").upper()
    mentions   = sent.get("total_mentions", 0)

    # AI analysis — trim to fit Discord field limit (1024 chars)
    analysis = ai.get("analysis") or "AI analysis not available"
    if len(analysis) > 1900:
        analysis = analysis[:1900] + "..."

    embed = {
        "title": f"{emoji}  {ticker} — Stock AI Bot Signal",
        "color": color,
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {"text": f"Stock AI Bot v1.0 • {ts}"},
        "fields": [
            {
                "name": "💰 Price Snapshot",
                "value": (
                    f"**${ta.get('last_price', 'N/A')}**  "
                    f"1D: `{ta.get('return_1d', 0):+.2f}%`  "
                    f"5D: `{ta.get('return_5d', 0):+.2f}%`  "
                    f"20D: `{ta.get('return_20d', 0):+.2f}%`\n"
                    f"_{fund_d.get('company_name', ticker)} | {fund_d.get('sector', 'N/A')}_"
                    f"{earn_warning}"
                ),
                "inline": False,
            },
            {
                "name": "📊 Technical Signals",
                "value": (
                    f"RSI: `{ta.get('rsi', 'N/A')}` ({(ta.get('rsi_signal') or 'N/A').upper()})\n"
                    f"MACD: `{(ta.get('macd_crossover') or 'N/A').upper()}`\n"
                    f"EMAs: `{(ta.get('ema_trend') or 'N/A').upper()}`\n"
                    f"Volume: `{ta.get('volume_ratio', 'N/A')}x` avg ({(ta.get('volume_signal') or 'N/A').upper()})"
                ),
                "inline": True,
            },
            {
                "name": "📈 Options & Shorts",
                "value": (
                    f"{opts.get('summary', 'No options data')}\n"
                    f"{short.get('summary', 'No short data')}"
                ),
                "inline": True,
            },
            {
                "name": "💬 Sentiment",
                "value": (
                    f"**{sent_label}** | {mentions} mentions\n"
                    f"Compound: `{sent.get('overall_compound', 0.0)}`"
                ),
                "inline": True,
            },
            {
                "name": "📋 Fundamentals",
                "value": (
                    f"P/E: `{fund_d.get('pe_ratio', 'N/A')}` | "
                    f"Fwd P/E: `{fund_d.get('forward_pe', 'N/A')}`\n"
                    f"Analyst: `{(fund_d.get('analyst_recommend_key') or 'N/A').upper()}` | "
                    f"Target: `${fund_d.get('analyst_target', 'N/A')}`\n"
                    f"Insider: `{fund.get('insider', {}).get('insider_signal', 'N/A').upper()}`"
                ),
                "inline": True,
            },
            {
                "name": f"🤖 AI Analysis",
                "value": analysis[:1024],
                "inline": False,
            },
            {
                "name": f"🎯 Confluence Score",
                "value": f"`[{bar}] {score}/100`\n**BIAS: {bias}** {emoji}",
                "inline": False,
            },
        ],
    }

    return embed


def send_discord_alert(
    webhook_url: str,
    ticker:      str,
    tech:        Dict[str, Any],
    sent:        Dict[str, Any],
    fund:        Dict[str, Any],
    ai:          Dict[str, Any],
    force:       bool = False,
) -> bool:
    """
    Send a Discord embed alert for a ticker.
    Only sends if confluence score >= threshold, unless force=True.
    Returns True if sent successfully.
    """
    if not webhook_url:
        log.warning("Discord webhook URL not configured — skipping alert")
        return False

    score = ai.get("confluence_score", 50)

    if not force and score < CONFLUENCE_THRESHOLD:
        log.info(f"Discord: {ticker} score {score} below threshold — no alert")
        return False

    embed = _build_embed(ticker, tech, sent, fund, ai)
    payload = {"embeds": [embed]}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        if resp.status_code in (200, 204):
            log.info(f"✅ Discord alert sent for {ticker} (score={score})")
            return True
        else:
            log.error(f"Discord webhook error: {resp.status_code} — {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Discord send failed for {ticker}: {e}")
        return False


def send_discord_test(webhook_url: str) -> bool:
    """Send a test message to verify webhook is working."""
    payload = {
        "embeds": [{
            "title": "✅ Stock AI Bot Connected!",
            "description": "You'll receive rich signal alerts here when high-conviction trades are detected.",
            "color": 0x00C851,
            "footer": {"text": "Stock AI Bot v1.0"},
            "timestamp": datetime.utcnow().isoformat(),
        }]
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        if resp.status_code in (200, 204):
            log.info("✅ Discord test message sent successfully!")
            return True
        else:
            log.error(f"Discord test failed: {resp.status_code} — {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Discord test error: {e}")
        return False
