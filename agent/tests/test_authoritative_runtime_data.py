from datetime import date, datetime, timedelta, timezone

import pytest

from src.core.analyzer import MarketAnalyzer
from src.core.brain import TradingBrain
from src.data.market_data import MarketDataError
from src.main import run_market_open_routine


NOW = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)


class AnalyzerDatabase:
    def __init__(self):
        self.saved_signals = []

    def query(self, sql, params=None):
        if "FROM companies" in sql:
            return [
                {
                    "ticker": "VOLV-B",
                    "name": "Volvo B",
                    "sector": "Industrials",
                }
            ]
        if "FROM input_dependencies" in sql:
            return []
        if "FROM prices" in sql:
            raise AssertionError("legacy daily prices are not authoritative")
        if "FROM macro" in sql:
            raise AssertionError("legacy macro data is not authoritative")
        return []

    def get_latest_authorized_prices(self):
        return [
            {
                "ticker": "VOLV-B",
                "close": 125.5,
                "date": date(2026, 7, 29),
                "change_pct": 1.25,
                "quote_id": 73,
                "source": "licensed-provider-delayed",
                "event_time": NOW - timedelta(minutes=15),
            }
        ]

    def get_authorized_daily_bars(self, ticker, *, limit):
        assert ticker == "VOLV-B"
        assert limit == 60
        return [
            {
                "date": date(2026, 7, 10) + timedelta(days=index),
                "open": 100 + index,
                "high": 101 + index,
                "low": 99 + index,
                "close": 100.5 + index,
                "volume": 1_000 + index,
            }
            for index in range(20)
        ]

    def get_current_authorized_index_change(self, now):
        assert now == NOW
        return {
            "symbol": "OMXSGI",
            "current_level": 245,
            "previous_close": 250,
            "change_pct": -2,
            "provider": "licensed-index",
            "source": "licensed-index-delayed",
            "event_time": NOW - timedelta(minutes=15),
            "received_at": NOW,
        }

    def execute(self, sql, params=None):
        if "INSERT INTO technical_signals" in sql:
            self.saved_signals.append(params)


def test_analyzer_uses_only_authorized_provider_prices_and_bars():
    database = AnalyzerDatabase()
    analyzer = MarketAnalyzer(database)

    latest = analyzer.get_latest_prices()
    analyzer.run_technical_analysis()

    assert latest["VOLV-B"]["quote_id"] == 73
    assert len(database.saved_signals) == 1


def test_analyzer_and_brain_use_only_authorized_index_context():
    database = AnalyzerDatabase()
    analyzer = MarketAnalyzer(database)
    market = analyzer.get_authorized_market_context(now=NOW)
    brain = TradingBrain.__new__(TradingBrain)
    brain.db = database
    brain._now_utc = lambda: NOW

    assert market["OMXSGI"]["change_pct"] == -2
    assert "OMXSGI: 245.00 (-2.0%)" in brain._get_macro_context()
    assert "VOLV-B: 125.50 SEK" in brain._get_prices_context()


def test_brain_describes_public_pretrade_breadth_without_requiring_index():
    class PublicPreTradeDatabase(AnalyzerDatabase):
        def require_operational_market_data(self, now):
            assert now == NOW
            return {
                "provider": "nasdaq-nordic",
                "data_type": "delayed-pre-trade-equity",
                "eligible_instrument_count": 254,
            }

        def get_current_authorized_index_change(self, _now):
            raise AssertionError(
                "public pre-trade context must not require OMXSGI"
            )

    brain = TradingBrain.__new__(TradingBrain)
    brain.db = PublicPreTradeDatabase()
    brain._now_utc = lambda: NOW

    context = brain._get_macro_context()

    assert "254" in context
    assert "OMXSGI krävs inte" in context


def test_missing_macro_evidence_contributes_no_opportunity_points():
    analyzer = MarketAnalyzer(AnalyzerDatabase())

    score = analyzer._calculate_opportunity_score(
        "VOLV-B",
        {"sector": "Industrials"},
        {"net_sentiment": 0, "impacts": []},
        {"change_pct": 0},
        None,
    )

    assert score["macro"] == 0


def test_nasdaq_sector_names_contribute_expected_opportunity_points():
    analyzer = MarketAnalyzer(AnalyzerDatabase())

    score = analyzer._calculate_opportunity_score(
        "ISOFOL",
        {"sector": "Health Care"},
        {"net_sentiment": 0, "impacts": []},
        {"change_pct": 0},
        None,
    )

    assert score["sector"] == 17


def test_analyzer_builds_prospects_from_multi_horizon_pretrade_evidence():
    from decimal import Decimal

    writes = []

    class CandidateDatabase:
        def query(self, sql, params=None):
            if "SELECT * FROM companies" in sql:
                return [{
                    "ticker": "VOLV-B",
                    "name": "Volvo B",
                    "sector": "Industrials",
                }]
            if "SELECT * FROM input_dependencies" in sql:
                return []
            return []

        def get_current_authorized_candidate_signals(self, *, now, limit):
            assert now == NOW
            assert limit == 1_000
            return [{
                "ticker": "VOLV-B",
                "name": "Volvo B",
                "sector": "Industrials",
                "provider": "nasdaq-nordic",
                "source": "nasdaq-nordic-delayed-pre-trade",
                "book_state_id": 73,
                "latest_price": Decimal("101.00"),
                "sma20": Decimal("100.70"),
                "price_5m_ago": Decimal("100.80"),
                "price_20m_ago": Decimal("100.40"),
                "price_60m_ago": Decimal("99.50"),
                "bid_price": Decimal("100.98"),
                "ask_price": Decimal("101.02"),
                "bid_quantity": Decimal("1200"),
                "ask_quantity": Decimal("800"),
                "range_20_bps": Decimal("100"),
                "range_60_bps": Decimal("250"),
                "first_report_minute": NOW - timedelta(minutes=75),
                "last_report_minute": NOW - timedelta(minutes=15),
                "latest_received_at": NOW,
            }]

        def execute(self, sql, params=None):
            writes.append((sql, params))

    analyzer = MarketAnalyzer(CandidateDatabase())

    opportunities = analyzer.find_opportunities(now=NOW)
    updated = analyzer.update_prospects(now=NOW)

    assert len(opportunities) == 1
    assert opportunities[0]["ticker"] == "VOLV-B"
    assert opportunities[0]["policy_version"] == "xsto-momentum-v1"
    assert "5 min" in opportunities[0]["thesis"]
    assert updated == 1
    assert len(writes) == 2
    assert "is_current = FALSE" in writes[0][0]
    assert writes[1][1][-4] == "xsto-momentum-v1"
    assert writes[1][1][-3] == 73


class RoutineDatabase:
    def __init__(self, *, ready=True):
        self.ready = ready
        self.readiness_checks = []
        self.snapshots = 0

    def require_operational_market_data(self, now):
        self.readiness_checks.append(now)
        if not self.ready:
            raise MarketDataError("full XSTO quote coverage is missing")

    def save_portfolio_snapshot(self):
        self.snapshots += 1


class RoutineAnalyzer:
    def __init__(self):
        self.scans = 0
        self.prospect_updates = 0

    def find_opportunities(self, *, now=None):
        self.scans += 1
        return []

    def update_prospects(self, *, now=None):
        self.prospect_updates += 1


class RoutineTrader:
    def __init__(self):
        self.exit_checks = []

    def check_positions(self, *, now):
        self.exit_checks.append(now)


def test_market_open_requires_complete_operational_provider_data():
    database = RoutineDatabase()
    analyzer = RoutineAnalyzer()
    trader = RoutineTrader()

    run_market_open_routine(
        database,
        analyzer,
        trader,
        now=NOW,
    )

    assert database.readiness_checks == [NOW]
    assert analyzer.scans == 1
    assert analyzer.prospect_updates == 1
    assert trader.exit_checks == [NOW]
    assert database.snapshots == 1


def test_market_open_fails_before_analysis_when_provider_data_is_not_ready():
    database = RoutineDatabase(ready=False)
    analyzer = RoutineAnalyzer()
    trader = RoutineTrader()

    with pytest.raises(MarketDataError, match="coverage"):
        run_market_open_routine(
            database,
            analyzer,
            trader,
            now=NOW,
        )

    assert analyzer.scans == 0
    assert analyzer.prospect_updates == 0
    assert trader.exit_checks == []
    assert database.snapshots == 0


def test_market_open_rejects_naive_clock_before_readiness_or_analysis():
    database = RoutineDatabase()
    analyzer = RoutineAnalyzer()
    trader = RoutineTrader()

    with pytest.raises(ValueError, match="timezone-aware"):
        run_market_open_routine(
            database,
            analyzer,
            trader,
            now=datetime(2026, 7, 29, 10, 0),
        )

    assert database.readiness_checks == []
    assert analyzer.scans == 0
    assert analyzer.prospect_updates == 0
    assert trader.exit_checks == []
    assert database.snapshots == 0
