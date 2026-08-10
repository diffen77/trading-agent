-- Bind benchmark starts and last-trade fills to exact immutable evidence.

ALTER TABLE market_data_files
    ADD COLUMN IF NOT EXISTS provider_contract_id BIGINT
        REFERENCES market_data_provider_contracts(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_market_data_file_provider_contract
    ON market_data_files(
        provider_contract_id,
        report_minute DESC,
        id DESC
    )
    WHERE provider_contract_id IS NOT NULL;

WITH unique_contract_match AS (
    SELECT
        source_file.id AS data_file_id,
        MIN(contract.id) AS provider_contract_id
    FROM market_data_files source_file
    JOIN market_data_provider_contracts contract
      ON contract.provider = source_file.provider
     AND contract.data_type = source_file.data_type
     AND (
        source_file.report_minute
            AT TIME ZONE 'Europe/Stockholm'
     )::DATE BETWEEN contract.valid_from AND contract.valid_until
    WHERE source_file.provider_contract_id IS NULL
    GROUP BY source_file.id
    HAVING COUNT(*) = 1
)
UPDATE market_data_files source_file
SET provider_contract_id = matched.provider_contract_id
FROM unique_contract_match matched
WHERE source_file.id = matched.data_file_id;

CREATE OR REPLACE FUNCTION enforce_market_data_file_contract()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    contract market_data_provider_contracts%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' AND ROW(
        OLD.provider,
        OLD.data_type,
        OLD.file_name,
        OLD.report_minute,
        OLD.received_at,
        OLD.source_url,
        OLD.checksum_sha256,
        OLD.content_encoding,
        OLD.compressed_content,
        OLD.uncompressed_bytes,
        OLD.provider_contract_id
    ) IS DISTINCT FROM ROW(
        NEW.provider,
        NEW.data_type,
        NEW.file_name,
        NEW.report_minute,
        NEW.received_at,
        NEW.source_url,
        NEW.checksum_sha256,
        NEW.content_encoding,
        NEW.compressed_content,
        NEW.uncompressed_bytes,
        NEW.provider_contract_id
    ) THEN
        RAISE EXCEPTION 'market data file evidence is immutable';
    END IF;

    IF NEW.provider_contract_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT *
    INTO contract
    FROM market_data_provider_contracts
    WHERE id = NEW.provider_contract_id;

    IF (
        contract.id IS NULL
        OR contract.provider != NEW.provider
        OR contract.data_type != NEW.data_type
        OR (
            NEW.report_minute AT TIME ZONE 'Europe/Stockholm'
        )::DATE NOT BETWEEN contract.valid_from AND contract.valid_until
    ) THEN
        RAISE EXCEPTION
            'market data file does not match its provider contract';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_market_data_file_contract
    ON market_data_files;
CREATE TRIGGER trg_market_data_file_contract
BEFORE INSERT OR UPDATE ON market_data_files
FOR EACH ROW EXECUTE FUNCTION enforce_market_data_file_contract();

CREATE OR REPLACE FUNCTION enforce_market_quote_file_provenance()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    source_file market_data_files%ROWTYPE;
    contract market_data_provider_contracts%ROWTYPE;
    instrument_mic CHAR(4);
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.data_file_id IS DISTINCT FROM OLD.data_file_id
    THEN
        RAISE EXCEPTION 'market quote source file is immutable';
    END IF;

    IF NEW.data_file_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT *
    INTO source_file
    FROM market_data_files
    WHERE id = NEW.data_file_id;

    IF source_file.id IS NULL THEN
        RAISE EXCEPTION 'market quote source file does not exist';
    END IF;
    IF NEW.received_at IS DISTINCT FROM source_file.received_at THEN
        RAISE EXCEPTION
            'market quote receipt time must match source file';
    END IF;
    IF NEW.source NOT LIKE source_file.provider || '%' THEN
        RAISE EXCEPTION
            'market quote source must match source file provider';
    END IF;

    IF source_file.provider_contract_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT *
    INTO contract
    FROM market_data_provider_contracts
    WHERE id = source_file.provider_contract_id;
    SELECT mic
    INTO instrument_mic
    FROM instruments
    WHERE id = NEW.instrument_id;

    IF (
        contract.id IS NULL
        OR instrument_mic IS NULL
        OR instrument_mic != contract.mic
        OR contract.provider != source_file.provider
        OR contract.data_type != source_file.data_type
        OR NEW.source NOT LIKE contract.provider || '%'
        OR (
            NEW.event_time AT TIME ZONE 'Europe/Stockholm'
        )::DATE NOT BETWEEN contract.valid_from AND contract.valid_until
    ) THEN
        RAISE EXCEPTION
            'market quote does not match its exact provider contract';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_market_quote_file_provenance
    ON market_quotes;
CREATE TRIGGER trg_market_quote_file_provenance
BEFORE INSERT OR UPDATE OF
    data_file_id,
    received_at,
    source,
    event_time,
    instrument_id
ON market_quotes
FOR EACH ROW
EXECUTE FUNCTION enforce_market_quote_file_provenance();

CREATE OR REPLACE FUNCTION protect_file_bound_market_quote()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.data_file_id IS NOT NULL THEN
            RAISE EXCEPTION
                'file-bound market quote evidence is append-only';
        END IF;
        RETURN OLD;
    END IF;

    IF (
        OLD.data_file_id IS NOT NULL
        OR NEW.data_file_id IS NOT NULL
    ) AND ROW(
        OLD.instrument_id,
        OLD.event_time,
        OLD.received_at,
        OLD.source,
        OLD.last_price,
        OLD.bid_price,
        OLD.ask_price,
        OLD.volume,
        OLD.currency,
        OLD.raw_payload,
        OLD.created_at,
        OLD.data_file_id
    ) IS DISTINCT FROM ROW(
        NEW.instrument_id,
        NEW.event_time,
        NEW.received_at,
        NEW.source,
        NEW.last_price,
        NEW.bid_price,
        NEW.ask_price,
        NEW.volume,
        NEW.currency,
        NEW.raw_payload,
        NEW.created_at,
        NEW.data_file_id
    ) THEN
        RAISE EXCEPTION
            'file-bound market quote evidence is append-only';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_protect_file_bound_market_quote
    ON market_quotes;
CREATE TRIGGER trg_protect_file_bound_market_quote
BEFORE UPDATE OR DELETE ON market_quotes
FOR EACH ROW
EXECUTE FUNCTION protect_file_bound_market_quote();

ALTER TABLE paper_benchmark_experiments
    ADD COLUMN IF NOT EXISTS benchmark_start_level_id BIGINT
        REFERENCES market_index_levels(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_paper_benchmark_start_level
    ON paper_benchmark_experiments(benchmark_start_level_id)
    WHERE benchmark_start_level_id IS NOT NULL;

WITH exact_start_match AS (
    SELECT
        experiment.id AS experiment_id,
        MIN(level.id) AS benchmark_start_level_id
    FROM paper_benchmark_experiments experiment
    JOIN market_index_levels level
      ON level.provider_contract_id =
            experiment.benchmark_provider_contract_id
     AND level.symbol = experiment.benchmark_symbol
     AND level.mic = 'XSTO'
     AND level.level = experiment.benchmark_start_level
     AND level.event_time =
            experiment.benchmark_start_event_time
     AND level.received_at =
            experiment.benchmark_start_available_at
     AND level.source_checksum_sha256 =
            experiment.benchmark_start_checksum_sha256
    WHERE experiment.started_at IS NOT NULL
      AND experiment.benchmark_start_level_id IS NULL
    GROUP BY experiment.id
    HAVING COUNT(*) = 1
)
UPDATE paper_benchmark_experiments experiment
SET benchmark_start_level_id =
        matched.benchmark_start_level_id
FROM exact_start_match matched
WHERE experiment.id = matched.experiment_id;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM paper_benchmark_experiments experiment
        WHERE experiment.started_at IS NOT NULL
          AND experiment.benchmark_start_level_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'schema 027 requires exact benchmark start evidence for every started experiment';
    END IF;
END
$$;

ALTER TABLE paper_benchmark_experiments
    DROP CONSTRAINT IF EXISTS ck_paper_benchmark_start_level_reference;

DO $$
BEGIN
    ALTER TABLE paper_benchmark_experiments
        ADD CONSTRAINT ck_paper_benchmark_start_level_reference
        CHECK (
            (
                started_at IS NULL
                AND benchmark_start_level_id IS NULL
            )
            OR (
                started_at IS NOT NULL
                AND benchmark_start_level_id IS NOT NULL
            )
        );
END
$$;

CREATE OR REPLACE FUNCTION require_paper_benchmark_draft_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status != 'DRAFT' THEN
        RAISE EXCEPTION
            'paper benchmark must be created as a draft';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_benchmark_00_require_draft_insert
    ON paper_benchmark_experiments;
CREATE TRIGGER trg_paper_benchmark_00_require_draft_insert
BEFORE INSERT ON paper_benchmark_experiments
FOR EACH ROW
EXECUTE FUNCTION require_paper_benchmark_draft_insert();

CREATE OR REPLACE FUNCTION protect_referenced_reference_snapshot()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pre_trade_batches batch
        WHERE batch.reference_snapshot_id = OLD.id
    ) OR EXISTS (
        SELECT 1
        FROM paper_benchmark_experiments experiment
        WHERE experiment.reference_snapshot_id = OLD.id
    ) THEN
        RAISE EXCEPTION
            'referenced reference snapshot is immutable';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION protect_referenced_provider_contract()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    is_referenced BOOLEAN;
BEGIN
    SELECT (
        EXISTS (
            SELECT 1
            FROM pre_trade_batches batch
            WHERE batch.provider_contract_id = OLD.id
        )
        OR EXISTS (
            SELECT 1
            FROM market_data_files source_file
            WHERE source_file.provider_contract_id = OLD.id
        )
        OR EXISTS (
            SELECT 1
            FROM market_index_levels level
            WHERE level.provider_contract_id = OLD.id
        )
        OR EXISTS (
            SELECT 1
            FROM paper_benchmark_experiments experiment
            WHERE experiment.quote_provider_contract_id = OLD.id
               OR experiment.execution_provider_contract_id = OLD.id
               OR experiment.benchmark_provider_contract_id = OLD.id
        )
    )
    INTO is_referenced;

    IF NOT is_referenced THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'referenced provider contract cannot be deleted';
    END IF;

    IF ROW(
        OLD.contract_key,
        OLD.provider,
        OLD.product_name,
        OLD.data_type,
        OLD.mic,
        OLD.delivery_mode,
        OLD.transport,
        OLD.nominal_delay_seconds,
        OLD.max_transport_lag_seconds,
        OLD.usage_scope,
        OLD.non_display_category,
        OLD.external_distribution,
        OLD.reference_symbols_included,
        OLD.terms_url,
        OLD.valid_from,
        OLD.valid_until,
        OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.contract_key,
        NEW.provider,
        NEW.product_name,
        NEW.data_type,
        NEW.mic,
        NEW.delivery_mode,
        NEW.transport,
        NEW.nominal_delay_seconds,
        NEW.max_transport_lag_seconds,
        NEW.usage_scope,
        NEW.non_display_category,
        NEW.external_distribution,
        NEW.reference_symbols_included,
        NEW.terms_url,
        NEW.valid_from,
        NEW.valid_until,
        NEW.created_at
    ) THEN
        RAISE EXCEPTION
            'referenced provider contract evidence is immutable';
    END IF;

    IF (
        ROW(
            OLD.legal_reviewed_at,
            OLD.transport_verified_at
        ) IS DISTINCT FROM ROW(
            NEW.legal_reviewed_at,
            NEW.transport_verified_at
        )
        AND NOT (
            OLD.status = 'DRAFT'
            AND NEW.status = 'VALIDATED'
        )
    ) THEN
        RAISE EXCEPTION
            'referenced provider contract evidence is immutable';
    END IF;

    IF OLD.status IS DISTINCT FROM NEW.status AND NOT (
        (
            OLD.status = 'DRAFT'
            AND NEW.status = 'VALIDATED'
        )
        OR (
            OLD.status = 'VALIDATED'
            AND NEW.status = 'REVOKED'
        )
    ) THEN
        RAISE EXCEPTION
            'referenced provider contract status transition is invalid';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_paper_benchmark_evidence_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF ROW(
        OLD.strategy_version_id,
        OLD.strategy_config_hash,
        OLD.release_sha,
        OLD.release_manifest_sha256,
        OLD.agent_image_digest_sha256,
        OLD.model_backend,
        OLD.model_name,
        OLD.model_evidence_sha256,
        OLD.reference_snapshot_id,
        OLD.universe_checksum_sha256,
        OLD.quote_provider_contract_id,
        OLD.execution_price_source,
        OLD.execution_provider_contract_id,
        OLD.benchmark_provider_contract_id,
        OLD.benchmark_symbol,
        OLD.benchmark_kind,
        OLD.benchmark_source_url,
        OLD.benchmark_terms_url,
        OLD.initial_capital,
        OLD.fee_bps,
        OLD.spread_bps,
        OLD.slippage_bps,
        OLD.min_trading_sessions,
        OLD.min_closed_trades,
        OLD.max_drawdown_pct,
        OLD.min_data_coverage_pct,
        OLD.preregistration_payload,
        OLD.preregistration_canonical_json,
        OLD.preregistration_hash,
        OLD.proposed_by,
        OLD.proposed_at
    ) IS DISTINCT FROM ROW(
        NEW.strategy_version_id,
        NEW.strategy_config_hash,
        NEW.release_sha,
        NEW.release_manifest_sha256,
        NEW.agent_image_digest_sha256,
        NEW.model_backend,
        NEW.model_name,
        NEW.model_evidence_sha256,
        NEW.reference_snapshot_id,
        NEW.universe_checksum_sha256,
        NEW.quote_provider_contract_id,
        NEW.execution_price_source,
        NEW.execution_provider_contract_id,
        NEW.benchmark_provider_contract_id,
        NEW.benchmark_symbol,
        NEW.benchmark_kind,
        NEW.benchmark_source_url,
        NEW.benchmark_terms_url,
        NEW.initial_capital,
        NEW.fee_bps,
        NEW.spread_bps,
        NEW.slippage_bps,
        NEW.min_trading_sessions,
        NEW.min_closed_trades,
        NEW.max_drawdown_pct,
        NEW.min_data_coverage_pct,
        NEW.preregistration_payload,
        NEW.preregistration_canonical_json,
        NEW.preregistration_hash,
        NEW.proposed_by,
        NEW.proposed_at
    ) THEN
        RAISE EXCEPTION
            'paper benchmark preregistration is immutable from creation';
    END IF;

    IF OLD.started_at IS NOT NULL AND ROW(
        OLD.started_by,
        OLD.started_at,
        OLD.benchmark_start_level_id,
        OLD.benchmark_start_level,
        OLD.benchmark_start_event_time,
        OLD.benchmark_start_available_at,
        OLD.benchmark_start_checksum_sha256
    ) IS DISTINCT FROM ROW(
        NEW.started_by,
        NEW.started_at,
        NEW.benchmark_start_level_id,
        NEW.benchmark_start_level,
        NEW.benchmark_start_event_time,
        NEW.benchmark_start_available_at,
        NEW.benchmark_start_checksum_sha256
    ) THEN
        RAISE EXCEPTION
            'paper benchmark start evidence is immutable';
    END IF;

    IF (
        NEW.status = 'RUNNING'
        AND OLD.status IN ('APPROVED', 'PAUSED')
        AND (
            NEW.benchmark_start_level_id IS NULL
            OR NOT EXISTS (
                SELECT 1
                FROM market_index_levels level
                WHERE level.id = NEW.benchmark_start_level_id
                  AND level.provider_contract_id =
                        NEW.benchmark_provider_contract_id
                  AND level.symbol = NEW.benchmark_symbol
                  AND level.mic = 'XSTO'
                  AND level.level = NEW.benchmark_start_level
                  AND level.event_time =
                        NEW.benchmark_start_event_time
                  AND level.received_at =
                        NEW.benchmark_start_available_at
                  AND level.source_checksum_sha256 =
                        NEW.benchmark_start_checksum_sha256
                  AND level.received_at <= NEW.started_at
            )
        )
    ) THEN
        RAISE EXCEPTION
            'paper benchmark start requires exact authorized index evidence';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_benchmark_00_evidence_integrity
    ON paper_benchmark_experiments;
CREATE TRIGGER trg_paper_benchmark_00_evidence_integrity
BEFORE UPDATE ON paper_benchmark_experiments
FOR EACH ROW
EXECUTE FUNCTION enforce_paper_benchmark_evidence_integrity();

CREATE OR REPLACE FUNCTION validate_benchmark_quote_evidence()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.benchmark_experiment_id IS NULL
        OR NEW.source_quote_id IS NULL
    THEN
        RETURN NEW;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM paper_benchmark_experiments experiment
        JOIN market_quotes quote
          ON quote.id = NEW.source_quote_id
        JOIN market_data_files source_file
          ON source_file.id = quote.data_file_id
         AND source_file.provider_contract_id =
                experiment.execution_provider_contract_id
        JOIN reference_snapshot_instruments membership
          ON membership.snapshot_id =
                experiment.reference_snapshot_id
         AND membership.instrument_id = quote.instrument_id
        WHERE experiment.id = NEW.benchmark_experiment_id
          AND experiment.status = 'RUNNING'
          AND experiment.execution_price_source =
                'LAST_TRADE_PLUS_BPS'
    ) THEN
        RAISE EXCEPTION
            'benchmark trade requires quote from exact contract and snapshot';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_benchmark_quote_evidence
    ON trades;
CREATE TRIGGER trg_validate_benchmark_quote_evidence
BEFORE INSERT ON trades
FOR EACH ROW
EXECUTE FUNCTION validate_benchmark_quote_evidence();

CREATE OR REPLACE FUNCTION protect_benchmark_trade_execution()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.benchmark_experiment_id IS NOT NULL THEN
            RAISE EXCEPTION
                'benchmark trade execution evidence is append-only';
        END IF;
        RETURN OLD;
    END IF;

    IF (
        OLD.benchmark_experiment_id IS NOT NULL
        OR NEW.benchmark_experiment_id IS NOT NULL
    ) AND ROW(
        OLD.benchmark_experiment_id,
        OLD.ticker,
        OLD.action,
        OLD.shares,
        OLD.quote_price,
        OLD.price,
        OLD.total_value,
        OLD.fee_amount,
        OLD.spread_cost,
        OLD.slippage_cost,
        OLD.net_cash_effect,
        OLD.executed_at,
        OLD.source_quote_id,
        OLD.source_book_state_id,
        OLD.strategy_version,
        OLD.idempotency_key,
        OLD.request_fingerprint,
        OLD.decision_id,
        OLD.decision_origin,
        OLD.closes_position,
        OLD.entry_trade_id,
        OLD.reasoning,
        OLD.confidence,
        OLD.hypothesis,
        OLD.macro_context,
        OLD.target_price,
        OLD.target_pct
    ) IS DISTINCT FROM ROW(
        NEW.benchmark_experiment_id,
        NEW.ticker,
        NEW.action,
        NEW.shares,
        NEW.quote_price,
        NEW.price,
        NEW.total_value,
        NEW.fee_amount,
        NEW.spread_cost,
        NEW.slippage_cost,
        NEW.net_cash_effect,
        NEW.executed_at,
        NEW.source_quote_id,
        NEW.source_book_state_id,
        NEW.strategy_version,
        NEW.idempotency_key,
        NEW.request_fingerprint,
        NEW.decision_id,
        NEW.decision_origin,
        NEW.closes_position,
        NEW.entry_trade_id,
        NEW.reasoning,
        NEW.confidence,
        NEW.hypothesis,
        NEW.macro_context,
        NEW.target_price,
        NEW.target_pct
    ) THEN
        RAISE EXCEPTION
            'benchmark trade execution evidence is append-only';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_protect_benchmark_trade_execution
    ON trades;
CREATE TRIGGER trg_protect_benchmark_trade_execution
BEFORE UPDATE OR DELETE ON trades
FOR EACH ROW
EXECUTE FUNCTION protect_benchmark_trade_execution();
