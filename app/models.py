"""
SQLAlchemy ORM models matching schema.sql (4 tables).
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Numeric, BigInteger,
    TIMESTAMP, Date, ARRAY, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class Market(Base):
    __tablename__ = "markets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    polymarket_id = Column(String(100), unique=True, nullable=False)
    category = Column(String(20), nullable=False)
    title = Column(Text)
    yes_price = Column(Numeric(5, 4))
    no_price = Column(Numeric(5, 4))
    liquidity = Column(Numeric(12, 2))
    end_timestamp = Column(String(50))
    rules = Column(JSONB)
    updated_at = Column(TIMESTAMP, server_default=func.now())


class RoanSignal(Base):
    __tablename__ = "roan_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(Integer)
    signal_type = Column(String(20))
    profit_pct = Column(Numeric(6, 4))
    confidence = Column(Numeric(4, 3))
    suggested_position = Column(Numeric(10, 2))
    telegram_msg_id = Column(BigInteger)
    status = Column(String(20), default="pending")
    created_at = Column(TIMESTAMP, server_default=func.now())


class ExternalEvent(Base):
    __tablename__ = "external_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(20))
    event_title = Column(Text)
    source = Column(String(100))
    confidence = Column(Numeric(4, 3))
    raw_data = Column(JSONB)
    market_related_ids = Column(ARRAY(Integer))
    created_at = Column(TIMESTAMP, server_default=func.now())


class RoanPerformance(Base):
    __tablename__ = "roan_performance"

    date = Column(Date, primary_key=True)
    signals_sent = Column(Integer, default=0)
    signals_profitable = Column(Integer, default=0)
    total_profit_usd = Column(Numeric(10, 2), default=0)
    capital_used = Column(Numeric(10, 2), default=0)
