"""Validate frozen datasets and persist point-in-time walk-forward runs."""

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math
from typing import Any

from sqlalchemy import text

from .core.backtest import (
    ENGINE_VERSION,
    BacktestCosts,
    CorporateAction,
    DailyBar,
    TradingSession,
    UniverseMembership,
    aggregate_fold_results,
    build_walk_forward_folds,
    run_fold,
    validate_point_in_time_dataset,
)
from .data.database import Database


@dataclass(frozen=True)
class LoadedDataset:
    metadata: dict[str, Any]
    sessions: tuple[TradingSession, ...]
    bars: tuple[DailyBar, ...]
    memberships: tuple[UniverseMembership, ...]
    corporate_actions: tuple[CorporateAction, ...]
    checksum: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Point-in-time walk-forward backtests.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    checksum = commands.add_parser("checksum")
    checksum.add_argument("dataset_id", type=int)

    validate = commands.add_parser("validate")
    validate.add_argument("dataset_id", type=int)
    validate.add_argument("--validated-by", required=True)
    validate.add_argument("--minimum-sessions", type=int, default=315)

    run = commands.add_parser("run")
    run.add_argument("dataset_id", type=int)
    run.add_argument("--strategy-version", required=True)
    run.add_argument("--train-sessions", type=int, default=252)
    run.add_argument("--test-sessions", type=int, default=63)
    run.add_argument("--fee-bps", type=float, required=True)
    run.add_argument("--spread-bps", type=float, required=True)
    run.add_argument("--slippage-bps", type=float, required=True)
    run.add_argument("--max-volume-participation", type=float, default=0.01)
    run.add_argument("--min-daily-turnover", type=float, required=True)
    run.add_argument("--initial-cash", type=float, default=20_000)
    run.add_argument("--sma-window", type=int, default=20)
    run.add_argument("--momentum-lookback", type=int, default=20)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = Database()

    if args.command == "checksum":
        loaded = load_dataset(db, args.dataset_id, require_validated=False)
        print(json.dumps({
            "dataset_id": args.dataset_id,
            "checksum_sha256": loaded.checksum,
        }))
        return 0

    if args.command == "validate":
        checksum = validate_dataset(
            db,
            args.dataset_id,
            validated_by=args.validated_by,
            minimum_sessions=args.minimum_sessions,
        )
        print(json.dumps({
            "dataset_id": args.dataset_id,
            "status": "VALIDATED",
            "checksum_sha256": checksum,
        }))
        return 0

    if args.command == "run":
        costs = BacktestCosts(
            fee_bps=args.fee_bps,
            spread_bps=args.spread_bps,
            slippage_bps=args.slippage_bps,
            max_volume_participation=args.max_volume_participation,
            min_daily_turnover=args.min_daily_turnover,
        )
        result = run_walk_forward(
            db,
            dataset_id=args.dataset_id,
            strategy_version=args.strategy_version,
            costs=costs,
            train_sessions=args.train_sessions,
            test_sessions=args.test_sessions,
            initial_cash=args.initial_cash,
            sma_window=args.sma_window,
            momentum_lookback=args.momentum_lookback,
        )
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0

    raise RuntimeError(f"Unsupported command: {args.command}")


def validate_dataset(
    db: Database,
    dataset_id: int,
    *,
    validated_by: str,
    minimum_sessions: int = 315,
) -> str:
    operator = db._operator_identity(validated_by)
    loaded = load_dataset(db, dataset_id, require_validated=False)
    metadata = loaded.metadata
    if metadata["status"] != "DRAFT":
        raise ValueError("Only a draft dataset can be validated")
    required_attestations = {
        "benchmark_is_total_return": metadata["benchmark_is_total_return"],
        "raw_unadjusted_prices": metadata["raw_unadjusted_prices"],
        "corporate_actions_complete": metadata["corporate_actions_complete"],
        "universe_history_complete": metadata["universe_history_complete"],
        "includes_inactive_and_delisted": (
            metadata["includes_inactive_and_delisted"]
        ),
    }
    missing = sorted(
        name for name, accepted in required_attestations.items() if not accepted
    )
    if missing:
        raise ValueError(
            "Dataset lacks required attestations: " + ", ".join(missing)
        )
    if len(loaded.sessions) < minimum_sessions:
        raise ValueError(
            f"Dataset has {len(loaded.sessions)} sessions; "
            f"at least {minimum_sessions} are required"
        )
    validate_point_in_time_dataset(
        sessions=loaded.sessions,
        bars=loaded.bars,
        memberships=loaded.memberships,
        corporate_actions=loaded.corporate_actions,
        benchmark_instrument_id=metadata["benchmark_instrument_id"],
        risk_instrument_id=metadata["risk_instrument_id"],
    )

    with db.Session.begin() as session:
        updated = session.execute(text("""
            UPDATE backtest_datasets
            SET
                status = 'VALIDATED',
                content_checksum_sha256 = :checksum,
                validated_by = :validated_by,
                validated_at = NOW(),
                updated_at = NOW()
            WHERE id = :dataset_id AND status = 'DRAFT'
            RETURNING id
        """), {
            "checksum": loaded.checksum,
            "validated_by": operator,
            "dataset_id": dataset_id,
        }).scalar_one_or_none()
        if updated is None:
            raise ValueError("Dataset was changed or is no longer draft")
    return loaded.checksum


def load_dataset(
    db: Database,
    dataset_id: int,
    *,
    require_validated: bool,
) -> LoadedDataset:
    if isinstance(dataset_id, bool) or dataset_id <= 0:
        raise ValueError("dataset_id must be positive")
    rows = db.query("""
        SELECT *
        FROM backtest_datasets
        WHERE id = :dataset_id
    """, {"dataset_id": dataset_id})
    if not rows:
        raise ValueError("Backtest dataset does not exist")
    metadata = rows[0]
    if require_validated and metadata["status"] != "VALIDATED":
        raise ValueError("Backtest dataset is not validated")

    sessions_raw = db.query("""
        SELECT session_date, opens_at, closes_at
        FROM market_sessions
        WHERE mic = 'XSTO'
          AND status IN ('OPEN', 'HALF_DAY')
          AND session_date BETWEEN :period_start AND :period_end
        ORDER BY session_date
    """, {
        "period_start": metadata["period_start"],
        "period_end": metadata["period_end"],
    })
    memberships_raw = db.query("""
        SELECT
            instrument_id,
            valid_from,
            valid_to,
            sector,
            source,
            source_available_at
        FROM backtest_universe_memberships
        WHERE dataset_id = :dataset_id
        ORDER BY instrument_id, valid_from
    """, {"dataset_id": dataset_id})
    instrument_ids = sorted({
        int(row["instrument_id"]) for row in memberships_raw
    } | {
        int(metadata["benchmark_instrument_id"]),
        int(metadata["risk_instrument_id"]),
    })
    bars_raw = db.query("""
        SELECT
            b.instrument_id,
            (b.event_time AT TIME ZONE 'Europe/Stockholm')::date
                AS session_date,
            b.event_time,
            b.available_at,
            b.open,
            b.high,
            b.low,
            b.close,
            b.volume,
            b.source
        FROM market_bars b
        WHERE b.source = :bar_source
          AND b.interval_seconds = :bar_interval_seconds
          AND (b.event_time AT TIME ZONE 'Europe/Stockholm')::date
              BETWEEN :period_start AND :period_end
          AND b.received_at <= :data_cutoff
          AND b.instrument_id = ANY(CAST(:instrument_ids AS BIGINT[]))
        ORDER BY b.instrument_id, b.event_time
    """, {
        "bar_source": metadata["bar_source"],
        "bar_interval_seconds": metadata["bar_interval_seconds"],
        "period_start": metadata["period_start"],
        "period_end": metadata["period_end"],
        "data_cutoff": metadata["data_cutoff"],
        "instrument_ids": instrument_ids,
    })
    actions_raw = db.query("""
        SELECT
            instrument_id,
            action_type,
            ex_date,
            split_ratio,
            cash_amount,
            currency,
            source,
            source_available_at
        FROM backtest_corporate_actions
        WHERE dataset_id = :dataset_id
        ORDER BY ex_date, instrument_id, action_type
    """, {"dataset_id": dataset_id})

    sessions = tuple(
        TradingSession(
            session_date=row["session_date"],
            opens_at=row["opens_at"],
            closes_at=row["closes_at"],
        )
        for row in sessions_raw
    )
    bars = tuple(
        DailyBar(
            instrument_id=int(row["instrument_id"]),
            session_date=row["session_date"],
            event_time=row["event_time"],
            available_at=row["available_at"],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"] or 0),
        )
        for row in bars_raw
    )
    memberships = tuple(
        UniverseMembership(
            instrument_id=int(row["instrument_id"]),
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            sector=row["sector"],
        )
        for row in memberships_raw
    )
    actions = tuple(
        CorporateAction(
            instrument_id=int(row["instrument_id"]),
            action_type=row["action_type"],
            ex_date=row["ex_date"],
            split_ratio=(
                float(row["split_ratio"])
                if row["split_ratio"] is not None
                else None
            ),
            cash_amount=(
                float(row["cash_amount"])
                if row["cash_amount"] is not None
                else None
            ),
        )
        for row in actions_raw
    )

    session_by_date = {row.session_date: row for row in sessions}
    for row in memberships_raw:
        first_session = next(
            (
                session
                for session in sessions
                if session.session_date >= row["valid_from"]
            ),
            None,
        )
        if (
            first_session is not None
            and row["source_available_at"] >= first_session.opens_at
        ):
            raise ValueError(
                "Universe membership was unavailable at its effective start"
            )
    for row in actions_raw:
        ex_session = session_by_date.get(row["ex_date"])
        if (
            ex_session is not None
            and row["source_available_at"] >= ex_session.opens_at
        ):
            raise ValueError(
                "Corporate action was unavailable before its ex-date open"
            )

    checksum = _dataset_checksum(
        metadata=metadata,
        sessions=sessions_raw,
        bars=bars_raw,
        memberships=memberships_raw,
        actions=actions_raw,
    )
    if (
        require_validated
        and metadata["content_checksum_sha256"].strip() != checksum
    ):
        raise ValueError("Validated backtest dataset checksum has changed")

    return LoadedDataset(
        metadata=metadata,
        sessions=sessions,
        bars=bars,
        memberships=memberships,
        corporate_actions=actions,
        checksum=checksum,
    )


def run_walk_forward(
    db: Database,
    *,
    dataset_id: int,
    strategy_version: str,
    costs: BacktestCosts,
    train_sessions: int,
    test_sessions: int,
    initial_cash: float,
    sma_window: int,
    momentum_lookback: int,
) -> dict[str, Any]:
    loaded = load_dataset(db, dataset_id, require_validated=True)
    metadata = loaded.metadata
    validate_point_in_time_dataset(
        sessions=loaded.sessions,
        bars=loaded.bars,
        memberships=loaded.memberships,
        corporate_actions=loaded.corporate_actions,
        benchmark_instrument_id=metadata["benchmark_instrument_id"],
        risk_instrument_id=metadata["risk_instrument_id"],
    )
    strategy = db.get_strategy_version(strategy_version)
    folds = build_walk_forward_folds(
        loaded.sessions,
        train_sessions=train_sessions,
        test_sessions=test_sessions,
    )
    run_config = {
        "costs": asdict(costs),
        "initial_cash": initial_cash,
        "train_sessions": train_sessions,
        "test_sessions": test_sessions,
        "sma_window": sma_window,
        "momentum_lookback": momentum_lookback,
        "execution": "signal-close-next-session-open",
        "same_bar_precedence": "stop-before-target",
    }
    input_checksum = _sha256({
        "dataset_checksum": loaded.checksum,
        "strategy_config_hash": strategy.config_hash,
        "engine_version": ENGINE_VERSION,
        "config": run_config,
    })
    run_key = f"walk-forward:{input_checksum}"

    with db.Session.begin() as session:
        existing = session.execute(text("""
            SELECT id, status
            FROM walk_forward_runs
            WHERE run_key = :run_key
        """), {"run_key": run_key}).mappings().one_or_none()
        if existing is not None:
            if existing["status"] == "SUCCEEDED":
                return _read_run_result(db, int(existing["id"]))
            raise RuntimeError(
                f"Backtest run {existing['id']} already exists with "
                f"status {existing['status']}"
            )
        run_id = int(session.execute(text("""
            INSERT INTO walk_forward_runs (
                run_key,
                dataset_id,
                strategy_version_id,
                engine_version,
                scope,
                config,
                input_checksum_sha256
            )
            SELECT
                :run_key,
                :dataset_id,
                s.id,
                :engine_version,
                'DETERMINISTIC_POLICY',
                CAST(:config AS JSONB),
                :input_checksum
            FROM strategy_versions s
            WHERE s.version = :strategy_version
            RETURNING id
        """), {
            "run_key": run_key,
            "dataset_id": dataset_id,
            "engine_version": ENGINE_VERSION,
            "config": json.dumps(
                run_config,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "input_checksum": input_checksum,
            "strategy_version": strategy.version,
        }).scalar_one())

    try:
        results = tuple(
            run_fold(
                fold=fold,
                sessions=loaded.sessions,
                bars=loaded.bars,
                memberships=loaded.memberships,
                corporate_actions=loaded.corporate_actions,
                benchmark_instrument_id=metadata["benchmark_instrument_id"],
                risk_instrument_id=metadata["risk_instrument_id"],
                strategy=strategy,
                costs=costs,
                initial_cash=initial_cash,
                sma_window=sma_window,
                momentum_lookback=momentum_lookback,
            )
            for fold in folds
        )
        metrics = aggregate_fold_results(
            results,
            initial_cash=initial_cash,
        )
        _persist_success(db, run_id, results, metrics)
    except Exception as exc:
        with db.Session.begin() as session:
            session.execute(text("""
                UPDATE walk_forward_runs
                SET
                    status = 'FAILED',
                    finished_at = NOW(),
                    error_message = :error_message
                WHERE id = :run_id
            """), {
                "error_message": str(exc)[:4000],
                "run_id": run_id,
            })
        raise

    return _read_run_result(db, run_id)


def _persist_success(db, run_id, results, metrics) -> None:
    with db.Session.begin() as session:
        for result in results:
            session.execute(text("""
                INSERT INTO walk_forward_folds (
                    run_id,
                    fold_number,
                    train_start,
                    train_end,
                    test_start,
                    test_end,
                    metrics
                )
                VALUES (
                    :run_id,
                    :fold_number,
                    :train_start,
                    :train_end,
                    :test_start,
                    :test_end,
                    CAST(:metrics AS JSONB)
                )
            """), {
                "run_id": run_id,
                **asdict(result.fold),
                "metrics": json.dumps(asdict(result.metrics)),
            })
            for trade in result.trades:
                session.execute(text("""
                    INSERT INTO backtest_trades (
                        run_id,
                        fold_number,
                        instrument_id,
                        entry_date,
                        exit_date,
                        shares,
                        entry_price,
                        exit_price,
                        gross_pnl,
                        fees,
                        net_pnl,
                        exit_reason
                    )
                    VALUES (
                        :run_id,
                        :fold_number,
                        :instrument_id,
                        :entry_date,
                        :exit_date,
                        :shares,
                        :entry_price,
                        :exit_price,
                        :gross_pnl,
                        :fees,
                        :net_pnl,
                        :exit_reason
                    )
                """), {
                    "run_id": run_id,
                    "fold_number": result.fold.fold_number,
                    **asdict(trade),
                })
            for point in result.equity_curve:
                session.execute(text("""
                    INSERT INTO backtest_equity_curve (
                        run_id,
                        fold_number,
                        session_date,
                        equity,
                        cash,
                        benchmark_value
                    )
                    VALUES (
                        :run_id,
                        :fold_number,
                        :session_date,
                        :equity,
                        :cash,
                        :benchmark_value
                    )
                """), {
                    "run_id": run_id,
                    "fold_number": result.fold.fold_number,
                    **asdict(point),
                })
        session.execute(text("""
            UPDATE walk_forward_runs
            SET
                status = 'SUCCEEDED',
                finished_at = NOW(),
                total_return_pct = :total_return_pct,
                benchmark_return_pct = :benchmark_return_pct,
                excess_return_pct = :excess_return_pct,
                max_drawdown_pct = :max_drawdown_pct,
                sharpe_ratio = :sharpe_ratio,
                turnover_ratio = :turnover_ratio,
                trades_count = :trades_count,
                win_rate = :win_rate
            WHERE id = :run_id
        """), {"run_id": run_id, **asdict(metrics)})


def _read_run_result(db: Database, run_id: int) -> dict[str, Any]:
    rows = db.query("""
        SELECT
            id,
            run_key,
            status,
            engine_version,
            scope,
            total_return_pct,
            benchmark_return_pct,
            excess_return_pct,
            max_drawdown_pct,
            sharpe_ratio,
            turnover_ratio,
            trades_count,
            win_rate,
            started_at,
            finished_at
        FROM walk_forward_runs
        WHERE id = :run_id
    """, {"run_id": run_id})
    if not rows:
        raise RuntimeError("Backtest result disappeared")
    return rows[0]


def _dataset_checksum(
    *,
    metadata,
    sessions,
    bars,
    memberships,
    actions,
) -> str:
    stable_metadata = {
        key: metadata[key]
        for key in (
            "name",
            "provider",
            "bar_source",
            "bar_interval_seconds",
            "period_start",
            "period_end",
            "data_cutoff",
            "benchmark_instrument_id",
            "risk_instrument_id",
            "benchmark_is_total_return",
            "raw_unadjusted_prices",
            "corporate_actions_complete",
            "universe_history_complete",
            "includes_inactive_and_delisted",
        )
    }
    return _sha256({
        "metadata": stable_metadata,
        "sessions": sessions,
        "bars": bars,
        "memberships": memberships,
        "actions": actions,
    })


def _sha256(value: Any) -> str:
    canonical = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("checksum payload contains non-finite float")
        return value
    return value


if __name__ == "__main__":
    raise SystemExit(main())
