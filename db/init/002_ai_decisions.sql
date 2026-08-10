-- AI Decisions logging table
CREATE TABLE IF NOT EXISTS ai_decisions (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prompt_tokens INTEGER,
    response_tokens INTEGER,
    decisions_json TEXT,
    market_data_json TEXT,
    raw_response TEXT,
    model_backend VARCHAR(30) NOT NULL DEFAULT 'legacy',
    model_name VARCHAR(200) NOT NULL DEFAULT 'unknown',
    model_provider VARCHAR(80),
    reasoning_effort VARCHAR(10),
    response_model VARCHAR(200),
    response_id VARCHAR(200)
);

CREATE INDEX IF NOT EXISTS idx_ai_decisions_timestamp ON ai_decisions(timestamp);
