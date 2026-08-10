from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.candidates import (
    CANDIDATE_POLICY_VERSION,
    CandidatePolicy,
    CandidateSignalError,
    candidate_policy_hash,
    rank_candidate_signals,
    render_candidate_context,
)


NOW = datetime(2026, 8, 4, 10, 30, tzinfo=timezone.utc)


def _signal(**overrides):
    values = {
        "ticker": "SE0000936478",
        "name": "Intrum AB",
        "sector": "Financial Services",
        "provider": "nasdaq-nordic-delayed-pre-trade",
        "source": "nasdaq-nordic-delayed-pre-trade",
        "book_state_id": 123,
        "latest_price": Decimal("35.50"),
        "sma20": Decimal("35.10"),
        "price_5m_ago": Decimal("35.30"),
        "price_20m_ago": Decimal("35.00"),
        "price_60m_ago": Decimal("34.50"),
        "bid_price": Decimal("35.48"),
        "ask_price": Decimal("35.52"),
        "bid_quantity": 1200,
        "ask_quantity": 800,
        "range_20_bps": Decimal("180"),
        "range_60_bps": Decimal("350"),
        "first_report_minute": NOW - timedelta(minutes=60),
        "last_report_minute": NOW,
        "latest_received_at": NOW + timedelta(minutes=15),
    }
    values.update(overrides)
    return values


def test_rank_candidate_signals_scores_multi_horizon_evidence():
    [candidate] = rank_candidate_signals([_signal()], limit=10)

    assert candidate["policy_version"] == CANDIDATE_POLICY_VERSION
    assert candidate["ticker"] == "SE0000936478"
    assert candidate["rank"] == 1
    assert candidate["eligible"] is True
    assert candidate["reason_code"] == "ELIGIBLE"
    assert candidate["momentum_5m_pct"] == pytest.approx(0.5666, abs=0.0001)
    assert candidate["momentum_20m_pct"] == pytest.approx(1.4286, abs=0.0001)
    assert candidate["momentum_60m_pct"] == pytest.approx(2.8986, abs=0.0001)
    assert candidate["spread_bps"] == pytest.approx(11.2676, abs=0.0001)
    assert candidate["book_imbalance"] == pytest.approx(0.2)
    assert candidate["sma20"] == pytest.approx(35.10)
    assert candidate["above_sma20"] is True
    assert candidate["feature_json"]["sma20"] == "35.10"
    assert candidate["feature_json"]["above_sma20"] is True
    assert 60 < candidate["signal_score"] <= 100


def test_rank_candidate_signals_rejects_wide_spread_without_hiding_row():
    [candidate] = rank_candidate_signals(
        [
            _signal(
                bid_price=Decimal("34.00"),
                ask_price=Decimal("37.00"),
            )
        ],
        limit=10,
    )

    assert candidate["eligible"] is False
    assert candidate["reason_code"] == "SPREAD_TOO_WIDE"
    assert candidate["signal_score"] == 0


def test_versioned_candidate_policy_filters_without_hiding_shadow_rows():
    policy = CandidatePolicy(
        version="xsto-momentum-v2",
        min_signal_score=95,
        max_spread_bps=250,
    )

    [candidate] = rank_candidate_signals(
        [_signal()],
        limit=10,
        policy=policy,
    )

    assert candidate["policy_version"] == "xsto-momentum-v2"
    assert candidate["eligible"] is False
    assert candidate["reason_code"] == "SCORE_BELOW_THRESHOLD"
    assert candidate["signal_score"] > 0
    assert candidate_policy_hash(
        CandidatePolicy(
            version="ignored-for-config-hash",
            min_signal_score=0,
            max_spread_bps=250,
        )
    ) == "c094e434d32c0976f395e9c4e5d7a7e9c3a3e9f70991e1ab3e8f3678d4b8a148"


def test_rank_candidate_signals_orders_by_score_then_ticker():
    ranked = rank_candidate_signals(
        [
            _signal(ticker="B", name="Bolag B"),
            _signal(
                ticker="A",
                name="Bolag A",
                price_5m_ago=Decimal("35.45"),
                price_20m_ago=Decimal("35.40"),
                price_60m_ago=Decimal("35.30"),
            ),
        ],
        limit=10,
    )

    assert [row["ticker"] for row in ranked] == ["B", "A"]
    assert [row["rank"] for row in ranked] == [1, 2]


def test_rank_candidate_signals_can_journal_the_full_xsto_universe():
    signals = [
        _signal(
            ticker=f"XSTO{index:03d}",
            name=f"Bolag {index}",
            book_state_id=index + 1,
        )
        for index in range(417)
    ]

    ranked = rank_candidate_signals(signals, limit=1_000)

    assert len(ranked) == 417
    assert ranked[0]["rank"] == 1
    assert ranked[-1]["rank"] == 417


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latest_price", Decimal("NaN")),
        ("sma20", None),
        ("bid_quantity", 0),
        ("first_report_minute", NOW),
        ("book_state_id", True),
    ],
)
def test_rank_candidate_signals_fails_closed_on_invalid_evidence(field, value):
    with pytest.raises(CandidateSignalError):
        rank_candidate_signals([_signal(**{field: value})], limit=10)


def test_render_candidate_context_is_human_readable_and_bounded():
    candidates = rank_candidate_signals([_signal()], limit=10)

    rendered = render_candidate_context(candidates)

    assert "Intrum AB" in rendered
    assert "SE0000936478" in rendered
    assert "5m +0.57%" in rendered
    assert "20m +1.43%" in rendered
    assert "60m +2.90%" in rendered
    assert "spread 11.3 bp" in rendered
    assert "SMA20(20m) 35.10, över_SMA20=true" in rendered
    assert "book_state=123" in rendered


def test_render_candidate_context_handles_empty_snapshot():
    assert render_candidate_context([]) == (
        "Inga kompletta, färska 60-minuterskandidater."
    )
