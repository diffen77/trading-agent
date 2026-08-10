from src.core.student import TradingStudent


class RecordingDatabase:
    def __init__(self):
        self.queries = []

    def query(self, sql):
        self.queries.append(" ".join(sql.split()).lower())
        return []


def test_strategy_evolution_only_analyzes_closed_realized_entry_trades():
    database = RecordingDatabase()
    student = TradingStudent.__new__(TradingStudent)
    student.db = database

    result = student._analyze_trade_patterns()

    assert result == {
        "sector_performance": [],
        "time_performance": [],
        "confidence_analysis": [],
    }
    assert len(database.queries) == 3
    for sql in database.queries:
        assert "action = 'buy'" in sql
        assert "closed_at is not null" in sql
        assert "pnl is not null" in sql
        assert "avg(case when t.pnl" not in sql
