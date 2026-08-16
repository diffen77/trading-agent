from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "migrations" / "049_knowledge_graph_backlog.sql"


def test_graph_backlog_migration_adds_bounded_append_only_evidence():
    sql = MIGRATION.read_text()

    assert "ADD COLUMN backlog_counts JSONB" in sql
    assert "ck_knowledge_graph_sync_backlog" in sql
    assert "backlog_counts ?& ARRAY" in sql
