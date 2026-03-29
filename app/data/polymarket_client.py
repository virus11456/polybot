"""
Polymarket API Wrapper
Fetches market data from Polymarket Gamma API and stores in PostgreSQL.
"""

import asyncio
import logging
from typing import List, Optional, Dict, Any

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

logger = logging.getLogger(__name__)

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"
DEFAULT_PARAMS = {
    "active": "true",
    "closed": "false",
    "limit": 100,
}

# Keyword classification map
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "macro": [
        "CPI", "GDP", "inflation", "Fed", "interest rate", "unemployment",
        "Federal Reserve", "monetary policy", "recession", "economic",
        "treasury", "yield", "FOMC", "rate hike", "rate cut",
    ],
    "weather": [
        "hurricane", "storm", "temperature", "rainfall", "NOAA",
        "tornado", "flood", "drought", "typhoon", "cyclone",
        "precipitation", "snowfall", "wildfire", "earthquake",
    ],
    "politics": [
        "election", "president", "senate", "congress", "vote", "poll",
        "governor", "parliament", "referendum", "ballot", "democrat",
        "republican", "party", "candidate", "campaign", "primary",
    ],
    "earnings": [
        "earnings", "revenue", "profit", "Q1", "Q2", "Q3", "Q4", "EPS",
        "quarterly", "annual report", "guidance", "forecast", "beat",
        "miss", "margin", "EBITDA", "dividend",
    ],
    "regulatory": [
        "SEC", "FDA", "FTC", "regulatory", "approval", "ban",
        "regulation", "compliance", "lawsuit", "antitrust", "fine",
        "investigation", "penalty", "enforcement", "ruling",
    ],
    "geopolitical": [
        "war", "conflict", "sanctions", "military", "NATO", "invasion",
        "ceasefire", "treaty", "alliance", "nuclear", "missile",
        "troops", "diplomacy", "embargo", "coup",
    ],
}


class PolymarketClient:
    """
    Async client for Polymarket Gamma API.
    Fetches active markets, classifies them, and persists to PostgreSQL.
    """

    def __init__(self, db_url: Optional[str] = None, session_timeout: int = 30):
        self.session_timeout = aiohttp.ClientTimeout(total=session_timeout)
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._db_engine = None
        self._async_session = None

        if db_url:
            self._db_engine = create_async_engine(db_url, echo=False)
            self._async_session = sessionmaker(
                self._db_engine, class_=AsyncSession, expire_on_commit=False
            )

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(timeout=self.session_timeout)
        return self._http_session

    async def close(self):
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        if self._db_engine:
            await self._db_engine.dispose()

    async def get_active_markets(self) -> List[dict]:
        """
        Fetch all active markets from Polymarket Gamma API, filtered to 6 categories.
        Returns only markets that match at least one category.
        """
        all_markets = []
        offset = 0
        limit = DEFAULT_PARAMS["limit"]

        session = await self._get_session()

        while True:
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset,
            }

            try:
                async with session.get(GAMMA_API_URL, params=params) as resp:
                    if resp.status == 429:
                        logger.warning("Rate limited by Polymarket API, waiting 5s...")
                        await asyncio.sleep(5)
                        continue
                    resp.raise_for_status()
                    data = await resp.json()
            except aiohttp.ClientError as e:
                logger.error(f"Network error fetching markets (offset={offset}): {e}")
                break

            if not data:
                break

            # Normalize market fields
            for market in data:
                normalized = self._normalize_market(market)
                category = self.classify_market(normalized)
                if category != "other":
                    normalized["category"] = category
                    all_markets.append(normalized)

            if len(data) < limit:
                break

            offset += limit

        logger.info(f"Fetched {len(all_markets)} classified active markets")
        return all_markets

    async def get_market_detail(self, market_id: str) -> dict:
        """Fetch details for a specific market by conditionId or market slug."""
        session = await self._get_session()

        try:
            url = f"{GAMMA_API_URL}/{market_id}"
            async with session.get(url) as resp:
                if resp.status == 429:
                    logger.warning("Rate limited, waiting 5s...")
                    await asyncio.sleep(5)
                    return await self.get_market_detail(market_id)
                resp.raise_for_status()
                data = await resp.json()

            return self._normalize_market(data)

        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching market {market_id}: {e}")
            return {}

    def classify_market(self, market: dict) -> str:
        """
        Classify market into one of 6 categories based on title keywords.
        Returns 'other' if no category matches.
        """
        title = (market.get("title") or market.get("question") or "").upper()

        for category, keywords in CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                if keyword.upper() in title:
                    return category

        return "other"

    def _normalize_market(self, raw: dict) -> dict:
        """Normalize raw API response to our internal market schema."""
        # outcomePrices is a JSON string like '["0.7", "0.3"]'
        outcome_prices = raw.get("outcomePrices", [])
        if isinstance(outcome_prices, str):
            import json
            try:
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, ValueError):
                outcome_prices = []

        yes_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else None
        no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else None

        return {
            "id": raw.get("conditionId") or raw.get("id"),
            "polymarket_id": raw.get("conditionId") or raw.get("id"),
            "title": raw.get("question") or raw.get("title"),
            "yes_price": yes_price,
            "no_price": no_price,
            "liquidity": float(raw.get("liquidity") or 0),
            "end_date": raw.get("endDate"),
            "end_timestamp": raw.get("endDateIso"),
            "category": raw.get("category", "other"),
            "rules": raw.get("description"),
            "condition_id": raw.get("conditionId"),
            "active": raw.get("active", True),
            "closed": raw.get("closed", False),
            "volume": float(raw.get("volume") or 0),
            "_raw": raw,
        }

    async def upsert_markets(self, markets: List[dict]) -> int:
        """
        Upsert markets into the `markets` table using polymarket_id.
        Returns count of upserted rows.
        """
        if not self._async_session:
            raise RuntimeError("Database not configured. Pass db_url to constructor.")

        if not markets:
            return 0

        upsert_sql = text("""
            INSERT INTO markets (
                polymarket_id, category, title,
                yes_price, no_price, liquidity, end_timestamp, rules, updated_at
            ) VALUES (
                :polymarket_id, :category, :title,
                :yes_price, :no_price, :liquidity, :end_timestamp, :rules, NOW()
            )
            ON CONFLICT (polymarket_id) DO UPDATE SET
                category = EXCLUDED.category,
                title = EXCLUDED.title,
                yes_price = EXCLUDED.yes_price,
                no_price = EXCLUDED.no_price,
                liquidity = EXCLUDED.liquidity,
                end_timestamp = EXCLUDED.end_timestamp,
                rules = EXCLUDED.rules,
                updated_at = NOW()
        """)

        count = 0
        async with self._async_session() as session:
            async with session.begin():
                for market in markets:
                    await session.execute(upsert_sql, {
                        "polymarket_id": market.get("polymarket_id"),
                        "category": market.get("category", "other"),
                        "title": market.get("title"),
                        "yes_price": market.get("yes_price"),
                        "no_price": market.get("no_price"),
                        "liquidity": market.get("liquidity", 0),
                        "end_timestamp": market.get("end_timestamp"),
                        "rules": market.get("rules"),
                    })
                    count += 1

        logger.info(f"Upserted {count} markets to database")
        return count

    async def fetch_and_store(self) -> List[dict]:
        """
        Convenience method: fetch active markets and store to DB if configured.
        Returns the list of classified markets.
        """
        markets = await self.get_active_markets()

        if self._async_session and markets:
            await self.upsert_markets(markets)

        return markets


# Quick test / manual run
if __name__ == "__main__":
    import os
    import json

    async def main():
        db_url = os.getenv("DATABASE_URL")
        # Convert postgresql:// to postgresql+asyncpg:// for async driver
        if db_url and db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

        client = PolymarketClient(db_url=db_url)
        try:
            markets = await client.get_active_markets()
            print(f"Found {len(markets)} classified markets")
            by_cat: Dict[str, int] = {}
            for m in markets:
                cat = m.get("category", "other")
                by_cat[cat] = by_cat.get(cat, 0) + 1
            print("By category:", json.dumps(by_cat, indent=2))
            if markets:
                print("Sample market:", json.dumps({
                    k: v for k, v in markets[0].items() if k != "_raw"
                }, indent=2, default=str))
        finally:
            await client.close()

    asyncio.run(main())
