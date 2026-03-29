import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RoanSignal:
    """A detected arbitrage signal."""
    market_id: str
    title: str
    category: str
    profit_pct: float
    confidence: float
    position_size: float
    signal_type: str  # price_sum, info_lead, logic_arb, multi_market
    yes_price: float = 0.0
    no_price: float = 0.0
    details: str = ""
    market_url: str = ""
    liquidity: float = 0.0
    slug: str = ""


class RoanDetector:
    """
    Roan-style arbitrage detector.

    Strategy 1: Price Sum < 1 (YES + NO < 1 minus fees = guaranteed profit)
    Strategy 2: Logic Arbitrage (related markets with inconsistent pricing)
    Strategy 3: Info Lead (external data suggests mispricing)
    """

    def __init__(self):
        self.fee_rate = settings.fee_rate
        self.min_profit = settings.min_profit_pct
        self.min_liquidity = settings.min_liquidity

    def detect(self, market: Dict) -> Optional[RoanSignal]:
        """Run all detection strategies on a single market."""
        if market.get("liquidity", 0) < self.min_liquidity:
            return None

        # Strategy 1: YES + NO < 1 (classic Roan)
        signal = self._detect_price_sum(market)
        if signal:
            return signal

        return None

    def detect_logic_arb(self, events: List[Dict]) -> List[RoanSignal]:
        """
        Strategy 2: Logic arbitrage across related markets in an event.
        e.g., if P(A) + P(B) + P(C) should sum to 1 but doesn't.
        """
        signals = []
        for event in events:
            markets = event.get("markets", [])
            if len(markets) < 2:
                continue

            signal = self._check_event_consistency(event, markets)
            if signal:
                signals.append(signal)

        return signals

    def detect_info_lead(self, market: Dict, external_events: List[Dict]) -> Optional[RoanSignal]:
        """
        Strategy 3: External information suggests market is mispriced.
        """
        for event in external_events:
            related_ids = event.get("related_market_ids", [])
            if market["polymarket_id"] in related_ids:
                confidence = event.get("confidence", 0.5)
                if confidence > 0.8:
                    # High confidence external info + market hasn't moved
                    current_sum = market["yes_price"] + market["no_price"]
                    if current_sum < 0.99:
                        profit = 1 - current_sum - self.fee_rate
                        if profit > self.min_profit:
                            return RoanSignal(
                                market_id=market["polymarket_id"],
                                title=market["title"],
                                category=market.get("category", "unknown"),
                                profit_pct=profit,
                                confidence=min(confidence, 0.90),
                                position_size=self._calc_position(profit, confidence),
                                signal_type="info_lead",
                                yes_price=market["yes_price"],
                                no_price=market["no_price"],
                                details=f"External event: {event['event_title']}",
                                market_url=self._market_url(market),
                                liquidity=market.get("liquidity", 0),
                                slug=market.get("slug", ""),
                            )
        return None

    def _detect_price_sum(self, market: Dict) -> Optional[RoanSignal]:
        """
        Roan classic: YES + NO < 1.

        If you buy both YES and NO, one must pay out $1.
        Profit = $1 - (YES_price + NO_price) - fees
        """
        yes_p = market["yes_price"]
        no_p = market["no_price"]
        total = yes_p + no_p

        if total >= 1.0:
            return None

        profit = 1.0 - total - self.fee_rate
        if profit <= self.min_profit:
            return None

        # Confidence is very high for pure price arbitrage
        confidence = 0.99 if profit > 0.02 else 0.95

        return RoanSignal(
            market_id=market["polymarket_id"],
            title=market["title"],
            category=market.get("category", "unknown"),
            profit_pct=profit,
            confidence=confidence,
            position_size=self._calc_position(profit, confidence),
            signal_type="price_sum",
            yes_price=yes_p,
            no_price=no_p,
            details=f"YES({yes_p:.4f}) + NO({no_p:.4f}) = {total:.4f} < 1.00",
            market_url=self._market_url(market),
            liquidity=market.get("liquidity", 0),
            slug=market.get("slug", ""),
        )

    def _check_event_consistency(self, event: Dict, markets: List[Dict]) -> Optional[RoanSignal]:
        """
        Check if markets within an event have consistent pricing.
        For mutually exclusive outcomes: sum of YES prices should = ~1.0
        """
        total_yes = 0.0
        valid_markets = []

        for m in markets:
            prices = m.get("outcomePrices", [])
            if prices and len(prices) >= 1:
                try:
                    yes_p = float(prices[0])
                    total_yes += yes_p
                    valid_markets.append(m)
                except (ValueError, TypeError):
                    continue

        if len(valid_markets) < 2:
            return None

        # If total YES prices significantly != 1.0, there's an arb
        deviation = abs(total_yes - 1.0)
        if deviation > (self.fee_rate + self.min_profit):
            profit = deviation - self.fee_rate
            if profit > self.min_profit:
                titles = [m.get("question", m.get("title", "?"))[:50] for m in valid_markets[:3]]
                return RoanSignal(
                    market_id=event.get("id", ""),
                    title=f"Logic Arb: {event.get('title', 'Event')}",
                    category="multi",
                    profit_pct=profit,
                    confidence=0.90,
                    position_size=self._calc_position(profit, 0.90),
                    signal_type="logic_arb",
                    details=f"Sum of YES prices: {total_yes:.4f} (expected ~1.0). Markets: {', '.join(titles)}",
                    market_url="",
                    liquidity=min(float(m.get("liquidity", 0)) for m in valid_markets),
                )

        return None

    def _calc_position(self, profit_pct: float, confidence: float) -> float:
        """Calculate suggested position size based on Kelly-like criterion."""
        base = settings.default_position_size
        # Scale by profit and confidence
        kelly_fraction = (confidence * profit_pct - (1 - confidence)) / profit_pct
        kelly_fraction = max(0.1, min(kelly_fraction, 1.0))  # Clamp
        position = base * kelly_fraction * (profit_pct / 0.01)  # Scale with profit
        return min(position, settings.max_position_size)

    def _market_url(self, market: Dict) -> str:
        slug = market.get("slug", "")
        if slug:
            return f"https://polymarket.com/event/{slug}"
        return f"https://polymarket.com"
