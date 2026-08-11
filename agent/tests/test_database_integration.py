import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st


sqlalchemy = pytest.importorskip("sqlalchemy")
psycopg2 = pytest.importorskip("psycopg2")

from src.data.database import Database
from src.data.market_data import (
    IndexLevelRecord,
    InstrumentRecord,
    InstrumentUniverseSnapshot,
    MarketDataArchive,
    MarketDataError,
    MarketDataGap,
    MarketSession,
    QuoteRecord,
)
from src.data.nasdaq_pretrade import (
    NasdaqPublicPolicyEvidence,
    parse_pre_trade_csv_lines,
)
from src.data.nasdaq_reference import (
    NasdaqInstrumentAlias,
    parse_nasdaq_reference_file,
)
from src.data.top_of_book import apply_top_of_book_batch
from src.data.xsto_calendar import nasdaq_stockholm_calendar
from src.backtest_runner import run_walk_forward, validate_dataset
from src.core.backtest import BacktestCosts


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


_PRETRADE_HEADER = (
    "Update date and time;Instrument identification code;Side;"
    "Market Marker;Price;Price currency;Price notation;Quantity;"
    "Quantity currency;Aggregated number of orders and quotes;Venue;"
    "Trading system;Trading system phase;Publication Time"
)
_PRETRADE_SOURCE_URL = (
    "https://tradereports.nasdaq.com/api/regulatory/"
    "trade-report/download?type=PRE_TRADE"
)
_PRETRADE_CONTRACT_KEY = "licensed-pretrade-xsto-v1"


def _pretrade_instrument(*, name: str = "Volvo B") -> InstrumentRecord:
    return InstrumentRecord(
        isin="SE0000115446",
        symbol="VOLV B",
        name=name,
        mic="XSTO",
        currency="SEK",
        instrument_type="COMMON_STOCK",
        source="test-reference",
    )


def _pretrade_alias() -> NasdaqInstrumentAlias:
    return NasdaqInstrumentAlias(
        isin="SE0000115446",
        provider_symbol="VOLV B",
        trading_currency="SEK",
        tradable_id="101",
        source_id="VOLV-B",
        valid_from=None,
        valid_to=None,
    )


def _pretrade_reference(*, name: str = "Volvo B"):
    return InstrumentUniverseSnapshot(
        mic="XSTO",
        instruments=(_pretrade_instrument(name=name),),
    )


def _pretrade_batch(
    *,
    reference=None,
    report_minute=datetime(
        2026,
        7,
        29,
        8,
        0,
        tzinfo=timezone.utc,
    ),
    received_at=None,
    bid_price: str = "321.10",
    ask_quantity: str = "800",
    empty_xsto: bool = False,
    trading_phase: str = "COTR",
):
    reference = reference or _pretrade_reference()
    received_at = received_at or report_minute + timedelta(minutes=15)
    event_time = (
        report_minute + timedelta(seconds=1)
    ).isoformat().replace("+00:00", "Z")
    if empty_xsto:
        rows = [
            (
                f"{event_time};SE0000115446;BUY;;1;SEK;1;1;SEK;1;"
                f"XCSE;CLOB;COTR;{event_time}"
            )
        ]
    else:
        ask_time = (
            report_minute + timedelta(seconds=2)
        ).isoformat().replace("+00:00", "Z")
        rows = [
            (
                f"{event_time};SE0000115446;BUY;;{bid_price};SEK;1;"
                f"1000;SEK;4;XSTO;CLOB;{trading_phase};{event_time}"
            ),
            (
                f"{ask_time};SE0000115446;SELL;;321.30;SEK;1;"
                f"{ask_quantity};SEK;3;XSTO;CLOB;{trading_phase};{ask_time}"
            ),
        ]
    content = (
        "\r\n".join(['"sep=;"', _PRETRADE_HEADER, *rows]) + "\r\n"
    ).encode("utf-8")
    local_minute = report_minute.astimezone(
        ZoneInfo("Europe/Stockholm")
    )
    return parse_pre_trade_csv_lines(
        iter(content.splitlines(keepends=True)),
        file_name=(
            "NordicEquity-pretrade-"
            f"{local_minute:%Y-%m-%dT%H%M}.csv"
        ),
        source_url=_PRETRADE_SOURCE_URL,
        source_checksum_sha256=hashlib.sha256(content).hexdigest(),
        reference_snapshot=reference,
        aliases=(_pretrade_alias(),),
        received_at=received_at,
    )


def _install_pretrade_evidence(
    db,
    connection,
    *,
    contract_status: str = "VALIDATED",
    max_transport_lag_seconds: int = 30,
) -> int:
    instrument = _pretrade_instrument()
    archive = MarketDataArchive.from_bytes(
        provider="test-reference",
        data_type="full-equity-reference",
        file_name="reference-2026-07-29.csv.gz",
        report_minute=datetime(
            2026,
            7,
            29,
            0,
            0,
            tzinfo=timezone.utc,
        ),
        received_at=datetime(
            2026,
            7,
            29,
            7,
            50,
            tzinfo=timezone.utc,
        ),
        source_url="https://example.test/reference.csv.gz",
        content=b"reference",
    )
    db.sync_instrument_snapshot(
        provider="test-reference",
        mic="XSTO",
        snapshot_date=date(2026, 7, 29),
        received_at=datetime(
            2026,
            7,
            29,
            7,
            50,
            tzinfo=timezone.utc,
        ),
        archives=(archive,),
        instruments=(instrument,),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, universe_checksum_sha256
            FROM reference_data_snapshots
            WHERE
                provider = 'test-reference'
                AND mic = 'XSTO'
                AND snapshot_date = '2026-07-29'
            """
        )
        reference_snapshot_id, reference_checksum = cursor.fetchone()
        reference_snapshot_id = int(reference_snapshot_id)
        cursor.execute(
            """
            INSERT INTO market_data_provider_contracts (
                contract_key, provider, product_name, data_type, mic,
                delivery_mode, transport, nominal_delay_seconds,
                max_transport_lag_seconds, usage_scope,
                non_display_category, external_distribution,
                reference_symbols_included, terms_url, status,
                valid_from, valid_until, governance_evidence_status,
                proposed_by
            )
            VALUES (
                %s,
                'nasdaq-nordic',
                'Licensed delayed XSTO Level 1',
                'delayed-pre-trade-equity',
                'XSTO',
                'DELAYED_15M',
                'AUTHORIZED_VENDOR',
                900,
                %s,
                'INTERNAL_ANALYSIS_AND_PAPER',
                'NONE',
                FALSE,
                TRUE,
                'https://example.test/terms',
                'DRAFT',
                '2026-01-01',
                '2026-12-31',
                'PENDING',
                'operator:test'
            )
            RETURNING id
            """,
            (
                _PRETRADE_CONTRACT_KEY,
                max_transport_lag_seconds,
            ),
        )
        contract_id = int(cursor.fetchone()[0])
        if contract_status == "VALIDATED":
            cursor.execute(
                """
                UPDATE market_data_provider_contracts
                SET
                    status = 'VALIDATED',
                    governance_evidence_status = 'VERIFIED',
                    terms_checksum_sha256 = %s,
                    raw_storage_allowed = TRUE,
                    derived_storage_allowed = TRUE,
                    retention_policy = 'Internal test retention.',
                    legal_reviewed_at =
                        '2026-07-28T00:00:00Z'::TIMESTAMPTZ,
                    transport_verified_at =
                        '2026-07-28T00:00:00Z'::TIMESTAMPTZ,
                    reviewed_by = 'operator:test',
                    updated_at = NOW()
                WHERE id = %s
                """,
                ("b" * 64, contract_id),
            )
            cursor.execute(
                """
                INSERT INTO market_data_provider_validations (
                    contract_id, status, validated_at, valid_until,
                    expected_instruments, product_covered_instruments,
                    symbol_mapped_instruments, sample_file_count,
                    sample_quote_count, max_observed_delivery_seconds,
                    evidence_checksum_sha256, validated_by,
                    governance_evidence_status, reference_snapshot_id,
                    reference_checksum_sha256, acceptance_session_count,
                    first_session_date, last_session_date,
                    acceptance_checksum_sha256
                )
                VALUES (
                    %s,
                    'PASSED',
                    '2026-07-28T00:00:00Z',
                    '2026-08-31T00:00:00Z',
                    1,
                    1,
                    1,
                    5,
                    10,
                    905,
                    %s,
                    'operator:test',
                    'VERIFIED',
                    %s,
                    %s,
                    5,
                    '2026-07-21',
                    '2026-07-25',
                    %s
                )
                """,
                (
                    contract_id,
                    "d" * 64,
                    reference_snapshot_id,
                    reference_checksum,
                    "e" * 64,
                ),
            )
        elif contract_status != "DRAFT":
            raise ValueError("unsupported provider fixture status")
    return reference_snapshot_id


def _install_open_xsto_session(connection, *, at: datetime) -> None:
    local_date = at.astimezone(
        ZoneInfo("Europe/Stockholm")
    ).date()
    with connection.cursor() as cursor:
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
                local_date,
                at - timedelta(hours=1),
                at + timedelta(hours=1),
            ),
        )


@pytest.fixture()
def connection():
    assert TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def clean_database(connection, monkeypatch):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            TRUNCATE
                reference_data_object_archives,
                market_data_object_archives,
                object_archive_runs,
                continuous_learning_runs,
                scheduled_job_runs,
                candidate_policy_versions,
                candidate_policy_calibration_runs,
                candidate_prediction_outcomes,
                candidate_prediction_entries,
                candidate_predictions,
                operational_alert_events,
                scheduled_routine_events,
                trading_risk_evaluations,
                trading_daily_risk,
                trading_control_events,
                pre_trade_book_states,
                pre_trade_stream_cursors,
                pre_trade_updates,
                pre_trade_batches,
                market_index_levels,
                market_data_provider_validations,
                market_data_provider_contracts,
                backtest_equity_curve,
                backtest_trades,
                walk_forward_folds,
                walk_forward_runs,
                backtest_corporate_actions,
                backtest_universe_memberships,
                backtest_datasets,
                trade_lot_allocations,
                position_lots,
                reference_data_files,
                reference_snapshot_instruments,
                reference_data_snapshots,
                reference_data_entitlements,
                market_calendar_snapshots,
                market_sessions,
                market_data_gaps,
                market_data_files,
                data_sync_runs,
                market_bars,
                market_quotes,
                instrument_aliases,
                instruments,
                portfolio,
                trades,
                balance
            RESTART IDENTITY CASCADE;
            INSERT INTO balance (cash, total_value) VALUES (20000, 20000);
            INSERT INTO candidate_policy_versions (
                version,
                status,
                config,
                config_hash,
                proposed_by,
                reviewed_by,
                reviewed_at,
                activated_by,
                activated_at
            )
            VALUES (
                'xsto-momentum-v1',
                'ACTIVE',
                '{"max_spread_bps":250,"min_signal_score":0}'::JSONB,
                'c094e434d32c0976f395e9c4e5d7a7e9c3a3e9f70991e1ab3e8f3678d4b8a148',
                'bootstrap-existing-policy',
                'operator-bootstrap',
                NOW(),
                'operator-bootstrap',
                NOW()
            );
            UPDATE trading_controls
            SET
                status = 'ACTIVE',
                max_daily_loss_pct = 3,
                reason = NULL,
                changed_by = 'operator:test-reset',
                changed_at = NOW()
            WHERE singleton = TRUE;
            """
        )
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    return Database()


def test_object_archive_queue_and_journal_are_idempotent_and_append_only(
    clean_database,
    connection,
):
    db = clean_database
    evaluated_at = datetime.now(timezone.utc).replace(microsecond=0)
    archive = MarketDataArchive.from_bytes(
        provider="esma-firds",
        data_type="full-equity-reference",
        file_name="DLTINS_SE_20260805_01of01.zip",
        report_minute=evaluated_at - timedelta(hours=1),
        received_at=evaluated_at,
        source_url="https://example.test/reference.zip",
        content=b"verified reference data",
    )
    db.ingest_market_data_file(archive, (), ())

    pending = db.get_pending_object_archive_inputs(limit=10)
    source = next(
        row for row in pending
        if row["file_name"] == archive.file_name
    )
    assert source["compressed_content"] == archive.compressed_content

    object_key = (
        "trading-agent/market-data/esma-firds/"
        "full-equity-reference/2026/08/05/"
        f"{source['id']}-{archive.checksum_sha256}.gz"
    )
    values = {
        "run_key": "object-archive:integration-test",
        "evaluated_at": evaluated_at,
        "status": "SUCCEEDED",
        "reason_code": "ARCHIVED",
        "selected_count": 1,
        "archived_count": 1,
        "uploaded_count": 1,
        "existing_count": 0,
        "failed_count": 0,
        "compressed_bytes": len(archive.compressed_content),
        "objects": [{
            "source_kind": "market-data",
            "source_id": source["id"],
            "bucket": "orders",
            "object_key": object_key,
            "compressed_checksum_sha256": hashlib.sha256(
                archive.compressed_content
            ).hexdigest(),
            "compressed_bytes": len(archive.compressed_content),
            "uploaded": True,
        }],
    }
    run_id = db.record_object_archive_run(**values)
    assert db.record_object_archive_run(**values) == run_id
    assert all(
        row["id"] != source["id"]
        for row in db.get_pending_object_archive_inputs(limit=10)
    )

    runtime = db.get_object_archive_runtime_status(now=evaluated_at)
    assert runtime["status"] == "SUCCEEDED"
    assert runtime["archived_count"] == 1
    assert runtime["total_object_count"] == 1
    assert runtime["total_compressed_bytes"] == len(
        archive.compressed_content
    )

    with connection.cursor() as cursor:
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="object archive evidence is append-only",
        ):
            cursor.execute(
                """
                UPDATE market_data_object_archives
                SET object_key = 'trading-agent/invalid'
                WHERE run_id = %s
                """,
                (run_id,),
            )

    raw_reference = b"verified FIRDS source bytes"
    compressed_reference = gzip.compress(raw_reference, mtime=0)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO data_sync_runs (
                provider,
                data_type,
                status,
                records_received,
                records_inserted,
                run_key,
                started_at,
                finished_at
            )
            VALUES (
                'esma-firds',
                'reference',
                'SUCCEEDED',
                1,
                1,
                'object-archive-reference-test',
                %s,
                %s
            )
            RETURNING id
            """,
            (evaluated_at - timedelta(seconds=1), evaluated_at),
        )
        sync_run_id = cursor.fetchone()[0]
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
                'esma-firds',
                'XSTO',
                %s,
                %s,
                %s,
                1,
                1,
                %s
            )
            RETURNING id
            """,
            (
                evaluated_at.date(),
                evaluated_at,
                "d" * 64,
                sync_run_id,
            ),
        )
        snapshot_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO reference_data_files (
                snapshot_id,
                file_name,
                source_url,
                checksum_sha256,
                content_encoding,
                compressed_content,
                uncompressed_bytes
            )
            VALUES (%s, %s, %s, %s, 'gzip', %s, %s)
            RETURNING id
            """,
            (
                snapshot_id,
                "DLTINS_SE_20260805_01of01.zip",
                "https://example.test/firds.zip",
                hashlib.sha256(raw_reference).hexdigest(),
                compressed_reference,
                len(raw_reference),
            ),
        )
        reference_file_id = cursor.fetchone()[0]

    reference_source = next(
        row
        for row in db.get_pending_object_archive_inputs(limit=10)
        if row["source_kind"] == "reference-data"
    )
    assert reference_source["id"] == reference_file_id
    assert reference_source["compressed_content"] == compressed_reference

    reference_values = {
        "run_key": "object-archive:reference-integration-test",
        "evaluated_at": evaluated_at,
        "status": "SUCCEEDED",
        "reason_code": "ARCHIVED",
        "selected_count": 1,
        "archived_count": 1,
        "uploaded_count": 1,
        "existing_count": 0,
        "failed_count": 0,
        "compressed_bytes": len(compressed_reference),
        "objects": [{
            "source_kind": "reference-data",
            "source_id": reference_file_id,
            "bucket": "orders",
            "object_key": (
                "trading-agent/reference-data/esma-firds/"
                "reference-universe/2026/08/05/"
                f"{reference_file_id}-"
                f"{hashlib.sha256(raw_reference).hexdigest()}.gz"
            ),
            "compressed_checksum_sha256": hashlib.sha256(
                compressed_reference
            ).hexdigest(),
            "compressed_bytes": len(compressed_reference),
            "uploaded": True,
        }],
    }
    reference_run_id = db.record_object_archive_run(**reference_values)
    assert db.record_object_archive_run(**reference_values) == (
        reference_run_id
    )
    assert not any(
        row["source_kind"] == "reference-data"
        and row["id"] == reference_file_id
        for row in db.get_pending_object_archive_inputs(limit=10)
    )


def test_latest_market_session_date_uses_only_open_xsto_sessions(
    clean_database,
    connection,
):
    with connection.cursor() as cursor:
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
            VALUES
                (
                    'XSTO',
                    DATE '2026-07-27',
                    TIMESTAMPTZ '2026-07-27 07:00:00+00',
                    TIMESTAMPTZ '2026-07-27 15:30:00+00',
                    'REGULAR',
                    'OPEN',
                    'Europe/Stockholm',
                    'test-calendar'
                ),
                (
                    'XSTO',
                    DATE '2026-07-28',
                    NULL,
                    NULL,
                    'CLOSED',
                    'CLOSED',
                    'Europe/Stockholm',
                    'test-calendar'
                ),
                (
                    'XSTO',
                    DATE '2026-07-29',
                    TIMESTAMPTZ '2026-07-29 07:00:00+00',
                    TIMESTAMPTZ '2026-07-29 11:00:00+00',
                    'HALF_DAY',
                    'HALF_DAY',
                    'Europe/Stockholm',
                    'test-calendar'
                ),
                (
                    'XSTO',
                    DATE '2026-07-31',
                    TIMESTAMPTZ '2026-07-31 07:00:00+00',
                    TIMESTAMPTZ '2026-07-31 15:30:00+00',
                    'REGULAR',
                    'OPEN',
                    'Europe/Stockholm',
                    'test-calendar'
                )
            """
        )

    assert clean_database.get_latest_market_session_date(
        mic="XSTO",
        on_or_before=date(2026, 7, 30),
    ) == date(2026, 7, 29)
    assert clean_database.get_latest_market_session_date(
        mic="XSTO",
        on_or_before=date(2026, 7, 26),
    ) is None
    with pytest.raises(MarketDataError, match="MIC"):
        clean_database.get_latest_market_session_date(
            mic="xsto",
            on_or_before=date(2026, 7, 30),
        )
    with pytest.raises(MarketDataError, match="date"):
        clean_database.get_latest_market_session_date(
            mic="XSTO",
            on_or_before=datetime(
                2026,
                7,
                30,
                tzinfo=timezone.utc,
            ),
        )


def test_scheduled_routine_evidence_survives_retries(clean_database):
    db = clean_database
    scheduled_at = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)

    db.record_scheduled_routine_event(
        routine_key="2026-07-30:midday",
        routine_name="midday",
        scheduled_at=scheduled_at,
        status="FAILED",
        failure_code="ROUTINE_EXECUTION_ERROR",
        observed_at=scheduled_at + timedelta(minutes=1),
    )
    db.record_scheduled_routine_event(
        routine_key="2026-07-30:midday",
        routine_name="midday",
        scheduled_at=scheduled_at,
        status="SUCCEEDED",
        observed_at=scheduled_at + timedelta(minutes=2),
    )

    assert db.get_successful_scheduled_routine_keys(
        date(2026, 7, 30)
    ) == ("2026-07-30:midday",)
    assert [
        row["status"]
        for row in db.query(
            """
            SELECT status
            FROM scheduled_routine_events
            ORDER BY id
            """
        )
    ] == ["FAILED", "SUCCEEDED"]


def test_scheduled_job_run_lease_recovers_after_restart_exactly_once(
    clean_database,
):
    db = clean_database
    scheduled_at = datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc)
    job_key = "scheduled-brain:1984324"

    assert db.claim_scheduled_job_run(
        job_key=job_key,
        job_name="brain_cycle",
        scheduled_at=scheduled_at,
        observed_at=scheduled_at + timedelta(minutes=1),
        run_kind="CURRENT",
        lease_seconds=1200,
    )
    assert not db.claim_scheduled_job_run(
        job_key=job_key,
        job_name="brain_cycle",
        scheduled_at=scheduled_at,
        observed_at=scheduled_at + timedelta(minutes=2),
        run_kind="CURRENT",
        lease_seconds=1200,
    )
    assert db.get_scheduled_job_runtime_status(
        now=scheduled_at + timedelta(minutes=22),
    ) == {
        "expired_claim_count": 1,
        "session_open": False,
        "latest_brain_at": None,
        "latest_brain_age_seconds": None,
        "latest_study_at": None,
        "latest_study_age_seconds": None,
    }
    assert db.claim_scheduled_job_run(
        job_key=job_key,
        job_name="brain_cycle",
        scheduled_at=scheduled_at,
        observed_at=scheduled_at + timedelta(minutes=22),
        run_kind="RECOVERY",
        lease_seconds=1200,
    )
    assert db.complete_scheduled_job_run(
        job_key=job_key,
        claimed_at=scheduled_at + timedelta(minutes=22),
        status="SUCCEEDED",
        observed_at=scheduled_at + timedelta(minutes=23),
    )
    assert not db.claim_scheduled_job_run(
        job_key=job_key,
        job_name="brain_cycle",
        scheduled_at=scheduled_at,
        observed_at=scheduled_at + timedelta(minutes=24),
        run_kind="CURRENT",
        lease_seconds=1200,
    )
    assert db.get_latest_terminal_scheduled_job_at("brain_cycle") == scheduled_at
    assert db.get_scheduled_job_runtime_status(
        now=scheduled_at + timedelta(minutes=23),
    ) == {
        "expired_claim_count": 0,
        "session_open": False,
        "latest_brain_at": scheduled_at,
        "latest_brain_age_seconds": 1380,
        "latest_study_at": None,
        "latest_study_age_seconds": None,
    }

    assert db.query(
        """
        SELECT status, attempt_count, run_kind
        FROM scheduled_job_runs
        WHERE job_key = :job_key
        """,
        {"job_key": job_key},
    ) == [
        {
            "status": "SUCCEEDED",
            "attempt_count": 2,
            "run_kind": "RECOVERY",
        }
    ]


def test_operational_alert_transitions_are_deduplicated(clean_database):
    from src.operational_monitor import OperationalAlert

    db = clean_database
    alert = OperationalAlert(
        "ledger",
        "LEDGER_INVARIANT_FAILED",
        "PAGE",
        "Paper ledger invariant is not satisfied",
    )
    first = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)

    opened = db.sync_operational_alerts((alert,), observed_at=first)
    duplicate = db.sync_operational_alerts(
        (alert,),
        observed_at=first + timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="older than persisted"):
        db.sync_operational_alerts(
            (alert,),
            observed_at=first - timedelta(minutes=1),
        )
    resolved = db.sync_operational_alerts(
        (),
        observed_at=first + timedelta(minutes=2),
    )

    assert [event["state"] for event in opened] == ["OPEN"]
    assert duplicate == ()
    assert [event["state"] for event in resolved] == ["RESOLVED"]
    assert [
        row["state"]
        for row in db.query(
            """
            SELECT state
            FROM operational_alert_events
            ORDER BY id
            """
        )
    ] == ["OPEN", "RESOLVED"]


def _ingest_provider_bound_quotes(db, quotes):
    inserted = 0
    for index, quote in enumerate(quotes):
        archive = MarketDataArchive.from_bytes(
            provider="licensed-provider",
            data_type="delayed-post-trade-equity",
            file_name=(
                "licensed-provider-"
                f"{quote.event_time:%Y%m%dT%H%M%S%f}-{index}.csv"
            ),
            report_minute=quote.event_time,
            received_at=quote.received_at,
            source_url="https://example.test/market-data.csv",
            content=(
                f"{quote.isin},{quote.event_time.isoformat()},"
                f"{quote.last_price}"
            ).encode("utf-8"),
            provider_contract_key="licensed-provider-xsto-v1",
        )
        result = db.ingest_market_data_file(archive, (quote,), ())
        inserted += result.quotes_inserted
    return inserted


def _seed_authorized_valuation_quote(db, connection):
    now = datetime.now(timezone.utc)
    instrument = InstrumentRecord(
        isin="SE0000115446",
        symbol="VOLV B",
        name="Volvo B",
        mic="XSTO",
        currency="SEK",
        instrument_type="COMMON_STOCK",
        source="test-reference",
    )
    instrument_id = db.upsert_instruments([instrument])[instrument.isin]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO companies (ticker, name, sector, instrument_id)
            VALUES ('VOLV-B', 'Volvo B', 'Industrials', %s)
            """,
            (instrument_id,),
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
                %s,
                NOW()
            )
            RETURNING id
            """,
            (f"valuation-reference-{now.timestamp()}",),
        )
        sync_run_id = cursor.fetchone()[0]
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
                CURRENT_DATE,
                NOW(),
                %s,
                1,
                1,
                %s
            )
            RETURNING id
            """,
            ("b" * 64, sync_run_id),
        )
        reference_snapshot_id = cursor.fetchone()[0]
        cursor.execute(
            """
            UPDATE instruments
            SET reference_snapshot_date = CURRENT_DATE
            WHERE id = %s
            """,
            (instrument_id,),
        )
        cursor.execute(
            """
            INSERT INTO reference_snapshot_instruments (
                snapshot_id,
                instrument_id,
                isin
            )
            VALUES (%s, %s, %s)
            """,
            (reference_snapshot_id, instrument_id, instrument.isin),
        )
        cursor.execute(
            """
            INSERT INTO market_data_provider_contracts (
                contract_key,
                provider,
                product_name,
                data_type,
                mic,
                delivery_mode,
                transport,
                nominal_delay_seconds,
                max_transport_lag_seconds,
                usage_scope,
                non_display_category,
                external_distribution,
                reference_symbols_included,
                terms_url,
                status,
                valid_from,
                valid_until,
                governance_evidence_status,
                proposed_by
            )
            VALUES (
                'licensed-provider-xsto-v1',
                'licensed-provider',
                'Licensed delayed equity',
                'delayed-post-trade-equity',
                'XSTO',
                'DELAYED_15M',
                'AUTHORIZED_VENDOR',
                900,
                30,
                'INTERNAL_ANALYSIS_AND_PAPER',
                'NONE',
                FALSE,
                TRUE,
                'https://example.test/terms',
                'DRAFT',
                CURRENT_DATE - 30,
                CURRENT_DATE + 30,
                'PENDING',
                'operator:test'
            )
            RETURNING id
            """
        )
        contract_id = cursor.fetchone()[0]
        cursor.execute(
            """
            UPDATE market_data_provider_contracts
            SET
                status = 'VALIDATED',
                governance_evidence_status = 'VERIFIED',
                terms_checksum_sha256 = %s,
                raw_storage_allowed = TRUE,
                derived_storage_allowed = TRUE,
                retention_policy = 'Internal test retention.',
                legal_reviewed_at = NOW(),
                transport_verified_at = NOW(),
                reviewed_by = 'operator:test',
                updated_at = NOW()
            WHERE id = %s
            """,
            ("a" * 64, contract_id),
        )
        cursor.execute(
            """
            INSERT INTO market_data_provider_validations (
                contract_id,
                status,
                validated_at,
                valid_until,
                expected_instruments,
                product_covered_instruments,
                symbol_mapped_instruments,
                sample_file_count,
                sample_quote_count,
                max_observed_delivery_seconds,
                evidence_checksum_sha256,
                validated_by,
                governance_evidence_status,
                reference_snapshot_id,
                reference_checksum_sha256,
                acceptance_session_count,
                first_session_date,
                last_session_date,
                acceptance_checksum_sha256
            )
            VALUES (
                %s,
                'PASSED',
                NOW(),
                NOW() + INTERVAL '7 days',
                1,
                1,
                1,
                1,
                1,
                920,
                %s,
                'operator:test',
                'VERIFIED',
                %s,
                %s,
                5,
                CURRENT_DATE - 6,
                CURRENT_DATE - 2,
                %s
            )
            """,
            (
                contract_id,
                "c" * 64,
                reference_snapshot_id,
                "b" * 64,
                "d" * 64,
            ),
        )
    quote = QuoteRecord(
        isin=instrument.isin,
        mic=instrument.mic,
        event_time=now - timedelta(minutes=15),
        received_at=now,
        source="licensed-provider-delayed",
        last_price=Decimal("125.50"),
        currency="SEK",
        volume=Decimal("100"),
    )
    assert _ingest_provider_bound_quotes(db, (quote,)) == 1
    return db.get_latest_market_quote("VOLV-B")


def test_database_rejects_duplicate_portfolio_rows(connection):
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM portfolio WHERE ticker = 'DUP-ROW'")
        cursor.execute(
            """
            INSERT INTO portfolio (ticker, shares, avg_price)
            VALUES ('DUP-ROW', 1, 100)
            """
        )

        with pytest.raises(psycopg2.errors.UniqueViolation):
            cursor.execute(
                """
                INSERT INTO portfolio (ticker, shares, avg_price)
                VALUES ('DUP-ROW', 1, 100)
                """
            )


def test_buy_and_sell_update_ledger_atomically(clean_database):
    db = clean_database
    buy = {
        "ticker": "VOLV-B",
        "action": "BUY",
        "shares": 20,
        "price": 100,
        "total_value": 2000,
        "reasoning": "Test buy",
        "confidence": 75,
        "hypothesis": "Test",
        "macro_context": {},
        "target_price": 110,
        "stop_loss": 95,
        "target_pct": 10,
        "stop_loss_pct": -5,
        "idempotency_key": "test-ledger-buy",
    }
    buy_trade_id = db.log_trade(buy)

    assert isinstance(buy_trade_id, int)
    assert db.get_balance()["cash"] == pytest.approx(18000)
    position = db.get_portfolio().iloc[0]
    assert float(position["shares"]) == pytest.approx(20)
    assert float(position["avg_price"]) == pytest.approx(100)

    sell = {
        **buy,
        "action": "SELL",
        "shares": 20,
        "price": 110,
        "total_value": 2200,
        "reasoning": "Test exit",
        "target_price": None,
        "stop_loss": None,
        "target_pct": 0,
        "stop_loss_pct": 0,
        "idempotency_key": "test-ledger-sell",
    }
    sell_trade_id = db.log_trade(sell)

    assert isinstance(sell_trade_id, int)
    assert db.get_balance()["cash"] == pytest.approx(20200)
    assert db.get_portfolio().empty

    trades = db.get_trades(limit=10)
    sell_row = trades[trades["action"] == "SELL"].iloc[0]
    buy_row = trades[trades["action"] == "BUY"].iloc[0]
    assert float(sell_row["pnl"]) == pytest.approx(200)
    assert int(sell_row["entry_trade_id"]) == buy_trade_id
    assert buy_row["closed_at"] is not None


def test_oversell_rolls_back_without_changing_cash_or_position(clean_database):
    db = clean_database
    buy = {
        "ticker": "VOLV-B",
        "action": "BUY",
        "shares": 10,
        "price": 100,
        "total_value": 1000,
        "reasoning": "Test buy",
        "confidence": 75,
        "hypothesis": "Test",
        "macro_context": {},
        "target_price": 110,
        "stop_loss": 95,
        "target_pct": 10,
        "stop_loss_pct": -5,
        "idempotency_key": "test-oversell-buy",
    }
    db.log_trade(buy)

    with pytest.raises(ValueError, match="Cannot sell"):
        db.log_trade(
            {
                **buy,
                "action": "SELL",
                "shares": 11,
                "price": 110,
                "total_value": 1210,
                "reasoning": "Invalid oversell",
                "idempotency_key": "test-oversell-sell",
            }
        )

    assert db.get_balance()["cash"] == pytest.approx(19000)
    position = db.get_portfolio().iloc[0]
    assert float(position["shares"]) == pytest.approx(10)


def test_fractional_trade_uses_database_precision_consistently(clean_database):
    db = clean_database
    db.log_trade(
        {
            "ticker": "VOLV-B",
            "action": "BUY",
            "shares": 6.00006,
            "price": 333.33,
            "total_value": 2000,
            "reasoning": "Fractional test",
            "confidence": 75,
            "hypothesis": "Test",
            "macro_context": {},
            "target_price": 366.663,
            "stop_loss": 316.6635,
            "target_pct": 10,
            "stop_loss_pct": -5,
            "idempotency_key": "test-fractional-buy",
        }
    )

    position = db.get_portfolio().iloc[0]
    assert float(position["shares"]) == pytest.approx(6.0001)
    assert db.get_balance()["cash"] == pytest.approx(17999.99)


def test_duplicate_idempotency_key_returns_original_trade_without_rebooking(
    clean_database,
):
    db = clean_database
    trade = {
        "ticker": "VOLV-B",
        "action": "BUY",
        "shares": 10,
        "price": 100,
        "total_value": 1000,
        "reasoning": "Idempotent buy",
        "confidence": 75,
        "hypothesis": "Test",
        "macro_context": {},
        "target_price": 110,
        "stop_loss": 95,
        "target_pct": 10,
        "stop_loss_pct": -5,
        "idempotency_key": "brain:slot-1:buy:volv-b",
    }

    first_id = db.log_trade(trade)
    second_id = db.log_trade(trade)

    assert second_id == first_id
    assert db.get_balance()["cash"] == pytest.approx(19000)
    assert float(db.get_portfolio().iloc[0]["shares"]) == pytest.approx(10)
    assert len(db.get_trades(limit=10)) == 1
    lots = db.query(
        "SELECT original_shares, remaining_shares FROM position_lots"
    )
    assert len(lots) == 1
    assert float(lots[0]["remaining_shares"]) == pytest.approx(10)


def test_idempotency_key_reuse_with_changed_payload_is_rejected(
    clean_database,
):
    db = clean_database
    trade = {
        "ticker": "VOLV-B",
        "action": "BUY",
        "shares": 10,
        "price": 100,
        "total_value": 1000,
        "reasoning": "Original buy",
        "confidence": 75,
        "hypothesis": "Test",
        "macro_context": {},
        "target_price": 110,
        "stop_loss": 95,
        "target_pct": 10,
        "stop_loss_pct": -5,
        "idempotency_key": "brain:slot-2:buy:volv-b",
    }
    original_id = db.log_trade(trade)

    with pytest.raises(ValueError, match="different trade payload"):
        db.log_trade({**trade, "price": 101, "total_value": 1010})

    assert db.get_balance()["cash"] == pytest.approx(19000)
    assert [row["id"] for row in db.query("SELECT id FROM trades")] == [
        original_id
    ]


def test_concurrent_duplicate_trade_is_booked_exactly_once(clean_database):
    db = clean_database
    trade = {
        "ticker": "VOLV-B",
        "action": "BUY",
        "shares": 10,
        "price": 100,
        "total_value": 1000,
        "reasoning": "Concurrent idempotent buy",
        "confidence": 75,
        "hypothesis": "Test",
        "macro_context": {},
        "target_price": 110,
        "stop_loss": 95,
        "target_pct": 10,
        "stop_loss_pct": -5,
        "idempotency_key": "brain:slot-3:buy:volv-b",
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: db.log_trade(trade), range(2)))

    assert ids[0] == ids[1]
    assert db.get_balance()["cash"] == pytest.approx(19000)
    assert len(db.query("SELECT id FROM trades")) == 1
    assert len(db.query("SELECT id FROM position_lots")) == 1


def test_daily_mark_to_market_loss_blocks_entries_until_next_day(
    clean_database,
):
    db = clean_database
    stockholm = ZoneInfo("Europe/Stockholm")
    session_date = datetime.now(timezone.utc).astimezone(stockholm).date()
    first_check = datetime(
        session_date.year,
        session_date.month,
        session_date.day,
        10,
        tzinfo=stockholm,
    ).astimezone(timezone.utc)

    initial = db.evaluate_entry_risk(
        total_value=20_000,
        evaluated_at=first_check,
    )
    breached = db.evaluate_entry_risk(
        total_value=19_400,
        evaluated_at=first_check + timedelta(hours=1),
    )
    recovered_same_day = db.evaluate_entry_risk(
        total_value=20_100,
        evaluated_at=first_check + timedelta(hours=2),
    )
    with pytest.raises(ValueError, match="Daily loss limit"):
        db.log_trade({
            "ticker": "VOLV-B",
            "action": "BUY",
            "shares": 10,
            "price": 100,
            "total_value": 1000,
            "reasoning": "Must remain blocked after daily breach",
            "confidence": 75,
            "hypothesis": "Test",
            "macro_context": {},
            "target_price": 110,
            "stop_loss": 95,
            "target_pct": 10,
            "stop_loss_pct": -5,
            "idempotency_key": "test-daily-loss-blocked-buy",
        })
    next_day = db.evaluate_entry_risk(
        total_value=19_400,
        evaluated_at=first_check + timedelta(days=1),
    )

    assert initial.allowed is True
    assert breached.allowed is False
    assert breached.reason == "DAILY_LOSS_LIMIT"
    assert breached.daily_return_pct == pytest.approx(-3)
    assert recovered_same_day.allowed is False
    assert recovered_same_day.reason == "DAILY_LOSS_LIMIT"
    assert next_day.allowed is True
    assert next_day.daily_return_pct == pytest.approx(0)
    states = db.query(
        """
        SELECT session_date, opening_equity, limit_breached
        FROM trading_daily_risk
        ORDER BY session_date
        """
    )
    assert len(states) == 2
    assert float(states[0]["opening_equity"]) == pytest.approx(20_000)
    assert states[0]["limit_breached"] is True
    assert len(db.query("SELECT id FROM trading_risk_evaluations")) == 4


def test_manual_halt_blocks_buys_but_allows_risk_reducing_sells(
    clean_database,
):
    db = clean_database
    trade = {
        "ticker": "VOLV-B",
        "action": "BUY",
        "shares": 10,
        "price": 100,
        "total_value": 1000,
        "reasoning": "Open before halt",
        "confidence": 75,
        "hypothesis": "Test",
        "macro_context": {},
        "target_price": 110,
        "stop_loss": 95,
        "target_pct": 10,
        "stop_loss_pct": -5,
        "idempotency_key": "test-risk-control-buy",
    }
    db.log_trade(trade)

    halted = db.update_trading_control(
        status="HALTED",
        reason="Operator incident response",
        changed_by="operator:test-suite",
    )

    assert halted.status == "HALTED"
    with pytest.raises(ValueError, match="Trading is halted"):
        db.log_trade({
            **trade,
            "idempotency_key": "test-risk-control-blocked-buy",
        })

    sell_id = db.log_trade({
        **trade,
        "action": "SELL",
        "price": 99,
        "total_value": 990,
        "reasoning": "Risk-reducing exit while halted",
        "target_price": None,
        "stop_loss": None,
        "target_pct": 0,
        "stop_loss_pct": 0,
        "idempotency_key": "test-risk-control-sell",
    })

    assert isinstance(sell_id, int)
    assert db.get_portfolio().empty
    events = db.query(
        """
        SELECT event_type, new_status, changed_by
        FROM trading_control_events
        ORDER BY id
        """
    )
    assert events == [{
        "event_type": "HALT",
        "new_status": "HALTED",
        "changed_by": "operator:test-suite",
    }]


def test_trading_control_changes_require_human_operator_identity(
    clean_database,
):
    with pytest.raises(ValueError, match="cannot approve"):
        clean_database.update_trading_control(
            status="HALTED",
            reason="Model requested halt",
            changed_by="agent:brain",
        )


def test_ai_trade_links_to_its_persisted_parent_decision(clean_database):
    db = clean_database
    strategy = db.get_active_strategy()
    decision_id = db.log_ai_decision(
        timestamp=datetime(2026, 7, 29, 10, tzinfo=timezone.utc),
        prompt_tokens=120,
        response_tokens=30,
        decisions_json={
            "decisions": [{
                "action": "BUY",
                "ticker": "VOLV-B",
                "reason": "Momentum",
            }],
        },
        market_data_json="verified context",
        raw_response='{"decisions":[]}',
        strategy_version=strategy.version,
        strategy_config_hash=strategy.config_hash,
        model_backend="hermes",
        model_name="gpt-5.6-sol",
        model_provider="openai-codex",
        reasoning_effort="medium",
        response_model="gpt-5.6-sol",
        response_id="resp_test",
    )
    trade_id = db.log_trade({
        "ticker": "VOLV-B",
        "action": "BUY",
        "shares": 10,
        "price": 100,
        "total_value": 1000,
        "reasoning": "AI-linked buy",
        "confidence": 75,
        "hypothesis": "Test",
        "macro_context": {},
        "target_price": 110,
        "stop_loss": 95,
        "target_pct": 10,
        "stop_loss_pct": -5,
        "idempotency_key": "test-ai-linked-buy",
        "strategy_version": strategy.version,
        "decision_id": decision_id,
        "decision_origin": "AI_DECISION",
    })

    audit = db.query(
        """
        SELECT
            trade.decision_id,
            trade.decision_origin,
            decision.strategy_version,
            decision.model_backend,
            decision.model_name,
            decision.model_provider,
            decision.reasoning_effort,
            decision.response_model,
            decision.response_id
        FROM trades trade
        JOIN ai_decisions decision ON decision.id = trade.decision_id
        WHERE trade.id = :trade_id
        """,
        {"trade_id": trade_id},
    )[0]
    assert audit == {
        "decision_id": decision_id,
        "decision_origin": "AI_DECISION",
        "strategy_version": strategy.version,
        "model_backend": "hermes",
        "model_name": "gpt-5.6-sol",
        "model_provider": "openai-codex",
        "reasoning_effort": "medium",
        "response_model": "gpt-5.6-sol",
        "response_id": "resp_test",
    }

    with pytest.raises(ValueError, match="decision_id"):
        db.log_trade({
            "ticker": "ATCO-A",
            "action": "BUY",
            "shares": 1,
            "price": 100,
            "total_value": 100,
            "reasoning": "Missing parent",
            "confidence": 75,
            "hypothesis": "Test",
            "macro_context": {},
            "target_price": 110,
            "stop_loss": 95,
            "target_pct": 10,
            "stop_loss_pct": -5,
            "idempotency_key": "test-ai-missing-parent",
            "strategy_version": strategy.version,
            "decision_origin": "AI_DECISION",
        })


def test_ai_decision_audit_is_append_only(clean_database, connection):
    db = clean_database
    strategy = db.get_active_strategy()
    decision_id = db.log_ai_decision(
        timestamp=datetime(2026, 7, 29, 10, tzinfo=timezone.utc),
        prompt_tokens=120,
        response_tokens=30,
        decisions_json={"decisions": []},
        market_data_json="verified context",
        raw_response='{"decisions":[]}',
        strategy_version=strategy.version,
        strategy_config_hash=strategy.config_hash,
    )

    with connection.cursor() as cursor:
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="AI decision audit is append-only",
        ):
            cursor.execute(
                """
                UPDATE ai_decisions
                SET raw_response = 'changed'
                WHERE id = %s
                """,
                (decision_id,),
            )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="AI decision audit is append-only",
        ):
            cursor.execute(
                "DELETE FROM ai_decisions WHERE id = %s",
                (decision_id,),
            )


def test_strategy_change_requires_learning_approval_and_separate_activation(
    clean_database,
    connection,
):
    db = clean_database
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO learnings (
                category,
                content,
                confidence,
                active
            )
            VALUES (
                'rule',
                'Validated losses support a higher confidence threshold.',
                80,
                TRUE
            )
            RETURNING id
            """
        )
        learning_id = int(cursor.fetchone()[0])

    try:
        baseline = db.get_active_strategy()
        assert baseline.version == "momentum-report-swing-v1"

        with pytest.raises(ValueError, match="At least one learning"):
            db.create_strategy_change_proposal(
                config_patch={"min_confidence": 60},
                learning_ids=[],
                rationale="No evidence should be rejected.",
                proposed_by="student:test-suite",
            )

        proposal_id = db.create_strategy_change_proposal(
            config_patch={"min_confidence": 60},
            learning_ids=[learning_id],
            rationale=(
                "Raise min confidence from 55 to 60 based on the linked "
                "validated learning."
            ),
            proposed_by="student:test-suite",
        )

        with pytest.raises(ValueError, match="does not exist"):
            db.activate_strategy_version(
                "momentum-report-swing-v2-test",
                activated_by="operator:test-suite",
            )

        with pytest.raises(ValueError, match="cannot approve"):
            db.approve_strategy_change_proposal(
                proposal_id,
                new_version="momentum-report-swing-v2-test",
                reviewed_by="student:test-suite",
            )

        db.approve_strategy_change_proposal(
            proposal_id,
            new_version="momentum-report-swing-v2-test",
            reviewed_by="operator:test-suite",
        )
        assert db.get_active_strategy().version == baseline.version

        db.activate_strategy_version(
            "momentum-report-swing-v2-test",
            activated_by="operator:test-suite",
        )
        active = db.get_active_strategy()
        assert active.version == "momentum-report-swing-v2-test"
        assert active.config.min_confidence == 60
        assert [learning.id for learning in active.learnings] == [learning_id]
        assert "Raise min confidence" in active.learnings[0].usage_note
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM strategy_change_proposals
                WHERE proposed_by = 'student:test-suite';

                DELETE FROM strategy_versions
                WHERE proposed_by = 'student:test-suite';

                UPDATE strategy_versions
                SET
                    status = 'ACTIVE',
                    retired_at = NULL,
                    updated_at = NOW()
                WHERE version = 'momentum-report-swing-v1';

                DELETE FROM learnings WHERE id = %s;
                """,
                (learning_id,),
            )


def test_strategy_change_rejects_learning_that_cannot_be_rendered(
    clean_database,
    connection,
):
    db = clean_database
    invalid_content = "x" * 2001
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO learnings (
                category,
                content,
                confidence,
                active
            )
            VALUES ('pattern', %s, 80, TRUE)
            RETURNING id
            """,
            (invalid_content,),
        )
        learning_id = int(cursor.fetchone()[0])

    try:
        with pytest.raises(ValueError, match="active learnings"):
            db.create_strategy_change_proposal(
                config_patch={"min_confidence": 60},
                learning_ids=[learning_id],
                rationale="Invalid prompt evidence must not be linkable.",
                proposed_by="student:test-invalid-learning",
            )

        with pytest.raises(ValueError, match="learning content"):
            db.add_learning({
                "category": "pattern",
                "content": invalid_content,
                "source_trade_ids": [],
                "confidence": 80,
            })
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM strategy_change_proposals
                WHERE proposed_by = 'student:test-invalid-learning';

                DELETE FROM learnings
                WHERE id = %s OR content = %s
                """,
                (learning_id, invalid_content),
            )


def test_validated_point_in_time_dataset_runs_and_persists_idempotently(
    clean_database,
    connection,
):
    db = clean_database
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO instruments (
                isin,
                symbol,
                name,
                mic,
                currency,
                instrument_type,
                source
            )
            VALUES
                (
                    'SE0000115446',
                    'VOLV-B',
                    'Volvo B',
                    'XSTO',
                    'SEK',
                    'COMMON_STOCK',
                    'test-backtest'
                ),
                (
                    'SE0000000001',
                    'OMXSBGI',
                    'OMX Stockholm Benchmark GI',
                    'XSTO',
                    'SEK',
                    'INDEX',
                    'test-backtest'
                )
            RETURNING id
            """
        )
        stock_id, benchmark_id = [int(row[0]) for row in cursor.fetchall()]

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
            SELECT
                'XSTO',
                ('2026-01-05'::date + day_index),
                (
                    ('2026-01-05'::date + day_index) + time '09:00'
                ) AT TIME ZONE 'Europe/Stockholm',
                (
                    ('2026-01-05'::date + day_index) + time '17:00'
                ) AT TIME ZONE 'Europe/Stockholm',
                'REGULAR',
                'OPEN',
                'Europe/Stockholm',
                'test-calendar'
            FROM generate_series(0, 8) AS day_index
            """
        )
        cursor.execute(
            """
            INSERT INTO backtest_datasets (
                name,
                provider,
                bar_source,
                period_start,
                period_end,
                data_cutoff,
                benchmark_instrument_id,
                risk_instrument_id,
                benchmark_is_total_return,
                raw_unadjusted_prices,
                corporate_actions_complete,
                universe_history_complete,
                includes_inactive_and_delisted,
                content_checksum_sha256
            )
            VALUES (
                'synthetic-point-in-time-test',
                'test-provider',
                'test-backtest',
                '2026-01-05',
                '2026-01-13',
                '2026-01-14 00:00:00+00',
                %s,
                %s,
                TRUE,
                TRUE,
                TRUE,
                TRUE,
                TRUE,
                %s
            )
            RETURNING id
            """,
            (benchmark_id, benchmark_id, "0" * 64),
        )
        dataset_id = int(cursor.fetchone()[0])
        cursor.execute(
            """
            INSERT INTO backtest_universe_memberships (
                dataset_id,
                instrument_id,
                valid_from,
                valid_to,
                sector,
                source,
                source_available_at
            )
            VALUES (
                %s,
                %s,
                '2026-01-05',
                '2026-01-13',
                'Industrials',
                'test-membership',
                '2026-01-01 00:00:00+00'
            )
            """,
            (dataset_id, stock_id),
        )
        cursor.execute(
            """
            WITH stock_values AS (
                SELECT
                    ordinal::integer - 1 AS day_index,
                    close_price
                FROM unnest(
                    ARRAY[100, 100, 100, 90, 110, 110, 112, 114, 116]
                        ::numeric[]
                ) WITH ORDINALITY AS stock_series(close_price, ordinal)
            ),
            all_values AS (
                SELECT
                    %s::bigint AS instrument_id,
                    day_index,
                    close_price
                FROM stock_values
                UNION ALL
                SELECT
                    %s::bigint,
                    day_index,
                    100::numeric
                FROM generate_series(0, 8) AS day_index
            )
            INSERT INTO market_bars (
                instrument_id,
                interval_seconds,
                event_time,
                available_at,
                received_at,
                source,
                open,
                high,
                low,
                close,
                volume,
                currency
            )
            SELECT
                instrument_id,
                86400,
                (
                    ('2026-01-05'::date + day_index) + time '17:00'
                ) AT TIME ZONE 'Europe/Stockholm',
                (
                    ('2026-01-05'::date + day_index) + time '17:05'
                ) AT TIME ZONE 'Europe/Stockholm',
                (
                    ('2026-01-05'::date + day_index) + time '17:05'
                ) AT TIME ZONE 'Europe/Stockholm',
                'test-backtest',
                close_price,
                close_price,
                close_price,
                close_price,
                100000,
                'SEK'
            FROM all_values
            """,
            (stock_id, benchmark_id),
        )

    checksum = validate_dataset(
        db,
        dataset_id,
        validated_by="operator:test-suite",
        minimum_sessions=9,
    )
    assert len(checksum) == 64

    result = run_walk_forward(
        db,
        dataset_id=dataset_id,
        strategy_version="momentum-report-swing-v1",
        costs=BacktestCosts(
            fee_bps=0,
            spread_bps=0,
            slippage_bps=0,
            max_volume_participation=0.01,
            min_daily_turnover=0,
        ),
        train_sessions=4,
        test_sessions=5,
        initial_cash=20_000,
        sma_window=3,
        momentum_lookback=3,
    )
    duplicate = run_walk_forward(
        db,
        dataset_id=dataset_id,
        strategy_version="momentum-report-swing-v1",
        costs=BacktestCosts(
            fee_bps=0,
            spread_bps=0,
            slippage_bps=0,
            max_volume_participation=0.01,
            min_daily_turnover=0,
        ),
        train_sessions=4,
        test_sessions=5,
        initial_cash=20_000,
        sma_window=3,
        momentum_lookback=3,
    )

    assert result["status"] == "SUCCEEDED"
    assert int(result["trades_count"]) == 1
    assert duplicate["id"] == result["id"]
    assert db.query("SELECT COUNT(*) AS count FROM walk_forward_runs")[0][
        "count"
    ] == 1
    assert db.query("SELECT COUNT(*) AS count FROM walk_forward_folds")[0][
        "count"
    ] == 1


def test_fifo_lots_allocate_partial_sell_and_recompute_open_cost_basis(
    clean_database,
):
    db = clean_database

    def buy(key, shares, price):
        return db.log_trade(
            {
                "ticker": "VOLV-B",
                "action": "BUY",
                "shares": shares,
                "price": price,
                "total_value": shares * price,
                "reasoning": f"Buy at {price}",
                "confidence": 75,
                "hypothesis": "Test",
                "macro_context": {},
                "target_price": price * 1.1,
                "stop_loss": price * 0.95,
                "target_pct": 10,
                "stop_loss_pct": -5,
                "idempotency_key": key,
            }
        )

    first_buy_id = buy("fifo-buy-1", 10, 100)
    second_buy_id = buy("fifo-buy-2", 10, 120)
    sell_id = db.log_trade(
        {
            "ticker": "VOLV-B",
            "action": "SELL",
            "shares": 15,
            "price": 130,
            "total_value": 1950,
            "reasoning": "FIFO partial exit",
            "confidence": 80,
            "hypothesis": "Test",
            "macro_context": {},
            "target_price": None,
            "stop_loss": None,
            "target_pct": 0,
            "stop_loss_pct": 0,
            "idempotency_key": "fifo-sell-1",
        }
    )

    allocations = db.query(
        """
        SELECT buy_trade_id, shares, entry_price, exit_price, realized_pnl
        FROM trade_lot_allocations
        WHERE sell_trade_id = :sell_trade_id
        ORDER BY id
        """,
        {"sell_trade_id": sell_id},
    )
    assert [row["buy_trade_id"] for row in allocations] == [
        first_buy_id,
        second_buy_id,
    ]
    assert [float(row["shares"]) for row in allocations] == [10, 5]
    assert [float(row["realized_pnl"]) for row in allocations] == [300, 50]

    sell = db.query(
        "SELECT pnl, entry_trade_id FROM trades WHERE id = :id",
        {"id": sell_id},
    )[0]
    assert float(sell["pnl"]) == pytest.approx(350)
    assert sell["entry_trade_id"] == first_buy_id

    position = db.get_portfolio().iloc[0]
    assert float(position["shares"]) == pytest.approx(5)
    assert float(position["avg_price"]) == pytest.approx(120)

    buy_rows = db.query(
        """
        SELECT id, closed_at, pnl
        FROM trades
        WHERE id IN (:first_id, :second_id)
        ORDER BY id
        """,
        {"first_id": first_buy_id, "second_id": second_buy_id},
    )
    assert buy_rows[0]["closed_at"] is not None
    assert float(buy_rows[0]["pnl"]) == pytest.approx(300)
    assert buy_rows[1]["closed_at"] is None
    assert buy_rows[1]["pnl"] is None

    second_sell_id = db.log_trade(
        {
            "ticker": "VOLV-B",
            "action": "SELL",
            "shares": 5,
            "price": 110,
            "total_value": 550,
            "reasoning": "Close remaining FIFO lot",
            "confidence": 80,
            "hypothesis": "Test",
            "macro_context": {},
            "target_price": None,
            "stop_loss": None,
            "target_pct": 0,
            "stop_loss_pct": 0,
            "idempotency_key": "fifo-sell-2",
        }
    )

    second_sell = db.query(
        "SELECT pnl FROM trades WHERE id = :id",
        {"id": second_sell_id},
    )[0]
    second_buy = db.query(
        "SELECT closed_at, pnl FROM trades WHERE id = :id",
        {"id": second_buy_id},
    )[0]
    assert float(second_sell["pnl"]) == pytest.approx(-50)
    assert second_buy["closed_at"] is not None
    assert float(second_buy["pnl"]) == pytest.approx(0)
    assert db.get_portfolio().empty
    assert db.get_balance()["cash"] == pytest.approx(20300)


def test_instrument_quote_and_session_round_trip(clean_database):
    db = clean_database
    instrument = InstrumentRecord(
        isin="SE0000115446",
        symbol="VOLV B",
        name="Volvo B",
        mic="XSTO",
        currency="SEK",
        instrument_type="COMMON_STOCK",
        source="test-reference",
    )
    instrument_ids = db.upsert_instruments([instrument])
    instrument_id = instrument_ids[instrument.isin]
    db.execute(
        """
        INSERT INTO companies (ticker, name, sector, instrument_id)
        VALUES (%s, %s, %s, %s)
        """,
        ("VOLV-B", "Volvo B", "Industrials", instrument_id),
    )

    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    quote = QuoteRecord(
        isin=instrument.isin,
        mic=instrument.mic,
        event_time=now - timedelta(minutes=15),
        received_at=now,
        source="test-delayed",
        last_price=Decimal("321.50"),
        currency="SEK",
        volume=Decimal("100"),
    )

    assert db.save_market_quotes([quote]) == 1
    assert db.save_market_quotes([quote]) == 0
    saved_quote = db.get_latest_market_quote("VOLV-B")
    assert saved_quote is not None
    assert saved_quote.quote_id is not None
    assert saved_quote == replace(quote, quote_id=saved_quote.quote_id)

    session = MarketSession.for_local_date(
        mic="XSTO",
        session_date=date(2026, 7, 29),
        opens_at="09:00",
        closes_at="17:30",
        timezone_name="Europe/Stockholm",
        source="test-calendar",
    )
    db.upsert_market_session(session)

    saved_session = db.get_market_session("XSTO", date(2026, 7, 29))
    assert saved_session == session


def test_portfolio_valuation_requires_authorized_quote_and_persists_mark(
    clean_database,
    connection,
):
    db = clean_database
    db.execute(
        """
        INSERT INTO portfolio (ticker, shares, avg_price, current_price)
        VALUES (%s, %s, %s, %s)
        """,
        ("VOLV-B", 10, 100, 999),
    )
    db.execute(
        """
        INSERT INTO prices (ticker, date, close)
        VALUES (%s, CURRENT_DATE, %s)
        """,
        ("VOLV-B", 888),
    )
    db.execute(
        "UPDATE balance SET cash = %s, total_value = %s",
        (18000, 18000),
    )

    unpriced = db.get_balance()
    assert unpriced["cash"] == pytest.approx(18000)
    assert unpriced["total_value"] is None
    assert unpriced["valuation_complete"] is False
    assert unpriced["unpriced_tickers"] == ["VOLV-B"]
    with pytest.raises(
        MarketDataError,
        match="authorized provider valuation mark",
    ):
        db.save_portfolio_snapshot()

    raw_quote = _seed_authorized_valuation_quote(db, connection)
    authorized_quote = db.get_latest_authorized_market_quote("VOLV-B")

    assert authorized_quote == raw_quote
    assert authorized_quote is not None
    assert authorized_quote.quote_id is not None
    assert db.get_balance()["total_value"] == pytest.approx(19255)

    assert raw_quote is not None
    db.save_portfolio_snapshot(recorded_at=raw_quote.received_at)
    marks = db.query(
        """
        SELECT
            history.total_value,
            history.cash,
            history.positions_value,
            history.recorded_at,
            mark.ticker,
            mark.quote_id,
            mark.shares,
            mark.price,
            mark.market_value
        FROM portfolio_valuation_marks mark
        JOIN portfolio_history history
          ON history.id = mark.portfolio_history_id
        ORDER BY history.recorded_at DESC
        LIMIT 1
        """
    )
    assert len(marks) == 1
    assert float(marks[0]["total_value"]) == pytest.approx(
        float(marks[0]["cash"]) + float(marks[0]["positions_value"])
    )
    assert float(marks[0]["positions_value"]) == pytest.approx(
        float(marks[0]["market_value"])
    )
    assert marks[0]["recorded_at"] == raw_quote.received_at
    assert marks[0]["ticker"] == "VOLV-B"
    assert marks[0]["quote_id"] == authorized_quote.quote_id
    assert float(marks[0]["shares"]) == pytest.approx(10)
    assert float(marks[0]["price"]) == pytest.approx(125.5)
    assert float(marks[0]["market_value"]) == pytest.approx(1255)

    db.execute(
            """
            UPDATE market_data_provider_contracts
            SET
                status = %s,
                revoked_at = NOW(),
                revoked_by = 'operator:test',
                revocation_reason = 'Test provider revocation.'
            WHERE contract_key = %s
            """,
        ("REVOKED", "licensed-provider-xsto-v1"),
    )
    assert db.get_latest_authorized_market_quote("VOLV-B") is None
    revoked = db.get_balance()
    assert revoked["total_value"] is None
    assert revoked["valuation_complete"] is False


def test_portfolio_valuation_persists_authorized_pretrade_mark(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    received_at = datetime.now(timezone.utc).replace(
        second=0,
        microsecond=0,
    )
    report_minute = received_at - timedelta(minutes=15)
    batch = _pretrade_batch(
        report_minute=report_minute,
        received_at=received_at,
    )
    snapshot = apply_top_of_book_batch(previous=(), batch=batch)
    persisted = db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=batch,
        snapshot=snapshot,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT state.id, state.instrument_id
            FROM pre_trade_book_states state
            WHERE state.batch_id = %s
            """,
            (persisted.batch_id,),
        )
        state_id, instrument_id = (
            int(value) for value in cursor.fetchone()
        )
        cursor.execute(
            """
            INSERT INTO companies (
                ticker,
                name,
                sector,
                instrument_id
            )
            VALUES ('VOLV-B', 'Volvo B', 'Industrials', %s)
            """,
            (instrument_id,),
        )
        cursor.execute(
            """
            INSERT INTO portfolio (
                ticker,
                shares,
                avg_price,
                current_price
            )
            VALUES ('VOLV-B', 10, 321.30, 321.30)
            """
        )
        cursor.execute(
            "UPDATE balance SET cash = 16000, total_value = 16000"
        )

    valuation_at = received_at + timedelta(seconds=10)
    db.save_portfolio_snapshot(recorded_at=valuation_at)

    marks = db.query(
        """
        SELECT
            history.total_value,
            history.cash,
            history.positions_value,
            history.recorded_at,
            mark.ticker,
            mark.quote_id,
            mark.source_book_state_id,
            mark.shares,
            mark.price,
            mark.market_value
        FROM portfolio_valuation_marks mark
        JOIN portfolio_history history
          ON history.id = mark.portfolio_history_id
        ORDER BY history.recorded_at DESC
        LIMIT 1
        """
    )
    assert len(marks) == 1
    assert marks[0]["recorded_at"] == valuation_at
    assert marks[0]["ticker"] == "VOLV-B"
    assert marks[0]["quote_id"] is None
    assert marks[0]["source_book_state_id"] == state_id
    assert Decimal(marks[0]["shares"]) == Decimal("10")
    assert Decimal(marks[0]["price"]) == Decimal("321.20000000")
    assert Decimal(marks[0]["market_value"]) == Decimal("3212.00")
    assert Decimal(marks[0]["cash"]) == Decimal("16000.00")
    assert Decimal(marks[0]["positions_value"]) == Decimal("3212.00")
    assert Decimal(marks[0]["total_value"]) == Decimal("19212.00")


def test_authorized_quote_rejects_provider_name_spoof_without_contract(
    clean_database,
    connection,
):
    db = clean_database
    archived_quote = _seed_authorized_valuation_quote(db, connection)
    assert archived_quote is not None
    spoofed_quote = QuoteRecord(
        isin=archived_quote.isin,
        mic=archived_quote.mic,
        event_time=archived_quote.event_time + timedelta(seconds=1),
        received_at=archived_quote.received_at,
        source="licensed-provider-forged",
        last_price=Decimal("999"),
        currency=archived_quote.currency,
        volume=Decimal("1"),
    )
    assert db.save_market_quotes((spoofed_quote,)) == 1

    selected = db.get_latest_authorized_market_quote("VOLV-B")

    assert selected is not None
    assert selected.quote_id == archived_quote.quote_id
    assert selected.last_price == Decimal("125.50")


def test_authorized_runtime_prices_bars_and_full_coverage_gate(
    clean_database,
    connection,
):
    db = clean_database
    _seed_authorized_valuation_quote(db, connection)
    db.execute("TRUNCATE market_quotes RESTART IDENTITY CASCADE")

    quote_times = (
        datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 29, 9, 30, tzinfo=timezone.utc),
        datetime(2026, 7, 29, 9, 45, tzinfo=timezone.utc),
    )
    prices = ("100", "110", "120", "130")
    volumes = ("10", "20", "30", "40")
    authorized_quotes = [
        QuoteRecord(
            isin="SE0000115446",
            mic="XSTO",
            event_time=event_time,
            received_at=event_time + timedelta(minutes=15),
            source="licensed-provider-delayed",
            last_price=Decimal(price),
            currency="SEK",
            volume=Decimal(volume),
        )
        for event_time, price, volume in zip(
            quote_times,
            prices,
            volumes,
        )
    ]
    rogue_quote = QuoteRecord(
        isin="SE0000115446",
        mic="XSTO",
        event_time=datetime(2026, 7, 29, 9, 50, tzinfo=timezone.utc),
        received_at=datetime(2026, 7, 29, 9, 51, tzinfo=timezone.utc),
        source="rogue-provider",
        last_price=Decimal("999"),
        currency="SEK",
        volume=Decimal("1000"),
    )
    assert _ingest_provider_bound_quotes(db, authorized_quotes) == 4
    assert db.save_market_quotes((rogue_quote,)) == 1
    for session_date in (date(2026, 7, 28), date(2026, 7, 29)):
        db.upsert_market_session(
            MarketSession.for_local_date(
                mic="XSTO",
                session_date=session_date,
                opens_at="09:00",
                closes_at="17:30",
                timezone_name="Europe/Stockholm",
                source="test-calendar",
            )
        )

    bars = db.get_authorized_daily_bars("VOLV-B", limit=60)
    latest = db.get_latest_authorized_prices()

    assert [bar["date"] for bar in bars] == [
        date(2026, 7, 29),
        date(2026, 7, 28),
    ]
    assert float(bars[0]["open"]) == pytest.approx(120)
    assert float(bars[0]["high"]) == pytest.approx(130)
    assert float(bars[0]["low"]) == pytest.approx(120)
    assert float(bars[0]["close"]) == pytest.approx(130)
    assert float(bars[0]["volume"]) == pytest.approx(70)
    assert latest[0]["ticker"] == "VOLV-B"
    assert float(latest[0]["close"]) == pytest.approx(130)
    assert float(latest[0]["change_pct"]) == pytest.approx(
        (130 / 110 - 1) * 100
    )
    assert latest[0]["source"] == "licensed-provider-delayed"

    db.execute(
        """
        INSERT INTO data_sync_runs (
            provider,
            data_type,
            started_at,
            status,
            records_received,
            records_inserted,
            run_key,
            finished_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            "licensed-provider",
            "delayed-post-trade-equity",
            datetime(2026, 7, 29, 9, 59, tzinfo=timezone.utc),
            "SUCCEEDED",
            1,
            1,
            "runtime-gate-success",
            datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
        ),
    )

    status = db.require_operational_market_data(
        datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
    )
    assert status["provider"] == "licensed-provider"
    assert status["fresh_quote_count"] == 1
    assert status["expected_instrument_count"] == 1

    db.execute(
        """
        INSERT INTO market_data_gaps (
            provider,
            data_type,
            gap_start,
            gap_end,
            reason
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            "licensed-provider",
            "delayed-post-trade-equity",
            datetime(2026, 7, 29, 9, 46, tzinfo=timezone.utc),
            datetime(2026, 7, 29, 9, 46, tzinfo=timezone.utc),
            "test gap",
        ),
    )
    with pytest.raises(MarketDataError, match="gap"):
        db.require_operational_market_data(
            datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
        )


def test_authorized_index_change_uses_current_and_previous_sessions(
    clean_database,
):
    db = clean_database
    for session_date in (date(2026, 7, 28), date(2026, 7, 29)):
        db.upsert_market_session(
            MarketSession.for_local_date(
                mic="XSTO",
                session_date=session_date,
                opens_at="09:00",
                closes_at="17:30",
                timezone_name="Europe/Stockholm",
                source="test-calendar",
            )
        )
    db.execute(
        """
        INSERT INTO market_data_provider_contracts (
            contract_key,
            provider,
            product_name,
            data_type,
            mic,
            delivery_mode,
            transport,
            nominal_delay_seconds,
            max_transport_lag_seconds,
            usage_scope,
            non_display_category,
            external_distribution,
            reference_symbols_included,
            terms_url,
            status,
            valid_from,
            valid_until,
            governance_evidence_status,
            proposed_by
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            "licensed-index-xsto-v1",
            "licensed-index",
            "Licensed delayed OMXSGI",
            "delayed-index-level",
            "XSTO",
            "DELAYED_15M",
            "AUTHORIZED_VENDOR",
            900,
            30,
            "INTERNAL_ANALYSIS_AND_PAPER",
            "NONE",
            False,
            True,
            "https://example.test/index-terms",
            "DRAFT",
            date(2026, 7, 1),
            date(2026, 8, 31),
            "PENDING",
            "operator:test",
        ),
    )
    contract_id = db.query(
        """
        SELECT id
        FROM market_data_provider_contracts
        WHERE contract_key = :contract_key
        """,
        {"contract_key": "licensed-index-xsto-v1"},
    )[0]["id"]
    db.execute(
        """
        UPDATE market_data_provider_contracts
        SET
            status = 'VALIDATED',
            governance_evidence_status = 'VERIFIED',
            terms_checksum_sha256 = %s,
            raw_storage_allowed = TRUE,
            derived_storage_allowed = TRUE,
            retention_policy = 'Internal test retention.',
            legal_reviewed_at = '2026-07-01T00:00:00Z',
            transport_verified_at = '2026-07-01T00:00:00Z',
            reviewed_by = 'operator:test',
            updated_at = NOW()
        WHERE id = %s
        """,
        ("b" * 64, contract_id),
    )
    db.execute(
        """
        INSERT INTO market_data_provider_validations (
            contract_id,
            status,
            validated_at,
            valid_until,
            expected_instruments,
            product_covered_instruments,
            symbol_mapped_instruments,
            sample_file_count,
            sample_quote_count,
            max_observed_delivery_seconds,
            evidence_checksum_sha256,
            validated_by,
            governance_evidence_status,
            acceptance_session_count,
            first_session_date,
            last_session_date,
            acceptance_checksum_sha256
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        """,
        (
            contract_id,
            "PASSED",
            datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 5, tzinfo=timezone.utc),
            1,
            1,
            1,
            1,
            2,
            920,
            "d" * 64,
            "operator:test",
            "VERIFIED",
            5,
            date(2026, 7, 21),
            date(2026, 7, 25),
            "e" * 64,
        ),
    )
    levels = (
        IndexLevelRecord(
            contract_key="licensed-index-xsto-v1",
            symbol="OMXSGI",
            mic="XSTO",
            event_time=datetime(
                2026,
                7,
                28,
                15,
                29,
                tzinfo=timezone.utc,
            ),
            received_at=datetime(
                2026,
                7,
                28,
                15,
                44,
                tzinfo=timezone.utc,
            ),
            source="licensed-index-delayed",
            level=Decimal("250"),
            source_checksum_sha256="e" * 64,
        ),
        IndexLevelRecord(
            contract_key="licensed-index-xsto-v1",
            symbol="OMXSGI",
            mic="XSTO",
            event_time=datetime(
                2026,
                7,
                29,
                9,
                45,
                tzinfo=timezone.utc,
            ),
            received_at=datetime(
                2026,
                7,
                29,
                10,
                0,
                tzinfo=timezone.utc,
            ),
            source="licensed-index-delayed",
            level=Decimal("245"),
            source_checksum_sha256="f" * 64,
        ),
    )

    assert db.save_market_index_levels(levels) == 2
    assert db.save_market_index_levels(levels) == 0

    signal = db.get_current_authorized_index_change(
        datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
    )
    assert signal["symbol"] == "OMXSGI"
    assert signal["provider"] == "licensed-index"
    assert float(signal["current_level"]) == pytest.approx(245)
    assert float(signal["previous_close"]) == pytest.approx(250)
    assert float(signal["change_pct"]) == pytest.approx(-2)

    with pytest.raises(MarketDataError, match="stale"):
        db.get_current_authorized_index_change(
            datetime(2026, 7, 29, 10, 1, tzinfo=timezone.utc),
        )


def test_official_market_calendar_sync_is_atomic_and_idempotent(
    clean_database,
    connection,
):
    db = clean_database
    snapshot = nasdaq_stockholm_calendar(
        2026,
        verified_at=datetime(
            2026,
            7,
            29,
            14,
            0,
            tzinfo=timezone.utc,
        ),
    )

    first = db.sync_market_calendar(snapshot)
    duplicate = db.sync_market_calendar(snapshot)

    assert first.inserted is True
    assert first.sessions_upserted == len(snapshot.sessions)
    assert duplicate.inserted is False
    assert duplicate.sync_run_id == first.sync_run_id

    half_day = db.get_market_session("XSTO", date(2026, 1, 5))
    assert half_day is not None
    assert half_day.status == "HALF_DAY"
    assert half_day.closes_at.hour == 13
    assert db.get_market_session("XSTO", date(2026, 1, 6)) is None
    assert db.get_market_session("XSTO", date(2026, 7, 29)) is not None

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT session_count, closed_day_count, half_day_count
            FROM market_calendar_snapshots
            WHERE mic = 'XSTO' AND year = 2026 AND is_current
            """
        )
        assert cursor.fetchone() == (
            len(snapshot.sessions),
            len(snapshot.closed_dates),
            5,
        )


def test_active_instrument_universe_excludes_non_common_stock(
    clean_database,
):
    db = clean_database
    common_stock = InstrumentRecord(
        isin="SE0000115446",
        symbol="VOLV B",
        name="Volvo B",
        mic="XSTO",
        currency="SEK",
        instrument_type="COMMON_STOCK",
        source="test-reference",
    )
    etf = InstrumentRecord(
        isin="SE0010323311",
        symbol="TEST ETF",
        name="Test ETF",
        mic="XSTO",
        currency="SEK",
        instrument_type="ETF",
        source="test-reference",
    )
    db.upsert_instruments([common_stock, etf])

    assert db.get_active_instruments(mic="XSTO") == (common_stock,)


def test_full_reference_snapshot_is_idempotent_and_deactivates_missing(
    clean_database,
    connection,
):
    db = clean_database
    received_at = datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc)
    first_archive = MarketDataArchive.from_bytes(
        provider="esma-firds",
        data_type="full-equity-reference",
        file_name="FULINS_E_20260725_01of01.zip",
        report_minute=datetime(
            2026,
            7,
            25,
            tzinfo=timezone.utc,
        ),
        received_at=received_at,
        source_url=(
            "https://firds.esma.europa.eu/firds/"
            "FULINS_E_20260725_01of01.zip"
        ),
        content=b"complete verified snapshot one",
    )
    common_stock = InstrumentRecord(
        isin="SE0000115446",
        symbol=None,
        name="Volvo AB ser. B",
        mic="XSTO",
        currency="SEK",
        instrument_type="COMMON_STOCK",
        source="esma-firds",
        first_trade_date=date(1984, 1, 2),
        cfi_code="ESVUFR",
    )
    preferred_stock = InstrumentRecord(
        isin="SE0009143670",
        symbol=None,
        name="Volati AB Pref",
        mic="XSTO",
        currency="SEK",
        instrument_type="PREFERRED_STOCK",
        source="esma-firds",
        first_trade_date=date(2015, 6, 8),
        cfi_code="EPRNNR",
    )
    depositary_receipt = InstrumentRecord(
        isin="US0528001094",
        symbol=None,
        name="Autoliv Inc. SDB",
        mic="XSTO",
        currency="SEK",
        instrument_type="DEPOSITARY_RECEIPT",
        source="esma-firds",
        first_trade_date=date(2024, 2, 28),
        cfi_code="EDSXDR",
    )

    first = db.sync_instrument_snapshot(
        provider="esma-firds",
        mic="XSTO",
        snapshot_date=date(2026, 7, 25),
        received_at=received_at,
        archives=(first_archive,),
        instruments=(common_stock, preferred_stock),
    )
    duplicate = db.sync_instrument_snapshot(
        provider="esma-firds",
        mic="XSTO",
        snapshot_date=date(2026, 7, 25),
        received_at=received_at,
        archives=(first_archive,),
        instruments=(common_stock, preferred_stock),
    )

    assert first.inserted is True
    assert first.instruments_upserted == 2
    assert first.instruments_deactivated == 0
    assert duplicate.inserted is False
    assert duplicate.sync_run_id == first.sync_run_id
    assert {
        instrument.instrument_type
        for instrument in db.get_active_instruments(mic="XSTO")
    } == {"COMMON_STOCK", "PREFERRED_STOCK"}

    second_archive = MarketDataArchive.from_bytes(
        provider="esma-firds",
        data_type="full-equity-reference",
        file_name="FULINS_E_20260801_01of01.zip",
        report_minute=datetime(
            2026,
            8,
            1,
            tzinfo=timezone.utc,
        ),
        received_at=datetime(
            2026,
            8,
            3,
            7,
            0,
            tzinfo=timezone.utc,
        ),
        source_url=(
            "https://firds.esma.europa.eu/firds/"
            "FULINS_E_20260801_01of01.zip"
        ),
        content=b"complete verified snapshot two",
    )
    second = db.sync_instrument_snapshot(
        provider="esma-firds",
        mic="XSTO",
        snapshot_date=date(2026, 8, 1),
        received_at=datetime(
            2026,
            8,
            3,
            7,
            0,
            tzinfo=timezone.utc,
        ),
        archives=(second_archive,),
        instruments=(common_stock, depositary_receipt),
    )

    assert second.instruments_deactivated == 1
    assert db.get_latest_reference_snapshot_date(
        provider="esma-firds",
        mic="XSTO",
    ) == date(2026, 8, 1)
    assert set(db.get_active_instruments(mic="XSTO")) == {
        common_stock,
        depositary_receipt,
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                snapshot.snapshot_date,
                ARRAY_AGG(
                    membership.isin ORDER BY membership.isin
                )
            FROM reference_data_snapshots snapshot
            JOIN reference_snapshot_instruments membership
              ON membership.snapshot_id = snapshot.id
            GROUP BY snapshot.id, snapshot.snapshot_date
            ORDER BY snapshot.snapshot_date
            """
        )
        assert cursor.fetchall() == [
            (
                date(2026, 7, 25),
                sorted([common_stock.isin, preferred_stock.isin]),
            ),
            (
                date(2026, 8, 1),
                sorted([common_stock.isin, depositary_receipt.isin]),
            ),
        ]

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="snapshot instrument membership is append-only",
        ):
            cursor.execute(
                """
                UPDATE reference_snapshot_instruments
                SET isin = %s
                WHERE snapshot_id = (
                    SELECT id
                    FROM reference_data_snapshots
                    WHERE snapshot_date = %s
                )
                """,
                ("SE0000000001", date(2026, 7, 25)),
            )

        cursor.execute(
            "SELECT status FROM instruments WHERE isin = %s",
            (preferred_stock.isin,),
        )
        assert cursor.fetchone()[0] == "INACTIVE"


def test_nasdaq_alias_snapshot_is_atomic_idempotent_and_immutable(
    clean_database,
    connection,
):
    db = clean_database
    instrument = InstrumentRecord(
        isin="SE0000115446",
        symbol=None,
        name="Volvo AB ser. B",
        mic="XSTO",
        currency="EUR",
        instrument_type="COMMON_STOCK",
        source="esma-firds",
        first_trade_date=date(1984, 1, 2),
        cfi_code="ESVUFR",
    )
    archive = MarketDataArchive.from_bytes(
        provider="esma-firds",
        data_type="full-equity-reference",
        file_name="FULINS_E_20260729_01of01.zip",
        report_minute=datetime(2026, 7, 29, tzinfo=timezone.utc),
        received_at=datetime(
            2026,
            7,
            29,
            7,
            0,
            tzinfo=timezone.utc,
        ),
        source_url=(
            "https://firds.esma.europa.eu/firds/"
            "FULINS_E_20260729_01of01.zip"
        ),
        content=b"verified FIRDS alias universe",
    )
    db.sync_instrument_snapshot(
        provider="esma-firds",
        mic="XSTO",
        snapshot_date=date(2026, 7, 29),
        received_at=datetime(
            2026,
            7,
            29,
            7,
            0,
            tzinfo=timezone.utc,
        ),
        archives=(archive,),
        instruments=(instrument,),
    )
    reference_snapshot_id, reference = (
        db.get_latest_reference_universe(
            provider="esma-firds",
            mic="XSTO",
        )
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO reference_data_entitlements (
                contract_key,
                provider,
                product_name,
                mic,
                transport,
                usage_scope,
                external_distribution,
                raw_storage_allowed,
                derived_storage_allowed,
                retention_policy,
                terms_url,
                terms_checksum_sha256,
                status,
                valid_from,
                valid_until,
                legal_reviewed_at,
                storage_reviewed_at,
                entitlement_verified_at,
                host_key_algorithm,
                host_key_fingerprint_sha256,
                reviewed_by
            )
            VALUES (
                'nasdaq-nordic-reference-xsto-v1',
                'nasdaq-nordic',
                'Nordic Equity Reference Data Files',
                'XSTO',
                'SFTP',
                'INTERNAL_ANALYSIS_AND_PAPER',
                FALSE,
                TRUE,
                TRUE,
                'Internal retention permitted by agreement.',
                'https://example.com/agreement',
                %s,
                'VALIDATED',
                DATE '2026-07-01',
                DATE '2026-12-31',
                TIMESTAMPTZ '2026-07-01 00:00:00+00',
                TIMESTAMPTZ '2026-07-01 00:00:00+00',
                TIMESTAMPTZ '2026-07-01 00:00:00+00',
                'ssh-ed25519',
                %s,
                'operator:test'
            )
            RETURNING id
            """,
            ("a" * 64, "SHA256:" + "A" * 43),
        )
        entitlement_id = int(cursor.fetchone()[0])

    test_now = datetime.now(timezone.utc)
    entitlement = db.require_runtime_reference_entitlement(
        contract_key="nasdaq-nordic-reference-xsto-v1",
        provider="nasdaq-nordic",
        mic="XSTO",
        transport="SFTP",
        usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
        now=test_now,
    )
    assert entitlement.entitlement_id == entitlement_id

    def payload(symbol: str) -> bytes:
        return (
            "BDBu;Bd20260730;\n"
            "BDx;i10;SiXSTO;s1;SYmXSTO;"
            "NAmNasdaq Stockholm;CNySE;MIcXSTO;\n"
            "BDt;i101;SiVOLV-B;s1;Ex10;Mk20;INiVOLV-B;"
            f"SYm{symbol};NAmVolvo AB;ISnSE0000115446;"
            "CUiSEK;CUtSEK;LDa19840102;CFcESVUFR;PMIcXSTO;\n"
            "BDSh;i101;SiVOLV-B;s1;\n"
            "EOBd;s1;\n"
        ).encode("ascii")

    received_at = test_now
    source_path = (
        "INET_Ref_Data/20260730/Nordic_Equity_RefData.tip"
    )
    content = payload("VOLV B")
    snapshot = parse_nasdaq_reference_file(
        content,
        source_path=source_path,
        received_at=received_at,
        reference_snapshot=reference,
        provider="nasdaq-nordic",
        tip_version="3.10.17.1",
    )
    first = db.sync_instrument_alias_snapshot(
        entitlement_id=entitlement_id,
        reference_snapshot_id=reference_snapshot_id,
        snapshot=snapshot,
        content=content,
    )
    replay_snapshot = parse_nasdaq_reference_file(
        content,
        source_path=source_path,
        received_at=received_at + timedelta(minutes=5),
        reference_snapshot=reference,
        provider="nasdaq-nordic",
        tip_version="3.10.17.1",
    )
    duplicate = db.sync_instrument_alias_snapshot(
        entitlement_id=entitlement_id,
        reference_snapshot_id=reference_snapshot_id,
        snapshot=replay_snapshot,
        content=content,
    )

    assert first.inserted is True
    assert first.aliases_inserted == 1
    assert duplicate.inserted is False
    assert duplicate.snapshot_id == first.snapshot_id
    assert duplicate.sync_run_id == first.sync_run_id
    assert db.get_active_provider_aliases(
        provider="nasdaq-nordic",
        mic="XSTO",
    ) == snapshot.aliases

    quote = QuoteRecord(
        isin=instrument.isin,
        mic="XSTO",
        event_time=received_at - timedelta(minutes=15),
        received_at=received_at,
        source="nasdaq-nordic-delayed-post-trade",
        last_price="321.20",
        currency="SEK",
        volume="100",
    )
    assert db.save_market_quotes((quote,)) == 1

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                head.snapshot_id,
                member.isin,
                member.provider_symbol,
                snapshot.compressed_content,
                snapshot.entitlement_id
            FROM instrument_alias_snapshot_heads head
            JOIN instrument_alias_snapshots snapshot
              ON snapshot.id = head.snapshot_id
            JOIN instrument_alias_snapshot_members member
              ON member.snapshot_id = snapshot.id
            WHERE head.provider = 'nasdaq-nordic'
              AND head.mic = 'XSTO'
            """
        )
        row = cursor.fetchone()
        assert row[0] == first.snapshot_id
        assert row[1].strip() == instrument.isin
        assert row[2] == "VOLV B"
        assert gzip.decompress(bytes(row[3])) == content
        assert row[4] == entitlement_id

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="instrument alias evidence is append-only",
        ):
            cursor.execute(
                """
                UPDATE instrument_alias_snapshot_members
                SET provider_symbol = 'VOLV-B'
                WHERE snapshot_id = %s
                """,
                (first.snapshot_id,),
            )

    altered_content = payload("VOLV-B")
    altered_snapshot = parse_nasdaq_reference_file(
        altered_content,
        source_path=source_path,
        received_at=received_at + timedelta(minutes=10),
        reference_snapshot=reference,
        provider="nasdaq-nordic",
        tip_version="3.10.17.1",
    )
    with pytest.raises(
        MarketDataError,
        match="stored alias snapshot differs",
    ):
        db.sync_instrument_alias_snapshot(
            entitlement_id=entitlement_id,
            reference_snapshot_id=reference_snapshot_id,
            snapshot=altered_snapshot,
            content=altered_content,
        )

    newer_archive = MarketDataArchive.from_bytes(
        provider="esma-firds",
        data_type="full-equity-reference",
        file_name="FULINS_E_20260730_01of01.zip",
        report_minute=datetime(2026, 7, 30, tzinfo=timezone.utc),
        received_at=datetime(
            2026,
            7,
            30,
            7,
            0,
            tzinfo=timezone.utc,
        ),
        source_url=(
            "https://firds.esma.europa.eu/firds/"
            "FULINS_E_20260730_01of01.zip"
        ),
        content=b"newer verified FIRDS alias universe",
    )
    db.sync_instrument_snapshot(
        provider="esma-firds",
        mic="XSTO",
        snapshot_date=date(2026, 7, 30),
        received_at=datetime(
            2026,
            7,
            30,
            7,
            0,
            tzinfo=timezone.utc,
        ),
        archives=(newer_archive,),
        instruments=(instrument,),
    )
    with pytest.raises(MarketDataError, match="stale against FIRDS"):
        db.get_active_provider_aliases(
            provider="nasdaq-nordic",
            mic="XSTO",
        )

    with connection.cursor() as cursor:
        cursor.execute(
                """
                UPDATE reference_data_entitlements
                SET
                    status = 'REVOKED',
                    revoked_at = NOW(),
                    revoked_by = 'operator:test',
                    revocation_reason = 'Integration-test revocation.'
                WHERE id = %s
                """,
            (entitlement_id,),
        )
    with pytest.raises(MarketDataError, match="not validated"):
        db.require_runtime_reference_entitlement(
            contract_key="nasdaq-nordic-reference-xsto-v1",
            provider="nasdaq-nordic",
            mic="XSTO",
            transport="SFTP",
            usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
            now=test_now,
        )
    assert db.get_active_provider_aliases(
        provider="nasdaq-nordic",
        mic="XSTO",
    ) == ()


def test_reference_entitlement_admin_records_validation_and_revocation(
    clean_database,
    connection,
    tmp_path,
):
    from src.reference_entitlement_admin import (
        ReferenceEntitlementAdmin,
        approval_from_files,
    )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    manifest_path = tmp_path / "approval.json"
    manifest_path.write_text(
        json.dumps({
            "contract_key": "nasdaq-nordic-reference-xsto-v1",
            "provider": "nasdaq-nordic",
            "product_name": "Nordic Equity Reference Data Files",
            "mic": "XSTO",
            "retention_policy": (
                "Internal retention permitted by agreement."
            ),
            "terms_url": "https://example.com/agreement",
            "valid_from": (now.date() - timedelta(days=1)).isoformat(),
            "valid_until": (now.date() + timedelta(days=30)).isoformat(),
            "host_key_algorithm": "ssh-ed25519",
            "host_key_fingerprint_sha256": "SHA256:" + "A" * 43,
        }),
        encoding="utf-8",
    )
    terms_path = tmp_path / "agreement.pdf"
    terms_path.write_bytes(b"reviewed agreement bytes")
    approval = approval_from_files(
        evidence_json_path=str(manifest_path),
        terms_file_path=str(terms_path),
        reviewed_by="operator:diffen",
        reviewed_at=now,
    )
    admin = ReferenceEntitlementAdmin(
        TEST_DATABASE_URL,
        clock=lambda: now,
    )

    validated = admin.validate_reference_entitlement(approval)

    assert validated["status"] == "VALIDATED"
    assert validated["contract_key"] == approval.contract_key
    assert validated["reviewed_by"] == "operator:diffen"
    assert clean_database.require_runtime_reference_entitlement(
        contract_key=approval.contract_key,
        provider="nasdaq-nordic",
        mic="XSTO",
        transport="SFTP",
        usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
        now=now,
    ).contract_key == approval.contract_key

    with pytest.raises(ValueError, match="already exists"):
        admin.validate_reference_entitlement(approval)

    revoked = admin.revoke_reference_entitlement(
        approval.contract_key,
        revoked_by="operator:diffen",
        reason="Contract terminated by the operator.",
    )

    assert revoked["status"] == "REVOKED"
    assert revoked["revoked_by"] == "operator:diffen"
    assert revoked["revocation_reason"] == (
        "Contract terminated by the operator."
    )
    assert revoked["revoked_at"] == now
    with pytest.raises(MarketDataError, match="not validated"):
        clean_database.require_runtime_reference_entitlement(
            contract_key=approval.contract_key,
            provider="nasdaq-nordic",
            mic="XSTO",
            transport="SFTP",
            usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
            now=now,
        )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                status,
                revoked_by,
                revocation_reason,
                revoked_at
            FROM reference_data_entitlements
            WHERE contract_key = %s
            """,
            (approval.contract_key,),
        )
        assert cursor.fetchone() == (
            "REVOKED",
            "operator:diffen",
            "Contract terminated by the operator.",
            now,
        )


def test_market_data_file_ingestion_is_atomic_and_idempotent(
    clean_database,
    connection,
):
    db = clean_database
    instrument = InstrumentRecord(
        isin="SE0000115446",
        symbol="VOLV B",
        name="Volvo B",
        mic="XSTO",
        currency="SEK",
        instrument_type="COMMON_STOCK",
        source="test-reference",
    )
    db.upsert_instruments([instrument])
    report_minute = datetime(
        2026,
        7,
        29,
        12,
        56,
        tzinfo=timezone.utc,
    )
    received_at = datetime(
        2026,
        7,
        29,
        13,
        13,
        tzinfo=timezone.utc,
    )
    archive = MarketDataArchive.from_bytes(
        provider="nasdaq-nordic",
        data_type="delayed-post-trade-equity",
        file_name="NordicEquity-posttrade-2026-07-29T1456",
        report_minute=report_minute,
        received_at=received_at,
        source_url=(
            "https://tradereports.nasdaq.com/api/regulatory/"
            "trade-report/download?type=POST_TRADE"
        ),
        content=b"verified-public-csv",
    )
    quote = QuoteRecord(
        isin=instrument.isin,
        mic=instrument.mic,
        event_time=report_minute,
        received_at=received_at,
        source="nasdaq-nordic-test-post-trade",
        last_price=Decimal("321.50"),
        currency="SEK",
        volume=Decimal("100"),
    )
    gap = MarketDataGap(
        provider=archive.provider,
        data_type=archive.data_type,
        gap_start=report_minute - timedelta(minutes=1),
        gap_end=report_minute - timedelta(minutes=1),
        reason="missing report minute on Nasdaq report page",
    )

    first = db.ingest_market_data_file(archive, [quote], [gap])
    second = db.ingest_market_data_file(archive, [quote], [gap])

    assert first.inserted is True
    assert first.quotes_inserted == 1
    assert second.inserted is False
    assert second.quotes_inserted == 0
    assert second.sync_run_id == first.sync_run_id

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM market_data_files),
                (SELECT COUNT(*) FROM market_quotes),
                (SELECT COUNT(*) FROM market_data_gaps),
                (SELECT COUNT(*) FROM data_sync_runs),
                (SELECT status FROM data_sync_runs LIMIT 1),
                (
                    SELECT market_data_files.file_name
                    FROM market_quotes
                    JOIN market_data_files
                        ON market_data_files.id = market_quotes.data_file_id
                    LIMIT 1
                )
            """
        )
        counts = cursor.fetchone()
    assert counts == (
        1,
        1,
        1,
        1,
        "SUCCEEDED",
        archive.file_name,
    )


def test_market_data_provider_requires_current_passed_evidence(
    clean_database,
    connection,
):
    db = clean_database
    now = datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc)

    with pytest.raises(MarketDataError, match="missing"):
        db.require_runtime_market_data_provider(
            contract_key="nasdaq-nordic-delayed-xsto-v1",
            provider="nasdaq-nordic",
            data_type="delayed-post-trade-equity",
            mic="XSTO",
            delivery_mode="DELAYED_15M",
            transport="PUBLIC_CSV",
            usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
            now=now,
            expected_instruments=1,
        )

    reference_archive = MarketDataArchive.from_bytes(
        provider="test-reference",
        data_type="full-equity-reference",
        file_name="provider-gate-reference.csv.gz",
        report_minute=now - timedelta(hours=1),
        received_at=now - timedelta(minutes=30),
        source_url="https://example.test/provider-gate-reference.csv.gz",
        content=b"provider gate reference",
    )
    db.sync_instrument_snapshot(
        provider="test-reference",
        mic="XSTO",
        snapshot_date=date(2026, 7, 29),
        received_at=now - timedelta(minutes=30),
        archives=(reference_archive,),
        instruments=(_pretrade_instrument(),),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, universe_checksum_sha256
            FROM reference_data_snapshots
            WHERE provider = 'test-reference' AND mic = 'XSTO'
            ORDER BY snapshot_date DESC, id DESC
            LIMIT 1
            """
        )
        reference_snapshot_id, reference_checksum = cursor.fetchone()
        cursor.execute(
            """
            INSERT INTO market_data_provider_contracts (
                contract_key, provider, product_name, data_type, mic,
                delivery_mode, transport, nominal_delay_seconds,
                max_transport_lag_seconds, usage_scope,
                non_display_category, external_distribution,
                reference_symbols_included, terms_url, status,
                valid_from, valid_until, governance_evidence_status,
                proposed_by
            )
            VALUES (
                'nasdaq-nordic-delayed-xsto-v1',
                'nasdaq-nordic',
                'Nasdaq Nordic Equity Level 1 delayed',
                'delayed-post-trade-equity',
                'XSTO',
                'DELAYED_15M',
                'PUBLIC_CSV',
                900,
                30,
                'INTERNAL_ANALYSIS_AND_PAPER',
                'NONE',
                FALSE,
                FALSE,
                'https://www.nasdaq.com/docs/Nasdaq_European_Data_Policies_April_2026',
                'DRAFT',
                '2026-04-01',
                '2026-08-31',
                'PENDING',
                'operator:test'
            )
            RETURNING id
            """,
        )
        contract_id = cursor.fetchone()[0]
        cursor.execute(
            """
            UPDATE market_data_provider_contracts
            SET
                status = 'VALIDATED',
                governance_evidence_status = 'VERIFIED',
                terms_checksum_sha256 = %s,
                raw_storage_allowed = TRUE,
                derived_storage_allowed = TRUE,
                retention_policy = 'Internal test retention.',
                legal_reviewed_at = %s,
                transport_verified_at = %s,
                reviewed_by = 'operator:test',
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                "b" * 64,
                now - timedelta(days=1),
                now - timedelta(hours=1),
                contract_id,
            ),
        )

    with pytest.raises(MarketDataError, match="evidence"):
        db.require_runtime_market_data_provider(
            contract_key="nasdaq-nordic-delayed-xsto-v1",
            provider="nasdaq-nordic",
            data_type="delayed-post-trade-equity",
            mic="XSTO",
            delivery_mode="DELAYED_15M",
            transport="PUBLIC_CSV",
            usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
            now=now,
            expected_instruments=1,
        )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO market_data_provider_validations (
                contract_id, status, validated_at, valid_until,
                expected_instruments, product_covered_instruments,
                symbol_mapped_instruments, sample_file_count,
                sample_quote_count, max_observed_delivery_seconds,
                evidence_checksum_sha256, validated_by,
                governance_evidence_status, reference_snapshot_id,
                reference_checksum_sha256, acceptance_session_count,
                first_session_date, last_session_date,
                acceptance_checksum_sha256
            )
            VALUES (
                %s, 'PASSED', %s, %s,
                1, 1, 1, 3, 20, 918, %s, 'operator:test',
                'VERIFIED', %s, %s, 5,
                '2026-07-21', '2026-07-25', %s
            )
            """,
            (
                contract_id,
                now - timedelta(minutes=10),
                now + timedelta(days=7),
                "a" * 64,
                reference_snapshot_id,
                reference_checksum,
                "c" * 64,
            ),
        )

    contract = db.require_runtime_market_data_provider(
        contract_key="nasdaq-nordic-delayed-xsto-v1",
        provider="nasdaq-nordic",
        data_type="delayed-post-trade-equity",
        mic="XSTO",
        delivery_mode="DELAYED_15M",
        transport="PUBLIC_CSV",
        usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
        now=now,
        expected_instruments=1,
    )

    assert contract.provider == "nasdaq-nordic"
    assert contract.delivery_mode == "DELAYED_15M"


def test_market_data_file_rejects_same_name_with_different_checksum(
    clean_database,
    connection,
):
    db = clean_database
    report_minute = datetime(
        2026,
        7,
        29,
        12,
        56,
        tzinfo=timezone.utc,
    )
    received_at = datetime(
        2026,
        7,
        29,
        13,
        13,
        tzinfo=timezone.utc,
    )
    values = {
        "provider": "nasdaq-nordic",
        "data_type": "delayed-post-trade-equity",
        "file_name": "NordicEquity-posttrade-2026-07-29T1456",
        "report_minute": report_minute,
        "received_at": received_at,
        "source_url": (
            "https://tradereports.nasdaq.com/api/regulatory/"
            "trade-report/download?type=POST_TRADE"
        ),
    }
    original = MarketDataArchive.from_bytes(
        **values,
        content=b"original-public-csv",
    )
    changed = MarketDataArchive.from_bytes(
        **values,
        content=b"changed-public-csv",
    )
    db.ingest_market_data_file(original, [], [])

    with pytest.raises(MarketDataError, match="checksum"):
        db.ingest_market_data_file(changed, [], [])

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT checksum_sha256, COUNT(*)
            FROM market_data_files
            GROUP BY checksum_sha256
            """
        )
        stored_checksum, count = cursor.fetchone()
    assert stored_checksum == original.checksum_sha256
    assert count == 1


def test_market_data_gap_resolves_only_after_every_minute_is_backfilled(
    clean_database,
    connection,
):
    db = clean_database
    received_at = datetime(
        2026,
        7,
        29,
        13,
        13,
        tzinfo=timezone.utc,
    )

    def archive(minute, local_minute):
        return MarketDataArchive.from_bytes(
            provider="nasdaq-nordic",
            data_type="delayed-post-trade-equity",
            file_name=(
                f"NordicEquity-posttrade-2026-07-29T{local_minute}"
            ),
            report_minute=minute,
            received_at=received_at,
            source_url=(
                "https://tradereports.nasdaq.com/api/regulatory/"
                f"trade-report/download?fileName={local_minute}"
            ),
            content=f"public-csv-{local_minute}".encode(),
        )

    minute_1255 = datetime(
        2026,
        7,
        29,
        12,
        55,
        tzinfo=timezone.utc,
    )
    minute_1256 = minute_1255 + timedelta(minutes=1)
    minute_1257 = minute_1256 + timedelta(minutes=1)
    gap = MarketDataGap(
        provider="nasdaq-nordic",
        data_type="delayed-post-trade-equity",
        gap_start=minute_1255,
        gap_end=minute_1256,
        reason="missing report minutes on Nasdaq report page",
    )

    db.ingest_market_data_file(archive(minute_1257, "1457"), [], [gap])
    db.ingest_market_data_file(archive(minute_1255, "1455"), [], [])

    with connection.cursor() as cursor:
        cursor.execute("SELECT status FROM market_data_gaps")
        assert cursor.fetchone()[0] == "OPEN"

    db.ingest_market_data_file(archive(minute_1256, "1456"), [], [])

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, resolved_at IS NOT NULL FROM market_data_gaps"
        )
        assert cursor.fetchone() == ("RESOLVED", True)


def test_failed_market_data_sync_is_audited_without_a_data_file(
    clean_database,
    connection,
):
    db = clean_database
    started_at = datetime(
        2026,
        7,
        29,
        13,
        12,
        tzinfo=timezone.utc,
    )
    finished_at = started_at + timedelta(seconds=20)

    sync_run_id = db.record_data_sync_failure(
        provider="nasdaq-nordic",
        data_type="delayed-post-trade-equity",
        started_at=started_at,
        finished_at=finished_at,
        error_message="Nasdaq delayed data request failed",
    )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                id, status, data_file_id, records_received,
                records_inserted, error_message
            FROM data_sync_runs
            WHERE id = %s
            """,
            (sync_run_id,),
        )
        row = cursor.fetchone()
    assert row == (
        sync_run_id,
        "FAILED",
        None,
        0,
        0,
        "Nasdaq delayed data request failed",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"shares": -1},
        {"price": 0},
        {"total_value": -100},
        {"action": "SHORT"},
        {"confidence": 101},
    ],
)
def test_invalid_trade_is_rejected_before_any_write(clean_database, changes):
    db = clean_database
    trade = {
        "ticker": "VOLV-B",
        "action": "BUY",
        "shares": 10,
        "price": 100,
        "total_value": 1000,
        "reasoning": "Test buy",
        "confidence": 75,
        "hypothesis": "Test",
        "macro_context": {},
        "target_price": 110,
        "stop_loss": 95,
        "target_pct": 10,
        "stop_loss_pct": -5,
        "idempotency_key": "test-invalid-trade",
    }
    trade.update(changes)

    with pytest.raises(ValueError):
        db.log_trade(trade)

    assert db.get_balance()["cash"] == pytest.approx(20000)
    assert db.get_trades(limit=10).empty


def _reset_fifo_property_ledger(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            TRUNCATE
                trade_lot_allocations,
                position_lots,
                portfolio,
                trades,
                balance
            RESTART IDENTITY CASCADE;
            INSERT INTO balance (cash, total_value)
            VALUES (20000, 20000);
            """
        )


@pytest.fixture()
def fifo_property_ledger(clean_database, connection):
    try:
        yield clean_database, connection
    finally:
        _reset_fifo_property_ledger(connection)


@st.composite
def _fifo_trade_sequences(draw):
    buys = draw(
        st.lists(
            st.tuples(
                st.integers(min_value=1, max_value=5),
                st.decimals(
                    min_value="10",
                    max_value="250",
                    places=2,
                    allow_nan=False,
                    allow_infinity=False,
                ),
            ),
            min_size=1,
            max_size=4,
        )
    )
    total_shares = sum(shares for shares, _price in buys)
    sold_shares = draw(
        st.integers(min_value=1, max_value=total_shares),
    )
    sell_price = draw(
        st.decimals(
            min_value="5",
            max_value="400",
            places=2,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    return buys, sold_shares, sell_price


@given(sequence=_fifo_trade_sequences())
@settings(
    max_examples=35,
    deadline=None,
    # Pytest fixtures are reused by Hypothesis. The body resets before
    # every example and fifo_property_ledger cleans again at teardown.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_fifo_ledger_preserves_cash_shares_and_realized_pnl_properties(
    fifo_property_ledger,
    sequence,
):
    db, connection = fifo_property_ledger
    buys, sold_shares, sell_price = sequence
    _reset_fifo_property_ledger(connection)

    total_buy_cost = Decimal("0.00")
    for index, (shares, price) in enumerate(buys):
        total_value = (Decimal(shares) * price).quantize(
            Decimal("0.01"),
        )
        total_buy_cost += total_value
        db.log_trade(
            {
                "ticker": "VOLV-B",
                "action": "BUY",
                "shares": shares,
                "price": price,
                "total_value": total_value,
                "reasoning": "Generated FIFO buy",
                "confidence": 75,
                "hypothesis": "Ledger property",
                "macro_context": {},
                "target_price": price * Decimal("1.10"),
                "stop_loss": price * Decimal("0.95"),
                "target_pct": 10,
                "stop_loss_pct": -5,
                "idempotency_key": f"property-fifo-buy-{index}",
            }
        )

    sell_value = (Decimal(sold_shares) * sell_price).quantize(
        Decimal("0.01"),
    )
    sell_id = db.log_trade(
        {
            "ticker": "VOLV-B",
            "action": "SELL",
            "shares": sold_shares,
            "price": sell_price,
            "total_value": sell_value,
            "reasoning": "Generated FIFO sell",
            "confidence": 80,
            "hypothesis": "Ledger property",
            "macro_context": {},
            "target_price": None,
            "stop_loss": None,
            "target_pct": 0,
            "stop_loss_pct": 0,
            "idempotency_key": "property-fifo-sell",
        }
    )

    shares_left_to_allocate = Decimal(sold_shares)
    expected_pnl = Decimal("0.00")
    for shares, entry_price in buys:
        allocated = min(Decimal(shares), shares_left_to_allocate)
        expected_pnl += allocated * (sell_price - entry_price)
        shares_left_to_allocate -= allocated
        if shares_left_to_allocate == 0:
            break
    expected_pnl = expected_pnl.quantize(Decimal("0.01"))

    total_bought_shares = Decimal(
        sum(shares for shares, _price in buys),
    )
    expected_remaining_shares = (
        total_bought_shares - Decimal(sold_shares)
    )
    expected_cash = (
        Decimal("20000.00") - total_buy_cost + sell_value
    )

    balance = db.query("SELECT cash FROM balance")[0]
    lots = db.query(
        """
        SELECT
            COALESCE(SUM(remaining_shares), 0) AS shares,
            COALESCE(MIN(remaining_shares), 0) AS minimum_shares
        FROM position_lots
        """
    )[0]
    allocations = db.query(
        """
        SELECT
            COALESCE(SUM(shares), 0) AS shares,
            COALESCE(SUM(realized_pnl), 0) AS realized_pnl
        FROM trade_lot_allocations
        WHERE sell_trade_id = :sell_id
        """,
        {"sell_id": sell_id},
    )[0]
    sell = db.query(
        "SELECT pnl FROM trades WHERE id = :sell_id",
        {"sell_id": sell_id},
    )[0]
    portfolio = db.query(
        "SELECT shares FROM portfolio WHERE ticker = 'VOLV-B'",
    )

    assert Decimal(balance["cash"]) == expected_cash
    assert Decimal(lots["shares"]) == expected_remaining_shares
    assert Decimal(lots["minimum_shares"]) >= 0
    assert Decimal(allocations["shares"]) == Decimal(sold_shares)
    assert Decimal(allocations["realized_pnl"]) == expected_pnl
    assert Decimal(sell["pnl"]) == expected_pnl
    if expected_remaining_shares == 0:
        assert portfolio == []
    else:
        assert Decimal(portfolio[0]["shares"]) == (
            expected_remaining_shares
        )


def test_pretrade_batch_persists_and_reloads_exactly_once(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    batch = _pretrade_batch()
    snapshot = apply_top_of_book_batch(previous=(), batch=batch)

    inserted = db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=batch,
        snapshot=snapshot,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE instruments
            SET name = 'Volvo renamed after snapshot'
            WHERE isin = 'SE0000115446'
            """
        )
    replayed = db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=batch,
        snapshot=snapshot,
    )
    reloaded = db.load_latest_top_of_book_snapshot(
        contract_key=_PRETRADE_CONTRACT_KEY,
    )

    assert inserted.inserted is True
    assert inserted.updates_inserted == 2
    assert inserted.states_inserted == 1
    assert replayed.batch_id == inserted.batch_id
    assert replayed.inserted is False
    assert replayed.updates_inserted == 0
    assert replayed.states_inserted == 0
    assert reloaded == snapshot
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM pre_trade_batches),
                (SELECT COUNT(*) FROM pre_trade_updates),
                (SELECT COUNT(*) FROM pre_trade_stream_cursors),
                (SELECT COUNT(*) FROM pre_trade_book_states)
            """
        )
        assert cursor.fetchone() == (1, 2, 1, 1)
        cursor.execute(
            """
            SELECT name
            FROM reference_snapshot_instruments
            WHERE snapshot_id = %s
            """,
            (reference_snapshot_id,),
        )
        assert cursor.fetchone()[0] == "Volvo B"


def test_public_pretrade_authorization_accepts_15_minute_delay_and_maps_isin(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    loaded_snapshot_id, reference = db.get_latest_reference_universe(
        provider="test-reference",
        mic="XSTO",
    )
    assert loaded_snapshot_id == reference_snapshot_id
    received_at = datetime.now(timezone.utc).replace(
        second=0,
        microsecond=0,
    )
    batch = _pretrade_batch(
        reference=reference,
        report_minute=received_at - timedelta(minutes=15),
        received_at=received_at,
    )
    mapped = db.ensure_isin_company_mappings(
        reference_snapshot_id=reference_snapshot_id,
        reference_snapshot=reference,
    )
    policy_evidence = NasdaqPublicPolicyEvidence(
        terms_url=(
            "https://tradereports.nasdaq.com/shares/"
            "trade-reports/pre-trade"
        ),
        checksum_sha256="a" * 64,
        verified_at=received_at - timedelta(seconds=1),
    )
    preregistered_key = db.ensure_public_pretrade_contract(
        policy_evidence=policy_evidence,
    )
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*)
            FROM market_data_provider_validations validation
            JOIN market_data_provider_contracts contract
              ON contract.id = validation.contract_id
            WHERE contract.contract_key = %s
        """, (preregistered_key,))
        assert cursor.fetchone()[0] == 0
    contract_key = db.ensure_public_pretrade_authorization(
        reference_snapshot_id=reference_snapshot_id,
        batch=batch,
        policy_evidence=policy_evidence,
    )
    snapshot = apply_top_of_book_batch(previous=(), batch=batch)
    persisted = db.persist_top_of_book_batch(
        contract_key=contract_key,
        reference_snapshot_id=reference_snapshot_id,
        batch=batch,
        snapshot=snapshot,
    )

    assert mapped == 1
    assert contract_key == "nasdaq-public-pretrade-xsto-v1"
    assert persisted.inserted is True
    assert db.get_authorized_market_data_mode(received_at) == {
        "provider": "nasdaq-nordic",
        "data_type": "delayed-pre-trade-equity",
        "usage_scope": "INTERNAL_ANALYSIS_AND_PAPER",
        "authorization_basis": "PUBLIC_NONCOMMERCIAL_TERMS",
        "nominal_delay_seconds": 900,
        "max_transport_lag_seconds": 300,
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT company.ticker, company.description
            FROM companies company
            JOIN instruments instrument
              ON instrument.id = company.instrument_id
            WHERE instrument.isin = 'SE0000115446'
            """
        )
        ticker, description = cursor.fetchone()
    assert ticker == "SE0000115446"
    assert "Internal ISIN key" in description


def test_pretrade_replay_rejects_changed_payload(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    original = _pretrade_batch()
    original_snapshot = apply_top_of_book_batch(
        previous=(),
        batch=original,
    )
    db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=original,
        snapshot=original_snapshot,
    )
    changed = _pretrade_batch(bid_price="321.20")
    changed_snapshot = apply_top_of_book_batch(
        previous=(),
        batch=changed,
    )

    with pytest.raises(MarketDataError, match="conflicts"):
        db.persist_top_of_book_batch(
            contract_key=_PRETRADE_CONTRACT_KEY,
            reference_snapshot_id=reference_snapshot_id,
            batch=changed,
            snapshot=changed_snapshot,
        )


def test_pretrade_restart_preserves_an_empty_minute_cursor(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    first_batch = _pretrade_batch()
    first_snapshot = apply_top_of_book_batch(
        previous=(),
        batch=first_batch,
    )
    db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=first_batch,
        snapshot=first_snapshot,
    )

    restarted = Database()
    restored = restarted.load_latest_top_of_book_snapshot(
        contract_key=_PRETRADE_CONTRACT_KEY,
    )
    second_minute = first_batch.report_minute + timedelta(minutes=1)
    empty_batch = _pretrade_batch(
        report_minute=second_minute,
        received_at=first_batch.received_at + timedelta(minutes=1),
        empty_xsto=True,
    )
    empty_snapshot = apply_top_of_book_batch(
        previous=restored,
        batch=empty_batch,
    )
    restarted.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=empty_batch,
        snapshot=empty_snapshot,
    )

    second_restart = Database()
    reloaded = second_restart.load_latest_top_of_book_snapshot(
        contract_key=_PRETRADE_CONTRACT_KEY,
    )
    assert reloaded == empty_snapshot
    assert reloaded is not None
    assert reloaded.cursor.covered_through == (
        first_batch.covered_through + timedelta(minutes=1)
    )
    assert len(reloaded.states) == 1


def test_pretrade_persistence_rejects_a_stream_gap(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    first_batch = _pretrade_batch()
    first_snapshot = apply_top_of_book_batch(
        previous=(),
        batch=first_batch,
    )
    db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=first_batch,
        snapshot=first_snapshot,
    )
    gap_batch = _pretrade_batch(
        report_minute=first_batch.report_minute + timedelta(minutes=2),
        received_at=first_batch.received_at + timedelta(minutes=2),
    )
    independently_reduced = apply_top_of_book_batch(
        previous=(),
        batch=gap_batch,
    )

    with pytest.raises(MarketDataError, match="gap"):
        db.persist_top_of_book_batch(
            contract_key=_PRETRADE_CONTRACT_KEY,
            reference_snapshot_id=reference_snapshot_id,
            batch=gap_batch,
            snapshot=independently_reduced,
        )


def test_pretrade_stream_reset_reseeds_after_an_audited_gap(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    first_batch = _pretrade_batch()
    first_snapshot = apply_top_of_book_batch(
        previous=(),
        batch=first_batch,
    )
    first_result = db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=first_batch,
        snapshot=first_snapshot,
    )
    gap_batch = _pretrade_batch(
        report_minute=first_batch.report_minute + timedelta(minutes=2),
        received_at=first_batch.received_at + timedelta(minutes=2),
    )
    reseeded = apply_top_of_book_batch(
        previous=(),
        batch=gap_batch,
    )

    reset_id = db.authorize_top_of_book_stream_reset(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=gap_batch,
    )
    persisted = db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=gap_batch,
        snapshot=reseeded,
        stream_reset_id=reset_id,
    )
    reloaded = db.load_latest_top_of_book_snapshot(
        contract_key=_PRETRADE_CONTRACT_KEY,
    )

    assert persisted.inserted is True
    assert reloaded == reseeded
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                reset.previous_batch_id,
                reset.next_report_minute,
                reset.reason,
                batch.stream_reset_id
            FROM pre_trade_stream_resets reset
            JOIN pre_trade_batches batch
              ON batch.stream_reset_id = reset.id
            WHERE reset.id = %s
            """,
            (reset_id,),
        )
        assert cursor.fetchone() == (
            first_result.batch_id,
            gap_batch.report_minute,
            "STALE_CURSOR_RECOVERY",
            reset_id,
        )


def test_pretrade_schema_rejects_a_direct_stream_gap(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    first_batch = _pretrade_batch()
    first_snapshot = apply_top_of_book_batch(
        previous=(),
        batch=first_batch,
    )
    persisted = db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=first_batch,
        snapshot=first_snapshot,
    )

    with connection.cursor() as cursor:
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="stream continuity",
        ):
            cursor.execute(
                """
                INSERT INTO pre_trade_batches (
                    provider_contract_id,
                    provider_validation_id,
                    reference_snapshot_id,
                    provider,
                    data_type,
                    source,
                    mic,
                    file_name,
                    source_url,
                    source_checksum_sha256,
                    reference_checksum_sha256,
                    payload_checksum_sha256,
                    report_minute,
                    covered_through,
                    received_at,
                    input_row_count,
                    update_count,
                    state_count
                )
                SELECT
                    provider_contract_id,
                    provider_validation_id,
                    reference_snapshot_id,
                    provider,
                    data_type,
                    source,
                    mic,
                    'direct-gap.csv',
                    source_url,
                    %s,
                    reference_checksum_sha256,
                    %s,
                    report_minute + INTERVAL '2 minutes',
                    covered_through + INTERVAL '2 minutes',
                    received_at + INTERVAL '2 minutes',
                    input_row_count,
                    update_count,
                    state_count
                FROM pre_trade_batches
                WHERE id = %s
                """,
                ("d" * 64, "f" * 64, persisted.batch_id),
            )


def test_pretrade_fill_rejects_a_previous_sealed_minute(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    second_received_at = datetime.now(timezone.utc).replace(
        second=0,
        microsecond=0,
    )
    first_received_at = second_received_at - timedelta(minutes=1)
    first_batch = _pretrade_batch(
        report_minute=first_received_at - timedelta(minutes=15),
        received_at=first_received_at,
        ask_quantity="2",
    )
    first_snapshot = apply_top_of_book_batch(
        previous=(),
        batch=first_batch,
    )
    first_result = db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=first_batch,
        snapshot=first_snapshot,
    )
    _install_open_xsto_session(connection, at=second_received_at)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT state.id, state.instrument_id
            FROM pre_trade_book_states state
            WHERE state.batch_id = %s
            """,
            (first_result.batch_id,),
        )
        old_state_id, instrument_id = (
            int(value) for value in cursor.fetchone()
        )
        cursor.execute(
            """
            INSERT INTO companies (
                ticker,
                name,
                sector,
                instrument_id
            )
            VALUES ('VOLV-B', 'Volvo B', 'Industrials', %s)
            """,
            (instrument_id,),
        )
        cursor.execute(
            """
            INSERT INTO trades (
                ticker,
                action,
                shares,
                price,
                total_value,
                reasoning,
                idempotency_key,
                request_fingerprint,
                strategy_version,
                source_book_state_id,
                decision_origin,
                executed_at
            )
            VALUES (
                'VOLV-B',
                'BUY',
                1,
                321.30,
                321.30,
                'Historical first-minute fill',
                'book-fill-first-minute-raw',
                %s,
                'legacy-unversioned',
                %s,
                'LEGACY',
                %s
            )
            """,
            (
                "c" * 64,
                old_state_id,
                first_received_at + timedelta(seconds=10),
            ),
        )
    second_batch = _pretrade_batch(
        report_minute=first_batch.report_minute + timedelta(minutes=1),
        received_at=second_received_at,
        empty_xsto=True,
    )
    second_snapshot = apply_top_of_book_batch(
        previous=first_snapshot,
        batch=second_batch,
    )
    second_result = db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=second_batch,
        snapshot=second_snapshot,
    )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT state.id
            FROM pre_trade_book_states state
            WHERE state.batch_id = %s
            """,
            (second_result.batch_id,),
        )
        latest_state_id = int(cursor.fetchone()[0])

    stale_trade = {
        "ticker": "VOLV-B",
        "action": "BUY",
        "shares": 1,
        "price": Decimal("321.30"),
        "total_value": Decimal("321.30"),
        "reasoning": "Old sealed minute must not execute",
        "confidence": 80,
        "hypothesis": "Latest seal wins",
        "macro_context": {},
        "target_price": Decimal("350"),
        "stop_loss": Decimal("300"),
        "target_pct": 10,
        "stop_loss_pct": -5,
        "idempotency_key": "book-fill-previous-sealed-minute",
        "source_book_state_id": old_state_id,
    }
    with pytest.raises(ValueError, match="latest sealed"):
        db.log_trade(stale_trade)

    latest_trade = {
        **stale_trade,
        "idempotency_key": "book-fill-latest-sealed-minute",
        "source_book_state_id": latest_state_id,
    }
    assert db.log_trade(latest_trade) > 0

    reuses_carried_side = {
        **latest_trade,
        "idempotency_key": "book-fill-reuses-carried-side",
    }
    with pytest.raises(ValueError, match="remaining executable"):
        db.log_trade(reuses_carried_side)


def test_pretrade_schema_rejects_superseded_validation(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    batch = _pretrade_batch()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                contract.id,
                validation.id,
                snapshot.universe_checksum_sha256
            FROM market_data_provider_contracts contract
            JOIN market_data_provider_validations validation
              ON validation.contract_id = contract.id
            JOIN reference_data_snapshots snapshot
              ON snapshot.id = %s
            WHERE contract.contract_key = %s
            """,
            (reference_snapshot_id, _PRETRADE_CONTRACT_KEY),
        )
        contract_id, old_validation_id, reference_checksum = cursor.fetchone()
        contract_id = int(contract_id)
        old_validation_id = int(old_validation_id)
        cursor.execute(
            """
            INSERT INTO market_data_provider_validations (
                contract_id,
                status,
                validated_at,
                valid_until,
                expected_instruments,
                product_covered_instruments,
                symbol_mapped_instruments,
                sample_file_count,
                sample_quote_count,
                max_observed_delivery_seconds,
                evidence_checksum_sha256,
                validated_by,
                notes,
                governance_evidence_status,
                reference_snapshot_id,
                reference_checksum_sha256,
                acceptance_session_count,
                first_session_date,
                last_session_date,
                acceptance_checksum_sha256
            )
            VALUES (
                %s,
                'PASSED',
                '2026-07-28T01:00:00Z',
                '2026-08-31T00:00:00Z',
                1,
                1,
                1,
                5,
                10,
                905,
                %s,
                'operator:test',
                'Superseding passed evidence',
                'VERIFIED',
                %s,
                %s,
                5,
                '2026-07-21',
                '2026-07-25',
                %s
            )
            """,
            (
                contract_id,
                    "a" * 64,
                    reference_snapshot_id,
                    reference_checksum,
                    "b" * 64,
            ),
        )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="validation is not runtime-valid",
        ):
            cursor.execute(
                """
                INSERT INTO pre_trade_batches (
                    provider_contract_id,
                    provider_validation_id,
                    reference_snapshot_id,
                    provider,
                    data_type,
                    source,
                    mic,
                    file_name,
                    source_url,
                    source_checksum_sha256,
                    reference_checksum_sha256,
                    payload_checksum_sha256,
                    report_minute,
                    covered_through,
                    received_at,
                    input_row_count,
                    update_count,
                    state_count
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    contract_id,
                    old_validation_id,
                    reference_snapshot_id,
                    batch.provider,
                    batch.data_type,
                    batch.source,
                    batch.mic,
                    batch.file_name,
                    batch.source_url,
                    batch.source_checksum_sha256,
                    batch.reference_checksum_sha256,
                    "e" * 64,
                    batch.report_minute,
                    batch.covered_through,
                    batch.received_at,
                    batch.input_row_count,
                    len(batch.updates),
                    1,
                ),
            )


def test_pretrade_persistence_rejects_unvalidated_contract(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(
        db,
        connection,
        contract_status="DRAFT",
    )
    batch = _pretrade_batch()
    snapshot = apply_top_of_book_batch(previous=(), batch=batch)

    with pytest.raises(MarketDataError, match="not validated"):
        db.persist_top_of_book_batch(
            contract_key=_PRETRADE_CONTRACT_KEY,
            reference_snapshot_id=reference_snapshot_id,
            batch=batch,
            snapshot=snapshot,
        )


def test_pretrade_persistence_rejects_reference_substitution(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    substituted_reference = _pretrade_reference(name="Altered identity")
    batch = _pretrade_batch(reference=substituted_reference)
    snapshot = apply_top_of_book_batch(previous=(), batch=batch)

    with pytest.raises(MarketDataError, match="reference"):
        db.persist_top_of_book_batch(
            contract_key=_PRETRADE_CONTRACT_KEY,
            reference_snapshot_id=reference_snapshot_id,
            batch=batch,
            snapshot=snapshot,
        )


def test_pretrade_evidence_is_append_only(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    batch = _pretrade_batch()
    snapshot = apply_top_of_book_batch(previous=(), batch=batch)
    result = db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=batch,
        snapshot=snapshot,
    )

    statements = (
        (
            "UPDATE pre_trade_batches SET input_row_count = 3 "
            "WHERE id = %s",
            result.batch_id,
        ),
        (
            "DELETE FROM pre_trade_updates WHERE batch_id = %s",
            result.batch_id,
        ),
        (
            "UPDATE pre_trade_stream_cursors SET mic = 'XHEL' "
            "WHERE batch_id = %s",
            result.batch_id,
        ),
        (
            "DELETE FROM pre_trade_book_states WHERE batch_id = %s",
            result.batch_id,
        ),
    )
    for statement, batch_id in statements:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg2.errors.RaiseException):
                cursor.execute(statement, (batch_id,))

    with connection.cursor() as cursor:
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="validation evidence is append-only",
        ):
            cursor.execute(
                """
                UPDATE market_data_provider_validations
                SET evidence_checksum_sha256 = %s
                WHERE id = (
                    SELECT provider_validation_id
                    FROM pre_trade_batches
                    WHERE id = %s
                )
                """,
                ("f" * 64, result.batch_id),
            )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="contract evidence is immutable",
        ):
            cursor.execute(
                """
                UPDATE market_data_provider_contracts
                SET terms_url = 'https://example.test/changed'
                WHERE id = (
                    SELECT provider_contract_id
                    FROM pre_trade_batches
                    WHERE id = %s
                )
                """,
                (result.batch_id,),
            )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="reference snapshot is immutable",
        ):
            cursor.execute(
                """
                UPDATE reference_data_snapshots
                SET universe_checksum_sha256 = %s
                WHERE id = %s
                """,
                ("f" * 64, reference_snapshot_id),
            )
        cursor.execute(
            """
            UPDATE market_data_provider_contracts
            SET
                status = 'REVOKED',
                revoked_at = NOW(),
                revoked_by = 'operator:test',
                revocation_reason = 'Test provider revocation.'
            WHERE id = (
                SELECT provider_contract_id
                FROM pre_trade_batches
                WHERE id = %s
            )
            """,
            (result.batch_id,),
        )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="already sealed",
        ):
            cursor.execute(
                """
                INSERT INTO pre_trade_updates (
                    batch_id,
                    instrument_id,
                    isin,
                    source_sequence,
                    side,
                    event_time,
                    publication_time,
                    received_at,
                    source,
                    mic,
                    currency,
                    price,
                    quantity,
                    order_count,
                    trading_system,
                    trading_phase
                )
                SELECT
                    batch_id,
                    instrument_id,
                    isin,
                    source_sequence,
                    side,
                    event_time,
                    publication_time,
                    received_at,
                    source,
                    mic,
                    currency,
                    price,
                    quantity,
                    order_count,
                    trading_system,
                    trading_phase
                FROM pre_trade_updates
                WHERE batch_id = %s
                ORDER BY source_sequence
                LIMIT 1
                """,
                (result.batch_id,),
            )


def test_pretrade_schema_rejects_an_incomplete_batch_commit(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    batch = _pretrade_batch()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT contract.id, validation.id
            FROM market_data_provider_contracts contract
            JOIN market_data_provider_validations validation
              ON validation.contract_id = contract.id
            WHERE contract.contract_key = %s
            """,
            (_PRETRADE_CONTRACT_KEY,),
        )
        contract_id, validation_id = (
            int(value) for value in cursor.fetchone()
        )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="incomplete",
        ):
            cursor.execute(
                """
                INSERT INTO pre_trade_batches (
                    provider_contract_id,
                    provider_validation_id,
                    reference_snapshot_id,
                    provider,
                    data_type,
                    source,
                    mic,
                    file_name,
                    source_url,
                    source_checksum_sha256,
                    reference_checksum_sha256,
                    payload_checksum_sha256,
                    report_minute,
                    covered_through,
                    received_at,
                    input_row_count,
                    update_count,
                    state_count
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    contract_id,
                    validation_id,
                    reference_snapshot_id,
                    batch.provider,
                    batch.data_type,
                    batch.source,
                    batch.mic,
                    batch.file_name,
                    batch.source_url,
                    batch.source_checksum_sha256,
                    batch.reference_checksum_sha256,
                    "e" * 64,
                    batch.report_minute,
                    batch.covered_through,
                    batch.received_at,
                    batch.input_row_count,
                    len(batch.updates),
                    1,
                ),
            )


def test_public_pretrade_exposes_ranked_continuous_intraday_signals(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(db, connection)
    latest_received_at = datetime.now(timezone.utc).replace(
        second=0,
        microsecond=0,
    )
    first_report_minute = (
        latest_received_at
        - timedelta(minutes=15)
        - timedelta(minutes=19)
    )
    _install_open_xsto_session(connection, at=latest_received_at)

    previous = ()
    for index in range(20):
        report_minute = first_report_minute + timedelta(minutes=index)
        bid_price = Decimal("320.00") + Decimal("0.05") * index
        batch = _pretrade_batch(
            report_minute=report_minute,
            received_at=report_minute + timedelta(minutes=15),
            bid_price=f"{bid_price:.2f}",
        )
        snapshot = apply_top_of_book_batch(
            previous=previous,
            batch=batch,
        )
        db.persist_top_of_book_batch(
            contract_key=_PRETRADE_CONTRACT_KEY,
            reference_snapshot_id=reference_snapshot_id,
            batch=batch,
            snapshot=snapshot,
        )
        previous = snapshot

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM instruments
            WHERE isin = 'SE0000115446'
            """
        )
        instrument_id = int(cursor.fetchone()[0])
        cursor.execute(
            """
            INSERT INTO companies (
                ticker,
                name,
                sector,
                instrument_id
            )
            VALUES ('VOLV-B', 'Volvo B', 'Industrials', %s)
            """,
            (instrument_id,),
        )

    signals = db.get_current_authorized_intraday_signals(
        now=latest_received_at + timedelta(seconds=10),
        window=20,
        limit=50,
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal["ticker"] == "VOLV-B"
    assert signal["name"] == "Volvo B"
    assert signal["sector"] == "Industrials"
    assert signal["window"] == 20
    assert signal["first_report_minute"] == first_report_minute
    assert signal["last_report_minute"] == (
        latest_received_at - timedelta(minutes=15)
    )
    assert signal["latest_price"] == Decimal("321.12500000")
    assert signal["sma20"] == Decimal("320.88750000")
    assert signal["momentum_pct"] == pytest.approx(
        Decimal("0.07401348"),
        abs=Decimal("0.00000001"),
    )
    assert signal["source"] == "nasdaq-nordic-delayed-pre-trade"


def test_paper_fill_is_bound_to_the_executable_book_side(
    clean_database,
    connection,
):
    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(
        db,
        connection,
        max_transport_lag_seconds=300,
    )
    received_at = datetime.now(timezone.utc).replace(
        second=0,
        microsecond=0,
    )
    report_minute = received_at - timedelta(minutes=15)
    _install_open_xsto_session(connection, at=received_at)
    batch = _pretrade_batch(
        report_minute=report_minute,
        received_at=received_at,
        ask_quantity="2",
    )
    snapshot = apply_top_of_book_batch(previous=(), batch=batch)
    persisted = db.persist_top_of_book_batch(
        contract_key=_PRETRADE_CONTRACT_KEY,
        reference_snapshot_id=reference_snapshot_id,
        batch=batch,
        snapshot=snapshot,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT state.id, state.instrument_id
            FROM pre_trade_book_states state
            WHERE state.batch_id = %s
            """,
            (persisted.batch_id,),
        )
        state_id, instrument_id = (
            int(value) for value in cursor.fetchone()
        )
        cursor.execute(
            """
            INSERT INTO companies (
                ticker,
                name,
                sector,
                instrument_id
            )
            VALUES ('VOLV-B', 'Volvo B', 'Industrials', %s)
            """,
            (instrument_id,),
        )

    executable = db.get_latest_authorized_execution_quote(
        "VOLV-B",
        action="BUY",
        now=received_at + timedelta(seconds=10),
    )
    assert executable is not None
    assert executable.book_state_id == state_id
    assert executable.quote_id is None
    assert executable.last_price == Decimal("321.30000000")

    operational = db.require_operational_market_data(
        received_at + timedelta(seconds=10),
    )
    assert operational["data_type"] == "delayed-pre-trade-equity"
    assert operational["eligible_instrument_count"] == 1
    within_transport_window = db.require_operational_market_data(
        received_at + timedelta(seconds=90),
    )
    assert within_transport_window["eligible_instrument_count"] == 1
    with pytest.raises(
        MarketDataError,
        match="no fresh executable two-sided XSTO books",
    ):
        db.require_operational_market_data(
            received_at + timedelta(seconds=301),
        )
    latest_marks = db.get_latest_authorized_prices()
    assert latest_marks[0]["book_state_id"] == state_id
    assert latest_marks[0]["close"] == Decimal("321.20000000")

    wrong_side = {
        "ticker": "VOLV-B",
        "action": "BUY",
        "shares": 1,
        "price": Decimal("321.10"),
        "total_value": Decimal("321.10"),
        "reasoning": "Wrong executable side",
        "confidence": 80,
        "hypothesis": "Must be rejected",
        "macro_context": {},
        "target_price": Decimal("350"),
        "stop_loss": Decimal("300"),
        "target_pct": 10,
        "stop_loss_pct": -5,
        "idempotency_key": "book-fill-wrong-side",
        "source_book_state_id": state_id,
    }
    with pytest.raises(ValueError, match="bid/ask side"):
        db.log_trade(wrong_side)

    oversized = {
        **wrong_side,
        "shares": 3,
        "price": Decimal("321.30"),
        "total_value": Decimal("963.90"),
        "idempotency_key": "book-fill-over-displayed-quantity",
    }
    with pytest.raises(ValueError, match="remaining executable"):
        db.log_trade(oversized)

    trade = {
        **wrong_side,
        "price": Decimal("321.30"),
        "total_value": Decimal("321.30"),
        "reasoning": "Executable ask evidence",
        "hypothesis": "Audit the exact ask",
        "idempotency_key": "book-fill-correct-side",
    }
    trade_id = db.log_trade(trade)

    consumes_same_ask_again = {
        **trade,
        "shares": 2,
        "total_value": Decimal("642.60"),
        "idempotency_key": "book-fill-reuses-displayed-quantity",
    }
    with pytest.raises(ValueError, match="remaining executable"):
        db.log_trade(consumes_same_ask_again)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                source_book_state_id,
                source_quote_id,
                quote_price,
                price
            FROM trades
            WHERE id = %s
            """,
            (trade_id,),
        )
        row = cursor.fetchone()
    assert row[0] == state_id
    assert row[1] is None
    assert Decimal(row[2]) == Decimal("321.30000000")
    assert Decimal(row[3]) == Decimal("321.30000000")

    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM market_sessions")
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="fresh two-sided pre-trade book",
        ):
            cursor.execute(
                """
                INSERT INTO trades (
                    ticker,
                    action,
                    shares,
                    quote_price,
                    price,
                    total_value,
                    fee_amount,
                    spread_cost,
                    slippage_cost,
                    net_cash_effect,
                    reasoning,
                    confidence,
                    hypothesis,
                    macro_context,
                    target_price,
                    stop_loss,
                    target_pct,
                    stop_loss_pct,
                    idempotency_key,
                    request_fingerprint,
                    strategy_version,
                    source_book_state_id,
                    decision_origin
                )
                SELECT
                    ticker,
                    action,
                    shares,
                    quote_price,
                    price,
                    total_value,
                    fee_amount,
                    spread_cost,
                    slippage_cost,
                    net_cash_effect,
                    'Closed-session attempt',
                    confidence,
                    hypothesis,
                    macro_context,
                    target_price,
                    stop_loss,
                    target_pct,
                    stop_loss_pct,
                    'book-fill-closed-session',
                    %s,
                    strategy_version,
                    source_book_state_id,
                    decision_origin
                FROM trades
                WHERE id = %s
                """,
                ("b" * 64, trade_id),
            )


def test_continuous_candidate_journal_labels_forward_outcomes(
    clean_database,
    connection,
):
    from src.core.candidates import rank_candidate_signals

    db = clean_database
    reference_snapshot_id = _install_pretrade_evidence(
        db,
        connection,
        max_transport_lag_seconds=300,
    )
    first_report_minute = datetime.now(timezone.utc).replace(
        second=0,
        microsecond=0,
    ) - timedelta(minutes=150)
    decision_report_minute = first_report_minute + timedelta(minutes=60)
    decision_observed_at = decision_report_minute + timedelta(
        minutes=15,
        seconds=10,
    )
    _install_open_xsto_session(
        connection,
        at=first_report_minute + timedelta(minutes=60),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE market_sessions
            SET closes_at = %s
            WHERE mic = 'XSTO'
            """,
            (first_report_minute + timedelta(minutes=130),),
        )

    previous = ()
    for index in range(61):
        report_minute = first_report_minute + timedelta(minutes=index)
        bid_price = Decimal("320.00") + Decimal("0.01") * index
        batch = _pretrade_batch(
            report_minute=report_minute,
            received_at=report_minute + timedelta(minutes=15),
            bid_price=f"{bid_price:.2f}",
        )
        snapshot = apply_top_of_book_batch(
            previous=previous,
            batch=batch,
        )
        db.persist_top_of_book_batch(
            contract_key=_PRETRADE_CONTRACT_KEY,
            reference_snapshot_id=reference_snapshot_id,
            batch=batch,
            snapshot=snapshot,
        )
        previous = snapshot

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM instruments
            WHERE isin = 'SE0000115446'
            """
        )
        instrument_id = int(cursor.fetchone()[0])
        cursor.execute(
            """
            INSERT INTO companies (
                ticker,
                name,
                sector,
                instrument_id
            )
            VALUES ('VOLV-B', 'Volvo B', 'Industrials', %s)
            """,
            (instrument_id,),
        )

    raw_signals = db.get_current_authorized_candidate_signals(
        now=decision_observed_at,
        limit=20,
    )
    candidates = rank_candidate_signals(raw_signals, limit=20)
    assert len(candidates) == 1
    assert candidates[0]["ticker"] == "VOLV-B"
    assert candidates[0]["book_state_id"] > 0
    assert candidates[0]["momentum_60m_pct"] > 0
    contract_fresh_at = decision_observed_at + timedelta(seconds=70)
    contract_fresh = db.get_current_authorized_candidate_signals(
        now=contract_fresh_at,
        limit=20,
    )
    assert len(contract_fresh) == 1
    assert contract_fresh[0]["sma20"] == Decimal("320.9025")

    contract_fresh_signal = db.get_authorized_intraday_signal(
        "VOLV-B",
        now=contract_fresh_at,
        window=20,
    )
    assert contract_fresh_signal is not None
    assert contract_fresh_signal["sma20"] == Decimal("320.9025")
    assert (
        contract_fresh_signal["book_state_id"]
        == candidates[0]["book_state_id"]
    )

    strategy = db.get_active_strategy()
    decision_id = db.log_ai_decision(
        timestamp=decision_observed_at,
        prompt_tokens=10,
        response_tokens=10,
        decisions_json={
            "decisions": [],
            "market_outlook": "neutral",
            "analysis_summary": "No action",
        },
        market_data_json="candidate snapshot",
        raw_response='{"decisions":[]}',
        strategy_version=strategy.version,
        strategy_config_hash=strategy.config_hash,
        model_backend="hermes",
        model_name="gpt-5.6-sol",
        model_provider="openai-codex",
        reasoning_effort="medium",
    )
    first_ids = db.record_candidate_predictions(
        ai_decision_id=decision_id,
        candidates=candidates,
        model_decisions=[],
    )
    replayed_ids = db.record_candidate_predictions(
        ai_decision_id=decision_id,
        candidates=candidates,
        model_decisions=[],
    )
    assert first_ids == replayed_ids
    assert len(first_ids) == 1

    for index in range(61, 108):
        report_minute = first_report_minute + timedelta(minutes=index)
        bid_price = Decimal("320.00") + Decimal("0.01") * index
        batch = _pretrade_batch(
            report_minute=report_minute,
            received_at=report_minute + timedelta(minutes=15),
            bid_price=f"{bid_price:.2f}",
            trading_phase="HALT" if index in {75, 106} else "COTR",
        )
        snapshot = apply_top_of_book_batch(
            previous=previous,
            batch=batch,
        )
        db.persist_top_of_book_batch(
            contract_key=_PRETRADE_CONTRACT_KEY,
            reference_snapshot_id=reference_snapshot_id,
            batch=batch,
            snapshot=snapshot,
        )
        previous = snapshot

    evaluated_at = (
        first_report_minute
        + timedelta(minutes=107)
        + timedelta(minutes=15, seconds=10)
    )
    inserted = db.record_candidate_prediction_outcomes(
        evaluated_at=evaluated_at,
    )
    replayed = db.record_candidate_prediction_outcomes(
        evaluated_at=evaluated_at,
    )
    assert inserted == 1
    assert replayed == 0

    status = db.get_continuous_learning_status(now=evaluated_at)
    assert status["policy_version"] == "xsto-momentum-v1"
    assert status["predictions"] == 1
    assert status["labelled_outcomes"] == 1
    assert status["pending_outcomes"] == 0
    assert status["scheduled_outcomes"] == 0
    assert status["overdue_outcomes"] == 0
    assert status["coverage_pct"] == 100.0
    assert status["action_metrics"][0]["action"] == "ABSTAIN"
    assert status["action_metrics"][0]["horizon_minutes"] == 30
    assert status["action_metrics"][0]["positive_rate_pct"] == 100.0

    policy = db.get_active_candidate_policy()
    assert policy.version == "xsto-momentum-v1"
    assert policy.min_signal_score == 0
    calibration_at = first_report_minute + timedelta(minutes=161)
    calibration = db.run_candidate_policy_calibration(
        evaluated_at=calibration_at,
    )
    replayed_calibration = db.run_candidate_policy_calibration(
        evaluated_at=calibration_at,
    )
    assert calibration["id"] == replayed_calibration["id"]
    assert calibration["status"] == "COLLECTING"
    assert calibration["reason_code"] == "INSUFFICIENT_SESSIONS"
    run_id = db.record_continuous_learning_run(
        run_key="continuous-learning:integration",
        evaluated_at=calibration_at,
        status="SUCCEEDED",
        outcomes_labelled=1,
        calibration_run_id=calibration["id"],
        error_code=None,
    )
    assert run_id == db.record_continuous_learning_run(
        run_key="continuous-learning:integration",
        evaluated_at=calibration_at,
        status="SUCCEEDED",
        outcomes_labelled=1,
        calibration_run_id=calibration["id"],
        error_code=None,
    )
    runtime = db.get_continuous_learning_runtime_status(
        now=calibration_at,
    )
    assert runtime["run"]["status"] == "SUCCEEDED"
    assert runtime["run"]["outcomes_labelled"] == 1
    assert runtime["calibration"]["status"] == "COLLECTING"
    assert runtime["policies"][0]["status"] == "ACTIVE"

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                entry.entry_event_time,
                entry.entry_price,
                outcome.target_event_time
            FROM candidate_prediction_entries entry
            JOIN candidate_prediction_outcomes outcome
              ON outcome.entry_id = entry.id
            WHERE entry.prediction_id = %s
            """,
            (first_ids[0],),
        )
        entry_event_time, entry_price, target_event_time = cursor.fetchone()
        assert entry_event_time == first_report_minute + timedelta(minutes=76)
        assert entry_price == Decimal("321.03000000")
        assert target_event_time == first_report_minute + timedelta(minutes=107)

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="candidate entry journal is append-only",
        ):
            cursor.execute(
                """
                UPDATE candidate_prediction_entries
                SET entry_price = entry_price + 1
                WHERE prediction_id = %s
                """,
                (first_ids[0],),
            )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="candidate prediction journal is append-only",
        ):
            cursor.execute(
                "UPDATE candidate_predictions SET signal_rank = 2 WHERE id = %s",
                (first_ids[0],),
            )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="candidate outcome journal is append-only",
        ):
            cursor.execute(
                "DELETE FROM candidate_prediction_outcomes WHERE prediction_id = %s",
                (first_ids[0],),
            )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="continuous learning evidence is append-only",
        ):
            cursor.execute(
                """
                UPDATE continuous_learning_runs
                SET outcomes_labelled = outcomes_labelled + 1
                WHERE id = %s
                """,
                (run_id,),
            )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO candidate_policy_calibration_runs (
                calibration_key,
                policy_version,
                evaluated_at,
                session_cutoff_date,
                status,
                reason_code,
                train_sessions,
                validation_sessions,
                train_outcomes,
                validation_outcomes,
                coverage_pct,
                active_min_signal_score,
                challenger_min_signal_score,
                baseline_validation_mean_bps,
                challenger_validation_mean_bps,
                validation_improvement_bps,
                metrics
            )
            VALUES (
                'candidate:xsto-momentum-v1:forward-test',
                'xsto-momentum-v1',
                %s,
                %s,
                'CHALLENGER',
                'FORWARD_GATE_PASSED',
                10,
                3,
                1000,
                300,
                100,
                0,
                60,
                1,
                6,
                5,
                '{}'::JSONB
            )
            RETURNING id
            """,
            (evaluated_at, evaluated_at.date()),
        )
        challenger_calibration_id = int(cursor.fetchone()[0])
        cursor.execute(
            """
            INSERT INTO candidate_policy_versions (
                version,
                status,
                config,
                config_hash,
                parent_version_id,
                calibration_run_id,
                proposed_by
            )
            SELECT
                'xsto-challenger-test',
                'DRAFT',
                '{"max_spread_bps":250,"min_signal_score":60}'::JSONB,
                %s,
                id,
                %s,
                'continuous-learning-worker'
            FROM candidate_policy_versions
            WHERE status = 'ACTIVE'
            """,
            (
                "f7d4611347849d783cc71b04c71d07381"
                "f3f838ed16d1e4516c551bc966eb38b",
                challenger_calibration_id,
            ),
        )

    db.approve_candidate_policy_version(
        "xsto-challenger-test",
        reviewed_by="operator:test",
    )
    assert db.get_active_candidate_policy().version == "xsto-momentum-v1"
    db.activate_candidate_policy_version(
        "xsto-challenger-test",
        activated_by="operator:test",
    )
    assert db.get_active_candidate_policy().version == "xsto-challenger-test"
    assert db.get_active_candidate_policy().min_signal_score == 60


def test_forward_challenger_can_be_automatically_activated(
    clean_database,
    connection,
    monkeypatch,
):
    db = clean_database
    transition_at = datetime.now(timezone.utc).replace(microsecond=0)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO candidate_policy_calibration_runs (
                calibration_key,
                policy_version,
                evaluated_at,
                session_cutoff_date,
                status,
                reason_code,
                train_sessions,
                validation_sessions,
                train_outcomes,
                validation_outcomes,
                coverage_pct,
                active_min_signal_score,
                challenger_min_signal_score,
                baseline_validation_mean_bps,
                challenger_validation_mean_bps,
                validation_improvement_bps,
                metrics
            )
            VALUES (
                'candidate:xsto-momentum-v1:auto-forward-test',
                'xsto-momentum-v1',
                %s,
                %s,
                'CHALLENGER',
                'FORWARD_GATE_PASSED',
                10,
                3,
                1000,
                300,
                100,
                0,
                60,
                1,
                6,
                5,
                '{}'::JSONB
            )
            RETURNING id
            """,
            (transition_at, transition_at.date()),
        )
        calibration_id = int(cursor.fetchone()[0])
        cursor.execute(
            """
            INSERT INTO candidate_policy_versions (
                version,
                status,
                config,
                config_hash,
                parent_version_id,
                calibration_run_id,
                proposed_by
            )
            SELECT
                'xsto-challenger-auto-test',
                'DRAFT',
                '{"max_spread_bps":250,"min_signal_score":60}'::JSONB,
                %s,
                id,
                %s,
                'continuous-learning-worker'
            FROM candidate_policy_versions
            WHERE status = 'ACTIVE'
            """,
            (
                "f7d4611347849d783cc71b04c71d07381"
                "f3f838ed16d1e4516c551bc966eb38b",
                calibration_id,
            ),
        )

    assert db.activate_candidate_policy_automatically(
        "xsto-challenger-auto-test",
        activated_at=transition_at,
    )
    assert not db.activate_candidate_policy_automatically(
        "xsto-challenger-auto-test",
        activated_at=transition_at,
    )
    assert db.get_active_candidate_policy().version == (
        "xsto-challenger-auto-test"
    )
    rows = db.query(
        """
        SELECT version, status, reviewed_by, activated_by
        FROM candidate_policy_versions
        ORDER BY id
        """
    )
    assert rows[0]["status"] == "RETIRED"
    assert rows[1]["status"] == "ACTIVE"
    assert rows[1]["reviewed_by"] == "continuous-learning-worker"
    assert rows[1]["activated_by"] == "continuous-learning-worker"

    from src.core import continuous_learning

    monkeypatch.setattr(
        continuous_learning,
        "evaluate_candidate_policy_rollback",
        lambda **_metrics: continuous_learning.CandidateRollbackResult(
            status="ROLLBACK",
            reason_code="PARENT_FORWARD_PERFORMANCE_RECOVERED",
            completed_sessions=3,
            coverage_pct=100,
            active_outcomes=100,
            parent_outcomes=100,
            active_mean_bps=-1,
            parent_mean_bps=6,
            parent_positive_rate_pct=55,
            parent_advantage_bps=7,
        ),
    )
    rollback = db.rollback_candidate_policy_automatically(
        evaluated_at=transition_at + timedelta(days=3),
    )
    assert rollback["status"] == "ROLLBACK"
    assert rollback["policy_version"].startswith("xsto-rollback-")
    restored = db.get_active_candidate_policy()
    assert restored.version == rollback["policy_version"]
    assert restored.min_signal_score == 0
    rows = db.query(
        """
        SELECT version, status, proposed_by, activated_by
        FROM candidate_policy_versions
        ORDER BY id
        """
    )
    assert [row["status"] for row in rows] == [
        "RETIRED",
        "RETIRED",
        "ACTIVE",
    ]
    assert rows[2]["proposed_by"] == "continuous-learning-worker"
    assert rows[2]["activated_by"] == "continuous-learning-worker"


def test_knowledge_shadow_journal_is_idempotent_and_append_only(
    clean_database,
    connection,
):
    db = clean_database
    evaluated_at = datetime.now(timezone.utc).replace(microsecond=0)
    strategy = db.get_active_strategy()
    decision_id = db.log_ai_decision(
        timestamp=evaluated_at,
        prompt_tokens=10,
        response_tokens=5,
        decisions_json={
            "decisions": [],
            "market_outlook": "neutral",
            "analysis_summary": "No action",
        },
        market_data_json="bounded source context",
        raw_response='{"decisions":[]}',
        strategy_version=strategy.version,
        strategy_config_hash=strategy.config_hash,
        model_backend="hermes",
        model_name="gpt-5.6",
        model_provider="openai-codex",
        reasoning_effort="high",
    )

    pending = db.get_pending_knowledge_shadow_inputs(
        now=evaluated_at,
        limit=2,
    )
    source = next(
        row for row in pending
        if row["source_decision_id"] == decision_id
    )
    assert source["market_context"] == "bounded source context"
    assert source["tickers"] == []

    values = {
        "source_decision_id": decision_id,
        "evaluated_at": evaluated_at,
        "status": "SKIPPED",
        "reason_code": "NO_CANDIDATES",
        "call_order": "NOT_RUN",
        "context_checksum_sha256": "a" * 64,
        "evidence_provenance": "neo4j:candidate-outcome-aggregate-v1",
        "evidence_as_of": evaluated_at,
        "evidence_checksum_sha256": "b" * 64,
        "evidence_fact_count": 0,
        "candidate_count": 0,
        "comparison_count": 0,
        "changed_count": 0,
        "control_prompt_tokens": 0,
        "control_response_tokens": 0,
        "memory_prompt_tokens": 0,
        "memory_response_tokens": 0,
        "model_backend": "hermes",
        "model_name": "gpt-5.6",
        "model_provider": "openai-codex",
        "reasoning_effort": "high",
        "comparisons": [],
    }
    run_id = db.record_knowledge_shadow_run(**values)
    assert db.record_knowledge_shadow_run(**values) == run_id
    assert all(
        row["source_decision_id"] != decision_id
        for row in db.get_pending_knowledge_shadow_inputs(
            now=evaluated_at,
            limit=2,
        )
    )

    runtime = db.get_knowledge_shadow_runtime_status(now=evaluated_at)
    assert runtime["status"] == "SKIPPED"
    assert runtime["reason_code"] == "NO_CANDIDATES"
    assert runtime["comparison_count"] == 0
    assert runtime["changed_count"] == 0

    with connection.cursor() as cursor:
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="knowledge shadow evidence is append-only",
        ):
            cursor.execute(
                """
                UPDATE knowledge_shadow_runs
                SET changed_count = 1
                WHERE id = %s
                """,
                (run_id,),
            )
