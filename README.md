# 📈 Stock AI Bot

An AI-powered stock analysis engine that synthesizes **technical indicators, social sentiment, options flow, SEC filings, and fundamental data** into actionable scenario analysis — powered by Claude.

---

## 🧠 What It Does

For each ticker in your watchlist, the bot:

1. **Technical Analysis** — RSI, MACD, Bollinger Bands, EMAs (9/21/50/200), ATR, Stochastic, OBV, volume anomaly detection
2. **Options Flow** — Yahoo Finance options chain scan for unusual call/put activity, put/call ratio
3. **Short Interest** — % of float short, days-to-cover, short squeeze candidates
4. **Sentiment Scraping** — Reddit (WSB, stocks, investing, options, etc.), StockTwits (with native bull/bear tags), Twitter/X, and 6 financial news RSS feeds
5. **Fundamental Analysis** — P/E, forward P/E, PEG, revenue/earnings growth, analyst consensus and price targets
6. **SEC Filings** — Recent 8-K, 10-Q, 10-K, Form 4 (insider trading) via EDGAR API
7. **Earnings Proximity** — Flags when earnings are within 14 days (major volatility catalyst)
8. **Claude AI Synthesis** — All data is sent to Claude, which produces a structured scenario analysis with a Confluence Score (0–100) and directional bias

---

## 🗂 Project Structure

```
stock-ai-bot/
├── main.py                    # Entry point & orchestrator
├── requirements.txt
├── .env                       # Your API keys (never commit this)
├── config/
│   ├── .env.example           # Template for .env
│   └── settings.py            # Config loader
├── core/
│   ├── logger.py              # Color terminal + rotating file logger
│   ├── ai_engine.py           # Claude synthesis engine
│   └── reporter.py            # Terminal + file report formatter
├── analyzers/
│   ├── technical.py           # OHLCV, indicators, options, shorts
│   ├── sentiment.py           # Reddit, StockTwits, Twitter, News
│   └── fundamentals.py        # SEC EDGAR, Yahoo Finance, earnings
├── logs/                      # Auto-created: JSON + TXT reports per scan
└── .github/
    └── workflows/
        └── scan.yml           # GitHub Actions scheduled runner
```

---

## ⚙️ Setup

### 1. Clone the repo
```bash
git clone https://github.com/YOURUSERNAME/stock-ai-bot.git
cd stock-ai-bot
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure API keys
```bash
cp config/.env.example .env
```

Edit `.env` and add your keys:

| Key | Where to get it |
|-----|----------------|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `REDDIT_CLIENT_ID` / `SECRET` | [reddit.com/prefs/apps](https://reddit.com/prefs/apps) → Create App |
| `REDDIT_USER_AGENT` | Any string, e.g. `StockBot/1.0 by u/YourUsername` |
| `STOCKTWITS_ACCESS_TOKEN` | Optional — public endpoints work without it |
| `TWITTER_BEARER_TOKEN` | [developer.twitter.com](https://developer.twitter.com) (free tier works) |

> **Note:** `yfinance` is free and requires no API key. Most features work even without Twitter/Reddit keys.

---

## 🚀 Running the Bot

### Scan your full watchlist (from `.env`)
```bash
python main.py
```

### Scan a specific ticker
```bash
python main.py --ticker NVDA
```

### Use a custom watchlist
```bash
python main.py --watchlist AAPL,TSLA,NVDA,SPY
```

### Run continuously (loop mode)
```bash
python main.py --loop
python main.py --ticker TSLA --loop
```

---

## 🤖 GitHub Actions (Automated Cloud Runs)

The bot includes a GitHub Actions workflow that runs automatically **every 30 minutes during US market hours (Mon–Fri)**.

### Setup GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions** and add:

- `ANTHROPIC_API_KEY`
- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT`
- `STOCKTWITS_ACCESS_TOKEN`
- `TWITTER_BEARER_TOKEN`
- `WATCHLIST` (e.g., `AAPL,TSLA,NVDA,SPY,QQQ`)

### Manual trigger
Go to **Actions → Stock AI Bot → Run workflow** and optionally specify a ticker.

### Reports
Each run uploads analysis reports as **GitHub Artifacts** (retained for 30 days). Download them from the Actions run page.

---

## 📊 Sample Output

```
══════════════════════════════════════════════════════════════
  📈  STOCK AI BOT — NVDA ANALYSIS  [2025-06-01 14:32:11]
══════════════════════════════════════════════════════════════

  PRICE SNAPSHOT
  Last Price:  $1,127.50
  1D / 5D / 20D:  +2.40% / +8.10% / +14.30%
  Company:  NVIDIA Corporation | Technology

  ⚡⚡⚡⚡⚡  EARNINGS IN 9 DAYS  ⚡⚡⚡⚡⚡

  TECHNICAL SIGNALS
  RSI:       67.4  (NEAR_OVERBOUGHT)
  MACD:      BULLISH_CROSS
  EMAs:      STRONG_BULL
  Volume:    2.1x avg  (HIGH)
  Options:   BULLISH OPTIONS FLOW | P/C Ratio: 0.62 | 7 contracts flagged
  Shorts:    Short % of Float: 1.4% | Days to Cover: 1.2

  SENTIMENT SIGNALS
  Overall:   BULLISH  (compound: 0.412)
  Mentions:  847 across Reddit / StockTwits / Twitter / News
  StockTwits: 🐂 24 bullish / 🐻 6 bearish (native tags)

  🤖  AI SYNTHESIS (Claude)
  ──────────────────────────────────────────────────────────

  1. MARKET SCENARIO
  NVDA shows strong momentum with earnings as the primary catalyst...
  [Full analysis continues...]

  CONFLUENCE:  [████████████████████░░░░░░░░░░] 78/100
  BIAS:        BULLISH
══════════════════════════════════════════════════════════════
```

---

## 📁 Log Files

Every scan saves two files in `/logs/`:

- `TICKER_YYYYMMDD_HHMMSS.json` — Full machine-readable data dump
- `TICKER_YYYYMMDD_HHMMSS.txt` — Human-readable report including the full AI analysis

---

## 🔧 Configuration Options (`.env`)

| Setting | Default | Description |
|---------|---------|-------------|
| `WATCHLIST` | `AAPL,TSLA,NVDA,SPY` | Tickers to scan |
| `SCAN_INTERVAL_MINUTES` | `15` | Loop interval |
| `CONFLUENCE_THRESHOLD` | `65` | Min score for high-conviction flag |
| `SENTIMENT_THRESHOLD` | `0.6` | Min compound score to flag |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |

---

## 🔮 Roadmap (Phase 2)

- [ ] Telegram bot integration
- [ ] Discord webhook alerts
- [ ] Web dashboard (GitHub Pages)
- [ ] Backtesting engine
- [ ] Multi-timeframe analysis (intraday + swing)
- [ ] Sector rotation tracking
- [ ] Unusual SEC filing detection (NLP on 8-K filings)

---

## ⚠️ Disclaimer

This tool is for **educational and research purposes only**. It does not constitute financial advice. Always do your own research. Past signals do not guarantee future results.
