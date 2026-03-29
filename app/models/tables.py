from sqlalchemy import (
    Column, Integer, String, Float, DateTime, JSON, BigInteger, Date,
    ForeignKey, ARRAY
)
from sqlalchemy.sql import func

from app.models.database import Base


class Market(Base):
    __tablename__ = "markets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    polymarket_id = Column(String(200), unique=True, nullable=False)
    condition_id = Column(String(200))
    category = Column(String(20), nullable=False)  # macro, weather, politics, earnings, regulation, geopolitics
    title = Column(String, nullable=False)
    description = Column(String)
    yes_price = Column(Float, default=0.0)
    no_price = Column(Float, default=0.0)
    liquidity = Column(Float, default=0.0)
    volume = Column(Float, default=0.0)
    end_timestamp = Column(BigInteger)
    rules = Column(JSON)
    active = Column(Integer, default=1)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    created_at = Column(DateTime, default=func.now())


class RoanSignal(Base):
    __tablename__ = "roan_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(Integer, ForeignKey("markets.id"))
    signal_type = Column(String(20), nullable=False)  # price_sum, info_lead, logic_arb
    profit_pct = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    suggested_position = Column(Float)
    yes_price_at_signal = Column(Float)
    no_price_at_signal = Column(Float)
    telegram_msg_id = Column(BigInteger)
    status = Column(String(20), default="pending")  # pending, sent, executed, ignored, expired
    executed_price = Column(Float)
    actual_profit = Column(Float)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class ExternalEvent(Base):
    __tablename__ = "external_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(20), nullable=False)
    event_title = Column(String, nullable=False)
    source = Column(String(100))
    confidence = Column(Float)
    raw_data = Column(JSON)
    market_related_ids = Column(ARRAY(Integer))
    created_at = Column(DateTime, default=func.now())


class RoanPerformance(Base):
    __tablename__ = "roan_performance"

    date = Column(Date, primary_key=True)
    signals_sent = Column(Integer, default=0)
    signals_executed = Column(Integer, default=0)
    signals_profitable = Column(Integer, default=0)
    total_profit_usd = Column(Float, default=0.0)
    capital_used = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)
