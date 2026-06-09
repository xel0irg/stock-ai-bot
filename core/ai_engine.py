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
from config.settings import ANTHROPIC_API_KEY, CONFLUENCE_THRESHOLD

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
    reddit = sent.get("reddit", {}).get("aggregated", {})
    twits  = sent.get("stocktwits", {})
    news   = sent.get("news", {}).get("aggregated", {})
    tw     = sent.get("twitter", {}).get("aggregated", {})

    # Pull top 3 news headlines
    top_news = "\n".join([
        f"  - [{a.get('source')}] {a.get('title', '')}"
        for a in sent.get("news", {}).get("articles", [])[:3]
    ]) or "  No news found"

    # Pull top 3 reddit posts
    top_reddit = "\n".join([
        f"  - [{p.get('source')}] {p.get('title', '')} (upvotes: {p.get('score', 0)})"
        for p in sent.get("reddit", {}).get("posts", [])[:3]
    ]) or "  No Reddit posts found"

    prompt = f"""You are an elite quantitative analyst and trading AI. You have just completed a comprehensive multi-source data pull on the stock ticker: **{ticker}**

Your job is to synthesize ALL of the data below into a clear, actionable analysis. Be direct, specific, and decisive. This is not investment advice — it is scenario analysis for an informed trader.

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

═══════════════════════════════════════════════
📰 SENTIMENT DATA
═══════════════════════════════════════════════
OVERALL SENTIMENT: {sent.get('overall_label', 'N/A').upper()} (compound: {sent.get('overall_compound', 'N/A')})
Total Mentions Across Platforms: {sent.get('total_mentions', 0)}

Reddit:       {reddit.get('sentiment_label', 'N/A').upper()} | {reddit.get('count', 0)} posts | Bull {reddit.get('bullish_pct', 0)}% / Bear {reddit.get('bearish_pct', 0)}%
StockTwits:   {twits.get('aggregated', {}).get('sentiment_label', 'N/A').upper()} | Native Bull: {twits.get('native_bull_pct', 0)}% | Bull {twits.get('bull_count', 0)} / Bear {twits.get('bear_count', 0)}
Twitter/X:    {tw.get('sentiment_label', 'N/A').upper()} | {tw.get('count', 0)} tweets
News:         {news.get('sentiment_label', 'N/A').upper()} | {news.get('count', 0)} articles

Top News Headlines:
{top_news}

Top Reddit Posts:
{top_reddit}

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
INSIDER ACTIVITY: {inside.get('insider_signal', 'N/A').upper()} | Form 4s (90D): {inside.get('form4_count_90d', 0)}
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
Rate the overall directional conviction on a scale of 0-100 where:
- 0-35: Bearish
- 36-50: Weak/uncertain
- 51-65: Slightly bullish
- 66-80: Bullish
- 81-100: Strongly bullish
Explain the score in 1 sentence.

**7. SUGGESTED BIAS**
One word: BULLISH / BEARISH / NEUTRAL + confidence level (Low/Medium/High)

**8. TRADE SETUP**
Based on the bias above, give ONE concrete trade setup a swing trader could act on.
Use EXACTLY this format (fill in real numbers, no ranges for entry/stop/target):

TRADE DIRECTION: LONG or SHORT or NONE (if score < 50, use NONE)
ENTRY PRICE: $X.XX (specific level to enter — e.g. on a bounce off support, or a breakout level)
STOP LOSS: $X.XX (the level that invalidates the thesis — where you exit if wrong)
TARGET 1: $X.XX (first take-profit level — conservative)
TARGET 2: $X.XX (second take-profit level — full target)
RISK/REWARD: X.X:1 (calculate as (Target1 - Entry) / (Entry - Stop) for LONG, reversed for SHORT)
POSITION SIZE NOTE: one sentence on how aggressive to size this given the confluence score
ENTRY CONDITION: one sentence describing WHEN exactly to enter (e.g. "Enter on a confirmed bounce off $391 with volume above 1.5x average")
TRADE VALID UNTIL: X days (how many trading days before this setup expires if not triggered)

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

    log.info(f"Sending {ticker} data to Claude for AI synthesis...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = _build_analysis_prompt(ticker, tech, sent, fund)

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2048,
            system=(
                "You are a professional quantitative analyst specializing in short-to-medium "
                "term stock analysis. You synthesize technical, fundamental, and sentiment "
                "data into clear, actionable scenario analysis. You are decisive, data-driven, "
                "and highlight both bullish and bearish signals honestly. You write in a clear, "
                "structured format that a trader can act on quickly."
            ),
            messages=[{"role": "user", "content": prompt}]
        )

        analysis_text = message.content[0].text

        # Extract confluence score from the response
        import re
        score_match = re.search(r"CONFLUENCE SCORE[:\s]+(\d+)/100", analysis_text, re.IGNORECASE)
        confluence_score = int(score_match.group(1)) if score_match else 50

        # Extract bias
        bias_match = re.search(r"SUGGESTED BIAS[:\s\n]+([A-Z]+)", analysis_text, re.IGNORECASE)
        bias = bias_match.group(1).upper() if bias_match else "NEUTRAL"

        # Extract trade setup fields
        def _extract_field(pattern, text, cast=str, default=None):
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                try:
                    return cast(m.group(1).strip())
                except (ValueError, AttributeError):
                    return default
            return default

        trade_direction = _extract_field(r"TRADE DIRECTION:\s*(LONG|SHORT|NONE)", analysis_text, str, "NONE")
        entry_price     = _extract_field(r"ENTRY PRICE:\s*\$?([\d.]+)", analysis_text, float)
        stop_loss       = _extract_field(r"STOP LOSS:\s*\$?([\d.]+)", analysis_text, float)
        target_1        = _extract_field(r"TARGET 1:\s*\$?([\d.]+)", analysis_text, float)
        target_2        = _extract_field(r"TARGET 2:\s*\$?([\d.]+)", analysis_text, float)
        risk_reward     = _extract_field(r"RISK/REWARD:\s*([\d.]+):1", analysis_text, float)
        size_note_m     = re.search(r"POSITION SIZE NOTE:\s*(.+?)(?:\n|$)", analysis_text, re.IGNORECASE)
        size_note       = size_note_m.group(1).strip() if size_note_m else None
        entry_cond_m    = re.search(r"ENTRY CONDITION:\s*(.+?)(?:\n|$)", analysis_text, re.IGNORECASE)
        entry_condition = entry_cond_m.group(1).strip() if entry_cond_m else None
        valid_days      = _extract_field(r"TRADE VALID UNTIL:\s*(\d+)\s*days?", analysis_text, int)

        rr_quality = (
            "POOR"       if risk_reward and risk_reward < 1.5  else
            "ACCEPTABLE" if risk_reward and risk_reward < 2.0  else
            "GOOD"       if risk_reward                        else
            "UNKNOWN"
        )

        trade_setup = {
            "direction":       trade_direction,
            "entry_price":     entry_price,
            "stop_loss":       stop_loss,
            "target_1":        target_1,
            "target_2":        target_2,
            "risk_reward":     risk_reward,
            "rr_quality":      rr_quality,
            "size_note":       size_note,
            "entry_condition": entry_condition,
            "valid_days":      valid_days,
        }

        log.info(
            f"AI synthesis complete for {ticker} | Score={confluence_score} | Bias={bias} | "
            f"Trade={trade_direction} | R/R={risk_reward}:1 ({rr_quality})"
        )

        if confluence_score >= CONFLUENCE_THRESHOLD:
            log_banner(log, ticker, confluence_score)

        return {
            "ticker":            ticker,
            "timestamp":         datetime.now().isoformat(),
            "analysis":          analysis_text,
            "confluence_score":  confluence_score,
            "suggested_bias":    bias,
            "trade_setup":       trade_setup,
            "model":             message.model,
            "tokens_used":       message.usage.input_tokens + message.usage.output_tokens,
        }

    except anthropic.APIError as e:
        log.error(f"Anthropic API error for {ticker}: {e}")
        return {"error": str(e), "analysis": None}
    except Exception as e:
        log.error(f"AI synthesis unexpected error for {ticker}: {e}")
        return {"error": str(e), "analysis": None}
