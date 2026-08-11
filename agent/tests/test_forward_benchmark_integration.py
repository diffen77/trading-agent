import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from zoneinfo import ZoneInfo

import pytest

from src.core.forward_benchmark import (
    BenchmarkRegistration,
    BenchmarkStartEvidence,
)
from src.benchmark_admin import BenchmarkAdmin
from src.data.database import Database


psycopg2 = pytest.importorskip("psycopg2")


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
STOCKHOLM = ZoneInfo("Europe/Stockholm")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture()
def connection():
    assert TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture(autouse=True)
def isolated_forward_benchmark_database(connection):
    connection.rollback()
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(
            """
            TRUNCATE
                paper_benchmark_evaluations,
                paper_benchmark_daily_observations,
                paper_benchmark_lifecycle_events,
                paper_benchmark_experiments,
                ai_decisions,
                trade_lot_allocations,
                position_lots,
                portfolio,
                trades,
                balance,
                pre_trade_book_states,
                pre_trade_stream_cursors,
                pre_trade_updates,
                pre_trade_batches,
                market_index_levels,
                market_data_provider_validations,
                market_data_provider_contracts,
                reference_data_files,
                reference_snapshot_instruments,
                reference_data_snapshots,
                market_calendar_snapshots,
                market_sessions,
                market_data_gaps,
                market_data_files,
                data_sync_runs,
                market_bars,
                market_quotes,
                instrument_aliases,
                instruments
            RESTART IDENTITY CASCADE;
            INSERT INTO balance (cash, total_value)
            VALUES (20000, 20000);
            """
        )
    connection.autocommit = False
    yield


def _insert_provider_contract(
    cursor,
    *,
    contract_key,
    data_type,
    expected_instruments,
    status="VALIDATED",
    max_transport_lag_seconds=30,
):
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
            %s,
            'test-provider',
            %s,
            %s,
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
            CURRENT_DATE - 500,
            CURRENT_DATE + 365,
            'PENDING',
            'operator:test'
        )
        RETURNING id
        """,
        (
            contract_key,
            contract_key,
            data_type,
            max_transport_lag_seconds,
        ),
    )
    contract_id = cursor.fetchone()[0]
    if status == "VALIDATED":
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
            ("b" * 64, contract_id),
        )
        reference_snapshot_id = None
        reference_checksum = None
        if data_type != "delayed-index-level":
            cursor.execute(
                """
                SELECT id, universe_checksum_sha256
                FROM reference_data_snapshots
                WHERE mic = 'XSTO'
                ORDER BY snapshot_date DESC, id DESC
                LIMIT 1
                """
            )
            reference_snapshot_id, reference_checksum = cursor.fetchone()
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
                NOW() + INTERVAL '30 days',
                %s,
                %s,
                %s,
                2,
                100,
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
                expected_instruments,
                expected_instruments,
                expected_instruments,
                "f" * 64,
                reference_snapshot_id,
                reference_checksum,
                hashlib.sha256(
                    f"{contract_key}:acceptance".encode()
                ).hexdigest(),
            ),
        )
    elif status != "DRAFT":
        raise ValueError("unsupported provider contract fixture status")
    return contract_id


def _insert_reference_snapshot(
    cursor,
    *,
    snapshot_date=None,
    checksum="a" * 64,
):
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
            416,
            416,
            %s,
            NOW()
        )
        RETURNING id
        """,
        (f"benchmark-reference-{datetime.now(timezone.utc).timestamp()}",),
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
            COALESCE(%s, CURRENT_DATE),
            NOW(),
            %s,
            1,
            416,
            %s
        )
        RETURNING id
        """,
        (snapshot_date, checksum, sync_run_id),
    )
    snapshot_id = cursor.fetchone()[0]
    cursor.execute(
        """
        WITH inserted AS (
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
            SELECT
                'SE' || LPAD(series.value::TEXT, 10, '0'),
                'TEST-' || series.value,
                'Test instrument ' || series.value,
                'XSTO',
                'SEK',
                'COMMON_STOCK',
                TRUE,
                'ACTIVE',
                'test-reference',
                COALESCE(%s, CURRENT_DATE)
            FROM GENERATE_SERIES(1, 416) AS series(value)
            ON CONFLICT (isin) DO UPDATE SET
                status = 'ACTIVE',
                source = 'test-reference',
                reference_snapshot_date = EXCLUDED.reference_snapshot_date
            RETURNING id, isin
        )
        INSERT INTO reference_snapshot_instruments (
            snapshot_id,
            instrument_id,
            isin
        )
        SELECT %s, inserted.id, inserted.isin
        FROM inserted
        """,
        (snapshot_date, snapshot_id),
    )
    return snapshot_id


def _insert_market_quote(
    cursor,
    *,
    price,
    event_time,
    received_at,
    contract_id=None,
    reference_snapshot_id=None,
    ticker="VOLV-B",
    instrument_id=None,
):
    if contract_id is None or reference_snapshot_id is None:
        cursor.execute(
            """
            SELECT
                execution_provider_contract_id,
                reference_snapshot_id
            FROM paper_benchmark_experiments
            WHERE status = 'RUNNING'
            """
        )
        running_evidence = cursor.fetchone()
        if running_evidence is None:
            raise AssertionError(
                "market quote fixture requires a running experiment"
            )
        contract_id = (
            contract_id
            if contract_id is not None
            else running_evidence[0]
        )
        reference_snapshot_id = (
            reference_snapshot_id
            if reference_snapshot_id is not None
            else running_evidence[1]
        )
    if instrument_id is None:
        cursor.execute(
            """
            SELECT membership.instrument_id
            FROM reference_snapshot_instruments membership
            WHERE membership.snapshot_id = %s
            ORDER BY membership.instrument_id
            LIMIT 1
            """,
            (reference_snapshot_id,),
        )
        instrument_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO companies (ticker, name, instrument_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (ticker) DO UPDATE
        SET instrument_id = EXCLUDED.instrument_id
        """,
        (ticker, ticker, instrument_id),
    )
    cursor.execute(
        """
        INSERT INTO market_data_files (
            provider,
            data_type,
            file_name,
            report_minute,
            received_at,
            source_url,
            checksum_sha256,
            content_encoding,
            compressed_content,
            uncompressed_bytes,
            provider_contract_id
        )
        VALUES (
            'test-provider',
            'delayed-post-trade-equity',
            %s,
            %s,
            %s,
            'https://example.test/benchmark-quote.csv',
            %s,
            'gzip',
            %s,
            1,
            %s
        )
        RETURNING id
        """,
        (
            (
                f"benchmark-quote-{event_time.timestamp()}-"
                f"{received_at.timestamp()}"
            ),
            event_time,
            received_at,
            hashlib.sha256(
                (
                    f"{contract_id}:{event_time.isoformat()}:"
                    f"{received_at.isoformat()}"
                ).encode()
            ).hexdigest(),
            psycopg2.Binary(b"x"),
            contract_id,
        ),
    )
    data_file_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO market_quotes (
            instrument_id,
            event_time,
            received_at,
            source,
            last_price,
            currency,
            data_file_id
        )
        VALUES (
            %s,
            %s,
            %s,
            'test-provider',
            %s,
            'SEK',
            %s
        )
        RETURNING id
        """,
        (
            instrument_id,
            event_time,
            received_at,
            price,
            data_file_id,
        ),
    )
    return cursor.fetchone()[0]


def _insert_pretrade_book(
    cursor,
    *,
    contract_id,
    reference_snapshot_id,
    received_at,
    bid_price=Decimal("99"),
    ask_price=Decimal("101"),
    displayed_quantity=Decimal("100"),
):
    report_minute = received_at.replace(
        second=0,
        microsecond=0,
    ) - timedelta(minutes=15)
    covered_through = report_minute + timedelta(minutes=1)
    bid_event_time = report_minute + timedelta(seconds=1)
    ask_event_time = report_minute + timedelta(seconds=2)
    cursor.execute(
        """
        SELECT validation.id
        FROM market_data_provider_validations validation
        WHERE validation.contract_id = %s
        ORDER BY validation.validated_at DESC, validation.id DESC
        LIMIT 1
        """,
        (contract_id,),
    )
    validation_id = int(cursor.fetchone()[0])
    cursor.execute(
        """
        SELECT membership.instrument_id, TRIM(membership.isin)
        FROM reference_snapshot_instruments membership
        WHERE membership.snapshot_id = %s
        ORDER BY membership.instrument_id
        LIMIT 1
        """,
        (reference_snapshot_id,),
    )
    instrument_id, isin = cursor.fetchone()
    cursor.execute(
        """
        INSERT INTO companies (ticker, name, sector, instrument_id)
        VALUES ('TEST-1', 'Test instrument 1', 'Industrials', %s)
        ON CONFLICT (ticker) DO UPDATE
        SET instrument_id = EXCLUDED.instrument_id
        """,
        (instrument_id,),
    )
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
            'test-provider',
            'delayed-pre-trade-equity',
            'test-provider-pretrade',
            'XSTO',
            'level1-test.csv',
            'https://example.test/level1-test.csv',
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            2,
            2,
            1
        )
        RETURNING id
        """,
        (
            contract_id,
            validation_id,
            reference_snapshot_id,
            "b" * 64,
            "a" * 64,
            "c" * 64,
            report_minute,
            covered_through,
            received_at,
        ),
    )
    batch_id = int(cursor.fetchone()[0])
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
        VALUES
            (
                %s, %s, %s, 1, 'BUY', %s, %s, %s,
                'test-provider-pretrade', 'XSTO', 'SEK',
                %s, %s, 2, 'CLOB', 'COTR'
            ),
            (
                %s, %s, %s, 2, 'SELL', %s, %s, %s,
                'test-provider-pretrade', 'XSTO', 'SEK',
                %s, %s, 2, 'CLOB', 'COTR'
            )
        """,
        (
            batch_id,
            instrument_id,
            isin,
            bid_event_time,
            bid_event_time,
            received_at,
            bid_price,
            displayed_quantity,
            batch_id,
            instrument_id,
            isin,
            ask_event_time,
            ask_event_time,
            received_at,
            ask_price,
            displayed_quantity,
        ),
    )
    cursor.execute(
        """
        INSERT INTO pre_trade_book_states (
            batch_id,
            instrument_id,
            isin,
            source,
            mic,
            reference_checksum_sha256,
            currency,
            bid_price,
            bid_quantity,
            bid_order_count,
            bid_event_time,
            bid_publication_time,
            ask_price,
            ask_quantity,
            ask_order_count,
            ask_event_time,
            ask_publication_time,
            covered_through,
            received_at,
            trading_system,
            trading_phase
        )
        VALUES (
            %s, %s, %s, 'test-provider-pretrade', 'XSTO',
            %s, 'SEK',
            %s, %s, 2, %s, %s,
            %s, %s, 2, %s, %s,
            %s, %s, 'CLOB', 'COTR'
        )
        RETURNING id
        """,
        (
            batch_id,
            instrument_id,
            isin,
            "a" * 64,
            bid_price,
            displayed_quantity,
            bid_event_time,
            bid_event_time,
            ask_price,
            displayed_quantity,
            ask_event_time,
            ask_event_time,
            covered_through,
            received_at,
        ),
    )
    state_id = int(cursor.fetchone()[0])
    cursor.execute(
        """
        INSERT INTO pre_trade_stream_cursors (
            batch_id,
            source,
            mic,
            covered_through,
            received_at,
            reference_checksum_sha256
        )
        VALUES (
            %s,
            'test-provider-pretrade',
            'XSTO',
            %s,
            %s,
            %s
        )
        """,
        (
            batch_id,
            covered_through,
            received_at,
            "a" * 64,
        ),
    )
    return state_id


def _insert_experiment(
    cursor,
    *,
    quote_contract_id=None,
    execution_price_source="LAST_TRADE_PLUS_BPS",
    execution_contract_id=None,
    benchmark_contract_id=None,
    reference_snapshot_id=None,
    min_trading_sessions=252,
    initial_status="DRAFT",
    universe_checksum="a" * 64,
):
    if execution_contract_id is None:
        execution_contract_id = quote_contract_id
    spread_bps = (
        0
        if execution_price_source == "TOP_OF_BOOK_PLUS_SLIPPAGE"
        else 10
    )
    provider_keys = {}
    provider_ids = [
        value
        for value in (
            quote_contract_id,
            execution_contract_id,
            benchmark_contract_id,
        )
        if value is not None
    ]
    if provider_ids:
        cursor.execute(
            """
            SELECT id, contract_key
            FROM market_data_provider_contracts
            WHERE id = ANY(%s)
            """,
            (provider_ids,),
        )
        provider_keys = dict(cursor.fetchall())
    strategy_hash = (
        "7a363941bdffa31f8bb204ad0a0828404"
        "b5fe57d83da752a66758fbfd6fd0f50"
    )
    experiment_key = (
        f"forward-paper-{datetime.now(timezone.utc).timestamp()}".replace(
            ".",
            "-",
        )
    )
    payload = {
        "experiment_key": experiment_key,
        "strategy_version": "momentum-report-swing-v1",
        "strategy_config_hash": strategy_hash,
        "release_sha": "b" * 40,
        "release_manifest_sha256": "1" * 64,
        "agent_image_digest_sha256": "2" * 64,
        "model_backend": "openai-compatible",
        "model_name": "frozen-test-model",
        "model_evidence_sha256": "3" * 64,
        "reference_snapshot_id": reference_snapshot_id,
        "universe_checksum_sha256": universe_checksum,
        "quote_provider_contract_key": provider_keys.get(
            quote_contract_id
        ),
        "execution_price_source": execution_price_source,
        "execution_provider_contract_key": provider_keys.get(
            execution_contract_id
        ),
        "benchmark_provider_contract_key": provider_keys.get(
            benchmark_contract_id
        ),
        "benchmark_symbol": "OMXSGI",
        "benchmark_kind": "TOTAL_RETURN_GROSS",
        "benchmark_source_url": (
            "https://indexes.nasdaqomx.com/Index/Overview/OMXSGI"
        ),
        "benchmark_terms_url": "https://example.test/benchmark-terms",
        "initial_capital": 20_000,
        "costs": {
            "fee_bps": 5,
            "spread_bps": spread_bps,
            "slippage_bps": 5,
        },
        "criteria": {
            "min_trading_sessions": min_trading_sessions,
            "min_closed_trades": 30,
            "max_drawdown_pct": 15,
            "min_data_coverage_pct": 99.5,
        },
        "proposed_by": "operator:test",
    }
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    payload_hash = hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()
    cursor.execute(
        """
        INSERT INTO paper_benchmark_experiments (
            experiment_key,
            status,
            strategy_version_id,
            strategy_config_hash,
            release_sha,
            release_manifest_sha256,
            agent_image_digest_sha256,
            model_backend,
            model_name,
            model_evidence_sha256,
            reference_snapshot_id,
            universe_checksum_sha256,
            quote_provider_contract_id,
            execution_price_source,
            execution_provider_contract_id,
            benchmark_provider_contract_id,
            benchmark_source_url,
            benchmark_terms_url,
            fee_bps,
            spread_bps,
            slippage_bps,
            min_trading_sessions,
            preregistration_payload,
            preregistration_canonical_json,
            preregistration_hash,
            proposed_by,
            proposed_at,
            approved_by,
            approved_at
        )
        SELECT
            %s,
            %s,
            strategy.id,
            strategy.config_hash,
            %s,
            %s,
            %s,
            'openai-compatible',
            'frozen-test-model',
            %s,
            %s,
            CASE WHEN %s IS NULL THEN NULL ELSE %s END,
            %s,
            %s,
            %s,
            %s,
            'https://indexes.nasdaqomx.com/Index/Overview/OMXSGI',
            'https://example.test/benchmark-terms',
            5,
            %s,
            5,
            %s,
            %s,
            %s,
            %s,
            'operator:test',
            NOW() - INTERVAL '400 days',
            CASE
                WHEN %s = 'APPROVED' THEN 'operator:test'
            END,
            CASE
                WHEN %s = 'APPROVED' THEN NOW()
            END
        FROM strategy_versions strategy
        WHERE strategy.version = 'momentum-report-swing-v1'
        RETURNING id
        """,
        (
            experiment_key,
            initial_status,
            "b" * 40,
            "1" * 64,
            "2" * 64,
            "3" * 64,
            reference_snapshot_id,
            reference_snapshot_id,
            universe_checksum,
            quote_contract_id,
            execution_price_source,
            execution_contract_id,
            benchmark_contract_id,
            spread_bps,
            min_trading_sessions,
            json.dumps(payload),
            canonical_json,
            payload_hash,
            initial_status,
            initial_status,
        ),
    )
    return cursor.fetchone()[0]


def _insert_start_level(
    cursor,
    experiment_id,
    *,
    event_time,
    received_at,
    level=Decimal("100"),
    checksum="9" * 64,
    contract_id=None,
):
    if contract_id is None:
        cursor.execute(
            """
            SELECT benchmark_provider_contract_id
            FROM paper_benchmark_experiments
            WHERE id = %s
            """,
            (experiment_id,),
        )
        contract_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO market_index_levels (
            provider_contract_id,
            symbol,
            mic,
            event_time,
            received_at,
            source,
            level,
            source_checksum_sha256
        )
        VALUES (
            %s,
            'OMXSGI',
            'XSTO',
            %s,
            %s,
            'test-provider-index',
            %s,
            %s
        )
        RETURNING id
        """,
        (
            contract_id,
            event_time,
            received_at,
            level,
            checksum,
        ),
    )
    return cursor.fetchone()[0]


def _approve_and_start(cursor, experiment_id):
    now = datetime.now(timezone.utc)
    started_at = now - timedelta(days=380)
    start_event_time = started_at - timedelta(minutes=15)
    cursor.execute(
        """
        INSERT INTO market_sessions (
            mic,
            session_date,
            opens_at,
            closes_at,
            status,
            source
        )
        VALUES (
            'XSTO',
            %s,
            %s,
            %s,
            'OPEN',
            'benchmark-test'
        )
        ON CONFLICT (mic, session_date) DO UPDATE
        SET
            opens_at = EXCLUDED.opens_at,
            closes_at = EXCLUDED.closes_at,
            status = 'OPEN'
        """,
        (
            now.astimezone(STOCKHOLM).date(),
            now - timedelta(hours=2),
            now + timedelta(hours=2),
        ),
    )
    cursor.execute(
        """
        UPDATE paper_benchmark_experiments
        SET
            status = 'APPROVED',
            approved_by = 'operator:test',
            approved_at = NOW() - INTERVAL '390 days'
        WHERE id = %s
        """,
        (experiment_id,),
    )
    start_level_id = _insert_start_level(
        cursor,
        experiment_id,
        event_time=start_event_time,
        received_at=started_at,
    )
    cursor.execute(
        """
        UPDATE paper_benchmark_experiments
        SET
            status = 'RUNNING',
            started_by = 'operator:test',
            started_at = %s,
            benchmark_start_level_id = %s,
            benchmark_start_level = 100,
            benchmark_start_event_time = %s,
            benchmark_start_available_at = %s,
            benchmark_start_checksum_sha256 = %s
        WHERE id = %s
        """,
        (
            started_at,
            start_level_id,
            start_event_time,
            started_at,
            "9" * 64,
            experiment_id,
        ),
    )


def _insert_ready_experiment(cursor):
    reference_snapshot_id = _insert_reference_snapshot(cursor)
    quote_contract_id = _insert_provider_contract(
        cursor,
        contract_key=(
            f"quote-{datetime.now(timezone.utc).timestamp()}".replace(".", "-")
        ),
        data_type="delayed-post-trade-equity",
        expected_instruments=416,
    )
    benchmark_contract_id = _insert_provider_contract(
        cursor,
        contract_key=(
            f"benchmark-{datetime.now(timezone.utc).timestamp()}".replace(
                ".",
                "-",
            )
        ),
        data_type="delayed-index-level",
        expected_instruments=1,
    )
    return _insert_experiment(
        cursor,
        quote_contract_id=quote_contract_id,
        benchmark_contract_id=benchmark_contract_id,
        reference_snapshot_id=reference_snapshot_id,
    )


def test_approval_rejects_unvalidated_provider_evidence(connection):
    with connection.cursor() as cursor:
        reference_snapshot_id = _insert_reference_snapshot(cursor)
        quote_contract_id = _insert_provider_contract(
            cursor,
            contract_key="unvalidated-quote",
            data_type="delayed-post-trade-equity",
            expected_instruments=416,
            status="DRAFT",
        )
        benchmark_contract_id = _insert_provider_contract(
            cursor,
            contract_key="validated-benchmark",
            data_type="delayed-index-level",
            expected_instruments=1,
        )
        experiment_id = _insert_experiment(
            cursor,
            quote_contract_id=quote_contract_id,
            benchmark_contract_id=benchmark_contract_id,
            reference_snapshot_id=reference_snapshot_id,
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="provider evidence is not runtime-ready",
        ):
            cursor.execute(
                """
                UPDATE paper_benchmark_experiments
                SET
                    status = 'APPROVED',
                    approved_by = 'operator:test',
                    approved_at = NOW()
                WHERE id = %s
                """,
                (experiment_id,),
            )


def test_approval_rejects_quote_evidence_for_wrong_universe_size(connection):
    with connection.cursor() as cursor:
        _insert_reference_snapshot(cursor)
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="verified acceptance evidence",
        ):
            _insert_provider_contract(
                cursor,
                contract_key="wrong-size-quote",
                data_type="delayed-post-trade-equity",
                expected_instruments=1,
            )


def test_approval_rejects_provider_validated_for_another_snapshot(connection):
    with connection.cursor() as cursor:
        _insert_reference_snapshot(
            cursor,
            snapshot_date=date.today() - timedelta(days=2),
            checksum="a" * 64,
        )
        quote_contract_id = _insert_provider_contract(
            cursor,
            contract_key="stale-snapshot-quote",
            data_type="delayed-post-trade-equity",
            expected_instruments=416,
        )
        current_snapshot_id = _insert_reference_snapshot(
            cursor,
            snapshot_date=date.today() - timedelta(days=1),
            checksum="c" * 64,
        )
        benchmark_contract_id = _insert_provider_contract(
            cursor,
            contract_key="current-snapshot-benchmark",
            data_type="delayed-index-level",
            expected_instruments=1,
        )
        experiment_id = _insert_experiment(
            cursor,
            quote_contract_id=quote_contract_id,
            benchmark_contract_id=benchmark_contract_id,
            reference_snapshot_id=current_snapshot_id,
            universe_checksum="c" * 64,
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match=(
                "provider validation does not match frozen "
                "reference snapshot"
            ),
        ):
            cursor.execute(
                """
                UPDATE paper_benchmark_experiments
                SET
                    status = 'APPROVED',
                    approved_by = 'operator:test',
                    approved_at = NOW()
                WHERE id = %s
                """,
                (experiment_id,),
            )


def test_start_revalidates_provider_evidence_after_approval(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        cursor.execute(
            """
            UPDATE paper_benchmark_experiments
            SET
                status = 'APPROVED',
                approved_by = 'operator:test',
                approved_at = NOW() - INTERVAL '1 day'
            WHERE id = %s
            """,
            (experiment_id,),
        )
        start_available_at = datetime.now(timezone.utc)
        start_event_time = start_available_at - timedelta(minutes=15)
        start_level_id = _insert_start_level(
            cursor,
            experiment_id,
            event_time=start_event_time,
            received_at=start_available_at,
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
                SELECT quote_provider_contract_id
                FROM paper_benchmark_experiments
                WHERE id = %s
            )
            """,
            (experiment_id,),
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="quote provider evidence is not runtime-ready",
        ):
            cursor.execute(
                """
                UPDATE paper_benchmark_experiments
                SET
                    status = 'RUNNING',
                    started_by = 'operator:test',
                    started_at = %s,
                    benchmark_start_level_id = %s,
                    benchmark_start_level = 100,
                    benchmark_start_event_time = %s,
                    benchmark_start_available_at = %s,
                    benchmark_start_checksum_sha256 = %s
                WHERE id = %s
                """,
                (
                    start_available_at,
                    start_level_id,
                    start_event_time,
                    start_available_at,
                    "9" * 64,
                    experiment_id,
                ),
            )


def test_start_requires_an_exact_authorized_index_level(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        cursor.execute(
            """
            UPDATE paper_benchmark_experiments
            SET
                status = 'APPROVED',
                approved_by = 'operator:test',
                approved_at = NOW() - INTERVAL '1 day'
            WHERE id = %s
            """,
            (experiment_id,),
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="exact authorized index evidence",
        ):
            cursor.execute(
                """
                UPDATE paper_benchmark_experiments
                SET
                    status = 'RUNNING',
                    started_by = 'operator:test',
                    started_at = NOW(),
                    benchmark_start_level = 100,
                    benchmark_start_event_time =
                        NOW() - INTERVAL '15 minutes',
                    benchmark_start_available_at = NOW(),
                    benchmark_start_checksum_sha256 = %s
                WHERE id = %s
                """,
                ("9" * 64, experiment_id),
            )


def test_running_experiment_rejects_trade_after_provider_revocation(
    connection,
):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)
        cursor.execute(
            """
            UPDATE market_data_provider_contracts
            SET
                status = 'REVOKED',
                revoked_at = NOW(),
                revoked_by = 'operator:test',
                revocation_reason = 'Test provider revocation.'
            WHERE id = (
                SELECT quote_provider_contract_id
                FROM paper_benchmark_experiments
                WHERE id = %s
            )
            """,
            (experiment_id,),
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="provider evidence is not current",
        ):
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
                    strategy_version
                )
                VALUES (
                    'VOLV-B',
                    'BUY',
                    1,
                    100,
                    100,
                    'provider must remain current',
                    'revoked-provider-trade',
                    %s,
                    'momentum-report-swing-v1'
                )
                """,
                ("d" * 64,),
            )


def test_resume_rejects_a_strategy_retired_during_pause(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)
        cursor.execute(
            """
            UPDATE paper_benchmark_experiments
            SET
                status = 'PAUSED',
                last_transition_by = 'operator:test',
                last_transition_at = NOW(),
                last_transition_reason = 'PAUSED_BY_OPERATOR'
            WHERE id = %s
            """,
            (experiment_id,),
        )
        cursor.execute(
            """
            UPDATE strategy_versions
            SET status = 'RETIRED', retired_at = NOW()
            WHERE version = 'momentum-report-swing-v1'
            """
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="strategy version is not active",
        ):
            cursor.execute(
                """
                UPDATE paper_benchmark_experiments
                SET
                    status = 'RUNNING',
                    last_transition_by = 'operator:test',
                    last_transition_at = NOW() + INTERVAL '1 second',
                    last_transition_reason = 'RESUMED_BY_OPERATOR'
                WHERE id = %s
                """,
                (experiment_id,),
            )


def test_start_rejects_a_non_initial_paper_ledger(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        cursor.execute(
            """
            UPDATE paper_benchmark_experiments
            SET
                status = 'APPROVED',
                approved_by = 'operator:test',
                approved_at = NOW() - INTERVAL '1 day'
            WHERE id = %s
            """,
            (experiment_id,),
        )
        cursor.execute(
            """
            UPDATE balance
            SET cash = 19999, total_value = 19999
            """
        )
        start_available_at = datetime.now(timezone.utc)
        start_event_time = start_available_at - timedelta(minutes=15)
        start_level_id = _insert_start_level(
            cursor,
            experiment_id,
            event_time=start_event_time,
            received_at=start_available_at,
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="clean initial paper ledger",
        ):
            cursor.execute(
                """
                UPDATE paper_benchmark_experiments
                SET
                    status = 'RUNNING',
                    started_by = 'operator:test',
                    started_at = %s,
                    benchmark_start_level_id = %s,
                    benchmark_start_level = 100,
                    benchmark_start_event_time = %s,
                    benchmark_start_available_at = %s,
                    benchmark_start_checksum_sha256 = %s
                WHERE id = %s
                """,
                (
                    start_available_at,
                    start_level_id,
                    start_event_time,
                    start_available_at,
                    "9" * 64,
                    experiment_id,
                ),
            )


def test_pause_blocks_trades_outside_the_frozen_experiment(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)
        cursor.execute(
            """
            UPDATE paper_benchmark_experiments
            SET
                status = 'PAUSED',
                last_transition_by = 'operator:test',
                last_transition_at = NOW(),
                last_transition_reason = 'PAUSED_BY_OPERATOR'
            WHERE id = %s
            """,
            (experiment_id,),
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="paper trades are blocked",
        ):
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
                    strategy_version
                )
                VALUES (
                    'VOLV-B',
                    'BUY',
                    1,
                    100,
                    100,
                    'must remain blocked',
                    'paused-trade-test',
                    %s,
                    'momentum-report-swing-v1'
                )
                """,
                ("d" * 64,),
            )


def test_approved_preregistration_is_immutable(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="preregistration is immutable",
        ):
            cursor.execute(
                """
                UPDATE paper_benchmark_experiments
                SET fee_bps = fee_bps + 1
                WHERE id = %s
                """,
                (experiment_id,),
            )


def test_experiment_cannot_be_inserted_past_draft(connection):
    with connection.cursor() as cursor:
        reference_snapshot_id = _insert_reference_snapshot(cursor)
        quote_contract_id = _insert_provider_contract(
            cursor,
            contract_key="insert-bypass-quotes",
            data_type="delayed-post-trade-equity",
            expected_instruments=416,
        )
        benchmark_contract_id = _insert_provider_contract(
            cursor,
            contract_key="insert-bypass-index",
            data_type="delayed-index-level",
            expected_instruments=1,
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="must be created as a draft",
        ):
            _insert_experiment(
                cursor,
                quote_contract_id=quote_contract_id,
                benchmark_contract_id=benchmark_contract_id,
                reference_snapshot_id=reference_snapshot_id,
                initial_status="APPROVED",
            )


def test_referenced_provider_contract_identity_is_immutable(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        cursor.execute(
            """
            SELECT quote_provider_contract_id
            FROM paper_benchmark_experiments
            WHERE id = %s
            """,
            (experiment_id,),
        )
        contract_id = cursor.fetchone()[0]

        for field, value in (
            ("contract_key", "rewritten-contract-key"),
            ("provider", "rewritten-provider"),
            ("terms_url", "https://example.test/rewritten-terms"),
        ):
            cursor.execute("SAVEPOINT rewrite_provider_contract")
            with pytest.raises(
                psycopg2.errors.RaiseException,
                match="provider contract evidence is immutable",
            ):
                cursor.execute(
                    f"""
                    UPDATE market_data_provider_contracts
                    SET {field} = %s
                    WHERE id = %s
                    """,
                    (value, contract_id),
                )
            cursor.execute(
                "ROLLBACK TO SAVEPOINT rewrite_provider_contract"
            )

        cursor.execute(
            """
            UPDATE market_data_provider_contracts
            SET
                status = 'REVOKED',
                revoked_at = NOW(),
                revoked_by = 'operator:test',
                revocation_reason = 'Test provider revocation.',
                updated_at = NOW()
            WHERE id = %s
            """,
            (contract_id,),
        )
        cursor.execute(
            """
            SELECT status
            FROM market_data_provider_contracts
            WHERE id = %s
            """,
            (contract_id,),
        )
        assert cursor.fetchone()[0] == "REVOKED"


def test_referenced_draft_contract_can_complete_validation(connection):
    with connection.cursor() as cursor:
        reference_snapshot_id = _insert_reference_snapshot(cursor)
        quote_contract_id = _insert_provider_contract(
            cursor,
            contract_key="draft-contract-validation",
            data_type="delayed-post-trade-equity",
            expected_instruments=416,
            status="DRAFT",
        )
        benchmark_contract_id = _insert_provider_contract(
            cursor,
            contract_key="draft-contract-validation-index",
            data_type="delayed-index-level",
            expected_instruments=1,
        )
        _insert_experiment(
            cursor,
            quote_contract_id=quote_contract_id,
            benchmark_contract_id=benchmark_contract_id,
            reference_snapshot_id=reference_snapshot_id,
        )

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
            ("b" * 64, quote_contract_id),
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
                %s, 'PASSED', NOW(), NOW() + INTERVAL '30 days',
                416, 416, 416, 5, 100, 920, %s, 'operator:test',
                'VERIFIED', %s, %s, 5,
                CURRENT_DATE - 6, CURRENT_DATE - 2, %s
            )
            """,
            (
                quote_contract_id,
                "c" * 64,
                reference_snapshot_id,
                "a" * 64,
                "d" * 64,
            ),
        )
        cursor.execute(
            """
            SELECT
                status,
                governance_evidence_status,
                legal_reviewed_at IS NOT NULL,
                transport_verified_at IS NOT NULL
            FROM market_data_provider_contracts
            WHERE id = %s
            """,
            (quote_contract_id,),
        )
        assert cursor.fetchone() == ("VALIDATED", "VERIFIED", True, True)

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="provider contract evidence is immutable",
        ):
            cursor.execute(
                """
                UPDATE market_data_provider_contracts
                SET legal_reviewed_at = NOW() - INTERVAL '1 day'
                WHERE id = %s
                """,
                (quote_contract_id,),
            )


def test_referenced_snapshot_identity_is_immutable(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        cursor.execute(
            """
            SELECT reference_snapshot_id
            FROM paper_benchmark_experiments
            WHERE id = %s
            """,
            (experiment_id,),
        )
        snapshot_id = cursor.fetchone()[0]

        for field, value in (
            ("instrument_count", 999),
            ("universe_checksum_sha256", "0" * 64),
        ):
            cursor.execute("SAVEPOINT rewrite_reference_snapshot")
            with pytest.raises(
                psycopg2.errors.RaiseException,
                match="reference snapshot is immutable",
            ):
                cursor.execute(
                    f"""
                    UPDATE reference_data_snapshots
                    SET {field} = %s
                    WHERE id = %s
                    """,
                    (value, snapshot_id),
                )
            cursor.execute(
                "ROLLBACK TO SAVEPOINT rewrite_reference_snapshot"
            )


def test_draft_preregistration_cannot_be_rewritten_during_approval(
    connection,
):
    with connection.cursor() as cursor:
        reference_snapshot_id = _insert_reference_snapshot(cursor)
        quote_contract_id = _insert_provider_contract(
            cursor,
            contract_key="draft-freeze-quotes",
            data_type="delayed-post-trade-equity",
            expected_instruments=416,
        )
        execution_contract_id = _insert_provider_contract(
            cursor,
            contract_key="draft-freeze-level1",
            data_type="delayed-pre-trade-equity",
            expected_instruments=416,
        )
        benchmark_contract_id = _insert_provider_contract(
            cursor,
            contract_key="draft-freeze-index",
            data_type="delayed-index-level",
            expected_instruments=1,
        )
        experiment_id = _insert_experiment(
            cursor,
            quote_contract_id=quote_contract_id,
            benchmark_contract_id=benchmark_contract_id,
            reference_snapshot_id=reference_snapshot_id,
        )
        cursor.execute(
            """
            SELECT preregistration_payload
            FROM paper_benchmark_experiments
            WHERE id = %s
            """,
            (experiment_id,),
        )
        payload = cursor.fetchone()[0]
        payload["execution_price_source"] = (
            "TOP_OF_BOOK_PLUS_SLIPPAGE"
        )
        payload["execution_provider_contract_key"] = (
            "draft-freeze-level1"
        )
        payload["costs"]["spread_bps"] = 0
        canonical_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        payload_hash = hashlib.sha256(
            canonical_json.encode("utf-8")
        ).hexdigest()

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="preregistration is immutable",
        ):
            cursor.execute(
                """
                UPDATE paper_benchmark_experiments
                SET
                    status = 'APPROVED',
                    approved_by = 'operator:test',
                    approved_at = NOW(),
                    execution_price_source =
                        'TOP_OF_BOOK_PLUS_SLIPPAGE',
                    execution_provider_contract_id = %s,
                    spread_bps = 0,
                    preregistration_payload = %s,
                    preregistration_canonical_json = %s,
                    preregistration_hash = %s
                WHERE id = %s
                """,
                (
                    execution_contract_id,
                    json.dumps(payload),
                    canonical_json,
                    payload_hash,
                    experiment_id,
                ),
            )


def test_database_rejects_payload_hash_that_does_not_match_evidence(
    connection,
):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="preregistration is immutable",
        ):
            cursor.execute(
                """
                UPDATE paper_benchmark_experiments
                SET preregistration_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, experiment_id),
            )


def test_running_experiment_auto_tags_trades_and_ai_decisions(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)
        cursor.execute("SELECT CURRENT_TIMESTAMP")
        now = cursor.fetchone()[0]
        source_quote_id = _insert_market_quote(
            cursor,
            price=100,
            event_time=now - timedelta(minutes=15),
            received_at=now,
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
                source_quote_id
            )
            VALUES (
                'VOLV-B',
                'BUY',
                1,
                100,
                100,
                'benchmark audit test',
                %s,
                %s,
                'momentum-report-swing-v1',
                %s
            )
            RETURNING benchmark_experiment_id
            """,
            (
                f"benchmark-trade-{datetime.now(timezone.utc).timestamp()}",
                "d" * 64,
                source_quote_id,
            ),
        )
        trade_experiment_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO ai_decisions (
                decisions_json,
                market_data_json,
                raw_response,
                strategy_version,
                strategy_config_hash
            )
            VALUES (
                '{"decisions":[]}',
                '{}',
                '{}',
                'momentum-report-swing-v1',
                %s
            )
            RETURNING benchmark_experiment_id
            """,
            ("7a363941bdffa31f8bb204ad0a0828404b5fe57d83da752a66758fbfd6fd0f50",),
        )
        decision_experiment_id = cursor.fetchone()[0]

    assert trade_experiment_id == experiment_id
    assert decision_experiment_id == experiment_id


def test_last_trade_fill_rejects_quote_from_another_provider_contract(
    connection,
):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)
        cursor.execute(
            """
            SELECT reference_snapshot_id
            FROM paper_benchmark_experiments
            WHERE id = %s
            """,
            (experiment_id,),
        )
        reference_snapshot_id = cursor.fetchone()[0]
        wrong_contract_id = _insert_provider_contract(
            cursor,
            contract_key="wrong-fill-contract",
            data_type="delayed-post-trade-equity",
            expected_instruments=416,
        )
        cursor.execute("SELECT CURRENT_TIMESTAMP")
        now = cursor.fetchone()[0]
        source_quote_id = _insert_market_quote(
            cursor,
            price=100,
            event_time=now - timedelta(minutes=15),
            received_at=now,
            contract_id=wrong_contract_id,
            reference_snapshot_id=reference_snapshot_id,
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="exact contract and snapshot",
        ):
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
                    source_quote_id
                )
                VALUES (
                    'VOLV-B',
                    'BUY',
                    1,
                    100,
                    100,
                    'wrong provider contract must fail',
                    'wrong-fill-provider-contract',
                    %s,
                    'momentum-report-swing-v1',
                    %s
                )
                """,
                ("d" * 64, source_quote_id),
            )


def test_last_trade_fill_rejects_quote_outside_frozen_snapshot(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)
        cursor.execute(
            """
            SELECT
                execution_provider_contract_id,
                reference_snapshot_id
            FROM paper_benchmark_experiments
            WHERE id = %s
            """,
            (experiment_id,),
        )
        contract_id, reference_snapshot_id = cursor.fetchone()
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
                'SE9999999999',
                'ROGUE',
                'Rogue instrument',
                'XSTO',
                'SEK',
                'COMMON_STOCK',
                TRUE,
                'ACTIVE',
                'test-reference',
                CURRENT_DATE
            )
            RETURNING id
            """
        )
        rogue_instrument_id = cursor.fetchone()[0]
        cursor.execute("SELECT CURRENT_TIMESTAMP")
        now = cursor.fetchone()[0]
        source_quote_id = _insert_market_quote(
            cursor,
            price=100,
            event_time=now - timedelta(minutes=15),
            received_at=now,
            contract_id=contract_id,
            reference_snapshot_id=reference_snapshot_id,
            ticker="ROGUE",
            instrument_id=rogue_instrument_id,
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="exact contract and snapshot",
        ):
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
                    source_quote_id
                )
                VALUES (
                    'ROGUE',
                    'BUY',
                    1,
                    100,
                    100,
                    'outside snapshot must fail',
                    'outside-frozen-snapshot',
                    %s,
                    'momentum-report-swing-v1',
                    %s
                )
                """,
                ("d" * 64, source_quote_id),
            )


def test_file_bound_market_quote_evidence_cannot_change_or_disappear(
    connection,
):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)
        cursor.execute("SELECT CURRENT_TIMESTAMP")
        now = cursor.fetchone()[0]
        source_quote_id = _insert_market_quote(
            cursor,
            price=100,
            event_time=now - timedelta(minutes=15),
            received_at=now,
        )

        cursor.execute("SAVEPOINT update_source_quote")
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="quote evidence is append-only",
        ):
            cursor.execute(
                """
                UPDATE market_quotes
                SET last_price = 999
                WHERE id = %s
                """,
                (source_quote_id,),
            )
        cursor.execute("ROLLBACK TO SAVEPOINT update_source_quote")

        for field, value in (
            ("bid_price", Decimal("99")),
            ("ask_price", Decimal("101")),
            ("raw_payload", json.dumps({"rewritten": True})),
        ):
            cursor.execute("SAVEPOINT rewrite_source_quote")
            with pytest.raises(
                psycopg2.errors.RaiseException,
                match="quote evidence is append-only",
            ):
                cursor.execute(
                    f"""
                    UPDATE market_quotes
                    SET {field} = %s
                    WHERE id = %s
                    """,
                    (value, source_quote_id),
                )
            cursor.execute("ROLLBACK TO SAVEPOINT rewrite_source_quote")

        cursor.execute("SAVEPOINT delete_source_quote")
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="quote evidence is append-only",
        ):
            cursor.execute(
                "DELETE FROM market_quotes WHERE id = %s",
                (source_quote_id,),
            )
        cursor.execute("ROLLBACK TO SAVEPOINT delete_source_quote")
        cursor.execute(
            "SELECT last_price FROM market_quotes WHERE id = %s",
            (source_quote_id,),
        )
        assert Decimal(cursor.fetchone()[0]) == Decimal("100")


def test_benchmark_trade_execution_cannot_be_updated_or_deleted(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)
        cursor.execute("SELECT CURRENT_TIMESTAMP")
        now = cursor.fetchone()[0]
        source_quote_id = _insert_market_quote(
            cursor,
            price=100,
            event_time=now - timedelta(minutes=15),
            received_at=now,
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
                source_quote_id
            )
            VALUES (
                'VOLV-B',
                'BUY',
                1,
                100,
                100,
                'append-only benchmark fill',
                'append-only-benchmark-fill',
                %s,
                'momentum-report-swing-v1',
                %s
            )
            RETURNING id
            """,
            ("d" * 64, source_quote_id),
        )
        trade_id = cursor.fetchone()[0]

        cursor.execute("SAVEPOINT update_benchmark_trade")
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="execution evidence is append-only",
        ):
            cursor.execute(
                "UPDATE trades SET shares = 2 WHERE id = %s",
                (trade_id,),
            )
        cursor.execute("ROLLBACK TO SAVEPOINT update_benchmark_trade")

        cursor.execute("SAVEPOINT delete_benchmark_trade")
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="execution evidence is append-only",
        ):
            cursor.execute(
                "DELETE FROM trades WHERE id = %s",
                (trade_id,),
            )
        cursor.execute("ROLLBACK TO SAVEPOINT delete_benchmark_trade")

        cursor.execute("SAVEPOINT rewrite_benchmark_reasoning")
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="execution evidence is append-only",
        ):
            cursor.execute(
                """
                UPDATE trades
                SET reasoning = 'rewritten after the fact'
                WHERE id = %s
                """,
                (trade_id,),
            )
        cursor.execute(
            "ROLLBACK TO SAVEPOINT rewrite_benchmark_reasoning"
        )

        cursor.execute(
            """
            UPDATE trades
            SET
                outcome = 'validated later',
                outcome_correct = TRUE,
                stop_loss = 95,
                stop_loss_pct = 1
            WHERE id = %s
            """,
            (trade_id,),
        )
        cursor.execute(
            """
            SELECT shares, outcome, stop_loss
            FROM trades
            WHERE id = %s
            """,
            (trade_id,),
        )
        shares, outcome, stop_loss = cursor.fetchone()
        assert Decimal(shares) == Decimal("1")
        assert outcome == "validated later"
        assert Decimal(stop_loss) == Decimal("95")


def test_running_top_of_book_benchmark_uses_exact_level1_cost_contract(
    connection,
    monkeypatch,
):
    with connection.cursor() as cursor:
        reference_snapshot_id = _insert_reference_snapshot(cursor)
        quote_contract_id = _insert_provider_contract(
            cursor,
            contract_key="level1-benchmark-valuations",
            data_type="delayed-post-trade-equity",
            expected_instruments=416,
        )
        execution_contract_id = _insert_provider_contract(
            cursor,
            contract_key="level1-benchmark-execution",
            data_type="delayed-pre-trade-equity",
            expected_instruments=416,
            max_transport_lag_seconds=60,
        )
        benchmark_contract_id = _insert_provider_contract(
            cursor,
            contract_key="level1-benchmark-index",
            data_type="delayed-index-level",
            expected_instruments=1,
        )
        experiment_id = _insert_experiment(
            cursor,
            quote_contract_id=quote_contract_id,
            execution_price_source="TOP_OF_BOOK_PLUS_SLIPPAGE",
            execution_contract_id=execution_contract_id,
            benchmark_contract_id=benchmark_contract_id,
            reference_snapshot_id=reference_snapshot_id,
        )
        _approve_and_start(cursor, experiment_id)
        cursor.execute("SELECT CURRENT_TIMESTAMP")
        received_at = cursor.fetchone()[0]
        state_id = _insert_pretrade_book(
            cursor,
            contract_id=execution_contract_id,
            reference_snapshot_id=reference_snapshot_id,
            received_at=received_at,
        )
    connection.commit()

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    database = Database()
    trade_id = database.log_trade(
        {
            "ticker": "TEST-1",
            "action": "BUY",
            "shares": Decimal("10"),
            "price": Decimal("101"),
            "total_value": Decimal("1010"),
            "reasoning": "Exact executable Level 1 ask",
            "confidence": Decimal("80"),
            "hypothesis": "Frozen top-of-book execution",
            "macro_context": {},
            "target_price": Decimal("110"),
            "stop_loss": Decimal("95"),
            "target_pct": Decimal("8"),
            "stop_loss_pct": Decimal("-5"),
            "idempotency_key": "benchmark-level1-buy",
            "strategy_version": "momentum-report-swing-v1",
            "source_book_state_id": state_id,
        }
    )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                benchmark_experiment_id,
                source_quote_id,
                source_book_state_id,
                quote_price,
                price,
                total_value,
                fee_amount,
                spread_cost,
                slippage_cost,
                net_cash_effect
            FROM trades
            WHERE id = %s
            """,
            (trade_id,),
        )
        row = cursor.fetchone()
        assert row[0] == experiment_id
        assert row[1] is None
        assert row[2] == state_id
        assert Decimal(row[3]) == Decimal("100.00000000")
        assert Decimal(row[4]) == Decimal("101.05050000")
        assert Decimal(row[5]) == Decimal("1010.51")
        assert Decimal(row[6]) == Decimal("0.51")
        assert Decimal(row[7]) == Decimal("10.00")
        assert Decimal(row[8]) == Decimal("0.51")
        assert Decimal(row[9]) == Decimal("1011.02")

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="fresh two-sided pre-trade book|frozen model",
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
                    idempotency_key,
                    request_fingerprint,
                    strategy_version,
                    benchmark_experiment_id,
                    source_book_state_id
                )
                VALUES (
                    'TEST-1',
                    'BUY',
                    1,
                    100,
                    101.0505,
                    101.05,
                    0.05,
                    0,
                    0.05,
                    101.10,
                    'Tampered spread cost',
                    'benchmark-level1-tampered',
                    %s,
                    'momentum-report-swing-v1',
                    %s,
                    %s
                )
                """,
                ("d" * 64, experiment_id, state_id),
            )


def test_database_trigger_uses_exact_half_spread_before_money_rounding(
    connection,
):
    with connection.cursor() as cursor:
        reference_snapshot_id = _insert_reference_snapshot(cursor)
        quote_contract_id = _insert_provider_contract(
            cursor,
            contract_key="fractional-valuations",
            data_type="delayed-post-trade-equity",
            expected_instruments=416,
        )
        execution_contract_id = _insert_provider_contract(
            cursor,
            contract_key="fractional-execution",
            data_type="delayed-pre-trade-equity",
            expected_instruments=416,
            max_transport_lag_seconds=60,
        )
        benchmark_contract_id = _insert_provider_contract(
            cursor,
            contract_key="fractional-index",
            data_type="delayed-index-level",
            expected_instruments=1,
        )
        experiment_id = _insert_experiment(
            cursor,
            quote_contract_id=quote_contract_id,
            execution_price_source="TOP_OF_BOOK_PLUS_SLIPPAGE",
            execution_contract_id=execution_contract_id,
            benchmark_contract_id=benchmark_contract_id,
            reference_snapshot_id=reference_snapshot_id,
        )
        _approve_and_start(cursor, experiment_id)
        cursor.execute("SELECT CURRENT_TIMESTAMP")
        received_at = cursor.fetchone()[0]
        state_id = _insert_pretrade_book(
            cursor,
            contract_id=execution_contract_id,
            reference_snapshot_id=reference_snapshot_id,
            received_at=received_at,
            bid_price=Decimal("100.00000000"),
            ask_price=Decimal("100.00000001"),
            displayed_quantity=Decimal("2000000"),
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
                source_book_state_id
            )
            VALUES (
                'TEST-1',
                'BUY',
                1000000,
                100.00000001,
                100000000.01,
                'Half-tick spread rounding',
                'benchmark-level1-half-tick',
                %s,
                'momentum-report-swing-v1',
                %s
            )
            RETURNING quote_price, spread_cost
            """,
            ("e" * 64, state_id),
        )
        quote_price, spread_cost = cursor.fetchone()

    assert Decimal(quote_price) == Decimal("100.00000001")
    assert Decimal(spread_cost) == Decimal("0.01")


def test_running_experiment_rejects_trade_without_source_quote(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="matching fresh source quote",
        ):
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
                    strategy_version
                )
                VALUES (
                    'VOLV-B',
                    'BUY',
                    1,
                    100,
                    100,
                    'must remain blocked',
                    'missing-source-quote',
                    %s,
                    'momentum-report-swing-v1'
                )
                """,
                ("d" * 64,),
            )


def test_running_experiment_rejects_wrong_ai_strategy_hash(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="strategy hash does not match",
        ):
            cursor.execute(
                """
                INSERT INTO ai_decisions (
                    decisions_json,
                    market_data_json,
                    raw_response,
                    strategy_version,
                    strategy_config_hash
                )
                VALUES (
                    '{"decisions":[]}',
                    '{}',
                    '{}',
                    'momentum-report-swing-v1',
                    %s
                )
                """,
                ("0" * 64,),
            )


def test_daily_observations_are_append_only(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)
        session_date = date.today()
        closes_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        opens_at = closes_at - timedelta(hours=8)
        cursor.execute(
            """
            INSERT INTO market_sessions (
                mic,
                session_date,
                opens_at,
                closes_at,
                status,
                source
            )
            VALUES ('XSTO', %s, %s, %s, 'OPEN', 'benchmark-test')
            ON CONFLICT (mic, session_date) DO UPDATE
            SET
                opens_at = EXCLUDED.opens_at,
                closes_at = EXCLUDED.closes_at,
                status = 'OPEN'
            """,
            (session_date, opens_at, closes_at),
        )
        cursor.execute(
            """
            INSERT INTO paper_benchmark_daily_observations (
                experiment_id,
                session_date,
                net_asset_value,
                session_high_nav,
                session_low_nav,
                cash,
                gross_exposure,
                benchmark_level,
                fees,
                spread_cost,
                slippage_cost,
                expected_quote_points,
                received_quote_points,
                event_cutoff_at,
                data_available_at,
                source_checksum_sha256
            )
            VALUES (
                %s, %s, 20000, 20000, 20000, 5000, 15000,
                100, 0, 0, 0,
                416, 416, %s, %s, %s
            )
            """,
            (
                experiment_id,
                session_date,
                closes_at,
                closes_at + timedelta(minutes=15),
                "e" * 64,
            ),
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="observations are append-only",
        ):
            cursor.execute(
                """
                UPDATE paper_benchmark_daily_observations
                SET net_asset_value = 999999
                WHERE experiment_id = %s
                """,
                (experiment_id,),
            )


@pytest.mark.parametrize(
    ("expected_points", "cutoff_delta", "available_delta", "message"),
    [
        (
            1,
            timedelta(0),
            timedelta(minutes=15),
            "expected quote points must match",
        ),
        (
            416,
            timedelta(seconds=1),
            timedelta(minutes=15),
            "cutoff must equal",
        ),
        (
            416,
            timedelta(0),
            timedelta(minutes=16),
            "exceeds the licensed delivery window",
        ),
    ],
)
def test_daily_observation_rejects_self_reported_or_stale_coverage(
    connection,
    expected_points,
    cutoff_delta,
    available_delta,
    message,
):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)
        session_date = date.today()
        closes_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        cursor.execute(
            """
            INSERT INTO market_sessions (
                mic,
                session_date,
                opens_at,
                closes_at,
                status,
                source
            )
            VALUES (
                'XSTO',
                %s,
                %s,
                %s,
                'OPEN',
                'benchmark-test'
            )
            ON CONFLICT (mic, session_date) DO UPDATE
            SET
                opens_at = EXCLUDED.opens_at,
                closes_at = EXCLUDED.closes_at,
                status = 'OPEN'
            """,
            (
                session_date,
                closes_at - timedelta(hours=8),
                closes_at,
            ),
        )

        with pytest.raises(
            psycopg2.errors.RaiseException,
            match=message,
        ):
            cursor.execute(
                """
                INSERT INTO paper_benchmark_daily_observations (
                    experiment_id,
                    session_date,
                    net_asset_value,
                    session_high_nav,
                    session_low_nav,
                    cash,
                    gross_exposure,
                    benchmark_level,
                    fees,
                    spread_cost,
                    slippage_cost,
                    expected_quote_points,
                    received_quote_points,
                    event_cutoff_at,
                    data_available_at,
                    source_checksum_sha256
                )
                VALUES (
                    %s, %s, 20000, 20000, 20000, 5000, 15000,
                    100, 0, 0, 0,
                    %s, %s, %s, %s, %s
                )
                """,
                (
                    experiment_id,
                    session_date,
                    expected_points,
                    expected_points,
                    closes_at + cutoff_delta,
                    closes_at + available_delta,
                    "e" * 64,
                ),
            )


@pytest.mark.parametrize("session_status", ["OPEN", "HALF_DAY"])
def test_post_close_observation_is_derived_and_idempotent(
    connection,
    monkeypatch,
    session_status,
):
    database = None
    try:
        observed_at = datetime.now(timezone.utc)
        session_date = observed_at.astimezone(STOCKHOLM).date()
        closes_at = observed_at - timedelta(minutes=20)
        opens_at = closes_at - timedelta(hours=8)
        quote_received_at = closes_at + timedelta(minutes=15)
        with connection.cursor() as cursor:
            experiment_id = _insert_ready_experiment(cursor)
            _approve_and_start(cursor, experiment_id)
            cursor.execute(
                """
                UPDATE market_sessions
                SET
                    opens_at = %s,
                    closes_at = %s,
                    status = %s,
                    session_type = %s
                WHERE mic = 'XSTO'
                  AND session_date = %s
                """,
                (
                    opens_at,
                    closes_at,
                    session_status,
                    (
                        "HALF_DAY"
                        if session_status == "HALF_DAY"
                        else "REGULAR"
                    ),
                    session_date,
                ),
            )
            cursor.execute(
                """
                SELECT
                    experiment.reference_snapshot_id,
                    experiment.quote_provider_contract_id,
                    experiment.benchmark_provider_contract_id,
                    membership.instrument_id
                FROM paper_benchmark_experiments experiment
                JOIN reference_snapshot_instruments membership
                  ON membership.snapshot_id =
                    experiment.reference_snapshot_id
                WHERE experiment.id = %s
                ORDER BY membership.instrument_id
                LIMIT 1
                """,
                (experiment_id,),
            )
            (
                _reference_snapshot_id,
                quote_contract_id,
                benchmark_contract_id,
                instrument_id,
            ) = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO market_data_files (
                    provider,
                    data_type,
                    file_name,
                    report_minute,
                    received_at,
                    source_url,
                    checksum_sha256,
                    content_encoding,
                    compressed_content,
                    uncompressed_bytes,
                    provider_contract_id
                )
                VALUES (
                    'test-provider',
                    'delayed-post-trade-equity',
                    %s,
                    %s,
                    %s,
                    'https://example.test/quotes.csv',
                    %s,
                    'gzip',
                    %s,
                    1,
                    %s
                )
                RETURNING id
                """,
                (
                    f"post-close-{observed_at.timestamp()}",
                    closes_at,
                    quote_received_at,
                    "4" * 64,
                    psycopg2.Binary(b"x"),
                    quote_contract_id,
                ),
            )
            data_file_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO market_quotes (
                    instrument_id,
                    event_time,
                    received_at,
                    source,
                    last_price,
                    currency,
                    data_file_id
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    'test-provider-delayed',
                    100,
                    'SEK',
                    %s
                )
                """,
                (
                    instrument_id,
                    closes_at,
                    quote_received_at,
                    data_file_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO market_index_levels (
                    provider_contract_id,
                    symbol,
                    mic,
                    event_time,
                    received_at,
                    source,
                    level,
                    source_checksum_sha256
                )
                VALUES (
                    %s,
                    'OMXSGI',
                    'XSTO',
                    %s,
                    %s,
                    'test-provider-index',
                    123.45,
                    %s
                )
                """,
                (
                    benchmark_contract_id,
                    closes_at,
                    quote_received_at,
                    "5" * 64,
                ),
            )
        connection.commit()
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        database = Database()
        database.save_portfolio_snapshot(recorded_at=observed_at)

        first_id = database.record_post_close_benchmark_observation(
            observed_at=observed_at,
        )
        second_id = database.record_post_close_benchmark_observation(
            observed_at=observed_at,
        )
        observations = database.query(
            """
            SELECT
                id,
                net_asset_value,
                cash,
                gross_exposure,
                benchmark_level,
                expected_quote_points,
                received_quote_points,
                event_cutoff_at,
                data_available_at
            FROM paper_benchmark_daily_observations
            WHERE experiment_id = :experiment_id
            """,
            {"experiment_id": experiment_id},
        )

        assert first_id == second_id
        assert len(observations) == 1
        assert float(observations[0]["net_asset_value"]) == 20000
        assert float(observations[0]["cash"]) == 20000
        assert float(observations[0]["gross_exposure"]) == 0
        assert float(observations[0]["benchmark_level"]) == pytest.approx(
            123.45
        )
        assert observations[0]["expected_quote_points"] == 416
        assert observations[0]["received_quote_points"] == 1
        assert observations[0]["event_cutoff_at"] == closes_at
        assert observations[0]["data_available_at"] == quote_received_at
    finally:
        if database is not None:
            database.engine.dispose()
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE
                    paper_benchmark_evaluations,
                    paper_benchmark_daily_observations,
                    paper_benchmark_incidents,
                    paper_benchmark_lifecycle_events,
                    portfolio_valuation_marks,
                    portfolio_history,
                    paper_benchmark_experiments,
                    market_index_levels,
                    market_quotes,
                    market_data_files,
                    market_data_provider_validations,
                    market_data_provider_contracts
                RESTART IDENTITY CASCADE
                """
            )
            cursor.execute(
                "TRUNCATE reference_snapshot_instruments"
            )
            cursor.execute(
                """
                DELETE FROM reference_data_snapshots
                WHERE provider = 'test-reference'
                """
            )
            cursor.execute(
                """
                DELETE FROM data_sync_runs
                WHERE provider = 'test-reference'
                """
            )
            cursor.execute(
                """
                DELETE FROM instruments
                WHERE source = 'test-reference'
                  AND symbol LIKE 'TEST-%'
                """
            )
            cursor.execute(
                """
                UPDATE balance
                SET cash = 20000, total_value = 20000, updated_at = NOW()
                """
            )
        connection.commit()


def test_evaluation_is_derived_from_append_only_daily_observations(connection):
    with connection.cursor() as cursor:
        experiment_id = _insert_ready_experiment(cursor)
        _approve_and_start(cursor, experiment_id)
        start_date = (
            datetime.now(timezone.utc).astimezone(STOCKHOLM).date()
            - timedelta(days=252)
        )

        for offset in range(252):
            session_date = start_date + timedelta(days=offset)
            nav = 20000 + (2000 * offset / 251)
            exposure = 15000 + (2000 * offset / 251)
            opens_at = datetime.combine(
                session_date,
                datetime.min.time(),
                timezone.utc,
            ) + timedelta(hours=7)
            closes_at = opens_at + timedelta(hours=8)
            cursor.execute(
                """
                INSERT INTO market_sessions (
                    mic,
                    session_date,
                    opens_at,
                    closes_at,
                    status,
                    source
                )
                VALUES ('XSTO', %s, %s, %s, 'OPEN', 'benchmark-test')
                ON CONFLICT (mic, session_date) DO UPDATE
                SET
                    opens_at = EXCLUDED.opens_at,
                    closes_at = EXCLUDED.closes_at,
                    status = 'OPEN'
                """,
                (session_date, opens_at, closes_at),
            )
            cursor.execute(
                """
                INSERT INTO paper_benchmark_daily_observations (
                    experiment_id,
                    session_date,
                    net_asset_value,
                    session_high_nav,
                    session_low_nav,
                    cash,
                    gross_exposure,
                    benchmark_level,
                    fees,
                    spread_cost,
                    slippage_cost,
                    expected_quote_points,
                    received_quote_points,
                    event_cutoff_at,
                    data_available_at,
                    source_checksum_sha256
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    5000,
                    %s,
                    %s,
                    0,
                    0,
                    0,
                    416,
                    416,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    experiment_id,
                    session_date,
                    nav,
                    nav,
                    nav,
                    exposure,
                    100 + (5 * offset / 251),
                    closes_at,
                    closes_at + timedelta(minutes=15),
                    f"{offset:064x}",
                ),
            )

        trade_closes_at = datetime.combine(
            start_date,
            datetime.min.time(),
            timezone.utc,
        ) + timedelta(hours=15)
        source_quote_id = _insert_market_quote(
            cursor,
            price=100,
            event_time=trade_closes_at - timedelta(minutes=15),
            received_at=trade_closes_at - timedelta(minutes=1),
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
                closes_position,
                source_quote_id,
                executed_at
            )
            SELECT
                'VOLV-B',
                'SELL',
                1,
                100,
                100,
                'derived closed-trade benchmark fixture',
                'benchmark-close-' || %s::TEXT || '-' || series::TEXT,
                REPEAT('d', 64),
                'momentum-report-swing-v1',
                TRUE,
                %s,
                %s
            FROM GENERATE_SERIES(1, 30) series
            """,
            (
                experiment_id,
                source_quote_id,
                trade_closes_at - timedelta(seconds=30),
            ),
        )
        cursor.execute(
            """
            INSERT INTO paper_benchmark_incidents (
                experiment_id,
                incident_key,
                session_date,
                severity,
                description,
                detected_at,
                source_checksum_sha256,
                recorded_by
            )
            VALUES (
                %s,
                'feed-gap-final-session',
                CURRENT_DATE,
                'CRITICAL',
                'Critical feed gap fixture',
                NOW(),
                %s,
                'operator:test'
            )
            """,
            (experiment_id, "f" * 64),
        )

        cursor.execute(
            """
            INSERT INTO paper_benchmark_evaluations (
                experiment_id,
                evaluated_by
            )
            VALUES (%s, 'operator:test')
            RETURNING
                trading_sessions,
                closed_trades,
                net_return_pct,
                benchmark_return_pct,
                excess_return_pct,
                max_drawdown_pct,
                data_coverage_pct,
                critical_incidents,
                passed,
                reason_codes
            """,
            (experiment_id,),
        )
        evaluation = cursor.fetchone()
        cursor.execute(
            """
            SELECT status
            FROM paper_benchmark_experiments
            WHERE id = %s
            """,
            (experiment_id,),
        )
        experiment_status = cursor.fetchone()[0]

    assert evaluation[0] == 252
    assert evaluation[1] == 30
    assert float(evaluation[2]) == pytest.approx(10)
    assert float(evaluation[3]) == pytest.approx(5)
    assert float(evaluation[4]) == pytest.approx(5)
    assert float(evaluation[5]) == pytest.approx(0)
    assert float(evaluation[6]) == pytest.approx(100)
    assert evaluation[7] == 1
    assert evaluation[8] is False
    assert evaluation[9] == ["CRITICAL_INCIDENTS_PRESENT"]
    assert experiment_status == "COMPLETED"


def test_running_experiment_applies_costs_to_cash_and_fifo_pnl(
    connection,
    monkeypatch,
):
    database = None
    try:
        with connection.cursor() as cursor:
            experiment_id = _insert_ready_experiment(cursor)
            _approve_and_start(cursor, experiment_id)
            now = datetime.now(timezone.utc)
            buy_quote_id = _insert_market_quote(
                cursor,
                price=100,
                event_time=now - timedelta(minutes=15),
                received_at=now,
            )
            sell_quote_id = _insert_market_quote(
                cursor,
                price=110,
                event_time=now - timedelta(minutes=14),
                received_at=now,
            )
        connection.commit()
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        database = Database()

        buy_id = database.log_trade(
            {
                "ticker": "VOLV-B",
                "action": "BUY",
                "shares": 10,
                "price": 100,
                "total_value": 1000,
                "reasoning": "cost model buy",
                "idempotency_key": "cost-model-buy",
                "strategy_version": "momentum-report-swing-v1",
                "source_quote_id": buy_quote_id,
            }
        )
        sell_id = database.log_trade(
            {
                "ticker": "VOLV-B",
                "action": "SELL",
                "shares": 10,
                "price": 110,
                "total_value": 1100,
                "reasoning": "cost model sell",
                "idempotency_key": "cost-model-sell",
                "strategy_version": "momentum-report-swing-v1",
                "source_quote_id": sell_quote_id,
            }
        )

        trades = database.query(
            """
            SELECT
                id,
                quote_price,
                price,
                fee_amount,
                spread_cost,
                slippage_cost,
                net_cash_effect,
                target_price,
                stop_loss,
                pnl,
                closes_position,
                benchmark_experiment_id
            FROM trades
            WHERE id IN (:buy_id, :sell_id)
            ORDER BY id
            """,
            {"buy_id": buy_id, "sell_id": sell_id},
        )
        allocation = database.query(
            """
            SELECT
                gross_realized_pnl,
                allocated_entry_fee,
                allocated_exit_fee,
                realized_pnl
            FROM trade_lot_allocations
            WHERE sell_trade_id = :sell_id
            """,
            {"sell_id": sell_id},
        )[0]

        assert float(trades[0]["quote_price"]) == pytest.approx(100)
        assert float(trades[0]["price"]) == pytest.approx(100.1)
        assert float(trades[0]["fee_amount"]) == pytest.approx(0.5)
        assert float(trades[0]["net_cash_effect"]) == pytest.approx(1001.5)
        assert float(trades[0]["target_price"]) == pytest.approx(110.11)
        assert float(trades[0]["stop_loss"]) == pytest.approx(95.095)
        assert trades[0]["benchmark_experiment_id"] == experiment_id
        assert float(trades[1]["price"]) == pytest.approx(109.89)
        assert float(trades[1]["fee_amount"]) == pytest.approx(0.55)
        assert float(trades[1]["net_cash_effect"]) == pytest.approx(1098.35)
        assert float(trades[1]["pnl"]) == pytest.approx(96.85)
        assert trades[1]["closes_position"] is True
        assert float(allocation["gross_realized_pnl"]) == pytest.approx(
            97.9
        )
        assert float(allocation["allocated_entry_fee"]) == pytest.approx(
            0.5
        )
        assert float(allocation["allocated_exit_fee"]) == pytest.approx(
            0.55
        )
        assert float(allocation["realized_pnl"]) == pytest.approx(96.85)
        assert database.get_balance()["cash"] == pytest.approx(20_096.85)
    finally:
        if database is not None:
            database.engine.dispose()
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE
                    paper_benchmark_evaluations,
                    paper_benchmark_daily_observations,
                    paper_benchmark_lifecycle_events,
                    paper_benchmark_experiments,
                    trade_lot_allocations,
                    position_lots,
                    trades,
                    portfolio,
                    market_index_levels,
                    market_quotes,
                    market_data_files,
                    market_data_provider_validations,
                    market_data_provider_contracts
                RESTART IDENTITY CASCADE
                """
            )
            cursor.execute(
                """
                UPDATE companies
                SET instrument_id = NULL
                WHERE ticker = 'VOLV-B'
                  AND instrument_id IN (
                      SELECT id
                      FROM instruments
                      WHERE source = 'test-reference'
                  )
                """
            )
            cursor.execute(
                """
                DELETE FROM instruments
                WHERE source = 'test-reference'
                  AND isin = 'SE0000115446'
                """
            )
            cursor.execute(
                """
                UPDATE balance
                SET cash = 20000, total_value = 20000, updated_at = NOW()
                """
            )
            cursor.execute(
                "TRUNCATE reference_snapshot_instruments"
            )
            cursor.execute(
                """
                DELETE FROM reference_data_snapshots
                WHERE provider = 'test-reference'
                """
            )
            cursor.execute(
                """
                DELETE FROM data_sync_runs
                WHERE provider = 'test-reference'
                """
            )
        connection.commit()


def test_benchmark_readiness_reports_exact_start_prerequisites(connection):
    with connection.cursor() as cursor:
        reference_snapshot_id = _insert_reference_snapshot(cursor)
        quote_key = "readiness-quotes"
        benchmark_key = "readiness-benchmark"
        _insert_provider_contract(
            cursor,
            contract_key=quote_key,
            data_type="delayed-post-trade-equity",
            expected_instruments=416,
        )
        benchmark_contract_id = _insert_provider_contract(
            cursor,
            contract_key=benchmark_key,
            data_type="delayed-index-level",
            expected_instruments=1,
        )
        available_at = datetime.now(timezone.utc)
        cursor.execute(
            """
            INSERT INTO market_index_levels (
                provider_contract_id,
                symbol,
                mic,
                event_time,
                received_at,
                source,
                level,
                source_checksum_sha256
            )
            VALUES (
                %s,
                'OMXSGI',
                'XSTO',
                %s,
                %s,
                'test-provider-index',
                100,
                %s
            )
            """,
            (
                benchmark_contract_id,
                available_at - timedelta(minutes=15),
                available_at,
                "9" * 64,
            ),
        )
    connection.commit()

    result = BenchmarkAdmin(TEST_DATABASE_URL).get_readiness()

    assert result["system_ready_for_preregistration"] is True
    assert result["system_ready_for_start"] is False
    assert result["blockers"] == [
        "APPROVED_PREREGISTRATION_MISSING",
    ]
    assert result["execution_options"] == ["LAST_TRADE_PLUS_BPS"]
    assert result["evidence"]["reference_snapshot_id"] == (
        reference_snapshot_id
    )
    assert result["evidence"]["quote_contract_key"] == quote_key
    assert result["evidence"]["benchmark_contract_key"] == benchmark_key


def test_database_admin_flow_creates_approves_and_starts_frozen_registration(
    connection,
    monkeypatch,
):
    quote_key = "admin-flow-quotes"
    benchmark_key = "admin-flow-benchmark"
    try:
        with connection.cursor() as cursor:
            reference_snapshot_id = _insert_reference_snapshot(cursor)
            _insert_provider_contract(
                cursor,
                contract_key=quote_key,
                data_type="delayed-post-trade-equity",
                expected_instruments=416,
            )
            benchmark_contract_id = _insert_provider_contract(
                cursor,
                contract_key=benchmark_key,
                data_type="delayed-index-level",
                expected_instruments=1,
            )
            start_available_at = datetime.now(timezone.utc)
            start_event_time = (
                start_available_at - timedelta(minutes=15)
            )
            cursor.execute(
                """
                INSERT INTO market_index_levels (
                    provider_contract_id,
                    symbol,
                    mic,
                    event_time,
                    received_at,
                    source,
                    level,
                    source_checksum_sha256
                )
                VALUES (
                    %s,
                    'OMXSGI',
                    'XSTO',
                    %s,
                    %s,
                    'test-provider-index',
                    100,
                    %s
                )
                RETURNING id
                """,
                (
                    benchmark_contract_id,
                    start_event_time,
                    start_available_at,
                    "9" * 64,
                ),
            )
            start_level_id = cursor.fetchone()[0]
        connection.commit()
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        database = BenchmarkAdmin()
        registration = BenchmarkRegistration(
            experiment_key="admin-flow-forward-paper",
            strategy_version="momentum-report-swing-v1",
            strategy_config_hash=(
                "7a363941bdffa31f8bb204ad0a0828404"
                "b5fe57d83da752a66758fbfd6fd0f50"
            ),
            release_sha="a" * 40,
            release_manifest_sha256="2" * 64,
            agent_image_digest_sha256="3" * 64,
            model_backend="openai-compatible",
            model_name="frozen-test-model",
            model_evidence_sha256="4" * 64,
            reference_snapshot_id=reference_snapshot_id,
            universe_checksum_sha256="a" * 64,
            quote_provider_contract_key=quote_key,
            execution_price_source="LAST_TRADE_PLUS_BPS",
            execution_provider_contract_key=quote_key,
            benchmark_provider_contract_key=benchmark_key,
            benchmark_source_url=(
                "https://indexes.nasdaqomx.com/Index/Overview/OMXSGI"
            ),
            benchmark_terms_url="https://example.test/benchmark-terms",
            fee_bps=5,
            spread_bps=10,
            slippage_bps=5,
            proposed_by="operator:test",
        )

        experiment_id = database.create_paper_benchmark_draft(
            registration,
        )
        database.approve_paper_benchmark(
            registration.experiment_key,
            approved_by="operator:test",
        )
        database.start_paper_benchmark(
            registration.experiment_key,
            evidence=BenchmarkStartEvidence(
                level_id=start_level_id,
                benchmark_level=100,
                event_time=start_event_time,
                available_at=start_available_at,
                source_checksum_sha256="9" * 64,
            ),
            started_by="operator:test",
        )
        status = database.get_paper_benchmark(
            registration.experiment_key,
        )

        assert experiment_id == status["id"]
        assert status["status"] == "RUNNING"
        assert status["preregistration_hash"] == registration.payload_hash
        assert status["benchmark_start_level_id"] == start_level_id
        assert float(status["benchmark_start_level"]) == pytest.approx(100)

        database.pause_paper_benchmark(
            registration.experiment_key,
            paused_by="operator:test",
        )
        assert database.get_paper_benchmark(
            registration.experiment_key,
        )["status"] == "PAUSED"
        database.resume_paper_benchmark(
            registration.experiment_key,
            resumed_by="operator:test",
        )
        assert database.get_paper_benchmark(
            registration.experiment_key,
        )["status"] == "RUNNING"
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT from_status, to_status, actor
                FROM paper_benchmark_lifecycle_events
                WHERE experiment_id = %s
                ORDER BY occurred_at, id
                """,
                (experiment_id,),
            )
            lifecycle = cursor.fetchall()
        assert lifecycle == [
            (None, "DRAFT", "operator:test"),
            ("DRAFT", "APPROVED", "operator:test"),
            ("APPROVED", "RUNNING", "operator:test"),
            ("RUNNING", "PAUSED", "operator:test"),
            ("PAUSED", "RUNNING", "operator:test"),
        ]
    finally:
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE
                    paper_benchmark_evaluations,
                    paper_benchmark_daily_observations,
                    paper_benchmark_experiments,
                    market_index_levels,
                    market_data_provider_validations,
                    market_data_provider_contracts
                RESTART IDENTITY CASCADE
                """
            )
            cursor.execute(
                "TRUNCATE reference_snapshot_instruments"
            )
            cursor.execute(
                """
                DELETE FROM reference_data_snapshots
                WHERE provider = 'test-reference'
                """
            )
            cursor.execute(
                """
                DELETE FROM data_sync_runs
                WHERE provider = 'test-reference'
                """
            )
        connection.commit()
