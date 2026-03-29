import logging
from dataclasses import dataclass
from typing import List, Optional

from app.config import settings
from app.core.roan_detector import RoanSignal

logger = logging.getLogger(__name__)


@dataclass
class PositionRecord:
    signal_id: str
    market_id: str
    amount: float
    entry_price: float
    status: str = "open"  # open, closed, expired
    pnl: float = 0.0


class RoanCapitalManager:
    """
    Capital management following Roan's principles:
    - Never risk more than X% of capital on a single trade
    - Track total exposure
    - Compound profits
    """

    def __init__(self):
        self.total_capital: float = 0.0
        self.available_capital: float = 0.0
        self.positions: List[PositionRecord] = []
        self.daily_exposure: float = 0.0
        self.max_position = settings.max_position_size
        self.max_daily = settings.max_daily_exposure

    def set_capital(self, amount: float):
        """Initialize or update total capital."""
        self.total_capital = amount
        self.available_capital = amount - sum(
            p.amount for p in self.positions if p.status == "open"
        )
        logger.info("Capital set: $%.2f (available: $%.2f)", self.total_capital, self.available_capital)

    def approve_position(self, signal: RoanSignal) -> Optional[float]:
        """
        Evaluate if a signal's position should be approved.
        Returns approved position size or None.
        """
        suggested = signal.position_size

        # Check daily exposure limit
        if self.daily_exposure + suggested > self.max_daily:
            remaining = self.max_daily - self.daily_exposure
            if remaining < 100:
                logger.info("Daily exposure limit reached")
                return None
            suggested = remaining

        # Check available capital
        if suggested > self.available_capital:
            suggested = self.available_capital
            if suggested < 100:
                logger.info("Insufficient capital")
                return None

        # Cap at max position size
        suggested = min(suggested, self.max_position)

        # Risk-adjusted sizing: reduce for lower confidence
        if signal.confidence < 0.95:
            suggested *= signal.confidence

        return round(suggested, 2)

    def record_entry(self, signal: RoanSignal, amount: float):
        """Record a position entry."""
        pos = PositionRecord(
            signal_id=signal.market_id,
            market_id=signal.market_id,
            amount=amount,
            entry_price=signal.yes_price + signal.no_price,
        )
        self.positions.append(pos)
        self.available_capital -= amount
        self.daily_exposure += amount
        logger.info("Position opened: $%.2f on %s", amount, signal.market_id)

    def record_exit(self, market_id: str, pnl: float):
        """Record a position exit."""
        for pos in self.positions:
            if pos.market_id == market_id and pos.status == "open":
                pos.status = "closed"
                pos.pnl = pnl
                self.available_capital += pos.amount + pnl
                self.total_capital += pnl
                logger.info("Position closed: %s, PnL: $%.2f", market_id, pnl)
                return
        logger.warning("No open position found for %s", market_id)

    @property
    def summary(self) -> dict:
        open_positions = [p for p in self.positions if p.status == "open"]
        closed_positions = [p for p in self.positions if p.status == "closed"]
        total_pnl = sum(p.pnl for p in closed_positions)
        return {
            "total_capital": self.total_capital,
            "available_capital": self.available_capital,
            "open_positions": len(open_positions),
            "closed_positions": len(closed_positions),
            "total_pnl": total_pnl,
            "daily_exposure": self.daily_exposure,
            "win_rate": (
                sum(1 for p in closed_positions if p.pnl > 0) / len(closed_positions)
                if closed_positions else 0.0
            ),
        }
