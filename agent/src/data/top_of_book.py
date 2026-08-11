"""Fail-closed Level 1 order-book state reduction."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from src.data.market_data import (
    MarketDataError,
    TopOfBookBatch,
    TopOfBookUpdate,
)


_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_MIC_PATTERN = re.compile(r"^[A-Z0-9]{4}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class TopOfBookState:
    """Latest known bid and ask after a verified gap-free source interval."""

    isin: str
    mic: str
    source: str
    reference_checksum_sha256: str
    currency: str
    bid_price: Decimal | None
    bid_quantity: Decimal | None
    bid_order_count: int | None
    bid_event_time: datetime | None
    bid_publication_time: datetime | None
    ask_price: Decimal | None
    ask_quantity: Decimal | None
    ask_order_count: int | None
    ask_event_time: datetime | None
    ask_publication_time: datetime | None
    covered_through: datetime
    received_at: datetime
    trading_system: str
    trading_phase: str

    def __post_init__(self) -> None:
        isin = _text(self.isin, "isin").upper()
        mic = _text(self.mic, "mic").upper()
        source = _text(self.source, "source")
        reference_checksum = _text(
            self.reference_checksum_sha256,
            "reference_checksum_sha256",
        ).lower()
        currency = _text(self.currency, "currency").upper()
        trading_system = _text(
            self.trading_system,
            "trading_system",
        ).upper()
        trading_phase = _text(self.trading_phase, "trading_phase").upper()

        if not _ISIN_PATTERN.fullmatch(isin) or not _valid_isin(isin):
            raise MarketDataError("isin is invalid")
        if not _MIC_PATTERN.fullmatch(mic):
            raise MarketDataError("mic must be a four-character MIC")
        if not _CURRENCY_PATTERN.fullmatch(currency):
            raise MarketDataError("currency must be a three-character code")
        if not _SHA256_PATTERN.fullmatch(reference_checksum):
            raise MarketDataError(
                "reference_checksum_sha256 must be lowercase SHA-256"
            )
        if not re.fullmatch(r"[A-Z0-9_-]{1,20}", trading_system):
            raise MarketDataError("trading_system has invalid format")
        if not re.fullmatch(r"[A-Z0-9_-]{1,20}", trading_phase):
            raise MarketDataError("trading_phase has invalid format")

        _require_aware(self.covered_through, "covered_through")
        _require_aware(self.received_at, "received_at")
        if self.covered_through > self.received_at:
            raise MarketDataError(
                "covered_through cannot be after received_at"
            )

        bid_price, bid_quantity, bid_order_count = _side_values(
            side="bid",
            price=self.bid_price,
            quantity=self.bid_quantity,
            order_count=self.bid_order_count,
            event_time=self.bid_event_time,
            publication_time=self.bid_publication_time,
            covered_through=self.covered_through,
        )
        ask_price, ask_quantity, ask_order_count = _side_values(
            side="ask",
            price=self.ask_price,
            quantity=self.ask_quantity,
            order_count=self.ask_order_count,
            event_time=self.ask_event_time,
            publication_time=self.ask_publication_time,
            covered_through=self.covered_through,
        )
        if (
            bid_price is not None
            and ask_price is not None
            and bid_price > ask_price
        ):
            raise MarketDataError("top of book is crossed")

        object.__setattr__(self, "isin", isin)
        object.__setattr__(self, "mic", mic)
        object.__setattr__(self, "source", source)
        object.__setattr__(
            self,
            "reference_checksum_sha256",
            reference_checksum,
        )
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "trading_system", trading_system)
        object.__setattr__(self, "trading_phase", trading_phase)
        object.__setattr__(self, "bid_price", bid_price)
        object.__setattr__(self, "bid_quantity", bid_quantity)
        object.__setattr__(self, "bid_order_count", bid_order_count)
        object.__setattr__(self, "ask_price", ask_price)
        object.__setattr__(self, "ask_quantity", ask_quantity)
        object.__setattr__(self, "ask_order_count", ask_order_count)

    @property
    def is_two_sided(self) -> bool:
        return self.bid_price is not None and self.ask_price is not None

    @property
    def mid_price(self) -> Decimal | None:
        if not self.is_two_sided:
            return None
        assert self.bid_price is not None
        assert self.ask_price is not None
        return (self.bid_price + self.ask_price) / Decimal("2")

    @property
    def observed_delay(self) -> timedelta:
        return self.received_at - self.covered_through


@dataclass(frozen=True)
class TopOfBookStreamCursor:
    """Continuity evidence that survives a minute with no XSTO updates."""

    source: str
    mic: str
    covered_through: datetime
    received_at: datetime
    reference_checksum_sha256: str

    def __post_init__(self) -> None:
        source = _text(self.source, "source")
        mic = _text(self.mic, "mic").upper()
        reference_checksum = _text(
            self.reference_checksum_sha256,
            "reference_checksum_sha256",
        ).lower()
        if not _MIC_PATTERN.fullmatch(mic):
            raise MarketDataError("mic must be a four-character MIC")
        if not _SHA256_PATTERN.fullmatch(reference_checksum):
            raise MarketDataError(
                "reference_checksum_sha256 must be lowercase SHA-256"
            )
        _require_aware(self.covered_through, "covered_through")
        _require_aware(self.received_at, "received_at")
        if self.covered_through > self.received_at:
            raise MarketDataError(
                "covered_through cannot be after received_at"
            )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "mic", mic)
        object.__setattr__(
            self,
            "reference_checksum_sha256",
            reference_checksum,
        )


@dataclass(frozen=True)
class TopOfBookSnapshot(Sequence[TopOfBookState]):
    """Order books plus the source cursor for the processed file minute."""

    states: tuple[TopOfBookState, ...]
    cursor: TopOfBookStreamCursor

    def __post_init__(self) -> None:
        if not isinstance(self.states, tuple):
            raise MarketDataError("states must be a tuple")
        if not isinstance(self.cursor, TopOfBookStreamCursor):
            raise MarketDataError(
                "cursor must be a TopOfBookStreamCursor"
            )
        seen: set[tuple[str, str, str]] = set()
        for state in self.states:
            if not isinstance(state, TopOfBookState):
                raise MarketDataError(
                    "states must contain TopOfBookState values"
                )
            key = (state.source, state.mic, state.isin)
            if key in seen:
                raise MarketDataError("states contain duplicate order books")
            seen.add(key)
            if (
                state.source != self.cursor.source
                or state.mic != self.cursor.mic
                or state.covered_through != self.cursor.covered_through
                or state.received_at != self.cursor.received_at
                or state.reference_checksum_sha256
                != self.cursor.reference_checksum_sha256
            ):
                raise MarketDataError(
                    "state identity does not match stream cursor"
                )

    def __len__(self) -> int:
        return len(self.states)

    def __getitem__(self, index):
        return self.states[index]


def apply_top_of_book_batch(
    *,
    previous: Sequence[TopOfBookState] | TopOfBookSnapshot,
    batch: TopOfBookBatch,
) -> TopOfBookSnapshot:
    """Apply one checksum-bound file after exact minute continuity."""
    if not isinstance(batch, TopOfBookBatch):
        raise TypeError("batch must be a TopOfBookBatch")

    books: dict[tuple[str, str, str], dict[str, object]] = {}
    prior_coverages: set[datetime] = set()
    prior_receipts: set[datetime] = set()
    if isinstance(previous, TopOfBookSnapshot):
        previous_states = previous.states
        prior_cursor = previous.cursor
        if (
            prior_cursor.source != batch.source
            or prior_cursor.mic != batch.mic
        ):
            raise MarketDataError(
                "previous stream cursor does not match source-file batch"
            )
        prior_coverages.add(prior_cursor.covered_through)
        prior_receipts.add(prior_cursor.received_at)
    else:
        previous_states = previous

    for state in previous_states:
        if not isinstance(state, TopOfBookState):
            raise TypeError("previous must contain TopOfBookState values")
        key = (state.source, state.mic, state.isin)
        if key in books:
            raise MarketDataError("previous contains duplicate order books")
        if state.source != batch.source or state.mic != batch.mic:
            raise MarketDataError(
                "previous order book does not match source-file batch"
            )
        if state.isin not in batch.instrument_isins:
            raise MarketDataError(
                "previous order book is outside the batch reference universe"
            )
        prior_coverages.add(state.covered_through)
        prior_receipts.add(state.received_at)
        books[key] = _state_values(state)
    if len(prior_coverages) > 1:
        raise MarketDataError(
            "previous order books have inconsistent coverage"
        )
    if prior_coverages:
        prior_coverage = next(iter(prior_coverages))
        if prior_coverage != batch.report_minute:
            raise MarketDataError(
                "source file minute gap prevents carrying order-book state"
            )
    if len(prior_receipts) > 1:
        raise MarketDataError(
            "previous order books have inconsistent received_at"
        )
    if prior_receipts and batch.received_at < next(iter(prior_receipts)):
        raise MarketDataError(
            "source file batch received_at regression"
        )

    for update in batch.updates:
        key = (update.source, update.mic, update.isin)
        book = books.get(key)
        if book is None:
            book = _empty_state_values(update, batch)
            books[key] = book
        elif book["currency"] != update.currency:
            raise MarketDataError(
                "update currency contradicts previous order-book state"
            )
        elif book["trading_system"] != update.trading_system:
            raise MarketDataError(
                "trading system changed without an explicit book reset"
            )

        prefix = "bid" if update.side == "BUY" else "ask"
        prior_event_time = book[f"{prefix}_event_time"]
        prior_publication_time = book[f"{prefix}_publication_time"]
        if (
            prior_event_time is not None
            and update.event_time < prior_event_time
        ):
            raise MarketDataError(
                f"{prefix} event time regression at source sequence "
                f"{update.source_sequence}"
            )
        if (
            prior_publication_time is not None
            and update.publication_time < prior_publication_time
        ):
            raise MarketDataError(
                f"{prefix} publication time regression at source sequence "
                f"{update.source_sequence}"
            )
        book[f"{prefix}_price"] = update.price
        book[f"{prefix}_quantity"] = update.quantity
        book[f"{prefix}_order_count"] = update.order_count
        book[f"{prefix}_event_time"] = update.event_time
        book[f"{prefix}_publication_time"] = update.publication_time
        book["trading_system"] = update.trading_system
        book["trading_phase"] = update.trading_phase
    states = []
    for book in books.values():
        book["covered_through"] = batch.covered_through
        book["received_at"] = batch.received_at
        book["reference_checksum_sha256"] = (
            batch.reference_checksum_sha256
        )
        bid_price = book["bid_price"]
        ask_price = book["ask_price"]
        if (
            bid_price is not None
            and ask_price is not None
            and bid_price > ask_price
        ):
            continue
        states.append(TopOfBookState(**book))
    if books and not states:
        raise MarketDataError("all final top-of-book states are crossed")
    ordered_states = tuple(
        sorted(
            states,
            key=lambda state: (state.mic, state.isin, state.source),
        )
    )
    cursor = TopOfBookStreamCursor(
        source=batch.source,
        mic=batch.mic,
        covered_through=batch.covered_through,
        received_at=batch.received_at,
        reference_checksum_sha256=batch.reference_checksum_sha256,
    )
    return TopOfBookSnapshot(states=ordered_states, cursor=cursor)


def _state_values(state: TopOfBookState) -> dict[str, object]:
    return {
        field: getattr(state, field)
        for field in state.__dataclass_fields__
    }


def _empty_state_values(
    update: TopOfBookUpdate,
    batch: TopOfBookBatch,
) -> dict[str, object]:
    return {
        "isin": update.isin,
        "mic": update.mic,
        "source": update.source,
        "reference_checksum_sha256": (
            batch.reference_checksum_sha256
        ),
        "currency": update.currency,
        "bid_price": None,
        "bid_quantity": None,
        "bid_order_count": None,
        "bid_event_time": None,
        "bid_publication_time": None,
        "ask_price": None,
        "ask_quantity": None,
        "ask_order_count": None,
        "ask_event_time": None,
        "ask_publication_time": None,
        "covered_through": update.event_time,
        "received_at": update.received_at,
        "trading_system": update.trading_system,
        "trading_phase": update.trading_phase,
    }


def _side_values(
    *,
    side: str,
    price: object,
    quantity: object,
    order_count: object,
    event_time: datetime | None,
    publication_time: datetime | None,
    covered_through: datetime,
) -> tuple[Decimal | None, Decimal | None, int | None]:
    present = (
        price is not None,
        quantity is not None,
        order_count is not None,
    )
    if any(present) and not all(present):
        raise MarketDataError(
            f"{side} price, quantity and order_count must be all present "
            "or all empty"
        )
    metadata_present = (
        event_time is not None,
        publication_time is not None,
    )
    if any(metadata_present) and not all(metadata_present):
        raise MarketDataError(
            f"{side} event and publication times must be both present or empty"
        )
    if all(present) and not all(metadata_present):
        raise MarketDataError(
            f"{side} values require event and publication times"
        )
    if all(metadata_present):
        assert event_time is not None
        assert publication_time is not None
        _require_aware(event_time, f"{side}_event_time")
        _require_aware(publication_time, f"{side}_publication_time")
        if event_time > publication_time:
            raise MarketDataError(
                f"{side} publication time cannot precede event time"
            )
        if publication_time > covered_through:
            raise MarketDataError(
                f"{side} publication time exceeds verified coverage"
            )

    normalized_price = None
    normalized_quantity = None
    normalized_order_count = None
    if all(present):
        normalized_price = _decimal(price, f"{side}_price", positive=True)
        normalized_quantity = _decimal(
            quantity,
            f"{side}_quantity",
            positive=True,
        )
        if (
            isinstance(order_count, bool)
            or not isinstance(order_count, int)
            or order_count <= 0
        ):
            raise MarketDataError(
                f"{side}_order_count must be a positive integer"
            )
        normalized_order_count = order_count
    return normalized_price, normalized_quantity, normalized_order_count


def _decimal(value: object, field: str, *, positive: bool) -> Decimal:
    if isinstance(value, bool):
        raise MarketDataError(f"{field} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketDataError(f"{field} must be numeric") from exc
    if not parsed.is_finite():
        raise MarketDataError(f"{field} must be finite")
    if positive and parsed <= 0:
        raise MarketDataError(f"{field} must be positive")
    if not positive and parsed < 0:
        raise MarketDataError(f"{field} cannot be negative")
    return parsed


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketDataError(f"{field} must be non-empty text")
    return value.strip()


def _require_aware(value: datetime, field: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise MarketDataError(f"{field} must be timezone-aware")


def _valid_isin(value: str) -> bool:
    converted = "".join(
        str(ord(character) - ord("A") + 10)
        if character.isalpha()
        else character
        for character in value
    )
    total = 0
    for index, digit in enumerate(reversed(converted)):
        number = int(digit)
        if index % 2 == 1:
            number *= 2
        total += number // 10 + number % 10
    return total % 10 == 0
