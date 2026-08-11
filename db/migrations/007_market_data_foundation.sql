-- Provider-neutral instrument identity, intraday data, and market calendar.

CREATE TABLE IF NOT EXISTS instruments (
    id BIGSERIAL PRIMARY KEY,
    isin CHAR(12) NOT NULL UNIQUE,
    symbol VARCHAR(32) NOT NULL,
    name VARCHAR(255) NOT NULL,
    mic CHAR(4) NOT NULL,
    currency CHAR(3) NOT NULL,
    instrument_type VARCHAR(40) NOT NULL,
    primary_listing BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    first_trade_date DATE,
    last_trade_date DATE,
    source VARCHAR(100) NOT NULL,
    source_updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_instruments_mic_symbol UNIQUE (mic, symbol),
    CONSTRAINT ck_instruments_isin CHECK (
        isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'
    ),
    CONSTRAINT ck_instruments_mic CHECK (mic ~ '^[A-Z0-9]{4}$'),
    CONSTRAINT ck_instruments_currency CHECK (currency ~ '^[A-Z]{3}$'),
    CONSTRAINT ck_instruments_status CHECK (
        status IN ('ACTIVE', 'INACTIVE', 'DELISTED', 'SUSPENDED')
    ),
    CONSTRAINT ck_instruments_trade_dates CHECK (
        last_trade_date IS NULL
        OR first_trade_date IS NULL
        OR last_trade_date >= first_trade_date
    )
);

CREATE INDEX IF NOT EXISTS idx_instruments_scope
    ON instruments(mic, status, instrument_type);

CREATE TABLE IF NOT EXISTS instrument_aliases (
    id BIGSERIAL PRIMARY KEY,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    provider VARCHAR(100) NOT NULL,
    provider_symbol VARCHAR(100) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from DATE,
    valid_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_instrument_alias UNIQUE (provider, provider_symbol),
    CONSTRAINT ck_instrument_alias_dates CHECK (
        valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from
    )
);

CREATE INDEX IF NOT EXISTS idx_instrument_alias_instrument
    ON instrument_aliases(instrument_id, active);

CREATE TABLE IF NOT EXISTS market_quotes (
    id BIGSERIAL PRIMARY KEY,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    event_time TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source VARCHAR(100) NOT NULL,
    last_price DECIMAL(18, 6) NOT NULL,
    bid_price DECIMAL(18, 6),
    ask_price DECIMAL(18, 6),
    volume DECIMAL(20, 4),
    currency CHAR(3) NOT NULL,
    raw_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_market_quote UNIQUE (
        instrument_id, source, event_time
    ),
    CONSTRAINT ck_market_quote_event_time CHECK (
        event_time <= received_at + INTERVAL '5 seconds'
    ),
    CONSTRAINT ck_market_quote_prices CHECK (
        last_price > 0
        AND (bid_price IS NULL OR bid_price > 0)
        AND (ask_price IS NULL OR ask_price > 0)
    ),
    CONSTRAINT ck_market_quote_spread CHECK (
        bid_price IS NULL OR ask_price IS NULL OR ask_price >= bid_price
    ),
    CONSTRAINT ck_market_quote_volume CHECK (
        volume IS NULL OR volume >= 0
    ),
    CONSTRAINT ck_market_quote_currency CHECK (
        currency ~ '^[A-Z]{3}$'
    )
);

CREATE INDEX IF NOT EXISTS idx_market_quotes_latest
    ON market_quotes(instrument_id, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_market_quotes_received
    ON market_quotes(received_at DESC);

CREATE TABLE IF NOT EXISTS market_bars (
    id BIGSERIAL PRIMARY KEY,
    instrument_id BIGINT NOT NULL REFERENCES instruments(id),
    interval_seconds INTEGER NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source VARCHAR(100) NOT NULL,
    open DECIMAL(18, 6) NOT NULL,
    high DECIMAL(18, 6) NOT NULL,
    low DECIMAL(18, 6) NOT NULL,
    close DECIMAL(18, 6) NOT NULL,
    volume DECIMAL(20, 4),
    currency CHAR(3) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_market_bar UNIQUE (
        instrument_id, interval_seconds, source, event_time
    ),
    CONSTRAINT ck_market_bar_interval CHECK (interval_seconds > 0),
    CONSTRAINT ck_market_bar_event_time CHECK (
        event_time <= received_at + INTERVAL '5 seconds'
    ),
    CONSTRAINT ck_market_bar_prices CHECK (
        low > 0
        AND high >= low
        AND open BETWEEN low AND high
        AND close BETWEEN low AND high
    ),
    CONSTRAINT ck_market_bar_volume CHECK (
        volume IS NULL OR volume >= 0
    ),
    CONSTRAINT ck_market_bar_currency CHECK (
        currency ~ '^[A-Z]{3}$'
    )
);

CREATE INDEX IF NOT EXISTS idx_market_bars_latest
    ON market_bars(instrument_id, interval_seconds, event_time DESC);

CREATE TABLE IF NOT EXISTS market_sessions (
    id BIGSERIAL PRIMARY KEY,
    mic CHAR(4) NOT NULL,
    session_date DATE NOT NULL,
    opens_at TIMESTAMPTZ,
    closes_at TIMESTAMPTZ,
    session_type VARCHAR(20) NOT NULL DEFAULT 'REGULAR',
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    timezone_name VARCHAR(100) NOT NULL DEFAULT 'Europe/Stockholm',
    source VARCHAR(100) NOT NULL,
    source_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_market_session UNIQUE (mic, session_date),
    CONSTRAINT ck_market_session_mic CHECK (mic ~ '^[A-Z0-9]{4}$'),
    CONSTRAINT ck_market_session_status CHECK (
        status IN ('OPEN', 'CLOSED', 'HALF_DAY')
    ),
    CONSTRAINT ck_market_session_hours CHECK (
        (status = 'CLOSED' AND opens_at IS NULL AND closes_at IS NULL)
        OR (
            status IN ('OPEN', 'HALF_DAY')
            AND opens_at IS NOT NULL
            AND closes_at IS NOT NULL
            AND closes_at > opens_at
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_market_sessions_date
    ON market_sessions(mic, session_date);

CREATE TABLE IF NOT EXISTS data_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(100) NOT NULL,
    data_type VARCHAR(50) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING',
    records_received INTEGER NOT NULL DEFAULT 0,
    records_rejected INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    CONSTRAINT ck_data_sync_status CHECK (
        status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'PARTIAL')
    ),
    CONSTRAINT ck_data_sync_counts CHECK (
        records_received >= 0 AND records_rejected >= 0
    ),
    CONSTRAINT ck_data_sync_finished CHECK (
        finished_at IS NULL OR finished_at >= started_at
    )
);

CREATE INDEX IF NOT EXISTS idx_data_sync_runs_latest
    ON data_sync_runs(provider, data_type, started_at DESC);

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS instrument_id BIGINT REFERENCES instruments(id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_instrument
    ON companies(instrument_id)
    WHERE instrument_id IS NOT NULL;

ALTER TABLE prices
    ADD COLUMN IF NOT EXISTS instrument_id BIGINT REFERENCES instruments(id);

CREATE INDEX IF NOT EXISTS idx_prices_instrument_date
    ON prices(instrument_id, date DESC)
    WHERE instrument_id IS NOT NULL;
