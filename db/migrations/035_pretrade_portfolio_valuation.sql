-- Preserve exact provider evidence when a portfolio is valued from either
-- last-trade quotes or the authorized delayed pre-trade order book.

ALTER TABLE portfolio_valuation_marks
    ADD COLUMN IF NOT EXISTS source_book_state_id BIGINT
        REFERENCES pre_trade_book_states(id) ON DELETE RESTRICT;

ALTER TABLE portfolio_valuation_marks
    ALTER COLUMN quote_id DROP NOT NULL,
    ALTER COLUMN price TYPE DECIMAL(20, 8);

ALTER TABLE portfolio_valuation_marks
    DROP CONSTRAINT IF EXISTS ck_portfolio_valuation_mark_evidence;

ALTER TABLE portfolio_valuation_marks
    ADD CONSTRAINT ck_portfolio_valuation_mark_evidence CHECK (
        (quote_id IS NOT NULL AND source_book_state_id IS NULL)
        OR
        (quote_id IS NULL AND source_book_state_id IS NOT NULL)
    );

CREATE INDEX IF NOT EXISTS idx_portfolio_valuation_marks_book_state
    ON portfolio_valuation_marks(source_book_state_id)
    WHERE source_book_state_id IS NOT NULL;
