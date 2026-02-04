-- Trading Agent Database Schema

-- Companies on Stockholm Stock Exchange
CREATE TABLE companies (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    sector VARCHAR(100),
    industry VARCHAR(100),
    description TEXT,
    inputs JSONB DEFAULT '[]',  -- Raw materials, currencies, etc
    competitors JSONB DEFAULT '[]',
    market_cap BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Daily price data
CREATE TABLE prices (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    open DECIMAL(12, 4),
    high DECIMAL(12, 4),
    low DECIMAL(12, 4),
    close DECIMAL(12, 4),
    volume BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(ticker, date)
);

-- Fundamental data
CREATE TABLE fundamentals (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    pe_ratio DECIMAL(10, 2),
    pb_ratio DECIMAL(10, 2),
    eps DECIMAL(10, 2),
    dividend_yield DECIMAL(6, 4),
    market_cap BIGINT,
    revenue BIGINT,
    profit_margin DECIMAL(6, 4),
    data JSONB,  -- Additional data
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(ticker, date)
);

-- Macro data (commodities, currencies, rates)
CREATE TABLE macro (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(50) NOT NULL,
    type VARCHAR(50) NOT NULL,  -- commodity, currency, rate
    date DATE NOT NULL,
    value DECIMAL(16, 6),
    change_pct DECIMAL(8, 4),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, date)
);

-- Paper trading portfolio
CREATE TABLE portfolio (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    shares DECIMAL(12, 4) NOT NULL,
    avg_price DECIMAL(12, 4) NOT NULL,
    current_price DECIMAL(12, 4),
    unrealized_pnl DECIMAL(12, 2),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Trade history
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    action VARCHAR(10) NOT NULL,  -- BUY, SELL
    shares DECIMAL(12, 4) NOT NULL,
    price DECIMAL(12, 4) NOT NULL,
    total_value DECIMAL(12, 2) NOT NULL,
    reasoning TEXT NOT NULL,  -- Why the agent made this trade
    confidence DECIMAL(4, 2),  -- 0-100
    hypothesis TEXT,  -- What the agent expects to happen
    outcome TEXT,  -- What actually happened (filled later)
    outcome_correct BOOLEAN,  -- Was the hypothesis correct?
    pnl DECIMAL(12, 2),  -- Profit/loss when position closed
    macro_context JSONB,  -- Relevant macro data at time of trade
    executed_at TIMESTAMP DEFAULT NOW(),
    closed_at TIMESTAMP
);

-- Agent learnings / knowledge base
CREATE TABLE learnings (
    id SERIAL PRIMARY KEY,
    category VARCHAR(50) NOT NULL,  -- pattern, mistake, insight, rule
    content TEXT NOT NULL,
    source_trade_ids INTEGER[],
    confidence DECIMAL(4, 2),
    times_validated INTEGER DEFAULT 0,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Weekly reviews
CREATE TABLE reviews (
    id SERIAL PRIMARY KEY,
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    total_pnl DECIMAL(12, 2),
    win_rate DECIMAL(5, 2),
    best_trade_id INTEGER REFERENCES trades(id),
    worst_trade_id INTEGER REFERENCES trades(id),
    patterns_identified TEXT[],
    strategy_adjustments TEXT[],
    reflection TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(week_start)
);

-- Portfolio cash balance
CREATE TABLE balance (
    id SERIAL PRIMARY KEY,
    cash DECIMAL(14, 2) NOT NULL DEFAULT 20000.00,
    total_value DECIMAL(14, 2) NOT NULL DEFAULT 20000.00,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Initialize with starting capital
INSERT INTO balance (cash, total_value) VALUES (20000.00, 20000.00);

-- Indexes
CREATE INDEX idx_prices_ticker_date ON prices(ticker, date DESC);
CREATE INDEX idx_trades_ticker ON trades(ticker);
CREATE INDEX idx_trades_executed_at ON trades(executed_at DESC);
CREATE INDEX idx_macro_symbol_date ON macro(symbol, date DESC);
CREATE INDEX idx_fundamentals_ticker ON fundamentals(ticker);
