-- Immutable Nasdaq ticker aliases bound to an exact frozen FIRDS universe.

CREATE TABLE IF NOT EXISTS instrument_alias_snapshots (
    id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(100) NOT NULL,
    mic CHAR(4) NOT NULL,
    business_date DATE NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    tip_version VARCHAR(30) NOT NULL,
    source_path TEXT NOT NULL,
    file_checksum_sha256 CHAR(64) NOT NULL,
    content_encoding VARCHAR(20) NOT NULL,
    compressed_content BYTEA NOT NULL,
    uncompressed_bytes INTEGER NOT NULL,
    reference_snapshot_id BIGINT NOT NULL
        REFERENCES reference_data_snapshots(id) ON DELETE RESTRICT,
    reference_checksum_sha256 CHAR(64) NOT NULL,
    alias_checksum_sha256 CHAR(64) NOT NULL,
    instrument_count INTEGER NOT NULL,
    sync_run_id BIGINT NOT NULL
        REFERENCES data_sync_runs(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_instrument_alias_snapshot_date UNIQUE (
        provider,
        mic,
        business_date
    ),
    CONSTRAINT uq_instrument_alias_snapshot_path UNIQUE (
        provider,
        source_path
    ),
    CONSTRAINT uq_instrument_alias_snapshot_sync UNIQUE (sync_run_id),
    CONSTRAINT ck_instrument_alias_snapshot_provider CHECK (
        provider ~ '^[a-z0-9][a-z0-9._-]{1,99}$'
    ),
    CONSTRAINT ck_instrument_alias_snapshot_mic CHECK (
        mic ~ '^[A-Z0-9]{4}$'
    ),
    CONSTRAINT ck_instrument_alias_snapshot_time CHECK (
        business_date <= (
            received_at AT TIME ZONE 'Europe/Stockholm'
        )::DATE
    ),
    CONSTRAINT ck_instrument_alias_snapshot_tip_version CHECK (
        tip_version ~ '^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$'
    ),
    CONSTRAINT ck_instrument_alias_snapshot_path CHECK (
        source_path ~
            '^INET_Ref_Data/[0-9]{8}/Nordic_Equity_RefData[.]tip$'
    ),
    CONSTRAINT ck_instrument_alias_snapshot_checksums CHECK (
        file_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND reference_checksum_sha256 ~ '^[0-9a-f]{64}$'
        AND alias_checksum_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_instrument_alias_snapshot_content CHECK (
        content_encoding = 'gzip'
        AND OCTET_LENGTH(compressed_content) > 0
        AND uncompressed_bytes > 0
        AND uncompressed_bytes <= 20000000
    ),
    CONSTRAINT ck_instrument_alias_snapshot_count CHECK (
        instrument_count > 0
    )
);

CREATE INDEX IF NOT EXISTS idx_instrument_alias_snapshots_latest
    ON instrument_alias_snapshots(provider, mic, business_date DESC);

CREATE TABLE IF NOT EXISTS instrument_alias_snapshot_members (
    snapshot_id BIGINT NOT NULL
        REFERENCES instrument_alias_snapshots(id) ON DELETE RESTRICT,
    instrument_id BIGINT NOT NULL
        REFERENCES instruments(id) ON DELETE RESTRICT,
    isin CHAR(12) NOT NULL,
    provider_symbol VARCHAR(32) NOT NULL,
    trading_currency CHAR(3) NOT NULL,
    tradable_id VARCHAR(64) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    valid_from DATE,
    valid_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_id, instrument_id),
    CONSTRAINT uq_instrument_alias_snapshot_isin UNIQUE (
        snapshot_id,
        isin
    ),
    CONSTRAINT uq_instrument_alias_snapshot_symbol UNIQUE (
        snapshot_id,
        provider_symbol
    ),
    CONSTRAINT ck_instrument_alias_snapshot_member_isin CHECK (
        isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'
    ),
    CONSTRAINT ck_instrument_alias_snapshot_member_symbol CHECK (
        provider_symbol ~ '^[A-Z0-9][A-Z0-9 .-]{0,31}$'
    ),
    CONSTRAINT ck_instrument_alias_snapshot_member_currency CHECK (
        trading_currency ~ '^[A-Z]{3}$'
    ),
    CONSTRAINT ck_instrument_alias_snapshot_member_ids CHECK (
        tradable_id ~ '^[A-Za-z0-9._:/+-]{1,9}$'
        AND source_id ~ '^[A-Za-z0-9._:/+-]{1,32}$'
    ),
    CONSTRAINT ck_instrument_alias_snapshot_member_dates CHECK (
        valid_to IS NULL
        OR valid_from IS NULL
        OR valid_to >= valid_from
    )
);

CREATE INDEX IF NOT EXISTS idx_instrument_alias_snapshot_member_lookup
    ON instrument_alias_snapshot_members(
        instrument_id,
        snapshot_id
    );

CREATE TABLE IF NOT EXISTS instrument_alias_snapshot_heads (
    provider VARCHAR(100) NOT NULL,
    mic CHAR(4) NOT NULL,
    snapshot_id BIGINT NOT NULL UNIQUE
        REFERENCES instrument_alias_snapshots(id) ON DELETE RESTRICT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, mic),
    CONSTRAINT ck_instrument_alias_snapshot_head_provider CHECK (
        provider ~ '^[a-z0-9][a-z0-9._-]{1,99}$'
    ),
    CONSTRAINT ck_instrument_alias_snapshot_head_mic CHECK (
        mic ~ '^[A-Z0-9]{4}$'
    )
);

CREATE OR REPLACE FUNCTION validate_instrument_alias_snapshot_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    reference_snapshot reference_data_snapshots%ROWTYPE;
    frozen_count INTEGER;
    verified_count INTEGER;
BEGIN
    SELECT *
    INTO reference_snapshot
    FROM reference_data_snapshots
    WHERE id = NEW.reference_snapshot_id
    FOR SHARE;

    IF (
        reference_snapshot.id IS NULL
        OR reference_snapshot.provider != 'esma-firds'
        OR reference_snapshot.mic != NEW.mic
        OR reference_snapshot.instrument_count != NEW.instrument_count
        OR reference_snapshot.snapshot_date > NEW.business_date
        OR NEW.business_date - reference_snapshot.snapshot_date > 10
    ) THEN
        RAISE EXCEPTION
            'alias snapshot does not match a current frozen FIRDS universe';
    END IF;

    SELECT
        COUNT(*)::INTEGER,
        COUNT(*) FILTER (
            WHERE frozen_evidence_status = 'VERIFIED'
        )::INTEGER
    INTO frozen_count, verified_count
    FROM reference_snapshot_instruments
    WHERE snapshot_id = NEW.reference_snapshot_id;

    IF (
        frozen_count != NEW.instrument_count
        OR verified_count != NEW.instrument_count
    ) THEN
        RAISE EXCEPTION
            'alias snapshot requires complete verified FIRDS membership';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_instrument_alias_snapshot_insert
BEFORE INSERT ON instrument_alias_snapshots
FOR EACH ROW EXECUTE FUNCTION validate_instrument_alias_snapshot_insert();

CREATE OR REPLACE FUNCTION validate_instrument_alias_member_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    alias_snapshot instrument_alias_snapshots%ROWTYPE;
    reference_member reference_snapshot_instruments%ROWTYPE;
    existing_count INTEGER;
BEGIN
    SELECT *
    INTO alias_snapshot
    FROM instrument_alias_snapshots
    WHERE id = NEW.snapshot_id
    FOR UPDATE;

    SELECT *
    INTO reference_member
    FROM reference_snapshot_instruments
    WHERE
        snapshot_id = alias_snapshot.reference_snapshot_id
        AND instrument_id = NEW.instrument_id
        AND TRIM(isin) = TRIM(NEW.isin);

    IF (
        alias_snapshot.id IS NULL
        OR reference_member.instrument_id IS NULL
        OR reference_member.frozen_evidence_status != 'VERIFIED'
        OR TRIM(reference_member.currency) !=
            TRIM(NEW.trading_currency)
        OR TRIM(reference_member.mic) != TRIM(alias_snapshot.mic)
    ) THEN
        RAISE EXCEPTION
            'alias member does not match frozen FIRDS evidence';
    END IF;

    SELECT COUNT(*)::INTEGER
    INTO existing_count
    FROM instrument_alias_snapshot_members
    WHERE snapshot_id = NEW.snapshot_id;

    IF existing_count >= alias_snapshot.instrument_count THEN
        RAISE EXCEPTION
            'alias snapshot membership already matches frozen count';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_instrument_alias_snapshot_member_insert
BEFORE INSERT ON instrument_alias_snapshot_members
FOR EACH ROW EXECUTE FUNCTION validate_instrument_alias_member_insert();

CREATE OR REPLACE FUNCTION forbid_instrument_alias_evidence_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'instrument alias evidence is append-only';
END;
$$;

CREATE TRIGGER trg_instrument_alias_snapshot_immutable
BEFORE UPDATE OR DELETE ON instrument_alias_snapshots
FOR EACH ROW EXECUTE FUNCTION forbid_instrument_alias_evidence_change();

CREATE TRIGGER trg_instrument_alias_snapshot_member_immutable
BEFORE UPDATE OR DELETE ON instrument_alias_snapshot_members
FOR EACH ROW EXECUTE FUNCTION forbid_instrument_alias_evidence_change();

CREATE OR REPLACE FUNCTION validate_instrument_alias_head_write()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    candidate instrument_alias_snapshots%ROWTYPE;
    current_snapshot instrument_alias_snapshots%ROWTYPE;
    member_count INTEGER;
BEGIN
    SELECT *
    INTO candidate
    FROM instrument_alias_snapshots
    WHERE id = NEW.snapshot_id
    FOR SHARE;

    SELECT COUNT(*)::INTEGER
    INTO member_count
    FROM instrument_alias_snapshot_members
    WHERE snapshot_id = NEW.snapshot_id;

    IF (
        candidate.id IS NULL
        OR candidate.provider != NEW.provider
        OR candidate.mic != NEW.mic
        OR member_count != candidate.instrument_count
    ) THEN
        RAISE EXCEPTION
            'alias head requires one complete matching snapshot';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        SELECT *
        INTO current_snapshot
        FROM instrument_alias_snapshots
        WHERE id = OLD.snapshot_id;

        IF (
            candidate.business_date < current_snapshot.business_date
            OR (
                candidate.business_date = current_snapshot.business_date
                AND candidate.id != current_snapshot.id
            )
        ) THEN
            RAISE EXCEPTION
                'alias head cannot move backwards or fork one date';
        END IF;
        NEW.updated_at := NOW();
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_instrument_alias_snapshot_head_write
BEFORE INSERT OR UPDATE ON instrument_alias_snapshot_heads
FOR EACH ROW EXECUTE FUNCTION validate_instrument_alias_head_write();
