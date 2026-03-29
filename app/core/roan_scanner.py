import asyncio
import logging
from typing import List
from datetime import datetime

from app.data.polymarket import PolymarketClient
from app.data.rss_parser import RSSParser
from app.data.fred_client import FredClient
from app.core.roan_detector import RoanDetector, RoanSignal
from app.config import settings

logger = logging.getLogger(__name__)


class RoanScanner:
    """
    Roan Arbitrage Scanner.

    Continuously monitors Polymarket for arbitrage opportunities using:
    1. Price sum arbitrage (YES + NO < 1)
    2. Logic arbitrage (inconsistent pricing across related markets)
    3. Information lead (external data suggests mispricing)
    """

    def __init__(self):
        self.polymarket = PolymarketClient()
        self.detector = RoanDetector()
        self.rss = RSSParser()
        self.fred = FredClient()
        self._running = False
        self._scan_count = 0
        self._signals_today: List[RoanSignal] = []
        self._today = datetime.utcnow().date()

    async def start_monitoring(self):
        """Start the continuous scanning loop."""
        self._running = True
        logger.info("Roan Scanner started — scanning every %ds", settings.scan_interval_seconds)

        while self._running:
            try:
                # Reset daily counters
                today = datetime.utcnow().date()
                if today != self._today:
                    self._signals_today = []
                    self._today = today

                signals = await self.scan_once()
                self._scan_count += 1

                if signals:
                    self._signals_today.extend(signals)
                    logger.info(
                        "Scan #%d: Found %d signals (total today: %d)",
                        self._scan_count, len(signals), len(self._signals_today),
                    )

            except Exception as e:
                logger.error("Scan error: %s", e, exc_info=True)

            await asyncio.sleep(settings.scan_interval_seconds)

    async def scan_once(self) -> List[RoanSignal]:
        """Execute a single scan cycle."""
        signals: List[RoanSignal] = []

        # 1. Fetch active markets
        markets = await self.polymarket.get_active_markets(limit=200)
        if not markets:
            logger.warning("No markets fetched")
            return signals

        # 2. Price sum arbitrage scan
        for market in markets:
            signal = self.detector.detect(market)
            if signal:
                signals.append(signal)

        # 3. Logic arbitrage scan (events with multiple markets)
        events = await self.polymarket.get_events(limit=50)
        logic_signals = self.detector.detect_logic_arb(events)
        signals.extend(logic_signals)

        # 4. Info lead scan (every 5th cycle to avoid rate limits)
        if self._scan_count % 5 == 0:
            try:
                rss_events = await self.rss.fetch_all_feeds()
                matched_events = self.rss.match_events_to_markets(rss_events, markets)
                for market in markets:
                    info_signal = self.detector.detect_info_lead(market, matched_events)
                    if info_signal:
                        signals.append(info_signal)
            except Exception as e:
                logger.error("RSS/info lead scan error: %s", e)

        # Deduplicate and sort by profit
        seen_ids = set()
        unique_signals = []
        for s in signals:
            if s.market_id not in seen_ids:
                seen_ids.add(s.market_id)
                unique_signals.append(s)

        return sorted(unique_signals, key=lambda x: x.profit_pct, reverse=True)

    def stop(self):
        """Stop the scanner."""
        self._running = False
        logger.info("Roan Scanner stopped")

    @property
    def status(self) -> dict:
        return {
            "running": self._running,
            "scan_count": self._scan_count,
            "signals_today": len(self._signals_today),
            "today": str(self._today),
        }
