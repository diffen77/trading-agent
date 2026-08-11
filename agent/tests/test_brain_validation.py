from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import src.core.brain as brain_module
from src.core.brain import TradingBrain
from src.core.strategy import baseline_strategy
from src.data.market_data import MarketDataError, QuoteRecord
from src.model_config import validate_hermes_url


class FakePortfolio:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.empty = not self.rows

    def iterrows(self):
        return enumerate(self.rows)


class FakeDatabase:
    def __init__(self, portfolio=None):
        self.portfolio = FakePortfolio(portfolio)

    def get_balance(self):
        return {"cash": 20_000, "total_value": 20_000}

    def get_portfolio(self):
        return self.portfolio

    def get_active_strategy(self):
        return baseline_strategy()

    def require_operational_market_data(self, _now):
        return {
            "provider": "test-delayed",
            "fresh_quote_count": 3,
            "expected_instrument_count": 3,
        }

    def get_current_authorized_index_change(self, _now):
        return {
            "symbol": "OMXSGI",
            "change_pct": 0.0,
            "provider": "test-index",
        }

    def get_latest_authorized_market_quote(self, _ticker):
        return QuoteRecord(
            quote_id=1,
            isin="SE0000115446",
            mic="XSTO",
            event_time=datetime(
                2026, 7, 29, 9, 45, tzinfo=timezone.utc
            ),
            received_at=datetime(
                2026, 7, 29, 10, 0, tzinfo=timezone.utc
            ),
            source="test-delayed",
            last_price=100,
            currency="SEK",
            volume=1000,
        )

    def query(self, sql, params=None):
        if "FROM companies" in sql:
            return [
                {"ticker": "VOLV-B", "sector": "Industrials"},
                {"ticker": "ATCO-A", "sector": "Industrials"},
                {"ticker": "SAND", "sector": "Industrials"},
            ]
        if "FROM market_sessions" in sql:
            return [
                {
                    "mic": "XSTO",
                    "session_date": datetime(2026, 7, 29).date(),
                    "opens_at": datetime(
                        2026, 7, 29, 7, 0, tzinfo=timezone.utc
                    ),
                    "closes_at": datetime(
                        2026, 7, 29, 15, 30, tzinfo=timezone.utc
                    ),
                    "timezone_name": "Europe/Stockholm",
                    "source": "test-calendar",
                    "status": "OPEN",
                }
            ]
        if "FROM technical_signals" in sql:
            return [
                {
                    "date": datetime(2026, 7, 29).date(),
                    "rsi": 50,
                    "sma20": 90,
                }
            ]
        return []


def make_brain(db=None):
    brain = TradingBrain.__new__(TradingBrain)
    brain.db = db or FakeDatabase()
    brain._now_utc = lambda: datetime(
        2026, 7, 29, 10, 0, tzinfo=timezone.utc
    )
    return brain


def test_portfolio_context_keeps_cash_and_unpriced_position_visible():
    database = FakeDatabase(portfolio=[{
        "ticker": "SE0010415281",
        "shares": 36_873.2,
        "avg_price": 0.14,
    }])
    database.get_latest_authorized_market_quote = lambda _ticker: None
    brain = make_brain(database)

    context = brain._get_portfolio_context()

    assert "Cash: 20000 SEK" in context
    assert "SE0010415281" in context
    assert "färsk verifierad kurs saknas" in context
    assert "Portföljdata ej tillgänglig" not in context


def test_brain_uses_explicit_openai_compatible_backend(monkeypatch):
    calls = []

    class _OpenAI:
        def __init__(self, **values):
            calls.append(values)

    monkeypatch.setattr(brain_module, "HAS_OPENAI", True)
    monkeypatch.setattr(brain_module, "OpenAI", _OpenAI, raising=False)
    monkeypatch.setenv("LLM_BACKEND", "openai-compatible")
    monkeypatch.setenv("LLM_MODEL", "local-model")
    monkeypatch.setenv("OLLAMA_URL", "http://model.internal:1234")
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)

    brain = TradingBrain(object())

    assert brain.backend == "openai-compatible"
    assert brain.model == "local-model"
    assert calls == [
        {
            "base_url": "http://model.internal:1234/v1",
            "api_key": "local-no-auth",
        }
    ]


def test_openai_compatible_backend_uses_chat_completions():
    create_calls = []
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"decisions": []}'),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=17,
            completion_tokens=5,
        ),
    )
    brain = make_brain()
    brain.backend = "openai-compatible"
    brain.model = "local-model"
    brain.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **values: (
                    create_calls.append(values) or response
                ),
            ),
        ),
    )

    result = brain._call_llm(
        system="system",
        user_msg="user",
        max_tokens=123,
    )

    assert result == ('{"decisions": []}', 17, 5)
    assert create_calls == [
        {
            "model": "local-model",
            "max_tokens": 123,
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            "temperature": 0.3,
        }
    ]


def test_brain_uses_explicit_hermes_responses_backend(
    monkeypatch,
    tmp_path,
):
    calls = []

    class _OpenAI:
        def __init__(self, **values):
            calls.append(values)

    secret_file = tmp_path / "hermes-api-key"
    secret_file.write_text("file-backed-hermes-test-key\n")
    secret_file.chmod(0o600)
    monkeypatch.setattr(brain_module, "HAS_OPENAI", True)
    monkeypatch.setattr(brain_module, "OpenAI", _OpenAI, raising=False)
    monkeypatch.setenv("LLM_BACKEND", "hermes")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "medium")
    monkeypatch.setenv("HERMES_PROVIDER", "openai-codex")
    monkeypatch.setenv("HERMES_URL", "http://100.91.215.127:8642")
    monkeypatch.setenv("HERMES_API_KEY_FILE", str(secret_file))

    brain = TradingBrain(object())

    assert brain.backend == "hermes"
    assert brain.model == "gpt-5.6-sol"
    assert brain.model_provider == "openai-codex"
    assert brain.reasoning_effort == "medium"
    assert calls == [
        {
            "base_url": "http://100.91.215.127:8642/v1",
            "api_key": "file-backed-hermes-test-key",
        }
    ]


def test_hermes_url_accepts_tailnet_https():
    assert (
        validate_hermes_url("https://trading-hermes.example.ts.net/")
        == "https://trading-hermes.example.ts.net"
    )


def test_hermes_backend_uses_responses_api_with_locked_runtime():
    create_calls = []
    response = SimpleNamespace(
        id="resp_test",
        model="gpt-5.6-sol",
        output_text='{"decisions": []}',
        usage=SimpleNamespace(
            input_tokens=23,
            output_tokens=7,
        ),
    )
    brain = make_brain()
    brain.backend = "hermes"
    brain.model = "gpt-5.6-sol"
    brain.model_provider = "openai-codex"
    brain.reasoning_effort = "medium"
    brain.client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **values: (
                create_calls.append(values) or response
            ),
        ),
    )

    result = brain._call_llm(
        system="system",
        user_msg="user",
        max_tokens=321,
    )

    assert result == ('{"decisions": []}', 23, 7)
    assert brain.last_response_id == "resp_test"
    assert brain.last_response_model == "gpt-5.6-sol"
    assert create_calls == [
        {
            "model": "gpt-5.6-sol",
            "instructions": "system",
            "input": "user",
            "max_output_tokens": 321,
            "store": False,
            "extra_body": {
                "provider": "openai-codex",
                "model_options": {
                    "reasoning": {
                        "enabled": True,
                        "effort": "medium",
                    },
                },
            },
        }
    ]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("HERMES_URL", "http://example.com:8642"),
        ("HERMES_URL", "https://example.com"),
        ("HERMES_URL", "http://user:password@100.91.215.127:8642"),
        ("LLM_MODEL", "gpt-5.4-mini"),
        ("HERMES_PROVIDER", "openrouter"),
        ("LLM_REASONING_EFFORT", "ultra"),
    ],
)
def test_hermes_backend_rejects_unsafe_or_unpinned_configuration(
    monkeypatch,
    tmp_path,
    name,
    value,
):
    secret_file = tmp_path / "hermes-api-key"
    secret_file.write_text("file-backed-hermes-test-key\n")
    secret_file.chmod(0o600)
    monkeypatch.setattr(brain_module, "HAS_OPENAI", True)
    monkeypatch.setenv("LLM_BACKEND", "hermes")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "medium")
    monkeypatch.setenv("HERMES_PROVIDER", "openai-codex")
    monkeypatch.setenv("HERMES_URL", "http://100.91.215.127:8642")
    monkeypatch.setenv("HERMES_API_KEY_FILE", str(secret_file))
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError):
        TradingBrain(object())


def test_brain_prefers_fresh_public_pretrade_signals_in_context():
    checked_at = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    calls = []

    class IntradayDatabase:
        def get_current_authorized_intraday_signals(
            self,
            *,
            now,
            window,
            limit,
        ):
            calls.append((now, window, limit))
            return [
                {
                    "ticker": "VOLV-B",
                    "name": "Volvo B",
                    "sector": "Industrials",
                    "latest_price": 101,
                    "sma20": 100,
                    "momentum_pct": 1,
                    "window": 20,
                    "source": "nasdaq-nordic-public-pretrade",
                }
            ]

        def query(self, *_args, **_kwargs):
            pytest.fail(
                "fresh public pre-trade signals must take precedence over "
                "legacy daily technical rows"
            )

    brain = make_brain(IntradayDatabase())
    brain._now_utc = lambda: checked_at

    context = brain._get_technical_context()

    assert calls == [(checked_at, 20, 50)]
    assert (
        context
        == "VOLV-B (Volvo B, Industrials): 101.00 SEK, SMA20=100.00, "
        "+1.00% mot SMA20 "
        "(20 minuter, nasdaq-nordic-public-pretrade)"
    )


def test_brain_premarket_public_paper_mode_does_not_request_omxsgi():
    class PremarketDatabase(FakeDatabase):
        def require_operational_market_data(self, _now):
            raise MarketDataError("XSTO session is not open")

        def get_authorized_market_data_mode(self, _now):
            return {
                "provider": "nasdaq-nordic",
                "data_type": "delayed-pre-trade-equity",
                "usage_scope": "INTERNAL_ANALYSIS_AND_PAPER",
                "authorization_basis": "PUBLIC_NONCOMMERCIAL_TERMS",
            }

        def get_current_authorized_index_change(self, _now):
            raise AssertionError("OMXSGI must not be requested in public paper mode")

    context = make_brain(PremarketDatabase())._get_macro_context()

    assert "väntar på en öppen XSTO-session" in context
    assert "Separat OMXSGI krävs inte" in context


def test_brain_can_select_anthropic_even_when_openai_is_installed(monkeypatch):
    calls = []

    class _Anthropic:
        def __init__(self, **values):
            calls.append(values)

    monkeypatch.setattr(brain_module, "HAS_OPENAI", True)
    monkeypatch.setattr(brain_module, "HAS_ANTHROPIC", True)
    monkeypatch.setattr(
        brain_module,
        "anthropic",
        SimpleNamespace(Anthropic=_Anthropic),
        raising=False,
    )
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "operator-selected-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-key")

    brain = TradingBrain(object())

    assert brain.backend == "anthropic"
    assert brain.model == "operator-selected-model"
    assert calls == [{"api_key": "test-only-key"}]


def test_brain_reads_anthropic_key_from_runtime_secret_file(
    monkeypatch,
    tmp_path,
):
    calls = []

    class _Anthropic:
        def __init__(self, **values):
            calls.append(values)

    secret_file = tmp_path / "anthropic-key"
    secret_file.write_text("file-backed-test-key\n")
    secret_file.chmod(0o600)
    monkeypatch.setattr(brain_module, "HAS_ANTHROPIC", True)
    monkeypatch.setattr(
        brain_module,
        "anthropic",
        SimpleNamespace(Anthropic=_Anthropic),
        raising=False,
    )
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "operator-selected-model")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", str(secret_file))

    brain = TradingBrain(object())

    assert calls == [{"api_key": "file-backed-test-key"}]


@pytest.mark.parametrize(
    "environment",
    [
        {"LLM_BACKEND": "unknown"},
        {"LLM_BACKEND": "anthropic", "ANTHROPIC_API_KEY": ""},
        {
            "LLM_BACKEND": "openai-compatible",
            "OLLAMA_URL": "http://user:password@model.internal:1234",
        },
    ],
)
def test_brain_rejects_ambiguous_or_unsafe_backend_configuration(
    monkeypatch,
    environment,
):
    monkeypatch.setattr(brain_module, "HAS_OPENAI", True)
    monkeypatch.setattr(brain_module, "HAS_ANTHROPIC", True)
    for name in (
        "LLM_BACKEND",
        "LLM_MODEL",
        "LLM_REASONING_EFFORT",
        "ANTHROPIC_API_KEY",
        "HERMES_API_KEY",
        "HERMES_API_KEY_FILE",
        "HERMES_PROVIDER",
        "HERMES_URL",
        "OLLAMA_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    if environment["LLM_BACKEND"] == "anthropic":
        monkeypatch.setenv("LLM_MODEL", "operator-selected-model")

    with pytest.raises(RuntimeError):
        TradingBrain(object())


def response(decision):
    return {
        "decisions": [decision],
        "market_outlook": "bullish",
        "analysis_summary": "Test",
    }


def buy(ticker="VOLV-B", **changes):
    decision = {
        "action": "BUY",
        "ticker": ticker,
        "reason": "Momentum",
        "confidence": 75,
        "position_size_pct": 15,
    }
    decision.update(changes)
    return decision


def sell(ticker="VOLV-B", **changes):
    decision = {
        "action": "SELL",
        "ticker": ticker,
        "reason": "Risk reduction",
        "confidence": 90,
    }
    decision.update(changes)
    return decision


def test_negative_position_size_from_llm_is_rejected():
    validated = make_brain().validate_decisions(
        response(buy(position_size_pct=-10))
    )

    assert validated == []


def test_unknown_ticker_from_llm_is_rejected():
    validated = make_brain().validate_decisions(response(buy("FAKE-B")))

    assert validated == []


def test_buy_below_sma20_is_rejected_as_mandatory_rule():
    db = FakeDatabase()

    def query(sql, params=None):
        if "FROM companies" in sql:
            return [{"ticker": "VOLV-B", "sector": "Industrials"}]
        if "FROM market_sessions" in sql:
            return FakeDatabase().query(sql, params)
        if "FROM technical_signals" in sql:
            return [
                {
                    "date": datetime(2026, 7, 29).date(),
                    "rsi": 50,
                    "sma20": 105,
                }
            ]
        return []

    db.query = query

    validated = make_brain(db).validate_decisions(response(buy()))

    assert validated == []


def test_stale_technical_signal_blocks_buy():
    db = FakeDatabase()
    original_query = db.query

    def query(sql, params=None):
        if "FROM technical_signals" in sql:
            return [
                {
                    "date": datetime(2026, 7, 28).date(),
                    "rsi": 50,
                    "sma20": 90,
                }
            ]
        return original_query(sql, params)

    db.query = query

    assert make_brain(db).validate_decisions(response(buy())) == []


def test_sell_holding_period_uses_latest_open_buy_and_injected_clock():
    db = FakeDatabase(
        portfolio=[{"ticker": "VOLV-B", "shares": 1}],
    )
    original_query = db.query

    def query(sql, params=None):
        if "FROM trades" in sql:
            if "closed_at IS NULL" in sql:
                return [
                    {
                        "executed_at": datetime(
                            2026, 7, 29, 9, 0, tzinfo=timezone.utc
                        )
                    }
                ]
            return [
                {
                    "executed_at": datetime(
                        2026, 7, 27, 9, 0, tzinfo=timezone.utc
                    )
                }
            ]
        return original_query(sql, params)

    db.query = query

    assert make_brain(db).validate_decisions(response(sell())) == []


def test_third_position_in_same_sector_is_rejected():
    db = FakeDatabase(
        portfolio=[
            {"ticker": "VOLV-B", "shares": 1},
            {"ticker": "ATCO-A", "shares": 1},
        ]
    )

    validated = make_brain(db).validate_decisions(response(buy("SAND")))

    assert validated == []


def test_valid_decision_is_normalized_and_gets_position_value():
    source = buy()

    validated = make_brain().validate_decisions(response(source))

    assert validated == [
        {
            "action": "BUY",
            "ticker": "VOLV-B",
            "reason": "Momentum",
            "confidence": 75.0,
            "position_size_pct": 15.0,
                "position_value": 3000,
                "execution_price": 100.0,
                "source_quote_id": 1,
                "source_book_state_id": None,
                "price_event_time": datetime(
                2026, 7, 29, 9, 45, tzinfo=timezone.utc
            ),
            "price_source": "test-delayed",
            "strategy_version": "momentum-report-swing-v1",
        }
    ]
    assert "position_value" not in source


def test_public_pretrade_buy_uses_book_side_and_intraday_warmup():
    db = FakeDatabase()
    db.require_operational_market_data = lambda _now: {
        "provider": "nasdaq-nordic",
        "data_type": "delayed-pre-trade-equity",
        "eligible_instrument_count": 1,
        "expected_instrument_count": 1,
    }
    db.get_current_authorized_index_change = lambda _now: pytest.fail(
        "licensed index must not block public pre-trade paper mode"
    )
    db.get_latest_authorized_execution_quote = (
        lambda ticker, *, action, now: QuoteRecord(
            book_state_id=73,
            isin="SE0000115446",
            mic="XSTO",
            event_time=now - timedelta(minutes=15),
            received_at=now,
            source="nasdaq-nordic-public-pretrade",
            last_price=101,
            currency="SEK",
            volume=1000,
        )
    )
    db.get_authorized_intraday_signal = (
        lambda ticker, *, now, window: {
            "ticker": ticker,
            "session_date": now.date(),
            "book_state_id": 73,
            "sma20": 100,
            "window": window,
        }
    )

    validated = make_brain(db).validate_decisions(response(buy()))

    assert validated[0]["execution_price"] == 101
    assert validated[0]["source_quote_id"] is None
    assert validated[0]["source_book_state_id"] == 73


def test_stale_intraday_quote_blocks_buy():
    db = FakeDatabase()
    def stale_quote(_ticker):
        return QuoteRecord(
            quote_id=1,
            isin="SE0000115446",
            mic="XSTO",
            event_time=datetime(
                2026, 7, 29, 9, 42, tzinfo=timezone.utc
            ),
            received_at=datetime(
                2026, 7, 29, 10, 0, tzinfo=timezone.utc
            ),
            source="test-delayed",
            last_price=100,
            currency="SEK",
            volume=1000,
        )
    db.get_latest_authorized_market_quote = stale_quote

    assert make_brain(db).validate_decisions(response(buy())) == []


def test_missing_authorized_provider_quote_blocks_buy():
    db = FakeDatabase()
    db.get_latest_authorized_market_quote = lambda _ticker: None

    assert make_brain(db).validate_decisions(response(buy())) == []


def test_incomplete_operational_market_data_blocks_all_decisions():
    db = FakeDatabase()
    db.require_operational_market_data = lambda _now: (
        _ for _ in ()
    ).throw(MarketDataError("full XSTO quote coverage is missing"))
    db.get_latest_authorized_market_quote = lambda _ticker: (
        pytest.fail("per-ticker quote must not bypass the universe gate")
    )

    assert make_brain(db).validate_decisions(response(buy())) == []


def test_missing_authorized_index_signal_blocks_buy():
    db = FakeDatabase()
    db.get_current_authorized_index_change = lambda _now: (
        _ for _ in ()
    ).throw(MarketDataError("authorized OMXSGI signal is missing"))

    assert make_brain(db).validate_decisions(response(buy())) == []


def test_missing_official_market_session_blocks_buy():
    db = FakeDatabase()
    original_query = db.query
    db.query = lambda sql, params=None: (
        [] if "FROM market_sessions" in sql else original_query(sql, params)
    )

    assert make_brain(db).validate_decisions(response(buy())) == []


def test_invalid_llm_json_fails_closed_and_is_audited():
    brain = make_brain()
    brain.backend = "test"
    brain.model = "test-model"
    brain.build_context = lambda deep=False: "context"
    brain._call_llm = lambda **kwargs: ("not-json", 12, 3)
    logged = []
    brain._log_decision = lambda **kwargs: logged.append(kwargs)

    result = brain.make_decisions()

    assert result["decisions"] == []
    assert result["market_outlook"] == "neutral"
    assert len(logged) == 1
    assert logged[0]["prompt_tokens"] == 12
    assert logged[0]["response_tokens"] == 3


def test_ungoverned_news_and_report_rows_never_reach_ai_context():
    calls = []

    class ContextDatabase:
        def query(self, sql, params=None):
            calls.append((sql, params))
            return []

    brain = make_brain(ContextDatabase())

    assert brain._get_news_context() == (
        "Nyhetsdata används inte utan auktoriserad källa och proveniens."
    )
    assert brain._get_reports_context() == (
        "Rapportkalender används inte utan auktoriserad källa och proveniens."
    )
    assert calls == []


def test_decision_prompt_and_audit_use_the_injected_clock():
    fixed_now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    audit_writes = []

    class AuditDatabase:
        def get_active_strategy(self):
            return baseline_strategy()

        def log_ai_decision(self, **values):
            audit_writes.append(values)
            return 73

    brain = make_brain(AuditDatabase())
    brain.backend = "test"
    brain.model = "test-model"
    brain.build_context = lambda deep=False: "context"
    prompts = []

    def call_llm(**kwargs):
        prompts.append(kwargs["user_msg"])
        return (
            '{"decisions":[],"market_outlook":"neutral",'
            '"analysis_summary":"test"}',
            12,
            3,
        )

    brain._call_llm = call_llm

    decisions = brain.make_decisions()

    assert "Datum: 2026-07-29 10:00 UTC" in prompts[0]
    assert audit_writes[0]["timestamp"] == fixed_now
    assert audit_writes[0]["model_backend"] == "test"
    assert audit_writes[0]["model_name"] == "test-model"
    assert audit_writes[0]["model_provider"] is None
    assert audit_writes[0]["reasoning_effort"] is None
    assert decisions["_audit_id"] == 73


def test_candidate_snapshot_is_reused_and_abstentions_are_audited():
    from decimal import Decimal

    fixed_now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    recorded = []

    class CandidateDatabase:
        def get_active_strategy(self):
            return baseline_strategy()

        def get_current_authorized_candidate_signals(self, *, now, limit):
            assert now == fixed_now
            assert limit == 1_000
            return [{
                "ticker": "VOLV-B",
                "name": "Volvo B",
                "sector": "Industrials",
                "provider": "nasdaq-nordic",
                "source": "nasdaq-nordic-delayed-pre-trade",
                "book_state_id": 73,
                "latest_price": Decimal("101.00"),
                "sma20": Decimal("100.70"),
                "price_5m_ago": Decimal("100.80"),
                "price_20m_ago": Decimal("100.40"),
                "price_60m_ago": Decimal("99.50"),
                "bid_price": Decimal("100.98"),
                "ask_price": Decimal("101.02"),
                "bid_quantity": Decimal("1200"),
                "ask_quantity": Decimal("800"),
                "range_20_bps": Decimal("100"),
                "range_60_bps": Decimal("250"),
                "first_report_minute": fixed_now - timedelta(minutes=75),
                "last_report_minute": fixed_now - timedelta(minutes=15),
                "latest_received_at": fixed_now,
            }]

        def log_ai_decision(self, **_values):
            return 73

        def record_candidate_predictions(self, **values):
            recorded.append(values)
            return [91]

    brain = make_brain(CandidateDatabase())
    brain.backend = "test"
    brain.model = "test-model"
    snapshots = []

    def build_context(*, deep=False, candidate_snapshot=None):
        snapshots.append(candidate_snapshot)
        return "candidate context"

    brain.build_context = build_context
    brain._call_llm = lambda **_kwargs: (
        '{"decisions":[],"market_outlook":"neutral",'
        '"analysis_summary":"avstår"}',
        12,
        3,
    )

    decisions = brain.make_decisions()

    assert decisions["_audit_id"] == 73
    assert len(snapshots) == 1
    assert snapshots[0][0]["ticker"] == "VOLV-B"
    assert recorded[0]["ai_decision_id"] == 73
    assert recorded[0]["candidates"] == snapshots[0]
    assert recorded[0]["model_decisions"] == []


def test_learning_feedback_requires_complete_minimum_sample():
    fixed_now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    class LearningDatabase:
        def get_continuous_learning_status(self, *, now):
            assert now == fixed_now
            return {
                "labelled_outcomes": 99,
                "matured_outcomes": 100,
                "overdue_outcomes": 1,
                "coverage_pct": 99.0,
                "action_metrics": [{
                    "action": "BUY",
                    "horizon_minutes": 30,
                    "outcomes": 99,
                    "mean_return_bps": 12.5,
                    "positive_rate_pct": 55.0,
                }],
            }

    context = make_brain(LearningDatabase())._get_learning_context(
        now=fixed_now,
    )

    assert "otillräckligt" in context.lower()
    assert "99 av 100" in context
    assert "ändra inte" in context.lower()
    assert "+12.5" not in context


def test_learning_feedback_returns_horizon_specific_verified_outcomes():
    fixed_now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    class LearningDatabase:
        def get_continuous_learning_status(self, *, now):
            assert now == fixed_now
            return {
                "labelled_outcomes": 120,
                "matured_outcomes": 120,
                "overdue_outcomes": 0,
                "coverage_pct": 100.0,
                "action_metrics": [{
                    "action": "BUY",
                    "horizon_minutes": 30,
                    "outcomes": 120,
                    "mean_return_bps": 12.5,
                    "positive_rate_pct": 55.0,
                }],
            }

    context = make_brain(LearningDatabase())._get_learning_context(
        now=fixed_now,
    )

    assert "BUY 30m" in context
    assert "+12.5 bp marknadsavkastning" in context
    assert "kursen steg i 55.0%" in context
    assert "ändrar aldrig riskregler" in context.lower()


def test_audit_write_failure_blocks_the_decision_cycle():
    class BrokenAuditDatabase:
        def log_ai_decision(self, **_kwargs):
            raise RuntimeError("test audit storage failure")

    brain = make_brain()
    brain.db = BrokenAuditDatabase()

    with pytest.raises(RuntimeError, match="audit storage failure"):
        brain._log_decision(
            prompt_tokens=12,
            response_tokens=3,
            decisions_json={"decisions": []},
            market_context="test",
            strategy=baseline_strategy(),
        )


def test_ai_execution_propagates_parent_decision_id_to_trade():
    captured = []

    class Trader:
        def execute_trade(self, opportunity):
            captured.append(opportunity)
            return True

    brain = make_brain()
    executed = brain.execute_decisions(
        [{
            "action": "BUY",
            "ticker": "VOLV-B",
            "confidence": 75,
            "reason": "Momentum",
            "position_value": 2_000,
            "execution_price": 100,
            "source_quote_id": 41,
        }],
        Trader(),
        cycle_key="brain:test-cycle",
        strategy=baseline_strategy(),
        decision_id=73,
    )

    assert len(executed) == 1
    assert captured[0]["decision_id"] == 73
    assert captured[0]["decision_origin"] == "AI_DECISION"
