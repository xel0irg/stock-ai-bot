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

HEADERS = {"User-Agent": "Degenic$/1.0 (research@degenic.com)"}


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


def fetch_openinsider(ticker: str) -> Dict[str, Any]:
    """
    Scrape OpenInsider for real Form 4 insider buy/sell transactions.
    No API key needed — public site, structured HTML table.

    Signals we extract:
    - Recent insider buys (Purchase) in last 90 days
    - Cluster buying (3+ insiders buying in same 30-day window)
    - Total $ value of buys vs sells
    - Most recent transaction details
    """
    result = {
        "has_data":        False,
        "buys_90d":        0,
        "sells_90d":       0,
        "buy_value_90d":   0.0,
        "sell_value_90d":  0.0,
        "cluster_buy":     False,
        "recent_trades":   [],
        "insider_signal":  "NO_RECENT_INSIDER_FILINGS",
        "summary":         "No insider data",
    }

    try:
        url = (
            f"http://openinsider.com/screener?"
            f"s={ticker}&o=&pl=&ph=&ll=&lh=&fd=90&td=&tdr=&fdlyl=&fdlyh="
            f"&daysago=&xs=1&vl=&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999"
            f"&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h="
            f"&sortcol=0&cnt=20&Action=1"
        )
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            log.warning(f"OpenInsider returned {resp.status_code} for {ticker}")
            return result

        # Parse the HTML table
        from html.parser import HTMLParser

        class TableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_table = False
                self.in_row   = False
                self.in_cell  = False
                self.rows     = []
                self.cur_row  = []
                self.cur_cell = ""
                self.td_count = 0

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                if tag == "table" and "tinytable" in attrs_dict.get("class", ""):
                    self.in_table = True
                if self.in_table and tag == "tr":
                    self.in_row = True
                    self.cur_row = []
                if self.in_row and tag in ("td", "th"):
                    self.in_cell = True
                    self.cur_cell = ""

            def handle_endtag(self, tag):
                if tag == "table" and self.in_table:
                    self.in_table = False
                if self.in_table and tag == "tr":
                    self.in_row = False
                    if self.cur_row:
                        self.rows.append(self.cur_row)
                if self.in_row and tag in ("td", "th"):
                    self.in_cell = False
                    self.cur_row.append(self.cur_cell.strip())

            def handle_data(self, data):
                if self.in_cell:
                    self.cur_cell += data

        parser = TableParser()
        parser.feed(resp.text)

        rows = parser.rows
        if len(rows) < 2:
            return result

        # Column indices for OpenInsider tinytable:
        # 0=X, 1=Filing Date, 2=Trade Date, 3=Ticker, 4=Company,
        # 5=Insider Name, 6=Title, 7=Trade Type, 8=Price,
        # 9=Qty, 10=Owned, 11=ΔOwn, 12=Value
        COL_DATE  = 2   # Trade date
        COL_NAME  = 5   # Insider name
        COL_TITLE = 6   # Title (CEO, CFO, Director, etc.)
        COL_TYPE  = 7   # P = Purchase, S = Sale
        COL_PRICE = 8
        COL_QTY   = 9
        COL_VALUE = 12

        buys, sells = [], []
        now = datetime.now()

        for row in rows[1:]:  # skip header
            if len(row) < 13:
                continue
            trade_type = row[COL_TYPE].strip().upper()
            if trade_type not in ("P - PURCHASE", "S - SALE", "P", "S"):
                continue

            # Parse value — strip $, commas
            def _parse_num(s):
                try:
                    return float(s.replace("$", "").replace(",", "").replace("+", "").strip())
                except (ValueError, AttributeError):
                    return 0.0

            trade = {
                "date":  row[COL_DATE].strip(),
                "name":  row[COL_NAME].strip(),
                "title": row[COL_TITLE].strip(),
                "type":  "BUY" if "P" in trade_type else "SELL",
                "price": _parse_num(row[COL_PRICE]),
                "qty":   _parse_num(row[COL_QTY]),
                "value": abs(_parse_num(row[COL_VALUE])),
            }

            if trade["type"] == "BUY":
                buys.append(trade)
                result["buy_value_90d"] += trade["value"]
            else:
                sells.append(trade)
                result["sell_value_90d"] += trade["value"]

        result["buys_90d"]      = len(buys)
        result["sells_90d"]     = len(sells)
        result["recent_trades"] = (buys + sells)[:5]  # top 5 most recent

        # Cluster buy: 3+ insiders buying within 30 days
        if len(buys) >= 3:
            # Check if 3 buys happened within any 30-day window
            try:
                dates = []
                for b in buys:
                    try:
                        d = datetime.strptime(b["date"], "%Y-%m-%d")
                        dates.append(d)
                    except ValueError:
                        pass
                dates.sort()
                for i in range(len(dates) - 2):
                    if (dates[i+2] - dates[i]).days <= 30:
                        result["cluster_buy"] = True
                        break
            except Exception:
                pass

        # Determine signal
        total_buy_value  = result["buy_value_90d"]
        total_sell_value = result["sell_value_90d"]

        if result["cluster_buy"] and total_buy_value > 100_000:
            result["insider_signal"] = "CLUSTER_BUY"
        elif len(buys) >= 2 and total_buy_value > 50_000:
            result["insider_signal"] = "MULTIPLE_INSIDERS_BUYING"
        elif len(buys) == 1 and total_buy_value > 100_000:
            result["insider_signal"] = "SIGNIFICANT_INSIDER_BUY"
        elif len(buys) >= 1:
            result["insider_signal"] = "MINOR_INSIDER_BUY"
        elif len(sells) > len(buys) * 2:
            result["insider_signal"] = "INSIDER_SELLING"
        elif len(sells) > 0:
            result["insider_signal"] = "SOME_INSIDER_SELLING"
        else:
            result["insider_signal"] = "NO_RECENT_INSIDER_FILINGS"

        result["has_data"] = True

        # Human-readable summary
        buy_str  = f"{len(buys)} buys (${total_buy_value:,.0f})"  if buys  else "0 buys"
        sell_str = f"{len(sells)} sells (${total_sell_value:,.0f})" if sells else "0 sells"
        cluster  = " ⚡ CLUSTER BUY" if result["cluster_buy"] else ""
        result["summary"] = f"{result['insider_signal']}{cluster} | 90d: {buy_str} / {sell_str}"

        log.info(f"OpenInsider for {ticker}: {result['summary']}")

    except Exception as e:
        log.warning(f"OpenInsider scrape error for {ticker}: {e}")

    return result


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
            f"P/E={pe} | Analyst={str(result.get('analyst_recommend_key') or 'N/A').upper()}"
        )

    except Exception as e:
        log.warning(f"Fundamentals error for {ticker}: {e}")

    return result


# ══════════════════════════════════════════════════════════
#  EARNINGS CALENDAR
# ══════════════════════════════════════════════════════════
def check_watchlist_earnings(watchlist: List[str], lookahead_days: int = 5) -> Dict[str, Any]:
    """
    Proactive earnings check across the entire watchlist — run once before
    market open rather than waiting for a regular per-ticker scan to discover it.

    Returns a summary with tickers grouped by urgency:
    - today: earnings today (highest risk — exclude from 0-2 DTE entirely)
    - tomorrow: earnings tomorrow (high risk — IV crush incoming)
    - this_week: earnings within lookahead_days (caution — IV may already be elevated)
    """
    result = {
        "checked_at":  datetime.now().isoformat(),
        "today":       [],
        "tomorrow":    [],
        "this_week":   [],
        "clear":       [],
        "has_alerts":  False,
    }

    for ticker in watchlist:
        try:
            tk   = yf.Ticker(ticker)
            info = tk.info
            earnings_date = info.get("earningsDate") or info.get("earningsTimestamp")

            if earnings_date and isinstance(earnings_date, (list, tuple)):
                earnings_date = earnings_date[0]

            if not earnings_date:
                result["clear"].append(ticker)
                continue

            if hasattr(earnings_date, "timestamp"):
                ed = datetime.fromtimestamp(earnings_date.timestamp())
            else:
                ed = datetime.fromtimestamp(int(earnings_date))

            days = (ed.date() - datetime.now().date()).days

            entry = {
                "ticker":        ticker,
                "earnings_date": ed.strftime("%Y-%m-%d"),
                "days_away":     days,
            }

            if days == 0:
                result["today"].append(entry)
                result["has_alerts"] = True
            elif days == 1:
                result["tomorrow"].append(entry)
                result["has_alerts"] = True
            elif 1 < days <= lookahead_days:
                result["this_week"].append(entry)
                result["has_alerts"] = True
            else:
                result["clear"].append(ticker)

        except Exception as e:
            log.debug(f"Earnings check failed for {ticker}: {e}")
            result["clear"].append(ticker)

    log.info(
        f"Watchlist earnings check: {len(result['today'])} today, "
        f"{len(result['tomorrow'])} tomorrow, {len(result['this_week'])} this week, "
        f"{len(result['clear'])} clear"
    )
    return result


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
    insider      = fetch_openinsider(ticker)
    earnings     = check_earnings_proximity(ticker, fundamentals)

    return {
        "ticker":       ticker,
        "timestamp":    datetime.now().isoformat(),
        "fundamentals": fundamentals,
        "sec_filings":  filings,
        "insider":      insider,
        "earnings":     earnings,
    }
