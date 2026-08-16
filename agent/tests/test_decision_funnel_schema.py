from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "migrations" / "050_decision_funnel.sql"


def test_decision_funnel_is_bounded_idempotent_and_append_only():
    sql = MIGRATION.read_text()

    assert "CREATE TABLE decision_funnel_events" in sql
    assert "event_key CHAR(64) NOT NULL UNIQUE" in sql
    assert "MODEL_ACTION" in sql
    assert "VALIDATION_REJECTED" in sql
    assert "ORDER_ATTEMPT" in sql
    assert "ORDER_FILLED" in sql
    assert "decision funnel evidence is append-only" in sql
