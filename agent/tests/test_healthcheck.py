from datetime import datetime, timezone
import json
from types import SimpleNamespace

from src.healthcheck import (
    HealthMode,
    _probe_model,
    collect_knowledge_graph_health_check,
    collect_health_report,
    main,
)
from src.data.market_data import MarketDataError


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


class _Strategy:
    version = "momentum-report-swing-v1"


class _Instrument:
    pass


class _Session:
    def is_open(self, _now):
        return True


class _Database:
    def __init__(
        self,
        *,
        balance_count=1,
        calendar_count=1,
        instrument_count=416,
        operational_ready=True,
        data_type="delayed-post-trade-equity",
        configured_data_type=None,
        index_ready=True,
        trading_status="ACTIVE",
        daily_loss_breached=False,
        lot_mismatch_count=0,
        learning_status="SUCCEEDED",
        learning_age_seconds=60,
        graph_status="SUCCEEDED",
        graph_age_seconds=60,
        graph_backlog_total=0,
        graph_backlog_growing=False,
        scheduler_expired_claims=0,
        scheduler_age_seconds=60,
        scheduler_session_open=False,
        scheduler_brain_age_seconds=None,
        scheduler_study_age_seconds=None,
        scheduler_brain_status="SUCCEEDED",
    ):
        self.balance_count = balance_count
        self.calendar_count = calendar_count
        self.instruments = tuple(
            _Instrument() for _ in range(instrument_count)
        )
        self.operational_ready = operational_ready
        self.data_type = data_type
        self.configured_data_type = configured_data_type or data_type
        self.index_ready = index_ready
        self.trading_status = trading_status
        self.daily_loss_breached = daily_loss_breached
        self.lot_mismatch_count = lot_mismatch_count
        self.learning_status = learning_status
        self.learning_age_seconds = learning_age_seconds
        self.graph_status = graph_status
        self.graph_age_seconds = graph_age_seconds
        self.graph_backlog_total = graph_backlog_total
        self.graph_backlog_growing = graph_backlog_growing
        self.scheduler_expired_claims = scheduler_expired_claims
        self.scheduler_session_open = scheduler_session_open
        self.scheduler_brain_age_seconds = (
            scheduler_age_seconds
            if scheduler_brain_age_seconds is None
            else scheduler_brain_age_seconds
        )
        self.scheduler_study_age_seconds = (
            scheduler_age_seconds
            if scheduler_study_age_seconds is None
            else scheduler_study_age_seconds
        )
        self.scheduler_brain_status = scheduler_brain_status
        self.operational_requests = []
        self.mode_requests = []
        self.index_requests = []

    def query(self, sql, params=None):
        if "balance_count" in sql:
            return [{"balance_count": self.balance_count}]
        if "lot_mismatch_count" in sql:
            return [{"lot_mismatch_count": self.lot_mismatch_count}]
        if "calendar_count" in sql:
            return [{"calendar_count": self.calendar_count}]
        if "limit_breached" in sql:
            return [{"limit_breached": self.daily_loss_breached}]
        raise AssertionError(f"unexpected health query: {sql}")

    def get_active_strategy(self):
        return _Strategy()

    def get_continuous_learning_runtime_status(self, *, now):
        assert now == NOW
        return {
            "run": {
                "status": self.learning_status,
                "age_seconds": self.learning_age_seconds,
            }
        }

    def get_knowledge_graph_runtime_status(self, *, now):
        assert now == NOW
        return {
            "status": self.graph_status,
            "age_seconds": self.graph_age_seconds,
            "backlog_total": self.graph_backlog_total,
            "backlog_growing": self.graph_backlog_growing,
        }


    def get_scheduled_job_runtime_status(self, *, now):
        assert now == NOW
        return {
            "expired_claim_count": self.scheduler_expired_claims,
            "session_open": self.scheduler_session_open,
            "latest_brain_age_seconds": self.scheduler_brain_age_seconds,
            "latest_brain_status": self.scheduler_brain_status,
            "latest_brain_failure_code": (
                "LLM_RESPONSE_INVALID"
                if self.scheduler_brain_status == "FAILED"
                else None
            ),
            "latest_study_age_seconds": self.scheduler_study_age_seconds,
        }

    def get_trading_control(self):
        return SimpleNamespace(
            status=self.trading_status,
            max_daily_loss_pct=3,
        )

    def get_active_instruments(self, *, mic):
        assert mic == "XSTO"
        return self.instruments

    def require_operational_market_data(self, now):
        self.operational_requests.append(now)
        if not self.operational_ready:
            raise MarketDataError("secret upstream detail")
        return {
            "provider": "nasdaq-nordic",
            "data_type": self.data_type,
            "fresh_quote_count": len(self.instruments),
            "eligible_instrument_count": 1,
            "expected_instrument_count": len(self.instruments),
        }

    def get_authorized_market_data_mode(self, now):
        self.mode_requests.append(now)
        return {
            "provider": "nasdaq-nordic",
            "data_type": self.configured_data_type,
            "usage_scope": "INTERNAL_ANALYSIS_AND_PAPER",
            "authorization_basis": "PUBLIC_NONCOMMERCIAL_TERMS",
        }

    def get_current_authorized_index_change(self, now):
        self.index_requests.append(now)
        if not self.index_ready:
            raise MarketDataError("secret index detail")
        return {
            "symbol": "OMXSGI",
            "provider": "licensed-index",
            "change_pct": 1,
        }


def _by_code(report):
    return {check.code: check for check in report.checks}


def test_readiness_checks_runtime_invariants_without_requiring_market_feed():
    database = _Database(operational_ready=False, index_ready=False)

    report = collect_health_report(
        mode=HealthMode.READINESS,
        database_factory=lambda: database,
        now=NOW,
        environ={},
    )

    assert report.status == "READY"
    assert [check.code for check in report.checks] == [
        "database_schema",
        "ledger_balance",
        "active_strategy",
        "market_calendar",
        "continuous_learning_worker",
        "scheduled_job_recovery",
    ]
    assert database.operational_requests == []
    assert database.index_requests == []


def test_readiness_fails_when_continuous_learning_worker_is_stale():
    report = collect_health_report(
        mode=HealthMode.READINESS,
        database_factory=lambda: _Database(
            learning_age_seconds=901,
        ),
        now=NOW,
        environ={},
    )

    assert report.status == "NOT_READY"
    assert not _by_code(report)["continuous_learning_worker"].ok


def test_readiness_fails_when_knowledge_graph_is_stale_or_backlog_grows():
    stale = collect_knowledge_graph_health_check(
        _Database(graph_age_seconds=901),
        NOW,
    )
    growing = collect_knowledge_graph_health_check(
        _Database(
            graph_backlog_total=12,
            graph_backlog_growing=True,
        ),
        NOW,
    )

    assert not stale.ok
    assert not growing.ok


def test_readiness_fails_when_scheduler_has_an_expired_restart_lease():
    database = _Database(scheduler_expired_claims=1)

    report = collect_health_report(
        mode=HealthMode.READINESS,
        database_factory=lambda: database,
        now=NOW,
        environ={},
    )

    checks = _by_code(report)
    assert report.status == "NOT_READY"
    assert not checks["scheduled_job_recovery"].ok


def test_readiness_fails_when_no_job_has_completed_for_two_hours():
    database = _Database(scheduler_age_seconds=7201)

    report = collect_health_report(
        mode=HealthMode.READINESS,
        database_factory=lambda: database,
        now=NOW,
        environ={},
    )

    checks = _by_code(report)
    assert report.status == "NOT_READY"
    assert not checks["scheduled_job_recovery"].ok


def test_readiness_requires_a_brain_slot_within_35_minutes_when_xsto_is_open():
    database = _Database(
        scheduler_session_open=True,
        scheduler_brain_age_seconds=2101,
        scheduler_study_age_seconds=60,
    )

    report = collect_health_report(
        mode=HealthMode.READINESS,
        database_factory=lambda: database,
        now=NOW,
        environ={},
    )

    checks = _by_code(report)
    assert report.status == "NOT_READY"
    assert not checks["scheduled_job_recovery"].ok


def test_readiness_fails_immediately_when_latest_open_session_brain_job_failed():
    database = _Database(
        scheduler_session_open=True,
        scheduler_brain_age_seconds=60,
        scheduler_brain_status="FAILED",
    )

    report = collect_health_report(
        mode=HealthMode.READINESS,
        database_factory=lambda: database,
        now=NOW,
        environ={},
    )

    assert not _by_code(report)["scheduled_job_recovery"].ok


def test_readiness_fails_closed_when_ledger_invariant_is_broken():
    report = collect_health_report(
        mode=HealthMode.READINESS,
        database_factory=lambda: _Database(balance_count=2),
        now=NOW,
        environ={},
    )

    assert report.status == "NOT_READY"
    assert not _by_code(report)["ledger_balance"].ok


def test_readiness_fails_when_portfolio_and_open_lots_disagree():
    report = collect_health_report(
        mode=HealthMode.READINESS,
        database_factory=lambda: _Database(lot_mismatch_count=1),
        now=NOW,
        environ={},
    )

    assert report.status == "NOT_READY"
    assert not _by_code(report)["ledger_balance"].ok


def test_trading_readiness_requires_provider_sync_and_zero_gaps():
    database = _Database(
        operational_ready=False,
    )

    report = collect_health_report(
        mode=HealthMode.TRADING_READINESS,
        database_factory=lambda: database,
        now=NOW,
        environ={},
        model_probe=lambda _environ: True,
    )

    checks = _by_code(report)
    assert report.status == "NOT_READY"
    assert checks["xsto_universe"].ok
    assert not checks["operational_market_data"].ok
    assert "secret upstream detail" not in json.dumps(report.to_dict())


def test_trading_readiness_passes_with_validated_feed():
    database = _Database()

    report = collect_health_report(
        mode=HealthMode.TRADING_READINESS,
        database_factory=lambda: database,
        now=NOW,
        environ={},
        model_probe=lambda _environ: True,
    )

    assert report.status == "READY"
    assert database.operational_requests == [NOW]
    assert database.index_requests == [NOW]


def test_public_pretrade_readiness_uses_executable_books_without_index():
    database = _Database(
        data_type="delayed-pre-trade-equity",
        index_ready=False,
    )

    report = collect_health_report(
        mode=HealthMode.TRADING_READINESS,
        database_factory=lambda: database,
        now=NOW,
        environ={},
        model_probe=lambda _environ: True,
    )

    checks = _by_code(report)
    assert report.status == "READY"
    assert checks["operational_market_data"].ok
    assert checks["market_index_signal"].ok
    assert database.index_requests == []


def test_premarket_public_paper_mode_does_not_report_omxsgi_as_blocker():
    database = _Database(
        operational_ready=False,
        data_type="delayed-pre-trade-equity",
        index_ready=False,
    )

    report = collect_health_report(
        mode=HealthMode.TRADING_READINESS,
        database_factory=lambda: database,
        now=NOW,
        environ={},
        model_probe=lambda _environ: True,
    )

    checks = _by_code(report)
    assert report.status == "NOT_READY"
    assert not checks["operational_market_data"].ok
    assert checks["market_index_signal"].ok
    assert "not required" in checks["market_index_signal"].detail
    assert database.mode_requests == [NOW]
    assert database.index_requests == []


def test_trading_readiness_requires_authorized_index_signal():
    database = _Database(
        index_ready=False,
    )

    report = collect_health_report(
        mode=HealthMode.TRADING_READINESS,
        database_factory=lambda: database,
        now=NOW,
        environ={},
        model_probe=lambda _environ: True,
    )

    checks = _by_code(report)
    assert report.status == "NOT_READY"
    assert checks["operational_market_data"].ok
    assert not checks["market_index_signal"].ok
    assert "secret index detail" not in json.dumps(report.to_dict())


def test_trading_readiness_fails_when_operator_kill_switch_is_halted():
    report = collect_health_report(
        mode=HealthMode.TRADING_READINESS,
        database_factory=lambda: _Database(trading_status="HALTED"),
        now=NOW,
        environ={},
        model_probe=lambda _environ: True,
    )

    assert report.status == "NOT_READY"
    assert not _by_code(report)["entry_risk_control"].ok


def test_trading_readiness_fails_after_daily_loss_limit_is_latched():
    report = collect_health_report(
        mode=HealthMode.TRADING_READINESS,
        database_factory=lambda: _Database(daily_loss_breached=True),
        now=NOW,
        environ={},
        model_probe=lambda _environ: True,
    )

    assert report.status == "NOT_READY"
    assert not _by_code(report)["entry_risk_control"].ok


def test_trading_readiness_reports_invalid_runtime_config_without_crashing():
    report = collect_health_report(
        mode=HealthMode.TRADING_READINESS,
        database_factory=lambda: _Database(),
        now=NOW,
        environ={"XSTO_MINIMUM_INSTRUMENTS": "not-an-integer"},
        model_probe=lambda _environ: True,
    )

    assert report.status == "NOT_READY"
    assert not _by_code(report)["xsto_universe"].ok
    assert "not-an-integer" not in json.dumps(report.to_dict())


def test_trading_readiness_requires_reachable_configured_model():
    report = collect_health_report(
        mode=HealthMode.TRADING_READINESS,
        database_factory=lambda: _Database(),
        now=NOW,
        environ={},
        model_probe=lambda _environ: False,
    )

    assert report.status == "NOT_READY"
    assert not _by_code(report)["ai_model"].ok


def test_openai_compatible_model_probe_requires_the_configured_model(
    monkeypatch,
):
    requests = []

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"id": "configured-model"}]}

    def get(url, **values):
        requests.append((url, values))
        return _Response()

    monkeypatch.setattr("src.healthcheck.requests.get", get)

    assert _probe_model(
        {
            "LLM_BACKEND": "openai-compatible",
            "LLM_MODEL": "configured-model",
            "OLLAMA_URL": "http://model.internal:1234",
        }
    )
    assert not _probe_model(
        {
            "LLM_BACKEND": "openai-compatible",
            "LLM_MODEL": "missing-model",
            "OLLAMA_URL": "http://model.internal:1234",
        }
    )
    assert requests[0] == (
        "http://model.internal:1234/v1/models",
        {"timeout": 5},
    )


def test_hermes_model_probe_requires_authenticated_gateway_and_model(
    monkeypatch,
    tmp_path,
):
    requests = []
    secret_file = tmp_path / "hermes-api-key"
    secret_file.write_text("file-backed-hermes-test-key\n")
    secret_file.chmod(0o600)

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "providers": [
                    {
                        "slug": "openai-codex",
                        "authenticated": True,
                        "models": [{"id": "gpt-5.6-sol"}],
                    }
                ]
            }

    def get(url, **values):
        requests.append((url, values))
        return _Response()

    monkeypatch.setattr("src.healthcheck.requests.get", get)

    assert _probe_model(
        {
            "LLM_BACKEND": "hermes",
            "LLM_MODEL": "gpt-5.6-sol",
            "HERMES_PROVIDER": "openai-codex",
            "HERMES_URL": "http://100.91.215.127:8642",
            "HERMES_API_KEY_FILE": str(secret_file),
        }
    )
    assert requests == [
        (
            "http://100.91.215.127:8642/api/model/options",
            {
                "headers": {
                    "Authorization": "Bearer file-backed-hermes-test-key",
                },
                "timeout": 5,
            },
        )
    ]


def test_healthcheck_cli_emits_one_json_event(monkeypatch, capsys):
    monkeypatch.setattr(
        "src.healthcheck.Database",
        lambda: _Database(),
    )

    exit_code = main(
        ["readiness"],
        now_factory=lambda: NOW,
        environ={},
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["event"] == "healthcheck_completed"
    assert payload["status"] == "READY"
    assert payload["observed_at"] == "2026-07-29T12:00:00+00:00"
