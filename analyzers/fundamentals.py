"""
analyzers/fundamentals.py — SEC filings, earnings, insider trading, company data
"""
from __future__ import annotations
import requests
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, Any, List

from core.logger import get_logger

log = get_logger("Fundamentals")

HEADERS = {"User-Agent": "StockAIBot/1.0 (research@stockaibot.com)"}


# ══════════════════════════════════════════════════════════
#  SEC EDGAR
# ══════════════════════════════════════════════════════════
def get_cik(ticker: str) -> str | None:
    """Resolve ticker to SEC CIK number."""
    try:
        url  = "https://www.sec.gov/files/company_tickers.json"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        data = resp.json()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker.upper():
                return str(entry["cik_str"]).zfill(10)
    except Exception as e:
        log.warning(f"CIK lookup failed for {ticker}: {e}")
    return None


def fetch_recent_filings(ticker: str, form_types: List[str] = None) -> List[Dict]:
    """
    Fetch recent SEC filings (8-K, 10-Q, 10-K, 4 for insider trading).
    Returns a list of recent filings with form type and description.
    """
    if form_types is None:
        form_types = ["8-K", "10-Q", "10-K", "4", "SC 13G", "SC 13D"]

    cik = get_cik(ticker)
    if not cik:
        log.warning(f"Could not find CIK for {ticker}")
        return []

    filings_out = []
    try:
        url  = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return []

        data     = resp.json()
        recent   = data.get("filings", {}).get("recent", {})
        forms    = recent.get("form", [])
        dates    = recent.get("filingDate", [])
        accs     = recent.get("accessionNumber", [])
        descs    = recent.get("primaryDocument", [])

        cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

        for i, (form, date, acc, desc) in enumerate(zip(forms, dates, accs, descs)):
            if form in form_types and date >= cutoff:
                link = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{int(cik)}/{acc.replace('-', '')}/{desc}"
                )
                filings_out.append({
                    "form":   form,
                    "date":   date,
                    "link":   link,
                    "acc":    acc,
                })
            if len(filings_out) >= 10:
                break

        log.info(f"SEC filings for {ticker}: {len(filings_out)} recent filings found")

    except Exception as e:
        log.warning(f"SEC EDGAR error for {ticker}: {e}")

    return filings_out


def parse_insider_trades(ticker: str, filings: List[Dict]) -> Dict[str, Any]:
    """Parse Form 4 filings to detect insider buying/selling."""
    form4 = [f for f in filings if f["form"] == "4"]
    # We summarize counts; deep XML parsing is optional
    return {
        "form4_count_90d": len(form4),
        "latest_form4": form4[0]["date"] if form4 else None,
        "insider_signal": (
            "active_insider_reporting" if len(form4) > 3 else
            "some_insider_activity"    if len(form4) > 0 else
            "no_recent_insider_filings"
        ),
    }


# ══════════════════════════════════════════════════════════
#  YAHOO FINANCE FUNDAMENTALS
# ══════════════════════════════════════════════════════════
def fetch_fundamentals(ticker: str) -> Dict[str, Any]:
    """Pull key fundamental metrics from yfinance."""
    result: Dict[str, Any] = {}
    try:
        info = yf.Ticker(ticker).info

        # Valuation
        result["market_cap"]   = info.get("marketCap")
        result["pe_ratio"]     = info.get("trailingPE")
        result["forward_pe"]   = info.get("forwardPE")
        result["peg_ratio"]    = info.get("pegRatio")
        result["ps_ratio"]     = info.get("priceToSalesTrailing12Months")
        result["pb_ratio"]     = info.get("priceToBook")
        result["ev_ebitda"]    = info.get("enterpriseToEbitda")

        # Growth / Earnings
        result["eps_ttm"]        = info.get("trailingEps")
        result["eps_forward"]    = info.get("forwardEps")
        result["revenue_growth"] = info.get("revenueGrowth")
        result["earnings_growth"]= info.get("earningsGrowth")
        result["profit_margin"]  = info.get("profitMargins")

        # Company info
        result["company_name"]   = info.get("longName", ticker)
        result["sector"]         = info.get("sector")
        result["industry"]       = info.get("industry")
        result["description"]    = (info.get("longBusinessSummary") or "")[:300]

        # Next earnings date
        result["next_earnings"]  = info.get("earningsDate")
        result["ex_dividend"]    = info.get("exDividendDate")

        # Analyst ratings
        result["analyst_target"]        = info.get("targetMeanPrice")
        result["analyst_recommend"]     = info.get("recommendationMean")  # 1=Strong Buy 5=Sell
        result["analyst_recommend_key"] = info.get("recommendationKey")
        result["analyst_count"]         = info.get("numberOfAnalystOpinions")

        # 52-week range
        result["52w_high"] = info.get("fiftyTwoWeekHigh")
        result["52w_low"]  = info.get("fiftyTwoWeekLow")
        result["beta"]     = info.get("beta")

        # Valuation signal
        pe = result.get("pe_ratio")
        result["valuation_signal"] = (
            "deeply_undervalued" if pe and pe < 10 else
            "undervalued"        if pe and pe < 20 else
            "fairly_valued"      if pe and pe < 30 else
            "elevated"           if pe and pe < 50 else
            "speculative"        if pe else
            "no_pe_data"
        )

        log.info(
            f"Fundamentals for {ticker}: {result.get('company_name')} | "
            f"P/E={pe} | Analyst={result.get('analyst_recommend_key', 'N/A').upper()}"
        )

    except Exception as e:
        log.warning(f"Fundamentals error for {ticker}: {e}")

    return result


# ══════════════════════════════════════════════════════════
#  EARNINGS CALENDAR
# ══════════════════════════════════════════════════════════
def check_earnings_proximity(ticker: str, fundamentals: Dict) -> Dict[str, Any]:
    """Check if earnings is imminent — a major catalyst for volatility."""
    result = {"earnings_imminent": False, "days_to_earnings": None}
    try:
        earnings_date = fundamentals.get("next_earnings")
        if earnings_date and isinstance(earnings_date, (list, tuple)):
            earnings_date = earnings_date[0]  # yfinance returns a list sometimes
        if earnings_date:
            if hasattr(earnings_date, "timestamp"):
                ed = datetime.fromtimestamp(earnings_date.timestamp())
            else:
                ed = datetime.fromtimestamp(int(earnings_date))
            days = (ed - datetime.now()).days
            result["days_to_earnings"] = days
            result["earnings_date"]    = ed.strftime("%Y-%m-%d")
            if 0 <= days <= 14:
                result["earnings_imminent"] = True
                result["signal"] = f"⚡ EARNINGS IN {days} DAYS — Expect elevated volatility"
                log.info(f"{ticker}: Earnings in {days} days — catalyst alert!")
            else:
                result["signal"] = f"Earnings in ~{max(days,0)} days"
    except Exception as e:
        log.debug(f"Earnings proximity check error: {e}")
    return result


def run_fundamental_analysis(ticker: str) -> Dict[str, Any]:
    """Full fundamentals pipeline for one ticker."""
    log.info(f"Running fundamental analysis for {ticker}...")

    fundamentals = fetch_fundamentals(ticker)
    filings      = fetch_recent_filings(ticker)
    insider      = parse_insider_trades(ticker, filings)
    earnings     = check_earnings_proximity(ticker, fundamentals)

    return {
        "ticker":       ticker,
        "timestamp":    datetime.now().isoformat(),
        "fundamentals": fundamentals,
        "sec_filings":  filings,
        "insider":      insider,
        "earnings":     earnings,
    }
