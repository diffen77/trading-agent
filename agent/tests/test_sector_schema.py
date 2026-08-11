from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "migrations" / "046_sector_classification.sql"


def test_sector_migration_records_source_and_verification_time():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "sector_source" in sql
    assert "sector_verified_at" in sql
    assert "companies_sector_provenance_pair" in sql
