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

    # Trade Decision Worksheet
    ts_data = ai.get("trade_setup", {})
    direction = ts_data.get("direction", "NONE")
    dir_color = Fore.GREEN if direction == "LONG" else Fore.RED if direction == "SHORT" else Fore.YELLOW
    rr        = ts_data.get("risk_reward")
    rr_q      = ts_data.get("rr_quality", "UNKNOWN")
    rr_color  = Fore.GREEN if rr_q == "GOOD" else Fore.YELLOW if rr_q == "ACCEPTABLE" else Fore.RED

    print(f"\n{Fore.CYAN}{'─'*65}{Style.RESET_ALL}")
    print(f"{Fore.WHITE}  📋  TRADE DECISION WORKSHEET{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'─'*65}{Style.RESET_ALL}")
    print(f"  Direction:    {dir_color}{direction}{Style.RESET_ALL}")
    if direction != "NONE":
        print(f"  Entry:        ${Fore.YELLOW}{ts_data.get('entry_price', 'N/A')}{Style.RESET_ALL}")
        print(f"  Stop Loss:    ${Fore.RED}{ts_data.get('stop_loss', 'N/A')}{Style.RESET_ALL}  ← exit here if wrong")
        print(f"  Target 1:     ${Fore.GREEN}{ts_data.get('target_1', 'N/A')}{Style.RESET_ALL}  ← take partial profit")
        print(f"  Target 2:     ${Fore.GREEN}{ts_data.get('target_2', 'N/A')}{Style.RESET_ALL}  ← full target")
        rr_display = f"{rr}:1" if rr else "N/A"
        print(f"  Risk/Reward:  {rr_color}{rr_display} ({rr_q}){Style.RESET_ALL}")
        if ts_data.get("entry_condition"):
            print(f"  When to enter: {ts_data['entry_condition']}")
        if ts_data.get("size_note"):
            print(f"  Position size: {ts_data['size_note']}")
        if ts_data.get("valid_days"):
            print(f"  Setup expires: in {ts_data['valid_days']} trading days")
    else:
        print(f"  {Fore.YELLOW}No trade setup — score below 50 or signals too mixed.{Style.RESET_ALL}")
        print(f"  Watch for a cleaner entry before committing capital.")

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

        f.write("── TRADE DECISION WORKSHEET ─────────────────────────\n")
        ts_data   = ai.get("trade_setup", {})
        direction = ts_data.get("direction", "NONE")
        f.write(f"  Direction:     {direction}\n")
        if direction != "NONE":
            f.write(f"  Entry:         ${ts_data.get('entry_price', 'N/A')}\n")
            f.write(f"  Stop Loss:     ${ts_data.get('stop_loss', 'N/A')}\n")
            f.write(f"  Target 1:      ${ts_data.get('target_1', 'N/A')}\n")
            f.write(f"  Target 2:      ${ts_data.get('target_2', 'N/A')}\n")
            rr = ts_data.get('risk_reward')
            f.write(f"  Risk/Reward:   {f'{rr}:1' if rr else 'N/A'} ({ts_data.get('rr_quality','UNKNOWN')})\n")
            if ts_data.get("entry_condition"):
                f.write(f"  When to enter: {ts_data['entry_condition']}\n")
            if ts_data.get("size_note"):
                f.write(f"  Position size: {ts_data['size_note']}\n")
            if ts_data.get("valid_days"):
                f.write(f"  Setup expires: in {ts_data['valid_days']} trading days\n")
        else:
            f.write("  No trade setup — score below 50 or signals too mixed.\n")
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
