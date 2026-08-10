from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from src.core.trader import PaperTrader
from src.core.risk import ExitDecision
from src.core.strategy import baseline_strategy
from src.data.market_data import MarketDataError, QuoteRecord


class ValuationDatabase:
    def __init__(self, portfolio, quotes=None):
        self.portfolio = portfolio
        self.quotes = quotes or {}
        self.requested_tickers = []

    def get_balance(self):
        return {"cash": 18_000}

    def get_portfolio(self):
        return self.portfolio

    def get_latest_authorized_market_quote(self, ticker):
        self.requested_tickers.append(ticker)
        return self.quotes.get(ticker)

    def get_latest_prices(self, _tickers):
        raise AssertionError("legacy daily prices must not value positions")


def quote(ticker_price):
    return QuoteRecord(
        quote_id=73,
        isin="SE0000115446",
        mic="XSTO",
        event_time=datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 7, 29, 13, 15, tzinfo=timezone.utc),
        source="licensed-provider-delayed",
        last_price=Decimal(str(ticker_price)),
        currency="SEK",
    )


def test_cash_only_portfolio_does_not_require_market_data():
    database = ValuationDatabase(
        pd.DataFrame(columns=["ticker", "shares", "avg_price"]),
    )

    result = PaperTrader(database).get_portfolio_value()

    assert result == {
        "cash": 18_000,
        "positions_value": 0,
        "total_value": 18_000,
        "pnl": -2_000,
        "pnl_pct": pytest.approx(-10),
        "price_marks": [],
    }
    assert database.requested_tickers == []


def test_portfolio_uses_authorized_quote_with_exact_provenance():
    portfolio = pd.DataFrame(
        [{"ticker": "VOLV-B", "shares": 10, "avg_price": 100}],
    )
    database = ValuationDatabase(
        portfolio,
        {"VOLV-B": quote("125.50")},
    )

    result = PaperTrader(database).get_portfolio_value()

    assert result["positions_value"] == pytest.approx(1_255)
    assert result["total_value"] == pytest.approx(19_255)
    assert result["price_marks"] == [
        {
            "ticker": "VOLV-B",
            "quote_id": 73,
            "book_state_id": None,
            "source": "licensed-provider-delayed",
            "event_time": datetime(
                2026,
                7,
                29,
                13,
                0,
                tzinfo=timezone.utc,
            ),
            "price": 125.5,
        },
    ]


@pytest.mark.parametrize(
    "quote_value",
    [None, quote("125.50")],
)
def test_portfolio_fails_closed_without_authorized_quote_provenance(
    quote_value,
):
    if quote_value is not None:
        object.__setattr__(quote_value, "quote_id", None)
    portfolio = pd.DataFrame(
        [{"ticker": "VOLV-B", "shares": 10, "avg_price": 100}],
    )
    database = ValuationDatabase(portfolio, {"VOLV-B": quote_value})

    with pytest.raises(
        MarketDataError,
        match="authorized provider quote",
    ):
        PaperTrader(database).get_portfolio_value()


def test_exit_checks_never_fall_back_to_unapproved_quote():
    class ExitDatabase:
        def __init__(self):
            self.authorized_requests = []

        def get_active_strategy(self):
            return baseline_strategy()

        def get_portfolio(self):
            return pd.DataFrame(
                [{"ticker": "VOLV-B", "shares": 10, "avg_price": 100}],
            )

        def get_latest_authorized_market_quote(self, ticker):
            self.authorized_requests.append(ticker)
            return None

        def get_latest_market_quote(self, _ticker):
            raise AssertionError("unapproved quote path must not be used")

        def query(self, *_args, **_kwargs):
            raise AssertionError("missing authorized quote must stop first")

    database = ExitDatabase()

    PaperTrader(database).check_positions()

    assert database.authorized_requests == ["VOLV-B"]


def test_exit_check_retries_during_pretrade_batch_commit(monkeypatch):
    checked_at = datetime(2026, 7, 29, 13, 16, tzinfo=timezone.utc)
    retry_at = checked_at + timedelta(seconds=5)
    opened_at = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    calls = []
    sleeps = []

    class ExitDatabase:
        def get_active_strategy(self):
            return baseline_strategy()

        def get_portfolio(self):
            return pd.DataFrame(
                [{"ticker": "VOLV-B", "shares": 10, "avg_price": 100}],
            )

        def get_latest_authorized_execution_quote(
            self,
            ticker,
            *,
            action,
            now,
        ):
            calls.append((ticker, action, now))
            return None if len(calls) == 1 else quote("101")

        def query(self, _sql, params):
            assert params == {"ticker": "VOLV-B"}
            return [{"executed_at": opened_at, "stop_loss": None}]

    monkeypatch.setattr(
        "src.core.trader.sleep",
        lambda seconds: sleeps.append(seconds),
        raising=False,
    )
    monkeypatch.setattr(
        "src.core.trader._utc_now",
        lambda: retry_at,
        raising=False,
    )
    monkeypatch.setattr(
        "src.core.trader.assert_fresh_quote",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.core.trader.evaluate_exit",
        lambda **_kwargs: ExitDecision(
            should_sell=False,
            reason=None,
            pnl_pct=1,
            new_stop_loss=None,
        ),
    )

    PaperTrader(ExitDatabase()).check_positions(now=checked_at)

    assert calls == [
        ("VOLV-B", "SELL", checked_at),
        ("VOLV-B", "SELL", retry_at),
    ]
    assert sleeps == [5]


def test_exit_check_uses_one_injected_utc_clock(monkeypatch):
    checked_at = datetime(2026, 7, 29, 13, 16, tzinfo=timezone.utc)
    opened_at = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    observed = {}

    class ExitDatabase:
        def get_active_strategy(self):
            return baseline_strategy()

        def get_portfolio(self):
            return pd.DataFrame(
                [{"ticker": "VOLV-B", "shares": 10, "avg_price": 100}],
            )

        def get_latest_authorized_market_quote(self, ticker):
            assert ticker == "VOLV-B"
            return quote("101")

        def query(self, _sql, params):
            assert params == {"ticker": "VOLV-B"}
            return [{"executed_at": opened_at, "stop_loss": None}]

    def capture_freshness(_quote, *, now, policy):
        observed["freshness_now"] = now
        observed["policy"] = policy

    def capture_exit(**kwargs):
        observed["exit_now"] = kwargs["now"]
        return ExitDecision(
            should_sell=False,
            reason=None,
            pnl_pct=1,
            new_stop_loss=None,
        )

    monkeypatch.setattr(
        "src.core.trader.assert_fresh_quote",
        capture_freshness,
    )
    monkeypatch.setattr("src.core.trader.evaluate_exit", capture_exit)

    PaperTrader(ExitDatabase()).check_positions(now=checked_at)

    assert observed["freshness_now"] == checked_at
    assert observed["exit_now"] == checked_at


def test_exit_check_rejects_naive_injected_clock():
    trader = PaperTrader(ValuationDatabase(pd.DataFrame()))

    with pytest.raises(ValueError, match="timezone-aware"):
        trader.check_positions(now=datetime(2026, 7, 29, 13, 16))
