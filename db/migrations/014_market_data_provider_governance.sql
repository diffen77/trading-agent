-- Fail-closed legal, transport, coverage, and freshness evidence.

CREATE TABLE IF NOT EXISTS market_data_provider_contracts (
    id BIGSERIAL PRIMARY KEY,
    contract_key VARCHAR(100) NOT NULL UNIQUE,
    provider VARCHAR(100) NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    data_type VARCHAR(50) NOT NULL,
    mic CHAR(4) NOT NULL,
    delivery_mode VARCHAR(20) NOT NULL,
    transport VARCHAR(30) NOT NULL,
    nominal_delay_seconds INTEGER NOT NULL,
    max_transport_lag_seconds INTEGER NOT NULL,
    usage_scope VARCHAR(50) NOT NULL,
    non_display_category VARCHAR(20) NOT NULL DEFAULT 'NONE',
    external_distribution BOOLEAN NOT NULL DEFAULT FALSE,
    reference_symbols_included BOOLEAN NOT NULL DEFAULT FALSE,
    terms_url TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    valid_from DATE NOT NULL,
    valid_until DATE NOT NULL,
    legal_reviewed_at TIMESTAMPTZ,
    transport_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_provider_contract_key CHECK (
        contract_key ~ '^[a-z0-9][a-z0-9-]{1,99}$'
    ),
    CONSTRAINT ck_provider_contract_provider CHECK (
        provider ~ '^[a-z0-9][a-z0-9-]{1,99}$'
    ),
    CONSTRAINT ck_provider_contract_mic CHECK (
        mic ~ '^[A-Z0-9]{4}$'
    ),
    CONSTRAINT ck_provider_contract_delivery_mode CHECK (
        delivery_mode IN ('DELAYED_15M', 'REALTIME')
    ),
    CONSTRAINT ck_provider_contract_transport CHECK (
        transport IN (
            'AUTHORIZED_VENDOR',
            'DIRECT_FEED',
            'PUBLIC_CSV',
            'SFTP',
            'WEB_API'
        )
    ),
    CONSTRAINT ck_provider_contract_delay CHECK (
        (
            delivery_mode = 'DELAYED_15M'
            AND nominal_delay_seconds = 900
        )
        OR (
            delivery_mode = 'REALTIME'
            AND nominal_delay_seconds = 0
        )
    ),
    CONSTRAINT ck_provider_contract_transport_lag CHECK (
        max_transport_lag_seconds BETWEEN 0 AND 300
    ),
    CONSTRAINT ck_provider_contract_usage CHECK (
        usage_scope IN (
            'AUTOMATED_LIVE_TRADING',
            'INTERNAL_ANALYSIS_AND_PAPER'
        )
    ),
    CONSTRAINT ck_provider_contract_non_display CHECK (
        non_display_category IN ('NONE', 'CATEGORY_1', 'CATEGORY_2')
        AND (
            delivery_mode != 'REALTIME'
            OR non_display_category != 'NONE'
        )
        AND (
            usage_scope != 'AUTOMATED_LIVE_TRADING'
            OR non_display_category = 'CATEGORY_2'
        )
    ),
    CONSTRAINT ck_provider_contract_terms_url CHECK (
        terms_url LIKE 'https://%'
    ),
    CONSTRAINT ck_provider_contract_status CHECK (
        status IN ('DRAFT', 'VALIDATED', 'REVOKED')
    ),
    CONSTRAINT ck_provider_contract_dates CHECK (
        valid_until >= valid_from
    ),
    CONSTRAINT ck_provider_contract_validated CHECK (
        status != 'VALIDATED'
        OR (
            legal_reviewed_at IS NOT NULL
            AND transport_verified_at IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_provider_contract_runtime
    ON market_data_provider_contracts(
        provider, data_type, mic, status, valid_until
    );

CREATE TABLE IF NOT EXISTS market_data_provider_validations (
    id BIGSERIAL PRIMARY KEY,
    contract_id BIGINT NOT NULL
        REFERENCES market_data_provider_contracts(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL,
    validated_at TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    expected_instruments INTEGER NOT NULL,
    product_covered_instruments INTEGER NOT NULL,
    symbol_mapped_instruments INTEGER NOT NULL,
    sample_file_count INTEGER NOT NULL,
    sample_quote_count INTEGER NOT NULL,
    max_observed_delivery_seconds INTEGER,
    evidence_checksum_sha256 CHAR(64) NOT NULL,
    validated_by VARCHAR(100) NOT NULL,
    notes VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_provider_validation_evidence UNIQUE (
        contract_id, evidence_checksum_sha256
    ),
    CONSTRAINT ck_provider_validation_status CHECK (
        status IN ('PASSED', 'FAILED')
    ),
    CONSTRAINT ck_provider_validation_time CHECK (
        valid_until >= validated_at
    ),
    CONSTRAINT ck_provider_validation_counts CHECK (
        expected_instruments > 0
        AND product_covered_instruments BETWEEN 0 AND expected_instruments
        AND symbol_mapped_instruments BETWEEN 0 AND expected_instruments
        AND sample_file_count >= 0
        AND sample_quote_count >= 0
    ),
    CONSTRAINT ck_provider_validation_delivery CHECK (
        max_observed_delivery_seconds IS NULL
        OR max_observed_delivery_seconds BETWEEN 0 AND 86400
    ),
    CONSTRAINT ck_provider_validation_checksum CHECK (
        evidence_checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_provider_validation_pass CHECK (
        status != 'PASSED'
        OR (
            product_covered_instruments = expected_instruments
            AND symbol_mapped_instruments = expected_instruments
            AND sample_file_count > 0
            AND sample_quote_count > 0
            AND max_observed_delivery_seconds IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_provider_validation_latest
    ON market_data_provider_validations(contract_id, validated_at DESC);

INSERT INTO market_data_provider_contracts (
    contract_key,
    provider,
    product_name,
    data_type,
    mic,
    delivery_mode,
    transport,
    nominal_delay_seconds,
    max_transport_lag_seconds,
    usage_scope,
    non_display_category,
    external_distribution,
    reference_symbols_included,
    terms_url,
    status,
    valid_from,
    valid_until,
    legal_reviewed_at
)
VALUES (
    'nasdaq-nordic-delayed-xsto-v1',
    'nasdaq-nordic',
    'Nasdaq Nordic Equity Level 1 delayed',
    'delayed-post-trade-equity',
    'XSTO',
    'DELAYED_15M',
    'PUBLIC_CSV',
    900,
    30,
    'INTERNAL_ANALYSIS_AND_PAPER',
    'NONE',
    FALSE,
    FALSE,
    'https://www.nasdaq.com/docs/Nasdaq_European_Data_Policies_April_2026',
    'DRAFT',
    DATE '2026-04-01',
    DATE '2026-08-31',
    NOW()
)
ON CONFLICT (contract_key) DO NOTHING;
