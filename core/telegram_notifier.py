"""
core/telegram_notifier.py — Telegram alert system for Stock AI Bot
Sends formatted signal alerts directly to your Telegram chat.
"""
from __future__ import annotations
import asyncio
import requests
from datetime import datetime
from typing import Dict, Any

from core.logger import get_logger
from config.settings import CONFLUENCE_THRESHOLD

log = get_logger("Telegram")


def _get_bias_emoji(bias: str) -> str:
    return {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(bias.upper(), "⚪")


def _get_score_bar(score: int) -> str:
    filled = int(score / 100 * 10)
    empty  = 10 - filled
    return "█" * filled + "░" * empty


def _format_alert(
    ticker:  str,
    tech:    Dict[str, Any],
    sent:    Dict[str, Any],
    fund:    Dict[str, Any],
    ai:      Dict[str, Any],
) -> str:
    """Format a clean Telegram message from analysis data."""
    ta     = tech.get("technicals", {})
    opts   = tech.get("options_flow", {})
    short  = tech.get("short_interest", {})
    fund_d = fund.get("fundamentals", {})
    earn   = fund.get("earnings", {})
    score  = ai.get("confluence_score", 50)
    bias   = ai.get("suggested_bias", "NEUTRAL")
    emoji  = _get_bias_emoji(bias)
    bar    = _get_score_bar(score)
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Earnings warning
    earn_line = ""
    if earn.get("earnings_imminent"):
        earn_line = f"\n⚡ *EARNINGS IN {earn.get('days_to_earnings')} DAYS*"

    # Sentiment
    sent_label = sent.get("overall_label", "neutral").upper()
    sent_score = sent.get("overall_compound", 0.0)
    mentions   = sent.get("total_mentions", 0)

    # Options trade setup block
    ts_data  = ai.get("trade_setup", {})
    contract = ts_data.get("contract_type", "NONE")
    quality  = ts_data.get("setup_quality", "NO TRADE")

    if contract != "NONE":
        con_emoji = "🟢" if contract == "CALL" else "🔴"
        qual_emoji = ("🔥" if quality == "HIGH CONVICTION" else
                      "⚠️" if quality == "MODERATE" else "❌")
        strike_str  = f"${ts_data['strike']}" if ts_data.get("strike") else "N/A"
        premium_str = f"${ts_data['est_premium']}" if ts_data.get("est_premium") else "N/A"
        target_str  = f"${ts_data['stock_target']}" if ts_data.get("stock_target") else "N/A"
        profit_str  = f"{ts_data['profit_target']}%" if ts_data.get("profit_target") else "N/A"

        trade_block = (
            f"{con_emoji} *{contract}* | {ts_data.get('expiry', 'N/A')} | "
            f"Strike: `{strike_str}` ({ts_data.get('moneyness', 'N/A')})\n"
            f"Stock target: `{target_str}` | Premium: `{premium_str}`\n"
            f"Profit target: `{profit_str}` | Max loss: `100% of premium`\n"
        )
        if ts_data.get("stop_rule"):
            trade_block += f"Stop: _{ts_data['stop_rule']}_\n"
        if ts_data.get("entry_condition"):
            trade_block += f"Enter: _{ts_data['entry_condition']}_\n"
        if ts_data.get("avoid_if"):
            trade_block += f"⛔ Avoid if: _{ts_data['avoid_if']}_\n"
        if ts_data.get("key_risk"):
            trade_block += f"⚠️ Risk: _{ts_data['key_risk']}_"
        conviction_line = f"{qual_emoji} *{quality}*"
    else:
        trade_block     = "_No trade — signals too mixed or score below threshold._\n_Sit this one out._"
        conviction_line = "❌ *NO TRADE*"

    # AI analysis — first 2 sections only (market scenario + bull/bear)
    analysis = ai.get("analysis", "")
    short_analysis = ""
    if analysis:
        lines = analysis.split("\n")
        preview = []
        section_count = 0
        for line in lines:
            if line.startswith("##"):
                section_count += 1
                if section_count > 2:
                    break
            preview.append(line)
        short_analysis = "\n".join(preview).strip()
        if len(short_analysis) > 800:
            short_analysis = short_analysis[:800] + "..."

    msg = f"""
{emoji} *STOCK AI BOT — {ticker}* {emoji}
`{ts}`{earn_line}

💰 *PRICE*
Last: `${ta.get('last_price', 'N/A')}` | 1D: `{ta.get('return_1d', 0):+.2f}%` | 5D: `{ta.get('return_5d', 0):+.2f}%`
_{fund_d.get('company_name', ticker)} | {fund_d.get('sector', 'N/A')}_

📊 *TECHNICALS*
RSI: `{ta.get('rsi', 'N/A')}` ({ta.get('rsi_signal', 'N/A').upper()})
MACD: `{(ta.get('macd_crossover') or 'N/A').upper()}`
EMAs: `{(ta.get('ema_trend') or 'N/A').upper()}`
Volume: `{ta.get('volume_ratio', 'N/A')}x` avg ({(ta.get('volume_signal') or 'N/A').upper()})

📈 *OPTIONS & SHORTS*
{opts.get('summary', 'No options data')}
{short.get('summary', 'No short data')}

💬 *SENTIMENT*
{sent_label} (score: `{sent_score}`) | {mentions} mentions

📋 *FUNDAMENTALS*
P/E: `{fund_d.get('pe_ratio', 'N/A')}` | Analyst: `{(fund_d.get('analyst_recommend_key') or 'N/A').upper()}` | Target: `${fund_d.get('analyst_target', 'N/A')}`

🤖 *AI SCENARIO*
{short_analysis}

🎯 *OPTIONS TRADE SETUP*
{trade_block}

*CONFLUENCE: [{bar}] {score}/100* | {conviction_line}
*BIAS: {bias}* {emoji}
""".strip()

    return msg


def send_telegram_alert(
    token:   str,
    chat_id: str,
    ticker:  str,
    tech:    Dict[str, Any],
    sent:    Dict[str, Any],
    fund:    Dict[str, Any],
    ai:      Dict[str, Any],
    force:   bool = False,
) -> bool:
    """
    Send a Telegram alert for a ticker.
    Only sends if confluence score >= threshold, unless force=True.
    Returns True if message was sent successfully.
    """
    if not token or not chat_id:
        log.warning("Telegram token or chat_id not configured — skipping alert")
        return False

    score = ai.get("confluence_score", 50)

    # Only alert on high-conviction signals unless forced
    if not force and score < CONFLUENCE_THRESHOLD:
        log.info(f"Telegram: {ticker} score {score} below threshold {CONFLUENCE_THRESHOLD} — no alert")
        return False

    message = _format_alert(ticker, tech, sent, fund, ai)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id":    chat_id,
        "text":       message,
        "parse_mode": "Markdown",
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            log.info(f"✅ Telegram alert sent for {ticker} (score={score})")
            return True
        else:
            log.error(f"Telegram API error: {resp.status_code} — {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Telegram send failed for {ticker}: {e}")
        return False


def send_telegram_test(token: str, chat_id: str) -> bool:
    """Send a test message to verify bot is working."""
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id":    chat_id,
        "text":       "✅ *Stock AI Bot is connected!*\nYou'll receive alerts here when high-conviction signals are detected.",
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            log.info("Telegram test message sent successfully!")
            return True
        else:
            log.error(f"Telegram test failed: {resp.status_code} — {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Telegram test error: {e}")
        return False
