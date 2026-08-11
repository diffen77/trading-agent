"""Validated public Nasdaq sector classifications for XSTO companies."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .market_data import MarketDataError


_ENDPOINT = "https://api.nasdaq.com/api/nordic/screener/shares"
_SOURCE = "nasdaq-public-screener-xsto"
_TIMEOUT = (5.0, 30.0)
_MAX_RESPONSE_BYTES = 5_000_000
_CHUNK_BYTES = 64 * 1024
_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_SECTORS = frozenset(
    {
        "Basic Materials",
        "Consumer Discretionary",
        "Consumer Staples",
        "Energy",
        "Financials",
        "Health Care",
        "Industrials",
        "Real Estate",
        "Technology",
        "Telecommunications",
        "Utilities",
    }
)


@dataclass(frozen=True)
class SectorClassification:
    isin: str
    sector: str
    company_name: str
    symbol: str


@dataclass(frozen=True)
class SectorSyncResult:
    active_instruments: int
    matched_instruments: int
    updated_companies: int
    coverage: float
    verified_at: datetime


class NasdaqSectorClient:
    """Fetch one bounded, validated Stockholm sector snapshot."""

    def __init__(self, *, session: requests.Session | None = None):
        self._session = session or build_nasdaq_sector_session()

    def fetch(self) -> dict[str, SectorClassification]:
        response = self._session.get(
            _ENDPOINT,
            params={
                "category": "MAIN_MARKET",
                "tableonly": "false",
                "market": "STO",
            },
            timeout=_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        try:
            response.raise_for_status()
            payload = _bounded_response(response)
        except requests.RequestException as exc:
            raise MarketDataError("Nasdaq sector request failed") from exc
        finally:
            response.close()
        return parse_nasdaq_sector_payload(payload)


class NasdaqSectorSyncService:
    """Apply classifications only when exact-ISIN coverage is sufficient."""

    def __init__(
        self,
        *,
        database,
        client: NasdaqSectorClient | None = None,
        clock: Callable[[], datetime] | None = None,
        minimum_coverage: float = 0.95,
    ):
        if not 0 < minimum_coverage <= 1:
            raise ValueError("minimum_coverage must be between zero and one")
        self._database = database
        self._client = client or NasdaqSectorClient()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._minimum_coverage = minimum_coverage

    def sync(self) -> SectorSyncResult:
        verified_at = self._clock()
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            raise MarketDataError("sector sync clock must be timezone-aware")
        active_isins = tuple(self._database.get_active_xsto_company_isins())
        if not active_isins:
            raise MarketDataError("active XSTO company universe is empty")
        classifications = self._client.fetch()
        matched = {
            isin: classifications[isin]
            for isin in active_isins
            if isin in classifications
        }
        coverage = len(matched) / len(active_isins)
        if coverage < self._minimum_coverage:
            raise MarketDataError(
                "Nasdaq sector coverage is below the required threshold"
            )
        updated = self._database.update_company_sectors(
            matched,
            source=_SOURCE,
            verified_at=verified_at,
        )
        return SectorSyncResult(
            active_instruments=len(active_isins),
            matched_instruments=len(matched),
            updated_companies=updated,
            coverage=coverage,
            verified_at=verified_at,
        )


def parse_nasdaq_sector_payload(
    payload: bytes,
) -> dict[str, SectorClassification]:
    try:
        document = json.loads(payload)
        if document["status"]["rCode"] != 200:
            raise KeyError("provider status")
        rows = document["data"]["instrumentListing"]["rows"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise MarketDataError("Nasdaq sector payload schema is invalid") from exc
    if not isinstance(rows, list) or len(rows) > 2000:
        raise MarketDataError("Nasdaq sector payload rows are invalid")

    result: dict[str, SectorClassification] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise MarketDataError("Nasdaq sector row is invalid")
        isin = _required_text(row.get("isin"), "ISIN", 12).upper()
        sector = _required_text(row.get("sector"), "sector", 50)
        company_name = _required_text(
            row.get("fullName"), "company name", 300
        )
        symbol = _required_text(row.get("symbol"), "symbol", 50)
        if not _ISIN_PATTERN.fullmatch(isin):
            raise MarketDataError("Nasdaq sector ISIN is invalid")
        if sector not in _SECTORS:
            raise MarketDataError("Nasdaq sector is unsupported")
        classification = SectorClassification(
            isin=isin,
            sector=sector,
            company_name=company_name,
            symbol=symbol,
        )
        previous = result.get(isin)
        if previous is not None and previous != classification:
            raise MarketDataError("Nasdaq sector ISIN is duplicated")
        result[isin] = classification
    return result


def build_nasdaq_sector_session() -> requests.Session:
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        status=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        backoff_factor=0.5,
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "TradingAgent/sector-sync-1.0",
        }
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _bounded_response(response) -> bytes:
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            if int(length) > _MAX_RESPONSE_BYTES:
                raise MarketDataError("Nasdaq sector response is too large")
        except ValueError as exc:
            raise MarketDataError(
                "Nasdaq sector Content-Length is invalid"
            ) from exc
    chunks = []
    total = 0
    for chunk in response.iter_content(_CHUNK_BYTES):
        if not chunk:
            continue
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            raise MarketDataError("Nasdaq sector response is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _required_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise MarketDataError(f"Nasdaq sector {field} is required")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or any(
        ord(char) < 32 for char in cleaned
    ):
        raise MarketDataError(f"Nasdaq sector {field} is invalid")
    return cleaned
