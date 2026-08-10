-- Separate FIRDS notional currency from Nasdaq quote/trading currency.
--
-- The legacy `currency` columns remain as compatibility mirrors of the
-- FIRDS notional currency. Authoritative Nasdaq trading currency lives in
-- instrument_alias_snapshot_members.trading_currency.

ALTER TABLE instruments
    ADD COLUMN IF NOT EXISTS notional_currency CHAR(3);

UPDATE instruments
SET notional_currency = currency
WHERE notional_currency IS NULL;

ALTER TABLE instruments
    ALTER COLUMN notional_currency SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_instruments_notional_currency_v32'
    ) THEN
        ALTER TABLE instruments
            ADD CONSTRAINT ck_instruments_notional_currency_v32 CHECK (
                notional_currency ~ '^[A-Z]{3}$'
                AND notional_currency = currency
            );
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION normalize_instrument_notional_currency_v32()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.notional_currency IS NULL THEN
            NEW.notional_currency := NEW.currency;
        ELSIF NEW.currency IS NULL THEN
            NEW.currency := NEW.notional_currency;
        END IF;
    ELSE
        IF (
            NEW.currency IS DISTINCT FROM OLD.currency
            AND NEW.notional_currency IS NOT DISTINCT FROM
                OLD.notional_currency
        ) THEN
            NEW.notional_currency := NEW.currency;
        ELSIF (
            NEW.notional_currency IS DISTINCT FROM OLD.notional_currency
            AND NEW.currency IS NOT DISTINCT FROM OLD.currency
        ) THEN
            NEW.currency := NEW.notional_currency;
        END IF;
    END IF;

    IF (
        NEW.currency IS NULL
        OR NEW.notional_currency IS NULL
        OR NEW.currency IS DISTINCT FROM NEW.notional_currency
    ) THEN
        RAISE EXCEPTION
            'instrument notional currency conflicts with legacy mirror';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_instrument_notional_currency_v32
    ON instruments;
CREATE TRIGGER trg_instrument_notional_currency_v32
BEFORE INSERT OR UPDATE OF currency, notional_currency ON instruments
FOR EACH ROW
EXECUTE FUNCTION normalize_instrument_notional_currency_v32();

DROP TRIGGER IF EXISTS trg_reference_snapshot_instrument_immutable
    ON reference_snapshot_instruments;

ALTER TABLE reference_snapshot_instruments
    ADD COLUMN IF NOT EXISTS notional_currency CHAR(3);

UPDATE reference_snapshot_instruments
SET notional_currency = currency
WHERE notional_currency IS NULL;

ALTER TABLE reference_snapshot_instruments
    ALTER COLUMN notional_currency SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_reference_snapshot_notional_currency_v32'
    ) THEN
        ALTER TABLE reference_snapshot_instruments
            ADD CONSTRAINT ck_reference_snapshot_notional_currency_v32 CHECK (
                notional_currency ~ '^[A-Z]{3}$'
                AND notional_currency = currency
            );
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION populate_reference_snapshot_frozen_evidence()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    instrument instruments%ROWTYPE;
BEGIN
    SELECT *
    INTO instrument
    FROM instruments
    WHERE id = NEW.instrument_id;

    IF instrument.id IS NULL THEN
        RAISE EXCEPTION
            'snapshot instrument requires existing instrument evidence';
    END IF;

    NEW.symbol := instrument.symbol;
    NEW.name := instrument.name;
    NEW.mic := instrument.mic;
    NEW.currency := instrument.notional_currency;
    NEW.notional_currency := instrument.notional_currency;
    NEW.instrument_type := instrument.instrument_type;
    NEW.primary_listing := instrument.primary_listing;
    NEW.status := instrument.status;
    NEW.first_trade_date := instrument.first_trade_date;
    NEW.last_trade_date := instrument.last_trade_date;
    NEW.source := instrument.source;
    NEW.cfi_code := instrument.cfi_code;
    NEW.frozen_evidence_status := 'VERIFIED';
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_reference_snapshot_instrument_immutable
BEFORE UPDATE OR DELETE ON reference_snapshot_instruments
FOR EACH ROW
EXECUTE FUNCTION forbid_reference_snapshot_instrument_change();

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
