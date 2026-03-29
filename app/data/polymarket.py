import httpx
import logging
from typing import List, Dict, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Category keywords for classification
CATEGORY_KEYWORDS = {
    "macro": ["gdp", "cpi", "inflation", "fed", "interest rate", "unemployment", "jobs",
              "pce", "fomc", "treasury", "recession", "economy"],
    "weather": ["hurricane", "tornado", "temperature", "weather", "climate", "storm",
                "flood", "wildfire", "earthquake", "noaa"],
    "politics": ["election", "president", "congress", "senate", "vote", "poll",
                 "democrat", "republican", "governor", "mayor", "primary"],
    "earnings": ["earnings", "revenue", "eps", "quarterly", "annual report",
                 "profit", "guidance", "ipo", "stock", "market cap"],
    "regulation": ["sec", "fda", "regulation", "approve", "ban", "law", "bill",
                   "act", "ruling", "court", "supreme court", "legal"],
    "geopolitics": ["war", "conflict", "sanction", "nato", "china", "russia",
                    "ukraine", "taiwan", "nuclear", "treaty", "diplomacy"],
}


def classify_market(title: str, description: str = "") -> str:
    """Classify a market into one of 6 categories."""
    text = (title + " " + (description or "")).lower()
    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for kw in keywords if kw in text)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "politics"  # default


class PolymarketClient:
    """Client for Polymarket CLOB and Gamma APIs."""

    def __init__(self):
        self.clob_base = settings.polymarket_api_base
        self.gamma_base = settings.polymarket_gamma_base
        self.client = httpx.AsyncClient(timeout=30.0)

    async def get_active_markets(self, limit: int = 100) -> List[Dict]:
        """Fetch active markets from Gamma API."""
        try:
            resp = await self.client.get(
                f"{self.gamma_base}/markets",
                params={
                    "limit": limit,
                    "active": True,
                    "closed": False,
                    "order": "liquidity",
                    "ascending": False,
                },
            )
            resp.raise_for_status()
            markets = resp.json()
            return [self._parse_market(m) for m in markets if self._is_valid(m)]
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    async def get_market_by_id(self, condition_id: str) -> Optional[Dict]:
        """Fetch a single market."""
        try:
            resp = await self.client.get(f"{self.gamma_base}/markets/{condition_id}")
            resp.raise_for_status()
            return self._parse_market(resp.json())
        except Exception as e:
            logger.error(f"Failed to fetch market {condition_id}: {e}")
            return None

    async def get_orderbook(self, token_id: str) -> Optional[Dict]:
        """Get order book for a token (for slippage estimation)."""
        try:
            resp = await self.client.get(
                f"{self.clob_base}/book",
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch orderbook: {e}")
            return None

    async def get_events(self, limit: int = 50) -> List[Dict]:
        """Fetch events (groups of related markets) for logic arb detection."""
        try:
            resp = await self.client.get(
                f"{self.gamma_base}/events",
                params={"limit": limit, "active": True, "closed": False},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch events: {e}")
            return []

    def _parse_market(self, raw: Dict) -> Dict:
        """Normalize market data."""
        outcomes = raw.get("outcomes", ["Yes", "No"])
        prices = raw.get("outcomePrices", [])

        yes_price = 0.0
        no_price = 0.0
        if prices and len(prices) >= 2:
            try:
                yes_price = float(prices[0])
                no_price = float(prices[1])
            except (ValueError, TypeError):
                pass

        title = raw.get("question", raw.get("title", ""))
        description = raw.get("description", "")

        return {
            "polymarket_id": raw.get("id", ""),
            "condition_id": raw.get("conditionId", raw.get("condition_id", "")),
            "title": title,
            "description": description,
            "category": classify_market(title, description),
            "yes_price": yes_price,
            "no_price": no_price,
            "liquidity": float(raw.get("liquidity", 0)),
            "volume": float(raw.get("volume", 0)),
            "end_timestamp": raw.get("endDate", raw.get("end_date_iso")),
            "outcomes": outcomes,
            "rules": raw.get("rules", {}),
            "tokens": raw.get("clobTokenIds", []),
            "slug": raw.get("slug", ""),
            "active": raw.get("active", True),
        }

    def _is_valid(self, raw: Dict) -> bool:
        """Filter out invalid markets."""
        prices = raw.get("outcomePrices", [])
        if not prices or len(prices) < 2:
            return False
        try:
            yes_p = float(prices[0])
            no_p = float(prices[1])
            if yes_p <= 0 or no_p <= 0:
                return False
        except (ValueError, TypeError):
            return False
        return True

    async def close(self):
        await self.client.aclose()
