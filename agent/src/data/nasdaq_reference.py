"""Strict Nasdaq Nordic TIP reference-file parsing for XSTO aliases."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from io import BytesIO
import json
import re

from .market_data import (
    InstrumentRecord,
    InstrumentUniverseSnapshot,
    MarketDataError,
)


_PRODUCTION_TIP_VERSIONS = frozenset({"3.10.17.1"})
_PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,99}$")
_SOURCE_PATH_PATTERN = re.compile(
    r"^INET_Ref_Data/(?P<date>\d{8})/"
    r"Nordic_Equity_RefData\.tip$"
)
_TIP_TAG_PATTERN = re.compile(r"^(?P<tag>[A-Z]*[a-z])(?P<value>.*)$")
_TIP_MESSAGE_TYPE_PATTERN = re.compile(r"^[A-Z]+[a-z]$")
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9 .-]{0,31}$")
_TIP_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:/+-]{1,9}$")
_SOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:/+-]{1,32}$")
_MAX_FILE_BYTES = 20_000_000
_MAX_MESSAGE_BYTES = 2048
_MAX_MESSAGES = 1_000_000
_INSTRUMENT_TYPE_BY_CFI_PREFIX = {
    "ES": "COMMON_STOCK",
    "EP": "PREFERRED_STOCK",
    "ED": "DEPOSITARY_RECEIPT",
}


def validate_nasdaq_tip_version(tip_version: object) -> str:
    """Return an explicitly supported production TIP version."""
    if (
        not isinstance(tip_version, str)
        or tip_version not in _PRODUCTION_TIP_VERSIONS
    ):
        raise MarketDataError("Nasdaq TIP version is not approved")
    return tip_version


@dataclass(frozen=True)
class NasdaqInstrumentAlias:
    """One exact provider symbol bound to a stable FIRDS ISIN."""

    isin: str
    provider_symbol: str
    trading_currency: str
    tradable_id: str
    source_id: str
    valid_from: date | None
    valid_to: date | None


def trading_currencies_by_isin(
    *,
    instruments: Sequence[InstrumentRecord],
    aliases: Sequence[NasdaqInstrumentAlias],
) -> dict[str, str]:
    """Return exact official quote currencies for one frozen XSTO universe."""
    normalized_instruments = tuple(instruments)
    if not normalized_instruments:
        raise MarketDataError("Nasdaq trading-currency universe is empty")
    if any(
        not isinstance(instrument, InstrumentRecord)
        or instrument.mic != "XSTO"
        for instrument in normalized_instruments
    ):
        raise MarketDataError(
            "Nasdaq trading currencies require only XSTO instruments"
        )
    expected_isins = {instrument.isin for instrument in normalized_instruments}
    if len(expected_isins) != len(normalized_instruments):
        raise MarketDataError(
            "Nasdaq trading-currency universe contains duplicate ISIN"
        )

    currencies: dict[str, str] = {}
    for alias in tuple(aliases):
        if not isinstance(alias, NasdaqInstrumentAlias):
            raise MarketDataError(
                "Nasdaq trading currencies require verified aliases"
            )
        isin = alias.isin.strip().upper()
        currency = alias.trading_currency.strip().upper()
        if isin not in expected_isins:
            raise MarketDataError(
                "Nasdaq trading currency is outside the instrument universe"
            )
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise MarketDataError(
                "Nasdaq trading currency has invalid format"
            )
        if isin in currencies:
            raise MarketDataError(
                "Nasdaq trading currencies contain duplicate ISIN"
            )
        currencies[isin] = currency

    if set(currencies) != expected_isins:
        raise MarketDataError(
            "Nasdaq trading currencies do not provide exact instrument coverage"
        )
    return {isin: currencies[isin] for isin in sorted(currencies)}


@dataclass(frozen=True)
class NasdaqAliasSnapshot:
    """Complete, immutable alias evidence for one XSTO business date."""

    provider: str
    mic: str
    business_date: date
    received_at: datetime
    tip_version: str
    source_path: str
    file_checksum_sha256: str
    uncompressed_bytes: int
    reference_checksum_sha256: str
    aliases: tuple[NasdaqInstrumentAlias, ...]

    @property
    def checksum_sha256(self) -> str:
        payload = {
            "schema_version": 1,
            "provider": self.provider,
            "mic": self.mic,
            "business_date": self.business_date.isoformat(),
            "tip_version": self.tip_version,
            "source_path": self.source_path,
            "file_checksum_sha256": self.file_checksum_sha256,
            "uncompressed_bytes": self.uncompressed_bytes,
            "reference_checksum_sha256": self.reference_checksum_sha256,
            "aliases": [
                {
                    "isin": alias.isin,
                    "provider_symbol": alias.provider_symbol,
                    "trading_currency": alias.trading_currency,
                    "tradable_id": alias.tradable_id,
                    "source_id": alias.source_id,
                    "valid_from": (
                        alias.valid_from.isoformat()
                        if alias.valid_from is not None
                        else None
                    ),
                    "valid_to": (
                        alias.valid_to.isoformat()
                        if alias.valid_to is not None
                        else None
                    ),
                }
                for alias in self.aliases
            ],
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class NasdaqAliasSyncResult:
    """Outcome of atomically storing and activating one alias snapshot."""

    inserted: bool
    aliases_inserted: int
    sync_run_id: int
    snapshot_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.inserted, bool):
            raise MarketDataError("inserted must be boolean")
        if (
            not isinstance(self.aliases_inserted, int)
            or isinstance(self.aliases_inserted, bool)
            or self.aliases_inserted < 0
        ):
            raise MarketDataError("aliases_inserted cannot be negative")
        for field_name in ("sync_run_id", "snapshot_id"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                raise MarketDataError(f"{field_name} must be positive")


def parse_nasdaq_reference_file(
    content: bytes,
    *,
    source_path: str,
    received_at: datetime,
    reference_snapshot: InstrumentUniverseSnapshot,
    provider: str,
    tip_version: str,
) -> NasdaqAliasSnapshot:
    """Parse one complete Nordic equity reference file and fail closed."""
    _validate_contract_inputs(
        content=content,
        source_path=source_path,
        received_at=received_at,
        reference_snapshot=reference_snapshot,
        provider=provider,
        tip_version=tip_version,
    )
    path_match = _SOURCE_PATH_PATTERN.fullmatch(source_path)
    assert path_match is not None
    path_date = _tip_date(path_match.group("date"), field="source path date")

    business_dates: list[date] = []
    exchanges: dict[tuple[str, str], str] = {}
    tradables: dict[tuple[str, str], dict[str, str]] = {}
    share_source_ids: dict[tuple[str, str], str] = {}
    seen_source_systems: set[str] = set()
    ended_source_systems: set[str] = set()

    for message_type, fields in _iter_tip_messages(content):
        source_system = fields.get("s")
        if message_type == "EOBd":
            source_system = _required(fields, "s", message_type)
            _validated_tip_id(
                source_system,
                field="source system",
            )
            if source_system in ended_source_systems:
                raise MarketDataError(
                    "TIP source has duplicate EndOfBasicData"
                )
            ended_source_systems.add(source_system)
            continue
        if source_system is not None:
            _validated_tip_id(
                source_system,
                field="source system",
            )
            if source_system in ended_source_systems:
                raise MarketDataError(
                    "TIP source data appears after EndOfBasicData"
                )
            seen_source_systems.add(source_system)
        if message_type == "BDBu":
            business_dates.append(
                _tip_date(
                    _required(fields, "Bd", message_type),
                    field="business date",
                )
            )
            continue
        if message_type == "BDx":
            key = _entity_key(fields, message_type)
            mic = _required(fields, "MIc", message_type).upper()
            _insert_unique(exchanges, key, mic, entity="exchange")
            continue
        if message_type == "BDt":
            key = _entity_key(fields, message_type)
            _insert_unique(tradables, key, fields, entity="tradable")
            continue
        if message_type == "BDSh":
            key = _entity_key(fields, message_type)
            share_source_id = _required(fields, "Si", message_type)
            if not _SOURCE_ID_PATTERN.fullmatch(share_source_id):
                raise MarketDataError(
                    "Nasdaq share source identity is invalid"
                )
            _insert_unique(
                share_source_ids,
                key,
                share_source_id,
                entity="share",
            )

    if not ended_source_systems:
        raise MarketDataError("TIP file is missing EndOfBasicData")
    if not seen_source_systems.issubset(ended_source_systems):
        raise MarketDataError(
            "TIP source is missing EndOfBasicData"
        )
    if len(business_dates) != 1:
        raise MarketDataError(
            "TIP file must contain exactly one business date"
        )
    business_date = business_dates[0]
    if business_date != path_date:
        raise MarketDataError(
            "TIP business date does not match source path date"
        )
    if business_date > received_at.date():
        raise MarketDataError("TIP business date is in the future")

    expected_by_isin = {
        instrument.isin: instrument
        for instrument in reference_snapshot.instruments
    }
    aliases_by_isin: dict[str, NasdaqInstrumentAlias] = {}
    isins_by_symbol: dict[str, str] = {}

    for key, share_source_id in sorted(share_source_ids.items()):
        fields = tradables.get(key)
        if fields is None:
            raise MarketDataError(
                "TIP share has no matching tradable entity"
            )
        primary_mic = fields.get("PMIc", "").upper()
        if primary_mic != reference_snapshot.mic:
            continue
        if "ITSt" in fields:
            continue

        valid_from = _optional_tip_date(fields.get("LDa"), field="listing date")
        valid_to = _optional_tip_date(
            fields.get("TTd"),
            field="traded-through date",
        )
        if valid_from is not None and valid_from > business_date:
            continue
        if valid_to is not None and valid_to < business_date:
            continue

        source_system, tradable_id = key
        exchange_id = _validated_tip_id(
            _required(fields, "Ex", "BDt"),
            field="exchange id",
        )
        exchange_mic = exchanges.get((source_system, exchange_id))
        if exchange_mic != reference_snapshot.mic:
            raise MarketDataError(
                "TIP tradable exchange does not match XSTO"
            )

        isin = _required(fields, "ISn", "BDt").upper()
        instrument = expected_by_isin.get(isin)
        if instrument is None:
            raise MarketDataError(
                "Nasdaq XSTO alias is outside the frozen FIRDS snapshot"
            )
        symbol = _required(fields, "SYm", "BDt").upper()
        if not _SYMBOL_PATTERN.fullmatch(symbol):
            raise MarketDataError("Nasdaq ticker has invalid format")
        currency = _required(fields, "CUt", "BDt").upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise MarketDataError(
                "Nasdaq trading currency has invalid format"
            )
        cfi_code = _required(fields, "CFc", "BDt").upper()
        expected_type = _INSTRUMENT_TYPE_BY_CFI_PREFIX.get(cfi_code[:2])
        if (
            len(cfi_code) != 6
            or not cfi_code.isalnum()
            or expected_type != instrument.instrument_type
            or (
                instrument.cfi_code is not None
                and cfi_code != instrument.cfi_code
            )
        ):
            raise MarketDataError("Nasdaq CFI conflicts with FIRDS")
        source_id = _required(fields, "Si", "BDt")
        if (
            source_id != share_source_id
            or not _SOURCE_ID_PATTERN.fullmatch(source_id)
        ):
            raise MarketDataError(
                "Nasdaq share and tradable source identity conflict"
            )
        if isin in aliases_by_isin:
            raise MarketDataError(
                "Nasdaq ISIN has more than one active XSTO ticker"
            )
        other_isin = isins_by_symbol.get(symbol)
        if other_isin is not None and other_isin != isin:
            raise MarketDataError(
                "Nasdaq ticker is not unique within XSTO"
            )
        alias = NasdaqInstrumentAlias(
            isin=isin,
            provider_symbol=symbol,
            trading_currency=currency,
            tradable_id=tradable_id,
            source_id=source_id,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        aliases_by_isin[isin] = alias
        isins_by_symbol[symbol] = isin

    if set(aliases_by_isin) != set(expected_by_isin):
        raise MarketDataError(
            "Nasdaq aliases do not provide exact FIRDS coverage"
        )
    aliases = tuple(
        aliases_by_isin[isin]
        for isin in sorted(aliases_by_isin)
    )
    return NasdaqAliasSnapshot(
        provider=provider,
        mic=reference_snapshot.mic,
        business_date=business_date,
        received_at=received_at,
        tip_version=tip_version,
        source_path=source_path,
        file_checksum_sha256=hashlib.sha256(content).hexdigest(),
        uncompressed_bytes=len(content),
        reference_checksum_sha256=reference_snapshot.checksum_sha256,
        aliases=aliases,
    )


def _validate_contract_inputs(
    *,
    content: bytes,
    source_path: str,
    received_at: datetime,
    reference_snapshot: InstrumentUniverseSnapshot,
    provider: str,
    tip_version: str,
) -> None:
    validate_nasdaq_tip_version(tip_version)
    if not isinstance(provider, str) or not _PROVIDER_PATTERN.fullmatch(
        provider
    ):
        raise MarketDataError("Nasdaq provider is invalid")
    if (
        not isinstance(reference_snapshot, InstrumentUniverseSnapshot)
        or reference_snapshot.mic != "XSTO"
    ):
        raise MarketDataError(
            "Nasdaq aliases require a frozen XSTO reference snapshot"
        )
    if (
        not isinstance(received_at, datetime)
        or received_at.tzinfo is None
        or received_at.utcoffset() is None
    ):
        raise MarketDataError("received_at must be timezone-aware")
    if not isinstance(source_path, str) or _SOURCE_PATH_PATTERN.fullmatch(
        source_path
    ) is None:
        raise MarketDataError("Nasdaq reference source path is invalid")
    if (
        not isinstance(content, bytes)
        or not content
        or len(content) > _MAX_FILE_BYTES
    ):
        raise MarketDataError("Nasdaq reference file size is invalid")
    if not content.isascii():
        raise MarketDataError(
            "Nasdaq reference file must be ASCII"
        )


def _iter_tip_messages(
    content: bytes,
) -> Iterator[tuple[str, dict[str, str]]]:
    message_count = 0
    for raw_line in BytesIO(content):
        message_count += 1
        if message_count > _MAX_MESSAGES:
            raise MarketDataError("TIP file contains too many messages")
        if raw_line.endswith(b"\n"):
            raw_line = raw_line[:-1]
            if raw_line.endswith(b"\r"):
                raw_line = raw_line[:-1]
        if b"\r" in raw_line or b"\n" in raw_line:
            raise MarketDataError(
                "TIP file contains an invalid line ending"
            )
        if not raw_line:
            raise MarketDataError("TIP file contains an empty message")
        if len(raw_line) > _MAX_MESSAGE_BYTES:
            raise MarketDataError("TIP message exceeds 2048 bytes")
        line = raw_line.decode("ascii")
        parts = _split_tip_fields(line)
        message_type = parts[0]
        if not _TIP_MESSAGE_TYPE_PATTERN.fullmatch(message_type):
            raise MarketDataError("TIP message type is invalid")
        fields: dict[str, str] = {}
        for part in parts[1:]:
            match = _TIP_TAG_PATTERN.fullmatch(part)
            if match is None:
                raise MarketDataError("TIP field tag is invalid")
            tag = match.group("tag")
            if tag in fields:
                raise MarketDataError("duplicate TIP tag in message")
            fields[tag] = match.group("value")
        yield message_type, fields
    if message_count == 0:
        raise MarketDataError("TIP file contains no messages")


def _split_tip_fields(line: str) -> tuple[str, ...]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    terminated = False
    for char in line:
        if escaped:
            if char not in {";", ":", ",", "\\", "n"}:
                raise MarketDataError("TIP escape sequence is invalid")
            current.append("\n" if char == "n" else char)
            escaped = False
            terminated = False
            continue
        if char == "\\":
            escaped = True
            terminated = False
            continue
        if char == ";":
            fields.append("".join(current))
            current = []
            terminated = True
            continue
        current.append(char)
        terminated = False
    if escaped or not terminated or current:
        raise MarketDataError(
            "TIP escape sequence is invalid or message must terminate "
            "with semicolon"
        )
    if not fields or not fields[0] or any(not field for field in fields):
        raise MarketDataError("TIP message contains an empty field")
    return tuple(fields)


def _entity_key(
    fields: dict[str, str],
    message_type: str,
) -> tuple[str, str]:
    key = (
        _required(fields, "s", message_type),
        _required(fields, "i", message_type),
    )
    _validated_tip_id(key[0], field="source system")
    _validated_tip_id(key[1], field="entity id")
    return key


def _required(
    fields: dict[str, str],
    tag: str,
    message_type: str,
) -> str:
    value = fields.get(tag)
    if value is None or not value:
        raise MarketDataError(
            f"{message_type} is missing required TIP tag {tag}"
        )
    return value


def _insert_unique(mapping, key, value, *, entity: str) -> None:
    if key in mapping:
        raise MarketDataError(f"TIP {entity} entity is duplicated")
    mapping[key] = value


def _tip_date(value: str, *, field: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise MarketDataError(f"TIP {field} is invalid")
    try:
        return date(
            int(value[0:4]),
            int(value[4:6]),
            int(value[6:8]),
        )
    except ValueError as exc:
        raise MarketDataError(f"TIP {field} is invalid") from exc


def _optional_tip_date(value: str | None, *, field: str) -> date | None:
    return None if value is None else _tip_date(value, field=field)


def _validated_tip_id(value: str, *, field: str) -> str:
    if not _TIP_ID_PATTERN.fullmatch(value):
        raise MarketDataError(f"TIP {field} is invalid")
    return value
