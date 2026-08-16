"""Deterministic, separately governed paper-trading exploration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping


_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,49}$")


class ExplorationPolicyError(ValueError):
    """Raised when exploration configuration or evidence is invalid."""


@dataclass(frozen=True)
class ExplorationPolicy:
    version: str
    score_margin: float
    max_positions_per_cycle: int
    max_position_pct: float

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not _VERSION_PATTERN.fullmatch(
            self.version
        ):
            raise ExplorationPolicyError("exploration version has invalid format")
        if isinstance(self.score_margin, bool) or not 0 < float(self.score_margin) <= 20:
            raise ExplorationPolicyError("score_margin must be between 0 and 20")
        if (
            isinstance(self.max_positions_per_cycle, bool)
            or not isinstance(self.max_positions_per_cycle, int)
            or not 1 <= self.max_positions_per_cycle <= 3
        ):
            raise ExplorationPolicyError("max_positions_per_cycle must be 1-3")
        if (
            isinstance(self.max_position_pct, bool)
            or not 0 < float(self.max_position_pct) <= 5
        ):
            raise ExplorationPolicyError("max_position_pct must be above 0 and at most 5")

    @classmethod
    def from_mapping(
        cls, *, version: str, config: Mapping[str, Any]
    ) -> "ExplorationPolicy":
        expected = {
            "score_margin",
            "max_positions_per_cycle",
            "max_position_pct",
        }
        if not isinstance(config, Mapping) or set(config) != expected:
            raise ExplorationPolicyError("exploration config has invalid fields")
        return cls(version=version, **dict(config))

    def to_config(self) -> dict[str, float | int]:
        return {
            "max_position_pct": self.max_position_pct,
            "max_positions_per_cycle": self.max_positions_per_cycle,
            "score_margin": self.score_margin,
        }


def exploration_policy_hash(policy: ExplorationPolicy) -> str:
    canonical = json.dumps(
        policy.to_config(),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def apply_exploration_policy(
    candidates: Iterable[Mapping[str, Any]],
    *,
    policy: ExplorationPolicy,
    active_min_signal_score: float,
) -> list[dict[str, Any]]:
    """Mark a bounded near-threshold sample; never override liquidity gates."""
    rows = [dict(candidate) for candidate in candidates]
    lower_bound = max(0.0, float(active_min_signal_score) - policy.score_margin)
    eligible = [
        row
        for row in rows
        if row.get("eligible") is False
        and row.get("reason_code") == "SCORE_BELOW_THRESHOLD"
        and lower_bound <= float(row.get("signal_score", -1))
        < float(active_min_signal_score)
    ]
    selected = {
        row["ticker"]
        for row in sorted(
            eligible,
            key=lambda row: (-float(row["signal_score"]), str(row["ticker"])),
        )[: policy.max_positions_per_cycle]
    }
    for row in rows:
        exploring = row.get("ticker") in selected
        row["exploration"] = exploring
        row["exploration_policy_version"] = policy.version if exploring else None
        row["exploration_max_position_pct"] = (
            policy.max_position_pct if exploring else None
        )
    return rows
