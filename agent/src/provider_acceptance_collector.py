"""In-memory, non-trading evidence collection for provider acceptance."""

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

from src.data.market_data import (
    InstrumentUniverseSnapshot,
    MarketDataError,
)
from src.data.nasdaq_delayed import parse_post_trade_csv
from src.data.nasdaq_reference import (
    NasdaqInstrumentAlias,
    trading_currencies_by_isin,
)
from src.provider_contract_admin import ProviderAcceptanceSession


_STOCKHOLM = ZoneInfo("Europe/Stockholm")
_FILE_PATTERN = re.compile(
    r"^NordicEquity-posttrade-"
    r"(?P<minute>\d{4}-\d{2}-\d{2}T\d{4})$"
)
_MAX_SAMPLE_BYTES = 20_000_000


@dataclass(frozen=True)
class AcceptanceSampleFile:
    """One bounded local sample; no network, database, or broker capability."""

    file_name: str
    received_at: datetime
    content: bytes

    def __post_init__(self) -> None:
        if (
            not isinstance(self.file_name, str)
            or _FILE_PATTERN.fullmatch(self.file_name) is None
        ):
            raise MarketDataError(
                "acceptance sample file name is invalid"
            )
        if (
            not isinstance(self.received_at, datetime)
            or self.received_at.tzinfo is None
            or self.received_at.utcoffset() is None
        ):
            raise MarketDataError(
                "acceptance sample received_at must be timezone-aware"
            )
        if (
            not isinstance(self.content, bytes)
            or not self.content
            or len(self.content) > _MAX_SAMPLE_BYTES
        ):
            raise MarketDataError(
                "acceptance sample content is invalid"
            )

    @property
    def report_minute(self) -> datetime:
        match = _FILE_PATTERN.fullmatch(self.file_name)
        assert match is not None
        local_minute = datetime.strptime(
            match.group("minute"),
            "%Y-%m-%dT%H%M",
        )
        aware = local_minute.replace(tzinfo=_STOCKHOLM)
        if (
            aware.replace(fold=0).utcoffset()
            != aware.replace(fold=1).utcoffset()
        ):
            raise MarketDataError(
                "acceptance sample minute is ambiguous"
            )
        return aware


def collect_acceptance_session(
    *,
    reference_snapshot: InstrumentUniverseSnapshot,
    aliases: tuple[NasdaqInstrumentAlias, ...],
    product_covered_isins: tuple[str, ...],
    samples: tuple[AcceptanceSampleFile, ...],
) -> ProviderAcceptanceSession:
    """Create aggregate acceptance evidence without storing price data."""
    if not isinstance(reference_snapshot, InstrumentUniverseSnapshot):
        raise MarketDataError(
            "acceptance requires a frozen reference snapshot"
        )
    trading_currencies = trading_currencies_by_isin(
        instruments=reference_snapshot.instruments,
        aliases=aliases,
    )

    expected_isins = reference_snapshot.instrument_isins
    normalized_coverage = tuple(
        sorted(
            isin.strip().upper()
            for isin in product_covered_isins
            if isinstance(isin, str) and isin.strip()
        )
    )
    if (
        normalized_coverage != expected_isins
        or len(normalized_coverage) != len(set(normalized_coverage))
    ):
        raise MarketDataError(
            "acceptance product coverage is not exact"
        )

    normalized_samples = tuple(samples)
    if not normalized_samples or any(
        not isinstance(sample, AcceptanceSampleFile)
        for sample in normalized_samples
    ):
        raise MarketDataError(
            "acceptance requires bounded sample files"
        )
    file_names = tuple(sample.file_name for sample in normalized_samples)
    if file_names != tuple(sorted(set(file_names))):
        raise MarketDataError(
            "acceptance sample files must be unique and ordered"
        )
    session_dates = {
        sample.report_minute.date()
        for sample in normalized_samples
    }
    if len(session_dates) != 1:
        raise MarketDataError(
            "acceptance samples must belong to one XSTO session"
        )

    quote_count = 0
    maximum_delivery_seconds = 0
    file_evidence = []
    for sample in normalized_samples:
        quotes = parse_post_trade_csv(
            sample.content,
            received_at=sample.received_at,
            instruments=reference_snapshot.instruments,
            aliases=aliases,
        )
        quote_count += len(quotes)
        file_maximum = 0
        for quote in quotes:
            delay_seconds = math.ceil(
                (quote.received_at - quote.event_time).total_seconds()
            )
            if delay_seconds <= 0:
                raise MarketDataError(
                    "acceptance delivery delay is invalid"
                )
            file_maximum = max(file_maximum, delay_seconds)
            maximum_delivery_seconds = max(
                maximum_delivery_seconds,
                delay_seconds,
            )
        file_evidence.append(
            {
                "file_name": sample.file_name,
                "received_at": sample.received_at.isoformat(),
                "content_checksum_sha256": hashlib.sha256(
                    sample.content
                ).hexdigest(),
                "quote_count": len(quotes),
                "max_observed_delivery_seconds": file_maximum,
            }
        )
    if quote_count <= 0 or maximum_delivery_seconds <= 0:
        raise MarketDataError(
            "acceptance samples contain no usable XSTO quotes"
        )

    evidence = {
        "schema_version": 1,
        "session_date": next(iter(session_dates)).isoformat(),
        "reference_checksum_sha256": reference_snapshot.checksum_sha256,
        "instrument_isins": list(expected_isins),
        "trading_currencies": [
            {"isin": isin, "currency": trading_currencies[isin]}
            for isin in sorted(trading_currencies)
        ],
        "files": file_evidence,
    }
    canonical = json.dumps(
        evidence,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return ProviderAcceptanceSession(
        session_date=next(iter(session_dates)),
        expected_instruments=len(expected_isins),
        product_covered_instruments=len(normalized_coverage),
        symbol_mapped_instruments=len(trading_currencies),
        sample_file_count=len(normalized_samples),
        sample_quote_count=quote_count,
        max_observed_delivery_seconds=maximum_delivery_seconds,
        evidence_checksum_sha256=hashlib.sha256(canonical).hexdigest(),
    )
