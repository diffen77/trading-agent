import pytest
from pathlib import Path

from src.core.exploration import (
    ExplorationPolicy,
    ExplorationPolicyError,
    apply_exploration_policy,
    exploration_policy_hash,
)


POLICY = ExplorationPolicy(
    version="xsto-exploration-v1",
    score_margin=5,
    max_positions_per_cycle=1,
    max_position_pct=5,
)


def test_exploration_selects_only_best_near_threshold_candidate():
    rows = apply_exploration_policy(
        [
            {"ticker": "B", "eligible": False, "reason_code": "SCORE_BELOW_THRESHOLD", "signal_score": 56},
            {"ticker": "A", "eligible": False, "reason_code": "SCORE_BELOW_THRESHOLD", "signal_score": 59},
            {"ticker": "C", "eligible": False, "reason_code": "SPREAD_TOO_WIDE", "signal_score": 59},
            {"ticker": "D", "eligible": True, "reason_code": "ELIGIBLE", "signal_score": 70},
        ],
        policy=POLICY,
        active_min_signal_score=60,
    )

    assert [row["ticker"] for row in rows if row["exploration"]] == ["A"]
    assert rows[1]["exploration_policy_version"] == "xsto-exploration-v1"
    assert rows[1]["exploration_max_position_pct"] == 5
    assert rows[2]["exploration"] is False


def test_exploration_has_stable_config_hash_and_strict_small_position_cap():
    assert exploration_policy_hash(POLICY) == (
        "85d634d931558688b479ed4f9f1d15359ddee22afd3fd5e697f048cd5ed66516"
    )
    with pytest.raises(ExplorationPolicyError):
        ExplorationPolicy(
            version="xsto-exploration-unsafe",
            score_margin=5,
            max_positions_per_cycle=1,
            max_position_pct=6,
        )


def test_exploration_schema_is_versioned_and_immutable():
    migration = (
        Path(__file__).resolve().parents[2]
        / "db"
        / "migrations"
        / "051_exploration_policy.sql"
    ).read_text()
    assert "CREATE TABLE exploration_policy_versions" in migration
    assert "trg_exploration_policy_immutable" in migration
    assert "ADD COLUMN exploration BOOLEAN NOT NULL" in migration
