"""
core/ai_engine.py — Claude AI synthesis engine
Takes all data sources and produces the final analysis report.
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Dict, Any

import anthropic

from core.logger import get_logger, log_banner
from core.spend_tracker import check_daily_limit, record_spend
from config.settings import ANTHROPIC_API_KEY, CONFLUENCE_THRESHOLD, DAILY_SPEND_LIMIT_USD

log = get_logger("AIEngine")


def _build_analysis_prompt(
    ticker: str,
    tech:   Dict[str, Any],
    sent:   Dict[str, Any],
    fund:   Dict[str, Any],
) -> str:
    """Build the master prompt for Claude to synthesize all signals."""

    ta     = tech.get("technicals", {})
    opts   = tech.get("options_flow", {})
    short  = tech.get("short_interest", {})
    fund_d = fund.get("fundamentals", {})
    earn   = fund.get("earnings", {})
    inside = fund.get("insider", {})
    # Market regime context (cached — fetched once per scan, shared across tickers)
    try:
        from core.market_regime import regime_for_ticker
        regime_block = regime_for_ticker(ticker)
    except Exception:
        regime_block = ""

    news     = sent.get("news", {}).get("aggregated", {})

    # Top 5 news headlines for the prompt
    top_news = "\n".join([
        f"  - [{a.get('source')}] {a.get('title', '')}"
        for a in sent.get("news", {}).get("articles", [])[:5]
    ]) or "  No news found"

    intraday = tech.get("intraday", {})
    tf_5m    = intraday.get("tf_5m",  {})
    tf_15m   = intraday.get("tf_15m", {})
    tf_1h    = intraday.get("tf_1h",  {})

    # Format intraday section
    if intraday.get("has_data"):
        intraday_section = f"""
═══════════════════════════════════════════════
⏱  INTRADAY SIGNALS (Entry Timing) — 3 Timeframes
═══════════════════════════════════════════════
Combined Intraday Bias: {intraday.get('intraday_bias', 'N/A')}
{intraday.get('summary', '')}

5-MINUTE TIMEFRAME (entry precision):
  Price vs VWAP: {tf_5m.get('vwap_position', 'N/A').replace('_', ' ').upper()} (VWAP=${tf_5m.get('vwap', 'N/A')})
  EMA Trend:     {str(tf_5m.get('ema_trend', 'N/A')).upper()} (EMA9=${tf_5m.get('ema9', 'N/A')} / EMA21=${tf_5m.get('ema21', 'N/A')})
  MACD:          {str(tf_5m.get('macd_direction', 'N/A')).upper()} (hist: {tf_5m.get('macd_hist', 'N/A')})
  RSI (9):       {tf_5m.get('rsi', 'N/A')} ({str(tf_5m.get('rsi_signal', 'N/A')).upper()})
  Volume:        {tf_5m.get('volume_ratio', 'N/A')}x avg ({str(tf_5m.get('volume_signal', 'N/A')).upper()})
  3-Candle Mom:  {tf_5m.get('momentum_3c', 'N/A')}% ({str(tf_5m.get('momentum_direction', 'N/A')).upper()})
  Bias:          {tf_5m.get('bias', 'N/A')} ({tf_5m.get('bull_signals', 0)} bull / {tf_5m.get('bear_signals', 0)} bear signals)

15-MINUTE TIMEFRAME (setup confirmation):
  Price vs VWAP: {tf_15m.get('vwap_position', 'N/A').replace('_', ' ').upper()} (VWAP=${tf_15m.get('vwap', 'N/A')})
  VWAP Distance: {tf_15m.get('vwap_distance_pct', 'N/A')}%
  EMA Trend:     {str(tf_15m.get('ema_trend', 'N/A')).upper()} (EMA9=${tf_15m.get('ema9', 'N/A')} / EMA21=${tf_15m.get('ema21', 'N/A')})
  MACD:          {str(tf_15m.get('macd_direction', 'N/A')).upper()} (hist: {tf_15m.get('macd_hist', 'N/A')})
  RSI (9):       {tf_15m.get('rsi', 'N/A')} ({str(tf_15m.get('rsi_signal', 'N/A')).upper()})
  Volume:        {tf_15m.get('volume_ratio', 'N/A')}x avg ({str(tf_15m.get('volume_signal', 'N/A')).upper()})
  3-Candle Mom:  {tf_15m.get('momentum_3c', 'N/A')}% ({str(tf_15m.get('momentum_direction', 'N/A')).upper()})
  Bias:          {tf_15m.get('bias', 'N/A')} ({tf_15m.get('bull_signals', 0)} bull / {tf_15m.get('bear_signals', 0)} bear signals)

1-HOUR TIMEFRAME (trend direction):
  Price vs VWAP: {tf_1h.get('vwap_position', 'N/A').replace('_', ' ').upper()} (VWAP=${tf_1h.get('vwap', 'N/A')})
  VWAP Distance: {tf_1h.get('vwap_distance_pct', 'N/A')}%
  EMA Trend:     {str(tf_1h.get('ema_trend', 'N/A')).upper()} (EMA9=${tf_1h.get('ema9', 'N/A')} / EMA21=${tf_1h.get('ema21', 'N/A')})
  MACD:          {str(tf_1h.get('macd_direction', 'N/A')).upper()} (hist: {tf_1h.get('macd_hist', 'N/A')})
  RSI (9):       {tf_1h.get('rsi', 'N/A')} ({str(tf_1h.get('rsi_signal', 'N/A')).upper()})
  Volume:        {tf_1h.get('volume_ratio', 'N/A')}x avg ({str(tf_1h.get('volume_signal', 'N/A')).upper()})
  3-Candle Mom:  {tf_1h.get('momentum_3c', 'N/A')}% ({str(tf_1h.get('momentum_direction', 'N/A')).upper()})
  Bias:          {tf_1h.get('bias', 'N/A')} ({tf_1h.get('bull_signals', 0)} bull / {tf_1h.get('bear_signals', 0)} bear signals)

TIMEFRAME CONFLUENCE:
  Daily + 1H + 15m + 5m all BEARISH = EXCEPTIONAL PUT entry (score 85+)
  Daily + 1H + 15m all BEARISH (5m mixed) = HIGH CONVICTION PUT (score 70-84)
  Daily + 1H + 15m + 5m all BULLISH = EXCEPTIONAL CALL entry (score 85+)
  Daily + 1H + 15m all BULLISH (5m mixed) = HIGH CONVICTION CALL (score 70-84)
  Mixed timeframes = wait for alignment before entering
"""
    else:
        intraday_section = """
═══════════════════════════════════════════════
⏱  INTRADAY SIGNALS (Entry Timing)
═══════════════════════════════════════════════
No intraday data available (market may be closed or data feed issue).
"""

    from datetime import datetime
    from zoneinfo import ZoneInfo
    _et_now = datetime.now(ZoneInfo("America/New_York"))
    _time_str = _et_now.strftime("%I:%M %p ET")
    _after_2pm = _et_now.hour >= 14

    prompt = f"""You are an elite quantitative analyst and trading AI. You have just completed a comprehensive multi-source data pull on the stock ticker: **{ticker}**

Current time: {_time_str}{"  ⚠️ It is after 2 PM ET — 0DTE is NOT permitted (theta decay too severe in final hours)." if _after_2pm else ""}

Your job is to synthesize ALL of the data below into a clear, actionable analysis. Be direct, specific, and decisive. This is not investment advice - it is scenario analysis for an informed trader.

═══════════════════════════════════════════════
📊 TECHNICAL ANALYSIS DATA
═══════════════════════════════════════════════
Last Price: ${ta.get('last_price', 'N/A')}
1D Return: {ta.get('return_1d', 0)}% | 5D Return: {ta.get('return_5d', 0)}% | 20D Return: {ta.get('return_20d', 0)}%
52W High: ${fund_d.get('52w_high', 'N/A')} | 52W Low: ${fund_d.get('52w_low', 'N/A')}

RSI ({ta.get('rsi', 'N/A')}): {ta.get('rsi_signal', 'N/A').upper()}
MACD Histogram: {ta.get('macd_hist', 'N/A')} → {ta.get('macd_crossover', 'N/A').upper()}
EMA Trend: {ta.get('ema_trend', 'N/A').upper()} (EMA9={ta.get('ema9')} / EMA21={ta.get('ema21')} / EMA50={ta.get('ema50')} / EMA200={ta.get('ema200')})
Bollinger Band Position: {ta.get('bb_pct', 'N/A')} ({ta.get('bb_signal', 'N/A').upper()}) | Upper={ta.get('bb_upper')} / Lower={ta.get('bb_lower')}
Stochastic K/D: {ta.get('stoch_k')}/{ta.get('stoch_d')} → {ta.get('stoch_signal', 'N/A').upper()}
ATR: {ta.get('atr')} ({ta.get('atr_pct')}% of price)
{tech.get('expected_move', {}).get('summary', '')}
OBV Trend: {ta.get('obv_trend', 'N/A').upper()}
Volume: {ta.get('volume_last') or 'N/A'} vs 20D MA {ta.get('volume_ma20') or 'N/A'} → {str(ta.get('volume_signal') or 'N/A').upper()} (ratio: {ta.get('volume_ratio') or 'N/A'}x)
20D Support: ${ta.get('support_20d', 'N/A')} | 20D Resistance: ${ta.get('resistance_20d', 'N/A')}

OPTIONS FLOW:
{opts.get('summary', 'No options data')}
Put/Call Ratio: {opts.get('put_call_ratio', 'N/A')}
Unusual Calls: {len(opts.get('unusual_calls', []))} flagged | Unusual Puts: {len(opts.get('unusual_puts', []))} flagged

SHORT INTEREST:
{short.get('summary', 'No short data')}
Short % of Float: {short.get('short_pct_float', 'N/A')}% | Days to Cover: {short.get('short_ratio', 'N/A')}
Short Squeeze Candidate: {'⚡ YES' if short.get('squeeze_candidate') else 'No'}

{intraday_section}
{regime_block}

═══════════════════════════════════════════════
📰 NEWS SENTIMENT
═══════════════════════════════════════════════
OVERALL: {sent.get('overall_label', 'N/A').upper()} (compound: {sent.get('overall_compound', 'N/A')}) | Articles found: {sent.get('total_mentions', 0)}
Note: Social sentiment (Reddit/StockTwits/Twitter) removed — APIs unavailable or returning non-ticker-specific data.

Top Headlines:
{top_news}

═══════════════════════════════════════════════
📋 FUNDAMENTAL & MACRO DATA
═══════════════════════════════════════════════
Company: {fund_d.get('company_name', ticker)} | Sector: {fund_d.get('sector') or 'N/A'} | Industry: {fund_d.get('industry') or 'N/A'}
Market Cap: ${fund_d.get('market_cap') or 'N/A'} | Beta: {fund_d.get('beta') or 'N/A'}
P/E (TTM): {fund_d.get('pe_ratio') or 'N/A'} | Forward P/E: {fund_d.get('forward_pe') or 'N/A'} | PEG: {fund_d.get('peg_ratio') or 'N/A'}
Revenue Growth: {fund_d.get('revenue_growth') or 'N/A'} | Earnings Growth: {fund_d.get('earnings_growth') or 'N/A'}
Analyst Consensus: {str(fund_d.get('analyst_recommend_key') or 'N/A').upper()} (mean: {fund_d.get('analyst_recommend') or 'N/A'}/5) | Price Target: ${fund_d.get('analyst_target') or 'N/A'} | # Analysts: {fund_d.get('analyst_count') or 'N/A'}
Valuation Signal: {str(fund_d.get('valuation_signal') or 'N/A').upper()}

EARNINGS: {earn.get('signal', 'N/A')} | Earnings Imminent: {'⚡ YES' if earn.get('earnings_imminent') else 'No'}

INSIDER ACTIVITY (OpenInsider — Form 4, last 90 days):
  Signal:      {inside.get('insider_signal', 'N/A').upper()}{' ⚡ CLUSTER BUY' if inside.get('cluster_buy') else ''}
  Buys (90d):  {inside.get('buys_90d', 0)} transactions | Total value: ${inside.get('buy_value_90d', 0):,.0f}
  Sells (90d): {inside.get('sells_90d', 0)} transactions | Total value: ${inside.get('sell_value_90d', 0):,.0f}
  Summary:     {inside.get('summary', 'No data')}
{chr(10).join(f"  [{t['date']}] {t['name']} ({t['title']}) {t['type']} {t['qty']:,.0f} shares @ ${t['price']:,.2f} = ${t['value']:,.0f}" for t in inside.get('recent_trades', [])[:3]) if inside.get('recent_trades') else '  No recent transactions'}

NOTE: Cluster buying (3+ insiders buying within 30 days) is one of the strongest bullish signals in markets.
Insider selling is usually less significant than buying (diversification, taxes) unless it's heavy across multiple insiders.

SEC Filings (90D): {len(fund.get('sec_filings', []))} recent filings

═══════════════════════════════════════════════
YOUR ANALYSIS TASK
═══════════════════════════════════════════════
Now produce a structured analysis with these exact sections:

**1. MARKET SCENARIO (2-3 sentences)**
What is the most plausible price scenario for {ticker} over the next 5-15 trading days based on ALL data above? Be specific.

**2. BULL CASE**
List the 3-4 strongest bullish signals from the data and what they imply.

**3. BEAR CASE**
List the 3-4 strongest bearish signals from the data and what they imply.

**4. WILDCARD / CATALYSTS**
What unexpected events or data points could significantly change the direction? (Earnings, short squeeze potential, options expiry, SEC filings, sentiment inflection, etc.)

**5. KEY LEVELS TO WATCH**
Specific price levels based on the data: support(s), resistance(s), and the critical level that changes your thesis.

**6. CONFLUENCE SCORE: X/100**
Rate the overall TRADE QUALITY on a scale of 0-100. This score answers ONE question: "how good is the actual 0-2 DTE OPTIONS TRADE here?" — NOT merely how clear the chart is. A crystal-clear directional read with no viable option trade is a LOW score, not a high one.

BOTH components must be high to score high:
  (a) SIGNAL CLARITY — how cleanly signals align for a directional move.
  (b) TRADEABILITY — can a 0-2 DTE option PROFIT on the realistic move? Tradeable only if the realistic target sits WITHIN the expected move (aim 0.5-0.8x) so premium pays before theta.

BANDS (apply BOTH):
- 0-30:   No trade — contradictory or thin data.
- 31-54:  Low — mixed for 0-2 DTE, OR clear signals but NO viable option (target beyond expected move). CORRECT for "clear chart, untradeable option."
- 55-69:  Moderate — signals agree AND a viable target fits the expected move.
- 70-84:  High — strong alignment WITH confirmation AND a viable target.
- 85-100: Exceptional — all aligned, clean tradeable target.

CRITICAL: If you output CONTRACT TYPE: NONE, the score MUST be <=54. A NONE result can NEVER score 55+. Never output 70 with NO TRADE — forbidden.

Not a bullish/bearish meter. Score TRADE QUALITY (clarity AND tradeability), not chart clarity alone.
Explain the score in 1 sentence, noting if tradeability capped it.

**7. SUGGESTED BIAS**
State the direction AND the options contract it implies:
- BULLISH → CALLS
- BEARISH → PUTS  
- NEUTRAL → NO TRADE
Include confidence level (Low/Medium/High). Example: "BEARISH → PUTS (Medium confidence)"

**8. OPTIONS TRADE SETUP (0-2 DTE)**
The trader uses SHORT-DATED OPTIONS (0-2 days to expiration) and trades BOTH directions equally — CALLS on bullish setups, PUTS on bearish setups. Theta decay is extreme so only suggest a trade if signals are clearly aligned in one direction with momentum confirmation.

CRITICAL RULES:
- If signals are clearly BEARISH and aligned → suggest PUT, not NONE
- If signals are clearly BULLISH and aligned → suggest CALL, not NONE  
- Only use NONE if signals are genuinely mixed/contradictory OR score < 55
- Do NOT default to NONE just because the setup is bearish
- TARGET REALISM (non-negotiable): the STOCK PRICE TARGET must sit WITHIN the expected move for your chosen expiry (shown in the technical data above). Backtest data shows targets beyond the expected move lose ~96% of the time even when direction is correct. Aim for 0.5–0.8x the expected move. If your thesis requires a move beyond ±1x expected move, the trade is not viable in 0-2 DTE — pick a closer target or output NONE.
- REGIME GATE (non-negotiable, overrides all other signals): Read the HARD REGIME GATE in the market context above and follow it exactly, INCLUDING the rules that FAVOR a direction — the gate is symmetric and is not only a brake on PUTs.
- DIRECTIONAL FAIRNESS (non-negotiable): Backtest data across 397 resolved trades shows PUT signals won 0.8% of the time (3 of 379) while CALL signals won 94% (17 of 18). This is NOT evidence that CALLs are easy — the CALL sample is small and will regress. It IS strong evidence that this system has been systematically over-producing PUTs and under-producing CALLs. Correct for this:
    • Evaluate the bullish case and the bearish case with EQUAL rigor before choosing. Write both honestly.
    • A ticker that is UP on the day, holding above its 15m VWAP, in a sector that is green, is a CALL candidate. Do not talk yourself into NONE or a PUT on such a ticker.
    • Do NOT treat "price below the 200 EMA" as meaningfully bearish for a 0-2 DTE trade. It is a long-horizon signal, it is true for nearly every ticker on this watchlist, and weighting it heavily is precisely what caused the historical PUT flood. Intraday structure (5m/15m/1H VWAP, EMA9/21, MACD, volume) outweighs it decisively.
    • NONE is the correct answer when signals genuinely conflict — it is NOT the safe default for a setup that simply is not bearish.
- EXPIRY SELECTION (non-negotiable): base expiry on confluence score AND setup characteristics:
    • Score 55–69 (LOW/MODERATE): use 1DTE or 2DTE only. 0DTE requires precision timing this setup cannot support — theta will punish hesitation.
    • Score 70–84 (HIGH CONVICTION): use 0DTE if the trigger is intraday and imminent (clear level, strong volume, time before 2 PM ET), otherwise 1DTE.
    • Score 85+ (EXTREME CONVICTION): prefer 0DTE. The setup is strong enough to absorb intraday noise.
    • Always use 2DTE if: VIX is elevated (>22), the setup requires a multi-session move, or the entry trigger has not yet been reached and may need time.
    • Never use 0DTE after 2 PM ET — theta decay accelerates sharply in the final two hours.

IMPORTANT: Output this section as plain key-value pairs ONLY. Do NOT use markdown tables, bold, or any formatting. Use exactly this format:

CONTRACT TYPE: CALL or PUT or NONE
EXPIRY: 0DTE or 1DTE or 2DTE
STRIKE: $X.XX
MONEYNESS: ATM or SLIGHTLY_OTM or OTM
STOCK PRICE TARGET: $X.XX (for PUTs this is where price needs to DROP to)
EST. OPTION PREMIUM: $X.XX
PROFIT TARGET: XX%
MAX LOSS: 100% of premium paid
STOP RULE: (one sentence)
ENTRY CONDITION: (one sentence — use the intraday VWAP, EMA, and volume data to specify a precise trigger: e.g. "Enter on 15-min candle close below VWAP $X with volume above 1.5x avg" or "Enter on rejection at 1H EMA9 with bearish MACD cross on 15m")
ENTRY TRIGGER PRICE: $X.XX (the single numeric stock price level from the entry condition above — for PUTs the level price must break BELOW, for CALLs the level price must break ABOVE)
AVOID IF: (one sentence)
KEY RISK: (one sentence)
NO TRADE REASON: (REQUIRED ONLY IF CONTRACT TYPE is NONE. One short sentence stating the SPECIFIC reason there is no trade. Be precise — distinguish between these cases: "signals genuinely conflict across timeframes"; "setup is clear but the realistic target lies beyond the 0-2 DTE expected move, so premium cannot pay"; "score below tradeable threshold"; "regime gate blocks this direction". Do NOT write a generic reason. If CONTRACT TYPE is CALL or PUT, omit this line.)

Be analytical, not promotional. Call out contradictions in the data. Do not make this longer than necessary.
"""
    return prompt


def run_ai_synthesis(
    ticker: str,
    tech:   Dict[str, Any],
    sent:   Dict[str, Any],
    fund:   Dict[str, Any],
) -> Dict[str, Any]:
    """Send all data to Claude and get back a synthesized analysis."""

    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY == "your_anthropic_api_key_here":
        log.error("ANTHROPIC_API_KEY not set — cannot run AI synthesis")
        return {"error": "Missing ANTHROPIC_API_KEY", "analysis": None}

    # ── Daily spend cap guard ─────────────────────────────────
    # Anthropic only supports monthly limits natively — this enforces
    # a custom daily cap before making the call at all.
    allowed, current_spend = check_daily_limit(DAILY_SPEND_LIMIT_USD)
    if not allowed:
        return {
            "error": f"daily_spend_limit_reached (${current_spend:.2f}/${DAILY_SPEND_LIMIT_USD:.2f})",
            "analysis": None,
        }

    log.info(f"Sending {ticker} data to Claude for AI synthesis...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = _build_analysis_prompt(ticker, tech, sent, fund)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=(
                "You are a professional quantitative analyst specializing in short-dated options trading. "
                "The trader you serve uses 0-2 DTE (days to expiration) options exclusively and trades "
                "BOTH directions equally — CALLS on bullish setups, PUTS on bearish setups. "
                "Theta decay is severe so only high-conviction setups are worth trading, but a clean "
                "bearish setup is just as valid a trade as a clean bullish one. Never default to NONE "
                "simply because a setup is bearish — suggest PUTs when bearish signals are clearly aligned. "
                "You are decisive and data-driven. Call out contradictions honestly. "
                "Write in a clear, structured format a trader can act on in under 60 seconds."
            ),
            messages=[{"role": "user", "content": prompt}]
        )

        analysis_text = message.content[0].text

        # Extract confluence score from the response
        import re
        score_match = re.search(r"CONFLUENCE SCORE[:\s]+(\d+)/100", analysis_text, re.IGNORECASE)
        confluence_score = int(score_match.group(1)) if score_match else 50

        # Extract bias — handles both old "BULLISH" and new "BEARISH → PUTS (Medium)" format
        bias_match = re.search(r"SUGGESTED BIAS[:\s\n*]+([A-Z]+)", analysis_text, re.IGNORECASE)
        bias = bias_match.group(1).upper() if bias_match else "NEUTRAL"

        # Extract trade setup fields (0-2 DTE options)
        # Claude sometimes outputs as plain "KEY: VALUE" and sometimes as markdown table
        # "| **KEY** | **VALUE** |" — both patterns are handled below.
        def _extract_field(patterns, text, cast=str, default=None):
            if isinstance(patterns, str):
                patterns = [patterns]
            for pattern in patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    try:
                        return cast(m.group(1).strip(" *|"))
                    except (ValueError, AttributeError):
                        continue
            return default

        def _extract_line(label, text):
            # Match both "LABEL: value" and "| **LABEL** | value |"
            patterns = [
                rf"\|\s*\*{{0,2}}{label}\*{{0,2}}\s*\|\s*(.+?)\s*\|",
                rf"{label}:\s*(.+?)(?:\n|$)",
            ]
            for pattern in patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    val = m.group(1).strip(" *|")
                    if val:
                        return val
            return None

        contract_type = _extract_field([
            r"CONTRACT TYPE:\s*(CALL|PUT|NONE)",
            r"\|\s*\*{0,2}CONTRACT TYPE\*{0,2}\s*\|\s*\*{0,2}(CALL|PUT|NONE)\*{0,2}\s*\|",
        ], analysis_text, str, "NONE")

        expiry = _extract_field([
            r"EXPIRY:\s*(0DTE|1DTE|2DTE)",
            r"\|\s*\*{0,2}EXPIRY\*{0,2}\s*\|\s*\*{0,2}(0DTE|1DTE|2DTE)\*{0,2}\s*\|",
        ], analysis_text, str)

        strike = _extract_field([
            r"STRIKE:\s*\$?([\d.]+)",
            r"\|\s*\*{0,2}STRIKE\*{0,2}\s*\|\s*\*{0,2}\$?([\d.]+)",
        ], analysis_text, float)

        # Snap the AI's strike to one that ACTUALLY EXISTS on the option
        # chain. The model invents strikes like $321 for AAPL or $234 for
        # AMZN, but those trade in $2.50 increments at those levels — the
        # contract doesn't exist and the trade can't be placed as written.
        if strike and contract_type in ("CALL", "PUT"):
            try:
                import yfinance as yf
                from datetime import date, timedelta
                _days = {"0DTE": 0, "1DTE": 1, "2DTE": 2}.get(expiry, 0)
                _t = datetime.now().date()
                _added = 0
                while _added < _days:
                    _t += timedelta(days=1)
                    if _t.weekday() < 5:
                        _added += 1
                _exps = list(yf.Ticker(ticker).options or [])
                _exp = next((e for e in _exps
                             if date.fromisoformat(e) >= _t), None)
                if _exp:
                    _ch = yf.Ticker(ticker).option_chain(_exp)
                    _tbl = _ch.calls if contract_type == "CALL" else _ch.puts
                    if not _tbl.empty:
                        _strikes = _tbl["strike"].tolist()
                        _near = min(_strikes, key=lambda s: abs(s - strike))
                        if abs(_near - strike) > 0.01:
                            log.info(f"{ticker}: strike ${strike} not tradeable "
                                     f"— using ${_near} (exp {_exp})")
                            strike = float(_near)
            except Exception as _e:
                log.debug(f"{ticker}: strike validation skipped — {_e}")

        moneyness = _extract_field([
            r"MONEYNESS:\s*(ATM|SLIGHTLY_OTM|OTM)",
            r"\|\s*\*{0,2}MONEYNESS\*{0,2}\s*\|\s*\*{0,2}(ATM|SLIGHTLY_OTM|OTM)\*{0,2}\s*\|",
        ], analysis_text, str)

        stock_target = _extract_field([
            r"STOCK PRICE TARGET:\s*\$?([\d.]+)",
            r"\|\s*\*{0,2}STOCK PRICE TARGET\*{0,2}\s*\|\s*\*{0,2}\$?([\d.]+)",
        ], analysis_text, float)

        est_premium = _extract_field([
            r"EST\.? OPTION PREMIUM:\s*\$?([\d.]+)",
            r"\|\s*\*{0,2}EST\.? OPTION PREMIUM\*{0,2}\s*\|\s*\*{0,2}\$?([\d.]+)",
        ], analysis_text, float)

        profit_target = _extract_field([
            r"PROFIT TARGET:\s*(\d+)%",
            r"\|\s*\*{0,2}PROFIT TARGET\*{0,2}\s*\|\s*\*{0,2}(\d+)%",
        ], analysis_text, int)

        stop_rule       = _extract_line("STOP RULE", analysis_text)
        entry_condition = _extract_line("ENTRY CONDITION", analysis_text)
        avoid_if        = _extract_line("AVOID IF", analysis_text)
        key_risk        = _extract_line("KEY RISK", analysis_text)
        no_trade_reason = _extract_line("NO TRADE REASON", analysis_text)

        entry_trigger = _extract_field([
            r"ENTRY TRIGGER PRICE:\s*\$?([\d.]+)",
            r"\|\s*\*{0,2}ENTRY TRIGGER PRICE\*{0,2}\s*\|\s*\*{0,2}\$?([\d.]+)",
        ], analysis_text, float)

        # HARD ENFORCEMENT: NONE can never score 55+ (untradeable = low quality).
        # Makes the "72/100 but NO TRADE" contradiction structurally impossible.
        if contract_type == "NONE" and confluence_score >= 55:
            log.info(f"{ticker}: NO TRADE with score {confluence_score} — clamping to 45")
            confluence_score = 45

        # Rough quality flag based on contract type + confluence
        if contract_type == "NONE":
            setup_quality = "NO TRADE"
        elif confluence_score >= 70:
            setup_quality = "HIGH CONVICTION"
        elif confluence_score >= 55:
            setup_quality = "MODERATE"
        else:
            setup_quality = "LOW CONVICTION"

        # ── Earnings guard ────────────────────────────────────────
        # Hard override: never suggest a 0-2 DTE trade into earnings.
        # IV crush after the print can wipe a winning directional bet.
        earnings_imminent = fund.get("earnings", {}).get("earnings_imminent", False)
        days_to_earnings  = fund.get("earnings", {}).get("days_to_earnings")
        if earnings_imminent and contract_type != "NONE":
            log.warning(
                f"⚡ Earnings guard triggered for {ticker} "
                f"({days_to_earnings}d to earnings) — forcing NO TRADE"
            )
            contract_type   = "NONE"
            setup_quality   = "NO TRADE — EARNINGS"
            key_risk        = (
                f"EARNINGS IN {days_to_earnings} DAY(S) — IV crush risk is extreme. "
                f"Do not trade 0-2 DTE options into an earnings print."
            )

        # ── Real option premium (Alpaca) ─────────────────────────
        # Replace Claude's ESTIMATED premium with the actual live market
        # price when available. The estimate was often ~2x off (e.g. card
        # showing $4.50 when the real contract was $2.07), which misled
        # anyone reading the card about cost and risk. `strike` here has
        # already been snapped to a real tradable contract above, so the
        # premium lookup is for a contract that genuinely exists.
        real_premium = None
        if contract_type in ("CALL", "PUT") and strike:
            try:
                from core import alpaca_data
                from datetime import date, timedelta
                if alpaca_data.is_enabled():
                    _days = {"0DTE": 0, "1DTE": 1, "2DTE": 2}.get(expiry, 0)
                    _t = datetime.now().date()
                    _added = 0
                    while _added < _days:
                        _t += timedelta(days=1)
                        if _t.weekday() < 5:
                            _added += 1
                    _exp = _t.isoformat()
                    real_premium = alpaca_data.get_option_mid(
                        ticker, contract_type, float(strike), _exp)
                    if real_premium is not None:
                        log.info(f"{ticker}: real premium ${real_premium} "
                                 f"(was est ${est_premium})")
            except Exception as _e:
                log.debug(f"{ticker}: real premium lookup skipped — {_e}")

        # Use the real premium on the card when we have it; else the estimate.
        display_premium = real_premium if real_premium is not None else est_premium

        trade_setup = {
            "contract_type":   contract_type,
            "expiry":          expiry,
            "strike":          strike,
            "moneyness":       moneyness,
            "stock_target":    stock_target,
            "est_premium":     display_premium,
            "est_premium_ai":  est_premium,
            "premium_is_real": real_premium is not None,
            "profit_target":   profit_target,
            "stop_rule":       stop_rule,
            "entry_condition": entry_condition,
            "entry_trigger":   entry_trigger,
            "avoid_if":        avoid_if,
            "key_risk":        key_risk,
            "setup_quality":   setup_quality,
            "no_trade_reason": no_trade_reason,
        }

        # ── Expected-move target validation ───────────────────
        em_data = tech.get("expected_move", {})
        if em_data.get("method") and contract_type in ("CALL", "PUT"):
            try:
                from core.expected_move import validate_target
                vt = validate_target(
                    contract_type=contract_type,
                    expiry=expiry,
                    spot=ta.get("last_price"),
                    stock_target=stock_target,
                    expected_move=em_data,
                )
                if vt["adjusted"]:
                    trade_setup["stock_target"]    = vt["target"]
                    trade_setup["target_original"] = vt["target_original"]
                    trade_setup["target_note"]     = vt["note"]
                    log.warning(f"🎯 {ticker}: {vt['note']}")
                trade_setup["target_em_ratio"] = vt.get("target_em_ratio")
                trade_setup["em_pct"]          = vt.get("em_pct")
            except Exception as e:
                log.debug(f"{ticker}: validate_target failed — {e}")

        # ── Entry-trigger geometry ────────────────────────────
        # Repair (never discard) triggers that sit at or through spot.
        if contract_type in ("CALL", "PUT"):
            try:
                from config.settings import (MIN_TRIGGER_DIST_PCT,
                                             TRIGGER_ATR_FRACTION)
                _spot = float(ta.get("last_price") or 0)
                _trg  = float(entry_trigger or 0)
                if _spot > 0 and _trg > 0:
                    _min_off = _spot * (MIN_TRIGGER_DIST_PCT / 100.0)
                    try:
                        _atr_off = float(ta.get("atr") or 0) * TRIGGER_ATR_FRACTION
                    except (TypeError, ValueError):
                        _atr_off = 0.0
                    _off = max(_min_off, _atr_off)
                    if contract_type == "PUT":
                        _needed = _spot - _min_off      # must sit below spot
                        _bad = _trg >= _needed
                        _repaired = round(_spot - _off, 2)
                    else:
                        _needed = _spot + _min_off      # must sit above spot
                        _bad = _trg <= _needed
                        _repaired = round(_spot + _off, 2)
                    if _bad:
                        trade_setup["trigger_original"] = _trg
                        trade_setup["entry_trigger"]    = _repaired
                        trade_setup["trigger_adjusted"] = True
                        trade_setup["trigger_note"] = (
                            f"AI trigger ${_trg} was at/through spot ${_spot} "
                            f"— moved to ${_repaired} so the entry requires "
                            f"real confirmation")
                        log.warning(f"⚑ {ticker}: {trade_setup['trigger_note']}")
                    trade_setup["trigger_dist_pct"] = round(
                        abs(trade_setup["entry_trigger"] - _spot) / _spot * 100, 3)
            except Exception as e:
                log.debug(f"{ticker}: trigger geometry check failed — {e}")

        # ── Verdict tier (TRADE / WATCH / RISKY) ──────────────────
        # Concise honest read driving the card banner and feed. Based on
        # tradeability: how the target fits the expected move.
        if contract_type in ("CALL", "PUT"):
            _ratio = trade_setup.get("target_em_ratio")
            _clamped = bool(trade_setup.get("target_original"))
            if _clamped or (_ratio is not None and _ratio > 1.0):
                verdict, verdict_note = "RISKY", ("Target near/beyond the expected "
                    "move — premium may not pay before theta. Aggressive only.")
            elif _ratio is not None and _ratio > 0.8:
                verdict, verdict_note = "WATCH", ("Direction clear but the move is "
                    "tight — only enter on strong confirmation.")
            else:
                verdict, verdict_note = "TRADE", "Setup and target both fit — tradeable as written."
            trade_setup["verdict"]      = verdict
            trade_setup["verdict_note"] = verdict_note

            # ── Premium-based exit rule (backtested) ──────────────
            # Derived from replaying real intraday premium paths across
            # tracked trades: a stop near -30% paired with a take-profit
            # near +55% produced the best expectancy of the levels tested.
            # Cutting losers early mattered more than capping winners —
            # a tight +15% take-profit RAISED win rate but LOWERED
            # returns, because it clips the large winners that carry the
            # system. These sit alongside (not instead of) the AI's
            # price-level stop: whichever triggers first is the exit.
            trade_setup["premium_stop_pct"]   = -30
            trade_setup["premium_target_pct"] = 55

        log.info(
            f"AI synthesis complete for {ticker} | Score={confluence_score} | Bias={bias} | "
            f"{contract_type} {expiry} ${strike} | Quality={setup_quality}"
        )

        if confluence_score >= CONFLUENCE_THRESHOLD:
            log_banner(log, ticker, confluence_score)

        # Record actual spend for the daily cap tracker
        input_tokens  = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        record_spend(ticker, message.model, input_tokens, output_tokens)

        return {
            "ticker":            ticker,
            "timestamp":         datetime.now().isoformat(),
            "analysis":          analysis_text,
            "confluence_score":  confluence_score,
            "suggested_bias":    bias,
            "trade_setup":       trade_setup,
            "model":             message.model,
            "tokens_used":       input_tokens + output_tokens,
        }

    except anthropic.APIError as e:
        log.error(f"Anthropic API error for {ticker}: {e}")
        return {"error": str(e), "analysis": None}
    except Exception as e:
        log.error(f"AI synthesis unexpected error for {ticker}: {e}")
        return {"error": str(e), "analysis": None}
