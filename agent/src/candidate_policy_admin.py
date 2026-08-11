"""Operator governance for forward-validated candidate policies."""

import argparse
from datetime import datetime, timezone
import json

from .data.database import Database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Review and activate candidate-policy challengers.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")

    approve = commands.add_parser("approve")
    approve.add_argument("version")
    approve.add_argument("--reviewed-by", required=True)

    activate = commands.add_parser("activate")
    activate.add_argument("version")
    activate.add_argument("--activated-by", required=True)

    reject = commands.add_parser("reject")
    reject.add_argument("version")
    reject.add_argument("--reviewed-by", required=True)
    reject.add_argument("--reason", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = Database()
    if args.command == "status":
        status = database.get_continuous_learning_runtime_status(
            now=datetime.now(timezone.utc),
        )
        print(json.dumps(status, default=str, ensure_ascii=False, indent=2))
        return 0
    if args.command == "approve":
        database.approve_candidate_policy_version(
            args.version,
            reviewed_by=args.reviewed_by,
        )
        print(json.dumps({
            "version": args.version,
            "status": "APPROVED",
        }))
        return 0
    if args.command == "activate":
        database.activate_candidate_policy_version(
            args.version,
            activated_by=args.activated_by,
        )
        print(json.dumps({
            "version": args.version,
            "status": "ACTIVE",
        }))
        return 0
    if args.command == "reject":
        database.reject_candidate_policy_version(
            args.version,
            reviewed_by=args.reviewed_by,
            reason=args.reason,
        )
        print(json.dumps({
            "version": args.version,
            "status": "REJECTED",
        }))
        return 0
    raise RuntimeError("unsupported candidate policy command")


if __name__ == "__main__":
    raise SystemExit(main())
