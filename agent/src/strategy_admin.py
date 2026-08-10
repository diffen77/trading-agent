"""Operator CLI for proposing, approving and activating strategy versions."""

import argparse
import json
import logging
from typing import Any

from .data.database import Database


logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage versioned paper-trading strategies.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="Show active strategy and proposals.")

    propose = commands.add_parser(
        "propose",
        help="Create a pending evidence-backed strategy patch.",
    )
    propose.add_argument("--patch-json", required=True)
    propose.add_argument(
        "--learning",
        type=int,
        action="append",
        required=True,
        dest="learning_ids",
    )
    propose.add_argument("--rationale", required=True)
    propose.add_argument("--proposed-by", required=True)

    approve = commands.add_parser(
        "approve",
        help="Operator-approve a pending proposal into an inactive version.",
    )
    approve.add_argument("proposal_id", type=int)
    approve.add_argument("--version", required=True)
    approve.add_argument("--reviewed-by", required=True)

    activate = commands.add_parser(
        "activate",
        help="Atomically activate an approved child of the current version.",
    )
    activate.add_argument("version")
    activate.add_argument("--activated-by", required=True)

    reject = commands.add_parser(
        "reject",
        help="Operator-reject a pending proposal.",
    )
    reject.add_argument("proposal_id", type=int)
    reject.add_argument("--reviewed-by", required=True)
    reject.add_argument("--reason", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = Database()

    if args.command == "status":
        _print_status(db)
        return 0

    if args.command == "propose":
        patch = _parse_patch(args.patch_json)
        proposal_id = db.create_strategy_change_proposal(
            config_patch=patch,
            learning_ids=args.learning_ids,
            rationale=args.rationale,
            proposed_by=args.proposed_by,
        )
        print(json.dumps({"proposal_id": proposal_id, "status": "PENDING"}))
        return 0

    if args.command == "approve":
        strategy_id = db.approve_strategy_change_proposal(
            args.proposal_id,
            new_version=args.version,
            reviewed_by=args.reviewed_by,
        )
        print(json.dumps({
            "strategy_id": strategy_id,
            "version": args.version,
            "status": "APPROVED",
        }))
        return 0

    if args.command == "activate":
        db.activate_strategy_version(
            args.version,
            activated_by=args.activated_by,
        )
        print(json.dumps({"version": args.version, "status": "ACTIVE"}))
        return 0

    if args.command == "reject":
        db.reject_strategy_change_proposal(
            args.proposal_id,
            reviewed_by=args.reviewed_by,
            reason=args.reason,
        )
        print(json.dumps({
            "proposal_id": args.proposal_id,
            "status": "REJECTED",
        }))
        return 0

    raise RuntimeError(f"Unsupported command: {args.command}")


def _parse_patch(value: str) -> dict[str, Any]:
    try:
        patch = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("--patch-json must contain valid JSON") from exc
    if not isinstance(patch, dict) or not patch:
        raise ValueError("--patch-json must contain a non-empty object")
    return patch


def _print_status(db: Database) -> None:
    strategy = db.get_active_strategy()
    eligible_learnings = db.get_learnings(active_only=True)
    proposals = db.query("""
        SELECT
            p.id,
            base.version AS base_version,
            proposed.version AS proposed_version,
            p.config_patch,
            p.learning_ids,
            p.rationale,
            p.proposed_by,
            p.status,
            p.reviewed_by,
            p.created_at
        FROM strategy_change_proposals p
        JOIN strategy_versions base ON base.id = p.base_version_id
        LEFT JOIN strategy_versions proposed
            ON proposed.id = p.proposed_version_id
        ORDER BY p.created_at DESC
        LIMIT 100
    """)
    print(json.dumps(
        {
            "active": {
                "version": strategy.version,
                "config_hash": strategy.config_hash,
                "config": strategy.config.to_dict(),
                "learning_ids": [
                    learning.id for learning in strategy.learnings
                ],
            },
            "eligible_learnings": eligible_learnings,
            "proposals": proposals,
        },
        ensure_ascii=False,
        default=str,
        indent=2,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
