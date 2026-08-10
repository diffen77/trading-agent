from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.market_data import InstrumentRecord, MarketDataError
from src.data.nasdaq_reference import NasdaqInstrumentAlias
from src.data.nasdaq_delayed import (
    NasdaqDelayedPostTradeProvider,
    _download_with_curl,
    build_nasdaq_http_session,
    discover_post_trade_files,
    parse_post_trade_catalog,
    parse_post_trade_csv,
)


HEADER = (
    "Trading date and time;Instrument identification code;Price;"
    "Missing Price;Price currency;Price notation;Quantity;"
    "Venue of execution;Trading system;Publication date and time;"
    "Venue of publication;Transaction identification code;Flags"
)


def _instrument() -> InstrumentRecord:
    return InstrumentRecord(
        isin="SE0000115446",
        symbol="VOLV B",
        name="Volvo B",
        mic="XSTO",
        currency="EUR",
        instrument_type="COMMON_STOCK",
        source="esma-firds",
    )


def _alias(*, trading_currency: str = "SEK") -> NasdaqInstrumentAlias:
    return NasdaqInstrumentAlias(
        isin="SE0000115446",
        provider_symbol="VOLV B",
        trading_currency=trading_currency,
        tradable_id="101",
        source_id="VOLV-B",
        valid_from=None,
        valid_to=None,
    )


def _row(*, timestamp: str, price: str, venue: str = "XSTO") -> str:
    return (
        f"{timestamp};SE0000115446;{price};;SEK;MONE;100;"
        f"{venue};CLOB;{timestamp};{venue};trade-1;---"
    )


def test_discovers_only_valid_nordic_post_trade_equity_files():
    html = """
    <a href="/api/regulatory/trade-report/download?type=POST_TRADE&amp;assetClass=EQUITY&amp;fileName=NordicEquity-posttrade-2026-07-29T1455">old</a>
    <a href="/api/regulatory/trade-report/download?type=POST_TRADE&amp;assetClass=EQUITY&amp;fileName=NordicEquity-posttrade-2026-07-29T1456">new</a>
    <a href="https://evil.example/download?type=POST_TRADE&amp;assetClass=EQUITY&amp;fileName=NordicEquity-posttrade-2026-07-29T1457">external</a>
    <a href="/api/regulatory/trade-report/download?type=PRE_TRADE&amp;assetClass=EQUITY&amp;fileName=NordicEquity-pretrade-2026-07-29T1457">wrong type</a>
    """

    files = discover_post_trade_files(html)

    assert [item.file_name for item in files] == [
        "NordicEquity-posttrade-2026-07-29T1456",
        "NordicEquity-posttrade-2026-07-29T1455",
    ]
    assert all(item.url.startswith("https://tradereports.nasdaq.com/") for item in files)
    assert files[0].report_minute == datetime(
        2026,
        7,
        29,
        12,
        56,
        tzinfo=timezone.utc,
    )


def test_catalog_parses_validated_report_names_newest_first():
    files = parse_post_trade_catalog(
        '{"message":null,"reports":['
        '"NordicEquity-posttrade-2026-07-29T1455",'
        '"NordicEquity-posttrade-2026-07-29T1456"]}'
    )

    assert [item.file_name for item in files] == [
        "NordicEquity-posttrade-2026-07-29T1456",
        "NordicEquity-posttrade-2026-07-29T1455",
    ]


def test_catalog_rejects_partial_or_unexpected_provider_payloads():
    with pytest.raises(MarketDataError, match="schema"):
        parse_post_trade_catalog('{"reports":[]}')

    with pytest.raises(MarketDataError, match="file name"):
        parse_post_trade_catalog(
            '{"message":null,"reports":["../../unexpected.csv"]}'
        )


def test_parser_filters_xsto_and_keeps_latest_trade_per_instrument():
    received_at = datetime(2026, 7, 29, 13, 13, tzinfo=timezone.utc)
    payload = "\r\n".join(
        [
            '"sep=;"',
            HEADER,
            _row(timestamp="2026-07-29T12:55:00.000000Z", price="320.10"),
            _row(timestamp="2026-07-29T12:56:00.000000Z", price="321.20"),
            _row(
                timestamp="2026-07-29T12:57:00.000000Z",
                price="999.00",
                venue="XCSE",
            ),
        ]
    )

    quotes = parse_post_trade_csv(
        payload,
        received_at=received_at,
        instruments=[_instrument()],
        aliases=[_alias()],
    )

    assert len(quotes) == 1
    assert quotes[0].isin == "SE0000115446"
    assert quotes[0].mic == "XSTO"
    assert quotes[0].last_price == Decimal("321.20")
    assert quotes[0].currency == "SEK"
    assert _instrument().notional_currency == "EUR"
    assert quotes[0].event_time == datetime(
        2026,
        7,
        29,
        12,
        56,
        tzinfo=timezone.utc,
    )


def test_parser_rejects_unknown_schema_and_contradictory_xsto_currency():
    received_at = datetime(2026, 7, 29, 13, 13, tzinfo=timezone.utc)

    with pytest.raises(MarketDataError, match="schema"):
        parse_post_trade_csv(
            "wrong;header\r\nvalue;value",
            received_at=received_at,
            instruments=[_instrument()],
            aliases=[_alias()],
        )

    bad_currency = _row(
        timestamp="2026-07-29T12:56:00.000000Z",
        price="321.20",
    ).replace(";SEK;MONE;", ";EUR;MONE;")
    with pytest.raises(MarketDataError, match="currency"):
        parse_post_trade_csv(
            "\r\n".join(['"sep=;"', HEADER, bad_currency]),
            received_at=received_at,
            instruments=[_instrument()],
            aliases=[_alias()],
        )


class _Response:
    def __init__(self, text: str):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = 200
        self.headers = {}
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.requested_urls: list[str] = []
        self.timeouts = []
        self.allow_redirects = []
        self.streams = []
        self.response_objects = []

    def get(self, url: str, *, timeout, allow_redirects, stream) -> _Response:
        self.requested_urls.append(url)
        self.timeouts.append(timeout)
        self.allow_redirects.append(allow_redirects)
        self.streams.append(stream)
        response = _Response(self.responses[url])
        self.response_objects.append(response)
        return response


def test_provider_fetches_recent_files_and_returns_latest_configured_quote():
    instrument = _instrument()
    catalog_url = (
        "https://tradereports.nasdaq.com/api/regulatory/trade-reports"
        "?type=POST_TRADE&assetClass=EQUITY"
    )
    file_1456 = (
        "https://tradereports.nasdaq.com/api/regulatory/trade-report/download"
        "?type=POST_TRADE&assetClass=EQUITY"
        "&fileName=NordicEquity-posttrade-2026-07-29T1456"
    )
    file_1455 = file_1456.replace("T1456", "T1455")
    catalog = (
        '{"message":null,"reports":['
        '"NordicEquity-posttrade-2026-07-29T1456",'
        '"NordicEquity-posttrade-2026-07-29T1455"]}'
    )
    session = _Session(
        {
            catalog_url: catalog,
            file_1456: "\r\n".join(
                [
                    '"sep=;"',
                    HEADER,
                    _row(
                        timestamp="2026-07-29T12:56:00.000000Z",
                        price="321.20",
                    ),
                ]
            ),
            file_1455: "\r\n".join(
                [
                    '"sep=;"',
                    HEADER,
                    _row(
                        timestamp="2026-07-29T12:55:00.000000Z",
                        price="320.10",
                    ),
                ]
            ),
        }
    )
    received_at = datetime(2026, 7, 29, 13, 13, tzinfo=timezone.utc)
    provider = NasdaqDelayedPostTradeProvider(
        instruments=[instrument],
        aliases=[_alias()],
        session=session,
        clock=lambda: received_at,
        max_files=2,
    )

    quotes = provider.latest_quotes([instrument])

    assert provider.list_instruments() == (instrument,)
    assert len(quotes) == 1
    assert quotes[0].last_price == Decimal("321.20")
    assert session.requested_urls == [catalog_url, file_1456]


def test_provider_exposes_validated_file_discovery_and_download():
    instrument = _instrument()
    catalog_url = (
        "https://tradereports.nasdaq.com/api/regulatory/trade-reports"
        "?type=POST_TRADE&assetClass=EQUITY"
    )
    file_url = (
        "https://tradereports.nasdaq.com/api/regulatory/trade-report/download"
        "?type=POST_TRADE&assetClass=EQUITY"
        "&fileName=NordicEquity-posttrade-2026-07-29T1456"
    )
    payload = "\r\n".join(
        [
            '"sep=;"',
            HEADER,
            _row(
                timestamp="2026-07-29T12:56:00.000000Z",
                price="321.20",
            ),
        ]
    )
    session = _Session(
        {
            catalog_url: (
                '{"message":null,"reports":['
                '"NordicEquity-posttrade-2026-07-29T1456"]}'
            ),
            file_url: payload,
        }
    )
    provider = NasdaqDelayedPostTradeProvider(
        instruments=[instrument],
        aliases=[_alias()],
        session=session,
    )

    report_file = provider.report_files()[0]
    downloaded = provider.download_report_file(report_file)

    assert report_file.file_name.endswith("T1456")
    assert downloaded == payload.encode("utf-8")
    assert session.requested_urls == [catalog_url, file_url]
    assert session.timeouts == [(5.0, 20.0), (5.0, 20.0)]
    assert session.allow_redirects == [False, False]
    assert session.streams == [True, True]
    assert all(response.closed for response in session.response_objects)


def test_default_nasdaq_session_has_identification_and_bounded_get_retries():
    session = build_nasdaq_http_session()
    retries = session.get_adapter("https://").max_retries

    assert session.headers["User-Agent"].startswith("TradingAgent/")
    assert retries.total == 1
    assert retries.connect == 1
    assert retries.read == 1
    assert retries.allowed_methods == frozenset({"GET"})


def test_curl_transport_is_bounded_allowlisted_and_never_uses_a_shell():
    calls = []

    def runner(arguments, **options):
        calls.append((arguments, options))
        output_path = Path(
            arguments[arguments.index("--output") + 1]
        )
        output_path.write_bytes(b'{"message":null,"reports":[]}')
        return SimpleNamespace(
            returncode=0,
            stdout=b"200",
            stderr=b"",
        )

    payload = _download_with_curl(
        (
            "https://tradereports.nasdaq.com/api/regulatory/"
            "trade-reports?type=POST_TRADE&assetClass=EQUITY"
        ),
        max_bytes=2_000_000,
        timeout=(5.0, 20.0),
        runner=runner,
    )

    assert payload == b'{"message":null,"reports":[]}'
    arguments, options = calls[0]
    assert arguments[0] == "/usr/bin/curl"
    assert "--location" not in arguments
    assert options["shell"] is False
    assert options["timeout"] == 30.0


def test_curl_transport_rejects_urls_outside_the_fixed_nasdaq_host():
    invalid_urls = (
        "https://example.com/market-data.csv",
        (
            "https://tradereports.nasdaq.com/api/regulatory/trade-reports"
            "?type=PRE_TRADE&assetClass=EQUITY"
        ),
        (
            "https://tradereports.nasdaq.com/api/regulatory/"
            "trade-report/download?type=POST_TRADE&assetClass=EQUITY"
            "&fileName=../../unexpected"
        ),
    )

    for invalid_url in invalid_urls:
        with pytest.raises(MarketDataError, match="authorized HTTPS"):
            _download_with_curl(
                invalid_url,
                max_bytes=2_000_000,
                timeout=(5.0, 20.0),
                runner=lambda *_args, **_options: pytest.fail(
                    "runner must not be called"
                ),
            )
