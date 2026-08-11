from types import SimpleNamespace

import pandas as pd
import pytest

from src.core.brain import trade_idempotency_key
from src.core.risk import EntryRiskDecision
from src.core.strategy import baseline_strategy
from src.core.trader import PaperTrader


class FakeDatabase:
    def __init__(self, *, inserted=True, entry_allowed=True):
        self.inserted = inserted
        self.entry_allowed = entry_allowed
        self.logged_trade = None
        self.entry_risk_checks = []

    def get_balance(self):
        return {"cash": 20_000}

    def get_portfolio(self):
        return pd.DataFrame(columns=["ticker"])

    def get_active_strategy(self):
        return baseline_strategy()

    def evaluate_entry_risk(self, *, total_value, evaluated_at):
        self.entry_risk_checks.append({
            "total_value": total_value,
            "evaluated_at": evaluated_at,
        })
        return EntryRiskDecision(
            allowed=self.entry_allowed,
            reason=None if self.entry_allowed else "TRADING_HALTED",
            daily_return_pct=0,
        )

    def log_trade_result(self, trade):
        self.logged_trade = trade
        return SimpleNamespace(trade_id=17, inserted=self.inserted)


def opportunity(**changes):
    value = {
        "ticker": "VOLV-B",
        "action": "BUY",
        "position_size": 2_000,
        "execution_price": 100,
        "reasoning": "Test",
        "confidence": 75,
        "idempotency_key": "brain:0123456789abcdef0123456789abcdef0123456789abcdef",
        "source_quote_id": 41,
    }
    value.update(changes)
    return value


def test_paper_trader_requires_and_propagates_idempotency_key():
    database = FakeDatabase()
    trader = PaperTrader(database)

    assert trader.execute_trade(opportunity()) is True
    assert (
        database.logged_trade["idempotency_key"]
        == opportunity()["idempotency_key"]
    )
    assert database.logged_trade["decision_origin"] == "MANUAL"
    assert database.logged_trade["decision_id"] is None

    database.logged_trade = None
    assert trader.execute_trade(opportunity(idempotency_key=None)) is False
    assert database.logged_trade is None


def test_paper_trader_does_not_report_duplicate_as_new_execution():
    database = FakeDatabase(inserted=False)
    trader = PaperTrader(database)

    assert trader.execute_trade(opportunity()) is False


def test_paper_trader_rejects_trade_without_exact_source_quote():
    database = FakeDatabase()
    trader = PaperTrader(database)

    assert trader.execute_trade(
        opportunity(source_quote_id=None),
    ) is False
    assert trader.execute_trade(
        opportunity(execution_price=None),
    ) is False
    assert database.logged_trade is None


def test_paper_trader_accepts_exact_pretrade_book_state():
    database = FakeDatabase()
    trader = PaperTrader(database)

    candidate = opportunity(
        source_quote_id=None,
        source_book_state_id=73,
    )

    assert trader.execute_trade(candidate) is True
    assert database.logged_trade["source_quote_id"] is None
    assert database.logged_trade["source_book_state_id"] == 73


def test_paper_trader_rejects_ambiguous_market_evidence():
    database = FakeDatabase()
    trader = PaperTrader(database)

    assert trader.execute_trade(
        opportunity(source_book_state_id=73),
    ) is False
    assert database.logged_trade is None


def test_paper_trader_blocks_buy_when_entry_risk_guard_is_closed():
    database = FakeDatabase(entry_allowed=False)
    trader = PaperTrader(database)

    assert trader.execute_trade(opportunity()) is False
    assert database.logged_trade is None
    assert database.entry_risk_checks[0]["total_value"] == 20_000


def test_paper_trader_allows_sell_while_entry_risk_guard_is_closed():
    database = FakeDatabase(entry_allowed=False)
    trader = PaperTrader(database)

    assert trader.execute_trade(
        opportunity(
            action="SELL",
            position_size=1_000,
            idempotency_key="auto-exit:test-sell",
        ),
    ) is True
    assert database.logged_trade["action"] == "SELL"
    assert database.entry_risk_checks == []


def test_brain_trade_key_is_stable_per_cycle_action_and_ticker():
    first = trade_idempotency_key(
        cycle_key="scheduled-brain:123",
        action="BUY",
        ticker="VOLV-B",
    )
    duplicate = trade_idempotency_key(
        cycle_key="scheduled-brain:123",
        action="BUY",
        ticker="VOLV-B",
    )
    next_cycle = trade_idempotency_key(
        cycle_key="scheduled-brain:124",
        action="BUY",
        ticker="VOLV-B",
    )

    assert duplicate == first
    assert next_cycle != first
    assert first.startswith("brain:")


def test_auto_trade_derives_stable_key_without_mutating_opportunity():
    database = FakeDatabase()
    trader = PaperTrader(database)
    candidate = opportunity(idempotency_key=None)
    candidate.pop("idempotency_key")

    first = trader.auto_trade([candidate], cycle_key="scan:2026-07-29T10:00")
    first_key = database.logged_trade["idempotency_key"]
    database.logged_trade = None
    duplicate = trader.auto_trade(
        [candidate],
        cycle_key="scan:2026-07-29T10:00",
    )

    assert len(first) == 1
    assert len(duplicate) == 1
    assert database.logged_trade["idempotency_key"] == first_key
    assert database.logged_trade["decision_origin"] == "AUTOMATED_SCAN"
    assert database.logged_trade["decision_id"] is None
    assert first_key.startswith("auto:")
    assert "idempotency_key" not in candidate


def test_auto_trade_requires_cycle_key():
    trader = PaperTrader(FakeDatabase())

    with pytest.raises(ValueError, match="cycle_key is required"):
        trader.auto_trade([opportunity()], cycle_key="")
