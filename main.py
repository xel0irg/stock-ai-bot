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
    args = parser.parse_args()

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
