"""
RSS Feed Parser for external news and event data.
Fetches articles from multiple RSS sources, classifies events,
and matches them to Polymarket markets.
"""

import asyncio
import logging
import re
from typing import List, Optional, Dict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RssParser:
    """
    Async RSS feed parser for news and event data.
    Fetches and classifies articles from multiple sources.
    Gracefully degrades on individual feed failures.
    """

    RSS_FEEDS: Dict[str, str] = {
        "noaa_weather": "https://www.weather.gov/rss_news.php",
        "sec_filings": (
            "https://www.sec.gov/cgi-bin/browse-edgar"
            "?action=getcurrent&type=8-K&dateb=&owner=include&count=40&output=atom"
        ),
        "reuters": "https://feeds.reuters.com/reuters/topNews",
        "ap": "https://feeds.apnews.com/rss/apf-topnews",
    }

    # Keyword sets for event classification
    CATEGORY_KEYWORDS: Dict[str, List[str]] = {
        "weather": [
            "hurricane", "storm", "tornado", "flood", "drought", "typhoon",
            "cyclone", "precipitation", "snowfall", "wildfire", "earthquake",
            "temperature", "NOAA", "weather alert", "blizzard", "heatwave",
        ],
        "regulatory": [
            "SEC", "FDA", "FTC", "regulation", "approval", "ban", "lawsuit",
            "antitrust", "fine", "investigation", "penalty", "enforcement",
            "ruling", "compliance", "8-K", "filing", "sanction",
        ],
        "geopolitical": [
            "war", "conflict", "sanctions", "military", "NATO", "invasion",
            "ceasefire", "treaty", "alliance", "nuclear", "missile",
            "troops", "diplomacy", "embargo", "coup", "geopolitical",
        ],
        "politics": [
            "election", "president", "senate", "congress", "vote", "poll",
            "governor", "parliament", "referendum", "ballot", "democrat",
            "republican", "party", "candidate", "campaign", "primary",
            "legislation", "bill", "executive order",
        ],
        "macro": [
            "CPI", "GDP", "inflation", "Fed", "interest rate", "unemployment",
            "Federal Reserve", "monetary policy", "recession", "economic",
            "treasury", "yield", "FOMC", "rate hike", "rate cut",
            "payroll", "jobs report",
        ],
    }

    # Market title keywords to match events against markets
    MARKET_MATCH_KEYWORDS: Dict[str, List[str]] = {
        "weather": ["hurricane", "storm", "tornado", "flood", "temperature", "weather"],
        "regulatory": ["SEC", "FDA", "FTC", "regulatory", "approval", "lawsuit", "fine"],
        "geopolitical": ["war", "conflict", "sanctions", "military", "ceasefire", "invasion"],
        "politics": ["election", "president", "senate", "congress", "vote", "governor"],
        "macro": ["CPI", "GDP", "inflation", "Fed", "unemployment", "interest rate"],
    }

    def __init__(self, feeds: Optional[Dict[str, str]] = None):
        self.feeds = feeds if feeds is not None else self.RSS_FEEDS

    async def fetch_all_feeds(self) -> List[dict]:
        """
        Fetch and parse all configured RSS feeds concurrently.
        Returns a unified list of article dicts. Skips failed feeds gracefully.
        Each article dict has:
          - feed_name, title, summary, link, published, category, source
        """
        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp is required for RssParser")
            return []

        async with aiohttp.ClientSession() as session:
            tasks = [
                self._fetch_feed(session, name, url)
                for name, url in self.feeds.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        articles = []
        for name, result in zip(self.feeds.keys(), results):
            if isinstance(result, Exception):
                logger.warning(f"Failed to fetch RSS feed '{name}': {result}")
                continue
            articles.extend(result)

        logger.info(f"Fetched {len(articles)} articles from {len(self.feeds)} RSS feeds")
        return articles

    async def _fetch_feed(
        self, session: "aiohttp.ClientSession", feed_name: str, url: str
    ) -> List[dict]:
        """Fetch and parse a single RSS/Atom feed."""
        try:
            import feedparser
        except ImportError:
            logger.error("feedparser is required for RssParser (pip install feedparser)")
            return []

        try:
            import aiohttp
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Feed '{feed_name}' returned HTTP {resp.status}")
                    return []
                raw_content = await resp.text()
        except Exception as e:
            logger.warning(f"Network error fetching feed '{feed_name}': {e}")
            return []

        try:
            feed = feedparser.parse(raw_content)
        except Exception as e:
            logger.warning(f"Failed to parse feed '{feed_name}': {e}")
            return []

        articles = []
        for entry in feed.entries:
            title = getattr(entry, "title", "") or ""
            summary = getattr(entry, "summary", "") or ""
            link = getattr(entry, "link", "") or ""
            published = self._parse_date(entry)

            category = self.classify_event({"title": title, "summary": summary})

            articles.append({
                "feed_name": feed_name,
                "title": title,
                "summary": summary[:500],  # cap summary length
                "link": link,
                "published": published,
                "category": category,
                "source": feed_name,
            })

        return articles

    def _parse_date(self, entry: object) -> Optional[str]:
        """Extract and normalize the published date from a feed entry."""
        for attr in ("published_parsed", "updated_parsed", "created_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                try:
                    dt = datetime(*parsed[:6], tzinfo=timezone.utc)
                    return dt.isoformat()
                except Exception:
                    pass
        return None

    def classify_event(self, entry: dict) -> str:
        """
        Classify an event into one of: weather, regulatory, geopolitical, politics, macro.
        Falls back to 'general' if no category matches.
        """
        text = (
            (entry.get("title") or "") + " " + (entry.get("summary") or "")
        ).lower()

        for category, keywords in self.CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in text:
                    return category

        return "general"

    def match_to_markets(self, event: dict, markets: List[dict]) -> List[str]:
        """
        Match an event to relevant Polymarket markets.
        Returns a list of matching polymarket_id strings.
        """
        category = event.get("category", "general")
        event_text = (
            (event.get("title") or "") + " " + (event.get("summary") or "")
        ).lower()

        # Collect keywords both from the event category and from the event text itself
        category_kws = self.MARKET_MATCH_KEYWORDS.get(category, [])

        matched_ids = []
        for market in markets:
            market_title = (market.get("title") or "").lower()
            market_id = market.get("polymarket_id") or market.get("id")
            if not market_id:
                continue

            # Match if any category keyword appears in market title
            if any(kw.lower() in market_title for kw in category_kws):
                matched_ids.append(str(market_id))
                continue

            # Also match if significant words from event title appear in market title
            event_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", event_text))
            market_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", market_title))
            overlap = event_words & market_words
            if len(overlap) >= 2:
                matched_ids.append(str(market_id))

        return matched_ids
