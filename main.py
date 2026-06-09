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
from core.telegram_notifier import send_telegram_alert, send_telegram_test
from core.discord_notifier  import send_discord_alert, send_discord_test
from analyzers.technical    import run_technical_analysis
from analyzers.sentiment    import run_sentiment_analysis
from analyzers.fundamentals import run_fundamental_analysis

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

        # Step 2: Sentiment
        log.info(f"[2/4] Sentiment Analysis (Reddit, StockTwits, Twitter, News)...")
        company_name = ""  # Will be filled from fundamentals if available
        sent = run_sentiment_analysis(ticker, company_name)

        # Step 3: Fundamentals
        log.info(f"[3/4] Fundamental Analysis (SEC, Earnings, Valuation)...")
        fund = run_fundamental_analysis(ticker)

        # Update company name for sentiment if we got it
        company_name = fund.get("fundamentals", {}).get("company_name", ticker)

        # Step 4: AI Synthesis
        log.info(f"[4/4] Running Claude AI Synthesis...")
        ai = run_ai_synthesis(ticker, tech, sent, fund)

        # Output
        print_terminal_report(ticker, tech, sent, fund, ai)
        json_path, txt_path = save_report(ticker, tech, sent, fund, ai)

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


def run_scan(watchlist: list[str]) -> list[dict]:
    """Scan all tickers in the watchlist."""
    log.info(f"Starting scan of {len(watchlist)} tickers: {', '.join(watchlist)}")
    all_results = []

    for ticker in watchlist:
        result = analyze_ticker(ticker)
        all_results.append(result)
        time.sleep(2)  # Be polite to APIs

    # Print summary table
    log.info("\n" + "="*55)
    log.info("  SCAN COMPLETE — SUMMARY")
    log.info("="*55)
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

    # Determine tickers
    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.watchlist:
        tickers = [t.strip().upper() for t in args.watchlist.split(",")]
    else:
        tickers = WATCHLIST

    log.info(f"Watchlist: {', '.join(tickers)}")
    log.info(f"Confluence threshold: {CONFLUENCE_THRESHOLD}/100 for high-conviction signals")

    if args.loop:
        log.info(f"Loop mode: scanning every {SCAN_INTERVAL_MINUTES} minutes. Press Ctrl+C to stop.\n")
        try:
            while True:
                run_scan(tickers)
                next_run = datetime.now()
                log.info(f"Next scan in {SCAN_INTERVAL_MINUTES} minutes...")
                time.sleep(SCAN_INTERVAL_MINUTES * 60)
        except KeyboardInterrupt:
            log.info("Bot stopped by user.")
    else:
        run_scan(tickers)


if __name__ == "__main__":
    main()
