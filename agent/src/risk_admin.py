"""Operator CLI for paper-trading kill switch and daily loss limit."""

import argparse
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
import json
from typing import Any

from .data.database import Database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage paper-trading entry risk controls.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show current control and daily risk.")

    halt = commands.add_parser(
        "halt",
        help="Block new BUY entries while keeping SELL exits available.",
    )
    _operator_change_arguments(halt)

    resume = commands.add_parser(
        "resume",
        help="Allow entries again if the daily loss latch is not breached.",
    )
    _operator_change_arguments(resume)

    limit = commands.add_parser(
        "set-limit",
        help="Set the daily mark-to-market entry loss limit in percent.",
    )
    limit.add_argument("max_daily_loss_pct", type=float)
    _operator_change_arguments(limit)
    return parser


def _operator_change_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reason", required=True)
    parser.add_argument("--operator", required=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = Database()

    if args.command == "status":
        _print_status(database)
        return 0
    if args.command == "halt":
        state = database.update_trading_control(
            status="HALTED",
            reason=args.reason,
            changed_by=args.operator,
        )
    elif args.command == "resume":
        state = database.update_trading_control(
            status="ACTIVE",
            reason=args.reason,
            changed_by=args.operator,
        )
    elif args.command == "set-limit":
        state = database.update_trading_control(
            max_daily_loss_pct=args.max_daily_loss_pct,
            reason=args.reason,
            changed_by=args.operator,
        )
    else:
        raise RuntimeError(f"Unsupported command: {args.command}")

    print(json.dumps(_json_value(asdict(state)), sort_keys=True))
    return 0


def _print_status(database: Database) -> None:
    control = asdict(database.get_trading_control())
    daily_rows = database.query("""
        SELECT
            session_date,
            opening_equity,
            latest_equity,
            daily_return_pct,
            limit_breached,
            first_evaluated_at,
            last_evaluated_at
        FROM trading_daily_risk
        ORDER BY session_date DESC
        LIMIT 1
    """)
    print(json.dumps(
        _json_value({
            "control": control,
            "latest_daily_risk": daily_rows[0] if daily_rows else None,
        }),
        sort_keys=True,
    ))


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
