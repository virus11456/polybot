"""
FRED (Federal Reserve Economic Data) API client.
Fetches macroeconomic data releases and correlates them with Polymarket markets.
Gracefully degrades when FRED_API_KEY is not set.
"""

import asyncio
import logging
import os
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Key macroeconomic data series
IMPORTANT_SERIES = {
    "CPIAUCSL": "CPI",
    "GDP": "GDP",
    "UNRATE": "Unemployment Rate",
    "FEDFUNDS": "Federal Funds Rate",
    "T10YIE": "10-Year Breakeven Inflation",
    "PAYEMS": "Nonfarm Payrolls",
    "PPIACO": "Producer Price Index",
    "DCOILWTICO": "Crude Oil Price (WTI)",
}

# Keywords to match series to markets
SERIES_KEYWORDS: Dict[str, List[str]] = {
    "CPIAUCSL": ["CPI", "inflation", "consumer price"],
    "GDP": ["GDP", "economic growth", "recession"],
    "UNRATE": ["unemployment", "jobs", "labor market", "payroll"],
    "FEDFUNDS": ["Fed", "interest rate", "FOMC", "rate hike", "rate cut", "monetary"],
    "T10YIE": ["inflation", "treasury", "yield", "breakeven"],
    "PAYEMS": ["payroll", "jobs", "employment", "labor"],
    "PPIACO": ["PPI", "producer price", "inflation"],
    "DCOILWTICO": ["oil", "energy", "crude", "WTI"],
}


class FredDataClient:
    """
    Client for FRED (Federal Reserve Economic Data) API.
    Requires FRED_API_KEY environment variable.
    Gracefully degrades (returns empty data) when key is not set.
    """

    FRED_API_URL = "https://api.stlouisfed.org/fred"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("FRED_API_KEY")
        self._enabled = bool(self.api_key)
        if not self._enabled:
            logger.warning(
                "FRED_API_KEY not set — FredDataClient will return empty data. "
                "Set FRED_API_KEY to enable macroeconomic data fetching."
            )

    async def get_upcoming_releases(self) -> List[dict]:
        """
        Fetch upcoming important economic data releases (CPI, GDP, employment, etc.).
        Returns a list of release dicts with keys:
          - series_id, series_name, release_date, value (if available), units
        Returns empty list if FRED_API_KEY is not set or on error.
        """
        if not self._enabled:
            return []

        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp is required for FredDataClient")
            return []

        results = []
        today = datetime.utcnow().date()
        observation_start = (today - timedelta(days=7)).isoformat()
        observation_end = (today + timedelta(days=30)).isoformat()

        async with aiohttp.ClientSession() as session:
            tasks = [
                self._fetch_series_observations(
                    session, series_id, observation_start, observation_end
                )
                for series_id in IMPORTANT_SERIES
            ]
            series_results = await asyncio.gather(*tasks, return_exceptions=True)

        for series_id, result in zip(IMPORTANT_SERIES.keys(), series_results):
            if isinstance(result, Exception):
                logger.warning(f"Failed to fetch FRED series {series_id}: {result}")
                continue
            if result:
                results.extend(result)

        logger.info(f"Fetched {len(results)} FRED data observations")
        return results

    async def _fetch_series_observations(
        self,
        session: Any,
        series_id: str,
        obs_start: str,
        obs_end: str,
    ) -> List[dict]:
        """Fetch recent observations for a single FRED series."""
        url = f"{self.FRED_API_URL}/series/observations"
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": obs_start,
            "observation_end": obs_end,
            "sort_order": "desc",
            "limit": 5,
        }

        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 429:
                    logger.warning(f"Rate limited by FRED API for series {series_id}")
                    return []
                if resp.status == 401:
                    logger.error("Invalid FRED API key — check FRED_API_KEY env var")
                    return []
                resp.raise_for_status()
                data = await resp.json()
        except Exception as e:
            logger.error(f"Error fetching FRED series {series_id}: {e}")
            return []

        observations = data.get("observations", [])
        series_name = IMPORTANT_SERIES.get(series_id, series_id)
        results = []
        for obs in observations:
            value = obs.get("value")
            if value == ".":
                continue  # FRED uses "." for missing values
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            results.append({
                "series_id": series_id,
                "series_name": series_name,
                "release_date": obs.get("date"),
                "value": value,
                "units": data.get("units", ""),
                "source": "FRED",
                "category": "macro",
            })

        return results

    async def get_latest_series(self, series_id: str) -> dict:
        """
        Fetch the most recent value for a specific FRED data series.
        Returns empty dict if not enabled or on error.
        """
        if not self._enabled:
            return {}

        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp is required for FredDataClient")
            return {}

        today = datetime.utcnow().date()
        obs_start = (today - timedelta(days=90)).isoformat()

        async with aiohttp.ClientSession() as session:
            observations = await self._fetch_series_observations(
                session, series_id, obs_start, today.isoformat()
            )

        if not observations:
            return {}

        latest = observations[0]
        logger.info(f"Latest {series_id}: {latest.get('value')} on {latest.get('release_date')}")
        return latest

    def match_to_markets(self, release: dict, markets: List[dict]) -> List[dict]:
        """
        Match an economic data release to relevant Polymarket markets.
        Returns a filtered list of market dicts that are likely related.
        """
        series_id = release.get("series_id", "")
        keywords = SERIES_KEYWORDS.get(series_id, [])

        if not keywords:
            return []

        matched = []
        for market in markets:
            title = (market.get("title") or "").upper()
            if any(kw.upper() in title for kw in keywords):
                matched.append(market)

        return matched
