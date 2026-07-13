"""
main.py — Stock AI Bot Entry Point
Orchestrates technical, sentiment, fundamental analysis + AI synthesis.

Usage:
    python main.py                    # Scan all watchlist tickers once
    python main.py --ticker NVDA      # Scan a specific ticker
    python main.py --loop             # Run continuously on schedule
    python main.py --ticker TSLA --loop  # Loop on one ticker
"""
from __future__ import annotations
import sys
import time
import argparse
import traceback
from datetime import datetime
from pathlib import Path

# ── Make sure local modules resolve ──────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import WATCHLIST, SCAN_INTERVAL_MINUTES, CONFLUENCE_THRESHOLD
from core.logger    import get_logger
from core.ai_engine import run_ai_synthesis
from core.reporter  import print_terminal_report, save_report
from core.telegram_notifier import send_telegram_alert, send_telegram_test, send_earnings_alert as send_telegram_earnings_alert
from core.discord_notifier  import send_discord_alert, send_discord_test, send_earnings_alert as send_discord_earnings_alert
from analyzers.technical    import run_technical_analysis
from analyzers.sentiment    import run_sentiment_analysis
from analyzers.fundamentals import check_watchlist_earnings
from core.fundamentals_cache import get_fundamentals

log = get_logger("StockAIBot")


BANNER = r"""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   ███████╗████████╗ ██████╗  ██████╗██╗  ██╗            ║
║   ██╔════╝╚══██╔══╝██╔═══██╗██╔════╝██║ ██╔╝            ║
║   ███████╗   ██║   ██║   ██║██║     █████╔╝             ║
║   ╚════██║   ██║   ██║   ██║██║     ██╔═██╗             ║
║   ███████║   ██║   ╚██████╔╝╚██████╗██║  ██╗            ║
║   ╚══════╝   ╚═╝    ╚═════╝  ╚═════╝╚═╝  ╚═╝            ║
║                                                          ║
║          AI-POWERED STOCK ANALYSIS BOT v1.0             ║
║   Technical · Sentiment · Options · SEC · AI Engine      ║
╚══════════════════════════════════════════════════════════╝
"""


def analyze_ticker(ticker: str) -> dict:
    """
    Full pipeline for a single ticker:
    1. Technical Analysis (OHLCV, indicators, options, short interest)
    2. Sentiment Analysis (Reddit, StockTwits, Twitter, News)
    3. Fundamental Analysis (SEC, earnings, insider, valuation)
    4. AI Synthesis (Claude)
    5. Report (terminal + log files)
    """
    ticker = ticker.strip().upper()
    log.info(f"{'─'*55}")
    log.info(f"Starting full analysis for: {ticker}")
    log.info(f"{'─'*55}")

    results = {"ticker": ticker, "error": None}

    try:
        # Step 1: Technical
        log.info(f"[1/4] Technical Analysis...")
        tech = run_technical_analysis(ticker)
        if "error" in tech:
            log.error(f"Technical analysis failed: {tech['error']}")
            results["error"] = tech["error"]
            return results

        # ── Data quality gate ─────────────────────────────────
        # If last_price is missing or NaN, yfinance returned bad data.
        # Skip the AI call entirely rather than sending corrupted data to Claude.
        import math
        last_price = tech.get("technicals", {}).get("last_price")
        if last_price is None or (isinstance(last_price, float) and math.isnan(last_price)):
            log.warning(f"⚠  {ticker}: last_price is NaN/missing — yfinance data unreliable. "
                        f"Skipping AI synthesis to avoid corrupted analysis. "
                        f"This usually self-resolves during market hours.")
            results["error"] = "bad_price_data"
            return results

        # Step 2: Sentiment
        log.info(f"[2/4] Sentiment Analysis (Reddit, StockTwits, Twitter, News)...")
        company_name = ""  # Will be filled from fundamentals if available
        sent = run_sentiment_analysis(ticker, company_name)

        # Step 3: Fundamentals (cached daily — SEC/yfinance data doesn't change intraday)
        log.info(f"[3/4] Fundamental Analysis (cached daily)...")
        fund = get_fundamentals(ticker)

        # Update company name for sentiment if we got it
        company_name = fund.get("fundamentals", {}).get("company_name", ticker)

        # ── Expected Move (options-implied) ───────────────────
        # Fetch ATM straddle EM so the AI can calibrate targets.
        # Stored on tech dict — accessible in ai_engine and notifier.
        try:
            from core.expected_move import get_expected_move
            ta = tech.get("technicals", {})
            tech["expected_move"] = get_expected_move(
                ticker=ticker,
                spot=ta.get("last_price"),
                atr_pct=ta.get("atr_pct"),
            )
        except Exception as em_err:
            log.warning(f"{ticker}: expected move fetch failed — {em_err}")
            tech["expected_move"] = {}

        # Step 4: AI Synthesis
        # Option 4 — check cache first. If this ticker wasn't flagged by
        # Tier 1 AND the last result was NONE (no trade) AND the cache is
        # fresh, skip the Claude call and reuse the cached result.
        log.info(f"[4/4] Running Claude AI Synthesis...")
        # Option 4: try AI cache before calling Claude.
        # cache_ai_result only caches NONE/no-trade results, so if anything
        # actionable was last seen, get_cached_ai_result returns None → fresh call.
        _cached_ai = None
        try:
            from core.prescreener import get_cached_ai_result
            _cached_ai = get_cached_ai_result(ticker)
        except Exception:
            pass

        if _cached_ai is not None:
            ai = _cached_ai
            log.info(f"✅ {ticker}: AI result served from cache — Claude call skipped")
        else:
            ai = run_ai_synthesis(ticker, tech, sent, fund)

        if ai.get("error", "").startswith("daily_spend_limit_reached"):
            log.warning(f"⛔ {ticker}: {ai['error']} — skipping report/alerts for this ticker")
            results["error"] = ai["error"]
            return results

        # ── Signal freshness check ─────────────────────────────
        # The scan data can be 30+ min old by the time the alert fires.
        # Re-fetch the LIVE price now and measure how much of the
        # trigger→target move has already been consumed. If the move
        # already happened (the NVDA case), flag the signal STALE so
        # nobody chases a dead entry.
        ts = ai.get("trade_setup", {})
        if ts.get("contract_type") in ("CALL", "PUT"):
            from core.freshness import check_signal_freshness
            ai["freshness"] = check_signal_freshness(
                ticker=ticker,
                contract_type=ts.get("contract_type"),
                entry_trigger=ts.get("entry_trigger"),
                stock_target=ts.get("stock_target"),
                snapshot_price=tech.get("technicals", {}).get("last_price"),
            )
            if ai["freshness"].get("is_stale"):
                log.warning(f"⏱ {ticker}: signal flagged STALE — {ai['freshness']['note']}")

        # Output
        print_terminal_report(ticker, tech, sent, fund, ai)
        json_path, txt_path = save_report(ticker, tech, sent, fund, ai)

        # Backtest logging — record EVERY signal regardless of score,
        # so we can later validate whether the threshold itself is
        # well-calibrated, not just whether high-conviction signals work
        from backtest.signal_logger import log_signal
        log_signal(ticker, tech, ai)

        # Send Telegram alert if score meets threshold
        from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DISCORD_WEBHOOK_URL
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            send_telegram_alert(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ticker, tech, sent, fund, ai)

        # Send Discord alert if webhook configured
        if DISCORD_WEBHOOK_URL:
            send_discord_alert(DISCORD_WEBHOOK_URL, ticker, tech, sent, fund, ai)

        results.update({
            "technical":         tech,
            "sentiment":         sent,
            "fundamental":       fund,
            "ai":                ai,
            "tech":              tech,   # alias for pre-screener cache
            "confluence_score":  ai.get("confluence_score", 50),
            "suggested_bias":    ai.get("suggested_bias", "NEUTRAL"),
            "reports":           {"json": json_path, "txt": txt_path},
        })

        # Summary log line
        score = ai.get("confluence_score", 50)
        bias  = ai.get("suggested_bias", "NEUTRAL")
        flag  = "🔥 HIGH CONVICTION" if score >= CONFLUENCE_THRESHOLD else ""
        log.info(
            f"✅ {ticker} DONE | Score={score}/100 | Bias={bias} {flag}"
        )

    except Exception as e:
        log.error(f"Pipeline error for {ticker}: {e}")
        log.debug(traceback.format_exc())
        results["error"] = str(e)

    return results


def run_scan(watchlist: list[str], tech_cache: dict | None = None) -> list[dict]:
    """
    Two-tier scan:
      Tier 1 — pre-screener: fast_info quote, volume, RSI, VWAP. Zero Claude cost.
      Tier 2 — AI synthesis: only flagged tickers + force-scan overdue ones.
      Option 4 — AI cache: skip Claude if ticker quiet and last result was NONE.
    """
    from core.prescreener import (run_prescreener, force_full_scan_interval,
                                   mark_full_scan, cache_ai_result, save_state)

    log.info(f"⚡ Tier 1 pre-screen: {len(watchlist)} tickers...")
    flagged, prescreen_results = run_prescreener(watchlist, tech_cache)

    force_tickers = force_full_scan_interval(watchlist)
    to_scan = list(dict.fromkeys(flagged + force_tickers))

    if not to_scan:
        log.info("📊 No tickers flagged — skipping AI synthesis this interval")
        save_state()
        return []

    log.info(f"🤖 Tier 2 AI synthesis: {len(to_scan)}/{len(watchlist)} tickers "
             f"({', '.join(to_scan)})")

    all_results = []
    for ticker in to_scan:
        was_flagged = ticker in flagged
        result = analyze_ticker(ticker)
        result["_was_flagged"] = was_flagged
        all_results.append(result)
        mark_full_scan(ticker)
        if result.get("ai"):
            cache_ai_result(ticker, result["ai"], was_flagged)
        time.sleep(2)

    save_state()

    log.info("\n" + "="*55)
    log.info("  SCAN COMPLETE — SUMMARY")
    log.info("="*55)

    skipped = [t for t in watchlist if t not in to_scan]
    for t in skipped:
        ps = prescreen_results.get(t, {})
        log.info(f"  {t:<8} ⏭  No trigger (price=${ps.get('price', '?'):.2f}, "
                 f"vol={ps.get('volume_ratio', 0):.1f}x)")

    for r in all_results:
        score = r.get("confluence_score", "?")
        bias  = r.get("suggested_bias", "N/A")
        err   = r.get("error")
        if err:
            log.warning(f"  {r['ticker']:<8} ❌ Error: {err[:50]}")
        else:
            flag = "🔥" if isinstance(score, int) and score >= CONFLUENCE_THRESHOLD else "  "
            log.info(f"  {r['ticker']:<8} Score={score}/100  Bias={bias:<8} {flag}")
    log.info("="*55 + "\n")

    return all_results


def main():
    print(BANNER)

    parser = argparse.ArgumentParser(
        description="Stock AI Bot — Multi-source market analysis"
    )
    parser.add_argument(
        "--ticker", "-t",
        type=str, default=None,
        help="Analyze a specific ticker (overrides watchlist)"
    )
    parser.add_argument(
        "--loop", "-l",
        action="store_true",
        help=f"Run continuously every {SCAN_INTERVAL_MINUTES} minutes"
    )
    parser.add_argument(
        "--watchlist", "-w",
        type=str, default=None,
        help="Comma-separated override watchlist, e.g. AAPL,TSLA,NVDA"
    )
    parser.add_argument(
        "--test-discord",
        action="store_true",
        help="Send a test message to verify Discord webhook is configured correctly"
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Send a test message to verify Telegram is configured correctly"
    )
    parser.add_argument(
        "--check-earnings",
        action="store_true",
        help="Proactively check the entire watchlist for upcoming earnings and alert if any are within 5 days"
    )
    parser.add_argument(
        "--spend",
        action="store_true",
        help="Show today's accumulated Anthropic API spend and the configured daily cap"
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Bypass the market-hours check and scan even when the market is closed"
    )
    args = parser.parse_args()

    # Discord test mode
    if args.test_discord:
        from config.settings import DISCORD_WEBHOOK_URL
        log.info("Sending Discord test message...")
        success = send_discord_test(DISCORD_WEBHOOK_URL)
        if success:
            log.info("✅ Discord is working! Check your server.")
        else:
            log.error("❌ Discord test failed. Check your DISCORD_WEBHOOK_URL in .env")
        return

    # Telegram test mode
    if args.test_telegram:
        from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        log.info("Sending Telegram test message...")
        success = send_telegram_test(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        if success:
            log.info("✅ Telegram is working! Check your phone.")
        else:
            log.error("❌ Telegram test failed. Check your token and chat ID in .env")
        return

    # Show today's spend
    if args.spend:
        from core.spend_tracker import get_today_spend
        from config.settings import DAILY_SPEND_LIMIT_USD
        spent = get_today_spend()
        pct = (spent / DAILY_SPEND_LIMIT_USD * 100) if DAILY_SPEND_LIMIT_USD > 0 else 0
        log.info(f"💰 Today's Anthropic spend: ${spent:.4f} / ${DAILY_SPEND_LIMIT_USD:.2f} ({pct:.1f}%)")
        if spent >= DAILY_SPEND_LIMIT_USD:
            log.warning("⛔ Daily limit reached — further AI calls today will be skipped")
        return

    # Proactive earnings calendar check mode
    if args.check_earnings:
        from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DISCORD_WEBHOOK_URL
        tickers = [t.strip().upper() for t in args.watchlist.split(",")] if args.watchlist else WATCHLIST
        log.info(f"Checking earnings calendar for {len(tickers)} tickers: {', '.join(tickers)}")
        earnings_data = check_watchlist_earnings(tickers)

        if not earnings_data.get("has_alerts"):
            log.info("✅ No earnings risk in the watchlist for the next 5 days — clear to trade normally")
        else:
            log.warning(
                f"⚠️ Earnings risk detected — Today: {len(earnings_data['today'])} | "
                f"Tomorrow: {len(earnings_data['tomorrow'])} | "
                f"This week: {len(earnings_data['this_week'])}"
            )
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                send_telegram_earnings_alert(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, earnings_data)
            if DISCORD_WEBHOOK_URL:
                send_discord_earnings_alert(DISCORD_WEBHOOK_URL, earnings_data)
        return

    # Determine tickers
    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.watchlist:
        tickers = [t.strip().upper() for t in args.watchlist.split(",")]
    else:
        tickers = WATCHLIST

    log.info(f"Watchlist: {', '.join(tickers)}")
    log.info(f"Confluence threshold: {CONFLUENCE_THRESHOLD}/100 for high-conviction signals")

    # ── Market-hours gate ─────────────────────────────────────
    # Skip scans on weekends, NYSE holidays, and outside the
    # 9:30 AM – 4:00 PM ET session (1:00 PM on early-close days).
    # No more manually disabling the cron-job.org trigger on holidays.
    # Bypass with --force for testing / off-hours analysis.
    from core.market_hours import market_status

    if args.loop:
        log.info(f"Loop mode: two-tier scan every {SCAN_INTERVAL_MINUTES} minutes. Press Ctrl+C to stop.\n")
        tech_cache: dict = {}
        try:
            while True:
                status = market_status()
                if args.force or status["is_open"]:
                    results = run_scan(tickers, tech_cache)
                    # Update tech cache with fresh results for next pre-screen
                    for r in results:
                        t = r.get("ticker")
                        if t and r.get("tech"):
                            tech_cache[t] = r["tech"]
                else:
                    log.info(f"⏸  Skipping scan — {status['reason']}")
                log.info(f"Next check in {SCAN_INTERVAL_MINUTES} minutes...")
                time.sleep(SCAN_INTERVAL_MINUTES * 60)
        except KeyboardInterrupt:
            log.info("Bot stopped by user.")
    else:
        status = market_status()
        if not args.force and not status["is_open"]:
            log.info(f"⏸  Market closed — {status['reason']}. Skipping scan. (Use --force to override.)")
            return
        if status.get("is_early_close"):
            log.info("🕐 Early-close day — market closes at 1:00 PM ET today")
        run_scan(tickers)


if __name__ == "__main__":
    main()
