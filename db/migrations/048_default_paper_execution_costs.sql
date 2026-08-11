CREATE TABLE paper_execution_cost_policies (
    id BIGSERIAL PRIMARY KEY,
    model_key VARCHAR(80) NOT NULL UNIQUE,
    execution_price_source VARCHAR(40) NOT NULL,
    fee_bps DECIMAL(10, 4) NOT NULL,
    spread_bps DECIMAL(10, 4) NOT NULL,
    slippage_bps DECIMAL(10, 4) NOT NULL,
    minimum_fee DECIMAL(20, 8) NOT NULL,
    fee_source_url TEXT NOT NULL,
    fee_verified_at TIMESTAMPTZ NOT NULL,
    slippage_assumption TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (execution_price_source IN (
        'TOP_OF_BOOK_PLUS_SLIPPAGE',
        'LAST_TRADE_PLUS_BPS'
    )),
    CHECK (fee_bps BETWEEN 0 AND 1000),
    CHECK (spread_bps BETWEEN 0 AND 1000),
    CHECK (slippage_bps BETWEEN 0 AND 1000),
    CHECK (minimum_fee BETWEEN 0 AND 10000),
    CHECK (
        execution_price_source != 'TOP_OF_BOOK_PLUS_SLIPPAGE'
        OR spread_bps = 0
    )
);

CREATE UNIQUE INDEX uq_active_paper_execution_cost_policy
ON paper_execution_cost_policies (active)
WHERE active = TRUE;

INSERT INTO paper_execution_cost_policies (
    model_key,
    execution_price_source,
    fee_bps,
    spread_bps,
    slippage_bps,
    minimum_fee,
    fee_source_url,
    fee_verified_at,
    slippage_assumption,
    active
)
VALUES (
    'se-retail-public-top-book-v1',
    'TOP_OF_BOOK_PLUS_SLIPPAGE',
    25,
    0,
    5,
    1,
    'https://www.nordnet.se/kundservice/prislista',
    '2026-08-11T00:00:00Z',
    'Conservative deterministic paper assumption; actual spread is taken from the sealed executable book.',
    TRUE
);

ALTER TABLE trades
ADD COLUMN paper_execution_cost_policy_id BIGINT
    REFERENCES paper_execution_cost_policies(id) ON DELETE RESTRICT;

ALTER TABLE trades
ADD CONSTRAINT ck_trade_execution_cost_contract
CHECK (
    benchmark_experiment_id IS NULL
    OR paper_execution_cost_policy_id IS NULL
);

CREATE INDEX idx_trades_paper_execution_cost_policy
ON trades (paper_execution_cost_policy_id, executed_at DESC);

CREATE OR REPLACE FUNCTION apply_default_paper_execution_costs()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    blocking_experiment_status VARCHAR(20);
    policy_id BIGINT;
    policy_source VARCHAR(40);
    policy_fee_bps DECIMAL(10, 4);
    policy_spread_bps DECIMAL(10, 4);
    policy_slippage_bps DECIMAL(10, 4);
    policy_minimum_fee DECIMAL(20, 8);
    source_bid DECIMAL(20, 8);
    source_ask DECIMAL(20, 8);
    source_price DECIMAL(20, 8);
    source_ticker VARCHAR(20);
    expected_quote_price DECIMAL(20, 8);
    expected_execution_price DECIMAL(20, 8);
    expected_total_value DECIMAL(20, 8);
    expected_fee_amount DECIMAL(20, 8);
    expected_spread_cost DECIMAL(20, 8);
    expected_slippage_cost DECIMAL(20, 8);
    expected_net_cash_effect DECIMAL(20, 8);
    direction DECIMAL(2, 0);
BEGIN
    SELECT status
    INTO blocking_experiment_status
    FROM paper_benchmark_experiments
    WHERE status IN ('RUNNING', 'APPROVED', 'PAUSED')
    ORDER BY id
    LIMIT 1;

    IF blocking_experiment_status IS NOT NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.benchmark_experiment_id IS NOT NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.source_book_state_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT
        id,
        execution_price_source,
        fee_bps,
        spread_bps,
        slippage_bps,
        minimum_fee
    INTO
        policy_id,
        policy_source,
        policy_fee_bps,
        policy_spread_bps,
        policy_slippage_bps,
        policy_minimum_fee
    FROM paper_execution_cost_policies
    WHERE active = TRUE;

    IF policy_id IS NULL THEN
        RAISE EXCEPTION 'active paper execution cost policy is missing';
    END IF;
    IF (
        NEW.paper_execution_cost_policy_id IS NOT NULL
        AND NEW.paper_execution_cost_policy_id != policy_id
    ) THEN
        RAISE EXCEPTION 'trade references the wrong paper execution cost policy';
    END IF;

    direction = CASE WHEN NEW.action = 'BUY' THEN 1 ELSE -1 END;
    IF NEW.source_book_state_id IS NOT NULL THEN
        IF policy_source != 'TOP_OF_BOOK_PLUS_SLIPPAGE' THEN
            RAISE EXCEPTION 'paper cost policy requires last-trade evidence';
        END IF;
        SELECT
            state.bid_price,
            state.ask_price,
            company.ticker
        INTO source_bid, source_ask, source_ticker
        FROM pre_trade_book_states state
        JOIN instruments instrument ON instrument.id = state.instrument_id
        JOIN companies company ON company.instrument_id = instrument.id
        WHERE state.id = NEW.source_book_state_id;

        IF (
            source_bid IS NULL
            OR source_ask IS NULL
            OR source_bid > source_ask
            OR source_ticker != NEW.ticker
        ) THEN
            RAISE EXCEPTION 'paper cost policy requires matching two-sided book evidence';
        END IF;
        expected_quote_price = ROUND((source_bid + source_ask) / 2, 8);
        source_price = CASE
            WHEN NEW.action = 'BUY' THEN source_ask
            ELSE source_bid
        END;
        expected_execution_price = ROUND(
            source_price * (
                1 + direction * policy_slippage_bps / 10000
            ),
            8
        );
        expected_spread_cost = ROUND(
            ABS(source_price - expected_quote_price) * NEW.shares,
            2
        );
    ELSE
        IF NEW.source_quote_id IS NOT NULL THEN
            SELECT quote.last_price, company.ticker
            INTO source_price, source_ticker
            FROM market_quotes quote
            JOIN instruments instrument ON instrument.id = quote.instrument_id
            JOIN companies company ON company.instrument_id = instrument.id
            WHERE quote.id = NEW.source_quote_id;
            IF source_price IS NULL OR source_ticker != NEW.ticker THEN
                RAISE EXCEPTION 'paper cost policy requires matching quote evidence';
            END IF;
        ELSE
            source_price = NEW.price;
        END IF;
        expected_quote_price = ROUND(source_price, 8);
        expected_execution_price = ROUND(
            source_price * (
                1 + direction * (
                    policy_spread_bps / 2 + policy_slippage_bps
                ) / 10000
            ),
            8
        );
        expected_spread_cost = ROUND(
            source_price * NEW.shares * policy_spread_bps / 20000,
            2
        );
    END IF;

    expected_total_value = ROUND(expected_execution_price * NEW.shares, 2);
    expected_fee_amount = GREATEST(
        ROUND(expected_total_value * policy_fee_bps / 10000, 2),
        ROUND(policy_minimum_fee, 2)
    );
    expected_slippage_cost = ROUND(
        source_price * NEW.shares * policy_slippage_bps / 10000,
        2
    );
    expected_net_cash_effect = CASE
        WHEN NEW.action = 'BUY'
            THEN expected_total_value + expected_fee_amount
        ELSE expected_total_value - expected_fee_amount
    END;

    IF NEW.paper_execution_cost_policy_id IS NOT NULL AND (
        NEW.quote_price != expected_quote_price
        OR NEW.price != expected_execution_price
        OR NEW.total_value != expected_total_value
        OR NEW.fee_amount != expected_fee_amount
        OR NEW.spread_cost != expected_spread_cost
        OR NEW.slippage_cost != expected_slippage_cost
        OR NEW.net_cash_effect != expected_net_cash_effect
    ) THEN
        RAISE EXCEPTION 'trade execution costs do not match the active paper policy';
    END IF;

    NEW.paper_execution_cost_policy_id = policy_id;
    NEW.quote_price = expected_quote_price;
    NEW.price = expected_execution_price;
    NEW.total_value = expected_total_value;
    NEW.fee_amount = expected_fee_amount;
    NEW.spread_cost = expected_spread_cost;
    NEW.slippage_cost = expected_slippage_cost;
    NEW.net_cash_effect = expected_net_cash_effect;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_tag_00_apply_default_paper_execution_costs
BEFORE INSERT ON trades
FOR EACH ROW
EXECUTE FUNCTION apply_default_paper_execution_costs();

DROP TRIGGER trg_tag_paper_benchmark_trade ON trades;
CREATE TRIGGER trg_tag_paper_benchmark_trade
BEFORE INSERT ON trades
FOR EACH ROW
WHEN (NEW.paper_execution_cost_policy_id IS NULL)
EXECUTE FUNCTION tag_paper_benchmark_trade();

CREATE OR REPLACE FUNCTION protect_paper_execution_cost_evidence()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.paper_execution_cost_policy_id IS NOT NULL THEN
            RAISE EXCEPTION 'paper execution cost evidence is append-only';
        END IF;
        RETURN OLD;
    END IF;
    IF (
        OLD.paper_execution_cost_policy_id IS NOT NULL
        OR NEW.paper_execution_cost_policy_id IS NOT NULL
    ) AND ROW(
        OLD.paper_execution_cost_policy_id,
        OLD.quote_price,
        OLD.price,
        OLD.total_value,
        OLD.fee_amount,
        OLD.spread_cost,
        OLD.slippage_cost,
        OLD.net_cash_effect,
        OLD.source_quote_id,
        OLD.source_book_state_id
    ) IS DISTINCT FROM ROW(
        NEW.paper_execution_cost_policy_id,
        NEW.quote_price,
        NEW.price,
        NEW.total_value,
        NEW.fee_amount,
        NEW.spread_cost,
        NEW.slippage_cost,
        NEW.net_cash_effect,
        NEW.source_quote_id,
        NEW.source_book_state_id
    ) THEN
        RAISE EXCEPTION 'paper execution cost evidence is append-only';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_protect_paper_execution_cost_evidence
BEFORE UPDATE OR DELETE ON trades
FOR EACH ROW
EXECUTE FUNCTION protect_paper_execution_cost_evidence();
