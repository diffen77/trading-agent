import json
from datetime import datetime, timezone

import pytest

from src.data.market_data import MarketDataError
from src.data.nasdaq_sector import (
    NasdaqSectorClient,
    NasdaqSectorSyncService,
)


NOW = datetime(2026, 8, 11, 17, 30, tzinfo=timezone.utc)


def _payload(rows):
    return json.dumps(
        {
            "data": {"instrumentListing": {"rows": rows}},
            "status": {"rCode": 200},
        }
    ).encode("utf-8")


class _Response:
    def __init__(self, content):
        self.content = content
        self.headers = {"Content-Length": str(len(content))}
        self.status_code = 200
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        assert chunk_size == 64 * 1024
        yield self.content

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, content):
        self.response = _Response(content)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_client_reads_nasdaq_sectors_by_isin_from_fixed_endpoint():
    session = _Session(
        _payload(
            [
                {
                    "isin": "SE0017486889",
                    "fullName": "Atlas Copco A",
                    "symbol": "ATCO A",
                    "sector": "Industrials",
                },
                {
                    "isin": "SE0009581051",
                    "fullName": "Isofol Medical",
                    "symbol": "ISOFOL",
                    "sector": "Health Care",
                },
            ]
        )
    )

    classifications = NasdaqSectorClient(session=session).fetch()

    assert classifications["SE0017486889"].sector == "Industrials"
    assert classifications["SE0009581051"].sector == "Health Care"
    assert session.calls == [
        (
            "https://api.nasdaq.com/api/nordic/screener/shares",
            {
                "params": {
                    "category": "MAIN_MARKET",
                    "tableonly": "false",
                    "market": "STO",
                },
                "timeout": (5.0, 30.0),
                "allow_redirects": False,
                "stream": True,
            },
        )
    ]
    assert session.response.closed


@pytest.mark.parametrize(
    "row,error",
    [
        (
            {
                "isin": "not-an-isin",
                "fullName": "Bad",
                "symbol": "BAD",
                "sector": "Technology",
            },
            "ISIN",
        ),
        (
            {
                "isin": "SE0017486889",
                "fullName": "Atlas Copco A",
                "symbol": "ATCO A",
                "sector": "Made Up Sector",
            },
            "sector",
        ),
    ],
)
def test_client_rejects_untrusted_provider_values(row, error):
    with pytest.raises(MarketDataError, match=error):
        NasdaqSectorClient(session=_Session(_payload([row]))).fetch()


class _Database:
    def __init__(self):
        self.updated = None

    def get_active_xsto_company_isins(self):
        return ("SE0017486889", "SE0009581051", "SE0000825820")

    def update_company_sectors(self, classifications, **metadata):
        self.updated = (classifications, metadata)
        return len(classifications)


class _Client:
    def fetch(self):
        return NasdaqSectorClient(
            session=_Session(
                _payload(
                    [
                        {
                            "isin": "SE0017486889",
                            "fullName": "Atlas Copco A",
                            "symbol": "ATCO A",
                            "sector": "Industrials",
                        },
                        {
                            "isin": "SE0009581051",
                            "fullName": "Isofol Medical",
                            "symbol": "ISOFOL",
                            "sector": "Health Care",
                        },
                    ]
                )
            )
        ).fetch()


def test_sync_fails_closed_before_writing_when_coverage_is_too_low():
    database = _Database()
    service = NasdaqSectorSyncService(
        database=database,
        client=_Client(),
        clock=lambda: NOW,
        minimum_coverage=0.80,
    )

    with pytest.raises(MarketDataError, match="coverage"):
        service.sync()

    assert database.updated is None


def test_sync_updates_only_exact_isin_matches_with_provenance():
    database = _Database()
    service = NasdaqSectorSyncService(
        database=database,
        client=_Client(),
        clock=lambda: NOW,
        minimum_coverage=0.60,
    )

    result = service.sync()

    classifications, metadata = database.updated
    assert set(classifications) == {"SE0017486889", "SE0009581051"}
    assert metadata == {
        "source": "nasdaq-public-screener-xsto",
        "verified_at": NOW,
    }
    assert result.active_instruments == 3
    assert result.matched_instruments == 2
    assert result.updated_companies == 2
    assert result.coverage == pytest.approx(2 / 3)
