from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src.core.trader import PaperTrader


class LearningDatabase:
    def __init__(self):
        self.executions = []
        self.requested_tickers = []

    def get_trades(self, limit):
        assert limit == 50
        return pd.DataFrame(
            [
                {
                    "id": 17,
                    "ticker": "VOLV-B",
                    "action": "BUY",
                    "shares": 10,
                    "price": 100,
                    "executed_at": datetime(
                        2026, 7, 1, 8, 0, tzinfo=timezone.utc
                    ),
                    "outcome_correct": None,
                    "reasoning": "Test",
                    "hypothesis": "Price rises",
                }
            ]
        )

    def get_authorized_daily_bars(self, ticker, *, limit):
        self.requested_tickers.append(ticker)
        assert limit == 250
        return [
            {
                "date": date(2026, 7, 16),
                "close": 104,
                "event_time": datetime(
                    2026, 7, 16, 15, 30, tzinfo=timezone.utc
                ),
                "source": "licensed-provider-delayed",
            },
            {
                "date": date(2026, 7, 15),
                "close": 102,
                "event_time": datetime(
                    2026, 7, 15, 15, 30, tzinfo=timezone.utc
                ),
                "source": "licensed-provider-delayed",
            },
        ]

    def get_latest_authorized_market_quote(self, _ticker):
        raise AssertionError(
            "hypothesis evaluation must use a governed official-session bar"
        )

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def add_learning(self, _learning):
        raise AssertionError("a 2% move must not create a strong-move learning")


def test_hypothesis_evaluation_is_deterministic_and_never_overwrites_ledger_pnl():
    database = LearningDatabase()
    trader = PaperTrader(database)

    result = trader.validate_hypotheses(
        days_to_check=14,
        now=datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc),
    )

    assert database.requested_tickers == ["VOLV-B"]
    assert len(result) == 1
    assert result[0]["pnl_pct"] == pytest.approx(2)
    assert len(database.executions) == 1
    sql, params = database.executions[0]
    assert "outcome_correct" in sql
    assert "pnl =" not in sql.lower()
    assert params[1] is True
    assert params[2] == 17


def test_legacy_arbitrary_price_outcome_checkpoint_is_disabled():
    class NoDatabaseAccess:
        def query(self, *_args, **_kwargs):
            raise AssertionError("disabled path must not read the database")

        def execute(self, *_args, **_kwargs):
            raise AssertionError("disabled path must not write the database")

    trader = PaperTrader(NoDatabaseAccess())

    with pytest.raises(RuntimeError, match="validate_hypotheses"):
        trader.record_trade_outcome(
            trade_id=17,
            current_price=105,
            entry_price=100,
            shares=10,
        )
