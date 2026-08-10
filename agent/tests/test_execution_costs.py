from decimal import Decimal

import pytest

from src.core.execution_costs import (
    ExecutionCostModel,
    calculate_paper_execution,
    calculate_top_of_book_execution,
)


def test_buy_and_sell_apply_frozen_spread_slippage_and_fee_model():
    model = ExecutionCostModel(
        fee_bps=Decimal("5"),
        spread_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
    )

    buy = calculate_paper_execution(
        action="BUY",
        quote_price=Decimal("100"),
        shares=Decimal("10"),
        costs=model,
    )
    sell = calculate_paper_execution(
        action="SELL",
        quote_price=Decimal("100"),
        shares=Decimal("10"),
        costs=model,
    )

    assert buy.execution_price == Decimal("100.10000000")
    assert buy.gross_value == Decimal("1001.00")
    assert buy.fee_amount == Decimal("0.50")
    assert buy.spread_cost == Decimal("0.50")
    assert buy.slippage_cost == Decimal("0.50")
    assert buy.net_cash_effect == Decimal("1001.50")

    assert sell.execution_price == Decimal("99.90000000")
    assert sell.gross_value == Decimal("999.00")
    assert sell.fee_amount == Decimal("0.50")
    assert sell.net_cash_effect == Decimal("998.50")


def test_zero_cost_model_preserves_the_quote_and_notional():
    result = calculate_paper_execution(
        action="BUY",
        quote_price=Decimal("123.45"),
        shares=Decimal("2"),
        costs=ExecutionCostModel.zero(),
    )

    assert result.execution_price == Decimal("123.45000000")
    assert result.gross_value == Decimal("246.90")
    assert result.net_cash_effect == Decimal("246.90")


def test_top_of_book_buy_uses_ask_actual_spread_and_frozen_slippage():
    result = calculate_top_of_book_execution(
        action="BUY",
        bid_price=Decimal("99"),
        ask_price=Decimal("101"),
        shares=Decimal("10"),
        costs=ExecutionCostModel(
            fee_bps=Decimal("5"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("5"),
        ),
    )

    assert result.quote_price == Decimal("100.00000000")
    assert result.execution_price == Decimal("101.05050000")
    assert result.gross_value == Decimal("1010.51")
    assert result.fee_amount == Decimal("0.51")
    assert result.spread_cost == Decimal("10.00")
    assert result.slippage_cost == Decimal("0.51")
    assert result.net_cash_effect == Decimal("1011.02")


def test_top_of_book_sell_uses_bid_actual_spread_and_frozen_slippage():
    result = calculate_top_of_book_execution(
        action="SELL",
        bid_price=Decimal("99"),
        ask_price=Decimal("101"),
        shares=Decimal("10"),
        costs=ExecutionCostModel(
            fee_bps=Decimal("5"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("5"),
        ),
    )

    assert result.quote_price == Decimal("100.00000000")
    assert result.execution_price == Decimal("98.95050000")
    assert result.gross_value == Decimal("989.51")
    assert result.fee_amount == Decimal("0.49")
    assert result.spread_cost == Decimal("10.00")
    assert result.slippage_cost == Decimal("0.50")
    assert result.net_cash_effect == Decimal("989.02")


def test_top_of_book_spread_cost_uses_exact_half_spread_before_rounding():
    costs = ExecutionCostModel.zero()

    buy = calculate_top_of_book_execution(
        action="BUY",
        bid_price=Decimal("100.00000000"),
        ask_price=Decimal("100.00000001"),
        shares=Decimal("1000000"),
        costs=costs,
    )
    sell = calculate_top_of_book_execution(
        action="SELL",
        bid_price=Decimal("100.00000000"),
        ask_price=Decimal("100.00000001"),
        shares=Decimal("1000000"),
        costs=costs,
    )

    assert buy.quote_price == Decimal("100.00000001")
    assert sell.quote_price == Decimal("100.00000001")
    assert buy.spread_cost == Decimal("0.01")
    assert sell.spread_cost == Decimal("0.01")


@pytest.mark.parametrize(
    "overrides",
    [
        {"bid_price": Decimal("101"), "ask_price": Decimal("100")},
        {"bid_price": Decimal("0")},
        {"shares": Decimal("0")},
        {
            "costs": ExecutionCostModel(
                fee_bps=Decimal("5"),
                spread_bps=Decimal("1"),
                slippage_bps=Decimal("5"),
            )
        },
    ],
)
def test_top_of_book_execution_rejects_ambiguous_or_double_counted_inputs(
    overrides,
):
    values = {
        "action": "BUY",
        "bid_price": Decimal("99"),
        "ask_price": Decimal("101"),
        "shares": Decimal("10"),
        "costs": ExecutionCostModel(
            fee_bps=Decimal("5"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("5"),
        ),
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        calculate_top_of_book_execution(**values)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fee_bps": Decimal("-1")},
        {"spread_bps": Decimal("1001")},
        {"slippage_bps": Decimal("NaN")},
    ],
)
def test_cost_model_rejects_invalid_values(kwargs):
    values = {
        "fee_bps": Decimal("5"),
        "spread_bps": Decimal("10"),
        "slippage_bps": Decimal("5"),
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        ExecutionCostModel(**values)


@pytest.mark.parametrize(
    ("action", "quote_price", "shares"),
    [
        ("HOLD", Decimal("100"), Decimal("1")),
        ("BUY", Decimal("0"), Decimal("1")),
        ("BUY", Decimal("100"), Decimal("0")),
    ],
)
def test_execution_rejects_invalid_order_inputs(
    action,
    quote_price,
    shares,
):
    with pytest.raises(ValueError):
        calculate_paper_execution(
            action=action,
            quote_price=quote_price,
            shares=shares,
            costs=ExecutionCostModel.zero(),
        )
