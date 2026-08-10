"""Deterministic validation and exit rules for paper trading."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any, Mapping, Optional, AbstractSet


_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
_SUPPORTED_ACTIONS = frozenset({"BUY", "SELL", "HOLD"})
_SUPPORTED_OUTLOOKS = frozenset({"bullish", "neutral", "bearish"})


class DecisionValidationError(ValueError):
    """Raised when an AI-generated trading decision is not safe to execute."""


@dataclass(frozen=True)
class ExitDecision:
    """Result from deterministic evaluation of an open position."""

    should_sell: bool
    reason: Optional[str]
    pnl_pct: float
    new_stop_loss: Optional[float] = None


@dataclass(frozen=True)
class EntryRiskDecision:
    """Deterministic permission to increase portfolio exposure."""

    allowed: bool
    reason: Optional[str]
    daily_return_pct: float


def evaluate_entry_risk(
    *,
    trading_status: str,
    opening_equity: float,
    current_equity: float,
    max_daily_loss_pct: float,
) -> EntryRiskDecision:
    """Fail closed for manual halts and daily mark-to-market losses."""
    if trading_status not in {"ACTIVE", "HALTED"}:
        raise ValueError("trading_status must be ACTIVE or HALTED")

    opening = _finite_decimal(opening_equity, "opening_equity")
    current = _finite_decimal(current_equity, "current_equity")
    loss_limit = _finite_decimal(
        max_daily_loss_pct,
        "max_daily_loss_pct",
    )
    if opening <= 0:
        raise ValueError("opening_equity must be greater than 0")
    if current < 0:
        raise ValueError("current_equity must be non-negative")
    if not Decimal("0") < loss_limit <= Decimal("20"):
        raise ValueError(
            "max_daily_loss_pct must be greater than 0 and at most 20"
        )

    daily_return = ((current / opening) - Decimal("1")) * Decimal("100")
    daily_return_pct = float(daily_return)
    if trading_status == "HALTED":
        return EntryRiskDecision(
            allowed=False,
            reason="TRADING_HALTED",
            daily_return_pct=daily_return_pct,
        )
    if daily_return <= -loss_limit:
        return EntryRiskDecision(
            allowed=False,
            reason="DAILY_LOSS_LIMIT",
            daily_return_pct=daily_return_pct,
        )
    return EntryRiskDecision(
        allowed=True,
        reason=None,
        daily_return_pct=daily_return_pct,
    )


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise DecisionValidationError(f"{field} must be a number")

    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DecisionValidationError(f"{field} must be a number") from exc

    if not math.isfinite(result):
        raise DecisionValidationError(f"{field} must be finite")
    return result


def _finite_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite number")
    return result


def validate_decision(
    decision: Mapping[str, Any],
    *,
    allowed_tickers: Optional[AbstractSet[str]] = None,
    max_position_pct: float = 25,
) -> dict[str, Any]:
    """Return a normalized decision or reject unsafe/unrecognized input."""
    if not isinstance(decision, Mapping):
        raise DecisionValidationError("decision must be an object")

    raw_action = decision.get("action")
    if not isinstance(raw_action, str):
        raise DecisionValidationError("action must be a string")
    action = raw_action.strip().upper()
    if action not in _SUPPORTED_ACTIONS:
        raise DecisionValidationError(f"unsupported action: {action!r}")

    raw_ticker = decision.get("ticker")
    if not isinstance(raw_ticker, str):
        raise DecisionValidationError("ticker must be a string")
    ticker = raw_ticker.strip().upper()
    if not _TICKER_PATTERN.fullmatch(ticker):
        raise DecisionValidationError("ticker has invalid format")
    if allowed_tickers is not None and ticker not in allowed_tickers:
        raise DecisionValidationError(f"ticker is outside the instrument universe: {ticker}")

    confidence = _finite_number(decision.get("confidence"), "confidence")
    if not 0 <= confidence <= 100:
        raise DecisionValidationError("confidence must be between 0 and 100")

    raw_reason = decision.get("reason", "")
    if not isinstance(raw_reason, str):
        raise DecisionValidationError("reason must be a string")
    reason = raw_reason.strip()
    if action != "HOLD" and not reason:
        raise DecisionValidationError("reason is required for BUY and SELL")
    if len(reason) > 2_000:
        raise DecisionValidationError("reason is too long")

    normalized = {
        "action": action,
        "ticker": ticker,
        "reason": reason,
        "confidence": confidence,
    }

    if action == "BUY":
        size_pct = _finite_number(
            decision.get("position_size_pct"),
            "position_size_pct",
        )
        if not 0 < size_pct <= max_position_pct:
            raise DecisionValidationError(
                "position_size_pct must be greater than 0 and at most "
                f"{max_position_pct:g}"
            )
        normalized["position_size_pct"] = size_pct
    elif "position_size_pct" in decision:
        size_pct = _finite_number(
            decision.get("position_size_pct"),
            "position_size_pct",
        )
        if not 0 <= size_pct <= max_position_pct:
            raise DecisionValidationError(
                "position_size_pct must be between 0 and "
                f"{max_position_pct:g}"
            )
        normalized["position_size_pct"] = size_pct

    return normalized


def validate_decision_response(payload: Any) -> dict[str, Any]:
    """Validate the LLM response envelope before it reaches trading logic."""
    if not isinstance(payload, Mapping):
        raise DecisionValidationError("decision response must be an object")

    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise DecisionValidationError("decisions must be a list")
    if len(decisions) > 100:
        raise DecisionValidationError("too many decisions in one response")

    outlook = payload.get("market_outlook")
    if not isinstance(outlook, str) or outlook not in _SUPPORTED_OUTLOOKS:
        raise DecisionValidationError(
            "market_outlook must be bullish, neutral, or bearish"
        )

    summary = payload.get("analysis_summary")
    if not isinstance(summary, str):
        raise DecisionValidationError("analysis_summary must be a string")
    if len(summary) > 5_000:
        raise DecisionValidationError("analysis_summary is too long")

    return {
        "decisions": list(decisions),
        "market_outlook": outlook,
        "analysis_summary": summary,
    }


def evaluate_exit(
    *,
    entry_price: float,
    current_price: float,
    opened_at: datetime,
    now: datetime,
    stored_stop_loss: Optional[float] = None,
    stop_loss_pct: float = -5,
    take_profit_pct: float = 10,
    trailing_activation_pct: float = 5,
    trailing_floor_pct: float = 2,
    time_stop_days: int = 10,
    time_stop_min_gain_pct: float = 3,
) -> ExitDecision:
    """Evaluate hard exits without delegating risk enforcement to an LLM."""
    entry = _finite_positive(entry_price, "entry_price")
    current = _finite_positive(current_price, "current_price")
    _validate_timestamps(opened_at, now)

    stored_stop = None
    if stored_stop_loss is not None:
        stored_stop = _finite_positive(stored_stop_loss, "stored_stop_loss")

    pnl_pct = ((current / entry) - 1) * 100

    if pnl_pct >= take_profit_pct:
        return ExitDecision(True, "TAKE_PROFIT", pnl_pct)

    if stored_stop is not None and current <= stored_stop:
        reason = "TRAILING_STOP" if stored_stop > entry else "STOP_LOSS"
        return ExitDecision(True, reason, pnl_pct)

    if pnl_pct <= stop_loss_pct:
        return ExitDecision(True, "STOP_LOSS", pnl_pct)

    holding_period = now - opened_at
    if holding_period.total_seconds() >= time_stop_days * 86_400:
        if pnl_pct < time_stop_min_gain_pct:
            return ExitDecision(True, "TIME_STOP", pnl_pct)

    if pnl_pct >= trailing_activation_pct:
        trailing_floor = entry * (1 + trailing_floor_pct / 100)
        if stored_stop is None or trailing_floor > stored_stop:
            return ExitDecision(False, None, pnl_pct, trailing_floor)

    return ExitDecision(False, None, pnl_pct)


def _finite_positive(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be finite and greater than 0")
    return result


def _validate_timestamps(opened_at: datetime, now: datetime) -> None:
    if not isinstance(opened_at, datetime) or not isinstance(now, datetime):
        raise ValueError("opened_at and now must be datetime values")
    if (opened_at.tzinfo is None) != (now.tzinfo is None):
        raise ValueError("opened_at and now must use compatible timezones")
    if opened_at > now:
        raise ValueError("opened_at cannot be in the future")
