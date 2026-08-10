-- Bind delayed quote rows to the exact immutable source file they came from.

ALTER TABLE market_quotes
    ADD COLUMN IF NOT EXISTS data_file_id BIGINT
        REFERENCES market_data_files(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_market_quotes_data_file
    ON market_quotes(data_file_id)
    WHERE data_file_id IS NOT NULL;

CREATE OR REPLACE FUNCTION enforce_market_quote_file_provenance()
RETURNS TRIGGER AS $$
DECLARE
    source_file market_data_files%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.data_file_id IS DISTINCT FROM OLD.data_file_id THEN
        RAISE EXCEPTION 'market quote source file is immutable';
    END IF;

    IF NEW.data_file_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT *
    INTO source_file
    FROM market_data_files
    WHERE id = NEW.data_file_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'market quote source file does not exist';
    END IF;

    IF NEW.received_at IS DISTINCT FROM source_file.received_at THEN
        RAISE EXCEPTION 'market quote receipt time must match source file';
    END IF;

    IF NEW.source NOT LIKE source_file.provider || '%' THEN
        RAISE EXCEPTION 'market quote source must match source file provider';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_market_quote_file_provenance
    ON market_quotes;
CREATE TRIGGER trg_market_quote_file_provenance
BEFORE INSERT OR UPDATE OF data_file_id, received_at, source
ON market_quotes
FOR EACH ROW
EXECUTE FUNCTION enforce_market_quote_file_provenance();
