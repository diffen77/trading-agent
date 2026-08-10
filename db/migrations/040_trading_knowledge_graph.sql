-- Append-only operational evidence for the external trading knowledge graph.

CREATE TABLE knowledge_graph_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    run_key VARCHAR(100) NOT NULL UNIQUE,
    synced_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL,
    synced_counts JSONB NOT NULL,
    total_nodes INTEGER NOT NULL,
    total_relationships INTEGER NOT NULL,
    error_code VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_knowledge_graph_sync_key CHECK (
        run_key ~ '^knowledge:[0-9]{8}T[0-9]{12}Z$'
    ),
    CONSTRAINT ck_knowledge_graph_sync_status CHECK (
        status IN ('SUCCEEDED', 'FAILED')
        AND total_nodes >= 0
        AND total_relationships >= 0
        AND JSONB_TYPEOF(synced_counts) = 'object'
        AND (
            (status = 'SUCCEEDED' AND error_code IS NULL)
            OR (
                status = 'FAILED'
                AND error_code ~ '^[A-Z][A-Z0-9_]{1,49}$'
            )
        )
    )
);

CREATE INDEX idx_knowledge_graph_sync_latest
    ON knowledge_graph_sync_runs(synced_at DESC, id DESC);

CREATE OR REPLACE FUNCTION reject_knowledge_graph_sync_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'knowledge graph sync evidence is append-only';
END;
$$;

CREATE TRIGGER trg_knowledge_graph_sync_no_update
BEFORE UPDATE ON knowledge_graph_sync_runs
FOR EACH ROW
EXECUTE FUNCTION reject_knowledge_graph_sync_mutation();

CREATE TRIGGER trg_knowledge_graph_sync_no_delete
BEFORE DELETE ON knowledge_graph_sync_runs
FOR EACH ROW
EXECUTE FUNCTION reject_knowledge_graph_sync_mutation();
