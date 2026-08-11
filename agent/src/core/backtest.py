"""Point-in-time, next-session execution engine for deterministic policies."""

from dataclasses import dataclass
from datetime import date, datetime
import math
from statistics import mean, stdev
from typing import Iterable, Mapping, Sequence

from .strategy import ActiveStrategy


ENGINE_VERSION = "point-in-time-v1"


class BacktestDataError(ValueError):
    """Raised when a dataset cannot support an unbiased backtest."""


@dataclass(frozen=True)
class TradingSession:
    session_date: date
    opens_at: datetime
    closes_at: datetime

    def __post_init__(self) -> None:
        if (
            self.opens_at.tzinfo is None
            or self.closes_at.tzinfo is None
            or self.closes_at <= self.opens_at
        ):
            raise BacktestDataError("session timestamps must be ordered and aware")


@dataclass(frozen=True)
class DailyBar:
    instrument_id: int
    session_date: date
    event_time: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(not math.isfinite(value) for value in values):
            raise BacktestDataError("bar values must be finite")
        if (
            self.instrument_id <= 0
            or self.low <= 0
            or self.high < self.low
            or not self.low <= self.open <= self.high
            or not self.low <= self.close <= self.high
            or self.volume < 0
        ):
            raise BacktestDataError("bar has invalid prices or volume")
        if self.event_time.tzinfo is None or self.available_at.tzinfo is None:
            raise BacktestDataError("bar timestamps must be timezone-aware")
        if self.available_at < self.event_time:
            raise BacktestDataError("bar cannot be available before event time")


@dataclass(frozen=True)
class UniverseMembership:
    instrument_id: int
    valid_from: date
    valid_to: date | None
    sector: str

    def contains(self, value: date) -> bool:
        return (
            value >= self.valid_from
            and (self.valid_to is None or value <= self.valid_to)
        )


@dataclass(frozen=True)
class CorporateAction:
    instrument_id: int
    action_type: str
    ex_date: date
    split_ratio: float | None = None
    cash_amount: float | None = None

    def __post_init__(self) -> None:
        if self.action_type not in {"SPLIT", "CASH_DIVIDEND", "DELISTING"}:
            raise BacktestDataError("unsupported corporate action")
        if self.action_type == "SPLIT":
            if (
                self.split_ratio is None
                or not math.isfinite(self.split_ratio)
                or self.split_ratio <= 0
                or self.cash_amount is not None
            ):
                raise BacktestDataError("split action requires a positive ratio")
        elif self.action_type == "CASH_DIVIDEND":
            if (
                self.cash_amount is None
                or not math.isfinite(self.cash_amount)
                or self.cash_amount < 0
                or self.split_ratio is not None
            ):
                raise BacktestDataError(
                    "cash dividend requires a non-negative amount"
                )
        elif self.split_ratio is not None or self.cash_amount is not None:
            raise BacktestDataError("delisting cannot carry split or cash values")


@dataclass(frozen=True)
class BacktestCosts:
    fee_bps: float
    spread_bps: float
    slippage_bps: float
    max_volume_participation: float
    min_daily_turnover: float

    def __post_init__(self) -> None:
        bps_values = (self.fee_bps, self.spread_bps, self.slippage_bps)
        if any(not math.isfinite(value) or value < 0 for value in bps_values):
            raise ValueError("fees, spread and slippage must be non-negative")
        if not 0 < self.max_volume_participation <= 0.25:
            raise ValueError("max_volume_participation must be in (0, 0.25]")
        if (
            not math.isfinite(self.min_daily_turnover)
            or self.min_daily_turnover < 0
        ):
            raise ValueError("min_daily_turnover must be non-negative")


@dataclass(frozen=True)
class WalkForwardFold:
    fold_number: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date


@dataclass(frozen=True)
class BacktestTrade:
    instrument_id: int
    entry_date: date
    exit_date: date
    shares: float
    entry_price: float
    exit_price: float
    gross_pnl: float
    fees: float
    net_pnl: float
    exit_reason: str


@dataclass(frozen=True)
class EquityPoint:
    session_date: date
    equity: float
    cash: float
    benchmark_value: float


@dataclass(frozen=True)
class BacktestMetrics:
    total_return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    turnover_ratio: float
    trades_count: int
    win_rate: float


@dataclass(frozen=True)
class FoldResult:
    fold: WalkForwardFold
    metrics: BacktestMetrics
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[EquityPoint, ...]


@dataclass
class _Position:
    instrument_id: int
    sector: str
    shares: float
    entry_date: date
    entry_price: float
    entry_fee: float
    stop_price: float
    target_price: float
    sessions_held: int = 0
    dividends: float = 0


def build_walk_forward_folds(
    sessions: Sequence[TradingSession],
    *,
    train_sessions: int,
    test_sessions: int,
    step_sessions: int | None = None,
) -> tuple[WalkForwardFold, ...]:
    """Build chronological, non-overlapping out-of-sample folds."""
    ordered = _validate_sessions(sessions)
    if train_sessions < 2 or test_sessions < 1:
        raise ValueError("train_sessions must be >=2 and test_sessions >=1")
    step = test_sessions if step_sessions is None else step_sessions
    if step < test_sessions:
        raise ValueError("out-of-sample folds may not overlap")

    folds = []
    fold_number = 1
    test_start_index = train_sessions
    while test_start_index + test_sessions <= len(ordered):
        train_start_index = test_start_index - train_sessions
        test_end_index = test_start_index + test_sessions - 1
        folds.append(WalkForwardFold(
            fold_number=fold_number,
            train_start=ordered[train_start_index].session_date,
            train_end=ordered[test_start_index - 1].session_date,
            test_start=ordered[test_start_index].session_date,
            test_end=ordered[test_end_index].session_date,
        ))
        fold_number += 1
        test_start_index += step
    if not folds:
        raise ValueError("not enough sessions for one walk-forward fold")
    return tuple(folds)


def validate_point_in_time_dataset(
    *,
    sessions: Sequence[TradingSession],
    bars: Sequence[DailyBar],
    memberships: Sequence[UniverseMembership],
    corporate_actions: Sequence[CorporateAction],
    benchmark_instrument_id: int,
    risk_instrument_id: int | None = None,
) -> None:
    """Validate chronology, complete historical membership and bar availability."""
    ordered_sessions = _validate_sessions(sessions)
    if len(ordered_sessions) < 2:
        raise BacktestDataError("dataset needs at least two sessions")
    session_by_date = {
        session.session_date: session for session in ordered_sessions
    }
    dates = [session.session_date for session in ordered_sessions]
    bar_index = _index_bars(bars)
    membership_index = _index_memberships(memberships)
    _validate_membership_history(membership_index)
    _index_actions(corporate_actions)
    _validate_test_dataset(
        dates=dates,
        test_dates=dates,
        sessions=session_by_date,
        bar_index=bar_index,
        memberships=membership_index,
        benchmark_instrument_id=benchmark_instrument_id,
        risk_instrument_id=(
            benchmark_instrument_id
            if risk_instrument_id is None
            else risk_instrument_id
        ),
    )


def run_fold(
    *,
    fold: WalkForwardFold,
    sessions: Sequence[TradingSession],
    bars: Sequence[DailyBar],
    memberships: Sequence[UniverseMembership],
    corporate_actions: Sequence[CorporateAction],
    benchmark_instrument_id: int,
    risk_instrument_id: int | None = None,
    strategy: ActiveStrategy,
    costs: BacktestCosts,
    initial_cash: float = 20_000,
    sma_window: int = 20,
    momentum_lookback: int = 20,
) -> FoldResult:
    """Run one out-of-sample fold using close signal -> next open execution."""
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if sma_window < 2 or momentum_lookback < 2:
        raise ValueError("signal lookbacks must be at least two sessions")

    ordered_sessions = _validate_sessions(sessions)
    session_by_date = {
        session.session_date: session for session in ordered_sessions
    }
    required_dates = [
        session.session_date
        for session in ordered_sessions
        if fold.train_start <= session.session_date <= fold.test_end
    ]
    test_dates = [
        value
        for value in required_dates
        if fold.test_start <= value <= fold.test_end
    ]
    if not required_dates or not test_dates:
        raise BacktestDataError("fold dates are not covered by sessions")
    if fold.train_end >= fold.test_start:
        raise BacktestDataError("training data must end before test data")

    bar_index = _index_bars(bars)
    membership_index = _index_memberships(memberships)
    action_index = _index_actions(corporate_actions)
    actions_by_instrument: dict[int, tuple[CorporateAction, ...]] = {}
    for action in corporate_actions:
        actions_by_instrument.setdefault(action.instrument_id, ())
        actions_by_instrument[action.instrument_id] += (action,)
    _validate_membership_history(membership_index)
    _validate_test_dataset(
        dates=required_dates,
        test_dates=test_dates,
        sessions=session_by_date,
        bar_index=bar_index,
        memberships=membership_index,
        benchmark_instrument_id=benchmark_instrument_id,
        risk_instrument_id=(
            benchmark_instrument_id
            if risk_instrument_id is None
            else risk_instrument_id
        ),
    )

    benchmark_first = bar_index[(benchmark_instrument_id, test_dates[0])].open
    cash = float(initial_cash)
    positions: dict[int, _Position] = {}
    pending_entries: tuple[int, ...] = ()
    trades: list[BacktestTrade] = []
    equity_curve: list[EquityPoint] = []
    total_traded_value = 0.0
    previous_equity = float(initial_cash)
    config = strategy.config
    delisted_instruments: set[int] = set()

    for index, session_date in enumerate(test_dates):
        current_bars = {
            instrument_id: bar
            for (instrument_id, value), bar in bar_index.items()
            if value == session_date
        }

        for action in action_index.get(session_date, ()):
            if action.action_type == "DELISTING":
                delisted_instruments.add(action.instrument_id)
            position = positions.get(action.instrument_id)
            if position is None:
                continue
            if action.action_type == "SPLIT":
                ratio = float(action.split_ratio)
                position.shares *= ratio
                position.entry_price /= ratio
                position.stop_price /= ratio
                position.target_price /= ratio
            elif action.action_type == "CASH_DIVIDEND":
                dividend = position.shares * float(action.cash_amount)
                position.dividends += dividend
                cash += dividend
            elif action.action_type == "DELISTING":
                bar = current_bars.get(action.instrument_id)
                if bar is None:
                    raise BacktestDataError(
                        "delisting requires a final executable daily bar"
                    )
                cash_delta, trade, traded_value = _close_position(
                    position,
                    session_date=session_date,
                    raw_exit_price=bar.open,
                    exit_reason="DELISTING",
                    costs=costs,
                )
                cash += cash_delta
                total_traded_value += traded_value
                trades.append(trade)
                del positions[action.instrument_id]

        # Membership is evaluated at the current session open. Looking at the
        # next session while still on today's close would leak future universe
        # state into the simulated decision.
        for instrument_id in list(positions):
            if (
                _membership_on(
                    membership_index,
                    instrument_id,
                    session_date,
                )
                is not None
            ):
                continue
            bar = current_bars.get(instrument_id)
            if bar is None:
                raise BacktestDataError(
                    f"universe exit lacks execution bar for {instrument_id}"
                )
            cash_delta, trade, traded_value = _close_position(
                positions[instrument_id],
                session_date=session_date,
                raw_exit_price=bar.open,
                exit_reason="UNIVERSE_EXIT",
                costs=costs,
            )
            cash += cash_delta
            total_traded_value += traded_value
            trades.append(trade)
            del positions[instrument_id]

        for instrument_id in pending_entries:
            if (
                instrument_id in positions
                or instrument_id in delisted_instruments
            ):
                continue
            membership = _membership_on(
                membership_index,
                instrument_id,
                session_date,
            )
            if membership is None:
                continue
            bar = current_bars.get(instrument_id)
            if bar is None:
                raise BacktestDataError(
                    f"pending entry lacks execution bar for {instrument_id}"
                )
            target_value = min(
                previous_equity * config.max_position_pct / 100,
                cash,
            )
            position, cash_used, traded_value = _open_position(
                instrument_id=instrument_id,
                sector=membership.sector,
                session_date=session_date,
                bar=bar,
                target_value=target_value,
                available_cash=cash,
                strategy=strategy,
                costs=costs,
            )
            if position is not None:
                positions[instrument_id] = position
                cash -= cash_used
                total_traded_value += traded_value
        pending_entries = ()

        for instrument_id in list(positions):
            position = positions[instrument_id]
            bar = current_bars.get(instrument_id)
            if bar is None:
                raise BacktestDataError(
                    f"open position lacks bar for {instrument_id}"
                )
            position.sessions_held += 1
            exit_price, reason = _exit_on_bar(position, bar, strategy)
            if reason is None:
                if session_date == test_dates[-1]:
                    exit_price = bar.close
                    reason = "PERIOD_END"

            if reason is not None:
                cash_delta, trade, traded_value = _close_position(
                    position,
                    session_date=session_date,
                    raw_exit_price=exit_price,
                    exit_reason=reason,
                    costs=costs,
                )
                cash += cash_delta
                total_traded_value += traded_value
                trades.append(trade)
                del positions[instrument_id]

        equity = cash + sum(
            position.shares * current_bars[instrument_id].close
            for instrument_id, position in positions.items()
        )
        benchmark_close = current_bars[benchmark_instrument_id].close
        benchmark_value = initial_cash * benchmark_close / benchmark_first
        equity_curve.append(EquityPoint(
            session_date=session_date,
            equity=equity,
            cash=cash,
            benchmark_value=benchmark_value,
        ))
        previous_equity = equity

        if index + 1 < len(test_dates):
            next_session = session_by_date[test_dates[index + 1]]
            pending_entries = _select_candidates(
                signal_date=session_date,
                next_session=next_session,
                history_dates=required_dates,
                bar_index=bar_index,
                memberships=membership_index,
                positions=positions,
                strategy=strategy,
                costs=costs,
                sma_window=sma_window,
                momentum_lookback=momentum_lookback,
                risk_instrument_id=(
                    benchmark_instrument_id
                    if risk_instrument_id is None
                    else risk_instrument_id
                ),
                actions_by_instrument=actions_by_instrument,
            )

    metrics = _calculate_metrics(
        initial_cash=initial_cash,
        equity_curve=equity_curve,
        trades=trades,
        total_traded_value=total_traded_value,
    )
    return FoldResult(
        fold=fold,
        metrics=metrics,
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
    )


def aggregate_fold_results(
    results: Sequence[FoldResult],
    *,
    initial_cash: float = 20_000,
) -> BacktestMetrics:
    """Aggregate sequential out-of-sample folds without summing percentages."""
    if not results:
        raise ValueError("at least one fold result is required")
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    ordered = tuple(sorted(results, key=lambda item: item.fold.fold_number))
    for previous, current in zip(ordered, ordered[1:]):
        if previous.fold.test_end >= current.fold.test_start:
            raise ValueError("out-of-sample fold results overlap")

    portfolio_factor = math.prod(
        1 + result.metrics.total_return_pct / 100
        for result in ordered
    )
    benchmark_factor = math.prod(
        1 + result.metrics.benchmark_return_pct / 100
        for result in ordered
    )
    all_trades = [
        trade
        for result in ordered
        for trade in result.trades
    ]
    daily_returns = []
    total_traded_value = 0.0
    average_equities = []
    compounded_capital = float(initial_cash)
    compounded_peak = compounded_capital
    compounded_max_drawdown = 0.0
    for result in ordered:
        if not result.equity_curve:
            raise ValueError("fold equity curve cannot be empty")
        fold_start_capital = compounded_capital
        previous_equity = initial_cash
        for point in result.equity_curve:
            daily_returns.append(point.equity / previous_equity - 1)
            previous_equity = point.equity
            average_equities.append(point.equity)
            compounded_equity = (
                fold_start_capital * point.equity / initial_cash
            )
            compounded_peak = max(compounded_peak, compounded_equity)
            compounded_max_drawdown = min(
                compounded_max_drawdown,
                (compounded_equity / compounded_peak - 1) * 100,
            )
        compounded_capital = (
            fold_start_capital
            * result.equity_curve[-1].equity
            / initial_cash
        )
        total_traded_value += result.metrics.turnover_ratio * mean(
            point.equity for point in result.equity_curve
        )

    sharpe = 0.0
    if len(daily_returns) >= 2 and stdev(daily_returns) > 0:
        sharpe = math.sqrt(252) * mean(daily_returns) / stdev(daily_returns)
    average_equity = mean(average_equities)
    winners = sum(1 for trade in all_trades if trade.net_pnl > 0)
    win_rate = winners / len(all_trades) * 100 if all_trades else 0.0
    total_return_pct = (portfolio_factor - 1) * 100
    benchmark_return_pct = (benchmark_factor - 1) * 100

    return BacktestMetrics(
        total_return_pct=total_return_pct,
        benchmark_return_pct=benchmark_return_pct,
        excess_return_pct=total_return_pct - benchmark_return_pct,
        max_drawdown_pct=compounded_max_drawdown,
        sharpe_ratio=sharpe,
        turnover_ratio=(
            total_traded_value / average_equity
            if average_equity > 0
            else 0.0
        ),
        trades_count=len(all_trades),
        win_rate=win_rate,
    )


def _select_candidates(
    *,
    signal_date: date,
    next_session: TradingSession,
    history_dates: Sequence[date],
    bar_index: Mapping[tuple[int, date], DailyBar],
    memberships: Mapping[int, tuple[UniverseMembership, ...]],
    positions: Mapping[int, _Position],
    strategy: ActiveStrategy,
    costs: BacktestCosts,
    sma_window: int,
    momentum_lookback: int,
    risk_instrument_id: int,
    actions_by_instrument: Mapping[int, tuple[CorporateAction, ...]],
) -> tuple[int, ...]:
    config = strategy.config
    capacity = config.max_positions - len(positions)
    if capacity <= 0:
        return ()

    risk_history = [
        bar_index[(risk_instrument_id, value)]
        for value in history_dates
        if value <= signal_date
        and (risk_instrument_id, value) in bar_index
    ]
    if len(risk_history) < 2:
        raise BacktestDataError("risk index needs two point-in-time bars")
    risk_change_pct = (
        risk_history[-1].close / risk_history[-2].close - 1
    ) * 100
    if risk_change_pct < config.omxs30_risk_off_pct:
        return ()

    sector_counts: dict[str, int] = {}
    for position in positions.values():
        sector_counts[position.sector] = sector_counts.get(position.sector, 0) + 1

    ranked = []
    cutoff_dates = [value for value in history_dates if value <= signal_date]
    for instrument_id, periods in memberships.items():
        membership = _membership_on(memberships, instrument_id, signal_date)
        if (
            membership is None
            or instrument_id in positions
            or any(
                action.action_type == "DELISTING"
                and action.ex_date <= signal_date
                for action in actions_by_instrument.get(instrument_id, ())
            )
        ):
            continue

        history = [
            bar_index[(instrument_id, value)]
            for value in cutoff_dates
            if (instrument_id, value) in bar_index
        ]
        required = max(sma_window + 1, momentum_lookback + 1)
        if len(history) < required:
            continue
        if history[-1].available_at >= next_session.opens_at:
            raise BacktestDataError(
                f"bar for {instrument_id} was unavailable before next open"
            )
        if history[-1].volume * history[-1].close < costs.min_daily_turnover:
            continue

        closes = _split_adjusted_closes(
            history,
            actions_by_instrument.get(instrument_id, ()),
            signal_date,
        )
        current_sma = mean(closes[-sma_window:])
        previous_sma = mean(closes[-sma_window - 1:-1])
        current_close = closes[-1]
        previous_close = closes[-2]
        if config.require_price_above_sma20:
            if not (
                current_close > current_sma
                and previous_close <= previous_sma
            ):
                continue
        momentum = current_close / closes[-momentum_lookback - 1] - 1
        ranked.append((momentum, instrument_id, membership.sector))

    selected = []
    for _, instrument_id, sector in sorted(
        ranked,
        key=lambda item: (-item[0], item[1]),
    ):
        if sector_counts.get(sector, 0) >= config.max_sector_positions:
            continue
        selected.append(instrument_id)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected) >= capacity:
            break
    return tuple(selected)


def _split_adjusted_closes(
    history: Sequence[DailyBar],
    actions: Sequence[CorporateAction],
    signal_date: date,
) -> list[float]:
    """Express pre-split raw closes on the signal-date share basis."""
    splits = [
        action
        for action in actions
        if action.action_type == "SPLIT" and action.ex_date <= signal_date
    ]
    adjusted = []
    for bar in history:
        factor = math.prod(
            float(action.split_ratio)
            for action in splits
            if bar.session_date < action.ex_date
        )
        adjusted.append(bar.close / factor if factor else bar.close)
    return adjusted


def _open_position(
    *,
    instrument_id: int,
    sector: str,
    session_date: date,
    bar: DailyBar,
    target_value: float,
    available_cash: float,
    strategy: ActiveStrategy,
    costs: BacktestCosts,
) -> tuple[_Position | None, float, float]:
    if bar.volume <= 0 or target_value <= 0:
        return None, 0.0, 0.0
    impact_bps = costs.spread_bps / 2 + costs.slippage_bps
    execution_price = bar.open * (1 + impact_bps / 10_000)
    fee_rate = costs.fee_bps / 10_000
    affordable_shares = min(target_value, available_cash) / (
        execution_price * (1 + fee_rate)
    )
    volume_cap = bar.volume * costs.max_volume_participation
    shares = min(affordable_shares, volume_cap)
    if shares <= 0:
        return None, 0.0, 0.0
    traded_value = shares * execution_price
    entry_fee = traded_value * fee_rate
    cash_used = traded_value + entry_fee
    config = strategy.config
    return (
        _Position(
            instrument_id=instrument_id,
            sector=sector,
            shares=shares,
            entry_date=session_date,
            entry_price=execution_price,
            entry_fee=entry_fee,
            stop_price=execution_price * (1 + config.stop_loss_pct / 100),
            target_price=execution_price * (
                1 + config.take_profit_pct / 100
            ),
        ),
        cash_used,
        traded_value,
    )


def _exit_on_bar(
    position: _Position,
    bar: DailyBar,
    strategy: ActiveStrategy,
) -> tuple[float | None, str | None]:
    if bar.open <= position.stop_price:
        return bar.open, "STOP_LOSS"
    if bar.low <= position.stop_price:
        return position.stop_price, "STOP_LOSS"
    trailing_activation = position.entry_price * (
        1 + strategy.config.trailing_activation_pct / 100
    )
    if bar.high >= trailing_activation:
        trailing_stop = position.entry_price * (
            1 + strategy.config.trailing_floor_pct / 100
        )
        position.stop_price = max(position.stop_price, trailing_stop)
        if bar.low <= position.stop_price:
            return position.stop_price, "TRAILING_STOP"
    if bar.open >= position.target_price:
        return bar.open, "TAKE_PROFIT"
    if bar.high >= position.target_price:
        return position.target_price, "TAKE_PROFIT"
    if position.sessions_held >= strategy.config.time_stop_days:
        pnl_pct = (bar.close / position.entry_price - 1) * 100
        if pnl_pct < strategy.config.time_stop_min_gain_pct:
            return bar.close, "TIME_STOP"
    return None, None


def _close_position(
    position: _Position,
    *,
    session_date: date,
    raw_exit_price: float,
    exit_reason: str,
    costs: BacktestCosts,
) -> tuple[float, BacktestTrade, float]:
    impact_bps = costs.spread_bps / 2 + costs.slippage_bps
    exit_price = raw_exit_price * (1 - impact_bps / 10_000)
    traded_value = position.shares * exit_price
    exit_fee = traded_value * costs.fee_bps / 10_000
    cash_delta = traded_value - exit_fee
    gross_pnl = (
        (exit_price - position.entry_price) * position.shares
        + position.dividends
    )
    fees = position.entry_fee + exit_fee
    trade = BacktestTrade(
        instrument_id=position.instrument_id,
        entry_date=position.entry_date,
        exit_date=session_date,
        shares=position.shares,
        entry_price=position.entry_price,
        exit_price=exit_price,
        gross_pnl=gross_pnl,
        fees=fees,
        net_pnl=gross_pnl - fees,
        exit_reason=exit_reason,
    )
    return cash_delta, trade, traded_value


def _calculate_metrics(
    *,
    initial_cash: float,
    equity_curve: Sequence[EquityPoint],
    trades: Sequence[BacktestTrade],
    total_traded_value: float,
) -> BacktestMetrics:
    if not equity_curve:
        raise BacktestDataError("equity curve is empty")
    final_equity = equity_curve[-1].equity
    total_return_pct = (final_equity / initial_cash - 1) * 100
    benchmark_return_pct = (
        equity_curve[-1].benchmark_value / initial_cash - 1
    ) * 100

    peak = initial_cash
    max_drawdown = 0.0
    daily_returns = []
    previous = initial_cash
    for point in equity_curve:
        peak = max(peak, point.equity)
        if peak > 0:
            max_drawdown = min(
                max_drawdown,
                (point.equity / peak - 1) * 100,
            )
        daily_returns.append(point.equity / previous - 1)
        previous = point.equity

    sharpe = 0.0
    if len(daily_returns) >= 2 and stdev(daily_returns) > 0:
        sharpe = math.sqrt(252) * mean(daily_returns) / stdev(daily_returns)
    average_equity = mean(point.equity for point in equity_curve)
    turnover = (
        total_traded_value / average_equity if average_equity > 0 else 0.0
    )
    winners = sum(1 for trade in trades if trade.net_pnl > 0)
    win_rate = winners / len(trades) * 100 if trades else 0.0

    return BacktestMetrics(
        total_return_pct=total_return_pct,
        benchmark_return_pct=benchmark_return_pct,
        excess_return_pct=total_return_pct - benchmark_return_pct,
        max_drawdown_pct=max_drawdown,
        sharpe_ratio=sharpe,
        turnover_ratio=turnover,
        trades_count=len(trades),
        win_rate=win_rate,
    )


def _validate_sessions(
    sessions: Sequence[TradingSession],
) -> tuple[TradingSession, ...]:
    ordered = tuple(sorted(sessions, key=lambda item: item.session_date))
    if len({item.session_date for item in ordered}) != len(ordered):
        raise BacktestDataError("sessions must have unique dates")
    if list(sessions) != list(ordered):
        raise BacktestDataError("sessions must be chronological")
    return ordered


def _index_bars(
    bars: Iterable[DailyBar],
) -> dict[tuple[int, date], DailyBar]:
    index = {}
    for bar in bars:
        key = (bar.instrument_id, bar.session_date)
        if key in index:
            raise BacktestDataError("duplicate daily bar")
        index[key] = bar
    return index


def _index_memberships(
    memberships: Iterable[UniverseMembership],
) -> dict[int, tuple[UniverseMembership, ...]]:
    grouped: dict[int, list[UniverseMembership]] = {}
    for membership in memberships:
        grouped.setdefault(membership.instrument_id, []).append(membership)
    return {
        instrument_id: tuple(
            sorted(periods, key=lambda item: item.valid_from)
        )
        for instrument_id, periods in grouped.items()
    }


def _validate_membership_history(
    memberships: Mapping[int, tuple[UniverseMembership, ...]],
) -> None:
    for periods in memberships.values():
        for previous, current in zip(periods, periods[1:]):
            if previous.valid_to is None or previous.valid_to >= current.valid_from:
                raise BacktestDataError("universe membership periods overlap")


def _membership_on(
    memberships: Mapping[int, tuple[UniverseMembership, ...]],
    instrument_id: int,
    value: date,
) -> UniverseMembership | None:
    for membership in memberships.get(instrument_id, ()):
        if membership.contains(value):
            return membership
    return None


def _index_actions(
    actions: Iterable[CorporateAction],
) -> dict[date, tuple[CorporateAction, ...]]:
    grouped: dict[date, list[CorporateAction]] = {}
    seen = set()
    for action in actions:
        key = (action.instrument_id, action.action_type, action.ex_date)
        if key in seen:
            raise BacktestDataError("duplicate corporate action")
        seen.add(key)
        grouped.setdefault(action.ex_date, []).append(action)
    return {
        value: tuple(sorted(rows, key=lambda item: item.instrument_id))
        for value, rows in grouped.items()
    }


def _validate_test_dataset(
    *,
    dates: Sequence[date],
    test_dates: Sequence[date],
    sessions: Mapping[date, TradingSession],
    bar_index: Mapping[tuple[int, date], DailyBar],
    memberships: Mapping[int, tuple[UniverseMembership, ...]],
    benchmark_instrument_id: int,
    risk_instrument_id: int,
) -> None:
    for value in test_dates:
        benchmark_bar = bar_index.get((benchmark_instrument_id, value))
        if benchmark_bar is None:
            raise BacktestDataError(
                f"benchmark lacks daily bar for {value.isoformat()}"
            )
        if value != dates[-1]:
            following_index = dates.index(value) + 1
            next_open = sessions[dates[following_index]].opens_at
            if benchmark_bar.available_at >= next_open:
                raise BacktestDataError(
                    "benchmark bar was unavailable before next open"
                )
        risk_bar = bar_index.get((risk_instrument_id, value))
        if risk_bar is None:
            raise BacktestDataError(
                f"risk index lacks daily bar for {value.isoformat()}"
            )
        if value != dates[-1]:
            following_index = dates.index(value) + 1
            next_open = sessions[dates[following_index]].opens_at
            if risk_bar.available_at >= next_open:
                raise BacktestDataError(
                    "risk-index bar was unavailable before next open"
                )
    for index, current in enumerate(dates):
        following = dates[index + 1] if index + 1 < len(dates) else None
        next_open = (
            sessions[following].opens_at
            if following is not None
            else None
        )
        for instrument_id in memberships:
            if _membership_on(memberships, instrument_id, current) is None:
                continue
            bar = bar_index.get((instrument_id, current))
            if bar is None:
                raise BacktestDataError(
                    f"active universe instrument {instrument_id} lacks "
                    f"bar for {current.isoformat()}"
                )
            if next_open is not None and bar.available_at >= next_open:
                raise BacktestDataError(
                    f"bar for {instrument_id} was unavailable before next open"
                )
            if (
                following is not None
                and _membership_on(
                    memberships,
                    instrument_id,
                    following,
                )
                is None
                and (instrument_id, following) not in bar_index
            ):
                raise BacktestDataError(
                    f"universe exit lacks execution bar for {instrument_id}"
                )
