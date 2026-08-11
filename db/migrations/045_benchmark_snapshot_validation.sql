-- Bind benchmark provider validation to the experiment's exact XSTO snapshot.

CREATE OR REPLACE FUNCTION enforce_paper_benchmark_snapshot_validation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    quote_validation_id BIGINT;
    quote_snapshot_id BIGINT;
    quote_snapshot_checksum CHAR(64);
    execution_validation_id BIGINT;
    execution_snapshot_id BIGINT;
    execution_snapshot_checksum CHAR(64);
BEGIN
    IF NOT (
        (OLD.status = 'DRAFT' AND NEW.status = 'APPROVED')
        OR (
            OLD.status IN ('APPROVED', 'PAUSED')
            AND NEW.status = 'RUNNING'
        )
    ) THEN
        RETURN NEW;
    END IF;

    SELECT
        validation.id,
        validation.reference_snapshot_id,
        validation.reference_checksum_sha256
    INTO
        quote_validation_id,
        quote_snapshot_id,
        quote_snapshot_checksum
    FROM market_data_provider_validations validation
    WHERE validation.contract_id = NEW.quote_provider_contract_id
    ORDER BY validation.validated_at DESC, validation.id DESC
    LIMIT 1;

    IF (
        quote_validation_id IS NOT NULL
        AND (
            quote_snapshot_id IS DISTINCT FROM NEW.reference_snapshot_id
            OR quote_snapshot_checksum
                IS DISTINCT FROM NEW.universe_checksum_sha256
        )
    ) THEN
        RAISE EXCEPTION
            'provider validation does not match frozen reference snapshot';
    END IF;

    IF NEW.execution_provider_contract_id
        IS DISTINCT FROM NEW.quote_provider_contract_id
    THEN
        SELECT
            validation.id,
            validation.reference_snapshot_id,
            validation.reference_checksum_sha256
        INTO
            execution_validation_id,
            execution_snapshot_id,
            execution_snapshot_checksum
        FROM market_data_provider_validations validation
        WHERE validation.contract_id = NEW.execution_provider_contract_id
        ORDER BY validation.validated_at DESC, validation.id DESC
        LIMIT 1;

        IF (
            execution_validation_id IS NOT NULL
            AND (
                execution_snapshot_id
                    IS DISTINCT FROM NEW.reference_snapshot_id
                OR execution_snapshot_checksum
                    IS DISTINCT FROM NEW.universe_checksum_sha256
            )
        ) THEN
            RAISE EXCEPTION
                'provider validation does not match frozen reference snapshot';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_benchmark_01_snapshot_validation
    ON paper_benchmark_experiments;
CREATE TRIGGER trg_paper_benchmark_01_snapshot_validation
BEFORE UPDATE ON paper_benchmark_experiments
FOR EACH ROW
EXECUTE FUNCTION enforce_paper_benchmark_snapshot_validation();
