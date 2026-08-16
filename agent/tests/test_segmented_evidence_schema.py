from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "migrations" / "052_segmented_evidence_reports.sql"


def test_segmented_evidence_covers_policy_baselines_rotation_and_reports():
    sql = MIGRATION.read_text()
    assert "CREATE VIEW candidate_outcome_segments" in sql
    assert "market_regime" in sql
    assert "liquidity_bucket" in sql
    assert "spread_bucket" in sql
    assert "session_phase" in sql
    assert "CREATE VIEW deterministic_baseline_evidence" in sql
    assert "'CASH'::TEXT" in sql
    assert "'TOP_SIGNAL'" in sql
    assert "CREATE VIEW rotation_quality_evidence" in sql
    assert "CREATE TABLE position_opportunity_observations" in sql
    assert "CREATE TABLE agent_evidence_reports" in sql
    assert "record_agent_evidence_report" in sql
