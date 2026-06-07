"""
analyzers/sentiment.py — Multi-source sentiment scraper & analyzer
Sources: Reddit, StockTwits, Twitter/X, News RSS feeds
"""
from __future__ import annotations
import re
import time
import requests
import feedparser
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

import praw

from core.logger import get_logger
from config.settings import (
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT,
    STOCKTWITS_TOKEN, TWITTER_BEARER, REDDIT_SUBS, NEWS_RSS_FEEDS
)

log      = get_logger("SentimentAnalyzer")
vader    = SentimentIntensityAnalyzer()

# ── Custom financial slang for VADER ─────────────────────
FINANCE_LEXICON = {
    "moon": 3.5, "mooning": 3.5, "🚀": 3.0, "💎": 2.0, "🙌": 1.5,
    "yolo": 0.5, "squeeze": 2.0, "rip": -2.5, "bagholding": -3.0,
    "bagholder": -3.0, "dump": -2.5, "shorted": -1.5, "puts": -0.5,
    "calls": 0.5, "tendies": 2.0, "apes": 1.0, "fud": -2.0,
    "bullish": 3.0, "bearish": -3.0, "catalyst": 2.0, "halt": -1.0,
    "circuit breaker": -1.5, "gap up": 2.0, "gap down": -2.0,
    "breakout": 2.5, "breakdown": -2.5, "reversal": 1.0,
    "undervalued": 2.0, "overvalued": -2.0, "short squeeze": 3.0,
}
vader.lexicon.update(FINANCE_LEXICON)


def analyze_text(text: str) -> Dict[str, float]:
    """Run VADER sentiment on a piece of text. Returns compound score."""
    if not text:
        return {"compound": 0.0, "pos": 0.0, "neu": 1.0, "neg": 0.0}
    clean = re.sub(r"http\S+|www\S+", "", text)
    return vader.polarity_scores(clean)


def aggregate_sentiments(posts: List[Dict]) -> Dict[str, Any]:
    """Aggregate list of {text, score} posts into summary stats."""
    if not posts:
        return {
            "count": 0, "avg_compound": 0.0,
            "bullish_pct": 0.0, "bearish_pct": 0.0, "neutral_pct": 0.0,
            "sentiment_label": "insufficient_data",
        }

    compounds = [p["sentiment"]["compound"] for p in posts]
    avg = sum(compounds) / len(compounds)

    bullish = sum(1 for c in compounds if c > 0.05)
    bearish = sum(1 for c in compounds if c < -0.05)
    neutral = len(compounds) - bullish - bearish
    total   = len(compounds)

    return {
        "count":        total,
        "avg_compound": round(avg, 3),
        "bullish_pct":  round(bullish / total * 100, 1),
        "bearish_pct":  round(bearish / total * 100, 1),
        "neutral_pct":  round(neutral / total * 100, 1),
        "sentiment_label": (
            "strongly_bullish" if avg > 0.3  else
            "bullish"          if avg > 0.05 else
            "strongly_bearish" if avg < -0.3 else
            "bearish"          if avg < -0.05 else
            "neutral"
        ),
    }


# ══════════════════════════════════════════════════════════
#  REDDIT
# ══════════════════════════════════════════════════════════
def _make_reddit() -> Optional[praw.Reddit]:
    if not REDDIT_CLIENT_ID or REDDIT_CLIENT_ID == "your_reddit_client_id":
        log.warning("Reddit credentials not configured — skipping Reddit scrape")
        return None
    try:
        r = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
        )
        r.user.me()  # test auth
        return r
    except Exception as e:
        log.warning(f"Reddit auth failed: {e}")
        return None


def scrape_reddit(ticker: str, limit: int = 100) -> Dict[str, Any]:
    reddit = _make_reddit()
    if reddit is None:
        return {"source": "reddit", "posts": [], "aggregated": aggregate_sentiments([])}

    posts    = []
    mentions = 0
    patterns = [
        re.compile(rf"\b{re.escape(ticker)}\b", re.IGNORECASE),
        re.compile(rf"\${re.escape(ticker)}\b", re.IGNORECASE),
    ]

    for sub_name in REDDIT_SUBS:
        try:
            sub = reddit.subreddit(sub_name)
            for post in sub.hot(limit=limit // len(REDDIT_SUBS)):
                text = f"{post.title} {post.selftext or ''}".strip()
                if any(p.search(text) for p in patterns):
                    mentions += 1
                    sent = analyze_text(text)
                    posts.append({
                        "source":    f"r/{sub_name}",
                        "title":     post.title[:200],
                        "score":     post.score,
                        "comments":  post.num_comments,
                        "url":       f"https://reddit.com{post.permalink}",
                        "sentiment": sent,
                        "created":   datetime.fromtimestamp(post.created_utc).isoformat(),
                    })
        except Exception as e:
            log.warning(f"Reddit r/{sub_name} error: {e}")

    log.info(f"Reddit: {mentions} posts mentioning {ticker} across {len(REDDIT_SUBS)} subs")
    return {
        "source":     "reddit",
        "mentions":   mentions,
        "posts":      sorted(posts, key=lambda x: x["score"], reverse=True)[:20],
        "aggregated": aggregate_sentiments(posts),
    }


# ══════════════════════════════════════════════════════════
#  STOCKTWITS
# ══════════════════════════════════════════════════════════
def scrape_stocktwits(ticker: str) -> Dict[str, Any]:
    """Scrape StockTwits public API — no key required for basic access."""
    url  = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    params = {"limit": 30}
    if STOCKTWITS_TOKEN and STOCKTWITS_TOKEN != "optional_stocktwits_token":
        params["access_token"] = STOCKTWITS_TOKEN

    posts = []
    bull_count = 0
    bear_count = 0

    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            log.warning(f"StockTwits returned {resp.status_code} for {ticker}")
            return {"source": "stocktwits", "posts": [], "aggregated": aggregate_sentiments([])}

        data = resp.json()
        messages = data.get("messages", [])

        for msg in messages:
            body  = msg.get("body", "")
            ent   = msg.get("entities", {}).get("sentiment", {})
            basic = ent.get("basic", "").lower() if ent else ""

            # Use StockTwits native bullish/bearish signal when available
            if basic == "bullish":
                bull_count += 1
                forced_compound = 0.5
            elif basic == "bearish":
                bear_count += 1
                forced_compound = -0.5
            else:
                forced_compound = None

            sent = analyze_text(body)
            if forced_compound is not None:
                sent["compound"] = forced_compound

            posts.append({
                "source":    "stocktwits",
                "text":      body[:200],
                "user":      msg.get("user", {}).get("username"),
                "native_sentiment": basic or "none",
                "sentiment": sent,
                "created":   msg.get("created_at", ""),
                "likes":     msg.get("likes", {}).get("total", 0),
            })

        log.info(
            f"StockTwits {ticker}: {len(messages)} messages | "
            f"🐂 {bull_count} bull / 🐻 {bear_count} bear"
        )

    except Exception as e:
        log.warning(f"StockTwits error for {ticker}: {e}")

    return {
        "source":           "stocktwits",
        "bull_count":       bull_count,
        "bear_count":       bear_count,
        "native_bull_pct":  round(bull_count / max(len(posts), 1) * 100, 1),
        "posts":            posts,
        "aggregated":       aggregate_sentiments(posts),
    }


# ══════════════════════════════════════════════════════════
#  TWITTER / X
# ══════════════════════════════════════════════════════════
def scrape_twitter(ticker: str, max_results: int = 50) -> Dict[str, Any]:
    """Search recent tweets via Twitter v2 API (Bearer token only)."""
    if not TWITTER_BEARER or TWITTER_BEARER == "your_twitter_bearer_token":
        log.warning("Twitter Bearer token not configured — skipping Twitter scrape")
        return {"source": "twitter", "posts": [], "aggregated": aggregate_sentiments([])}

    url     = "https://api.twitter.com/2/tweets/search/recent"
    headers = {"Authorization": f"Bearer {TWITTER_BEARER}"}
    query   = f"(${ticker} OR #{ticker}) lang:en -is:retweet"
    params  = {
        "query":       query,
        "max_results": min(max_results, 100),
        "tweet.fields": "created_at,public_metrics,author_id",
    }

    posts = []
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            log.warning(f"Twitter API returned {resp.status_code}")
            return {"source": "twitter", "posts": [], "aggregated": aggregate_sentiments([])}

        tweets = resp.json().get("data", [])
        for tw in tweets:
            text = tw.get("text", "")
            sent = analyze_text(text)
            metrics = tw.get("public_metrics", {})
            posts.append({
                "source":    "twitter",
                "text":      text[:280],
                "likes":     metrics.get("like_count", 0),
                "retweets":  metrics.get("retweet_count", 0),
                "replies":   metrics.get("reply_count", 0),
                "sentiment": sent,
                "created":   tw.get("created_at", ""),
            })

        log.info(f"Twitter: {len(posts)} tweets for {ticker}")

    except Exception as e:
        log.warning(f"Twitter scrape error for {ticker}: {e}")

    return {
        "source":     "twitter",
        "posts":      posts,
        "aggregated": aggregate_sentiments(posts),
    }


# ══════════════════════════════════════════════════════════
#  NEWS RSS
# ══════════════════════════════════════════════════════════
def scrape_news(ticker: str, company_name: str = "") -> Dict[str, Any]:
    """Scrape financial news RSS feeds for ticker mentions."""
    posts    = []
    patterns = [re.compile(rf"\b{re.escape(ticker)}\b", re.IGNORECASE)]
    if company_name:
        patterns.append(re.compile(rf"\b{re.escape(company_name)}\b", re.IGNORECASE))

    for source, feed_url in NEWS_RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:50]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                text    = f"{title} {summary}"

                if any(p.search(text) for p in patterns):
                    sent = analyze_text(text)
                    posts.append({
                        "source":    source,
                        "title":     title[:200],
                        "url":       entry.get("link", ""),
                        "sentiment": sent,
                        "published": entry.get("published", ""),
                    })
        except Exception as e:
            log.warning(f"News RSS error ({source}): {e}")

    log.info(f"News: {len(posts)} articles mentioning {ticker}")
    return {
        "source":     "news",
        "articles":   sorted(posts, key=lambda x: x["sentiment"]["compound"], reverse=True)[:15],
        "aggregated": aggregate_sentiments(posts),
    }


# ══════════════════════════════════════════════════════════
#  COMBINED SENTIMENT ENGINE
# ══════════════════════════════════════════════════════════
def run_sentiment_analysis(ticker: str, company_name: str = "") -> Dict[str, Any]:
    """Run all sentiment sources and merge into a single weighted score."""
    log.info(f"Running sentiment analysis for {ticker}...")

    reddit   = scrape_reddit(ticker)
    twits    = scrape_stocktwits(ticker)
    twitter  = scrape_twitter(ticker)
    news     = scrape_news(ticker, company_name)

    # Weighted compound: news > reddit > stocktwits > twitter
    weights = {"news": 0.35, "reddit": 0.30, "stocktwits": 0.25, "twitter": 0.10}

    weighted_compound = 0.0
    weight_used       = 0.0

    source_scores = {}
    for source, data, w in [
        ("news",       news,    weights["news"]),
        ("reddit",     reddit,  weights["reddit"]),
        ("stocktwits", twits,   weights["stocktwits"]),
        ("twitter",    twitter, weights["twitter"]),
    ]:
        agg = data.get("aggregated", {})
        if agg.get("count", 0) > 0:
            compound = agg.get("avg_compound", 0.0)
            weighted_compound += compound * w
            weight_used       += w
            source_scores[source] = compound

    if weight_used > 0:
        weighted_compound /= weight_used

    overall_label = (
        "strongly_bullish" if weighted_compound > 0.3  else
        "bullish"          if weighted_compound > 0.05 else
        "strongly_bearish" if weighted_compound < -0.3 else
        "bearish"          if weighted_compound < -0.05 else
        "neutral"
    )

    total_mentions = (
        reddit.get("mentions", 0) +
        len(twits.get("posts", [])) +
        len(twitter.get("posts", [])) +
        len(news.get("articles", []))
    )

    log.info(
        f"Sentiment for {ticker}: {overall_label.upper()} "
        f"(compound={weighted_compound:.3f}, mentions={total_mentions})"
    )

    return {
        "ticker":             ticker,
        "timestamp":          datetime.now().isoformat(),
        "overall_compound":   round(weighted_compound, 3),
        "overall_label":      overall_label,
        "total_mentions":     total_mentions,
        "source_scores":      source_scores,
        "reddit":             reddit,
        "stocktwits":         twits,
        "twitter":            twitter,
        "news":               news,
    }
