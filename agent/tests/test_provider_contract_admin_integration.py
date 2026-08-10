import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from src.data.database import Database
from src.provider_contract_admin import (
    ProviderContractAdmin,
    acceptance_from_files,
    proposal_from_file,
)


psycopg2 = pytest.importorskip("psycopg2")

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture()
def connection():
    assert TEST_DATABASE_URL
    connection = psycopg2.connect(TEST_DATABASE_URL)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE
                    market_data_provider_validations,
                    market_data_provider_contracts,
                    reference_snapshot_instruments,
                    reference_data_snapshots,
                    market_sessions,
                    data_sync_runs,
                    instruments
                RESTART IDENTITY CASCADE
                """
            )
            cursor.execute(
                """
                INSERT INTO data_sync_runs (
                    provider,
                    data_type,
                    status,
                    records_received,
                    records_inserted,
                    run_key,
                    finished_at
                )
                VALUES (
                    'test-reference',
                    'reference',
                    'SUCCEEDED',
                    1,
                    1,
                    'provider-admin-reference',
                    NOW()
                )
                RETURNING id
                """
            )
            sync_run_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO instruments (
                    isin,
                    symbol,
                    name,
                    mic,
                    currency,
                    instrument_type,
                    primary_listing,
                    status,
                    source,
                    reference_snapshot_date
                )
                VALUES (
                    'SE0000115446',
                    'VOLV B',
                    'Volvo B',
                    'XSTO',
                    'SEK',
                    'COMMON_STOCK',
                    TRUE,
                    'ACTIVE',
                    'test-reference',
                    DATE '2026-07-24'
                )
                RETURNING id
                """
            )
            instrument_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO reference_data_snapshots (
                    provider,
                    mic,
                    snapshot_date,
                    received_at,
                    universe_checksum_sha256,
                    file_count,
                    instrument_count,
                    sync_run_id
                )
                VALUES (
                    'test-reference',
                    'XSTO',
                    DATE '2026-07-24',
                    TIMESTAMPTZ '2026-07-24T18:00:00Z',
                    %s,
                    1,
                    1,
                    %s
                )
                RETURNING id
                """,
                ("a" * 64, sync_run_id),
            )
            snapshot_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO reference_snapshot_instruments (
                    snapshot_id,
                    instrument_id,
                    isin
                )
                VALUES (%s, %s, 'SE0000115446')
                """,
                (snapshot_id, instrument_id),
            )
            for session_date in (
                date(2026, 7, 20),
                date(2026, 7, 21),
                date(2026, 7, 22),
                date(2026, 7, 23),
                date(2026, 7, 24),
            ):
                cursor.execute(
                    """
                    INSERT INTO market_sessions (
                        mic,
                        session_date,
                        opens_at,
                        closes_at,
                        session_type,
                        status,
                        timezone_name,
                        source
                    )
                    VALUES (
                        'XSTO',
                        %s,
                        %s,
                        %s,
                        'REGULAR',
                        'OPEN',
                        'Europe/Stockholm',
                        'test-calendar'
                    )
                    """,
                    (
                        session_date,
                        datetime.combine(
                            session_date,
                            datetime.min.time(),
                            tzinfo=timezone.utc,
                        )
                        + timedelta(hours=7),
                        datetime.combine(
                            session_date,
                            datetime.min.time(),
                            tzinfo=timezone.utc,
                        )
                        + timedelta(hours=15, minutes=30),
                    ),
                )
        yield connection, snapshot_id
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE
                    market_data_provider_validations,
                    market_data_provider_contracts,
                    reference_snapshot_instruments,
                    reference_data_snapshots,
                    market_sessions,
                    data_sync_runs,
                    instruments
                RESTART IDENTITY CASCADE
                """
            )
        connection.close()


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_provider_admin_validates_index_without_equity_snapshot(
    connection,
    tmp_path,
    monkeypatch,
):
    _, _snapshot_id = connection
    now = datetime.now(timezone.utc)
    contract_key = "licensed-delayed-omxsgi-v31"
    contract_path = _write_json(
        tmp_path / "index-contract.json",
        {
            "contract_key": contract_key,
            "provider": "licensed-index",
            "product_name": "Licensed delayed OMXSGI",
            "data_type": "delayed-index-level",
            "mic": "XSTO",
            "delivery_mode": "DELAYED_15M",
            "transport": "AUTHORIZED_VENDOR",
            "nominal_delay_seconds": 900,
            "max_transport_lag_seconds": 30,
            "non_display_category": "NONE",
            "reference_symbols_included": True,
            "terms_url": "https://example.test/index-terms",
            "valid_from": "2026-07-01",
            "valid_until": "2027-07-01",
        },
    )
    acceptance_path = _write_json(
        tmp_path / "index-acceptance.json",
        {
            "contract_key": contract_key,
            "reference_snapshot_id": None,
            "reference_checksum_sha256": None,
            "validation_valid_until": (
                now + timedelta(days=30)
            ).isoformat(),
            "retention_policy": (
                "Index levels retained for internal paper trading."
            ),
            "raw_storage_allowed": True,
            "derived_storage_allowed": True,
            "transport_verified": True,
            "correction_handling_verified": True,
            "restart_verified": True,
            "gap_recovery_verified": True,
            "kill_switch_verified": True,
            "sessions": [
                {
                    "session_date": (
                        date(2026, 7, 20) + timedelta(days=offset)
                    ).isoformat(),
                    "expected_instruments": 1,
                    "product_covered_instruments": 1,
                    "symbol_mapped_instruments": 1,
                    "sample_file_count": 1,
                    "sample_quote_count": 500,
                    "max_observed_delivery_seconds": 925,
                    "evidence_checksum_sha256": f"{offset + 10:064x}",
                }
                for offset in range(5)
            ],
        },
    )
    terms_path = tmp_path / "index-terms.pdf"
    terms_path.write_bytes(b"exact reviewed index agreement")
    admin = ProviderContractAdmin(TEST_DATABASE_URL, clock=lambda: now)

    admin.create_draft(
        proposal_from_file(
            contract_json_path=str(contract_path),
            proposed_by="operator:test",
        )
    )
    result = admin.validate_contract(
        acceptance_from_files(
            acceptance_json_path=str(acceptance_path),
            terms_file_path=str(terms_path),
            validated_by="operator:test",
        )
    )

    assert result["inserted"] is True
    assert result["acceptance_session_count"] == 5
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    database = Database()
    try:
        contract = database.require_runtime_market_data_provider(
            contract_key=contract_key,
            provider="licensed-index",
            data_type="delayed-index-level",
            mic="XSTO",
            delivery_mode="DELAYED_15M",
            transport="AUTHORIZED_VENDOR",
            usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
            now=now,
            expected_instruments=1,
        )
        assert contract.contract_key == contract_key
    finally:
        database.engine.dispose()


def test_provider_admin_enforces_exact_append_only_acceptance(
    connection,
    tmp_path,
):
    db_connection, snapshot_id = connection
    now = datetime.now(timezone.utc)
    contract_key = "licensed-delayed-pretrade-xsto-v31"
    contract_path = _write_json(
        tmp_path / "contract.json",
        {
            "contract_key": contract_key,
            "provider": "licensed-provider",
            "product_name": "Licensed delayed XSTO Level 1",
            "data_type": "delayed-pre-trade-equity",
            "mic": "XSTO",
            "delivery_mode": "DELAYED_15M",
            "transport": "AUTHORIZED_VENDOR",
            "nominal_delay_seconds": 900,
            "max_transport_lag_seconds": 30,
            "non_display_category": "NONE",
            "reference_symbols_included": True,
            "terms_url": "https://example.test/provider-terms",
            "valid_from": "2026-07-01",
            "valid_until": "2027-07-01",
        },
    )
    sessions = []
    for offset in range(5):
        sessions.append(
            {
                "session_date": (
                    date(2026, 7, 20) + timedelta(days=offset)
                ).isoformat(),
                "expected_instruments": 1,
                "product_covered_instruments": 1,
                "symbol_mapped_instruments": 1,
                "sample_file_count": 390,
                "sample_quote_count": 50_000,
                "max_observed_delivery_seconds": 925,
                "evidence_checksum_sha256": f"{offset + 1:064x}",
            }
        )
    acceptance_path = _write_json(
        tmp_path / "acceptance.json",
        {
            "contract_key": contract_key,
            "reference_snapshot_id": snapshot_id,
            "reference_checksum_sha256": "a" * 64,
            "validation_valid_until": (
                now + timedelta(days=30)
            ).isoformat(),
            "retention_policy": (
                "Raw and derived data retained for internal paper trading."
            ),
            "raw_storage_allowed": True,
            "derived_storage_allowed": True,
            "transport_verified": True,
            "correction_handling_verified": True,
            "restart_verified": True,
            "gap_recovery_verified": True,
            "kill_switch_verified": True,
            "sessions": sessions,
        },
    )
    terms_path = tmp_path / "terms.pdf"
    terms_path.write_bytes(b"exact reviewed provider agreement")
    proposal = proposal_from_file(
        contract_json_path=str(contract_path),
        proposed_by="operator:test",
    )
    approval = acceptance_from_files(
        acceptance_json_path=str(acceptance_path),
        terms_file_path=str(terms_path),
        validated_by="operator:test",
    )
    clock_values = iter(
        (
            now,
            now + timedelta(seconds=1),
            now + timedelta(seconds=2),
        )
    )
    admin = ProviderContractAdmin(
        TEST_DATABASE_URL,
        clock=lambda: next(clock_values),
    )

    draft = admin.create_draft(proposal)
    repeated_draft = admin.create_draft(proposal)
    assert draft["inserted"] is True
    assert repeated_draft["inserted"] is False

    validation = admin.validate_contract(approval)
    repeated_validation = admin.validate_contract(approval)
    assert validation["inserted"] is True
    assert validation["acceptance_session_count"] == 5
    assert repeated_validation["inserted"] is False

    status = admin.list_contracts(contract_key)
    assert len(status) == 1
    assert status[0]["status"] == "VALIDATED"
    assert status[0]["governance_evidence_status"] == "VERIFIED"
    assert status[0]["proposed_by"] == "operator:test"
    assert status[0]["latest_validation_status"] == "PASSED"
    assert status[0]["acceptance_session_count"] == 5

    with db_connection.cursor() as cursor:
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="validation evidence is append-only",
        ):
            cursor.execute(
                """
                UPDATE market_data_provider_validations
                SET notes = 'rewritten'
                WHERE id = %s
                """,
                (validation["id"],),
            )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="contract evidence is immutable",
        ):
            cursor.execute(
                """
                UPDATE market_data_provider_contracts
                SET product_name = 'rewritten'
                WHERE contract_key = %s
                """,
                (contract_key,),
            )

    revoked = admin.revoke_contract(
        contract_key,
        revoked_by="operator:test",
        reason="Provider access intentionally disabled.",
    )
    assert revoked["status"] == "REVOKED"
    assert revoked["revoked_by"] == "operator:test"

    with db_connection.cursor() as cursor:
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="append-only",
        ):
            cursor.execute(
                """
                DELETE FROM market_data_provider_contracts
                WHERE contract_key = %s
                """,
                (contract_key,),
            )
