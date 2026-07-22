"""
core/discord_notifier.py — Discord webhook alert system for Degënic$
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

    # AI analysis — extract ONLY the market scenario prose (one tight
    # paragraph). The card carries the trade mechanics; the text just
    # needs to say what's going on. Everything else was redundant.
    analysis = ai.get("analysis") or ""
    scenario = ""
    lines = analysis.split("\n")
    capture = False
    for line in lines:
        if line.strip().startswith("##") and "SCENARIO" in line.upper():
            capture = True
            continue
        if capture:
            if line.strip().startswith("##") or line.strip().startswith("---"):
                break
            if line.strip():
                scenario += line.strip() + " "
    scenario = scenario.strip()
    # Trim to ~2 sentences for readability
    if scenario:
        parts = scenario.split(". ")
        scenario = ". ".join(parts[:2]).strip()
        if scenario and not scenario.endswith("."):
            scenario += "."
    if len(scenario) > 400:
        scenario = scenario[:400].rsplit(" ", 1)[0] + "..."
    analysis_short = scenario or "See card below for the setup."

    # Options trade setup field
    ts_data  = ai.get("trade_setup", {})
    contract = ts_data.get("contract_type", "NONE")
    quality  = ts_data.get("setup_quality", "NO TRADE")

    if contract != "NONE":
        con_emoji  = "🟢" if contract == "CALL" else "🔴"
        qual_emoji = ("🔥" if quality == "HIGH CONVICTION" else
                      "⚠️" if quality == "MODERATE" else "❌")
        strike_str  = f"${ts_data['strike']}" if ts_data.get("strike") else "N/A"
        premium_str = f"${ts_data['est_premium']}" if ts_data.get("est_premium") else "N/A"
        target_str  = f"${ts_data['stock_target']}" if ts_data.get("stock_target") else "N/A"
        profit_str  = f"{ts_data['profit_target']}%" if ts_data.get("profit_target") else "N/A"

        # Show original target if clamped + EM context
        target_display = f"`{target_str}`"
        if ts_data.get("target_original"):
            target_display = f"`{target_str}` *(was ${ts_data['target_original']:.2f} — clamped to EM)*"
        em_pct = ts_data.get("em_pct")
        ratio  = ts_data.get("target_em_ratio")
        em_str = f" | EM ±{em_pct}% ({ratio:.1f}x)" if em_pct and ratio else ""

        # The card below shows strike / target / premium / max-loss.
        # Text keeps ONLY the actionable conditions the card truncates.
        setup_value = ""
        if ts_data.get("entry_condition"):
            setup_value += f"✅ **Enter:** {ts_data['entry_condition']}\n"
        if ts_data.get("stop_rule"):
            setup_value += f"🛑 **Stop:** {ts_data['stop_rule']}\n"
        if ts_data.get("avoid_if"):
            setup_value += f"⛔ **Avoid if:** {ts_data['avoid_if']}"
        setup_value = setup_value.strip() or "See card below for full setup."

        # ── Freshness banner ──────────────────────────────────
        # Injected by core/freshness.py at alert time. A STALE flag
        # means the trigger→target move already mostly happened
        # before this alert fired — entering now is chasing.
        fresh = ai.get("freshness", {})
        if fresh.get("is_stale"):
            setup_value = (
                f"🚨 **STALE SIGNAL — DO NOT CHASE** 🚨\n"
                f"⏱ {fresh.get('note', 'Move already happened before this alert.')}\n\n"
                + setup_value
            )
            quality = f"STALE — {quality}"
        elif fresh.get("checked") and fresh.get("note"):
            setup_value += f"\n⏱ Freshness: {fresh['note']}"

        conviction_str = f"{qual_emoji} {quality}"
    elif "EARNINGS" in quality:
        setup_value    = f"⚡ **EARNINGS IMMINENT — NO TRADE**\n{ts_data.get('key_risk', 'IV crush risk is extreme. Do not trade 0-2 DTE into earnings.')}"
        conviction_str = "⚡ NO TRADE — EARNINGS"
    else:
        setup_value    = "❌ No trade — signals too mixed or score below threshold.\nSit this one out."
        conviction_str = "❌ NO TRADE"

    embed = {
        "title": f"{emoji}  {ticker} — Degënic$ Signal",
        "color": color,
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {"text": f"Degënic$ v1.0 • {ts}"},
        "fields": [
            {
                "name": f"{emoji} ${ta.get('last_price', 'N/A')}  ·  {score}/100  ·  {conviction_str}",
                "value": (
                    f"1D `{ta.get('return_1d', 0):+.2f}%`  "
                    f"5D `{ta.get('return_5d', 0):+.2f}%`  "
                    f"20D `{ta.get('return_20d', 0):+.2f}%`  ·  "
                    f"RSI `{ta.get('rsi', 'N/A')}`  ·  "
                    f"Vol `{ta.get('volume_ratio', 'N/A')}x`"
                    f"{earn_warning}"
                ),
                "inline": False,
            },
            {
                "name": "🤖 Read",
                "value": analysis_short[:600],
                "inline": False,
            },
            {
                "name": "🎯 How to play it",
                "value": setup_value[:1024],
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

    # ── Duplicate suppression ─────────────────────────────────
    # The same setup re-alerting every scan interval is noise, not
    # signal. Suppress repeats of the same ticker+direction inside
    # the cooldown window unless the score materially improved.
    direction = (ai.get("trade_setup") or {}).get("contract_type", "NONE")
    if not force:
        try:
            from core.alert_cooldown import should_alert
            allowed, reason = should_alert(ticker, direction, score)
            if not allowed:
                log.info(f"Discord: {ticker} suppressed — {reason}")
                return False
            if reason:
                log.info(f"Discord: {ticker} alerting — {reason}")
        except Exception as e:
            log.warning(f"Cooldown check failed for {ticker} ({e}) — alerting anyway")

    embed = _build_embed(ticker, tech, sent, fund, ai)

    # ── Signal card image ─────────────────────────────────────
    # Render a dashboard-style PNG card and attach it so the embed
    # shows a readable visual summary above the text breakdown.
    # If rendering fails for ANY reason we silently fall back to
    # the text-only embed — the alert always goes out.
    card_png = None
    try:
        from core.signal_card import render_signal_card
        card_png = render_signal_card(ticker, tech, ai)
    except Exception as e:
        log.warning(f"Signal card unavailable for {ticker}: {e}")

    try:
        if card_png:
            import json as _json
            embed["image"] = {"url": "attachment://signal_card.png"}
            resp = requests.post(
                webhook_url,
                data={"payload_json": _json.dumps({"embeds": [embed]})},
                files={"files[0]": ("signal_card.png", card_png, "image/png")},
                timeout=20,
            )
        else:
            resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=15)

        if resp.status_code in (200, 204):
            log.info(f"✅ Discord alert sent for {ticker} (score={score}"
                     f"{', with card' if card_png else ''})")
            try:
                from core.alert_cooldown import record_alert
                record_alert(ticker, direction, score)
            except Exception:
                pass
            return True
        else:
            log.error(f"Discord webhook error: {resp.status_code} — {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Discord send failed for {ticker}: {e}")
        return False


def send_earnings_alert(webhook_url: str, earnings_data: Dict[str, Any]) -> bool:
    """
    Send a proactive earnings calendar alert before market open as a
    Discord embed. Always sends if there are any alerts — informational,
    not gated by confluence score.
    """
    if not webhook_url:
        return False

    if not earnings_data.get("has_alerts"):
        log.info("Earnings check: no upcoming earnings in lookahead window — no alert sent")
        return False

    fields = []

    today = earnings_data.get("today", [])
    if today:
        fields.append({
            "name":  "🚨 EARNINGS TODAY — DO NOT TRADE 0-2 DTE",
            "value": "\n".join(f"**{e['ticker']}** reports today" for e in today),
            "inline": False,
        })

    tomorrow = earnings_data.get("tomorrow", [])
    if tomorrow:
        fields.append({
            "name":  "⚠️ EARNINGS TOMORROW — IV crush risk",
            "value": "\n".join(f"**{e['ticker']}** reports tomorrow ({e['earnings_date']})" for e in tomorrow),
            "inline": False,
        })

    this_week = earnings_data.get("this_week", [])
    if this_week:
        sorted_week = sorted(this_week, key=lambda x: x["days_away"])
        fields.append({
            "name":  "📌 EARNINGS THIS WEEK — elevated IV likely",
            "value": "\n".join(f"**{e['ticker']}** in {e['days_away']} days ({e['earnings_date']})" for e in sorted_week),
            "inline": False,
        })

    clear = earnings_data.get("clear", [])
    if clear:
        fields.append({
            "name":  "✅ Clear to trade normally",
            "value": ", ".join(clear),
            "inline": False,
        })

    embed = {
        "title":       "📅 Earnings Calendar Alert",
        "description": "Pre-market watchlist check",
        "color":       0xF59E0B,
        "timestamp":   datetime.utcnow().isoformat(),
        "fields":      fields,
        "footer":      {"text": "Degënic$ — Earnings Guard"},
    }

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        if resp.status_code in (200, 204):
            log.info("✅ Earnings calendar alert sent to Discord")
            return True
        else:
            log.error(f"Earnings alert failed: {resp.status_code} — {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Earnings alert send error: {e}")
        return False


def send_discord_test(webhook_url: str) -> bool:
    """Send a test message to verify webhook is working."""
    payload = {
        "embeds": [{
            "title": "✅ Degënic$ Connected!",
            "description": "You'll receive rich signal alerts here when high-conviction trades are detected.",
            "color": 0x00C851,
            "footer": {"text": "Degënic$ v1.0"},
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
