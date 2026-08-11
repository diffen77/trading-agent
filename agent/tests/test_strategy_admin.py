import json

import pytest

from src.core.strategy import baseline_strategy
from src.strategy_admin import _parse_patch, _print_status, build_parser


def test_strategy_admin_requires_explicit_operator_arguments():
    args = build_parser().parse_args([
        "approve",
        "12",
        "--version",
        "momentum-report-swing-v2",
        "--reviewed-by",
        "operator:diffen",
    ])

    assert args.command == "approve"
    assert args.proposal_id == 12
    assert args.reviewed_by == "operator:diffen"


def test_strategy_patch_must_be_nonempty_json_object():
    assert _parse_patch('{"min_confidence": 60}') == {
        "min_confidence": 60
    }

    with pytest.raises(ValueError, match="non-empty object"):
        _parse_patch("[]")


def test_strategy_status_lists_learning_ids_available_for_proposals(capsys):
    class Database:
        def get_active_strategy(self):
            return baseline_strategy()

        def get_learnings(self, *, active_only):
            assert active_only is True
            return [
                {
                    "id": 7,
                    "category": "mistake",
                    "content": "Validerad förlust.",
                    "confidence": 80,
                    "active": True,
                }
            ]

        def query(self, _sql):
            return []

    _print_status(Database())

    status = json.loads(capsys.readouterr().out)
    assert status["eligible_learnings"][0]["id"] == 7
