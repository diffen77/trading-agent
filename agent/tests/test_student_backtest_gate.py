import pytest

from src.core.student import TradingStudent


def test_student_rejects_ungoverned_web_search_injection():
    with pytest.raises(TypeError):
        TradingStudent(object(), lambda *_args, **_kwargs: {})


def test_student_cannot_run_legacy_unvalidated_backtest():
    student = TradingStudent.__new__(TradingStudent)

    result = student.run_backtest_engine()

    assert result == {
        "status": "blocked",
        "reason": "operator_validated_dataset_required",
        "backtests_run": 0,
    }


def test_student_cannot_study_reports_without_governed_history():
    student = TradingStudent.__new__(TradingStudent)
    student.db = object()

    assert student.run_report_study() == {
        "status": "blocked",
        "reason": "governed_historical_market_data_required",
        "reactions_analyzed": 0,
    }


def test_student_cannot_review_trades_without_governed_history():
    student = TradingStudent.__new__(TradingStudent)
    student.db = object()
    student.claude_client = object()

    assert student.run_trade_review() == {
        "status": "blocked",
        "reason": "governed_historical_market_data_required",
        "learnings_added": 0,
    }


def test_student_cannot_search_news_without_authorized_provider():
    class NoDatabaseAccess:
        def query(self, *_args, **_kwargs):
            raise AssertionError("blocked study must not read the database")

    student = TradingStudent.__new__(TradingStudent)
    student.db = NoDatabaseAccess()
    student.web_search = lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(
            AssertionError("blocked study must not use web search")
        )
    )

    assert student.run_news_research() == {
        "status": "blocked",
        "reason": "authorized_research_provider_required",
        "research_notes_added": 0,
    }


def test_student_cannot_self_study_without_authorized_provider():
    class NoDatabaseAccess:
        def get_active_strategy(self):
            raise AssertionError("blocked study must not read the database")

    student = TradingStudent.__new__(TradingStudent)
    student.db = NoDatabaseAccess()
    student.web_search = lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(
            AssertionError("blocked study must not use web search")
        )
    )

    assert student.run_self_study() == {
        "status": "blocked",
        "reason": "authorized_research_provider_required",
        "insights_added": 0,
    }
