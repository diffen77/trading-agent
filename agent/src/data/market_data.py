"""Provider-neutral contracts for instruments, sessions, and quotes."""

from dataclasses import InitVar, dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
from io import BytesIO
import json
import re
from typing import Protocol, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_MIC_PATTERN = re.compile(r"^[A-Z0-9]{4}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9 .-]{0,31}$")
_CONTRACT_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,99}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_RAW_ARCHIVE_BYTES = 20_000_000
_MAX_COMPRESSED_ARCHIVE_BYTES = 21_000_000
_MAX_TOP_OF_BOOK_INPUT_ROWS = 5_000_000
_TOP_OF_BOOK_PARSER_PROOF = object()


class MarketDataError(ValueError):
    """Raised when reference or market data is unsafe to consume."""


@dataclass(frozen=True)
class FreshnessPolicy:
    max_delay: timedelta
    tolerance: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        if self.max_delay < timedelta(0):
            raise MarketDataError("max_delay cannot be negative")
        if self.tolerance < timedelta(0):
            raise MarketDataError("tolerance cannot be negative")


@dataclass(frozen=True)
class InstrumentRecord:
    isin: str
    symbol: str | None
    name: str
    mic: str
    currency: str
    instrument_type: str
    source: str
    primary_listing: bool = True
    status: str = "ACTIVE"
    first_trade_date: date | None = None
    last_trade_date: date | None = None
    cfi_code: str | None = None

    def __post_init__(self) -> None:
        isin = _normalized_text(self.isin, "isin").upper()
        symbol = None
        if self.symbol is not None:
            symbol = _normalized_text(self.symbol, "symbol").upper()
        name = _normalized_text(self.name, "name")
        mic = _normalized_text(self.mic, "mic").upper()
        currency = _normalized_text(self.currency, "currency").upper()
        instrument_type = _normalized_text(
            self.instrument_type,
            "instrument_type",
        ).upper()
        source = _normalized_text(self.source, "source")
        status = _normalized_text(self.status, "status").upper()
        cfi_code = None
        if self.cfi_code is not None:
            cfi_code = _normalized_text(self.cfi_code, "cfi_code").upper()

        if not _ISIN_PATTERN.fullmatch(isin) or not _valid_isin(isin):
            raise MarketDataError("isin is invalid")
        if symbol is not None and not _SYMBOL_PATTERN.fullmatch(symbol):
            raise MarketDataError("symbol has invalid format")
        if len(name) > 255:
            raise MarketDataError("name exceeds 255 characters")
        if not _MIC_PATTERN.fullmatch(mic):
            raise MarketDataError("mic must be a four-character MIC")
        if not _CURRENCY_PATTERN.fullmatch(currency):
            raise MarketDataError("currency must be a three-character code")
        if not isinstance(self.primary_listing, bool):
            raise MarketDataError("primary_listing must be boolean")
        if status not in {"ACTIVE", "INACTIVE", "DELISTED", "SUSPENDED"}:
            raise MarketDataError("status is invalid")
        if self.first_trade_date is not None and not isinstance(
            self.first_trade_date,
            date,
        ):
            raise MarketDataError("first_trade_date must be a date")
        if self.last_trade_date is not None and not isinstance(
            self.last_trade_date,
            date,
        ):
            raise MarketDataError("last_trade_date must be a date")
        if (
            self.first_trade_date is not None
            and self.last_trade_date is not None
            and self.last_trade_date < self.first_trade_date
        ):
            raise MarketDataError(
                "last_trade_date cannot precede first_trade_date"
            )
        if cfi_code is not None and not re.fullmatch(
            r"[A-Z0-9]{6}",
            cfi_code,
        ):
            raise MarketDataError("cfi_code must contain six characters")

        object.__setattr__(self, "isin", isin)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "mic", mic)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "instrument_type", instrument_type)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "cfi_code", cfi_code)


    @property
    def notional_currency(self) -> str:
        """Return the FIRDS NtnlCcy value, never a quote currency."""
        return self.currency


@dataclass(frozen=True)
class InstrumentUniverseSnapshot:
    """Canonical active primary-equity membership for one MIC."""

    mic: str
    instruments: tuple[InstrumentRecord, ...]

    def __post_init__(self) -> None:
        mic = _normalized_text(self.mic, "mic").upper()
        if not _MIC_PATTERN.fullmatch(mic):
            raise MarketDataError("mic must be a four-character MIC")
        if not isinstance(self.instruments, tuple) or not self.instruments:
            raise MarketDataError(
                "instrument universe snapshot requires instruments"
            )
        instruments = []
        seen_isins: set[str] = set()
        for instrument in self.instruments:
            if not isinstance(instrument, InstrumentRecord):
                raise MarketDataError(
                    "instrument universe must contain InstrumentRecord values"
                )
            if instrument.mic != mic:
                raise MarketDataError(
                    "instrument universe contains the wrong MIC"
                )
            if instrument.status != "ACTIVE":
                raise MarketDataError(
                    "instrument universe requires active instruments"
                )
            if not instrument.primary_listing:
                raise MarketDataError(
                    "instrument universe requires primary listings"
                )
            if instrument.instrument_type not in {
                "COMMON_STOCK",
                "PREFERRED_STOCK",
                "DEPOSITARY_RECEIPT",
            }:
                raise MarketDataError(
                    "instrument universe contains an unsupported equity type"
                )
            if instrument.isin in seen_isins:
                raise MarketDataError(
                    "instrument universe has duplicate XSTO instrument"
                )
            seen_isins.add(instrument.isin)
            instruments.append(instrument)

        object.__setattr__(self, "mic", mic)
        object.__setattr__(
            self,
            "instruments",
            tuple(sorted(instruments, key=lambda item: item.isin)),
        )

    @property
    def instrument_isins(self) -> tuple[str, ...]:
        return tuple(instrument.isin for instrument in self.instruments)

    @property
    def checksum_sha256(self) -> str:
        canonical = {
            "schema_version": 1,
            "mic": self.mic,
            "instruments": [
                {
                    "isin": instrument.isin,
                    "symbol": instrument.symbol,
                    "name": instrument.name,
                    "currency": instrument.currency,
                    "instrument_type": instrument.instrument_type,
                    "source": instrument.source,
                    "primary_listing": instrument.primary_listing,
                    "status": instrument.status,
                    "first_trade_date": (
                        instrument.first_trade_date.isoformat()
                        if instrument.first_trade_date is not None
                        else None
                    ),
                    "last_trade_date": (
                        instrument.last_trade_date.isoformat()
                        if instrument.last_trade_date is not None
                        else None
                    ),
                    "cfi_code": instrument.cfi_code,
                }
                for instrument in self.instruments
            ],
        }
        payload = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class QuoteRecord:
    isin: str
    mic: str
    event_time: datetime
    received_at: datetime
    source: str
    last_price: Decimal
    currency: str
    covered_through: datetime | None = None
    volume: Decimal | None = None
    quote_id: int | None = None
    book_state_id: int | None = None

    def __post_init__(self) -> None:
        isin = _normalized_text(self.isin, "isin").upper()
        mic = _normalized_text(self.mic, "mic").upper()
        source = _normalized_text(self.source, "source")
        currency = _normalized_text(self.currency, "currency").upper()

        if not _ISIN_PATTERN.fullmatch(isin) or not _valid_isin(isin):
            raise MarketDataError("isin is invalid")
        if not _MIC_PATTERN.fullmatch(mic):
            raise MarketDataError("mic must be a four-character MIC")
        if not _CURRENCY_PATTERN.fullmatch(currency):
            raise MarketDataError("currency must be a three-character code")

        _require_aware(self.event_time, "event_time")
        _require_aware(self.received_at, "received_at")
        if self.event_time > self.received_at:
            raise MarketDataError("event_time cannot be after received_at")
        if self.covered_through is not None:
            _require_aware(self.covered_through, "covered_through")
            if self.covered_through < self.event_time:
                raise MarketDataError(
                    "covered_through cannot be before event_time"
                )
            if self.covered_through > self.received_at:
                raise MarketDataError(
                    "covered_through cannot be after received_at"
                )

        price = _positive_decimal(self.last_price, "last_price")
        volume = None
        if self.volume is not None:
            volume = _nonnegative_decimal(self.volume, "volume")
        if self.quote_id is not None and (
            isinstance(self.quote_id, bool)
            or not isinstance(self.quote_id, int)
            or self.quote_id <= 0
        ):
            raise MarketDataError("quote_id must be a positive integer")
        if self.book_state_id is not None and (
            isinstance(self.book_state_id, bool)
            or not isinstance(self.book_state_id, int)
            or self.book_state_id <= 0
        ):
            raise MarketDataError(
                "book_state_id must be a positive integer"
            )
        if self.quote_id is not None and self.book_state_id is not None:
            raise MarketDataError(
                "quote must use at most one persisted price evidence id"
            )

        object.__setattr__(self, "isin", isin)
        object.__setattr__(self, "mic", mic)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "last_price", price)
        object.__setattr__(self, "volume", volume)

    @property
    def observed_delay(self) -> timedelta:
        return self.received_at - self.event_time


@dataclass(frozen=True)
class TopOfBookUpdate:
    """One ordered Level 1 side update from an authorized venue feed."""

    isin: str
    mic: str
    event_time: datetime
    publication_time: datetime
    received_at: datetime
    source: str
    side: str
    currency: str
    price: Decimal | None
    quantity: Decimal | None
    order_count: int | None
    source_sequence: int
    trading_system: str
    trading_phase: str

    def __post_init__(self) -> None:
        isin = _normalized_text(self.isin, "isin").upper()
        mic = _normalized_text(self.mic, "mic").upper()
        source = _normalized_text(self.source, "source")
        side = _normalized_text(self.side, "side").upper()
        currency = _normalized_text(self.currency, "currency").upper()
        trading_system = _normalized_text(
            self.trading_system,
            "trading_system",
        ).upper()
        trading_phase = _normalized_text(
            self.trading_phase,
            "trading_phase",
        ).upper()

        if not _ISIN_PATTERN.fullmatch(isin) or not _valid_isin(isin):
            raise MarketDataError("isin is invalid")
        if not _MIC_PATTERN.fullmatch(mic):
            raise MarketDataError("mic must be a four-character MIC")
        if not _CURRENCY_PATTERN.fullmatch(currency):
            raise MarketDataError("currency must be a three-character code")
        if side not in {"BUY", "SELL"}:
            raise MarketDataError("side must be BUY or SELL")
        if (
            isinstance(self.source_sequence, bool)
            or not isinstance(self.source_sequence, int)
            or self.source_sequence <= 0
        ):
            raise MarketDataError(
                "source_sequence must be a positive integer"
            )
        if not re.fullmatch(r"[A-Z0-9_-]{1,20}", trading_system):
            raise MarketDataError("trading_system has invalid format")
        if not re.fullmatch(r"[A-Z0-9_-]{1,20}", trading_phase):
            raise MarketDataError("trading_phase has invalid format")

        _require_aware(self.event_time, "event_time")
        _require_aware(self.publication_time, "publication_time")
        _require_aware(self.received_at, "received_at")
        if self.publication_time < self.event_time:
            raise MarketDataError(
                "publication_time cannot precede event_time"
            )
        if self.publication_time > self.received_at:
            raise MarketDataError(
                "publication_time cannot be after received_at"
            )

        values_present = (
            self.price is not None,
            self.quantity is not None,
            self.order_count is not None,
        )
        if any(values_present) and not all(values_present):
            raise MarketDataError(
                "price, quantity and order_count must be all present or all empty"
            )
        price = None
        quantity = None
        order_count = None
        if all(values_present):
            price = _positive_decimal(self.price, "price")
            quantity = _positive_decimal(self.quantity, "quantity")
            if (
                isinstance(self.order_count, bool)
                or not isinstance(self.order_count, int)
                or self.order_count <= 0
            ):
                raise MarketDataError(
                    "order_count must be a positive integer"
                )
            order_count = self.order_count

        object.__setattr__(self, "isin", isin)
        object.__setattr__(self, "mic", mic)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "order_count", order_count)
        object.__setattr__(self, "trading_system", trading_system)
        object.__setattr__(self, "trading_phase", trading_phase)

    @property
    def is_delete(self) -> bool:
        return self.price is None

    @property
    def observed_delay(self) -> timedelta:
        return self.received_at - self.publication_time


@dataclass(frozen=True)
class TopOfBookBatch:
    """One parser-verified, gap-detectable Level 1 source-file minute."""

    provider: str
    data_type: str
    source: str
    mic: str
    file_name: str
    source_url: str
    source_checksum_sha256: str
    reference_checksum_sha256: str
    instrument_isins: tuple[str, ...]
    report_minute: datetime
    received_at: datetime
    input_row_count: int
    updates: tuple[TopOfBookUpdate, ...]
    _parser_proof: InitVar[object] = None

    def __post_init__(self, _parser_proof: object) -> None:
        if _parser_proof is not _TOP_OF_BOOK_PARSER_PROOF:
            raise MarketDataError(
                "TopOfBookBatch must be created by a verified parser"
            )
        provider = _normalized_text(self.provider, "provider")
        data_type = _normalized_text(self.data_type, "data_type")
        source = _normalized_text(self.source, "source")
        mic = _normalized_text(self.mic, "mic").upper()
        file_name = _normalized_text(self.file_name, "file_name")
        source_url = _normalized_text(self.source_url, "source_url")
        checksum = _normalized_text(
            self.source_checksum_sha256,
            "source_checksum_sha256",
        ).lower()
        reference_checksum = _normalized_text(
            self.reference_checksum_sha256,
            "reference_checksum_sha256",
        ).lower()

        if not _MIC_PATTERN.fullmatch(mic):
            raise MarketDataError("mic must be a four-character MIC")
        if "/" in file_name or "\\" in file_name or file_name in {".", ".."}:
            raise MarketDataError("file_name must not contain a path")
        parsed_url = urlparse(source_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            raise MarketDataError("source_url must be an absolute HTTPS URL")
        if not _SHA256_PATTERN.fullmatch(checksum):
            raise MarketDataError(
                "source_checksum_sha256 must be lowercase SHA-256"
            )
        if not _SHA256_PATTERN.fullmatch(reference_checksum):
            raise MarketDataError(
                "reference_checksum_sha256 must be lowercase SHA-256"
            )
        if not isinstance(self.instrument_isins, tuple):
            raise MarketDataError("instrument_isins must be a tuple")
        instrument_isins = tuple(
            _normalized_text(isin, "instrument_isin").upper()
            for isin in self.instrument_isins
        )
        if not instrument_isins:
            raise MarketDataError(
                "instrument_isins must contain the active MIC universe"
            )
        if instrument_isins != tuple(sorted(set(instrument_isins))):
            raise MarketDataError(
                "instrument_isins must be sorted and unique"
            )
        if any(
            not _ISIN_PATTERN.fullmatch(isin) or not _valid_isin(isin)
            for isin in instrument_isins
        ):
            raise MarketDataError("instrument_isins contains an invalid ISIN")

        _require_aware(self.report_minute, "report_minute")
        _require_aware(self.received_at, "received_at")
        if (
            self.report_minute.second != 0
            or self.report_minute.microsecond != 0
        ):
            raise MarketDataError("report_minute must be minute-aligned")
        if self.covered_through > self.received_at:
            raise MarketDataError(
                "covered file minute cannot be after received_at"
            )
        if (
            isinstance(self.input_row_count, bool)
            or not isinstance(self.input_row_count, int)
            or not 1 <= self.input_row_count <= _MAX_TOP_OF_BOOK_INPUT_ROWS
        ):
            raise MarketDataError(
                "input_row_count is outside the safe file limit"
            )
        if not isinstance(self.updates, tuple):
            raise MarketDataError("updates must be a tuple")
        if len(self.updates) > self.input_row_count:
            raise MarketDataError(
                "updates cannot exceed source input rows"
            )

        prior_sequence = 0
        for update in self.updates:
            if not isinstance(update, TopOfBookUpdate):
                raise MarketDataError(
                    "updates must contain TopOfBookUpdate values"
                )
            if update.source_sequence <= prior_sequence:
                raise MarketDataError(
                    "updates are not in increasing source sequence"
                )
            prior_sequence = update.source_sequence
            if (
                update.source != source
                or update.mic != mic
                or update.received_at != self.received_at
            ):
                raise MarketDataError(
                    "update identity does not match source-file batch"
                )
            if update.isin not in instrument_isins:
                raise MarketDataError(
                    "update is outside the batch reference universe"
                )
            if not (
                self.report_minute
                <= update.event_time
                < self.covered_through
                and self.report_minute
                <= update.publication_time
                < self.covered_through
            ):
                raise MarketDataError(
                    "update timestamp is outside source file minute"
                )

        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "data_type", data_type)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "mic", mic)
        object.__setattr__(self, "file_name", file_name)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "source_checksum_sha256", checksum)
        object.__setattr__(
            self,
            "reference_checksum_sha256",
            reference_checksum,
        )
        object.__setattr__(self, "instrument_isins", instrument_isins)

    @property
    def covered_through(self) -> datetime:
        return self.report_minute + timedelta(minutes=1)

    @property
    def observed_delay(self) -> timedelta:
        return self.received_at - self.covered_through

@dataclass(frozen=True)
class IndexLevelRecord:
    contract_key: str
    symbol: str
    mic: str
    event_time: datetime
    received_at: datetime
    source: str
    level: Decimal
    source_checksum_sha256: str
    level_id: int | None = None

    def __post_init__(self) -> None:
        contract_key = _normalized_text(
            self.contract_key,
            "contract_key",
        ).lower()
        symbol = _normalized_text(self.symbol, "symbol").upper()
        mic = _normalized_text(self.mic, "mic").upper()
        source = _normalized_text(self.source, "source")
        checksum = _normalized_text(
            self.source_checksum_sha256,
            "source_checksum_sha256",
        )

        if not _CONTRACT_KEY_PATTERN.fullmatch(contract_key):
            raise MarketDataError("contract_key has invalid format")
        if not _SYMBOL_PATTERN.fullmatch(symbol):
            raise MarketDataError("symbol has invalid format")
        if not _MIC_PATTERN.fullmatch(mic):
            raise MarketDataError("mic must be a four-character MIC")
        if not _SHA256_PATTERN.fullmatch(checksum):
            raise MarketDataError(
                "source_checksum_sha256 must be lowercase SHA-256"
            )
        _require_aware(self.event_time, "event_time")
        _require_aware(self.received_at, "received_at")
        if self.event_time > self.received_at:
            raise MarketDataError("event_time cannot be after received_at")
        level = _positive_decimal(self.level, "level")
        if self.level_id is not None and (
            isinstance(self.level_id, bool)
            or not isinstance(self.level_id, int)
            or self.level_id <= 0
        ):
            raise MarketDataError("level_id must be a positive integer")

        object.__setattr__(self, "contract_key", contract_key)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "mic", mic)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "level", level)
        object.__setattr__(
            self,
            "source_checksum_sha256",
            checksum,
        )

    @property
    def observed_delay(self) -> timedelta:
        return self.received_at - self.event_time


@dataclass(frozen=True)
class MarketSession:
    mic: str
    session_date: date
    opens_at: datetime
    closes_at: datetime
    timezone_name: str
    source: str
    session_type: str = "REGULAR"
    status: str = "OPEN"

    def __post_init__(self) -> None:
        mic = _normalized_mic(self.mic)
        source = _normalized_text(self.source, "source")
        session_type = _normalized_text(
            self.session_type,
            "session_type",
        ).upper()
        status = _normalized_text(self.status, "status").upper()
        if not isinstance(self.session_date, date):
            raise MarketDataError("session_date must be a date")
        _require_aware(self.opens_at, "opens_at")
        _require_aware(self.closes_at, "closes_at")
        if self.closes_at <= self.opens_at:
            raise MarketDataError("closes_at must be after opens_at")
        if self.opens_at.date() != self.session_date:
            raise MarketDataError("opens_at must match session_date")
        try:
            ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise MarketDataError("unknown market timezone") from exc
        if session_type not in {"REGULAR", "HALF_DAY"}:
            raise MarketDataError("session_type is invalid")
        if status not in {"OPEN", "HALF_DAY"}:
            raise MarketDataError("market session status is invalid")
        if (
            (session_type == "HALF_DAY")
            != (status == "HALF_DAY")
        ):
            raise MarketDataError(
                "half-day session type and status must agree"
            )

        object.__setattr__(self, "mic", mic)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "session_type", session_type)
        object.__setattr__(self, "status", status)

    @classmethod
    def for_local_date(
        cls,
        *,
        mic: str,
        session_date: date,
        opens_at: str,
        closes_at: str,
        timezone_name: str,
        source: str,
        session_type: str = "REGULAR",
        status: str = "OPEN",
    ) -> "MarketSession":
        if not isinstance(session_date, date):
            raise MarketDataError("session_date must be a date")
        if session_date.weekday() >= 5:
            raise MarketDataError("a regular market session cannot be on a weekend")

        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise MarketDataError("unknown market timezone") from exc

        open_time = _parse_local_time(opens_at, "opens_at")
        close_time = _parse_local_time(closes_at, "closes_at")
        open_datetime = datetime.combine(session_date, open_time, zone)
        close_datetime = datetime.combine(session_date, close_time, zone)
        if close_datetime <= open_datetime:
            raise MarketDataError("closes_at must be after opens_at")

        return cls(
            mic=_normalized_mic(mic),
            session_date=session_date,
            opens_at=open_datetime,
            closes_at=close_datetime,
            timezone_name=timezone_name,
            source=_normalized_text(source, "source"),
            session_type=session_type,
            status=status,
        )

    def is_open(self, moment: datetime) -> bool:
        _require_aware(moment, "moment")
        return (
            self.status in {"OPEN", "HALF_DAY"}
            and self.opens_at <= moment < self.closes_at
        )


@dataclass(frozen=True)
class MarketCalendarSnapshot:
    mic: str
    year: int
    source: str
    source_url: str
    verified_at: datetime
    sessions: tuple[MarketSession, ...]
    closed_dates: tuple[date, ...]

    def __post_init__(self) -> None:
        mic = _normalized_mic(self.mic)
        source = _normalized_text(self.source, "source")
        source_url = _normalized_text(self.source_url, "source_url")
        if (
            not isinstance(self.year, int)
            or isinstance(self.year, bool)
            or not 2000 <= self.year <= 2100
        ):
            raise MarketDataError("calendar year is invalid")
        _require_aware(self.verified_at, "verified_at")
        parsed_url = urlparse(source_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            raise MarketDataError(
                "calendar source_url must be absolute HTTPS"
            )
        if not isinstance(self.sessions, tuple):
            raise MarketDataError("calendar sessions must be a tuple")
        if not isinstance(self.closed_dates, tuple):
            raise MarketDataError("closed_dates must be a tuple")

        session_dates = [item.session_date for item in self.sessions]
        if len(set(session_dates)) != len(session_dates):
            raise MarketDataError("calendar sessions contain duplicates")
        if len(set(self.closed_dates)) != len(self.closed_dates):
            raise MarketDataError("closed_dates contain duplicates")
        if set(session_dates) & set(self.closed_dates):
            raise MarketDataError(
                "calendar date cannot be both open and closed"
            )
        for session in self.sessions:
            if (
                session.mic != mic
                or session.session_date.year != self.year
                or session.source != source
                or session.timezone_name != "Europe/Stockholm"
            ):
                raise MarketDataError(
                    "market session does not match calendar snapshot"
                )
        for closed_date in self.closed_dates:
            if (
                not isinstance(closed_date, date)
                or closed_date.year != self.year
                or closed_date.weekday() >= 5
            ):
                raise MarketDataError("closed market date is invalid")

        expected_weekdays = {
            date.fromordinal(day)
            for day in range(
                date(self.year, 1, 1).toordinal(),
                date(self.year + 1, 1, 1).toordinal(),
            )
            if date.fromordinal(day).weekday() < 5
        }
        if set(session_dates) | set(self.closed_dates) != expected_weekdays:
            raise MarketDataError(
                "calendar must classify every weekday exactly once"
            )
        object.__setattr__(self, "mic", mic)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(
            self,
            "sessions",
            tuple(
                sorted(
                    self.sessions,
                    key=lambda item: item.session_date,
                )
            ),
        )
        object.__setattr__(
            self,
            "closed_dates",
            tuple(sorted(self.closed_dates)),
        )

    @property
    def checksum_sha256(self) -> str:
        payload = {
            "mic": self.mic,
            "year": self.year,
            "source": self.source,
            "source_url": self.source_url,
            "sessions": [
                {
                    "date": item.session_date.isoformat(),
                    "opens_at": item.opens_at.isoformat(),
                    "closes_at": item.closes_at.isoformat(),
                    "session_type": item.session_type,
                    "status": item.status,
                }
                for item in self.sessions
            ],
            "closed_dates": [
                item.isoformat() for item in self.closed_dates
            ],
        }
        canonical = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


class MarketDataProvider(Protocol):
    """Minimal interface implemented by any licensed data provider."""

    def list_instruments(self) -> Sequence[InstrumentRecord]:
        """Return the provider's point-in-time instrument universe."""

    def latest_quotes(
        self,
        instruments: Sequence[InstrumentRecord],
    ) -> Sequence[QuoteRecord]:
        """Return latest quotes with exchange and receipt timestamps."""


@dataclass(frozen=True)
class MarketDataArchive:
    provider: str
    data_type: str
    file_name: str
    report_minute: datetime
    received_at: datetime
    source_url: str
    checksum_sha256: str
    content_encoding: str
    compressed_content: bytes
    uncompressed_bytes: int
    upstream_checksum_algorithm: str | None = None
    upstream_checksum: str | None = None
    provider_contract_key: str | None = None

    def __post_init__(self) -> None:
        provider = _normalized_text(self.provider, "provider")
        data_type = _normalized_text(self.data_type, "data_type")
        file_name = _normalized_text(self.file_name, "file_name")
        source_url = _normalized_text(self.source_url, "source_url")
        _require_aware(self.report_minute, "report_minute")
        _require_aware(self.received_at, "received_at")
        if self.report_minute > self.received_at:
            raise MarketDataError("report_minute cannot be after received_at")
        if "/" in file_name or "\\" in file_name or file_name in {".", ".."}:
            raise MarketDataError("file_name must not contain a path")
        parsed_url = urlparse(source_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            raise MarketDataError("source_url must be an absolute HTTPS URL")
        if not re.fullmatch(r"[0-9a-f]{64}", self.checksum_sha256):
            raise MarketDataError("checksum_sha256 must be lowercase SHA-256")
        if self.content_encoding != "gzip":
            raise MarketDataError("content_encoding must be gzip")
        if (
            not isinstance(self.compressed_content, bytes)
            or not self.compressed_content
        ):
            raise MarketDataError("compressed_content must be non-empty bytes")
        if (
            not isinstance(self.uncompressed_bytes, int)
            or isinstance(self.uncompressed_bytes, bool)
            or self.uncompressed_bytes <= 0
        ):
            raise MarketDataError("uncompressed_bytes must be greater than 0")
        if (
            self.uncompressed_bytes > _MAX_RAW_ARCHIVE_BYTES
            or len(self.compressed_content) > _MAX_COMPRESSED_ARCHIVE_BYTES
        ):
            raise MarketDataError("market data archive exceeds size limit")
        try:
            with gzip.GzipFile(
                fileobj=BytesIO(self.compressed_content),
                mode="rb",
            ) as compressed:
                raw_content = compressed.read(_MAX_RAW_ARCHIVE_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise MarketDataError("compressed_content is not valid gzip") from exc
        if len(raw_content) > _MAX_RAW_ARCHIVE_BYTES:
            raise MarketDataError("market data archive exceeds size limit")
        if len(raw_content) != self.uncompressed_bytes:
            raise MarketDataError("uncompressed size does not match archive")
        if hashlib.sha256(raw_content).hexdigest() != self.checksum_sha256:
            raise MarketDataError("checksum does not match archive")
        algorithm = self.upstream_checksum_algorithm
        upstream_checksum = self.upstream_checksum
        provider_contract_key = self.provider_contract_key
        if (algorithm is None) != (upstream_checksum is None):
            raise MarketDataError(
                "upstream checksum algorithm and value must be paired"
            )
        if algorithm is not None:
            algorithm = _normalized_text(
                algorithm,
                "upstream_checksum_algorithm",
            ).lower()
            upstream_checksum = _normalized_text(
                upstream_checksum,
                "upstream_checksum",
            ).lower()
            expected_length = {"md5": 32, "sha256": 64}.get(algorithm)
            if expected_length is None or not re.fullmatch(
                rf"[0-9a-f]{{{expected_length}}}",
                upstream_checksum,
            ):
                raise MarketDataError("upstream checksum is invalid")
        if provider_contract_key is not None:
            provider_contract_key = _normalized_text(
                provider_contract_key,
                "provider_contract_key",
            ).lower()
            if not _CONTRACT_KEY_PATTERN.fullmatch(
                provider_contract_key
            ):
                raise MarketDataError(
                    "provider_contract_key has invalid format"
                )

        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "data_type", data_type)
        object.__setattr__(self, "file_name", file_name)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(
            self,
            "upstream_checksum_algorithm",
            algorithm,
        )
        object.__setattr__(self, "upstream_checksum", upstream_checksum)
        object.__setattr__(
            self,
            "provider_contract_key",
            provider_contract_key,
        )

    @classmethod
    def from_bytes(
        cls,
        *,
        provider: str,
        data_type: str,
        file_name: str,
        report_minute: datetime,
        received_at: datetime,
        source_url: str,
        content: bytes,
        upstream_checksum_algorithm: str | None = None,
        upstream_checksum: str | None = None,
        provider_contract_key: str | None = None,
    ) -> "MarketDataArchive":
        if not isinstance(content, bytes) or not content:
            raise MarketDataError("content must be non-empty bytes")
        if len(content) > _MAX_RAW_ARCHIVE_BYTES:
            raise MarketDataError("market data archive exceeds size limit")
        return cls(
            provider=provider,
            data_type=data_type,
            file_name=file_name,
            report_minute=report_minute,
            received_at=received_at,
            source_url=source_url,
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            content_encoding="gzip",
            compressed_content=gzip.compress(content, mtime=0),
            uncompressed_bytes=len(content),
            upstream_checksum_algorithm=upstream_checksum_algorithm,
            upstream_checksum=upstream_checksum,
            provider_contract_key=provider_contract_key,
        )


@dataclass(frozen=True)
class MarketDataGap:
    provider: str
    data_type: str
    gap_start: datetime
    gap_end: datetime
    reason: str

    def __post_init__(self) -> None:
        provider = _normalized_text(self.provider, "provider")
        data_type = _normalized_text(self.data_type, "data_type")
        reason = _normalized_text(self.reason, "reason")
        _require_aware(self.gap_start, "gap_start")
        _require_aware(self.gap_end, "gap_end")
        if self.gap_end < self.gap_start:
            raise MarketDataError("gap_end cannot be before gap_start")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "data_type", data_type)
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True)
class FileIngestionResult:
    inserted: bool
    quotes_inserted: int
    sync_run_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.inserted, bool):
            raise MarketDataError("inserted must be boolean")
        if (
            not isinstance(self.quotes_inserted, int)
            or isinstance(self.quotes_inserted, bool)
            or self.quotes_inserted < 0
        ):
            raise MarketDataError("quotes_inserted cannot be negative")
        if (
            not isinstance(self.sync_run_id, int)
            or isinstance(self.sync_run_id, bool)
            or self.sync_run_id <= 0
        ):
            raise MarketDataError("sync_run_id must be greater than 0")


@dataclass(frozen=True)
class ReferenceSyncResult:
    inserted: bool
    instruments_upserted: int
    instruments_deactivated: int
    sync_run_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.inserted, bool):
            raise MarketDataError("inserted must be boolean")
        for field_name in (
            "instruments_upserted",
            "instruments_deactivated",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise MarketDataError(f"{field_name} cannot be negative")
        if (
            not isinstance(self.sync_run_id, int)
            or isinstance(self.sync_run_id, bool)
            or self.sync_run_id <= 0
        ):
            raise MarketDataError("sync_run_id must be greater than 0")


@dataclass(frozen=True)
class CalendarSyncResult:
    inserted: bool
    sessions_upserted: int
    sync_run_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.inserted, bool):
            raise MarketDataError("inserted must be boolean")
        if (
            not isinstance(self.sessions_upserted, int)
            or isinstance(self.sessions_upserted, bool)
            or self.sessions_upserted < 0
        ):
            raise MarketDataError("sessions_upserted cannot be negative")
        if (
            not isinstance(self.sync_run_id, int)
            or isinstance(self.sync_run_id, bool)
            or self.sync_run_id <= 0
        ):
            raise MarketDataError("sync_run_id must be greater than 0")


@dataclass(frozen=True)
class SyncCycleResult:
    processed_files: int
    inserted_quotes: int
    duplicate_files: int
    gaps_detected: int

    def __post_init__(self) -> None:
        for field_name in (
            "processed_files",
            "inserted_quotes",
            "duplicate_files",
            "gaps_detected",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise MarketDataError(f"{field_name} cannot be negative")


def assert_fresh_quote(
    quote: QuoteRecord,
    *,
    now: datetime,
    policy: FreshnessPolicy,
) -> None:
    """Raise unless a quote satisfies the configured freshness contract."""
    _require_aware(now, "now")
    if quote.received_at > now + timedelta(seconds=5):
        raise MarketDataError("quote receipt timestamp is in the future")
    freshness_time = quote.covered_through or quote.event_time
    if freshness_time > now:
        raise MarketDataError("quote freshness timestamp is in the future")

    age = now - freshness_time
    maximum_age = policy.max_delay + policy.tolerance
    if age > maximum_age:
        raise MarketDataError(
            f"quote is stale: age {age} exceeds {maximum_age}"
        )


def _normalized_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketDataError(f"{field} is required")
    return value.strip()


def _normalized_mic(value: object) -> str:
    mic = _normalized_text(value, "mic").upper()
    if not _MIC_PATTERN.fullmatch(mic):
        raise MarketDataError("mic must be a four-character MIC")
    return mic


def _require_aware(value: object, field: str) -> None:
    if not isinstance(value, datetime):
        raise MarketDataError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataError(f"{field} must be timezone-aware")


def _parse_local_time(value: object, field: str) -> time:
    if not isinstance(value, str):
        raise MarketDataError(f"{field} must use HH:MM")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise MarketDataError(f"{field} must use HH:MM") from exc
    if parsed.tzinfo is not None:
        raise MarketDataError(f"{field} must be a local wall-clock time")
    return parsed


def _positive_decimal(value: object, field: str) -> Decimal:
    result = _decimal(value, field)
    if result <= 0:
        raise MarketDataError(f"{field} must be greater than 0")
    return result


def _nonnegative_decimal(value: object, field: str) -> Decimal:
    result = _decimal(value, field)
    if result < 0:
        raise MarketDataError(f"{field} cannot be negative")
    return result


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise MarketDataError(f"{field} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MarketDataError(f"{field} must be a finite number") from exc
    if not result.is_finite():
        raise MarketDataError(f"{field} must be a finite number")
    return result


def _valid_isin(isin: str) -> bool:
    digits = "".join(
        character
        if character.isdigit()
        else str(ord(character) - ord("A") + 10)
        for character in isin
    )
    total = 0
    for index, character in enumerate(reversed(digits)):
        value = int(character)
        if index % 2 == 1:
            value *= 2
        total += value // 10 + value % 10
    return total % 10 == 0
