-- Exact quote provenance for every persisted portfolio valuation.

CREATE TABLE IF NOT EXISTS portfolio_valuation_marks (
    id BIGSERIAL PRIMARY KEY,
    portfolio_history_id BIGINT NOT NULL
        REFERENCES portfolio_history(id) ON DELETE CASCADE,
    ticker VARCHAR(20) NOT NULL,
    quote_id BIGINT NOT NULL
        REFERENCES market_quotes(id) ON DELETE RESTRICT,
    shares DECIMAL(20, 8) NOT NULL,
    price DECIMAL(14, 4) NOT NULL,
    market_value DECIMAL(16, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_portfolio_valuation_mark UNIQUE (
        portfolio_history_id,
        ticker
    ),
    CONSTRAINT ck_portfolio_valuation_mark_values CHECK (
        shares > 0
        AND price > 0
        AND market_value >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_portfolio_valuation_marks_quote
    ON portfolio_valuation_marks(quote_id);
