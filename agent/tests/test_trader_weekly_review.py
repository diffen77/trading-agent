from datetime import datetime, timezone

import pytest

from src.core.trader import PaperTrader


class ReviewDatabase:
    def __init__(self):
        self.queries = []
        self.executions = []

    def query(self, sql, params):
        normalized = " ".join(sql.split()).lower()
        self.queries.append((normalized, params))
        assert "action = 'buy'" in normalized
        assert "closed_at is not null" in normalized
        assert "pnl is not null" in normalized
        assert "closed_at >=" in normalized
        return [
            {
                "id": 1,
                "ticker": "AAA",
                "pnl": 150,
                "sector": "Industrials",
            },
            {
                "id": 2,
                "ticker": "BBB",
                "pnl": -50,
                "sector": "Technology",
            },
        ]

    def execute(self, sql, params):
        self.executions.append((sql, params))


def test_weekly_review_uses_each_realized_entry_trade_once():
    database = ReviewDatabase()
    trader = PaperTrader(database)

    result = trader.run_weekly_review(
        now=datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc),
    )

    assert result == {
        "total_trades": 2,
        "winning_trades": 1,
        "losing_trades": 1,
        "total_pnl": pytest.approx(100),
        "win_rate": pytest.approx(50),
    }
    assert len(database.queries) == 1
    assert len(database.executions) == 1
    saved = database.executions[0][1]
    assert saved[2:7] == (2, 1, 1, 100.0, 50.0)
