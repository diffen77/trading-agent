from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hypothesis import given, settings, strategies as st
import pytest

from src.core.risk import (
    DecisionValidationError,
    evaluate_entry_risk,
    evaluate_exit,
    validate_decision,
    validate_decision_response,
)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", -1),
        ("confidence", 101),
        ("confidence", float("nan")),
        ("position_size_pct", -10),
        ("position_size_pct", 0),
        ("position_size_pct", 26),
        ("position_size_pct", float("inf")),
    ],
)
def test_buy_decision_rejects_invalid_numeric_values(field, value):
    decision = {
        "action": "BUY",
        "ticker": "VOLV-B",
        "reason": "Momentum",
        "confidence": 75,
        "position_size_pct": 15,
    }
    decision[field] = value

    with pytest.raises(DecisionValidationError):
        validate_decision(decision, allowed_tickers={"VOLV-B"})


@pytest.mark.parametrize("action", ["", "SHORT", "BUY NOW", None])
def test_decision_rejects_unsupported_actions(action):
    with pytest.raises(DecisionValidationError):
        validate_decision(
            {
                "action": action,
                "ticker": "VOLV-B",
                "reason": "Momentum",
                "confidence": 75,
                "position_size_pct": 15,
            },
            allowed_tickers={"VOLV-B"},
        )


def test_decision_rejects_unknown_ticker():
    with pytest.raises(DecisionValidationError):
        validate_decision(
            {
                "action": "BUY",
                "ticker": "NOT-LISTED",
                "reason": "Momentum",
                "confidence": 75,
                "position_size_pct": 15,
            },
            allowed_tickers={"VOLV-B"},
        )


def test_valid_buy_decision_is_normalized_without_mutating_input():
    source = {
        "action": "buy",
        "ticker": " volv-b ",
        "reason": " Momentum ",
        "confidence": "75",
        "position_size_pct": "15",
    }

    result = validate_decision(source, allowed_tickers={"VOLV-B"})

    assert result == {
        "action": "BUY",
        "ticker": "VOLV-B",
        "reason": "Momentum",
        "confidence": 75.0,
        "position_size_pct": 15.0,
    }
    assert source["action"] == "buy"


def test_strategy_position_cap_is_enforced_by_input_validator():
    with pytest.raises(DecisionValidationError, match="at most 10"):
        validate_decision(
            {
                "action": "BUY",
                "ticker": "VOLV-B",
                "reason": "Momentum",
                "confidence": 75,
                "position_size_pct": 15,
            },
            allowed_tickers={"VOLV-B"},
            max_position_pct=10,
        )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"decisions": {}, "market_outlook": "neutral", "analysis_summary": ""},
        {"decisions": [], "market_outlook": "certain", "analysis_summary": ""},
        {"decisions": [], "market_outlook": "neutral", "analysis_summary": 123},
    ],
)
def test_decision_response_rejects_invalid_root_shape(payload):
    with pytest.raises(DecisionValidationError):
        validate_decision_response(payload)


def test_valid_decision_response_is_copied():
    source = {
        "decisions": [],
        "market_outlook": "neutral",
        "analysis_summary": "Avvakta",
    }

    result = validate_decision_response(source)

    assert result == source
    assert result is not source


def test_static_stop_loss_triggers_at_five_percent_loss():
    now = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)

    decision = evaluate_exit(
        entry_price=100,
        current_price=95,
        opened_at=now - timedelta(days=2),
        now=now,
    )

    assert decision.should_sell is True
    assert decision.reason == "STOP_LOSS"
    assert decision.pnl_pct == pytest.approx(-5)


def test_take_profit_triggers_at_ten_percent_gain():
    now = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)

    decision = evaluate_exit(
        entry_price=100,
        current_price=110,
        opened_at=now - timedelta(days=2),
        now=now,
    )

    assert decision.should_sell is True
    assert decision.reason == "TAKE_PROFIT"


def test_five_percent_gain_activates_two_percent_trailing_floor():
    now = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)

    decision = evaluate_exit(
        entry_price=100,
        current_price=105,
        opened_at=now - timedelta(days=2),
        now=now,
    )

    assert decision.should_sell is False
    assert decision.new_stop_loss == pytest.approx(102)


def test_stored_trailing_stop_is_enforced_after_price_reverses():
    now = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)

    decision = evaluate_exit(
        entry_price=100,
        current_price=101.5,
        opened_at=now - timedelta(days=3),
        now=now,
        stored_stop_loss=102,
    )

    assert decision.should_sell is True
    assert decision.reason == "TRAILING_STOP"


def test_time_stop_triggers_after_ten_days_below_three_percent_gain():
    now = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)

    decision = evaluate_exit(
        entry_price=100,
        current_price=102.9,
        opened_at=now - timedelta(days=10),
        now=now,
    )

    assert decision.should_sell is True
    assert decision.reason == "TIME_STOP"


@pytest.mark.parametrize(
    ("entry_price", "current_price"),
    [(0, 100), (-1, 100), (100, 0), (100, float("nan"))],
)
def test_exit_evaluation_rejects_invalid_prices(entry_price, current_price):
    now = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)

    with pytest.raises(ValueError):
        evaluate_exit(
            entry_price=entry_price,
            current_price=current_price,
            opened_at=now,
            now=now,
        )


def test_manual_kill_switch_blocks_new_entries_even_when_profitable():
    decision = evaluate_entry_risk(
        trading_status="HALTED",
        opening_equity=20_000,
        current_equity=20_500,
        max_daily_loss_pct=3,
    )

    assert decision.allowed is False
    assert decision.reason == "TRADING_HALTED"
    assert decision.daily_return_pct == pytest.approx(2.5)


def test_daily_loss_limit_blocks_new_entries_at_the_exact_boundary():
    decision = evaluate_entry_risk(
        trading_status="ACTIVE",
        opening_equity=20_000,
        current_equity=19_400,
        max_daily_loss_pct=3,
    )

    assert decision.allowed is False
    assert decision.reason == "DAILY_LOSS_LIMIT"
    assert decision.daily_return_pct == pytest.approx(-3)


def test_entry_remains_allowed_immediately_above_daily_loss_limit():
    decision = evaluate_entry_risk(
        trading_status="ACTIVE",
        opening_equity=20_000,
        current_equity=19_400.01,
        max_daily_loss_pct=3,
    )

    assert decision.allowed is True
    assert decision.reason is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trading_status", "UNKNOWN"),
        ("opening_equity", 0),
        ("opening_equity", float("nan")),
        ("current_equity", -1),
        ("current_equity", float("inf")),
        ("max_daily_loss_pct", 0),
        ("max_daily_loss_pct", 20.01),
    ],
)
def test_entry_risk_rejects_invalid_control_or_equity_values(field, value):
    arguments = {
        "trading_status": "ACTIVE",
        "opening_equity": 20_000,
        "current_equity": 20_000,
        "max_daily_loss_pct": 3,
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        evaluate_entry_risk(**arguments)


@given(
    opening_equity=st.decimals(
        min_value="0.01",
        max_value="1000000",
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    current_equity=st.decimals(
        min_value="0",
        max_value="1200000",
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    max_daily_loss_pct=st.decimals(
        min_value="0.01",
        max_value="20",
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)
@settings(max_examples=200)
def test_entry_risk_gate_matches_the_exact_mark_to_market_property(
    opening_equity,
    current_equity,
    max_daily_loss_pct,
):
    decision = evaluate_entry_risk(
        trading_status="ACTIVE",
        opening_equity=opening_equity,
        current_equity=current_equity,
        max_daily_loss_pct=max_daily_loss_pct,
    )
    expected_return = (
        (current_equity / opening_equity) - Decimal("1")
    ) * Decimal("100")
    expected_allowed = expected_return > -max_daily_loss_pct

    assert decision.allowed is expected_allowed
    assert decision.reason == (
        None if expected_allowed else "DAILY_LOSS_LIMIT"
    )
    assert decision.daily_return_pct == pytest.approx(
        float(expected_return),
    )


@given(
    entry_price=st.decimals(
        min_value="1",
        max_value="10000",
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    current_ratio_pct=st.decimals(
        min_value="50",
        max_value="150",
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)
@settings(max_examples=200)
def test_exit_policy_partitions_price_space_without_unclassified_gaps(
    entry_price,
    current_ratio_pct,
):
    now = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)
    current_price = entry_price * current_ratio_pct / Decimal("100")

    decision = evaluate_exit(
        entry_price=entry_price,
        current_price=current_price,
        opened_at=now - timedelta(days=2),
        now=now,
    )
    expected_pnl = (
        (float(current_price) / float(entry_price)) - 1
    ) * 100

    if expected_pnl >= 10:
        assert (decision.should_sell, decision.reason) == (
            True,
            "TAKE_PROFIT",
        )
        assert decision.new_stop_loss is None
    elif expected_pnl <= -5:
        assert (decision.should_sell, decision.reason) == (
            True,
            "STOP_LOSS",
        )
        assert decision.new_stop_loss is None
    elif expected_pnl >= 5:
        assert decision.should_sell is False
        assert decision.reason is None
        assert decision.new_stop_loss == pytest.approx(
            float(entry_price) * 1.02,
        )
    else:
        assert decision.should_sell is False
        assert decision.reason is None
        assert decision.new_stop_loss is None

    assert decision.pnl_pct == pytest.approx(expected_pnl)


@given(
    confidence=st.decimals(
        min_value="0",
        max_value="100",
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
    position_size_pct=st.decimals(
        min_value="0.01",
        max_value="25",
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)
@settings(max_examples=200)
def test_valid_buy_numeric_domain_is_preserved_by_normalization(
    confidence,
    position_size_pct,
):
    normalized = validate_decision(
        {
            "action": "buy",
            "ticker": " volv-b ",
            "reason": " Property test ",
            "confidence": confidence,
            "position_size_pct": position_size_pct,
        },
        allowed_tickers={"VOLV-B"},
    )

    assert normalized["action"] == "BUY"
    assert normalized["ticker"] == "VOLV-B"
    assert normalized["confidence"] == pytest.approx(float(confidence))
    assert normalized["position_size_pct"] == pytest.approx(
        float(position_size_pct),
    )
