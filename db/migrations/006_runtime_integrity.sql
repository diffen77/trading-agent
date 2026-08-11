-- Complete the runtime schema without replaying destructive legacy seeds.

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS target_price DECIMAL(12, 4),
    ADD COLUMN IF NOT EXISTS stop_loss DECIMAL(12, 4),
    ADD COLUMN IF NOT EXISTS target_pct DECIMAL(8, 4),
    ADD COLUMN IF NOT EXISTS stop_loss_pct DECIMAL(8, 4),
    ADD COLUMN IF NOT EXISTS entry_trade_id INTEGER REFERENCES trades(id);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM portfolio GROUP BY ticker HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Cannot add portfolio ticker uniqueness: duplicate positions exist';
    END IF;

    IF EXISTS (
        SELECT 1 FROM portfolio
        WHERE shares < 0 OR avg_price <= 0
    ) THEN
        RAISE EXCEPTION
            'Cannot add portfolio constraints: invalid positions exist';
    END IF;

    IF EXISTS (
        SELECT 1 FROM trades
        WHERE action NOT IN ('BUY', 'SELL')
           OR shares <= 0
           OR price <= 0
           OR total_value <= 0
           OR confidence < 0
           OR confidence > 100
    ) THEN
        RAISE EXCEPTION
            'Cannot add trade constraints: invalid trades exist';
    END IF;

    IF EXISTS (SELECT 1 FROM balance WHERE cash < 0 OR total_value < 0) THEN
        RAISE EXCEPTION
            'Cannot add balance constraints: negative values exist';
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_ticker ON portfolio(ticker);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_portfolio_shares'
    ) THEN
        ALTER TABLE portfolio
            ADD CONSTRAINT ck_portfolio_shares CHECK (shares >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_portfolio_avg_price'
    ) THEN
        ALTER TABLE portfolio
            ADD CONSTRAINT ck_portfolio_avg_price CHECK (avg_price > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_trades_action'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_action CHECK (action IN ('BUY', 'SELL'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_trades_shares'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_shares CHECK (shares > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_trades_price'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_price CHECK (price > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_trades_total_value'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_total_value CHECK (total_value > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_trades_confidence'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_confidence
            CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 100);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_balance_cash'
    ) THEN
        ALTER TABLE balance
            ADD CONSTRAINT ck_balance_cash CHECK (cash >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_balance_total_value'
    ) THEN
        ALTER TABLE balance
            ADD CONSTRAINT ck_balance_total_value CHECK (total_value >= 0);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS news (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20),
    headline VARCHAR(500) NOT NULL,
    summary VARCHAR(1000),
    source VARCHAR(255) NOT NULL,
    url TEXT NOT NULL,
    sentiment VARCHAR(20) NOT NULL DEFAULT 'neutral',
    sentiment_score DECIMAL(8, 4),
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_news_url ON news(url);
CREATE INDEX IF NOT EXISTS idx_news_ticker_published
    ON news(ticker, published_at DESC);

CREATE TABLE IF NOT EXISTS prospects (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    name VARCHAR(255) NOT NULL,
    thesis TEXT NOT NULL,
    target_price DECIMAL(12, 4),
    current_price DECIMAL(12, 4),
    entry_trigger TEXT,
    confidence DECIMAL(5, 2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'watching',
    priority INTEGER,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_prospects_ticker_status UNIQUE (ticker, status),
    CONSTRAINT ck_prospects_confidence CHECK (confidence BETWEEN 0 AND 100)
);

CREATE INDEX IF NOT EXISTS idx_prospects_status_priority
    ON prospects(status, priority, confidence DESC);

CREATE TABLE IF NOT EXISTS portfolio_history (
    id BIGSERIAL PRIMARY KEY,
    total_value DECIMAL(14, 2) NOT NULL,
    cash DECIMAL(14, 2) NOT NULL,
    positions_value DECIMAL(14, 2) NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_portfolio_history_values CHECK (
        total_value >= 0 AND cash >= 0 AND positions_value >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_portfolio_history_recorded_at
    ON portfolio_history(recorded_at DESC);

CREATE TABLE IF NOT EXISTS technical_signals (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    rsi DECIMAL(8, 4),
    sma20 DECIMAL(12, 4),
    sma50 DECIMAL(12, 4),
    volume_ratio DECIMAL(8, 4),
    momentum_score DECIMAL(8, 4),
    pattern VARCHAR(100),
    pattern_signal VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_technical_signals_ticker_date UNIQUE (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_technical_ticker_date
    ON technical_signals(ticker, date DESC);

ALTER TABLE technical_signals
    ADD COLUMN IF NOT EXISTS pattern VARCHAR(100),
    ADD COLUMN IF NOT EXISTS pattern_signal VARCHAR(20);

CREATE TABLE IF NOT EXISTS report_calendar (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    report_date DATE NOT NULL,
    report_type VARCHAR(20),
    source VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_report_calendar_ticker_date UNIQUE (ticker, report_date)
);

CREATE INDEX IF NOT EXISTS idx_report_calendar_date
    ON report_calendar(report_date);
CREATE INDEX IF NOT EXISTS idx_report_calendar_ticker
    ON report_calendar(ticker);

CREATE TABLE IF NOT EXISTS backtest_results (
    id BIGSERIAL PRIMARY KEY,
    strategy_name VARCHAR(100) NOT NULL,
    params TEXT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    trades_count INTEGER NOT NULL DEFAULT 0,
    win_rate DECIMAL(8, 4),
    total_return_pct DECIMAL(12, 4),
    avg_trade_pct DECIMAL(12, 4),
    max_drawdown_pct DECIMAL(12, 4),
    sharpe_ratio DECIMAL(12, 4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_backtest_period CHECK (period_end >= period_start),
    CONSTRAINT ck_backtest_trade_count CHECK (trades_count >= 0)
);

CREATE TABLE IF NOT EXISTS report_reactions (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    report_date DATE NOT NULL,
    report_type VARCHAR(20) NOT NULL,
    price_before DECIMAL(12, 4),
    price_day1 DECIMAL(12, 4),
    price_day5 DECIMAL(12, 4),
    price_day10 DECIMAL(12, 4),
    reaction_pct_day1 DECIMAL(12, 4),
    reaction_pct_day5 DECIMAL(12, 4),
    reaction_pct_day10 DECIMAL(12, 4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_report_reaction UNIQUE (ticker, report_date, report_type)
);

CREATE TABLE IF NOT EXISTS research_notes (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    topic TEXT NOT NULL,
    content TEXT,
    source TEXT NOT NULL,
    relevance_score DECIMAL(5, 2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_research_note UNIQUE (ticker, topic, source),
    CONSTRAINT ck_research_relevance CHECK (
        relevance_score IS NULL OR relevance_score BETWEEN 0 AND 100
    )
);

CREATE INDEX IF NOT EXISTS idx_research_notes_ticker_created
    ON research_notes(ticker, created_at DESC);

CREATE TABLE IF NOT EXISTS strategy_insights (
    id BIGSERIAL PRIMARY KEY,
    insight_type VARCHAR(100) NOT NULL,
    content TEXT NOT NULL,
    confidence DECIMAL(5, 2),
    evidence TEXT,
    strategy_version VARCHAR(50) NOT NULL DEFAULT 'unversioned',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_strategy_insight_confidence CHECK (
        confidence IS NULL OR confidence BETWEEN 0 AND 100
    )
);

CREATE INDEX IF NOT EXISTS idx_strategy_insights_active_created
    ON strategy_insights(active, created_at DESC);
