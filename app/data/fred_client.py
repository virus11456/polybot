import httpx
import logging
from typing import Dict, Optional, List
from datetime import datetime, timedelta

from app.config import settings

logger = logging.getLogger(__name__)

# Key FRED series for macro monitoring
MACRO_SERIES = {
    "CPIAUCSL": "CPI (All Urban Consumers)",
    "UNRATE": "Unemployment Rate",
    "GDP": "Gross Domestic Product",
    "FEDFUNDS": "Federal Funds Rate",
    "T10Y2Y": "10Y-2Y Treasury Spread",
    "PCEPI": "PCE Price Index",
    "PAYEMS": "Total Nonfarm Payrolls",
    "UMCSENT": "Consumer Sentiment",
}


class FredClient:
    """FRED API client for macroeconomic data."""

    def __init__(self):
        self.api_key = settings.fred_api_key
        self.base_url = "https://api.stlouisfed.org/fred"
        self.client = httpx.AsyncClient(timeout=30.0)

    async def get_latest_observation(self, series_id: str) -> Optional[Dict]:
        """Get the most recent data point for a series."""
        if not self.api_key:
            logger.warning("FRED API key not configured")
            return None

        try:
            resp = await self.client.get(
                f"{self.base_url}/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            observations = data.get("observations", [])
            if observations:
                obs = observations[0]
                return {
                    "series_id": series_id,
                    "name": MACRO_SERIES.get(series_id, series_id),
                    "date": obs["date"],
                    "value": float(obs["value"]) if obs["value"] != "." else None,
                }
        except Exception as e:
            logger.error(f"FRED fetch error for {series_id}: {e}")
        return None

    async def get_upcoming_releases(self) -> List[Dict]:
        """Get upcoming data releases (next 7 days)."""
        if not self.api_key:
            return []

        try:
            now = datetime.utcnow()
            resp = await self.client.get(
                f"{self.base_url}/releases/dates",
                params={
                    "api_key": self.api_key,
                    "file_type": "json",
                    "realtime_start": now.strftime("%Y-%m-%d"),
                    "realtime_end": (now + timedelta(days=7)).strftime("%Y-%m-%d"),
                },
            )
            resp.raise_for_status()
            return resp.json().get("release_dates", [])
        except Exception as e:
            logger.error(f"FRED releases error: {e}")
            return []

    async def check_macro_signals(self) -> List[Dict]:
        """Check all macro series for significant movements."""
        signals = []
        for series_id in MACRO_SERIES:
            obs = await self.get_latest_observation(series_id)
            if obs and obs["value"] is not None:
                signals.append(obs)
        return signals

    async def close(self):
        await self.client.aclose()
