from datetime import date, datetime, time, timedelta, timezone

import pytest

from src.core.backtest import (
    BacktestCosts,
    BacktestDataError,
    CorporateAction,
    DailyBar,
    EquityPoint,
    FoldResult,
    TradingSession,
    UniverseMembership,
    WalkForwardFold,
    _calculate_metrics,
    aggregate_fold_results,
    build_walk_forward_folds,
    run_fold,
)
from src.core.strategy import baseline_strategy


def make_sessions(count=9):
    start = date(2026, 1, 5)
    return tuple(
        TradingSession(
            session_date=start + timedelta(days=index),
            opens_at=datetime.combine(
                start + timedelta(days=index),
                time(8),
                tzinfo=timezone.utc,
            ),
            closes_at=datetime.combine(
                start + timedelta(days=index),
                time(16),
                tzinfo=timezone.utc,
            ),
        )
        for index in range(count)
    )


def make_bar(
    instrument_id,
    session,
    close,
    *,
    open_price=None,
    high=None,
    low=None,
    volume=100_000,
    available_at=None,
):
    open_price = close if open_price is None else open_price
    high = max(open_price, close) if high is None else high
    low = min(open_price, close) if low is None else low
    return DailyBar(
        instrument_id=instrument_id,
        session_date=session.session_date,
        event_time=session.closes_at,
        available_at=available_at or session.closes_at + timedelta(minutes=5),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def dataset(*, instrument_closes=None, overrides=None):
    sessions = make_sessions()
    closes = instrument_closes or [100, 100, 100, 90, 110, 110, 112, 114, 116]
    overrides = overrides or {}
    bars = []
    for index, session in enumerate(sessions):
        values = overrides.get(index, {})
        bars.append(make_bar(
            1,
            session,
            closes[index],
            **values,
        ))
        bars.append(make_bar(99, session, 100))
    memberships = (
        UniverseMembership(
            instrument_id=1,
            valid_from=sessions[0].session_date,
            valid_to=sessions[-1].session_date,
            sector="Industrials",
        ),
    )
    fold = WalkForwardFold(
        fold_number=1,
        train_start=sessions[0].session_date,
        train_end=sessions[3].session_date,
        test_start=sessions[4].session_date,
        test_end=sessions[-1].session_date,
    )
    return sessions, tuple(bars), memberships, fold


def zero_costs():
    return BacktestCosts(
        fee_bps=0,
        spread_bps=0,
        slippage_bps=0,
        max_volume_participation=0.01,
        min_daily_turnover=0,
    )


def run_synthetic(*, costs=None, actions=(), **dataset_changes):
    sessions, bars, memberships, fold = dataset(**dataset_changes)
    return run_fold(
        fold=fold,
        sessions=sessions,
        bars=bars,
        memberships=memberships,
        corporate_actions=actions,
        benchmark_instrument_id=99,
        strategy=baseline_strategy(),
        costs=costs or zero_costs(),
        sma_window=3,
        momentum_lookback=3,
    )


def test_signal_at_close_executes_at_next_session_open():
    result = run_synthetic()

    assert len(result.trades) == 1
    assert result.trades[0].entry_date == date(2026, 1, 10)
    assert result.trades[0].entry_date > date(2026, 1, 9)
    assert result.trades[0].exit_reason == "PERIOD_END"
    assert result.metrics.total_return_pct == pytest.approx(1.36363636)
    assert result.metrics.benchmark_return_pct == pytest.approx(0)


def test_bar_unavailable_before_next_open_fails_closed():
    sessions = make_sessions()
    unavailable = sessions[5].opens_at

    with pytest.raises(BacktestDataError, match="unavailable before next open"):
        run_synthetic(
            overrides={4: {"available_at": unavailable}},
        )


def test_spread_slippage_and_fees_reduce_net_return():
    without_costs = run_synthetic()
    with_costs = run_synthetic(costs=BacktestCosts(
        fee_bps=10,
        spread_bps=20,
        slippage_bps=10,
        max_volume_participation=0.01,
        min_daily_turnover=0,
    ))

    assert with_costs.metrics.total_return_pct < (
        without_costs.metrics.total_return_pct
    )
    assert with_costs.trades[0].fees > 0
    assert with_costs.metrics.turnover_ratio > 0


def test_same_bar_stop_and_target_collision_uses_conservative_stop():
    result = run_synthetic(
        instrument_closes=[100, 100, 100, 90, 110, 100, 100, 100, 100],
        overrides={
            5: {
                "open_price": 100,
                "high": 101,
                "low": 99,
            },
            6: {
                "open_price": 100,
                "high": 115,
                "low": 90,
            },
        },
    )

    assert result.trades[0].exit_reason == "STOP_LOSS"
    assert result.trades[0].exit_price == pytest.approx(95)
    assert result.trades[0].net_pnl < 0


def test_raw_split_and_cash_dividend_preserve_total_return():
    sessions = make_sessions()
    actions = (
        CorporateAction(
            instrument_id=1,
            action_type="SPLIT",
            ex_date=sessions[6].session_date,
            split_ratio=2,
        ),
        CorporateAction(
            instrument_id=1,
            action_type="CASH_DIVIDEND",
            ex_date=sessions[7].session_date,
            cash_amount=1,
        ),
    )
    result = run_synthetic(
        actions=actions,
        instrument_closes=[100, 100, 100, 90, 110, 100, 50, 50, 50],
        overrides={
            5: {"open_price": 100, "high": 101, "low": 99},
            6: {"open_price": 50, "high": 51, "low": 49},
            7: {"open_price": 50, "high": 51, "low": 49},
            8: {"open_price": 50, "high": 51, "low": 49},
        },
    )

    assert result.trades[0].shares == pytest.approx(100)
    assert result.trades[0].gross_pnl == pytest.approx(100)
    assert result.metrics.total_return_pct == pytest.approx(0.5)


def test_delisting_day_cannot_reopen_a_pending_entry():
    sessions = make_sessions()
    result = run_synthetic(actions=(
        CorporateAction(
            instrument_id=1,
            action_type="DELISTING",
            ex_date=sessions[5].session_date,
        ),
    ))

    assert result.trades == ()


def test_historical_membership_prevents_survivorship_entry():
    sessions, bars, _, fold = dataset()
    memberships = (
        UniverseMembership(
            instrument_id=1,
            valid_from=sessions[6].session_date,
            valid_to=sessions[-1].session_date,
            sector="Industrials",
        ),
    )

    result = run_fold(
        fold=fold,
        sessions=sessions,
        bars=bars,
        memberships=memberships,
        corporate_actions=(),
        benchmark_instrument_id=99,
        strategy=baseline_strategy(),
        costs=zero_costs(),
        sma_window=3,
        momentum_lookback=3,
    )

    assert result.trades == ()


def test_universe_exit_executes_at_next_open_without_future_close_lookahead():
    sessions, bars, _, fold = dataset(
        instrument_closes=[100, 100, 100, 90, 110, 100, 100, 50, 50],
        overrides={
            5: {"open_price": 100, "high": 101, "low": 99},
            6: {"open_price": 100, "high": 101, "low": 99},
            7: {"open_price": 50, "high": 50, "low": 50},
            8: {"open_price": 50, "high": 50, "low": 50},
        },
    )
    memberships = (
        UniverseMembership(
            instrument_id=1,
            valid_from=sessions[0].session_date,
            valid_to=sessions[6].session_date,
            sector="Industrials",
        ),
    )

    result = run_fold(
        fold=fold,
        sessions=sessions,
        bars=bars,
        memberships=memberships,
        corporate_actions=(),
        benchmark_instrument_id=99,
        strategy=baseline_strategy(),
        costs=zero_costs(),
        sma_window=3,
        momentum_lookback=3,
    )

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "UNIVERSE_EXIT"
    assert result.trades[0].exit_date == sessions[7].session_date
    assert result.trades[0].exit_price == pytest.approx(50)


def test_reverse_split_does_not_create_false_momentum_signal():
    sessions = make_sessions()
    actions = (
        CorporateAction(
            instrument_id=1,
            action_type="SPLIT",
            ex_date=sessions[4].session_date,
            split_ratio=0.5,
        ),
    )
    result = run_synthetic(
        actions=actions,
        instrument_closes=[100, 100, 100, 100, 200, 200, 200, 200, 200],
    )

    assert result.trades == ()


def test_walk_forward_folds_are_chronological_and_non_overlapping():
    sessions = make_sessions(12)
    folds = build_walk_forward_folds(
        sessions,
        train_sessions=4,
        test_sessions=2,
    )

    assert len(folds) == 4
    assert all(fold.train_end < fold.test_start for fold in folds)
    assert all(
        previous.test_end < current.test_start
        for previous, current in zip(folds, folds[1:])
    )

    with pytest.raises(ValueError, match="may not overlap"):
        build_walk_forward_folds(
            sessions,
            train_sessions=4,
            test_sessions=3,
            step_sessions=2,
        )


def test_single_fold_aggregation_preserves_compounded_metrics():
    result = run_synthetic()

    aggregate = aggregate_fold_results((result,))

    assert aggregate.total_return_pct == pytest.approx(
        result.metrics.total_return_pct
    )
    assert aggregate.benchmark_return_pct == pytest.approx(
        result.metrics.benchmark_return_pct
    )
    assert aggregate.trades_count == result.metrics.trades_count


def test_first_session_loss_is_included_in_max_drawdown():
    metrics = _calculate_metrics(
        initial_cash=20_000,
        equity_curve=(
            EquityPoint(
                session_date=date(2026, 1, 5),
                equity=18_000,
                cash=18_000,
                benchmark_value=20_000,
            ),
            EquityPoint(
                session_date=date(2026, 1, 6),
                equity=18_000,
                cash=18_000,
                benchmark_value=20_000,
            ),
        ),
        trades=(),
        total_traded_value=0,
    )

    assert metrics.max_drawdown_pct == pytest.approx(-10)


def test_aggregate_drawdown_continues_across_fold_boundary():
    first_curve = (
        EquityPoint(date(2026, 1, 5), 26_000, 26_000, 20_000),
        EquityPoint(date(2026, 1, 6), 24_000, 24_000, 20_000),
    )
    second_curve = (
        EquityPoint(date(2026, 1, 7), 19_000, 19_000, 20_000),
        EquityPoint(date(2026, 1, 8), 19_000, 19_000, 20_000),
    )
    results = (
        FoldResult(
            fold=WalkForwardFold(
                1,
                date(2025, 12, 1),
                date(2026, 1, 4),
                date(2026, 1, 5),
                date(2026, 1, 6),
            ),
            metrics=_calculate_metrics(
                initial_cash=20_000,
                equity_curve=first_curve,
                trades=(),
                total_traded_value=0,
            ),
            trades=(),
            equity_curve=first_curve,
        ),
        FoldResult(
            fold=WalkForwardFold(
                2,
                date(2025, 12, 3),
                date(2026, 1, 6),
                date(2026, 1, 7),
                date(2026, 1, 8),
            ),
            metrics=_calculate_metrics(
                initial_cash=20_000,
                equity_curve=second_curve,
                trades=(),
                total_traded_value=0,
            ),
            trades=(),
            equity_curve=second_curve,
        ),
    )

    aggregate = aggregate_fold_results(results)

    assert aggregate.max_drawdown_pct == pytest.approx(
        (22_800 / 26_000 - 1) * 100
    )
