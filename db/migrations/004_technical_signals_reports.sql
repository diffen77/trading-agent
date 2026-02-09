-- Migration 004: Technical signals and report calendar

CREATE TABLE IF NOT EXISTS technical_signals (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    rsi DECIMAL(8,4),
    sma20 DECIMAL(12,4),
    sma50 DECIMAL(12,4),
    volume_ratio DECIMAL(8,4),
    momentum_score DECIMAL(8,4),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_technical_ticker_date ON technical_signals(ticker, date DESC);

CREATE TABLE IF NOT EXISTS report_calendar (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    report_date DATE NOT NULL,
    report_type VARCHAR(20),  -- Q1, Q2, Q3, Q4, bokslut
    source VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(ticker, report_date)
);

CREATE INDEX IF NOT EXISTS idx_report_calendar_date ON report_calendar(report_date);
CREATE INDEX IF NOT EXISTS idx_report_calendar_ticker ON report_calendar(ticker);
