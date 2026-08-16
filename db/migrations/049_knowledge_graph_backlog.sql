-- Bounded backlog evidence for the PostgreSQL-to-Neo4j trading graph.

ALTER TABLE knowledge_graph_sync_runs
ADD COLUMN backlog_counts JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE knowledge_graph_sync_runs
ADD CONSTRAINT ck_knowledge_graph_sync_backlog CHECK (
    JSONB_TYPEOF(backlog_counts) = 'object'
    AND (
        backlog_counts = '{}'::JSONB
        OR (
            backlog_counts ?& ARRAY['decisions', 'predictions', 'outcomes']
            AND backlog_counts - ARRAY[
                'decisions',
                'predictions',
                'outcomes'
            ] = '{}'::JSONB
            AND JSONB_TYPEOF(backlog_counts -> 'decisions') = 'number'
            AND JSONB_TYPEOF(backlog_counts -> 'predictions') = 'number'
            AND JSONB_TYPEOF(backlog_counts -> 'outcomes') = 'number'
            AND (backlog_counts ->> 'decisions')::INTEGER >= 0
            AND (backlog_counts ->> 'predictions')::INTEGER >= 0
            AND (backlog_counts ->> 'outcomes')::INTEGER >= 0
            AND MOD((backlog_counts ->> 'decisions')::NUMERIC, 1) = 0
            AND MOD((backlog_counts ->> 'predictions')::NUMERIC, 1) = 0
            AND MOD((backlog_counts ->> 'outcomes')::NUMERIC, 1) = 0
        )
    )
);
