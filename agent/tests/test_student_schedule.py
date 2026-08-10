from datetime import date, datetime, timezone

from src.core.student import TradingStudent


class Session:
    def __init__(self, opens_at, closes_at):
        self.opens_at = opens_at
        self.closes_at = closes_at

    def is_open(self, now):
        return self.opens_at <= now <= self.closes_at


class CalendarDatabase:
    def __init__(self, session):
        self.session = session
        self.requested = []

    def get_market_session(self, mic, session_date):
        self.requested.append((mic, session_date))
        return self.session


def test_student_uses_official_xsto_session_instead_of_fixed_utc_hours():
    session = Session(
        datetime(2026, 7, 29, 7, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 29, 15, 30, tzinfo=timezone.utc),
    )
    database = CalendarDatabase(session)
    student = TradingStudent.__new__(TradingStudent)
    student.db = database

    assert student.is_market_hours(
        now=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )
    assert not student.is_market_hours(
        now=datetime(2026, 7, 29, 15, 40, tzinfo=timezone.utc),
    )
    assert database.requested == [
        ("XSTO", date(2026, 7, 29)),
        ("XSTO", date(2026, 7, 29)),
    ]


def test_student_fails_closed_when_calendar_cannot_be_loaded():
    class BrokenDatabase:
        def get_market_session(self, _mic, _session_date):
            raise RuntimeError("database unavailable")

    student = TradingStudent.__new__(TradingStudent)
    student.db = BrokenDatabase()

    assert student.is_market_hours(
        now=datetime(2026, 7, 29, 20, 0, tzinfo=timezone.utc),
    )


def test_blocked_news_study_is_not_reported_as_completed():
    class ConnectedDatabase:
        def query(self, sql):
            assert sql == "SELECT 1"
            return [{"ok": 1}]

    student = TradingStudent.__new__(TradingStudent)
    student.db = ConnectedDatabase()
    student.should_run_study = lambda *, now: True
    student.run_news_research = lambda: {
        "status": "blocked",
        "reason": "authorized_research_provider_required",
        "research_notes_added": 0,
    }

    result = student.study_cycle(
        now=datetime(2026, 7, 29, 19, 0, tzinfo=timezone.utc),
    )

    assert result["studies_completed"] == []
    assert result["studies_blocked"] == [
        {
            "study": "news_research",
            "reason": "authorized_research_provider_required",
        }
    ]
