-- Freeze exact instrument membership for every forward-reference snapshot.

CREATE TABLE IF NOT EXISTS reference_snapshot_instruments (
    snapshot_id BIGINT NOT NULL
        REFERENCES reference_data_snapshots(id) ON DELETE RESTRICT,
    instrument_id BIGINT NOT NULL
        REFERENCES instruments(id) ON DELETE RESTRICT,
    isin CHAR(12) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_id, instrument_id),
    CONSTRAINT uq_reference_snapshot_instrument_isin UNIQUE (
        snapshot_id,
        isin
    ),
    CONSTRAINT ck_reference_snapshot_instrument_isin CHECK (
        isin ~ '^[A-Z0-9]{12}$'
    )
);

CREATE INDEX IF NOT EXISTS idx_reference_snapshot_instrument
    ON reference_snapshot_instruments(instrument_id, snapshot_id);

CREATE OR REPLACE FUNCTION enforce_reference_snapshot_instrument_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    snapshot reference_data_snapshots%ROWTYPE;
    instrument instruments%ROWTYPE;
    existing_count INTEGER;
BEGIN
    SELECT *
    INTO snapshot
    FROM reference_data_snapshots
    WHERE id = NEW.snapshot_id
    FOR UPDATE;

    SELECT *
    INTO instrument
    FROM instruments
    WHERE id = NEW.instrument_id;

    IF snapshot.id IS NULL OR instrument.id IS NULL THEN
        RAISE EXCEPTION
            'snapshot instrument membership requires existing evidence';
    END IF;
    IF (
        TRIM(NEW.isin) != TRIM(instrument.isin)
        OR instrument.source != snapshot.provider
        OR instrument.mic != snapshot.mic
        OR instrument.reference_snapshot_date != snapshot.snapshot_date
        OR instrument.status != 'ACTIVE'
        OR NOT instrument.primary_listing
        OR instrument.instrument_type NOT IN (
            'COMMON_STOCK',
            'PREFERRED_STOCK',
            'DEPOSITARY_RECEIPT'
        )
    ) THEN
        RAISE EXCEPTION
            'snapshot instrument membership does not match reference evidence';
    END IF;

    SELECT COUNT(*)::INTEGER
    INTO existing_count
    FROM reference_snapshot_instruments
    WHERE snapshot_id = NEW.snapshot_id;

    IF existing_count >= snapshot.instrument_count THEN
        RAISE EXCEPTION
            'snapshot instrument membership already matches frozen count';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_reference_snapshot_instrument_insert
BEFORE INSERT ON reference_snapshot_instruments
FOR EACH ROW EXECUTE FUNCTION enforce_reference_snapshot_instrument_insert();

CREATE OR REPLACE FUNCTION forbid_reference_snapshot_instrument_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'snapshot instrument membership is append-only';
END;
$$;

CREATE TRIGGER trg_reference_snapshot_instrument_immutable
BEFORE UPDATE OR DELETE ON reference_snapshot_instruments
FOR EACH ROW EXECUTE FUNCTION forbid_reference_snapshot_instrument_change();

-- A repository upgraded from schema 17 can safely reconstruct only snapshots
-- whose current instrument rows still exactly match the archived snapshot.
INSERT INTO reference_snapshot_instruments (
    snapshot_id,
    instrument_id,
    isin
)
SELECT
    snapshot.id,
    instrument.id,
    instrument.isin
FROM reference_data_snapshots snapshot
JOIN instruments instrument
  ON instrument.source = snapshot.provider
 AND instrument.mic = snapshot.mic
 AND instrument.reference_snapshot_date = snapshot.snapshot_date
 AND instrument.status = 'ACTIVE'
 AND instrument.primary_listing
 AND instrument.instrument_type IN (
     'COMMON_STOCK',
     'PREFERRED_STOCK',
     'DEPOSITARY_RECEIPT'
 )
WHERE (
    SELECT COUNT(*)
    FROM instruments candidate
    WHERE candidate.source = snapshot.provider
      AND candidate.mic = snapshot.mic
      AND candidate.reference_snapshot_date = snapshot.snapshot_date
      AND candidate.status = 'ACTIVE'
      AND candidate.primary_listing
      AND candidate.instrument_type IN (
          'COMMON_STOCK',
          'PREFERRED_STOCK',
          'DEPOSITARY_RECEIPT'
      )
) = snapshot.instrument_count
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION enforce_benchmark_snapshot_membership()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    expected_count INTEGER;
    frozen_count INTEGER;
BEGIN
    IF (
        NEW.status IN ('APPROVED', 'RUNNING')
        AND NEW.status IS DISTINCT FROM OLD.status
    ) THEN
        SELECT snapshot.instrument_count
        INTO expected_count
        FROM reference_data_snapshots snapshot
        WHERE snapshot.id = NEW.reference_snapshot_id;

        SELECT COUNT(*)::INTEGER
        INTO frozen_count
        FROM reference_snapshot_instruments membership
        WHERE membership.snapshot_id = NEW.reference_snapshot_id;

        IF expected_count IS NULL OR frozen_count != expected_count THEN
            RAISE EXCEPTION
                'paper benchmark requires complete frozen snapshot membership';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_benchmark_snapshot_membership
BEFORE UPDATE OF status ON paper_benchmark_experiments
FOR EACH ROW EXECUTE FUNCTION enforce_benchmark_snapshot_membership();
