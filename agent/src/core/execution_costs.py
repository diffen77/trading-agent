"""Deterministic paper-fill costs for paper trading and benchmarks."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


_BPS = Decimal("10000")
_PRICE_QUANTUM = Decimal("0.00000001")
_MONEY_QUANTUM = Decimal("0.01")


@dataclass(frozen=True)
class ExecutionCostModel:
    fee_bps: Decimal
    spread_bps: Decimal
    slippage_bps: Decimal
    minimum_fee: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name in ("fee_bps", "spread_bps", "slippage_bps"):
            value = _finite_decimal(getattr(self, name), name)
            if not Decimal("0") <= value <= Decimal("1000"):
                raise ValueError(
                    f"{name} must be between 0 and 1000"
                )
            object.__setattr__(self, name, value)
        minimum_fee = _finite_decimal(self.minimum_fee, "minimum_fee")
        if not Decimal("0") <= minimum_fee <= Decimal("10000"):
            raise ValueError("minimum_fee must be between 0 and 10000")
        object.__setattr__(self, "minimum_fee", minimum_fee)

    @classmethod
    def zero(cls) -> "ExecutionCostModel":
        return cls(
            fee_bps=Decimal("0"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        )

    @classmethod
    def swedish_retail(cls) -> "ExecutionCostModel":
        """Default paper model: retail fee, actual book spread, slippage."""
        return cls(
            fee_bps=Decimal("25"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("5"),
            minimum_fee=Decimal("1"),
        )


@dataclass(frozen=True)
class PaperExecution:
    quote_price: Decimal
    execution_price: Decimal
    shares: Decimal
    gross_value: Decimal
    fee_amount: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    net_cash_effect: Decimal


def calculate_paper_execution(
    *,
    action: str,
    quote_price: Decimal,
    shares: Decimal,
    costs: ExecutionCostModel,
) -> PaperExecution:
    if action not in {"BUY", "SELL"}:
        raise ValueError("action must be BUY or SELL")
    quote = _finite_decimal(quote_price, "quote_price")
    quantity = _finite_decimal(shares, "shares")
    if quote <= 0 or quantity <= 0:
        raise ValueError("quote_price and shares must be greater than zero")
    if not isinstance(costs, ExecutionCostModel):
        raise ValueError("costs must be ExecutionCostModel")

    half_spread_bps = costs.spread_bps / Decimal("2")
    impact_bps = half_spread_bps + costs.slippage_bps
    direction = Decimal("1") if action == "BUY" else Decimal("-1")
    execution_price = (
        quote * (Decimal("1") + direction * impact_bps / _BPS)
    ).quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    if execution_price <= 0:
        raise ValueError("cost model produced a non-positive fill price")

    gross_value = (execution_price * quantity).quantize(
        _MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    fee_amount = max(
        (gross_value * costs.fee_bps / _BPS).quantize(
            _MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        ),
        costs.minimum_fee.quantize(
            _MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        ),
    )
    spread_cost = (
        quote * quantity * half_spread_bps / _BPS
    ).quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    slippage_cost = (
        quote * quantity * costs.slippage_bps / _BPS
    ).quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    if action == "BUY":
        net_cash_effect = gross_value + fee_amount
    else:
        net_cash_effect = gross_value - fee_amount
    return PaperExecution(
        quote_price=quote.quantize(
            _PRICE_QUANTUM,
            rounding=ROUND_HALF_UP,
        ),
        execution_price=execution_price,
        shares=quantity,
        gross_value=gross_value,
        fee_amount=fee_amount,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        net_cash_effect=net_cash_effect.quantize(
            _MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        ),
    )


def calculate_top_of_book_execution(
    *,
    action: str,
    bid_price: Decimal,
    ask_price: Decimal,
    shares: Decimal,
    costs: ExecutionCostModel,
) -> PaperExecution:
    """Price a paper fill from an executable Level 1 bid/ask pair."""
    if action not in {"BUY", "SELL"}:
        raise ValueError("action must be BUY or SELL")
    bid = _finite_decimal(bid_price, "bid_price")
    ask = _finite_decimal(ask_price, "ask_price")
    quantity = _finite_decimal(shares, "shares")
    if bid <= 0 or ask <= 0 or quantity <= 0:
        raise ValueError(
            "bid_price, ask_price, and shares must be greater than zero"
        )
    if bid > ask:
        raise ValueError("bid_price cannot exceed ask_price")
    if not isinstance(costs, ExecutionCostModel):
        raise ValueError("costs must be ExecutionCostModel")
    if costs.spread_bps != 0:
        raise ValueError(
            "top-of-book execution requires spread_bps to be zero"
        )

    midpoint = (bid + ask) / Decimal("2")
    executable_side = ask if action == "BUY" else bid
    direction = Decimal("1") if action == "BUY" else Decimal("-1")
    execution_price = (
        executable_side
        * (
            Decimal("1")
            + direction * costs.slippage_bps / _BPS
        )
    ).quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    if execution_price <= 0:
        raise ValueError("cost model produced a non-positive fill price")

    gross_value = (execution_price * quantity).quantize(
        _MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    fee_amount = max(
        (gross_value * costs.fee_bps / _BPS).quantize(
            _MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        ),
        costs.minimum_fee.quantize(
            _MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        ),
    )
    spread_cost = (
        abs(executable_side - midpoint) * quantity
    ).quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    slippage_cost = (
        executable_side
        * quantity
        * costs.slippage_bps
        / _BPS
    ).quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    net_cash_effect = (
        gross_value + fee_amount
        if action == "BUY"
        else gross_value - fee_amount
    )
    return PaperExecution(
        quote_price=midpoint.quantize(
            _PRICE_QUANTUM,
            rounding=ROUND_HALF_UP,
        ),
        execution_price=execution_price,
        shares=quantity,
        gross_value=gross_value,
        fee_amount=fee_amount,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        net_cash_effect=net_cash_effect.quantize(
            _MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        ),
    )


def _finite_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result
