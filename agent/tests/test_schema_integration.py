import os

import pytest


psycopg2 = pytest.importorskip("psycopg2")


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


REQUIRED_TABLES = {
    "backtest_results",
    "backtest_corporate_actions",
    "backtest_datasets",
    "backtest_equity_curve",
    "backtest_trades",
    "backtest_universe_memberships",
    "data_sync_runs",
    "instrument_aliases",
    "instrument_alias_snapshot_heads",
    "instrument_alias_snapshot_members",
    "instrument_alias_snapshots",
    "instruments",
    "market_bars",
    "market_calendar_snapshots",
    "market_data_files",
    "market_data_object_archives",
    "market_data_gaps",
    "market_data_provider_contracts",
    "market_data_provider_validations",
    "market_index_levels",
    "market_quotes",
    "operational_alert_events",
    "object_archive_runs",
    "market_sessions",
    "news",
    "paper_benchmark_daily_observations",
    "paper_benchmark_evaluations",
    "paper_benchmark_experiments",
    "paper_benchmark_lifecycle_events",
    "paper_execution_cost_policies",
    "portfolio_history",
    "portfolio_valuation_marks",
    "position_lots",
    "pre_trade_batches",
    "pre_trade_book_states",
    "candidate_predictions",
    "candidate_prediction_entries",
    "candidate_prediction_outcomes",
    "candidate_policy_calibration_runs",
    "candidate_policy_versions",
    "continuous_learning_runs",
    "knowledge_graph_sync_runs",
    "knowledge_shadow_runs",
    "knowledge_shadow_decisions",
    "pre_trade_stream_resets",
    "pre_trade_stream_cursors",
    "pre_trade_updates",
    "prospects",
    "report_calendar",
    "report_reactions",
    "reference_data_files",
    "reference_data_object_archives",
    "reference_data_entitlements",
    "reference_snapshot_instruments",
    "reference_data_snapshots",
    "research_notes",
    "schema_migrations",
    "strategy_insights",
    "strategy_change_proposals",
    "strategy_version_learnings",
    "strategy_versions",
    "scheduled_routine_events",
    "scheduled_job_runs",
    "technical_signals",
    "trade_lot_allocations",
    "trading_control_events",
    "trading_controls",
    "trading_daily_risk",
    "trading_risk_evaluations",
    "walk_forward_folds",
    "walk_forward_runs",
}

REQUIRED_TRADE_COLUMNS = {
    "decision_id",
    "decision_origin",
    "entry_trade_id",
    "idempotency_key",
    "pnl",
    "stop_loss",
    "stop_loss_pct",
    "target_pct",
    "target_price",
    "request_fingerprint",
    "source_book_state_id",
    "strategy_version",
    "benchmark_experiment_id",
    "paper_execution_cost_policy_id",
}

REQUIRED_SYNC_COLUMNS = {
    "data_file_id",
    "first_event_time",
    "gap_count",
    "last_event_time",
    "records_inserted",
    "run_key",
}

REQUIRED_INSTRUMENT_COLUMNS = {
    "cfi_code",
    "notional_currency",
    "reference_snapshot_date",
}

REQUIRED_COMPANY_COLUMNS = {
    "sector_source",
    "sector_verified_at",
}

REQUIRED_REFERENCE_MEMBERSHIP_COLUMNS = {
    "cfi_code",
    "currency",
    "first_trade_date",
    "frozen_evidence_status",
    "instrument_type",
    "last_trade_date",
    "mic",
    "name",
    "notional_currency",
    "primary_listing",
    "source",
    "status",
    "symbol",
}

REQUIRED_ALIAS_SNAPSHOT_COLUMNS = {
    "alias_checksum_sha256",
    "business_date",
    "compressed_content",
    "file_checksum_sha256",
    "entitlement_id",
    "instrument_count",
    "reference_checksum_sha256",
    "reference_snapshot_id",
    "source_path",
    "sync_run_id",
    "tip_version",
}

REQUIRED_ALIAS_MEMBER_COLUMNS = {
    "instrument_id",
    "isin",
    "provider_symbol",
    "snapshot_id",
    "source_id",
    "tradable_id",
    "trading_currency",
}

REQUIRED_REFERENCE_ENTITLEMENT_COLUMNS = {
    "revoked_at",
    "revoked_by",
    "revocation_reason",
}

REQUIRED_PROVIDER_CONTRACT_COLUMNS = {
    "authorization_basis",
    "derived_storage_allowed",
    "policy_verified_at",
    "policy_verified_by",
    "governance_evidence_status",
    "raw_storage_allowed",
    "retention_policy",
    "proposed_by",
    "reviewed_by",
    "revocation_reason",
    "revoked_at",
    "revoked_by",
    "terms_checksum_sha256",
}

REQUIRED_PROVIDER_VALIDATION_COLUMNS = {
    "acceptance_checksum_sha256",
    "validation_basis",
    "acceptance_session_count",
    "first_session_date",
    "governance_evidence_status",
    "last_session_date",
    "reference_checksum_sha256",
    "reference_snapshot_id",
}

REQUIRED_AI_DECISION_COLUMNS = {
    "benchmark_experiment_id",
    "model_backend",
    "model_name",
    "model_provider",
    "reasoning_effort",
    "response_id",
    "response_model",
    "strategy_config_hash",
    "strategy_version",
}

REQUIRED_MARKET_BAR_COLUMNS = {
    "available_at",
}

REQUIRED_MARKET_QUOTE_COLUMNS = {
    "data_file_id",
}

REQUIRED_MARKET_DATA_FILE_COLUMNS = {
    "provider_contract_id",
}

REQUIRED_PORTFOLIO_HISTORY_COLUMNS = {
    "benchmark_experiment_id",
}

REQUIRED_PORTFOLIO_MARK_COLUMNS = {
    "quote_id",
    "source_book_state_id",
}

REQUIRED_PAPER_BENCHMARK_COLUMNS = {
    "benchmark_start_level_id",
    "execution_price_source",
    "execution_provider_contract_id",
}

REQUIRED_TRADING_CONTROL_COLUMNS = {
    "changed_at",
    "changed_by",
    "max_daily_loss_pct",
    "reason",
    "status",
}


@pytest.fixture()
def connection():
    assert TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def test_runtime_schema_is_complete(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
        tables = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'trades'
            """
        )
        trade_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'data_sync_runs'
            """
        )
        sync_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'instruments'
            """
        )
        instrument_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'companies'
            """
        )
        company_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = 'reference_snapshot_instruments'
            """
        )
        reference_membership_columns = {
            row[0] for row in cursor.fetchall()
        }

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = 'instrument_alias_snapshots'
            """
        )
        alias_snapshot_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = 'instrument_alias_snapshot_members'
            """
        )
        alias_member_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = 'reference_data_entitlements'
            """
        )
        reference_entitlement_columns = {
            row[0] for row in cursor.fetchall()
        }

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = 'market_data_provider_contracts'
            """
        )
        provider_contract_columns = {
            row[0] for row in cursor.fetchall()
        }

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = 'market_data_provider_validations'
            """
        )
        provider_validation_columns = {
            row[0] for row in cursor.fetchall()
        }

        cursor.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        )
        schema_version = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'ai_decisions'
            """
        )
        ai_decision_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'market_bars'
            """
        )
        market_bar_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'market_quotes'
            """
        )
        market_quote_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = 'market_data_files'
            """
        )
        market_data_file_columns = {
            row[0] for row in cursor.fetchall()
        }

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = 'portfolio_history'
            """
        )
        portfolio_history_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = 'portfolio_valuation_marks'
            """
        )
        portfolio_mark_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = 'paper_benchmark_experiments'
            """
        )
        paper_benchmark_columns = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE
                table_schema = 'public'
                AND table_name = 'trading_controls'
            """
        )
        trading_control_columns = {row[0] for row in cursor.fetchall()}

    assert REQUIRED_TABLES <= tables
    assert REQUIRED_TRADE_COLUMNS <= trade_columns
    assert REQUIRED_SYNC_COLUMNS <= sync_columns
    assert REQUIRED_INSTRUMENT_COLUMNS <= instrument_columns
    assert (
        REQUIRED_REFERENCE_MEMBERSHIP_COLUMNS
        <= reference_membership_columns
    )
    assert REQUIRED_ALIAS_SNAPSHOT_COLUMNS <= alias_snapshot_columns
    assert REQUIRED_ALIAS_MEMBER_COLUMNS <= alias_member_columns
    assert (
        REQUIRED_REFERENCE_ENTITLEMENT_COLUMNS
        <= reference_entitlement_columns
    )
    assert REQUIRED_PROVIDER_CONTRACT_COLUMNS <= provider_contract_columns
    assert (
        REQUIRED_PROVIDER_VALIDATION_COLUMNS
        <= provider_validation_columns
    )
    assert schema_version == 48
    assert REQUIRED_COMPANY_COLUMNS <= company_columns
    assert REQUIRED_AI_DECISION_COLUMNS <= ai_decision_columns
    assert REQUIRED_MARKET_BAR_COLUMNS <= market_bar_columns
    assert REQUIRED_MARKET_QUOTE_COLUMNS <= market_quote_columns
    assert (
        REQUIRED_MARKET_DATA_FILE_COLUMNS
        <= market_data_file_columns
    )
    assert (
        REQUIRED_PORTFOLIO_HISTORY_COLUMNS
        <= portfolio_history_columns
    )
    assert REQUIRED_PORTFOLIO_MARK_COLUMNS <= portfolio_mark_columns
    assert REQUIRED_PAPER_BENCHMARK_COLUMNS <= paper_benchmark_columns
    assert REQUIRED_TRADING_CONTROL_COLUMNS <= trading_control_columns


def test_alias_activation_rejects_future_or_incomplete_entitlement(
    connection,
):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT PG_GET_FUNCTIONDEF(
                'validate_instrument_alias_snapshot_insert()'::regprocedure
            )
            """
        )
        insert_guard = cursor.fetchone()[0].lower()
        cursor.execute(
            """
            SELECT PG_GET_FUNCTIONDEF(
                'validate_instrument_alias_head_write()'::regprocedure
            )
            """
        )
        head_guard = cursor.fetchone()[0].lower()

    assert "new.received_at > now()" in insert_guard
    assert "new.business_date >" in insert_guard
    assert "entitlement.legal_reviewed_at is null" in insert_guard
    assert "candidate.received_at > now()" in head_guard
    assert "candidate.business_date > stockholm_date" in head_guard
    assert "entitlement.entitlement_verified_at > now()" in head_guard
    assert "entitlement.host_key_fingerprint_sha256 is null" in head_guard


def test_operational_evidence_tables_are_append_only(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO scheduled_routine_events (
                routine_key,
                routine_name,
                scheduled_at,
                status,
                failure_code,
                observed_at
            )
            VALUES (
                '2026-07-30:morning',
                'morning',
                '2026-07-30T05:00:00Z',
                'SUCCEEDED',
                NULL,
                '2026-07-30T05:01:00Z'
            )
            RETURNING id
            """
        )
        routine_event_id = cursor.fetchone()[0]
        with pytest.raises(psycopg2.errors.RaiseException):
            cursor.execute(
                "DELETE FROM scheduled_routine_events WHERE id = %s",
                (routine_event_id,),
            )

        cursor.execute(
            """
            INSERT INTO operational_alert_events (
                alert_key,
                code,
                severity,
                state,
                summary,
                runbook,
                observed_at
            )
            VALUES (
                'schema-test',
                'SCHEMA_OR_MIGRATION_NOT_READY',
                'PAGE',
                'OPEN',
                'Database or required schema is not ready',
                'docs/operations.md',
                NOW()
            )
            RETURNING id
            """
        )
        alert_event_id = cursor.fetchone()[0]
        with pytest.raises(psycopg2.errors.RaiseException):
            cursor.execute(
                """
                UPDATE operational_alert_events
                SET state = 'RESOLVED'
                WHERE id = %s
                """,
                (alert_event_id,),
            )


def test_database_rejects_invalid_trade_values(connection):
    with connection.cursor() as cursor:
        with pytest.raises(psycopg2.errors.CheckViolation):
            cursor.execute(
                """
                INSERT INTO trades (
                    ticker, action, shares, price, total_value, reasoning,
                    idempotency_key, request_fingerprint
                )
                VALUES (
                    'VOLV-B', 'BUY', -1, 100, -100, 'invalid',
                    'invalid-trade-test',
                    '0000000000000000000000000000000000000000000000000000000000000000'
                )
                """
            )


def test_market_data_schema_preserves_identity_and_event_time(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO instruments (
                isin, symbol, name, mic, currency, instrument_type, source
            )
            VALUES (
                'SE0000115446', 'VOLV B', 'Volvo B', 'XSTO', 'SEK',
                'COMMON_STOCK', 'test'
            )
            ON CONFLICT (isin) DO UPDATE
            SET
                symbol = EXCLUDED.symbol,
                name = EXCLUDED.name,
                mic = EXCLUDED.mic,
                currency = EXCLUDED.currency,
                instrument_type = EXCLUDED.instrument_type,
                source = EXCLUDED.source
            RETURNING id
            """
        )
        instrument_id = cursor.fetchone()[0]

        with pytest.raises(psycopg2.errors.CheckViolation):
            cursor.execute(
                """
                INSERT INTO market_quotes (
                    instrument_id, event_time, received_at, source,
                    last_price, currency
                )
                VALUES (
                    %s,
                    '2026-07-29 10:01:00+00',
                    '2026-07-29 10:00:00+00',
                    'test',
                    100,
                    'SEK'
                )
                """,
                (instrument_id,),
            )


def test_trading_control_audit_events_are_append_only(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO trading_control_events (
                event_type,
                previous_status,
                new_status,
                previous_max_daily_loss_pct,
                new_max_daily_loss_pct,
                reason,
                changed_by
            )
            VALUES (
                'CONTROL_UPDATE',
                'ACTIVE',
                'ACTIVE',
                3,
                3,
                'Append-only schema test',
                'operator:test-suite'
            )
            RETURNING id
            """
        )
        event_id = cursor.fetchone()[0]

        with pytest.raises(psycopg2.errors.RaiseException):
            cursor.execute(
                """
                UPDATE trading_control_events
                SET reason = 'tampered'
                WHERE id = %s
                """,
                (event_id,),
            )
