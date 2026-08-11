-- Treat official XSTO half days as trading sessions in benchmark evidence.

CREATE OR REPLACE FUNCTION enforce_paper_benchmark_incident()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    experiment_status VARCHAR(20);
    experiment_started_at TIMESTAMPTZ;
BEGIN
    SELECT status, started_at
    INTO experiment_status, experiment_started_at
    FROM paper_benchmark_experiments
    WHERE id = NEW.experiment_id
    FOR UPDATE;
    IF experiment_status NOT IN ('RUNNING', 'PAUSED') THEN
        RAISE EXCEPTION
            'incident requires a running or paused experiment';
    END IF;
    IF NEW.detected_at < experiment_started_at THEN
        RAISE EXCEPTION
            'incident cannot predate the experiment start';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM market_sessions session
        WHERE session.mic = 'XSTO'
          AND session.session_date = NEW.session_date
          AND session.status IN ('OPEN', 'HALF_DAY')
    ) THEN
        RAISE EXCEPTION
            'incident requires an official XSTO trading session';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_paper_benchmark_observation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    experiment_started_at TIMESTAMPTZ;
    experiment_status VARCHAR(20);
    reference_instrument_count INTEGER;
    max_delivery_seconds INTEGER;
    quote_provider_contract_id BIGINT;
    benchmark_provider_contract_id BIGINT;
    session_close TIMESTAMPTZ;
    derived_fees DECIMAL(20, 8);
    derived_spread_cost DECIMAL(20, 8);
    derived_slippage_cost DECIMAL(20, 8);
BEGIN
    IF TG_OP != 'INSERT' THEN
        RAISE EXCEPTION 'paper benchmark observations are append-only';
    END IF;

    SELECT
        experiment.status,
        experiment.started_at,
        snapshot.instrument_count,
        experiment.quote_provider_contract_id,
        experiment.benchmark_provider_contract_id,
        GREATEST(
            quote_contract.nominal_delay_seconds
                + quote_contract.max_transport_lag_seconds,
            benchmark_contract.nominal_delay_seconds
                + benchmark_contract.max_transport_lag_seconds
        )
    INTO
        experiment_status,
        experiment_started_at,
        reference_instrument_count,
        quote_provider_contract_id,
        benchmark_provider_contract_id,
        max_delivery_seconds
    FROM paper_benchmark_experiments experiment
    JOIN reference_data_snapshots snapshot
      ON snapshot.id = experiment.reference_snapshot_id
    JOIN market_data_provider_contracts quote_contract
      ON quote_contract.id = experiment.quote_provider_contract_id
    JOIN market_data_provider_contracts benchmark_contract
      ON benchmark_contract.id
        = experiment.benchmark_provider_contract_id
    WHERE experiment.id = NEW.experiment_id
    FOR UPDATE OF experiment;

    IF experiment_status IS NULL THEN
        RAISE EXCEPTION 'paper benchmark experiment does not exist';
    END IF;
    IF experiment_status != 'RUNNING' THEN
        RAISE EXCEPTION
            'paper benchmark observation requires RUNNING status';
    END IF;
    IF NEW.session_date
        < (experiment_started_at AT TIME ZONE 'Europe/Stockholm')::DATE
    THEN
        RAISE EXCEPTION 'observation predates the experiment start';
    END IF;
    IF NEW.expected_quote_points != reference_instrument_count THEN
        RAISE EXCEPTION
            'expected quote points must match the frozen XSTO universe';
    END IF;
    IF (
        NOT paper_benchmark_provider_evidence_ready(
            quote_provider_contract_id,
            reference_instrument_count,
            ARRAY[
                'delayed-post-trade-equity',
                'realtime-equity-level-1'
            ]
        )
        OR NOT paper_benchmark_provider_evidence_ready(
            benchmark_provider_contract_id,
            1,
            ARRAY[
                'delayed-index-level',
                'realtime-index-level'
            ]
        )
    ) THEN
        RAISE EXCEPTION
            'daily observation requires current provider evidence';
    END IF;

    SELECT closes_at
    INTO session_close
    FROM market_sessions
    WHERE mic = 'XSTO'
      AND session_date = NEW.session_date
      AND status IN ('OPEN', 'HALF_DAY');

    IF session_close IS NULL THEN
        RAISE EXCEPTION
            'observation requires an official XSTO trading session';
    END IF;
    IF NEW.event_cutoff_at != session_close THEN
        RAISE EXCEPTION
            'daily observation cutoff must equal the official XSTO close';
    END IF;
    IF EXTRACT(
        EPOCH FROM (
            NEW.data_available_at - NEW.event_cutoff_at
        )
    ) > max_delivery_seconds THEN
        RAISE EXCEPTION
            'daily observation exceeds the licensed delivery window';
    END IF;

    SELECT
        COALESCE(SUM(fee_amount), 0),
        COALESCE(SUM(spread_cost), 0),
        COALESCE(SUM(slippage_cost), 0)
    INTO
        derived_fees,
        derived_spread_cost,
        derived_slippage_cost
    FROM trades
    WHERE benchmark_experiment_id = NEW.experiment_id
      AND (
          executed_at AT TIME ZONE 'Europe/Stockholm'
      )::DATE = NEW.session_date;
    IF (
        NEW.fees != derived_fees
        OR NEW.spread_cost != derived_spread_cost
        OR NEW.slippage_cost != derived_slippage_cost
    ) THEN
        RAISE EXCEPTION
            'daily costs must match the immutable trade ledger';
    END IF;

    RETURN NEW;
END;
$$;
