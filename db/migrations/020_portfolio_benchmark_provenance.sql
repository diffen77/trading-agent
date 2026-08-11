-- Bind portfolio valuations to the forward experiment active at capture time.

ALTER TABLE portfolio_history
    ADD COLUMN IF NOT EXISTS benchmark_experiment_id BIGINT
        REFERENCES paper_benchmark_experiments(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_portfolio_history_benchmark
    ON portfolio_history(
        benchmark_experiment_id,
        recorded_at DESC,
        id DESC
    )
    WHERE benchmark_experiment_id IS NOT NULL;
