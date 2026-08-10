-- Immutable, provider-contract-bound index evidence for runtime risk signals.

CREATE TABLE IF NOT EXISTS market_index_levels (
    id BIGSERIAL PRIMARY KEY,
    provider_contract_id BIGINT NOT NULL
        REFERENCES market_data_provider_contracts(id) ON DELETE RESTRICT,
    symbol VARCHAR(32) NOT NULL,
    mic CHAR(4) NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    source VARCHAR(100) NOT NULL,
    level DECIMAL(20, 8) NOT NULL,
    source_checksum_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_market_index_level UNIQUE (
        provider_contract_id,
        symbol,
        event_time
    ),
    CONSTRAINT ck_market_index_level_symbol CHECK (
        symbol ~ '^[A-Z0-9][A-Z0-9 .-]{0,31}$'
    ),
    CONSTRAINT ck_market_index_level_mic CHECK (
        mic ~ '^[A-Z0-9]{4}$'
    ),
    CONSTRAINT ck_market_index_level_time CHECK (
        event_time <= received_at
        AND received_at <= created_at + INTERVAL '5 seconds'
    ),
    CONSTRAINT ck_market_index_level_value CHECK (level > 0),
    CONSTRAINT ck_market_index_level_source CHECK (
        LENGTH(BTRIM(source)) BETWEEN 1 AND 100
    ),
    CONSTRAINT ck_market_index_level_checksum CHECK (
        source_checksum_sha256 ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX IF NOT EXISTS idx_market_index_level_runtime
    ON market_index_levels(
        symbol,
        mic,
        event_time DESC,
        provider_contract_id
    );

CREATE OR REPLACE FUNCTION enforce_market_index_level_evidence()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    contract_provider VARCHAR(100);
    contract_mic CHAR(4);
    contract_data_type VARCHAR(50);
    contract_valid_from DATE;
    contract_valid_until DATE;
BEGIN
    IF TG_OP != 'INSERT' THEN
        RAISE EXCEPTION 'market index levels are append-only';
    END IF;

    SELECT
        provider,
        mic,
        data_type,
        valid_from,
        valid_until
    INTO
        contract_provider,
        contract_mic,
        contract_data_type,
        contract_valid_from,
        contract_valid_until
    FROM market_data_provider_contracts
    WHERE id = NEW.provider_contract_id;

    IF (
        contract_provider IS NULL
        OR NEW.mic != contract_mic
        OR contract_data_type NOT IN (
            'delayed-index-level',
            'realtime-index-level'
        )
        OR NEW.source NOT LIKE contract_provider || '%'
        OR (
            NEW.event_time AT TIME ZONE 'Europe/Stockholm'
        )::DATE NOT BETWEEN contract_valid_from AND contract_valid_until
    ) THEN
        RAISE EXCEPTION
            'index level does not match its provider contract';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_market_index_level_evidence
BEFORE INSERT OR UPDATE OR DELETE ON market_index_levels
FOR EACH ROW EXECUTE FUNCTION enforce_market_index_level_evidence();
