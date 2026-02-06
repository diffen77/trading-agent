-- Migration 002: Company input dependencies and trade outcomes
-- Run against existing database

-- Input dependencies table (relational, not JSONB)
CREATE TABLE IF NOT EXISTS input_dependencies (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL REFERENCES companies(ticker),
    input_name VARCHAR(100) NOT NULL,        -- e.g. 'oil', 'copper', 'EUR/SEK'
    macro_symbol VARCHAR(50),                 -- Yahoo symbol e.g. 'BZ=F', 'HG=F'
    impact_direction VARCHAR(10) NOT NULL DEFAULT 'cost',  -- 'cost' or 'revenue'
    impact_strength DECIMAL(3,2) NOT NULL DEFAULT 0.5,     -- 0.0-1.0
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(ticker, input_name)
);

-- Trade outcomes for learning loop
CREATE TABLE IF NOT EXISTS trade_outcomes (
    id SERIAL PRIMARY KEY,
    trade_id INTEGER NOT NULL REFERENCES trades(id),
    check_date DATE NOT NULL,
    days_since_entry INTEGER NOT NULL,
    price_at_check DECIMAL(12, 4),
    pnl_pct DECIMAL(8, 4),
    pnl_amount DECIMAL(12, 2),
    hypothesis_correct BOOLEAN,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(trade_id, check_date)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_input_deps_ticker ON input_dependencies(ticker);
CREATE INDEX IF NOT EXISTS idx_input_deps_symbol ON input_dependencies(macro_symbol);
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_trade ON trade_outcomes(trade_id);
