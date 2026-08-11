"""Fail-closed evaluation for a pre-registered forward paper experiment."""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import hashlib
import json
import math
import re
from typing import Any, Mapping
from urllib.parse import urlparse


MIN_TRADING_SESSIONS = 252
MIN_CLOSED_TRADES = 30
MAX_DRAWDOWN_PCT = 15.0
MIN_DATA_COVERAGE_PCT = 99.5
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,99}$")
_PROVIDER_KEY_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9-]{1,99}$"
)
_OPERATOR_PATTERN = re.compile(r"^operator:[A-Za-z0-9._-]{1,80}$")
_SHA_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class BenchmarkCriteria:
    min_trading_sessions: int = MIN_TRADING_SESSIONS
    min_closed_trades: int = MIN_CLOSED_TRADES
    max_drawdown_pct: float = MAX_DRAWDOWN_PCT
    min_data_coverage_pct: float = MIN_DATA_COVERAGE_PCT

    def __post_init__(self) -> None:
        _nonnegative_int(
            self.min_trading_sessions,
            "min_trading_sessions",
        )
        _nonnegative_int(self.min_closed_trades, "min_closed_trades")
        max_drawdown = _finite_number(
            self.max_drawdown_pct,
            "max_drawdown_pct",
        )
        coverage = _finite_number(
            self.min_data_coverage_pct,
            "min_data_coverage_pct",
        )
        if self.min_trading_sessions < MIN_TRADING_SESSIONS:
            raise ValueError(
                f"min_trading_sessions must be at least {MIN_TRADING_SESSIONS}"
            )
        if self.min_closed_trades < MIN_CLOSED_TRADES:
            raise ValueError(
                f"min_closed_trades must be at least {MIN_CLOSED_TRADES}"
            )
        if not 0 < max_drawdown <= MAX_DRAWDOWN_PCT:
            raise ValueError(
                f"max_drawdown_pct must be in (0, {MAX_DRAWDOWN_PCT}]"
            )
        if not MIN_DATA_COVERAGE_PCT <= coverage <= 100:
            raise ValueError(
                "min_data_coverage_pct must be between "
                f"{MIN_DATA_COVERAGE_PCT} and 100"
            )


@dataclass(frozen=True)
class BenchmarkMetrics:
    trading_sessions: int
    closed_trades: int
    net_return_pct: float
    benchmark_return_pct: float
    max_drawdown_pct: float
    data_coverage_pct: float
    critical_incidents: int

    def __post_init__(self) -> None:
        _nonnegative_int(self.trading_sessions, "trading_sessions")
        _nonnegative_int(self.closed_trades, "closed_trades")
        _nonnegative_int(self.critical_incidents, "critical_incidents")
        net_return = _finite_number(self.net_return_pct, "net_return_pct")
        benchmark_return = _finite_number(
            self.benchmark_return_pct,
            "benchmark_return_pct",
        )
        drawdown = _finite_number(
            self.max_drawdown_pct,
            "max_drawdown_pct",
        )
        coverage = _finite_number(
            self.data_coverage_pct,
            "data_coverage_pct",
        )
        if net_return < -100:
            raise ValueError("net_return_pct cannot be below -100")
        if benchmark_return < -100:
            raise ValueError("benchmark_return_pct cannot be below -100")
        if not -100 <= drawdown <= 0:
            raise ValueError("max_drawdown_pct must be between -100 and 0")
        if not 0 <= coverage <= 100:
            raise ValueError("data_coverage_pct must be between 0 and 100")


@dataclass(frozen=True)
class BenchmarkEvaluation:
    passed: bool
    excess_return_pct: float
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkStartEvidence:
    level_id: int
    benchmark_level: float
    event_time: datetime
    available_at: datetime
    source_checksum_sha256: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.level_id, bool)
            or not isinstance(self.level_id, int)
            or self.level_id <= 0
        ):
            raise ValueError("level_id must be a positive integer")
        level = _finite_number(
            self.benchmark_level,
            "benchmark_level",
        )
        if level <= 0:
            raise ValueError(
                "benchmark_level must be greater than zero"
            )
        object.__setattr__(self, "benchmark_level", level)
        _aware_datetime(self.event_time, "event_time")
        _aware_datetime(self.available_at, "available_at")
        if self.available_at < self.event_time:
            raise ValueError(
                "available_at cannot precede event_time"
            )
        if (
            not isinstance(self.source_checksum_sha256, str)
            or not _SHA256_PATTERN.fullmatch(
                self.source_checksum_sha256,
            )
        ):
            raise ValueError(
                "source_checksum_sha256 must be lowercase SHA-256"
            )


@dataclass(frozen=True)
class BenchmarkIncident:
    incident_key: str
    session_date: date
    severity: str
    description: str
    detected_at: datetime
    source_checksum_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.incident_key, str)
            or not _KEY_PATTERN.fullmatch(self.incident_key)
        ):
            raise ValueError("incident_key has invalid format")
        if type(self.session_date) is not date:
            raise ValueError("session_date must be a date")
        if self.severity not in {"WARNING", "CRITICAL"}:
            raise ValueError("severity must be WARNING or CRITICAL")
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or len(self.description.strip()) > 2000
        ):
            raise ValueError(
                "description must contain between 1 and 2000 characters"
            )
        object.__setattr__(self, "description", self.description.strip())
        _aware_datetime(self.detected_at, "detected_at")
        if (
            not isinstance(self.source_checksum_sha256, str)
            or not _SHA256_PATTERN.fullmatch(
                self.source_checksum_sha256,
            )
        ):
            raise ValueError(
                "source_checksum_sha256 must be lowercase SHA-256"
            )


@dataclass(frozen=True)
class BenchmarkRegistration:
    experiment_key: str
    strategy_version: str
    strategy_config_hash: str
    release_sha: str
    release_manifest_sha256: str
    agent_image_digest_sha256: str
    model_backend: str
    model_name: str
    model_evidence_sha256: str
    reference_snapshot_id: int
    universe_checksum_sha256: str
    quote_provider_contract_key: str
    execution_price_source: str
    execution_provider_contract_key: str
    benchmark_provider_contract_key: str
    benchmark_source_url: str
    benchmark_terms_url: str
    fee_bps: float
    spread_bps: float
    slippage_bps: float
    proposed_by: str
    criteria: BenchmarkCriteria = field(default_factory=BenchmarkCriteria)

    def __post_init__(self) -> None:
        from .strategy import validate_strategy_version

        if (
            not isinstance(self.experiment_key, str)
            or not _KEY_PATTERN.fullmatch(self.experiment_key)
        ):
            raise ValueError("experiment_key has invalid format")
        validate_strategy_version(self.strategy_version)
        for name in (
            "strategy_config_hash",
            "release_manifest_sha256",
            "agent_image_digest_sha256",
            "model_evidence_sha256",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not _SHA256_PATTERN.fullmatch(value)
            ):
                raise ValueError(f"{name} must be lowercase SHA-256")
        if (
            not isinstance(self.release_sha, str)
            or not _SHA_PATTERN.fullmatch(self.release_sha)
        ):
            raise ValueError("release_sha must be a lowercase git SHA")
        if self.model_backend not in {"openai-compatible", "anthropic"}:
            raise ValueError("model_backend is unsupported")
        if (
            not isinstance(self.model_name, str)
            or not self.model_name.strip()
            or self.model_name != self.model_name.strip()
            or len(self.model_name) > 200
        ):
            raise ValueError("model_name must be a bounded non-empty string")
        if (
            isinstance(self.reference_snapshot_id, bool)
            or not isinstance(self.reference_snapshot_id, int)
            or self.reference_snapshot_id <= 0
        ):
            raise ValueError("reference_snapshot_id must be a positive integer")
        if (
            not isinstance(self.universe_checksum_sha256, str)
            or not _SHA256_PATTERN.fullmatch(
                self.universe_checksum_sha256,
            )
        ):
            raise ValueError(
                "universe_checksum_sha256 must be lowercase SHA-256"
            )
        for name in (
            "quote_provider_contract_key",
            "execution_provider_contract_key",
            "benchmark_provider_contract_key",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not _PROVIDER_KEY_PATTERN.fullmatch(value)
            ):
                raise ValueError(f"{name} has invalid format")
        if (
            self.quote_provider_contract_key
            == self.benchmark_provider_contract_key
        ):
            raise ValueError(
                "quote and benchmark provider evidence must be separate"
            )
        if self.execution_price_source not in {
            "LAST_TRADE_PLUS_BPS",
            "TOP_OF_BOOK_PLUS_SLIPPAGE",
        }:
            raise ValueError("execution_price_source is unsupported")
        if (
            self.execution_provider_contract_key
            == self.benchmark_provider_contract_key
        ):
            raise ValueError(
                "execution and benchmark provider evidence must be separate"
            )
        if (
            self.execution_price_source == "LAST_TRADE_PLUS_BPS"
            and self.execution_provider_contract_key
            != self.quote_provider_contract_key
        ):
            raise ValueError(
                "last-trade execution must use the quote provider contract"
            )
        _https_url(self.benchmark_source_url, "benchmark_source_url")
        _https_url(self.benchmark_terms_url, "benchmark_terms_url")
        for name in ("fee_bps", "spread_bps", "slippage_bps"):
            value = _finite_number(getattr(self, name), name)
            if not 0 <= value <= 1000:
                raise ValueError(f"{name} must be between 0 and 1000")
            object.__setattr__(self, name, value)
        if (
            self.execution_price_source
            == "TOP_OF_BOOK_PLUS_SLIPPAGE"
            and self.spread_bps != 0
        ):
            raise ValueError(
                "top-of-book execution requires spread_bps to be zero"
            )
        if not isinstance(self.proposed_by, str) or not _OPERATOR_PATTERN.fullmatch(
            self.proposed_by
        ):
            raise ValueError("proposed_by must identify an operator")
        if not isinstance(self.criteria, BenchmarkCriteria):
            raise ValueError("criteria must be BenchmarkCriteria")

    def to_payload(self) -> dict[str, Any]:
        return {
            "experiment_key": self.experiment_key,
            "strategy_version": self.strategy_version,
            "strategy_config_hash": self.strategy_config_hash,
            "release_sha": self.release_sha,
            "release_manifest_sha256": self.release_manifest_sha256,
            "agent_image_digest_sha256": (
                self.agent_image_digest_sha256
            ),
            "model_backend": self.model_backend,
            "model_name": self.model_name,
            "model_evidence_sha256": self.model_evidence_sha256,
            "reference_snapshot_id": self.reference_snapshot_id,
            "universe_checksum_sha256": self.universe_checksum_sha256,
            "quote_provider_contract_key": (
                self.quote_provider_contract_key
            ),
            "execution_price_source": self.execution_price_source,
            "execution_provider_contract_key": (
                self.execution_provider_contract_key
            ),
            "benchmark_provider_contract_key": (
                self.benchmark_provider_contract_key
            ),
            "benchmark_symbol": "OMXSGI",
            "benchmark_kind": "TOTAL_RETURN_GROSS",
            "benchmark_source_url": self.benchmark_source_url,
            "benchmark_terms_url": self.benchmark_terms_url,
            "initial_capital": 20_000,
            "costs": {
                "fee_bps": self.fee_bps,
                "spread_bps": self.spread_bps,
                "slippage_bps": self.slippage_bps,
            },
            "criteria": asdict(self.criteria),
            "proposed_by": self.proposed_by,
        }

    @property
    def canonical_json(self) -> str:
        return json.dumps(
            self.to_payload(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(
            self.canonical_json.encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class BenchmarkObservation:
    session_date: date
    net_asset_value: float
    session_high_nav: float
    session_low_nav: float
    cash: float
    gross_exposure: float
    benchmark_level: float
    fees: float
    spread_cost: float
    slippage_cost: float
    expected_quote_points: int
    received_quote_points: int
    event_cutoff_at: datetime
    data_available_at: datetime
    source_checksum_sha256: str

    def __post_init__(self) -> None:
        if type(self.session_date) is not date:
            raise ValueError("session_date must be a date")
        for name in (
            "net_asset_value",
            "session_high_nav",
            "session_low_nav",
            "cash",
            "gross_exposure",
            "benchmark_level",
            "fees",
            "spread_cost",
            "slippage_cost",
        ):
            value = _finite_number(getattr(self, name), name)
            object.__setattr__(self, name, value)
        if self.net_asset_value <= 0:
            raise ValueError("net_asset_value must be greater than zero")
        if (
            self.session_low_nav <= 0
            or self.session_high_nav <= 0
            or self.session_low_nav > self.net_asset_value
            or self.net_asset_value > self.session_high_nav
        ):
            raise ValueError(
                "session_low_nav <= net_asset_value <= "
                "session_high_nav must hold"
            )
        if self.cash < 0 or self.cash > self.net_asset_value:
            raise ValueError("cash must be between zero and net asset value")
        if self.gross_exposure < 0:
            raise ValueError("gross_exposure cannot be negative")
        if abs(
            self.cash + self.gross_exposure - self.net_asset_value
        ) > 0.01:
            raise ValueError(
                "cash plus gross_exposure must equal net_asset_value"
            )
        if self.benchmark_level <= 0:
            raise ValueError("benchmark_level must be greater than zero")
        for name in ("fees", "spread_cost", "slippage_cost"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        _nonnegative_int(
            self.expected_quote_points,
            "expected_quote_points",
        )
        _nonnegative_int(
            self.received_quote_points,
            "received_quote_points",
        )
        if self.expected_quote_points <= 0:
            raise ValueError(
                "expected_quote_points must be greater than zero"
            )
        if self.received_quote_points > self.expected_quote_points:
            raise ValueError(
                "received_quote_points cannot exceed expected_quote_points"
            )
        _aware_datetime(self.event_cutoff_at, "event_cutoff_at")
        _aware_datetime(self.data_available_at, "data_available_at")
        if self.data_available_at < self.event_cutoff_at:
            raise ValueError(
                "data_available_at cannot precede event_cutoff_at"
            )
        if (
            not isinstance(self.source_checksum_sha256, str)
            or not _SHA256_PATTERN.fullmatch(
                self.source_checksum_sha256,
            )
        ):
            raise ValueError(
                "source_checksum_sha256 must be lowercase SHA-256"
            )


def evaluate_forward_benchmark(
    metrics: BenchmarkMetrics,
    criteria: BenchmarkCriteria,
) -> BenchmarkEvaluation:
    """Evaluate all frozen gates and return stable failure reason codes."""
    excess_return = metrics.net_return_pct - metrics.benchmark_return_pct
    reasons: list[str] = []
    if metrics.trading_sessions < criteria.min_trading_sessions:
        reasons.append("INSUFFICIENT_TRADING_SESSIONS")
    if metrics.closed_trades < criteria.min_closed_trades:
        reasons.append("INSUFFICIENT_CLOSED_TRADES")
    if metrics.net_return_pct <= 0:
        reasons.append("NET_RETURN_NOT_POSITIVE")
    if excess_return <= 0:
        reasons.append("BENCHMARK_NOT_OUTPERFORMED")
    if metrics.max_drawdown_pct < -criteria.max_drawdown_pct:
        reasons.append("MAX_DRAWDOWN_EXCEEDED")
    if metrics.data_coverage_pct < criteria.min_data_coverage_pct:
        reasons.append("DATA_COVERAGE_BELOW_MINIMUM")
    if metrics.critical_incidents:
        reasons.append("CRITICAL_INCIDENTS_PRESENT")
    return BenchmarkEvaluation(
        passed=not reasons,
        excess_return_pct=excess_return,
        reason_codes=tuple(reasons),
    )


def preregistration_hash(payload: Mapping[str, Any]) -> str:
    """Return a stable SHA-256 for an immutable registration payload."""
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("preregistration payload must be a non-empty object")
    try:
        canonical = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("preregistration payload is not canonical JSON") from exc
    return hashlib.sha256(canonical).hexdigest()


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _https_url(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError(f"{name} must be a bounded HTTPS URL")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be a public HTTPS URL")
    return value


def _aware_datetime(value: Any, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be timezone-aware")
    return value
