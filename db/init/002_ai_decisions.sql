-- AI Decisions logging table
CREATE TABLE IF NOT EXISTS ai_decisions (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prompt_tokens INTEGER,
    response_tokens INTEGER,
    decisions_json TEXT,
    market_data_json TEXT,
    raw_response TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_decisions_timestamp ON ai_decisions(timestamp);
