import feedparser
import logging
from typing import List, Dict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# RSS feeds for real-time event monitoring
RSS_FEEDS = {
    "regulation": [
        {"name": "SEC Press Releases", "url": "https://www.sec.gov/rss/news/pressreleases.xml"},
        {"name": "FDA Press", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds"},
    ],
    "geopolitics": [
        {"name": "Reuters World", "url": "https://feeds.reuters.com/Reuters/worldNews"},
        {"name": "AP Top News", "url": "https://rsshub.app/apnews/topics/apf-topnews"},
    ],
    "weather": [
        {"name": "NOAA Alerts", "url": "https://alerts.weather.gov/cap/us.php?x=0"},
    ],
    "macro": [
        {"name": "BLS News", "url": "https://www.bls.gov/feed/bls_latest.rss"},
    ],
    "politics": [
        {"name": "Politico", "url": "https://rss.politico.com/politics-news.xml"},
    ],
}


class RSSParser:
    """Parse RSS feeds for market-relevant events."""

    def __init__(self):
        self.last_check: Dict[str, datetime] = {}

    async def fetch_all_feeds(self) -> List[Dict]:
        """Fetch and parse all configured RSS feeds."""
        events = []
        for category, feeds in RSS_FEEDS.items():
            for feed_info in feeds:
                try:
                    new_items = self._parse_feed(feed_info["url"], feed_info["name"], category)
                    events.extend(new_items)
                except Exception as e:
                    logger.error(f"RSS parse error for {feed_info['name']}: {e}")
        return events

    def _parse_feed(self, url: str, source_name: str, category: str) -> List[Dict]:
        """Parse a single RSS feed."""
        feed = feedparser.parse(url)
        items = []
        cutoff = datetime.utcnow() - timedelta(hours=6)  # Only last 6 hours

        for entry in feed.entries[:20]:  # Max 20 per feed
            published = self._parse_date(entry)
            if published and published < cutoff:
                continue

            items.append({
                "category": category,
                "event_title": entry.get("title", ""),
                "source": source_name,
                "link": entry.get("link", ""),
                "summary": entry.get("summary", "")[:500],
                "published": published.isoformat() if published else None,
                "raw_data": {
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:1000],
                },
            })

        return items

    def _parse_date(self, entry) -> datetime | None:
        """Parse date from RSS entry."""
        for field in ["published_parsed", "updated_parsed"]:
            parsed = getattr(entry, field, None)
            if parsed:
                try:
                    return datetime(*parsed[:6])
                except Exception:
                    pass
        return None

    def match_events_to_markets(self, events: List[Dict], markets: List[Dict]) -> List[Dict]:
        """Match external events to relevant Polymarket markets."""
        matched = []
        for event in events:
            event_text = (event["event_title"] + " " + event.get("summary", "")).lower()
            related_markets = []
            for market in markets:
                market_text = market["title"].lower()
                # Simple keyword overlap matching
                event_words = set(event_text.split())
                market_words = set(market_text.split())
                overlap = event_words & market_words
                # Filter common words
                overlap -= {"the", "a", "an", "in", "on", "at", "to", "for", "of", "is", "will", "be"}
                if len(overlap) >= 2:
                    related_markets.append(market["polymarket_id"])

            if related_markets:
                event["related_market_ids"] = related_markets
                matched.append(event)

        return matched
