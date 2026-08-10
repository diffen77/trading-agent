-- Exactly-once paper orders and auditable FIFO cost-basis allocation.

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(160),
    ADD COLUMN IF NOT EXISTS request_fingerprint CHAR(64);

UPDATE trades
SET
    idempotency_key = COALESCE(idempotency_key, 'legacy:' || id::text),
    request_fingerprint = COALESCE(request_fingerprint, repeat('0', 64))
WHERE idempotency_key IS NULL OR request_fingerprint IS NULL;

ALTER TABLE trades
    ALTER COLUMN idempotency_key SET NOT NULL,
    ALTER COLUMN request_fingerprint SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_idempotency_key
    ON trades(idempotency_key);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_trades_idempotency_key'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_idempotency_key CHECK (
                idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9:._/-]{7,159}$'
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_trades_request_fingerprint'
    ) THEN
        ALTER TABLE trades
            ADD CONSTRAINT ck_trades_request_fingerprint CHECK (
                request_fingerprint ~ '^[0-9a-f]{64}$'
            );
    END IF;
END
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM portfolio WHERE shares <= 0) THEN
        RAISE EXCEPTION
            'Cannot create position lots: portfolio has non-positive shares';
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS position_lots (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    entry_trade_id INTEGER REFERENCES trades(id),
    original_shares DECIMAL(12, 4) NOT NULL,
    remaining_shares DECIMAL(12, 4) NOT NULL,
    entry_price DECIMAL(12, 4) NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    source VARCHAR(32) NOT NULL DEFAULT 'TRADE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_position_lots_entry_trade UNIQUE (entry_trade_id),
    CONSTRAINT ck_position_lots_shares CHECK (
        original_shares > 0
        AND remaining_shares >= 0
        AND remaining_shares <= original_shares
    ),
    CONSTRAINT ck_position_lots_entry_price CHECK (entry_price > 0),
    CONSTRAINT ck_position_lots_source CHECK (
        source IN ('TRADE', 'MIGRATED_AVERAGE_COST')
    ),
    CONSTRAINT ck_position_lots_entry_source CHECK (
        (source = 'TRADE' AND entry_trade_id IS NOT NULL)
        OR (source = 'MIGRATED_AVERAGE_COST' AND entry_trade_id IS NULL)
    ),
    CONSTRAINT ck_position_lots_closed CHECK (
        (remaining_shares = 0 AND closed_at IS NOT NULL)
        OR (remaining_shares > 0 AND closed_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_position_lots_open_fifo
    ON position_lots(ticker, opened_at, id)
    WHERE remaining_shares > 0;

INSERT INTO position_lots (
    ticker,
    entry_trade_id,
    original_shares,
    remaining_shares,
    entry_price,
    opened_at,
    source
)
SELECT
    p.ticker,
    NULL,
    p.shares,
    p.shares,
    p.avg_price,
    COALESCE(p.created_at, NOW()),
    'MIGRATED_AVERAGE_COST'
FROM portfolio p
WHERE NOT EXISTS (
    SELECT 1
    FROM position_lots lot
    WHERE lot.ticker = p.ticker AND lot.remaining_shares > 0
);

CREATE TABLE IF NOT EXISTS trade_lot_allocations (
    id BIGSERIAL PRIMARY KEY,
    sell_trade_id INTEGER NOT NULL REFERENCES trades(id),
    lot_id BIGINT NOT NULL REFERENCES position_lots(id),
    buy_trade_id INTEGER REFERENCES trades(id),
    shares DECIMAL(12, 4) NOT NULL,
    entry_price DECIMAL(12, 4) NOT NULL,
    exit_price DECIMAL(12, 4) NOT NULL,
    realized_pnl DECIMAL(14, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_trade_lot_allocation UNIQUE (sell_trade_id, lot_id),
    CONSTRAINT ck_trade_lot_allocation_values CHECK (
        shares > 0 AND entry_price > 0 AND exit_price > 0
    )
);

CREATE INDEX IF NOT EXISTS idx_trade_lot_allocations_buy
    ON trade_lot_allocations(buy_trade_id, created_at);
