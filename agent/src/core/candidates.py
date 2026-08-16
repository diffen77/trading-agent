"""Deterministic, evidence-bound ranking for XSTO intraday candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping


CANDIDATE_POLICY_VERSION = "xsto-momentum-v1"
_MAX_SPREAD_BPS = Decimal("250")
_IDENTITY_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_POLICY_VERSION_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9._-]{2,49}$"
)


class CandidateSignalError(ValueError):
    """Raised when candidate evidence is incomplete or internally invalid."""


def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise CandidateSignalError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CandidateSignalError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise CandidateSignalError(f"{field} must be finite")
    if positive and result <= 0:
        raise CandidateSignalError(f"{field} must be positive")
    return result


@dataclass(frozen=True)
class CandidatePolicy:
    """Versioned deterministic gate applied before model consideration."""

    version: str
    min_signal_score: float
    max_spread_bps: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.version, str)
            or not _POLICY_VERSION_PATTERN.fullmatch(self.version)
        ):
            raise CandidateSignalError(
                "candidate policy version has invalid format"
            )
        min_score = _decimal(
            self.min_signal_score,
            "min_signal_score",
        )
        max_spread = _decimal(
            self.max_spread_bps,
            "max_spread_bps",
            positive=True,
        )
        if not Decimal("0") <= min_score <= Decimal("100"):
            raise CandidateSignalError(
                "min_signal_score must be between 0 and 100"
            )
        if max_spread > Decimal("1000"):
            raise CandidateSignalError(
                "max_spread_bps must be at most 1000"
            )
        object.__setattr__(self, "min_signal_score", float(min_score))
        object.__setattr__(self, "max_spread_bps", float(max_spread))

    @classmethod
    def from_mapping(
        cls,
        *,
        version: str,
        config: Mapping[str, Any],
    ) -> "CandidatePolicy":
        if not isinstance(config, Mapping):
            raise CandidateSignalError(
                "candidate policy config must be an object"
            )
        if set(config) != {"min_signal_score", "max_spread_bps"}:
            raise CandidateSignalError(
                "candidate policy config has invalid fields"
            )
        return cls(
            version=version,
            min_signal_score=config["min_signal_score"],
            max_spread_bps=config["max_spread_bps"],
        )

    def to_config(self) -> dict[str, float | int]:
        def canonical_number(value: float) -> float | int:
            return int(value) if value.is_integer() else value

        return {
            "max_spread_bps": canonical_number(self.max_spread_bps),
            "min_signal_score": canonical_number(self.min_signal_score),
        }


BASELINE_CANDIDATE_POLICY = CandidatePolicy(
    version=CANDIDATE_POLICY_VERSION,
    min_signal_score=0,
    max_spread_bps=float(_MAX_SPREAD_BPS),
)


def candidate_policy_hash(policy: CandidatePolicy) -> str:
    if not isinstance(policy, CandidatePolicy):
        raise CandidateSignalError("policy must be a CandidatePolicy")
    canonical = json.dumps(
        policy.to_config(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CandidateSignalError(f"{field} must be a positive integer")
    return value


def _aware(value: Any, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CandidateSignalError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise CandidateSignalError(f"{field} must be text")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise CandidateSignalError(f"{field} has invalid format")
    return normalized


def _percent(current: Decimal, previous: Decimal) -> Decimal:
    return ((current / previous) - Decimal("1")) * Decimal("100")


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return max(lower, min(upper, value))


def _normalise_signal(
    row: Mapping[str, Any],
    *,
    policy: CandidatePolicy,
) -> dict[str, Any]:
    ticker = _text(row.get("ticker"), "ticker", maximum=32).upper()
    if not _IDENTITY_PATTERN.fullmatch(ticker):
        raise CandidateSignalError("ticker has invalid format")
    name = _text(row.get("name"), "name", maximum=200)
    sector = _text(
        row.get("sector") or "Unclassified",
        "sector",
        maximum=100,
    )
    provider = _text(row.get("provider"), "provider", maximum=100)
    source = _text(row.get("source"), "source", maximum=100)
    book_state_id = _positive_integer(
        row.get("book_state_id"),
        "book_state_id",
    )

    latest_price = _decimal(
        row.get("latest_price"),
        "latest_price",
        positive=True,
    )
    sma20 = _decimal(
        row.get("sma20"),
        "sma20",
        positive=True,
    )
    price_5m_ago = _decimal(
        row.get("price_5m_ago"),
        "price_5m_ago",
        positive=True,
    )
    price_20m_ago = _decimal(
        row.get("price_20m_ago"),
        "price_20m_ago",
        positive=True,
    )
    price_60m_ago = _decimal(
        row.get("price_60m_ago"),
        "price_60m_ago",
        positive=True,
    )
    bid_price = _decimal(row.get("bid_price"), "bid_price", positive=True)
    ask_price = _decimal(row.get("ask_price"), "ask_price", positive=True)
    if ask_price < bid_price:
        raise CandidateSignalError("candidate book is crossed")
    midpoint = (bid_price + ask_price) / Decimal("2")
    if abs(midpoint - latest_price) > Decimal("0.00000001"):
        raise CandidateSignalError("latest_price is not the quoted midpoint")

    bid_quantity = _decimal(
        row.get("bid_quantity"),
        "bid_quantity",
        positive=True,
    )
    ask_quantity = _decimal(
        row.get("ask_quantity"),
        "ask_quantity",
        positive=True,
    )
    range_20_bps = _decimal(row.get("range_20_bps"), "range_20_bps")
    range_60_bps = _decimal(row.get("range_60_bps"), "range_60_bps")
    if range_20_bps < 0 or range_60_bps < range_20_bps:
        raise CandidateSignalError("candidate ranges are inconsistent")

    first_report_minute = _aware(
        row.get("first_report_minute"),
        "first_report_minute",
    )
    last_report_minute = _aware(
        row.get("last_report_minute"),
        "last_report_minute",
    )
    latest_received_at = _aware(
        row.get("latest_received_at"),
        "latest_received_at",
    )
    if last_report_minute - first_report_minute != timedelta(minutes=60):
        raise CandidateSignalError("candidate window must be exactly 60 minutes")
    if latest_received_at < last_report_minute:
        raise CandidateSignalError("candidate receipt precedes market evidence")

    momentum_5m_pct = _percent(latest_price, price_5m_ago)
    momentum_20m_pct = _percent(latest_price, price_20m_ago)
    momentum_60m_pct = _percent(latest_price, price_60m_ago)
    above_sma20 = latest_price > sma20
    spread_bps = (
        (ask_price - bid_price) / midpoint * Decimal("10000")
    )
    book_imbalance = Decimal(bid_quantity - ask_quantity) / Decimal(
        bid_quantity + ask_quantity
    )

    feature_payload = {
        "above_sma20": above_sma20,
        "ask_price": str(ask_price),
        "ask_quantity": str(ask_quantity),
        "bid_price": str(bid_price),
        "bid_quantity": str(bid_quantity),
        "book_imbalance": str(book_imbalance),
        "book_state_id": book_state_id,
        "first_report_minute": first_report_minute.isoformat(),
        "last_report_minute": last_report_minute.isoformat(),
        "latest_price": str(latest_price),
        "latest_received_at": latest_received_at.isoformat(),
        "momentum_20m_pct": str(momentum_20m_pct),
        "momentum_5m_pct": str(momentum_5m_pct),
        "momentum_60m_pct": str(momentum_60m_pct),
        "price_20m_ago": str(price_20m_ago),
        "price_5m_ago": str(price_5m_ago),
        "price_60m_ago": str(price_60m_ago),
        "provider": provider,
        "range_20_bps": str(range_20_bps),
        "range_60_bps": str(range_60_bps),
        "sma20": str(sma20),
        "source": source,
        "spread_bps": str(spread_bps),
    }
    canonical = json.dumps(
        feature_payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    feature_checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    spread_eligible = spread_bps <= Decimal(
        str(policy.max_spread_bps)
    )
    if spread_eligible:
        momentum_component = _clamp(
            momentum_5m_pct * Decimal("8")
            + momentum_20m_pct * Decimal("5")
            + momentum_60m_pct * Decimal("3"),
            Decimal("-30"),
            Decimal("30"),
        )
        imbalance_component = book_imbalance * Decimal("5")
        spread_penalty = min(Decimal("12"), spread_bps / Decimal("10"))
        range_penalty = max(
            Decimal("0"),
            (range_60_bps - Decimal("500")) / Decimal("100"),
        )
        score = _clamp(
            Decimal("50")
            + momentum_component
            + imbalance_component
            - spread_penalty
            - range_penalty,
            Decimal("0"),
            Decimal("100"),
        )
    else:
        score = Decimal("0")

    score_eligible = score >= Decimal(str(policy.min_signal_score))
    eligible = spread_eligible and score_eligible
    if not spread_eligible:
        reason_code = "SPREAD_TOO_WIDE"
    elif not score_eligible:
        reason_code = "SCORE_BELOW_THRESHOLD"
    else:
        reason_code = "ELIGIBLE"

    return {
        "policy_version": policy.version,
        "ticker": ticker,
        "name": name,
        "sector": sector,
        "provider": provider,
        "source": source,
        "book_state_id": book_state_id,
        "latest_price": float(latest_price),
        "sma20": float(sma20),
        "above_sma20": above_sma20,
        "momentum_5m_pct": float(momentum_5m_pct),
        "momentum_20m_pct": float(momentum_20m_pct),
        "momentum_60m_pct": float(momentum_60m_pct),
        "spread_bps": float(spread_bps),
        "book_imbalance": float(book_imbalance),
        "range_20_bps": float(range_20_bps),
        "range_60_bps": float(range_60_bps),
        "first_report_minute": first_report_minute,
        "last_report_minute": last_report_minute,
        "latest_received_at": latest_received_at,
        "eligible": eligible,
        "reason_code": reason_code,
        "signal_score": float(score.quantize(Decimal("0.0001"))),
        "feature_json": feature_payload,
        "feature_checksum_sha256": feature_checksum,
    }


def rank_candidate_signals(
    rows: Iterable[Mapping[str, Any]],
    *,
    limit: int = 20,
    policy: CandidatePolicy | None = None,
) -> list[dict[str, Any]]:
    """Validate, score and deterministically rank candidate evidence."""
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 1_000
    ):
        raise CandidateSignalError(
            "limit must be an integer between 1 and 1000"
        )
    active_policy = policy or BASELINE_CANDIDATE_POLICY
    if not isinstance(active_policy, CandidatePolicy):
        raise CandidateSignalError("policy must be a CandidatePolicy")
    candidates = [
        _normalise_signal(row, policy=active_policy)
        for row in rows
    ]
    candidates.sort(
        key=lambda row: (
            not row["eligible"],
            -row["signal_score"],
            row["ticker"],
        )
    )
    ranked = candidates[:limit]
    for rank, candidate in enumerate(ranked, start=1):
        candidate["rank"] = rank
    return ranked


def render_candidate_context(candidates: Iterable[Mapping[str, Any]]) -> str:
    """Render a bounded, human-readable view of the structured snapshot."""
    rows = list(candidates)
    if not rows:
        return "Inga kompletta, färska 60-minuterskandidater."
    lines = [f"Kandidatpolicy: {CANDIDATE_POLICY_VERSION}"]
    for row in rows[:20]:
        values = (
            float(row["signal_score"]),
            float(row["momentum_5m_pct"]),
            float(row["momentum_20m_pct"]),
            float(row["momentum_60m_pct"]),
            float(row["spread_bps"]),
            float(row["book_imbalance"]),
            float(row["sma20"]),
        )
        if not all(math.isfinite(value) for value in values):
            raise CandidateSignalError("candidate render values must be finite")
        if row.get("exploration") is True:
            eligibility = (
                "exploration:"
                f"{row.get('exploration_policy_version')} max "
                f"{float(row.get('exploration_max_position_pct')):.1f}%"
            )
        else:
            eligibility = (
                "valbar"
                if row.get("eligible")
                else f"ej valbar:{row.get('reason_code')}"
            )
        lines.append(
            f"{int(row['rank'])}. {row['name']} ({row['ticker']}): "
            f"score {values[0]:.1f}, 5m {values[1]:+.2f}%, "
            f"20m {values[2]:+.2f}%, 60m {values[3]:+.2f}%, "
            f"spread {values[4]:.1f} bp, obalans {values[5]:+.2f}, "
            f"SMA20(20m) {values[6]:.2f}, "
            f"över_SMA20={str(bool(row['above_sma20'])).lower()}, "
            f"{eligibility}, book_state={int(row['book_state_id'])}"
        )
    return "\n".join(lines)[:10_000]
