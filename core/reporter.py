"""
core/reporter.py — Formats analysis into terminal + log file reports
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from colorama import Fore, Style, Back
from core.logger import get_logger

log     = get_logger("Reporter")
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


BIAS_COLORS = {
    "BULLISH":  Fore.GREEN,
    "BEARISH":  Fore.RED,
    "NEUTRAL":  Fore.YELLOW,
}

SCORE_COLOR = lambda s: (
    Fore.GREEN   if s >= 66 else
    Fore.YELLOW  if s >= 50 else
    Fore.RED
)


def _score_bar(score: int, width: int = 30) -> str:
    filled = int(score / 100 * width)
    empty  = width - filled
    color  = SCORE_COLOR(score)
    return f"{color}{'█' * filled}{Style.DIM}{'░' * empty}{Style.RESET_ALL}"


def print_terminal_report(
    ticker:  str,
    tech:    Dict[str, Any],
    sent:    Dict[str, Any],
    fund:    Dict[str, Any],
    ai:      Dict[str, Any],
):
    """Print a rich formatted report to the terminal."""
    ta      = tech.get("technicals", {})
    opts    = tech.get("options_flow", {})
    short   = tech.get("short_interest", {})
    fund_d  = fund.get("fundamentals", {})
    earn    = fund.get("earnings", {})
    ta_sc   = tech.get("ta_score", 50)
    overall = sent.get("overall_label", "neutral").upper()
    score   = ai.get("confluence_score", 50)
    bias    = ai.get("suggested_bias", "NEUTRAL")
    bias_c  = BIAS_COLORS.get(bias, Fore.WHITE)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{Fore.CYAN}{'═'*65}{Style.RESET_ALL}")
    print(f"{Fore.WHITE}{Back.BLUE}  📈  STOCK AI BOT — {ticker} ANALYSIS  [{ts}]  {Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'═'*65}{Style.RESET_ALL}")

    # Price snapshot
    print(f"\n{Fore.WHITE}  PRICE SNAPSHOT{Style.RESET_ALL}")
    print(f"  Last Price:  ${Fore.YELLOW}{ta.get('last_price', 'N/A')}{Style.RESET_ALL}")
    r1d = ta.get('return_1d') or 0
    r5d = ta.get('return_5d') or 0
    r20d = ta.get('return_20d') or 0
    print(f"  1D / 5D / 20D:  {r1d:+.2f}% / {r5d:+.2f}% / {r20d:+.2f}%")
    print(f"  Company:  {fund_d.get('company_name', ticker)} | {fund_d.get('sector') or 'N/A'}")

    # Earnings alert
    if earn.get("earnings_imminent"):
        print(f"\n  {Fore.MAGENTA}{'⚡'*5}  EARNINGS IN {earn.get('days_to_earnings')} DAYS  {'⚡'*5}{Style.RESET_ALL}")

    # Technical snapshot
    print(f"\n{Fore.WHITE}  TECHNICAL SIGNALS{Style.RESET_ALL}")
    print(f"  RSI:       {ta.get('rsi') or 'N/A'}  ({ta.get('rsi_signal','').upper()})")
    print(f"  MACD:      {(ta.get('macd_crossover') or 'N/A').upper()}")
    print(f"  EMAs:      {(ta.get('ema_trend') or 'N/A').upper()}")
    print(f"  Volume:    {ta.get('volume_ratio') or 'N/A'}x avg  ({(ta.get('volume_signal') or 'N/A').upper()})")
    print(f"  Options:   {opts.get('summary','N/A')}")
    print(f"  Shorts:    {short.get('summary','N/A')}")
    print(f"  TA Score:  {_score_bar(ta_sc)} {ta_sc}/100")

    # Sentiment snapshot
    print(f"\n{Fore.WHITE}  SENTIMENT SIGNALS{Style.RESET_ALL}")
    sent_c = Fore.GREEN if "bull" in overall.lower() else Fore.RED if "bear" in overall.lower() else Fore.YELLOW
    print(f"  Overall:     {sent_c}{overall}{Style.RESET_ALL}  (compound: {sent.get('overall_compound','N/A')})")
    print(f"  Mentions:    {sent.get('total_mentions', 0)} across Reddit / StockTwits / Twitter / News")
    st = sent.get("stocktwits",{})
    print(f"  StockTwits:  🐂 {st.get('bull_count',0)} bullish / 🐻 {st.get('bear_count',0)} bearish (native tags)")

    # Fundamentals
    print(f"\n{Fore.WHITE}  FUNDAMENTAL SIGNALS{Style.RESET_ALL}")
    print(f"  P/E:        {fund_d.get('pe_ratio') or 'N/A'} | Forward: {fund_d.get('forward_pe') or 'N/A'}")
    print(f"  Analyst:    {str(fund_d.get('analyst_recommend_key') or 'N/A').upper()} | Target: ${fund_d.get('analyst_target') or 'N/A'}")
    print(f"  Insider:    {str(fund.get('insider',{}).get('insider_signal') or 'N/A').upper()}")

    # AI Analysis
    print(f"\n{Fore.CYAN}{'─'*65}{Style.RESET_ALL}")
    print(f"{Fore.WHITE}  🤖  AI SYNTHESIS (Claude){Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'─'*65}{Style.RESET_ALL}\n")

    if ai.get("analysis"):
        for line in ai["analysis"].split("\n"):
            print(f"  {line}")
    else:
        print(f"  {Fore.RED}AI analysis unavailable: {ai.get('error','Unknown error')}{Style.RESET_ALL}")

    # Options Trade Worksheet (0-2 DTE)
    ts_data  = ai.get("trade_setup", {})
    contract = ts_data.get("contract_type", "NONE")
    quality  = ts_data.get("setup_quality", "NO TRADE")
    expiry   = ts_data.get("expiry", "N/A")
    strike   = ts_data.get("strike")
    moneyness = ts_data.get("moneyness", "")

    con_color  = Fore.GREEN if contract == "CALL" else Fore.RED if contract == "PUT" else Fore.YELLOW
    qual_color = (Fore.GREEN  if quality == "HIGH CONVICTION"   else
                  Fore.YELLOW if quality == "MODERATE"          else
                  Fore.MAGENTA if "EARNINGS" in quality         else
                  Fore.RED)

    print(f"\n{Fore.CYAN}{'─'*65}{Style.RESET_ALL}")
    print(f"{Fore.WHITE}  🎯  OPTIONS TRADE SETUP  (0-2 DTE){Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'─'*65}{Style.RESET_ALL}")
    print(f"  Contract:      {con_color}{contract}{Style.RESET_ALL}  |  Conviction: {qual_color}{quality}{Style.RESET_ALL}")

    if contract != "NONE":
        strike_display = f"${strike}" if strike else "N/A"
        print(f"  Expiry:        {Fore.YELLOW}{expiry}{Style.RESET_ALL}")
        print(f"  Strike:        {Fore.YELLOW}{strike_display}{Style.RESET_ALL}  ({moneyness})")
        if ts_data.get("stock_target"):
            print(f"  Stock needs:   ${Fore.GREEN}{ts_data['stock_target']}{Style.RESET_ALL}  ← where stock must go")
        if ts_data.get("est_premium"):
            print(f"  Est. premium:  ${Fore.YELLOW}{ts_data['est_premium']}{Style.RESET_ALL}  per contract × 100 shares")
        if ts_data.get("profit_target"):
            print(f"  Profit target: {Fore.GREEN}{ts_data['profit_target']}%{Style.RESET_ALL} gain on contract")
        print(f"  Max loss:      {Fore.RED}100% of premium{Style.RESET_ALL}  (always true for long options)")
        if ts_data.get("stop_rule"):
            print(f"  Stop rule:     {ts_data['stop_rule']}")
        if ts_data.get("entry_condition"):
            print(f"  Enter when:    {ts_data['entry_condition']}")
        if ts_data.get("avoid_if"):
            print(f"  {Fore.RED}Avoid if:{Style.RESET_ALL}      {ts_data['avoid_if']}")
        if ts_data.get("key_risk"):
            print(f"  {Fore.YELLOW}Key risk:{Style.RESET_ALL}      {ts_data['key_risk']}")
    else:
        if "EARNINGS" in quality:
            print(f"  {Fore.MAGENTA}⚡ EARNINGS IMMINENT — NO TRADE{Style.RESET_ALL}")
            if ts_data.get("key_risk"):
                print(f"  {Fore.MAGENTA}{ts_data['key_risk']}{Style.RESET_ALL}")
        else:
            print(f"  {Fore.YELLOW}No trade — signals too mixed or score below threshold.{Style.RESET_ALL}")
            print(f"  0-2 DTE requires high conviction. Sit this one out.")

    # Final verdict
    print(f"\n{Fore.CYAN}{'─'*65}{Style.RESET_ALL}")
    print(f"  CONFLUENCE:  {_score_bar(score)} {score}/100")
    print(f"  BIAS:        {bias_c}{bias}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'═'*65}{Style.RESET_ALL}\n")


def save_report(
    ticker:  str,
    tech:    Dict[str, Any],
    sent:    Dict[str, Any],
    fund:    Dict[str, Any],
    ai:      Dict[str, Any],
):
    """Save the full analysis as a JSON report and a human-readable .txt log."""
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    fund_d  = fund.get("fundamentals", {})

    # ── JSON (machine-readable) ───────────────────────────
    payload = {
        "ticker":    ticker,
        "timestamp": datetime.now().isoformat(),
        "technicals":       tech.get("technicals", {}),
        "ta_score":         tech.get("ta_score", 50),
        "options_flow":     tech.get("options_flow", {}),
        "short_interest":   tech.get("short_interest", {}),
        "sentiment_overall": sent.get("overall_label"),
        "sentiment_score":   sent.get("overall_compound"),
        "total_mentions":    sent.get("total_mentions"),
        "fundamentals":      fund.get("fundamentals", {}),
        "earnings":          fund.get("earnings", {}),
        "insider":           fund.get("insider", {}),
        "confluence_score":  ai.get("confluence_score"),
        "suggested_bias":    ai.get("suggested_bias"),
        "trade_setup":       ai.get("trade_setup", {}),
        "ai_analysis":       ai.get("analysis"),
    }

    json_path = LOG_DIR / f"{ticker}_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    # ── TXT (human-readable) ──────────────────────────────
    txt_path = LOG_DIR / f"{ticker}_{ts}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"STOCK AI BOT — {ticker} ANALYSIS\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"{'='*60}\n\n")

        f.write(f"CONFLUENCE SCORE: {ai.get('confluence_score','N/A')}/100\n")
        f.write(f"SUGGESTED BIAS:   {ai.get('suggested_bias','N/A')}\n\n")

        f.write("── OPTIONS TRADE SETUP (0-2 DTE) ────────────────────\n")
        ts_data  = ai.get("trade_setup", {})
        contract = ts_data.get("contract_type", "NONE")
        f.write(f"  Contract:      {contract}\n")
        f.write(f"  Conviction:    {ts_data.get('setup_quality', 'N/A')}\n")
        if contract != "NONE":
            f.write(f"  Expiry:        {ts_data.get('expiry', 'N/A')}\n")
            f.write(f"  Strike:        ${ts_data.get('strike', 'N/A')}  ({ts_data.get('moneyness', 'N/A')})\n")
            f.write(f"  Stock target:  ${ts_data.get('stock_target', 'N/A')}\n")
            f.write(f"  Est. premium:  ${ts_data.get('est_premium', 'N/A')}\n")
            f.write(f"  Profit target: {ts_data.get('profit_target', 'N/A')}%\n")
            f.write(f"  Max loss:      100% of premium\n")
            if ts_data.get("stop_rule"):
                f.write(f"  Stop rule:     {ts_data['stop_rule']}\n")
            if ts_data.get("entry_condition"):
                f.write(f"  Enter when:    {ts_data['entry_condition']}\n")
            if ts_data.get("avoid_if"):
                f.write(f"  Avoid if:      {ts_data['avoid_if']}\n")
            if ts_data.get("key_risk"):
                f.write(f"  Key risk:      {ts_data['key_risk']}\n")
        else:
            f.write("  No trade — signals too mixed or score below threshold.\n")
        f.write("\n")

        f.write("── AI ANALYSIS ─────────────────────────────────────\n\n")
        f.write((ai.get("analysis") or "AI analysis not available") + "\n\n")

        f.write("── TECHNICALS ───────────────────────────────────────\n")
        ta = tech.get("technicals", {})
        for k, v in ta.items():
            f.write(f"  {k}: {v}\n")

        f.write("\n── OPTIONS FLOW ─────────────────────────────────────\n")
        f.write(f"  {tech.get('options_flow',{}).get('summary','N/A')}\n")

        f.write("\n── SHORT INTEREST ───────────────────────────────────\n")
        f.write(f"  {tech.get('short_interest',{}).get('summary','N/A')}\n")

        f.write("\n── SENTIMENT ────────────────────────────────────────\n")
        f.write(f"  Overall: {sent.get('overall_label','N/A').upper()}\n")
        f.write(f"  Compound: {sent.get('overall_compound','N/A')}\n")
        f.write(f"  Total Mentions: {sent.get('total_mentions',0)}\n")

        f.write("\n── TOP NEWS ─────────────────────────────────────────\n")
        for a in sent.get("news",{}).get("articles",[])[:5]:
            f.write(f"  [{a.get('source')}] {a.get('title','')}\n")

        f.write("\n── FUNDAMENTALS ─────────────────────────────────────\n")
        for k in ["company_name","pe_ratio","forward_pe","revenue_growth",
                  "analyst_recommend_key","analyst_target"]:
            f.write(f"  {k}: {fund_d.get(k,'N/A')}\n")

    log.info(f"Reports saved: {json_path.name} + {txt_path.name}")
    return str(json_path), str(txt_path)
