-- Roan Arbitrage Machine - Database Schema
-- Run this to initialize the PostgreSQL database

-- 1. Markets table: stores Polymarket market data
CREATE TABLE IF NOT EXISTS markets (
    id SERIAL PRIMARY KEY,
    polymarket_id VARCHAR(100) UNIQUE NOT NULL,
    category VARCHAR(20) NOT NULL,  -- macro, weather, politics, earnings, regulatory, geopolitical
    title TEXT,
    yes_price DECIMAL(5,4),
    no_price DECIMAL(5,4),
    liquidity DECIMAL(12,2),
    end_timestamp VARCHAR(50),
    rules JSONB,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_markets_category ON markets(category);
CREATE INDEX IF NOT EXISTS idx_markets_updated_at ON markets(updated_at DESC);

-- 2. Roan signals table: stores detected arbitrage signals
CREATE TABLE IF NOT EXISTS roan_signals (
    id SERIAL PRIMARY KEY,
    market_id INT REFERENCES markets(id),
    signal_type VARCHAR(20),  -- price_sum, info_lead, logic_arb
    profit_pct DECIMAL(6,4),
    confidence DECIMAL(4,3),
    suggested_position DECIMAL(10,2),
    telegram_msg_id BIGINT,
    status VARCHAR(20) DEFAULT 'pending',  -- pending, sent, executed, ignored
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_status ON roan_signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON roan_signals(created_at DESC);

-- 3. External events table: FRED / RSS events correlated to markets
CREATE TABLE IF NOT EXISTS external_events (
    id SERIAL PRIMARY KEY,
    category VARCHAR(20),
    event_title TEXT,
    source VARCHAR(100),
    confidence DECIMAL(4,3),
    raw_data JSONB,
    market_related_ids INTEGER[],
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_category ON external_events(category);

-- 4. Roan performance tracking
CREATE TABLE IF NOT EXISTS roan_performance (
    date DATE PRIMARY KEY,
    signals_sent INT DEFAULT 0,
    signals_profitable INT DEFAULT 0,
    total_profit_usd DECIMAL(10,2) DEFAULT 0,
    capital_used DECIMAL(10,2) DEFAULT 0
);
