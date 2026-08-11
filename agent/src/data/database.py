"""
Database Layer

Handles all PostgreSQL interactions for the trading agent.
"""

import gzip
import json
import hashlib
import logging
import math
import re
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from ..runtime_secrets import read_runtime_secret
from ..core.execution_costs import (
    ExecutionCostModel,
    calculate_paper_execution,
    calculate_top_of_book_execution,
)
from ..core.risk import EntryRiskDecision, evaluate_entry_risk
from .market_data import (
    CalendarSyncResult,
    FileIngestionResult,
    IndexLevelRecord,
    InstrumentRecord,
    InstrumentUniverseSnapshot,
    MarketCalendarSnapshot,
    MarketDataArchive,
    MarketDataError,
    MarketDataGap,
    MarketSession,
    QuoteRecord,
    ReferenceSyncResult,
    TopOfBookBatch,
)
from .provider_governance import (
    ProviderContract,
    ProviderValidation,
    ReferenceDataEntitlement,
    assert_provider_ready,
    assert_reference_entitlement_ready,
)
from .nasdaq_pretrade import NasdaqPublicPolicyEvidence
from .nasdaq_reference import (
    NasdaqAliasSnapshot,
    NasdaqAliasSyncResult,
    NasdaqInstrumentAlias,
)
from .nasdaq_sector import SectorClassification
from .top_of_book import (
    TopOfBookSnapshot,
    TopOfBookState,
    TopOfBookStreamCursor,
    apply_top_of_book_batch,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeLogResult:
    trade_id: int
    inserted: bool


@dataclass(frozen=True)
class PreTradeIngestionResult:
    batch_id: int
    inserted: bool
    updates_inserted: int
    states_inserted: int


@dataclass(frozen=True)
class TradingControlState:
    status: str
    max_daily_loss_pct: float
    reason: str | None
    changed_by: str
    changed_at: datetime


_AUTHORIZED_XSTO_PROVIDER_CTE = """
latest_reference AS (
    SELECT id, instrument_count, universe_checksum_sha256
    FROM reference_data_snapshots
    WHERE mic = 'XSTO'
    ORDER BY snapshot_date DESC, id DESC
    LIMIT 1
),
authorized_provider AS (
    SELECT
        contract.id AS contract_id,
        contract.provider,
        contract.data_type,
        contract.delivery_mode,
        contract.nominal_delay_seconds,
        contract.max_transport_lag_seconds,
        contract.authorization_basis
    FROM market_data_provider_contracts contract
    JOIN LATERAL (
        SELECT validation.*
        FROM market_data_provider_validations validation
        WHERE validation.contract_id = contract.id
        ORDER BY validation.validated_at DESC, validation.id DESC
        LIMIT 1
    ) validation ON TRUE
    JOIN latest_reference reference ON TRUE
    WHERE contract.mic = 'XSTO'
      AND contract.data_type IN (
          'delayed-post-trade-equity',
          'delayed-pre-trade-equity',
          'realtime-equity-level-1'
      )
      AND contract.status = 'VALIDATED'
      AND contract.governance_evidence_status = 'VERIFIED'
      AND contract.proposed_by IS NOT NULL
      AND contract.reviewed_by IS NOT NULL
      AND contract.external_distribution = FALSE
      AND contract.derived_storage_allowed = TRUE
      AND CURRENT_DATE BETWEEN contract.valid_from AND contract.valid_until
      AND contract.transport_verified_at IS NOT NULL
      AND validation.status = 'PASSED'
      AND validation.governance_evidence_status = 'VERIFIED'
      AND validation.valid_until >= NOW()
      AND validation.reference_snapshot_id = reference.id
      AND validation.reference_checksum_sha256 =
          reference.universe_checksum_sha256
      AND validation.expected_instruments = reference.instrument_count
      AND validation.product_covered_instruments =
          validation.expected_instruments
      AND validation.symbol_mapped_instruments =
          validation.expected_instruments
      AND validation.sample_file_count > 0
      AND validation.sample_quote_count > 0
      AND validation.max_observed_delivery_seconds BETWEEN
          contract.nominal_delay_seconds
          AND (
              contract.nominal_delay_seconds
              + contract.max_transport_lag_seconds
          )
      AND (
          (
              contract.authorization_basis = 'NEGOTIATED_CONTRACT'
              AND contract.legal_reviewed_at IS NOT NULL
              AND contract.raw_storage_allowed = TRUE
              AND validation.validation_basis =
                  'FIVE_SESSION_ACCEPTANCE'
              AND validation.acceptance_session_count >= 5
          )
          OR (
              contract.authorization_basis =
                  'PUBLIC_NONCOMMERCIAL_TERMS'
              AND contract.provider = 'nasdaq-nordic'
              AND contract.data_type = 'delayed-pre-trade-equity'
              AND contract.delivery_mode = 'DELAYED_15M'
              AND contract.transport = 'PUBLIC_CSV'
              AND contract.usage_scope =
                  'INTERNAL_ANALYSIS_AND_PAPER'
              AND contract.raw_storage_allowed = FALSE
              AND contract.policy_verified_at IS NOT NULL
              AND contract.policy_verified_by IS NOT NULL
              AND validation.validation_basis =
                  'CONTINUOUS_FILE_VALIDATION'
              AND validation.acceptance_session_count >= 1
          )
      )
    ORDER BY contract.updated_at DESC, contract.id DESC
    LIMIT 1
)
"""


def _reference_snapshot_checksum(
    archives: Sequence[MarketDataArchive],
    instruments: Sequence[InstrumentRecord],
) -> str:
    payload = {
        "files": [
            {
                "file_name": archive.file_name,
                "checksum_sha256": archive.checksum_sha256,
                "upstream_checksum_algorithm": (
                    archive.upstream_checksum_algorithm
                ),
                "upstream_checksum": archive.upstream_checksum,
            }
            for archive in sorted(
                archives,
                key=lambda item: item.file_name,
            )
        ],
        "instruments": [
            {
                "isin": instrument.isin,
                "symbol": instrument.symbol,
                "name": instrument.name,
                "mic": instrument.mic,
                "currency": instrument.currency,
                "instrument_type": instrument.instrument_type,
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
                "source": instrument.source,
            }
            for instrument in sorted(
                instruments,
                key=lambda item: item.isin,
            )
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _canonical_evidence_value(value):
    if is_dataclass(value):
        return {
            field.name: _canonical_evidence_value(
                getattr(value, field.name)
            )
            for field in fields(value)
            if not field.name.startswith("_")
        }
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise MarketDataError(
                "pre-trade evidence contains a naive timestamp"
            )
        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [_canonical_evidence_value(item) for item in value]
    if isinstance(value, list):
        return [_canonical_evidence_value(item) for item in value]
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_evidence_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise MarketDataError(
        "pre-trade evidence contains an unsupported payload value"
    )


def _top_of_book_payload_checksum(
    batch: TopOfBookBatch,
    snapshot: TopOfBookSnapshot,
) -> str:
    payload = {
        "schema_version": 1,
        "batch": _canonical_evidence_value(batch),
        "snapshot": _canonical_evidence_value(snapshot),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class Database:
    """Database interface for trading agent."""

    def __init__(self):
        self.database_url = read_runtime_secret(
            "DATABASE_URL",
            required=True,
        )
        self.engine = create_engine(self.database_url)
        self.Session = sessionmaker(bind=self.engine)
        self._ensure_tables()

    def _ensure_tables(self):
        """Fail fast when the external migration step has not completed."""
        with self.Session() as session:
            migration_table = session.execute(
                text("SELECT to_regclass('public.schema_migrations')")
            ).scalar_one()
            if migration_table is None:
                raise RuntimeError(
                    "Database migrations have not run; start the migrate service first"
                )

            version = session.execute(
                text("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
            ).scalar_one()
            if version < 48:
                raise RuntimeError(
                    f"Database schema is too old (version {version}); "
                    "version 48 is required"
                )

    def upsert_instruments(
        self,
        instruments: Sequence[InstrumentRecord],
    ) -> dict[str, int]:
        """Upsert point-in-time reference data keyed by stable ISIN."""
        with self.Session.begin() as session:
            return self._upsert_instruments(
                session,
                instruments,
                reference_snapshot_date=None,
            )

    def get_active_instruments(
        self,
        *,
        mic: str,
    ) -> tuple[InstrumentRecord, ...]:
        """Return active primary-listed common shares for one MIC."""
        if not isinstance(mic, str) or not re.fullmatch(
            r"[A-Z0-9]{4}",
            mic,
        ):
            raise ValueError("mic must be a four-character MIC")
        with self.Session() as session:
            rows = session.execute(text("""
                SELECT
                    isin,
                    symbol,
                    name,
                    mic,
                    notional_currency,
                    instrument_type,
                    source,
                    primary_listing,
                    status,
                    first_trade_date,
                    last_trade_date,
                    cfi_code
                FROM instruments
                WHERE
                    mic = :mic
                    AND status = 'ACTIVE'
                    AND primary_listing = TRUE
                    AND instrument_type IN (
                        'COMMON_STOCK',
                        'PREFERRED_STOCK',
                        'DEPOSITARY_RECEIPT'
                    )
                ORDER BY COALESCE(symbol, isin), isin
            """), {'mic': mic}).mappings().all()
        return tuple(
            InstrumentRecord(
                isin=row['isin'].strip(),
                symbol=row['symbol'],
                name=row['name'],
                mic=row['mic'].strip(),
                currency=row['notional_currency'].strip(),
                instrument_type=row['instrument_type'],
                source=row['source'],
                primary_listing=row['primary_listing'],
                status=row['status'],
                first_trade_date=row['first_trade_date'],
                last_trade_date=row['last_trade_date'],
                cfi_code=(
                    row['cfi_code'].strip()
                    if row['cfi_code'] is not None
                    else None
                ),
            )
            for row in rows
        )

    def sync_instrument_snapshot(
        self,
        *,
        provider: str,
        mic: str,
        snapshot_date: date,
        received_at: datetime,
        archives: Sequence[MarketDataArchive],
        instruments: Sequence[InstrumentRecord],
    ) -> ReferenceSyncResult:
        """Atomically replace one provider's complete point-in-time universe."""
        self._validate_reference_snapshot(
            provider=provider,
            mic=mic,
            snapshot_date=snapshot_date,
            received_at=received_at,
            archives=archives,
            instruments=instruments,
        )
        checksum = _reference_snapshot_checksum(archives, instruments)
        run_key = (
            f"reference:{provider}:{mic}:{snapshot_date.isoformat()}"
        )
        active_isins = [instrument.isin for instrument in instruments]

        with self.Session.begin() as session:
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:run_key, 0))"
                ),
                {"run_key": run_key},
            )
            latest = session.execute(text("""
                SELECT
                    snapshot_date,
                    instrument_count
                FROM reference_data_snapshots
                WHERE provider = :provider AND mic = :mic
                ORDER BY snapshot_date DESC
                LIMIT 1
            """), {
                "provider": provider,
                "mic": mic,
            }).mappings().first()
            if latest is not None and latest["snapshot_date"] > snapshot_date:
                raise MarketDataError(
                    "reference snapshot is older than stored state"
                )

            existing = session.execute(text("""
                SELECT
                    id,
                    universe_checksum_sha256,
                    file_count,
                    instrument_count,
                    sync_run_id
                FROM reference_data_snapshots
                WHERE
                    provider = :provider
                    AND mic = :mic
                    AND snapshot_date = :snapshot_date
            """), {
                "provider": provider,
                "mic": mic,
                "snapshot_date": snapshot_date,
            }).mappings().first()
            if existing is not None:
                if (
                    existing["universe_checksum_sha256"].strip() != checksum
                    or existing["file_count"] != len(archives)
                    or existing["instrument_count"] != len(instruments)
                ):
                    raise MarketDataError(
                        "stored reference snapshot differs from input"
                    )
                membership_count = int(session.execute(text("""
                    SELECT COUNT(*)
                    FROM reference_snapshot_instruments
                    WHERE snapshot_id = :snapshot_id
                """), {
                    "snapshot_id": existing["id"],
                }).scalar_one())
                if membership_count != len(instruments):
                    raise MarketDataError(
                        "stored reference snapshot membership is incomplete"
                    )
                return ReferenceSyncResult(
                    inserted=False,
                    instruments_upserted=0,
                    instruments_deactivated=0,
                    sync_run_id=int(existing["sync_run_id"]),
                )

            if (
                latest is not None
                and len(instruments) * 100
                < int(latest["instrument_count"]) * 80
            ):
                raise MarketDataError(
                    "reference universe shrank by more than 20 percent"
                )

            sync_run_id = int(session.execute(text("""
                INSERT INTO data_sync_runs (
                    provider,
                    data_type,
                    started_at,
                    finished_at,
                    status,
                    records_received,
                    records_rejected,
                    records_inserted,
                    run_key
                )
                VALUES (
                    :provider,
                    'instrument-reference-full',
                    NOW(),
                    NOW(),
                    'SUCCEEDED',
                    :instrument_count,
                    0,
                    :instrument_count,
                    :run_key
                )
                RETURNING id
            """), {
                "provider": provider,
                "instrument_count": len(instruments),
                "run_key": run_key,
            }).scalar_one())
            snapshot_id = int(session.execute(text("""
                INSERT INTO reference_data_snapshots (
                    provider,
                    mic,
                    snapshot_date,
                    received_at,
                    universe_checksum_sha256,
                    file_count,
                    instrument_count,
                    sync_run_id
                )
                VALUES (
                    :provider,
                    :mic,
                    :snapshot_date,
                    :received_at,
                    :checksum,
                    :file_count,
                    :instrument_count,
                    :sync_run_id
                )
                RETURNING id
            """), {
                "provider": provider,
                "mic": mic,
                "snapshot_date": snapshot_date,
                "received_at": received_at,
                "checksum": checksum,
                "file_count": len(archives),
                "instrument_count": len(instruments),
                "sync_run_id": sync_run_id,
            }).scalar_one())

            for archive in archives:
                session.execute(text("""
                    INSERT INTO reference_data_files (
                        snapshot_id,
                        file_name,
                        source_url,
                        checksum_sha256,
                        upstream_checksum_algorithm,
                        upstream_checksum,
                        content_encoding,
                        compressed_content,
                        uncompressed_bytes
                    )
                    VALUES (
                        :snapshot_id,
                        :file_name,
                        :source_url,
                        :checksum_sha256,
                        :upstream_checksum_algorithm,
                        :upstream_checksum,
                        :content_encoding,
                        :compressed_content,
                        :uncompressed_bytes
                    )
                """), {
                    "snapshot_id": snapshot_id,
                    "file_name": archive.file_name,
                    "source_url": archive.source_url,
                    "checksum_sha256": archive.checksum_sha256,
                    "upstream_checksum_algorithm": (
                        archive.upstream_checksum_algorithm
                    ),
                    "upstream_checksum": archive.upstream_checksum,
                    "content_encoding": archive.content_encoding,
                    "compressed_content": archive.compressed_content,
                    "uncompressed_bytes": archive.uncompressed_bytes,
                })

            instrument_ids = self._upsert_instruments(
                session,
                instruments,
                reference_snapshot_date=snapshot_date,
            )
            for instrument in instruments:
                session.execute(text("""
                    INSERT INTO reference_snapshot_instruments (
                        snapshot_id,
                        instrument_id,
                        isin
                    )
                    VALUES (
                        :snapshot_id,
                        :instrument_id,
                        :isin
                    )
                """), {
                    "snapshot_id": snapshot_id,
                    "instrument_id": instrument_ids[instrument.isin],
                    "isin": instrument.isin,
                })
            deactivated = session.execute(text("""
                UPDATE instruments
                SET
                    status = 'INACTIVE',
                    source_updated_at = NOW(),
                    synced_at = NOW()
                WHERE
                    source = :provider
                    AND mic = :mic
                    AND status = 'ACTIVE'
                    AND reference_snapshot_date IS DISTINCT FROM :snapshot_date
                    AND NOT (TRIM(isin) = ANY(:active_isins))
            """), {
                "provider": provider,
                "mic": mic,
                "snapshot_date": snapshot_date,
                "active_isins": active_isins,
            }).rowcount
            return ReferenceSyncResult(
                inserted=True,
                instruments_upserted=len(instruments),
                instruments_deactivated=int(deactivated),
                sync_run_id=sync_run_id,
            )

    def get_latest_reference_snapshot_date(
        self,
        *,
        provider: str,
        mic: str,
    ) -> date | None:
        if not isinstance(provider, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{1,99}",
            provider,
        ):
            raise MarketDataError("reference provider is invalid")
        if not isinstance(mic, str) or not re.fullmatch(
            r"[A-Z0-9]{4}",
            mic,
        ):
            raise MarketDataError("mic must be a four-character MIC")
        with self.Session() as session:
            return session.execute(text("""
                SELECT MAX(snapshot_date)
                FROM reference_data_snapshots
                WHERE provider = :provider AND mic = :mic
            """), {
                "provider": provider,
                "mic": mic,
            }).scalar_one()

    def get_latest_reference_universe(
        self,
        *,
        provider: str,
        mic: str,
    ) -> tuple[int, InstrumentUniverseSnapshot]:
        """Load the latest complete frozen universe and its database ID."""
        if not isinstance(provider, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{1,99}",
            provider,
        ):
            raise MarketDataError("reference provider is invalid")
        if not isinstance(mic, str) or not re.fullmatch(
            r"[A-Z0-9]{4}",
            mic,
        ):
            raise MarketDataError("mic must be a four-character MIC")
        with self.Session() as session:
            snapshot_id = session.execute(text("""
                SELECT id
                FROM reference_data_snapshots
                WHERE provider = :provider AND mic = :mic
                ORDER BY snapshot_date DESC, id DESC
                LIMIT 1
            """), {
                "provider": provider,
                "mic": mic,
            }).scalar_one_or_none()
            if snapshot_id is None:
                raise MarketDataError(
                    "frozen reference snapshot is missing"
                )
            universe, _instrument_ids = self._pretrade_reference_universe(
                session,
                reference_snapshot_id=int(snapshot_id),
            )
            return int(snapshot_id), universe

    def sync_instrument_alias_snapshot(
        self,
        *,
        entitlement_id: int,
        reference_snapshot_id: int,
        snapshot: NasdaqAliasSnapshot,
        content: bytes,
    ) -> NasdaqAliasSyncResult:
        """Store and atomically activate exact Nasdaq-to-ISIN aliases."""
        if (
            not isinstance(entitlement_id, int)
            or isinstance(entitlement_id, bool)
            or entitlement_id <= 0
        ):
            raise MarketDataError(
                "entitlement_id must be a positive integer"
            )
        if (
            not isinstance(reference_snapshot_id, int)
            or isinstance(reference_snapshot_id, bool)
            or reference_snapshot_id <= 0
        ):
            raise MarketDataError(
                "reference_snapshot_id must be a positive integer"
            )
        if not isinstance(snapshot, NasdaqAliasSnapshot):
            raise MarketDataError("snapshot must be NasdaqAliasSnapshot")
        if (
            not isinstance(content, bytes)
            or not content
            or len(content) > 20_000_000
        ):
            raise MarketDataError("Nasdaq alias content is invalid")
        if len(content) != snapshot.uncompressed_bytes or (
            hashlib.sha256(content).hexdigest()
            != snapshot.file_checksum_sha256
        ):
            raise MarketDataError(
                "Nasdaq alias content does not match parsed evidence"
            )
        compressed_content = gzip.compress(content, mtime=0)
        run_key = (
            f"aliases:{snapshot.provider}:{snapshot.mic}:"
            f"{snapshot.business_date.isoformat()}"
        )

        with self.Session.begin() as session:
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:run_key, 0))"
                ),
                {"run_key": run_key},
            )
            reference_universe, instrument_ids = (
                self._pretrade_reference_universe(
                    session,
                    reference_snapshot_id=reference_snapshot_id,
                )
            )
            if (
                snapshot.mic != reference_universe.mic
                or snapshot.reference_checksum_sha256
                != reference_universe.checksum_sha256
                or len(snapshot.aliases)
                != len(reference_universe.instruments)
                or {alias.isin for alias in snapshot.aliases}
                != set(reference_universe.instrument_isins)
            ):
                raise MarketDataError(
                    "Nasdaq aliases do not match frozen reference evidence"
                )

            existing = session.execute(text("""
                SELECT
                    id,
                    tip_version,
                    source_path,
                    file_checksum_sha256,
                    uncompressed_bytes,
                    entitlement_id,
                    reference_snapshot_id,
                    reference_checksum_sha256,
                    alias_checksum_sha256,
                    instrument_count,
                    sync_run_id
                FROM instrument_alias_snapshots
                WHERE
                    provider = :provider
                    AND mic = :mic
                    AND business_date = :business_date
                FOR SHARE
            """), {
                "provider": snapshot.provider,
                "mic": snapshot.mic,
                "business_date": snapshot.business_date,
            }).mappings().first()
            if existing is not None:
                if (
                    existing["tip_version"] != snapshot.tip_version
                    or existing["source_path"] != snapshot.source_path
                    or existing["file_checksum_sha256"].strip()
                    != snapshot.file_checksum_sha256
                    or int(existing["uncompressed_bytes"])
                    != snapshot.uncompressed_bytes
                    or existing["entitlement_id"] is None
                    or int(existing["entitlement_id"])
                    != entitlement_id
                    or int(existing["reference_snapshot_id"])
                    != reference_snapshot_id
                    or existing["reference_checksum_sha256"].strip()
                    != snapshot.reference_checksum_sha256
                    or existing["alias_checksum_sha256"].strip()
                    != snapshot.checksum_sha256
                    or int(existing["instrument_count"])
                    != len(snapshot.aliases)
                ):
                    raise MarketDataError(
                        "stored alias snapshot differs from input"
                    )
                stored_aliases = session.execute(text("""
                    SELECT
                        TRIM(isin) AS isin,
                        provider_symbol,
                        TRIM(trading_currency) AS trading_currency,
                        tradable_id,
                        source_id,
                        valid_from,
                        valid_to
                    FROM instrument_alias_snapshot_members
                    WHERE snapshot_id = :snapshot_id
                    ORDER BY TRIM(isin)
                """), {
                    "snapshot_id": existing["id"],
                }).all()
                expected_aliases = [
                    (
                        alias.isin,
                        alias.provider_symbol,
                        alias.trading_currency,
                        alias.tradable_id,
                        alias.source_id,
                        alias.valid_from,
                        alias.valid_to,
                    )
                    for alias in snapshot.aliases
                ]
                if stored_aliases != expected_aliases:
                    raise MarketDataError(
                        "stored alias snapshot membership differs from input"
                    )
                return NasdaqAliasSyncResult(
                    inserted=False,
                    aliases_inserted=0,
                    sync_run_id=int(existing["sync_run_id"]),
                    snapshot_id=int(existing["id"]),
                )

            active_date = session.execute(text("""
                SELECT snapshot.business_date
                FROM instrument_alias_snapshot_heads head
                JOIN instrument_alias_snapshots snapshot
                  ON snapshot.id = head.snapshot_id
                WHERE
                    head.provider = :provider
                    AND head.mic = :mic
                FOR SHARE OF head
            """), {
                "provider": snapshot.provider,
                "mic": snapshot.mic,
            }).scalar_one_or_none()
            if (
                active_date is not None
                and active_date >= snapshot.business_date
            ):
                raise MarketDataError(
                    "Nasdaq alias snapshot is older than active state"
                )

            sync_run_id = int(session.execute(text("""
                INSERT INTO data_sync_runs (
                    provider,
                    data_type,
                    started_at,
                    finished_at,
                    status,
                    records_received,
                    records_rejected,
                    records_inserted,
                    run_key
                )
                VALUES (
                    :provider,
                    'instrument-alias-reference-full',
                    NOW(),
                    NOW(),
                    'SUCCEEDED',
                    :instrument_count,
                    0,
                    :instrument_count,
                    :run_key
                )
                RETURNING id
            """), {
                "provider": snapshot.provider,
                "instrument_count": len(snapshot.aliases),
                "run_key": run_key,
            }).scalar_one())
            snapshot_id = int(session.execute(text("""
                INSERT INTO instrument_alias_snapshots (
                    provider,
                    mic,
                    business_date,
                    received_at,
                    tip_version,
                    source_path,
                    file_checksum_sha256,
                    content_encoding,
                    compressed_content,
                    uncompressed_bytes,
                    entitlement_id,
                    reference_snapshot_id,
                    reference_checksum_sha256,
                    alias_checksum_sha256,
                    instrument_count,
                    sync_run_id
                )
                VALUES (
                    :provider,
                    :mic,
                    :business_date,
                    :received_at,
                    :tip_version,
                    :source_path,
                    :file_checksum_sha256,
                    'gzip',
                    :compressed_content,
                    :uncompressed_bytes,
                    :entitlement_id,
                    :reference_snapshot_id,
                    :reference_checksum_sha256,
                    :alias_checksum_sha256,
                    :instrument_count,
                    :sync_run_id
                )
                RETURNING id
            """), {
                "provider": snapshot.provider,
                "mic": snapshot.mic,
                "business_date": snapshot.business_date,
                "received_at": snapshot.received_at,
                "tip_version": snapshot.tip_version,
                "source_path": snapshot.source_path,
                "file_checksum_sha256": snapshot.file_checksum_sha256,
                "compressed_content": compressed_content,
                "uncompressed_bytes": snapshot.uncompressed_bytes,
                "entitlement_id": entitlement_id,
                "reference_snapshot_id": reference_snapshot_id,
                "reference_checksum_sha256": (
                    snapshot.reference_checksum_sha256
                ),
                "alias_checksum_sha256": snapshot.checksum_sha256,
                "instrument_count": len(snapshot.aliases),
                "sync_run_id": sync_run_id,
            }).scalar_one())

            for alias in snapshot.aliases:
                session.execute(text("""
                    INSERT INTO instrument_alias_snapshot_members (
                        snapshot_id,
                        instrument_id,
                        isin,
                        provider_symbol,
                        trading_currency,
                        tradable_id,
                        source_id,
                        valid_from,
                        valid_to
                    )
                    VALUES (
                        :snapshot_id,
                        :instrument_id,
                        :isin,
                        :provider_symbol,
                        :trading_currency,
                        :tradable_id,
                        :source_id,
                        :valid_from,
                        :valid_to
                    )
                """), {
                    "snapshot_id": snapshot_id,
                    "instrument_id": instrument_ids[alias.isin],
                    "isin": alias.isin,
                    "provider_symbol": alias.provider_symbol,
                    "trading_currency": alias.trading_currency,
                    "tradable_id": alias.tradable_id,
                    "source_id": alias.source_id,
                    "valid_from": alias.valid_from,
                    "valid_to": alias.valid_to,
                })

            session.execute(text("""
                INSERT INTO instrument_alias_snapshot_heads (
                    provider,
                    mic,
                    snapshot_id
                )
                VALUES (:provider, :mic, :snapshot_id)
                ON CONFLICT (provider, mic) DO UPDATE SET
                    snapshot_id = EXCLUDED.snapshot_id
            """), {
                "provider": snapshot.provider,
                "mic": snapshot.mic,
                "snapshot_id": snapshot_id,
            })
            return NasdaqAliasSyncResult(
                inserted=True,
                aliases_inserted=len(snapshot.aliases),
                sync_run_id=sync_run_id,
                snapshot_id=snapshot_id,
            )

    def get_active_provider_aliases(
        self,
        *,
        provider: str,
        mic: str,
    ) -> tuple[NasdaqInstrumentAlias, ...]:
        """Return only the complete alias head for the latest FIRDS state."""
        if not isinstance(provider, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{1,99}",
            provider,
        ):
            raise MarketDataError("alias provider is invalid")
        if not isinstance(mic, str) or not re.fullmatch(
            r"[A-Z0-9]{4}",
            mic,
        ):
            raise MarketDataError("mic must be a four-character MIC")
        with self.Session() as session:
            snapshot = session.execute(text("""
                SELECT
                    alias_snapshot.id,
                    alias_snapshot.reference_snapshot_id,
                    alias_snapshot.instrument_count,
                    latest_reference.id AS latest_reference_snapshot_id
                FROM instrument_alias_snapshot_heads head
                JOIN instrument_alias_snapshots alias_snapshot
                  ON alias_snapshot.id = head.snapshot_id
                JOIN reference_data_entitlements entitlement
                  ON entitlement.id = alias_snapshot.entitlement_id
                JOIN LATERAL (
                    SELECT id
                    FROM reference_data_snapshots
                    WHERE provider = 'esma-firds'
                      AND mic = head.mic
                    ORDER BY snapshot_date DESC, id DESC
                    LIMIT 1
                ) latest_reference ON TRUE
                WHERE head.provider = :provider
                  AND head.mic = :mic
                  AND entitlement.status = 'VALIDATED'
                  AND entitlement.provider = head.provider
                  AND entitlement.mic = head.mic
                  AND entitlement.transport = 'SFTP'
                  AND entitlement.usage_scope =
                    'INTERNAL_ANALYSIS_AND_PAPER'
                  AND entitlement.external_distribution = FALSE
                  AND entitlement.raw_storage_allowed = TRUE
                  AND entitlement.derived_storage_allowed = TRUE
                  AND alias_snapshot.business_date <= (
                    NOW() AT TIME ZONE 'Europe/Stockholm'
                  )::date
                  AND alias_snapshot.received_at <= NOW()
                  AND (
                    NOW() AT TIME ZONE 'Europe/Stockholm'
                  )::date BETWEEN
                    entitlement.valid_from AND entitlement.valid_until
                  AND entitlement.legal_reviewed_at IS NOT NULL
                  AND entitlement.legal_reviewed_at <= NOW()
                  AND entitlement.storage_reviewed_at IS NOT NULL
                  AND entitlement.storage_reviewed_at <= NOW()
                  AND entitlement.entitlement_verified_at IS NOT NULL
                  AND entitlement.entitlement_verified_at <= NOW()
                  AND entitlement.host_key_algorithm IS NOT NULL
                  AND entitlement.host_key_fingerprint_sha256 IS NOT NULL
            """), {
                "provider": provider,
                "mic": mic,
            }).mappings().first()
            if snapshot is None:
                return ()
            if (
                int(snapshot["reference_snapshot_id"])
                != int(snapshot["latest_reference_snapshot_id"])
            ):
                raise MarketDataError(
                    "active provider aliases are stale against FIRDS"
                )
            rows = session.execute(text("""
                SELECT
                    TRIM(isin) AS isin,
                    provider_symbol,
                    TRIM(trading_currency) AS trading_currency,
                    tradable_id,
                    source_id,
                    valid_from,
                    valid_to
                FROM instrument_alias_snapshot_members
                WHERE snapshot_id = :snapshot_id
                ORDER BY TRIM(isin)
            """), {
                "snapshot_id": snapshot["id"],
            }).mappings().all()
            if len(rows) != int(snapshot["instrument_count"]):
                raise MarketDataError(
                    "active provider alias membership is incomplete"
                )
            return tuple(
                NasdaqInstrumentAlias(
                    isin=row["isin"],
                    provider_symbol=row["provider_symbol"],
                    trading_currency=row["trading_currency"],
                    tradable_id=row["tradable_id"],
                    source_id=row["source_id"],
                    valid_from=row["valid_from"],
                    valid_to=row["valid_to"],
                )
                for row in rows
            )

    @staticmethod
    def _validate_reference_snapshot(
        *,
        provider: str,
        mic: str,
        snapshot_date: date,
        received_at: datetime,
        archives: Sequence[MarketDataArchive],
        instruments: Sequence[InstrumentRecord],
    ) -> None:
        if not isinstance(provider, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{1,99}",
            provider,
        ):
            raise MarketDataError("reference provider is invalid")
        if not isinstance(mic, str) or not re.fullmatch(
            r"[A-Z0-9]{4}",
            mic,
        ):
            raise MarketDataError("mic must be a four-character MIC")
        if not isinstance(snapshot_date, date):
            raise MarketDataError("snapshot_date must be a date")
        if (
            not isinstance(received_at, datetime)
            or received_at.tzinfo is None
            or received_at.utcoffset() is None
        ):
            raise MarketDataError("received_at must be timezone-aware")
        if snapshot_date > received_at.date():
            raise MarketDataError("snapshot_date cannot be in the future")
        if not archives or not instruments:
            raise MarketDataError(
                "reference snapshot requires files and instruments"
            )
        if len({archive.file_name for archive in archives}) != len(archives):
            raise MarketDataError("reference files must be unique")
        for archive in archives:
            if (
                archive.provider != provider
                or archive.data_type != "full-equity-reference"
                or archive.report_minute.date() != snapshot_date
            ):
                raise MarketDataError(
                    "reference archive does not match snapshot"
                )
        if len({instrument.isin for instrument in instruments}) != len(
            instruments
        ):
            raise MarketDataError("reference instruments must be unique")
        allowed_types = {
            "COMMON_STOCK",
            "PREFERRED_STOCK",
            "DEPOSITARY_RECEIPT",
        }
        for instrument in instruments:
            if (
                instrument.source != provider
                or instrument.mic != mic
                or instrument.status != "ACTIVE"
                or not instrument.primary_listing
                or instrument.instrument_type not in allowed_types
            ):
                raise MarketDataError(
                    "reference instrument is outside snapshot scope"
                )

    @staticmethod
    def _upsert_instruments(
        session,
        instruments: Sequence[InstrumentRecord],
        *,
        reference_snapshot_date: date | None,
    ) -> dict[str, int]:
        instrument_ids = {}
        for instrument in instruments:
            result = session.execute(text("""
                INSERT INTO instruments (
                    isin,
                    symbol,
                    name,
                    mic,
                    currency,
                    notional_currency,
                    instrument_type,
                    primary_listing,
                    status,
                    first_trade_date,
                    last_trade_date,
                    source,
                    cfi_code,
                    reference_snapshot_date,
                    source_updated_at,
                    synced_at
                )
                VALUES (
                    :isin,
                    :symbol,
                    :name,
                    :mic,
                    :notional_currency,
                    :notional_currency,
                    :instrument_type,
                    :primary_listing,
                    :status,
                    :first_trade_date,
                    :last_trade_date,
                    :source,
                    :cfi_code,
                    :reference_snapshot_date,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (isin) DO UPDATE SET
                    symbol = COALESCE(EXCLUDED.symbol, instruments.symbol),
                    name = EXCLUDED.name,
                    mic = EXCLUDED.mic,
                    currency = EXCLUDED.notional_currency,
                    notional_currency = EXCLUDED.notional_currency,
                    instrument_type = EXCLUDED.instrument_type,
                    primary_listing = EXCLUDED.primary_listing,
                    status = EXCLUDED.status,
                    first_trade_date = EXCLUDED.first_trade_date,
                    last_trade_date = EXCLUDED.last_trade_date,
                    source = EXCLUDED.source,
                    cfi_code = EXCLUDED.cfi_code,
                    reference_snapshot_date = COALESCE(
                        EXCLUDED.reference_snapshot_date,
                        instruments.reference_snapshot_date
                    ),
                    source_updated_at = NOW(),
                    synced_at = NOW()
                RETURNING id
            """), {
                "isin": instrument.isin,
                "symbol": instrument.symbol,
                "name": instrument.name,
                "mic": instrument.mic,
                "notional_currency": instrument.notional_currency,
                "instrument_type": instrument.instrument_type,
                "primary_listing": instrument.primary_listing,
                "status": instrument.status,
                "first_trade_date": instrument.first_trade_date,
                "last_trade_date": instrument.last_trade_date,
                "source": instrument.source,
                "cfi_code": instrument.cfi_code,
                "reference_snapshot_date": reference_snapshot_date,
            })
            instrument_ids[instrument.isin] = int(result.scalar_one())
        return instrument_ids

    def ensure_isin_company_mappings(
        self,
        *,
        reference_snapshot_id: int,
        reference_snapshot: InstrumentUniverseSnapshot,
    ) -> int:
        """Create truthful internal mappings when no exchange ticker is free.

        Existing official ticker mappings are preserved. Missing mappings use
        the ISIN itself as the stable internal instrument key; this must never
        be presented as an exchange ticker.
        """
        if (
            isinstance(reference_snapshot_id, bool)
            or not isinstance(reference_snapshot_id, int)
            or reference_snapshot_id <= 0
        ):
            raise MarketDataError(
                "reference_snapshot_id must be a positive integer"
            )
        if not isinstance(reference_snapshot, InstrumentUniverseSnapshot):
            raise TypeError(
                "reference_snapshot must be an InstrumentUniverseSnapshot"
            )
        if reference_snapshot.mic != "XSTO":
            raise MarketDataError("company mappings require an XSTO snapshot")

        with self.Session.begin() as session:
            snapshot = session.execute(text("""
                SELECT
                    snapshot.instrument_count,
                    COUNT(membership.instrument_id)::INTEGER
                        AS membership_count,
                    ARRAY_AGG(
                        BTRIM(instrument.isin)
                        ORDER BY BTRIM(instrument.isin)
                    ) FILTER (
                        WHERE instrument.id IS NOT NULL
                    ) AS instrument_isins
                FROM reference_data_snapshots snapshot
                LEFT JOIN reference_snapshot_instruments membership
                  ON membership.snapshot_id = snapshot.id
                LEFT JOIN instruments instrument
                  ON instrument.id = membership.instrument_id
                WHERE snapshot.id = :reference_snapshot_id
                  AND snapshot.mic = 'XSTO'
                GROUP BY snapshot.id
            """), {
                "reference_snapshot_id": reference_snapshot_id,
            }).mappings().first()
            expected = len(reference_snapshot.instruments)
            if (
                snapshot is None
                or int(snapshot["instrument_count"]) != expected
                or int(snapshot["membership_count"]) != expected
                or tuple(snapshot["instrument_isins"] or ())
                    != reference_snapshot.instrument_isins
            ):
                raise MarketDataError(
                    "company mapping reference snapshot is incomplete"
                )

            session.execute(text("""
                INSERT INTO companies (
                    ticker,
                    name,
                    sector,
                    industry,
                    description,
                    instrument_id,
                    updated_at
                )
                SELECT
                    UPPER(BTRIM(instrument.isin)),
                    instrument.name,
                    'Unclassified',
                    'Unclassified',
                    'Internal ISIN key for XSTO; the free delayed feed does '
                        || 'not supply an official exchange ticker.',
                    instrument.id,
                    NOW()
                FROM reference_snapshot_instruments membership
                JOIN instruments instrument
                  ON instrument.id = membership.instrument_id
                LEFT JOIN companies mapped
                  ON mapped.instrument_id = instrument.id
                WHERE membership.snapshot_id = :reference_snapshot_id
                  AND mapped.id IS NULL
                ON CONFLICT (ticker) DO UPDATE SET
                    name = EXCLUDED.name,
                    sector = COALESCE(companies.sector, EXCLUDED.sector),
                    industry = COALESCE(companies.industry, EXCLUDED.industry),
                    description = CASE
                        WHEN companies.instrument_id IS NULL
                        THEN EXCLUDED.description
                        ELSE companies.description
                    END,
                    instrument_id = CASE
                        WHEN companies.instrument_id IS NULL
                        THEN EXCLUDED.instrument_id
                        ELSE companies.instrument_id
                    END,
                    updated_at = NOW()
            """), {
                "reference_snapshot_id": reference_snapshot_id,
            })
            mapped_count = session.execute(text("""
                SELECT COUNT(DISTINCT company.instrument_id)::INTEGER
                FROM reference_snapshot_instruments membership
                JOIN companies company
                  ON company.instrument_id = membership.instrument_id
                WHERE membership.snapshot_id = :reference_snapshot_id
            """), {
                "reference_snapshot_id": reference_snapshot_id,
            }).scalar_one()
            if int(mapped_count) != expected:
                raise MarketDataError(
                    "XSTO company mapping is incomplete after ISIN fallback"
                )
            return int(mapped_count)

    def get_active_xsto_company_isins(self) -> tuple[str, ...]:
        """Return stable identifiers for active mapped XSTO companies."""
        with self.Session() as session:
            rows = session.execute(text("""
                SELECT instrument.isin
                FROM companies company
                JOIN instruments instrument
                  ON instrument.id = company.instrument_id
                WHERE instrument.mic = 'XSTO'
                  AND instrument.status = 'ACTIVE'
                ORDER BY instrument.isin
            """)).mappings().all()
        return tuple(row["isin"].strip().upper() for row in rows)

    def update_company_sectors(
        self,
        classifications: Mapping[str, SectorClassification],
        *,
        source: str,
        verified_at: datetime,
    ) -> int:
        """Apply exact-ISIN sectors without replacing curated classifications."""
        if (
            not isinstance(source, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,99}", source)
        ):
            raise MarketDataError("sector source is invalid")
        if (
            not isinstance(verified_at, datetime)
            or verified_at.tzinfo is None
            or verified_at.utcoffset() is None
        ):
            raise MarketDataError("sector verified_at must be timezone-aware")
        if not isinstance(classifications, Mapping) or not classifications:
            raise MarketDataError("sector classifications are empty")
        if len(classifications) > 2000:
            raise MarketDataError("sector classifications are too large")

        updated = 0
        with self.Session.begin() as session:
            for isin, classification in classifications.items():
                if (
                    not isinstance(classification, SectorClassification)
                    or isin != classification.isin
                    or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin)
                ):
                    raise MarketDataError(
                        "sector classification identity is invalid"
                    )
                result = session.execute(text("""
                    UPDATE companies company
                    SET
                        sector = :sector,
                        sector_source = :source,
                        sector_verified_at = :verified_at,
                        updated_at = NOW()
                    FROM instruments instrument
                    WHERE instrument.id = company.instrument_id
                      AND instrument.isin = :isin
                      AND instrument.mic = 'XSTO'
                      AND instrument.status = 'ACTIVE'
                      AND (
                          company.sector IS NULL
                          OR BTRIM(company.sector) = ''
                          OR company.sector = 'Unclassified'
                          OR company.sector_source = :source
                      )
                """), {
                    "isin": isin,
                    "sector": classification.sector,
                    "source": source,
                    "verified_at": verified_at,
                })
                updated += max(0, int(result.rowcount or 0))
        return updated

    def ensure_public_pretrade_contract(
        self,
        *,
        policy_evidence: NasdaqPublicPolicyEvidence,
        contract_key: str = "nasdaq-public-pretrade-xsto-v1",
    ) -> str:
        """Register verified public terms without claiming file acceptance."""
        contract_key = self._required_sync_text(
            contract_key,
            "contract_key",
        ).lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,99}", contract_key):
            raise MarketDataError("contract_key has invalid format")
        if not isinstance(policy_evidence, NasdaqPublicPolicyEvidence):
            raise TypeError(
                "policy_evidence must be NasdaqPublicPolicyEvidence"
            )
        if policy_evidence.terms_url != (
            "https://tradereports.nasdaq.com/shares/"
            "trade-reports/pre-trade"
        ):
            raise MarketDataError("public policy URL is not authorized")
        if not re.fullmatch(
            r"[0-9a-f]{64}",
            policy_evidence.checksum_sha256,
        ):
            raise MarketDataError("public policy checksum is invalid")
        if (
            policy_evidence.verified_at.tzinfo is None
            or policy_evidence.verified_at.utcoffset() is None
        ):
            raise MarketDataError("public policy verification time must be aware")

        contract_valid_until = policy_evidence.verified_at.date() + timedelta(
            days=365
        )
        with self.Session.begin() as session:
            inserted_contract_id = session.execute(text("""
                INSERT INTO market_data_provider_contracts (
                    contract_key,
                    provider,
                    product_name,
                    data_type,
                    mic,
                    delivery_mode,
                    transport,
                    nominal_delay_seconds,
                    max_transport_lag_seconds,
                    usage_scope,
                    non_display_category,
                    external_distribution,
                    reference_symbols_included,
                    terms_url,
                    status,
                    valid_from,
                    valid_until,
                    legal_reviewed_at,
                    transport_verified_at,
                    governance_evidence_status,
                    terms_checksum_sha256,
                    raw_storage_allowed,
                    derived_storage_allowed,
                    retention_policy,
                    proposed_by,
                    reviewed_by,
                    authorization_basis,
                    policy_verified_at,
                    policy_verified_by
                )
                VALUES (
                    :contract_key,
                    'nasdaq-nordic',
                    'Nasdaq Nordic MiFID delayed pre-trade equity',
                    'delayed-pre-trade-equity',
                    'XSTO',
                    'DELAYED_15M',
                    'PUBLIC_CSV',
                    900,
                    300,
                    'INTERNAL_ANALYSIS_AND_PAPER',
                    'NONE',
                    FALSE,
                    FALSE,
                    :terms_url,
                    'VALIDATED',
                    :valid_from,
                    :valid_until,
                    :legacy_review_timestamp,
                    :transport_verified_at,
                    'VERIFIED',
                    :terms_checksum_sha256,
                    FALSE,
                    TRUE,
                    :retention_policy,
                    'operator:codex',
                    'operator:codex',
                    'PUBLIC_NONCOMMERCIAL_TERMS',
                    :policy_verified_at,
                    'operator:codex'
                )
                ON CONFLICT (contract_key) DO NOTHING
                RETURNING id
            """), {
                "contract_key": contract_key,
                "terms_url": policy_evidence.terms_url,
                "valid_from": policy_evidence.verified_at.date(),
                "valid_until": contract_valid_until,
                "legacy_review_timestamp": policy_evidence.verified_at,
                "transport_verified_at": policy_evidence.verified_at,
                "terms_checksum_sha256": policy_evidence.checksum_sha256,
                "retention_policy": (
                    "Raw Nasdaq files are ephemeral and deleted after strict "
                    "parsing; derived order-book evidence is internal-only."
                ),
                "policy_verified_at": policy_evidence.verified_at,
            }).scalar_one_or_none()
            contract = session.execute(text("""
                SELECT
                    id,
                    provider,
                    data_type,
                    mic,
                    delivery_mode,
                    transport,
                    usage_scope,
                    external_distribution,
                    status,
                    valid_until,
                    governance_evidence_status,
                    terms_checksum_sha256,
                    raw_storage_allowed,
                    derived_storage_allowed,
                    authorization_basis
                FROM market_data_provider_contracts
                WHERE contract_key = :contract_key
                FOR SHARE
            """), {"contract_key": contract_key}).mappings().one()
            if (
                contract["provider"] != "nasdaq-nordic"
                or contract["data_type"] != "delayed-pre-trade-equity"
                or contract["mic"].strip() != "XSTO"
                or contract["delivery_mode"] != "DELAYED_15M"
                or contract["transport"] != "PUBLIC_CSV"
                or contract["usage_scope"]
                    != "INTERNAL_ANALYSIS_AND_PAPER"
                or contract["external_distribution"]
                or contract["status"] != "VALIDATED"
                or contract["governance_evidence_status"] != "VERIFIED"
                or contract["terms_checksum_sha256"].strip()
                    != policy_evidence.checksum_sha256
                or contract["raw_storage_allowed"]
                or not contract["derived_storage_allowed"]
                or contract["authorization_basis"]
                    != "PUBLIC_NONCOMMERCIAL_TERMS"
                or contract["valid_until"]
                    < policy_evidence.verified_at.date()
            ):
                raise MarketDataError(
                    "stored public contract conflicts with current policy"
                )
            if (
                inserted_contract_id is not None
                and int(inserted_contract_id) != int(contract["id"])
            ):
                raise MarketDataError(
                    "public contract identity is inconsistent"
                )
        return contract_key

    def ensure_public_pretrade_authorization(
        self,
        *,
        reference_snapshot_id: int,
        batch: TopOfBookBatch,
        policy_evidence: NasdaqPublicPolicyEvidence,
        contract_key: str = "nasdaq-public-pretrade-xsto-v1",
    ) -> str:
        """Bind one strictly parsed public file to published noncommercial terms."""
        contract_key = self._required_sync_text(
            contract_key,
            "contract_key",
        ).lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,99}", contract_key):
            raise MarketDataError("contract_key has invalid format")
        if (
            isinstance(reference_snapshot_id, bool)
            or not isinstance(reference_snapshot_id, int)
            or reference_snapshot_id <= 0
        ):
            raise MarketDataError(
                "reference_snapshot_id must be a positive integer"
            )
        if not isinstance(batch, TopOfBookBatch):
            raise TypeError("batch must be a TopOfBookBatch")
        if not isinstance(policy_evidence, NasdaqPublicPolicyEvidence):
            raise TypeError(
                "policy_evidence must be NasdaqPublicPolicyEvidence"
            )
        if (
            batch.provider != "nasdaq-nordic"
            or batch.data_type != "delayed-pre-trade-equity"
            or batch.mic != "XSTO"
            or not batch.updates
        ):
            raise MarketDataError(
                "public authorization requires a non-empty XSTO pre-trade batch"
            )
        if policy_evidence.verified_at > batch.received_at:
            raise MarketDataError(
                "public policy evidence cannot postdate the sample"
            )
        observed_delivery = math.ceil(
            (batch.received_at - batch.report_minute).total_seconds()
        )
        if not 900 <= observed_delivery <= 1200:
            raise MarketDataError(
                "public pre-trade sample is outside 15-20 minute delivery"
            )
        session_date = batch.report_minute.astimezone(
            ZoneInfo("Europe/Stockholm")
        ).date()
        acceptance_payload = {
            "schema_version": 1,
            "authorization_basis": "PUBLIC_NONCOMMERCIAL_TERMS",
            "policy_checksum_sha256": policy_evidence.checksum_sha256,
            "reference_checksum_sha256": batch.reference_checksum_sha256,
            "source_checksum_sha256": batch.source_checksum_sha256,
            "report_minute": batch.report_minute.isoformat(),
            "received_at": batch.received_at.isoformat(),
            "observed_delivery_seconds": observed_delivery,
            "sample_quote_count": len(batch.updates),
        }
        acceptance_checksum = hashlib.sha256(
            json.dumps(
                acceptance_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        contract_valid_until = policy_evidence.verified_at.date() + timedelta(
            days=365
        )
        validation_valid_until = batch.received_at + timedelta(days=30)

        with self.Session.begin() as session:
            frozen_universe, _instrument_ids = (
                self._pretrade_reference_universe(
                    session,
                    reference_snapshot_id=reference_snapshot_id,
                )
            )
            snapshot = session.execute(text("""
                SELECT
                    snapshot.id,
                    snapshot.mic,
                    snapshot.instrument_count,
                    snapshot.universe_checksum_sha256,
                    COUNT(membership.instrument_id)::INTEGER
                        AS membership_count,
                    COUNT(membership.instrument_id) FILTER (
                        WHERE membership.frozen_evidence_status = 'VERIFIED'
                    )::INTEGER AS verified_count,
                    COUNT(company.instrument_id)::INTEGER AS mapped_count
                FROM reference_data_snapshots snapshot
                LEFT JOIN reference_snapshot_instruments membership
                  ON membership.snapshot_id = snapshot.id
                LEFT JOIN companies company
                  ON company.instrument_id = membership.instrument_id
                WHERE snapshot.id = :reference_snapshot_id
                GROUP BY snapshot.id
            """), {
                "reference_snapshot_id": reference_snapshot_id,
            }).mappings().first()
            if (
                snapshot is None
                or snapshot["mic"].strip() != "XSTO"
                or frozen_universe.checksum_sha256
                    != batch.reference_checksum_sha256
                or frozen_universe.instrument_isins
                    != batch.instrument_isins
                or int(snapshot["instrument_count"])
                    != len(batch.instrument_isins)
                or int(snapshot["membership_count"])
                    != int(snapshot["instrument_count"])
                or int(snapshot["verified_count"])
                    != int(snapshot["instrument_count"])
                or int(snapshot["mapped_count"])
                    != int(snapshot["instrument_count"])
            ):
                raise MarketDataError(
                    "public authorization reference snapshot or mapping is incomplete"
                )
            expected_instruments = int(snapshot["instrument_count"])

            inserted_contract_id = session.execute(text("""
                INSERT INTO market_data_provider_contracts (
                    contract_key,
                    provider,
                    product_name,
                    data_type,
                    mic,
                    delivery_mode,
                    transport,
                    nominal_delay_seconds,
                    max_transport_lag_seconds,
                    usage_scope,
                    non_display_category,
                    external_distribution,
                    reference_symbols_included,
                    terms_url,
                    status,
                    valid_from,
                    valid_until,
                    legal_reviewed_at,
                    transport_verified_at,
                    governance_evidence_status,
                    terms_checksum_sha256,
                    raw_storage_allowed,
                    derived_storage_allowed,
                    retention_policy,
                    proposed_by,
                    reviewed_by,
                    authorization_basis,
                    policy_verified_at,
                    policy_verified_by
                )
                VALUES (
                    :contract_key,
                    'nasdaq-nordic',
                    'Nasdaq Nordic MiFID delayed pre-trade equity',
                    'delayed-pre-trade-equity',
                    'XSTO',
                    'DELAYED_15M',
                    'PUBLIC_CSV',
                    900,
                    300,
                    'INTERNAL_ANALYSIS_AND_PAPER',
                    'NONE',
                    FALSE,
                    FALSE,
                    :terms_url,
                    'VALIDATED',
                    :valid_from,
                    :valid_until,
                    :legacy_review_timestamp,
                    :transport_verified_at,
                    'VERIFIED',
                    :terms_checksum_sha256,
                    FALSE,
                    TRUE,
                    :retention_policy,
                    'operator:codex',
                    'operator:codex',
                    'PUBLIC_NONCOMMERCIAL_TERMS',
                    :policy_verified_at,
                    'operator:codex'
                )
                ON CONFLICT (contract_key) DO NOTHING
                RETURNING id
            """), {
                "contract_key": contract_key,
                "terms_url": policy_evidence.terms_url,
                "valid_from": policy_evidence.verified_at.date(),
                "valid_until": contract_valid_until,
                "legacy_review_timestamp": policy_evidence.verified_at,
                "transport_verified_at": batch.received_at,
                "terms_checksum_sha256": (
                    policy_evidence.checksum_sha256
                ),
                "retention_policy": (
                    "Raw Nasdaq files are ephemeral and deleted after strict "
                    "parsing; derived order-book evidence is internal-only."
                ),
                "policy_verified_at": policy_evidence.verified_at,
            }).scalar_one_or_none()
            contract = session.execute(text("""
                SELECT
                    id,
                    provider,
                    data_type,
                    mic,
                    delivery_mode,
                    transport,
                    usage_scope,
                    external_distribution,
                    status,
                    valid_until,
                    governance_evidence_status,
                    terms_checksum_sha256,
                    raw_storage_allowed,
                    derived_storage_allowed,
                    authorization_basis
                FROM market_data_provider_contracts
                WHERE contract_key = :contract_key
                FOR SHARE
            """), {"contract_key": contract_key}).mappings().one()
            if (
                contract["provider"] != "nasdaq-nordic"
                or contract["data_type"] != "delayed-pre-trade-equity"
                or contract["mic"].strip() != "XSTO"
                or contract["delivery_mode"] != "DELAYED_15M"
                or contract["transport"] != "PUBLIC_CSV"
                or contract["usage_scope"]
                    != "INTERNAL_ANALYSIS_AND_PAPER"
                or contract["external_distribution"]
                or contract["status"] != "VALIDATED"
                or contract["governance_evidence_status"] != "VERIFIED"
                or contract["terms_checksum_sha256"].strip()
                    != policy_evidence.checksum_sha256
                or contract["raw_storage_allowed"]
                or not contract["derived_storage_allowed"]
                or contract["authorization_basis"]
                    != "PUBLIC_NONCOMMERCIAL_TERMS"
                or contract["valid_until"] < batch.received_at.date()
            ):
                raise MarketDataError(
                    "stored public authorization conflicts with current policy"
                )
            contract_id = int(contract["id"])
            if (
                inserted_contract_id is not None
                and int(inserted_contract_id) != contract_id
            ):
                raise MarketDataError(
                    "public authorization identity is inconsistent"
                )

            session.execute(text("""
                INSERT INTO market_data_provider_validations (
                    contract_id,
                    status,
                    validated_at,
                    valid_until,
                    expected_instruments,
                    product_covered_instruments,
                    symbol_mapped_instruments,
                    sample_file_count,
                    sample_quote_count,
                    max_observed_delivery_seconds,
                    evidence_checksum_sha256,
                    validated_by,
                    notes,
                    governance_evidence_status,
                    reference_snapshot_id,
                    reference_checksum_sha256,
                    acceptance_session_count,
                    first_session_date,
                    last_session_date,
                    acceptance_checksum_sha256,
                    validation_basis
                )
                VALUES (
                    :contract_id,
                    'PASSED',
                    :validated_at,
                    :valid_until,
                    :expected_instruments,
                    :expected_instruments,
                    :expected_instruments,
                    1,
                    :sample_quote_count,
                    :max_observed_delivery_seconds,
                    :evidence_checksum_sha256,
                    'operator:codex',
                    :notes,
                    'VERIFIED',
                    :reference_snapshot_id,
                    :reference_checksum_sha256,
                    1,
                    :session_date,
                    :session_date,
                    :acceptance_checksum_sha256,
                    'CONTINUOUS_FILE_VALIDATION'
                )
                ON CONFLICT (
                    contract_id,
                    acceptance_checksum_sha256
                ) WHERE acceptance_checksum_sha256 IS NOT NULL
                DO NOTHING
            """), {
                "contract_id": contract_id,
                "validated_at": batch.received_at,
                "valid_until": validation_valid_until,
                "expected_instruments": expected_instruments,
                "sample_quote_count": len(batch.updates),
                "max_observed_delivery_seconds": observed_delivery,
                "evidence_checksum_sha256": acceptance_checksum,
                "notes": (
                    "Continuous strict schema, ISIN, MIC, currency, checksum "
                    "and delivery validation of a public Nasdaq file."
                ),
                "reference_snapshot_id": reference_snapshot_id,
                "reference_checksum_sha256": snapshot[
                    "universe_checksum_sha256"
                ].strip(),
                "session_date": session_date,
                "acceptance_checksum_sha256": acceptance_checksum,
            })
        return contract_key

    @staticmethod
    def _ready_pretrade_contract(
        session,
        *,
        contract_key: str,
        batch: TopOfBookBatch,
        expected_instruments: int,
    ) -> tuple[int, int, ProviderContract]:
        row = session.execute(text("""
            SELECT
                contract.id AS contract_id,
                contract.contract_key,
                contract.provider,
                contract.product_name,
                contract.data_type,
                contract.mic,
                contract.delivery_mode,
                contract.transport,
                contract.nominal_delay_seconds,
                contract.max_transport_lag_seconds,
                contract.usage_scope,
                contract.non_display_category,
                contract.external_distribution,
                contract.reference_symbols_included,
                contract.terms_url,
                contract.status AS contract_status,
                contract.valid_from AS contract_valid_from,
                contract.valid_until AS contract_valid_until,
                contract.legal_reviewed_at,
                contract.transport_verified_at,
                contract.authorization_basis,
                contract.policy_verified_at,
                contract.policy_verified_by,
                contract.governance_evidence_status
                    AS contract_governance_evidence_status,
                contract.terms_checksum_sha256,
                contract.raw_storage_allowed,
                contract.derived_storage_allowed,
                contract.retention_policy,
                contract.proposed_by,
                contract.reviewed_by,
                contract.revoked_at,
                contract.revoked_by,
                contract.revocation_reason,
                validation.id AS validation_id,
                validation.status AS validation_status,
                validation.validated_at,
                validation.valid_until AS validation_valid_until,
                validation.expected_instruments,
                validation.product_covered_instruments,
                validation.symbol_mapped_instruments,
                validation.sample_file_count,
                validation.sample_quote_count,
                validation.max_observed_delivery_seconds,
                validation.evidence_checksum_sha256,
                validation.validated_by,
                validation.validation_basis,
                validation.governance_evidence_status
                    AS validation_governance_evidence_status,
                validation.reference_snapshot_id,
                validation.reference_checksum_sha256,
                validation.acceptance_session_count,
                validation.first_session_date,
                validation.last_session_date,
                validation.acceptance_checksum_sha256
            FROM market_data_provider_contracts contract
            LEFT JOIN LATERAL (
                SELECT candidate.*
                FROM market_data_provider_validations candidate
                WHERE candidate.contract_id = contract.id
                ORDER BY candidate.validated_at DESC, candidate.id DESC
                LIMIT 1
            ) validation ON TRUE
            WHERE contract.contract_key = :contract_key
            FOR SHARE OF contract
        """), {
            "contract_key": contract_key,
        }).mappings().first()
        if row is None:
            raise MarketDataError("provider contract is missing")

        contract = ProviderContract(
            contract_key=row["contract_key"],
            provider=row["provider"],
            product_name=row["product_name"],
            data_type=row["data_type"],
            mic=row["mic"].strip(),
            delivery_mode=row["delivery_mode"],
            transport=row["transport"],
            nominal_delay_seconds=row["nominal_delay_seconds"],
            max_transport_lag_seconds=row[
                "max_transport_lag_seconds"
            ],
            usage_scope=row["usage_scope"],
            non_display_category=row["non_display_category"],
            external_distribution=row["external_distribution"],
            reference_symbols_included=row[
                "reference_symbols_included"
            ],
            terms_url=row["terms_url"],
            status=row["contract_status"],
            valid_from=row["contract_valid_from"],
            valid_until=row["contract_valid_until"],
            legal_reviewed_at=row["legal_reviewed_at"],
            transport_verified_at=row["transport_verified_at"],
            authorization_basis=row["authorization_basis"],
            policy_verified_at=row["policy_verified_at"],
            policy_verified_by=row["policy_verified_by"],
            governance_evidence_status=row[
                "contract_governance_evidence_status"
            ],
            terms_checksum_sha256=(
                row["terms_checksum_sha256"].strip()
                if row["terms_checksum_sha256"] is not None
                else None
            ),
            raw_storage_allowed=row["raw_storage_allowed"],
            derived_storage_allowed=row["derived_storage_allowed"],
            retention_policy=row["retention_policy"],
            proposed_by=row["proposed_by"],
            reviewed_by=row["reviewed_by"],
            revoked_at=row["revoked_at"],
            revoked_by=row["revoked_by"],
            revocation_reason=row["revocation_reason"],
        )
        validation = None
        if row["validation_status"] is not None:
            validation = ProviderValidation(
                contract_key=row["contract_key"],
                status=row["validation_status"],
                validated_at=row["validated_at"],
                valid_until=row["validation_valid_until"],
                expected_instruments=row["expected_instruments"],
                product_covered_instruments=row[
                    "product_covered_instruments"
                ],
                symbol_mapped_instruments=row[
                    "symbol_mapped_instruments"
                ],
                sample_file_count=row["sample_file_count"],
                sample_quote_count=row["sample_quote_count"],
                max_observed_delivery_seconds=row[
                    "max_observed_delivery_seconds"
                ],
                evidence_checksum_sha256=row[
                    "evidence_checksum_sha256"
                ].strip(),
                validated_by=row["validated_by"],
                validation_basis=row["validation_basis"],
                governance_evidence_status=row[
                    "validation_governance_evidence_status"
                ],
                reference_snapshot_id=row["reference_snapshot_id"],
                reference_checksum_sha256=(
                    row["reference_checksum_sha256"].strip()
                    if row["reference_checksum_sha256"] is not None
                    else None
                ),
                acceptance_session_count=row[
                    "acceptance_session_count"
                ],
                first_session_date=row["first_session_date"],
                last_session_date=row["last_session_date"],
                acceptance_checksum_sha256=(
                    row["acceptance_checksum_sha256"].strip()
                    if row["acceptance_checksum_sha256"] is not None
                    else None
                ),
            )
        ready = assert_provider_ready(
            contract,
            validation,
            now=batch.received_at,
            expected_instruments=expected_instruments,
        )
        if (
            ready.provider != batch.provider
            or ready.data_type != batch.data_type
            or ready.mic != batch.mic
            or batch.data_type not in {
                "delayed-pre-trade-equity",
                "realtime-equity-level-1",
            }
            or not batch.source.startswith(f"{ready.provider}-")
        ):
            raise MarketDataError(
                "pre-trade batch does not match provider contract"
            )
        observed_delivery = (
            batch.received_at - batch.report_minute
        ).total_seconds()
        if not (
            ready.nominal_delay_seconds
            <= observed_delivery
            <= (
                ready.nominal_delay_seconds
                + ready.max_transport_lag_seconds
            )
        ):
            raise MarketDataError(
                "pre-trade batch delivery is outside provider bounds"
            )
        assert row["validation_id"] is not None
        return (
            int(row["contract_id"]),
            int(row["validation_id"]),
            ready,
        )

    @staticmethod
    def _pretrade_reference_universe(
        session,
        *,
        reference_snapshot_id: int,
    ) -> tuple[InstrumentUniverseSnapshot, dict[str, int]]:
        snapshot = session.execute(text("""
            SELECT id, mic, instrument_count, received_at
            FROM reference_data_snapshots
            WHERE id = :reference_snapshot_id
            FOR SHARE
        """), {
            "reference_snapshot_id": reference_snapshot_id,
        }).mappings().first()
        if snapshot is None:
            raise MarketDataError("pre-trade reference snapshot is missing")
        rows = session.execute(text("""
            SELECT
                instrument.id AS instrument_id,
                membership.isin,
                membership.symbol,
                membership.name,
                membership.mic,
                membership.notional_currency,
                membership.instrument_type,
                membership.source,
                membership.primary_listing,
                membership.status,
                membership.first_trade_date,
                membership.last_trade_date,
                membership.cfi_code,
                membership.frozen_evidence_status
            FROM reference_snapshot_instruments membership
            JOIN instruments instrument
              ON instrument.id = membership.instrument_id
            WHERE membership.snapshot_id = :reference_snapshot_id
            ORDER BY membership.isin
        """), {
            "reference_snapshot_id": reference_snapshot_id,
        }).mappings().all()
        if (
            len(rows) != int(snapshot["instrument_count"])
            or any(
                row["frozen_evidence_status"] != "VERIFIED"
                for row in rows
            )
        ):
            raise MarketDataError(
                "pre-trade reference snapshot membership is incomplete "
                "or not historically verified"
            )
        instruments = tuple(
            InstrumentRecord(
                isin=row["isin"].strip(),
                symbol=row["symbol"],
                name=row["name"],
                mic=row["mic"].strip(),
                currency=row["notional_currency"].strip(),
                instrument_type=row["instrument_type"],
                source=row["source"],
                primary_listing=row["primary_listing"],
                status=row["status"],
                first_trade_date=row["first_trade_date"],
                last_trade_date=row["last_trade_date"],
                cfi_code=(
                    row["cfi_code"].strip()
                    if row["cfi_code"] is not None
                    else None
                ),
            )
            for row in rows
        )
        universe = InstrumentUniverseSnapshot(
            mic=snapshot["mic"].strip(),
            instruments=instruments,
        )
        instrument_ids = {
            row["isin"].strip(): int(row["instrument_id"])
            for row in rows
        }
        return universe, instrument_ids

    @staticmethod
    def _load_top_of_book_snapshot_session(
        session,
        *,
        provider_contract_id: int,
    ) -> TopOfBookSnapshot | None:
        cursor_row = session.execute(text("""
            SELECT
                batch.id AS batch_id,
                cursor.source,
                cursor.mic,
                cursor.covered_through,
                cursor.received_at,
                cursor.reference_checksum_sha256
            FROM pre_trade_batches batch
            JOIN pre_trade_stream_cursors cursor
              ON cursor.batch_id = batch.id
            WHERE batch.provider_contract_id = :provider_contract_id
            ORDER BY batch.report_minute DESC, batch.id DESC
            LIMIT 1
        """), {
            "provider_contract_id": provider_contract_id,
        }).mappings().first()
        if cursor_row is None:
            return None

        state_rows = session.execute(text("""
            SELECT
                isin,
                source,
                mic,
                reference_checksum_sha256,
                currency,
                bid_price,
                bid_quantity,
                bid_order_count,
                bid_event_time,
                bid_publication_time,
                ask_price,
                ask_quantity,
                ask_order_count,
                ask_event_time,
                ask_publication_time,
                covered_through,
                received_at,
                trading_system,
                trading_phase
            FROM pre_trade_book_states
            WHERE batch_id = :batch_id
            ORDER BY mic, isin, source
        """), {
            "batch_id": cursor_row["batch_id"],
        }).mappings().all()
        states = tuple(
            TopOfBookState(
                isin=row["isin"].strip(),
                mic=row["mic"].strip(),
                source=row["source"],
                reference_checksum_sha256=row[
                    "reference_checksum_sha256"
                ].strip(),
                currency=row["currency"].strip(),
                bid_price=row["bid_price"],
                bid_quantity=row["bid_quantity"],
                bid_order_count=row["bid_order_count"],
                bid_event_time=row["bid_event_time"],
                bid_publication_time=row["bid_publication_time"],
                ask_price=row["ask_price"],
                ask_quantity=row["ask_quantity"],
                ask_order_count=row["ask_order_count"],
                ask_event_time=row["ask_event_time"],
                ask_publication_time=row["ask_publication_time"],
                covered_through=row["covered_through"],
                received_at=row["received_at"],
                trading_system=row["trading_system"],
                trading_phase=row["trading_phase"],
            )
            for row in state_rows
        )
        cursor = TopOfBookStreamCursor(
            source=cursor_row["source"],
            mic=cursor_row["mic"].strip(),
            covered_through=cursor_row["covered_through"],
            received_at=cursor_row["received_at"],
            reference_checksum_sha256=cursor_row[
                "reference_checksum_sha256"
            ].strip(),
        )
        return TopOfBookSnapshot(states=states, cursor=cursor)

    def persist_top_of_book_batch(
        self,
        *,
        contract_key: str,
        reference_snapshot_id: int,
        batch: TopOfBookBatch,
        snapshot: TopOfBookSnapshot,
        stream_reset_id: int | None = None,
    ) -> PreTradeIngestionResult:
        """Persist one complete parser-issued minute exactly once."""
        contract_key = self._required_sync_text(
            contract_key,
            "contract_key",
        ).lower()
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{1,99}",
            contract_key,
        ):
            raise MarketDataError("contract_key has invalid format")
        if (
            isinstance(reference_snapshot_id, bool)
            or not isinstance(reference_snapshot_id, int)
            or reference_snapshot_id <= 0
        ):
            raise MarketDataError(
                "reference_snapshot_id must be a positive integer"
            )
        if not isinstance(batch, TopOfBookBatch):
            raise TypeError("batch must be a TopOfBookBatch")
        if not isinstance(snapshot, TopOfBookSnapshot):
            raise TypeError("snapshot must be a TopOfBookSnapshot")
        if (
            stream_reset_id is not None
            and (
                isinstance(stream_reset_id, bool)
                or not isinstance(stream_reset_id, int)
                or stream_reset_id <= 0
            )
        ):
            raise MarketDataError(
                "stream_reset_id must be a positive integer"
            )
        if (
            snapshot.cursor.source != batch.source
            or snapshot.cursor.mic != batch.mic
            or snapshot.cursor.covered_through != batch.covered_through
            or snapshot.cursor.received_at != batch.received_at
            or snapshot.cursor.reference_checksum_sha256
                != batch.reference_checksum_sha256
        ):
            raise MarketDataError(
                "pre-trade snapshot cursor does not match the batch"
            )
        payload_checksum = _top_of_book_payload_checksum(batch, snapshot)

        with self.Session.begin() as session:
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:lock_key, 0))"
                ),
                {"lock_key": f"pre-trade:{contract_key}"},
            )
            universe, instrument_ids = self._pretrade_reference_universe(
                session,
                reference_snapshot_id=reference_snapshot_id,
            )
            if (
                universe.checksum_sha256
                != batch.reference_checksum_sha256
                or universe.instrument_isins != batch.instrument_isins
            ):
                raise MarketDataError(
                    "pre-trade batch reference checksum or membership "
                    "does not match frozen reference evidence"
                )
            (
                provider_contract_id,
                provider_validation_id,
                _contract,
            ) = (
                self._ready_pretrade_contract(
                    session,
                    contract_key=contract_key,
                    batch=batch,
                    expected_instruments=len(universe.instruments),
                )
            )

            existing_rows = session.execute(text("""
                SELECT
                    id,
                    reference_snapshot_id,
                    provider,
                    data_type,
                    source,
                    mic,
                    file_name,
                    source_url,
                    source_checksum_sha256,
                    reference_checksum_sha256,
                    payload_checksum_sha256,
                    stream_reset_id,
                    report_minute,
                    covered_through,
                    received_at,
                    input_row_count,
                    update_count,
                    state_count
                FROM pre_trade_batches
                WHERE
                    provider_contract_id = :provider_contract_id
                    AND (
                        report_minute = :report_minute
                        OR file_name = :file_name
                    )
                FOR SHARE
            """), {
                "provider_contract_id": provider_contract_id,
                "report_minute": batch.report_minute,
                "file_name": batch.file_name,
            }).mappings().all()
            if existing_rows:
                if len(existing_rows) != 1:
                    raise MarketDataError(
                        "stored pre-trade file identities conflict"
                    )
                existing = existing_rows[0]
                exact_replay = (
                    int(existing["reference_snapshot_id"])
                    == reference_snapshot_id
                    and existing["provider"] == batch.provider
                    and existing["data_type"] == batch.data_type
                    and existing["source"] == batch.source
                    and existing["mic"].strip() == batch.mic
                    and existing["file_name"] == batch.file_name
                    and existing["source_url"] == batch.source_url
                    and existing["source_checksum_sha256"].strip()
                        == batch.source_checksum_sha256
                    and existing["reference_checksum_sha256"].strip()
                        == batch.reference_checksum_sha256
                    and existing["payload_checksum_sha256"].strip()
                        == payload_checksum
                    and existing["stream_reset_id"] == stream_reset_id
                    and existing["report_minute"] == batch.report_minute
                    and existing["covered_through"]
                        == batch.covered_through
                    and existing["received_at"] == batch.received_at
                    and int(existing["input_row_count"])
                        == batch.input_row_count
                    and int(existing["update_count"])
                        == len(batch.updates)
                    and int(existing["state_count"])
                        == len(snapshot.states)
                )
                if not exact_replay:
                    raise MarketDataError(
                        "pre-trade replay conflicts with stored evidence"
                    )
                return PreTradeIngestionResult(
                    batch_id=int(existing["id"]),
                    inserted=False,
                    updates_inserted=0,
                    states_inserted=0,
                )

            previous = self._load_top_of_book_snapshot_session(
                session,
                provider_contract_id=provider_contract_id,
            )
            if stream_reset_id is not None:
                reset_matches = session.execute(text("""
                    SELECT COUNT(*)
                    FROM pre_trade_stream_resets
                    WHERE
                        id = :stream_reset_id
                        AND provider_contract_id = :provider_contract_id
                        AND reference_snapshot_id = :reference_snapshot_id
                        AND source = :source
                        AND mic = :mic
                        AND next_report_minute = :report_minute
                        AND next_file_name = :file_name
                        AND next_source_checksum_sha256
                            = :source_checksum_sha256
                        AND next_reference_checksum_sha256
                            = :reference_checksum_sha256
                        AND authorized_at = :received_at
                """), {
                    "stream_reset_id": stream_reset_id,
                    "provider_contract_id": provider_contract_id,
                    "reference_snapshot_id": reference_snapshot_id,
                    "source": batch.source,
                    "mic": batch.mic,
                    "report_minute": batch.report_minute,
                    "file_name": batch.file_name,
                    "source_checksum_sha256": (
                        batch.source_checksum_sha256
                    ),
                    "reference_checksum_sha256": (
                        batch.reference_checksum_sha256
                    ),
                    "received_at": batch.received_at,
                }).scalar_one()
                if int(reset_matches) != 1:
                    raise MarketDataError(
                        "pre-trade stream reset evidence is missing or mismatched"
                    )
            expected_snapshot = apply_top_of_book_batch(
                previous=(
                    ()
                    if stream_reset_id is not None
                    else previous if previous is not None else ()
                ),
                batch=batch,
            )
            if expected_snapshot != snapshot:
                raise MarketDataError(
                    "pre-trade snapshot does not match ordered batch reduction"
                )

            batch_id = int(session.execute(text("""
                INSERT INTO pre_trade_batches (
                    provider_contract_id,
                    provider_validation_id,
                    reference_snapshot_id,
                    provider,
                    data_type,
                    source,
                    mic,
                    file_name,
                    source_url,
                    source_checksum_sha256,
                    reference_checksum_sha256,
                    payload_checksum_sha256,
                    stream_reset_id,
                    report_minute,
                    covered_through,
                    received_at,
                    input_row_count,
                    update_count,
                    state_count
                )
                VALUES (
                    :provider_contract_id,
                    :provider_validation_id,
                    :reference_snapshot_id,
                    :provider,
                    :data_type,
                    :source,
                    :mic,
                    :file_name,
                    :source_url,
                    :source_checksum_sha256,
                    :reference_checksum_sha256,
                    :payload_checksum_sha256,
                    :stream_reset_id,
                    :report_minute,
                    :covered_through,
                    :received_at,
                    :input_row_count,
                    :update_count,
                    :state_count
                )
                RETURNING id
            """), {
                "provider_contract_id": provider_contract_id,
                "provider_validation_id": provider_validation_id,
                "reference_snapshot_id": reference_snapshot_id,
                "provider": batch.provider,
                "data_type": batch.data_type,
                "source": batch.source,
                "mic": batch.mic,
                "file_name": batch.file_name,
                "source_url": batch.source_url,
                "source_checksum_sha256": (
                    batch.source_checksum_sha256
                ),
                "reference_checksum_sha256": (
                    batch.reference_checksum_sha256
                ),
                "payload_checksum_sha256": payload_checksum,
                "stream_reset_id": stream_reset_id,
                "report_minute": batch.report_minute,
                "covered_through": batch.covered_through,
                "received_at": batch.received_at,
                "input_row_count": batch.input_row_count,
                "update_count": len(batch.updates),
                "state_count": len(snapshot.states),
            }).scalar_one())

            if batch.updates:
                session.execute(
                    text("""
                        INSERT INTO pre_trade_updates (
                            batch_id,
                            instrument_id,
                            isin,
                            source_sequence,
                            side,
                            event_time,
                            publication_time,
                            received_at,
                            source,
                            mic,
                            currency,
                            price,
                            quantity,
                            order_count,
                            trading_system,
                            trading_phase
                        )
                        VALUES (
                            :batch_id,
                            :instrument_id,
                            :isin,
                            :source_sequence,
                            :side,
                            :event_time,
                            :publication_time,
                            :received_at,
                            :source,
                            :mic,
                            :currency,
                            :price,
                            :quantity,
                            :order_count,
                            :trading_system,
                            :trading_phase
                        )
                    """),
                    [
                        {
                            "batch_id": batch_id,
                            "instrument_id": instrument_ids[update.isin],
                            "isin": update.isin,
                            "source_sequence": update.source_sequence,
                            "side": update.side,
                            "event_time": update.event_time,
                            "publication_time": update.publication_time,
                            "received_at": update.received_at,
                            "source": update.source,
                            "mic": update.mic,
                            "currency": update.currency,
                            "price": update.price,
                            "quantity": update.quantity,
                            "order_count": update.order_count,
                            "trading_system": update.trading_system,
                            "trading_phase": update.trading_phase,
                        }
                        for update in batch.updates
                    ],
                )

            if snapshot.states:
                session.execute(
                    text("""
                        INSERT INTO pre_trade_book_states (
                            batch_id,
                            instrument_id,
                            isin,
                            source,
                            mic,
                            reference_checksum_sha256,
                            currency,
                            bid_price,
                            bid_quantity,
                            bid_order_count,
                            bid_event_time,
                            bid_publication_time,
                            ask_price,
                            ask_quantity,
                            ask_order_count,
                            ask_event_time,
                            ask_publication_time,
                            covered_through,
                            received_at,
                            trading_system,
                            trading_phase
                        )
                        VALUES (
                            :batch_id,
                            :instrument_id,
                            :isin,
                            :source,
                            :mic,
                            :reference_checksum_sha256,
                            :currency,
                            :bid_price,
                            :bid_quantity,
                            :bid_order_count,
                            :bid_event_time,
                            :bid_publication_time,
                            :ask_price,
                            :ask_quantity,
                            :ask_order_count,
                            :ask_event_time,
                            :ask_publication_time,
                            :covered_through,
                            :received_at,
                            :trading_system,
                            :trading_phase
                        )
                    """),
                    [
                        {
                            "batch_id": batch_id,
                            "instrument_id": instrument_ids[state.isin],
                            "isin": state.isin,
                            "source": state.source,
                            "mic": state.mic,
                            "reference_checksum_sha256": (
                                state.reference_checksum_sha256
                            ),
                            "currency": state.currency,
                            "bid_price": state.bid_price,
                            "bid_quantity": state.bid_quantity,
                            "bid_order_count": state.bid_order_count,
                            "bid_event_time": state.bid_event_time,
                            "bid_publication_time": (
                                state.bid_publication_time
                            ),
                            "ask_price": state.ask_price,
                            "ask_quantity": state.ask_quantity,
                            "ask_order_count": state.ask_order_count,
                            "ask_event_time": state.ask_event_time,
                            "ask_publication_time": (
                                state.ask_publication_time
                            ),
                            "covered_through": state.covered_through,
                            "received_at": state.received_at,
                            "trading_system": state.trading_system,
                            "trading_phase": state.trading_phase,
                        }
                        for state in snapshot.states
                    ],
                )

            session.execute(text("""
                INSERT INTO pre_trade_stream_cursors (
                    batch_id,
                    source,
                    mic,
                    covered_through,
                    received_at,
                    reference_checksum_sha256
                )
                VALUES (
                    :batch_id,
                    :source,
                    :mic,
                    :covered_through,
                    :received_at,
                    :reference_checksum_sha256
                )
            """), {
                "batch_id": batch_id,
                "source": snapshot.cursor.source,
                "mic": snapshot.cursor.mic,
                "covered_through": snapshot.cursor.covered_through,
                "received_at": snapshot.cursor.received_at,
                "reference_checksum_sha256": (
                    snapshot.cursor.reference_checksum_sha256
                ),
            })

            event_times = [update.event_time for update in batch.updates]
            session.execute(text("""
                INSERT INTO data_sync_runs (
                    provider,
                    data_type,
                    started_at,
                    finished_at,
                    status,
                    records_received,
                    records_rejected,
                    records_inserted,
                    run_key,
                    first_event_time,
                    last_event_time,
                    gap_count
                )
                VALUES (
                    :provider,
                    :data_type,
                    :started_at,
                    NOW(),
                    'SUCCEEDED',
                    :records_received,
                    0,
                    :records_inserted,
                    :run_key,
                    :first_event_time,
                    :last_event_time,
                    0
                )
                ON CONFLICT (run_key)
                WHERE run_key IS NOT NULL
                DO NOTHING
            """), {
                "provider": batch.provider,
                "data_type": batch.data_type,
                "started_at": batch.received_at,
                "records_received": batch.input_row_count,
                "records_inserted": len(batch.updates),
                "run_key": (
                    f"pretrade:{batch.provider}:{batch.file_name}:"
                    f"{batch.source_checksum_sha256[:16]}"
                ),
                "first_event_time": min(event_times) if event_times else None,
                "last_event_time": max(event_times) if event_times else None,
            })

            return PreTradeIngestionResult(
                batch_id=batch_id,
                inserted=True,
                updates_inserted=len(batch.updates),
                states_inserted=len(snapshot.states),
            )

    def load_latest_top_of_book_snapshot(
        self,
        *,
        contract_key: str,
    ) -> TopOfBookSnapshot | None:
        """Reload the latest complete order-book cursor and state."""
        contract_key = self._required_sync_text(
            contract_key,
            "contract_key",
        ).lower()
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{1,99}",
            contract_key,
        ):
            raise MarketDataError("contract_key has invalid format")
        with self.Session() as session:
            provider_contract_id = session.execute(text("""
                SELECT id
                FROM market_data_provider_contracts
                WHERE contract_key = :contract_key
            """), {
                "contract_key": contract_key,
            }).scalar()
            if provider_contract_id is None:
                raise MarketDataError("provider contract is missing")
            return self._load_top_of_book_snapshot_session(
                session,
                provider_contract_id=int(provider_contract_id),
            )

    def authorize_top_of_book_stream_reset(
        self,
        *,
        contract_key: str,
        reference_snapshot_id: int,
        batch: TopOfBookBatch,
    ) -> int:
        """Authorize one checksum-bound reseed after a stale stream gap."""
        contract_key = self._required_sync_text(
            contract_key,
            "contract_key",
        ).lower()
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{1,99}",
            contract_key,
        ):
            raise MarketDataError("contract_key has invalid format")
        if (
            isinstance(reference_snapshot_id, bool)
            or not isinstance(reference_snapshot_id, int)
            or reference_snapshot_id <= 0
        ):
            raise MarketDataError(
                "reference_snapshot_id must be a positive integer"
            )
        if not isinstance(batch, TopOfBookBatch):
            raise TypeError("batch must be a TopOfBookBatch")

        with self.Session.begin() as session:
            contract = session.execute(text("""
                SELECT
                    id,
                    mic,
                    status,
                    nominal_delay_seconds,
                    max_transport_lag_seconds
                FROM market_data_provider_contracts
                WHERE contract_key = :contract_key
                FOR SHARE
            """), {
                "contract_key": contract_key,
            }).mappings().first()
            if contract is None or contract["status"] != "VALIDATED":
                raise MarketDataError(
                    "provider contract is unavailable for stream reset"
                )
            provider_contract_id = int(contract["id"])
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:lock_key, 0))"
                ),
                {"lock_key": f"pre-trade:{contract_key}"},
            )
            previous = session.execute(text("""
                SELECT
                    id,
                    source,
                    mic,
                    covered_through,
                    received_at
                FROM pre_trade_batches
                WHERE provider_contract_id = :provider_contract_id
                ORDER BY report_minute DESC, id DESC
                LIMIT 1
                FOR SHARE
            """), {
                "provider_contract_id": provider_contract_id,
            }).mappings().first()
            reference_universe, _instrument_ids = (
                self._pretrade_reference_universe(
                    session,
                    reference_snapshot_id=reference_snapshot_id,
                )
            )
            reference_checksum = reference_universe.checksum_sha256
            observed_delivery = math.ceil(
                (batch.received_at - batch.report_minute).total_seconds()
            )
            reset_checks = {
                "previous_batch": previous is not None,
                "reference_snapshot": reference_checksum is not None,
                "source": (
                    previous is not None
                    and previous["source"] == batch.source
                ),
                "mic": (
                    previous is not None
                    and previous["mic"].strip() == batch.mic
                    and contract["mic"].strip() == batch.mic
                ),
                "forward_gap": (
                    previous is not None
                    and batch.report_minute > previous["covered_through"]
                ),
                "received_at": (
                    previous is not None
                    and batch.received_at >= previous["received_at"]
                ),
                "reference_checksum": (
                    reference_checksum is not None
                    and reference_checksum.strip()
                        == batch.reference_checksum_sha256
                ),
                "delivery_window": (
                    observed_delivery
                    >= int(contract["nominal_delay_seconds"])
                    and observed_delivery <= (
                        int(contract["nominal_delay_seconds"])
                        + int(contract["max_transport_lag_seconds"])
                    )
                ),
            }
            failed_checks = sorted(
                name
                for name, passed in reset_checks.items()
                if not passed
            )
            if failed_checks:
                raise MarketDataError(
                    "pre-trade stream reset evidence is not runtime-valid: "
                    + ", ".join(failed_checks)
                )

            evidence_payload = {
                "schema_version": 1,
                "provider_contract_id": provider_contract_id,
                "previous_batch_id": int(previous["id"]),
                "reference_snapshot_id": reference_snapshot_id,
                "source": batch.source,
                "mic": batch.mic,
                "next_report_minute": batch.report_minute.isoformat(),
                "next_file_name": batch.file_name,
                "next_source_checksum_sha256": (
                    batch.source_checksum_sha256
                ),
                "next_reference_checksum_sha256": (
                    batch.reference_checksum_sha256
                ),
                "reason": "STALE_CURSOR_RECOVERY",
                "authorized_at": batch.received_at.isoformat(),
                "authorized_by": "operator:market-sync",
            }
            evidence_checksum = hashlib.sha256(
                json.dumps(
                    evidence_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            ).hexdigest()
            inserted_id = session.execute(text("""
                INSERT INTO pre_trade_stream_resets (
                    provider_contract_id,
                    previous_batch_id,
                    reference_snapshot_id,
                    source,
                    mic,
                    next_report_minute,
                    next_file_name,
                    next_source_checksum_sha256,
                    next_reference_checksum_sha256,
                    reason,
                    authorized_at,
                    authorized_by,
                    evidence_checksum_sha256
                )
                VALUES (
                    :provider_contract_id,
                    :previous_batch_id,
                    :reference_snapshot_id,
                    :source,
                    :mic,
                    :next_report_minute,
                    :next_file_name,
                    :next_source_checksum_sha256,
                    :next_reference_checksum_sha256,
                    'STALE_CURSOR_RECOVERY',
                    :authorized_at,
                    'operator:market-sync',
                    :evidence_checksum_sha256
                )
                ON CONFLICT (
                    provider_contract_id,
                    next_report_minute
                ) DO NOTHING
                RETURNING id
            """), {
                **evidence_payload,
                "evidence_checksum_sha256": evidence_checksum,
            }).scalar_one_or_none()
            stored = session.execute(text("""
                SELECT
                    id,
                    previous_batch_id,
                    reference_snapshot_id,
                    source,
                    mic,
                    next_file_name,
                    next_source_checksum_sha256,
                    next_reference_checksum_sha256,
                    reason,
                    authorized_at,
                    authorized_by,
                    evidence_checksum_sha256
                FROM pre_trade_stream_resets
                WHERE
                    provider_contract_id = :provider_contract_id
                    AND next_report_minute = :next_report_minute
                FOR SHARE
            """), {
                "provider_contract_id": provider_contract_id,
                "next_report_minute": batch.report_minute,
            }).mappings().one()
            if (
                int(stored["previous_batch_id"]) != int(previous["id"])
                or int(stored["reference_snapshot_id"])
                    != reference_snapshot_id
                or stored["source"] != batch.source
                or stored["mic"].strip() != batch.mic
                or stored["next_file_name"] != batch.file_name
                or stored["next_source_checksum_sha256"].strip()
                    != batch.source_checksum_sha256
                or stored["next_reference_checksum_sha256"].strip()
                    != batch.reference_checksum_sha256
                or stored["reason"] != "STALE_CURSOR_RECOVERY"
                or stored["authorized_at"] != batch.received_at
                or stored["authorized_by"] != "operator:market-sync"
                or stored["evidence_checksum_sha256"].strip()
                    != evidence_checksum
            ):
                raise MarketDataError(
                    "stored pre-trade stream reset evidence conflicts"
                )
            reset_id = int(stored["id"])
            if (
                inserted_id is not None
                and int(inserted_id) != reset_id
            ):
                raise MarketDataError(
                    "pre-trade stream reset identity is inconsistent"
                )
            return reset_id

    def save_market_quotes(
        self,
        quotes: Sequence[QuoteRecord],
    ) -> int:
        """Store validated quotes idempotently by source and event time."""
        with self.Session.begin() as session:
            return self._insert_market_quotes(
                session,
                quotes,
                data_file_id=None,
            )

    def save_market_index_levels(
        self,
        levels: Sequence[IndexLevelRecord],
    ) -> int:
        """Store immutable index levels bound to explicit provider contracts."""
        inserted = 0
        with self.Session.begin() as session:
            for level in levels:
                if not isinstance(level, IndexLevelRecord):
                    raise MarketDataError(
                        "levels must contain IndexLevelRecord values"
                    )
                contract = session.execute(text("""
                    SELECT id, provider, mic, data_type
                    FROM market_data_provider_contracts
                    WHERE contract_key = :contract_key
                """), {
                    "contract_key": level.contract_key,
                }).mappings().first()
                if (
                    contract is None
                    or contract["mic"].strip() != level.mic
                    or contract["data_type"] not in {
                        "delayed-index-level",
                        "realtime-index-level",
                    }
                    or not level.source.startswith(contract["provider"])
                ):
                    raise MarketDataError(
                        "index level does not match its provider contract"
                    )
                result = session.execute(text("""
                    INSERT INTO market_index_levels (
                        provider_contract_id,
                        symbol,
                        mic,
                        event_time,
                        received_at,
                        source,
                        level,
                        source_checksum_sha256
                    )
                    VALUES (
                        :provider_contract_id,
                        :symbol,
                        :mic,
                        :event_time,
                        :received_at,
                        :source,
                        :level,
                        :source_checksum_sha256
                    )
                    ON CONFLICT (
                        provider_contract_id,
                        symbol,
                        event_time
                    )
                    DO NOTHING
                    RETURNING id
                """), {
                    "provider_contract_id": contract["id"],
                    "symbol": level.symbol,
                    "mic": level.mic,
                    "event_time": level.event_time,
                    "received_at": level.received_at,
                    "source": level.source,
                    "level": level.level,
                    "source_checksum_sha256": (
                        level.source_checksum_sha256
                    ),
                })
                if result.scalar() is not None:
                    inserted += 1
        return inserted

    def get_current_authorized_index_change(
        self,
        checked_at: datetime,
    ) -> dict:
        """Return fresh OMXSGI change versus the previous official close."""
        if (
            not isinstance(checked_at, datetime)
            or checked_at.tzinfo is None
            or checked_at.utcoffset() is None
        ):
            raise MarketDataError("checked_at must be timezone-aware")
        with self.Session() as session:
            row = session.execute(text("""
                WITH authorized_provider AS (
                    SELECT
                        contract.id,
                        contract.provider,
                        contract.nominal_delay_seconds,
                        contract.max_transport_lag_seconds
                    FROM market_data_provider_contracts contract
                    JOIN LATERAL (
                        SELECT validation.*
                        FROM market_data_provider_validations validation
                        WHERE validation.contract_id = contract.id
                          AND validation.validated_at <= :checked_at
                        ORDER BY
                            validation.validated_at DESC,
                            validation.id DESC
                        LIMIT 1
                    ) validation ON TRUE
                    WHERE contract.mic = 'XSTO'
                      AND contract.data_type IN (
                          'delayed-index-level',
                          'realtime-index-level'
                      )
                      AND contract.status = 'VALIDATED'
                      AND (
                          :checked_at AT TIME ZONE 'Europe/Stockholm'
                      )::DATE BETWEEN
                          contract.valid_from AND contract.valid_until
                      AND contract.legal_reviewed_at IS NOT NULL
                      AND contract.transport_verified_at IS NOT NULL
                      AND validation.status = 'PASSED'
                      AND validation.valid_until >= :checked_at
                      AND validation.expected_instruments = 1
                      AND validation.product_covered_instruments = 1
                      AND validation.symbol_mapped_instruments = 1
                      AND validation.sample_file_count > 0
                      AND validation.sample_quote_count > 0
                      AND validation.max_observed_delivery_seconds
                            <= (
                                contract.nominal_delay_seconds
                                + contract.max_transport_lag_seconds
                            )
                    ORDER BY contract.updated_at DESC, contract.id DESC
                    LIMIT 1
                ),
                current_session AS (
                    SELECT session.*
                    FROM market_sessions session
                    WHERE session.mic = 'XSTO'
                      AND session.session_date = (
                          :checked_at AT TIME ZONE 'Europe/Stockholm'
                      )::DATE
                      AND session.status IN ('OPEN', 'HALF_DAY')
                      AND :checked_at >= session.opens_at
                      AND :checked_at < session.closes_at
                    LIMIT 1
                ),
                previous_session AS (
                    SELECT session.*
                    FROM market_sessions session
                    JOIN current_session current ON TRUE
                    WHERE session.mic = 'XSTO'
                      AND session.status IN ('OPEN', 'HALF_DAY')
                      AND session.closes_at < current.opens_at
                    ORDER BY session.closes_at DESC
                    LIMIT 1
                )
                SELECT
                    provider.provider,
                    provider.nominal_delay_seconds,
                    provider.max_transport_lag_seconds,
                    current_level.id AS current_level_id,
                    current_level.symbol,
                    current_level.level AS current_level,
                    current_level.event_time,
                    current_level.received_at,
                    current_level.source,
                    current_level.source_checksum_sha256,
                    previous_level.level AS previous_close,
                    previous_level.event_time AS previous_event_time
                FROM authorized_provider provider
                JOIN current_session current ON TRUE
                JOIN previous_session previous ON TRUE
                JOIN LATERAL (
                    SELECT level.*
                    FROM market_index_levels level
                    WHERE level.provider_contract_id = provider.id
                      AND level.symbol = 'OMXSGI'
                      AND level.mic = 'XSTO'
                      AND level.source LIKE provider.provider || '%'
                      AND level.event_time
                            BETWEEN current.opens_at AND :checked_at
                      AND level.received_at <= :checked_at
                    ORDER BY level.event_time DESC, level.id DESC
                    LIMIT 1
                ) current_level ON TRUE
                JOIN LATERAL (
                    SELECT level.*
                    FROM market_index_levels level
                    WHERE level.provider_contract_id = provider.id
                      AND level.symbol = 'OMXSGI'
                      AND level.mic = 'XSTO'
                      AND level.source LIKE provider.provider || '%'
                      AND level.event_time BETWEEN
                            previous.opens_at AND previous.closes_at
                      AND level.received_at <= :checked_at
                    ORDER BY level.event_time DESC, level.id DESC
                    LIMIT 1
                ) previous_level ON TRUE
            """), {
                "checked_at": checked_at,
            }).mappings().first()
        if row is None:
            raise MarketDataError(
                "authorized OMXSGI provider, session, or level is missing"
            )
        maximum_age = (
            int(row["nominal_delay_seconds"])
            + int(row["max_transport_lag_seconds"])
        )
        observed_age = (checked_at - row["event_time"]).total_seconds()
        if observed_age < 0 or observed_age > maximum_age:
            raise MarketDataError("authorized OMXSGI level is stale")
        current_level = Decimal(row["current_level"])
        previous_close = Decimal(row["previous_close"])
        change_pct = (
            (current_level / previous_close) - Decimal("1")
        ) * Decimal("100")
        return {
            "symbol": row["symbol"],
            "provider": row["provider"],
            "current_level": current_level,
            "previous_close": previous_close,
            "change_pct": change_pct,
            "level_id": int(row["current_level_id"]),
            "event_time": row["event_time"],
            "received_at": row["received_at"],
            "source": row["source"],
            "source_checksum_sha256": row[
                "source_checksum_sha256"
            ].strip(),
        }

    def get_latest_data_file_report_minute(
        self,
        *,
        provider: str,
        data_type: str,
    ):
        """Return the newest atomically archived provider report minute."""
        provider = self._required_sync_text(provider, "provider")
        data_type = self._required_sync_text(data_type, "data_type")
        with self.Session() as session:
            return session.execute(text("""
                SELECT MAX(report_minute)
                FROM market_data_files
                WHERE provider = :provider AND data_type = :data_type
            """), {
                'provider': provider,
                'data_type': data_type,
            }).scalar_one()

    def require_runtime_market_data_provider(
        self,
        *,
        contract_key: str,
        provider: str,
        data_type: str,
        mic: str,
        delivery_mode: str,
        transport: str,
        usage_scope: str,
        now: datetime,
        expected_instruments: int,
    ) -> ProviderContract:
        """Require current legal, transport, coverage, and freshness proof."""
        contract_key = self._required_sync_text(
            contract_key,
            "contract_key",
        ).lower()
        expected_values = {
            "provider": self._required_sync_text(
                provider,
                "provider",
            ).lower(),
            "data_type": self._required_sync_text(
                data_type,
                "data_type",
            ),
            "mic": self._required_sync_text(mic, "mic").upper(),
            "delivery_mode": self._required_sync_text(
                delivery_mode,
                "delivery_mode",
            ).upper(),
            "transport": self._required_sync_text(
                transport,
                "transport",
            ).upper(),
            "usage_scope": self._required_sync_text(
                usage_scope,
                "usage_scope",
            ).upper(),
        }
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{1,99}",
            contract_key,
        ):
            raise MarketDataError("contract_key has invalid format")
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise MarketDataError("now must be timezone-aware")
        if (
            not isinstance(expected_instruments, int)
            or isinstance(expected_instruments, bool)
            or expected_instruments <= 0
        ):
            raise MarketDataError(
                "expected_instruments must be greater than zero"
            )

        with self.Session() as session:
            row = session.execute(text("""
                SELECT
                    contract.contract_key,
                    contract.provider,
                    contract.product_name,
                    contract.data_type,
                    contract.mic,
                    contract.delivery_mode,
                    contract.transport,
                    contract.nominal_delay_seconds,
                    contract.max_transport_lag_seconds,
                    contract.usage_scope,
                    contract.non_display_category,
                    contract.external_distribution,
                    contract.reference_symbols_included,
                    contract.terms_url,
                    contract.status AS contract_status,
                    contract.valid_from AS contract_valid_from,
                    contract.valid_until AS contract_valid_until,
                    contract.legal_reviewed_at,
                    contract.transport_verified_at,
                    contract.authorization_basis,
                    contract.policy_verified_at,
                    contract.policy_verified_by,
                    contract.governance_evidence_status
                        AS contract_governance_evidence_status,
                    contract.terms_checksum_sha256,
                    contract.raw_storage_allowed,
                    contract.derived_storage_allowed,
                    contract.retention_policy,
                    contract.proposed_by,
                    contract.reviewed_by,
                    contract.revoked_at,
                    contract.revoked_by,
                    contract.revocation_reason,
                    validation.status AS validation_status,
                    validation.validated_at,
                    validation.valid_until AS validation_valid_until,
                    validation.expected_instruments,
                    validation.product_covered_instruments,
                    validation.symbol_mapped_instruments,
                    validation.sample_file_count,
                    validation.sample_quote_count,
                    validation.max_observed_delivery_seconds,
                    validation.evidence_checksum_sha256,
                    validation.validated_by,
                    validation.validation_basis,
                    validation.governance_evidence_status
                        AS validation_governance_evidence_status,
                    validation.reference_snapshot_id,
                    validation.reference_checksum_sha256,
                    validation.acceptance_session_count,
                    validation.first_session_date,
                    validation.last_session_date,
                    validation.acceptance_checksum_sha256
                FROM market_data_provider_contracts contract
                LEFT JOIN LATERAL (
                    SELECT candidate.*
                    FROM market_data_provider_validations candidate
                    WHERE candidate.contract_id = contract.id
                    ORDER BY candidate.validated_at DESC, candidate.id DESC
                    LIMIT 1
                ) validation ON TRUE
                WHERE contract.contract_key = :contract_key
            """), {
                "contract_key": contract_key,
            }).mappings().first()

        if row is None:
            raise MarketDataError("provider contract is missing")
        contract = ProviderContract(
            contract_key=row["contract_key"],
            provider=row["provider"],
            product_name=row["product_name"],
            data_type=row["data_type"],
            mic=row["mic"],
            delivery_mode=row["delivery_mode"],
            transport=row["transport"],
            nominal_delay_seconds=row["nominal_delay_seconds"],
            max_transport_lag_seconds=row[
                "max_transport_lag_seconds"
            ],
            usage_scope=row["usage_scope"],
            non_display_category=row["non_display_category"],
            external_distribution=row["external_distribution"],
            reference_symbols_included=row[
                "reference_symbols_included"
            ],
            terms_url=row["terms_url"],
            status=row["contract_status"],
            valid_from=row["contract_valid_from"],
            valid_until=row["contract_valid_until"],
            legal_reviewed_at=row["legal_reviewed_at"],
            transport_verified_at=row["transport_verified_at"],
            authorization_basis=row["authorization_basis"],
            policy_verified_at=row["policy_verified_at"],
            policy_verified_by=row["policy_verified_by"],
            governance_evidence_status=row[
                "contract_governance_evidence_status"
            ],
            terms_checksum_sha256=(
                row["terms_checksum_sha256"].strip()
                if row["terms_checksum_sha256"] is not None
                else None
            ),
            raw_storage_allowed=row["raw_storage_allowed"],
            derived_storage_allowed=row["derived_storage_allowed"],
            retention_policy=row["retention_policy"],
            proposed_by=row["proposed_by"],
            reviewed_by=row["reviewed_by"],
            revoked_at=row["revoked_at"],
            revoked_by=row["revoked_by"],
            revocation_reason=row["revocation_reason"],
        )
        validation = None
        if row["validation_status"] is not None:
            validation = ProviderValidation(
                contract_key=row["contract_key"],
                status=row["validation_status"],
                validated_at=row["validated_at"],
                valid_until=row["validation_valid_until"],
                expected_instruments=row["expected_instruments"],
                product_covered_instruments=row[
                    "product_covered_instruments"
                ],
                symbol_mapped_instruments=row[
                    "symbol_mapped_instruments"
                ],
                sample_file_count=row["sample_file_count"],
                sample_quote_count=row["sample_quote_count"],
                max_observed_delivery_seconds=row[
                    "max_observed_delivery_seconds"
                ],
                evidence_checksum_sha256=row[
                    "evidence_checksum_sha256"
                ].strip(),
                validated_by=row["validated_by"],
                validation_basis=row["validation_basis"],
                governance_evidence_status=row[
                    "validation_governance_evidence_status"
                ],
                reference_snapshot_id=row["reference_snapshot_id"],
                reference_checksum_sha256=(
                    row["reference_checksum_sha256"].strip()
                    if row["reference_checksum_sha256"] is not None
                    else None
                ),
                acceptance_session_count=row[
                    "acceptance_session_count"
                ],
                first_session_date=row["first_session_date"],
                last_session_date=row["last_session_date"],
                acceptance_checksum_sha256=(
                    row["acceptance_checksum_sha256"].strip()
                    if row["acceptance_checksum_sha256"] is not None
                    else None
                ),
            )
        ready = assert_provider_ready(
            contract,
            validation,
            now=now,
            expected_instruments=expected_instruments,
        )
        for field, expected in expected_values.items():
            if getattr(ready, field) != expected:
                raise MarketDataError(
                    f"provider contract {field} does not match runtime"
                )
        return ready

    def require_runtime_reference_entitlement(
        self,
        *,
        contract_key: str,
        provider: str,
        mic: str,
        transport: str,
        usage_scope: str,
        now: datetime,
    ) -> ReferenceDataEntitlement:
        """Require current legal, storage, and host-key entitlement."""
        contract_key = self._required_sync_text(
            contract_key,
            "contract_key",
        ).lower()
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{1,99}",
            contract_key,
        ):
            raise MarketDataError("contract_key has invalid format")
        with self.Session() as session:
            row = session.execute(text("""
                SELECT *
                FROM reference_data_entitlements
                WHERE contract_key = :contract_key
            """), {
                "contract_key": contract_key,
            }).mappings().first()
        if row is None:
            raise MarketDataError("reference entitlement is missing")
        entitlement = ReferenceDataEntitlement(
            entitlement_id=int(row["id"]),
            contract_key=row["contract_key"],
            provider=row["provider"],
            product_name=row["product_name"],
            mic=row["mic"].strip(),
            transport=row["transport"],
            usage_scope=row["usage_scope"],
            external_distribution=row["external_distribution"],
            raw_storage_allowed=row["raw_storage_allowed"],
            derived_storage_allowed=row["derived_storage_allowed"],
            retention_policy=row["retention_policy"],
            terms_url=row["terms_url"],
            terms_checksum_sha256=row[
                "terms_checksum_sha256"
            ].strip(),
            status=row["status"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            legal_reviewed_at=row["legal_reviewed_at"],
            storage_reviewed_at=row["storage_reviewed_at"],
            entitlement_verified_at=row[
                "entitlement_verified_at"
            ],
            host_key_algorithm=row["host_key_algorithm"],
            host_key_fingerprint_sha256=row[
                "host_key_fingerprint_sha256"
            ],
            reviewed_by=row["reviewed_by"],
        )
        return assert_reference_entitlement_ready(
            entitlement,
            now=now,
            contract_key=contract_key,
            provider=provider,
            mic=mic,
            transport=transport,
            usage_scope=usage_scope,
        )

    def record_data_sync_failure(
        self,
        *,
        provider: str,
        data_type: str,
        started_at,
        finished_at,
        error_message: str,
    ) -> int:
        """Persist a bounded, non-file sync failure for operations audit."""
        provider = self._required_sync_text(provider, "provider")
        data_type = self._required_sync_text(data_type, "data_type")
        error_message = self._required_sync_text(
            error_message,
            "error_message",
        )
        if len(error_message) > 500:
            raise ValueError("error_message cannot exceed 500 characters")
        for value, field in (
            (started_at, "started_at"),
            (finished_at, "finished_at"),
        ):
            if (
                not hasattr(value, "utcoffset")
                or value.utcoffset() is None
            ):
                raise ValueError(f"{field} must be timezone-aware")
        if finished_at < started_at:
            raise ValueError("finished_at cannot be before started_at")

        with self.Session.begin() as session:
            return int(session.execute(text("""
                INSERT INTO data_sync_runs (
                    provider, data_type, started_at, finished_at,
                    status, records_received, records_rejected,
                    records_inserted, gap_count, error_message
                )
                VALUES (
                    :provider, :data_type, :started_at, :finished_at,
                    'FAILED', 0, 0, 0, 0, :error_message
                )
                RETURNING id
            """), {
                'provider': provider,
                'data_type': data_type,
                'started_at': started_at,
                'finished_at': finished_at,
                'error_message': error_message,
            }).scalar_one())

    def ingest_market_data_file(
        self,
        archive: MarketDataArchive,
        quotes: Sequence[QuoteRecord],
        gaps: Sequence[MarketDataGap],
    ) -> FileIngestionResult:
        """Atomically archive one file, its quotes, gaps, and sync audit."""
        if not isinstance(archive, MarketDataArchive):
            raise TypeError("archive must be a MarketDataArchive")
        quotes = tuple(quotes)
        gaps = tuple(gaps)
        for gap in gaps:
            if (
                gap.provider != archive.provider
                or gap.data_type != archive.data_type
            ):
                raise MarketDataError(
                    "gap provider and data_type must match archive"
                )

        run_key = (
            f"{archive.provider}:{archive.data_type}:{archive.file_name}"
        )
        first_event_time = min(
            (quote.event_time for quote in quotes),
            default=None,
        )
        last_event_time = max(
            (quote.event_time for quote in quotes),
            default=None,
        )

        with self.Session.begin() as session:
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:run_key, 0))"
                ),
                {'run_key': run_key},
            )
            provider_contract_id = None
            if archive.provider_contract_key is not None:
                contract = session.execute(text("""
                    SELECT id, mic
                    FROM market_data_provider_contracts
                    WHERE contract_key = :contract_key
                      AND provider = :provider
                      AND data_type = :data_type
                      AND (
                        :report_minute
                            AT TIME ZONE 'Europe/Stockholm'
                      )::DATE BETWEEN valid_from AND valid_until
                """), {
                    "contract_key": archive.provider_contract_key,
                    "provider": archive.provider,
                    "data_type": archive.data_type,
                    "report_minute": archive.report_minute,
                }).mappings().first()
                if contract is None:
                    raise MarketDataError(
                        "archive does not match its provider contract"
                    )
                if any(
                    quote.mic != contract["mic"].strip()
                    for quote in quotes
                ):
                    raise MarketDataError(
                        "archive quotes do not match provider contract MIC"
                    )
                provider_contract_id = int(contract["id"])
            existing = session.execute(text("""
                SELECT
                    mdf.id,
                    mdf.checksum_sha256,
                    mdf.report_minute,
                    mdf.source_url,
                    mdf.uncompressed_bytes,
                    mdf.provider_contract_id,
                    dsr.id AS sync_run_id
                FROM market_data_files mdf
                LEFT JOIN data_sync_runs dsr
                    ON dsr.data_file_id = mdf.id
                WHERE
                    mdf.provider = :provider
                    AND mdf.data_type = :data_type
                    AND mdf.file_name = :file_name
                FOR UPDATE OF mdf
            """), {
                'provider': archive.provider,
                'data_type': archive.data_type,
                'file_name': archive.file_name,
            }).mappings().first()
            if existing is not None:
                if existing['checksum_sha256'].strip() != archive.checksum_sha256:
                    raise MarketDataError(
                        "archived file checksum changed for the same name"
                    )
                if (
                    existing['report_minute'] != archive.report_minute
                    or existing['source_url'] != archive.source_url
                    or existing['uncompressed_bytes']
                    != archive.uncompressed_bytes
                    or existing['provider_contract_id']
                    != provider_contract_id
                ):
                    raise MarketDataError(
                        "archived file metadata changed for the same name"
                    )
                if existing['sync_run_id'] is None:
                    raise RuntimeError(
                        "archived market data file has no sync audit"
                    )
                return FileIngestionResult(
                    inserted=False,
                    quotes_inserted=0,
                    sync_run_id=int(existing['sync_run_id']),
                )

            same_minute = session.execute(text("""
                SELECT file_name
                FROM market_data_files
                WHERE
                    provider = :provider
                    AND data_type = :data_type
                    AND report_minute = :report_minute
                FOR UPDATE
            """), {
                'provider': archive.provider,
                'data_type': archive.data_type,
                'report_minute': archive.report_minute,
            }).scalar()
            if same_minute is not None:
                raise MarketDataError(
                    "a different file already owns the report minute"
                )

            data_file_id = int(session.execute(text("""
                INSERT INTO market_data_files (
                    provider, data_type, file_name, report_minute,
                    received_at, source_url, checksum_sha256,
                    content_encoding, compressed_content,
                    uncompressed_bytes, provider_contract_id
                )
                VALUES (
                    :provider, :data_type, :file_name, :report_minute,
                    :received_at, :source_url, :checksum_sha256,
                    :content_encoding, :compressed_content,
                    :uncompressed_bytes, :provider_contract_id
                )
                RETURNING id
            """), {
                'provider': archive.provider,
                'data_type': archive.data_type,
                'file_name': archive.file_name,
                'report_minute': archive.report_minute,
                'received_at': archive.received_at,
                'source_url': archive.source_url,
                'checksum_sha256': archive.checksum_sha256,
                'content_encoding': archive.content_encoding,
                'compressed_content': archive.compressed_content,
                'uncompressed_bytes': archive.uncompressed_bytes,
                'provider_contract_id': provider_contract_id,
            }).scalar_one())

            quotes_inserted = self._insert_market_quotes(
                session,
                quotes,
                data_file_id=data_file_id,
            )
            for gap in gaps:
                session.execute(text("""
                    INSERT INTO market_data_gaps (
                        provider, data_type, gap_start, gap_end, reason
                    )
                    VALUES (
                        :provider, :data_type, :gap_start, :gap_end, :reason
                    )
                    ON CONFLICT (
                        provider, data_type, gap_start, gap_end
                    ) DO NOTHING
                """), {
                    'provider': gap.provider,
                    'data_type': gap.data_type,
                    'gap_start': gap.gap_start,
                    'gap_end': gap.gap_end,
                    'reason': gap.reason,
                })

            session.execute(text("""
                UPDATE market_data_gaps gap
                SET status = 'RESOLVED', resolved_at = NOW()
                WHERE
                    gap.provider = :provider
                    AND gap.data_type = :data_type
                    AND gap.status = 'OPEN'
                    AND NOT EXISTS (
                        SELECT 1
                        FROM generate_series(
                            gap.gap_start,
                            gap.gap_end,
                            INTERVAL '1 minute'
                        ) AS expected(report_minute)
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM market_data_files archived
                            WHERE
                                archived.provider = gap.provider
                                AND archived.data_type = gap.data_type
                                AND archived.report_minute
                                    = expected.report_minute
                        )
                    )
            """), {
                'provider': archive.provider,
                'data_type': archive.data_type,
            })

            sync_run_id = int(session.execute(text("""
                INSERT INTO data_sync_runs (
                    provider, data_type, started_at, finished_at,
                    status, records_received, records_rejected,
                    run_key, data_file_id, records_inserted,
                    first_event_time, last_event_time, gap_count
                )
                VALUES (
                    :provider, :data_type, :started_at, NOW(),
                    'SUCCEEDED', :records_received, 0,
                    :run_key, :data_file_id, :records_inserted,
                    :first_event_time, :last_event_time, :gap_count
                )
                RETURNING id
            """), {
                'provider': archive.provider,
                'data_type': archive.data_type,
                'started_at': archive.received_at,
                'records_received': len(quotes),
                'run_key': run_key,
                'data_file_id': data_file_id,
                'records_inserted': quotes_inserted,
                'first_event_time': first_event_time,
                'last_event_time': last_event_time,
                'gap_count': len(gaps),
            }).scalar_one())

        return FileIngestionResult(
            inserted=True,
            quotes_inserted=quotes_inserted,
            sync_run_id=sync_run_id,
        )

    @staticmethod
    def _required_sync_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
        return value.strip()

    @staticmethod
    def _insert_market_quotes(
        session,
        quotes: Sequence[QuoteRecord],
        *,
        data_file_id: int | None,
    ) -> int:
        inserted = 0
        for quote in quotes:
            if not isinstance(quote, QuoteRecord):
                raise TypeError("quotes must contain QuoteRecord values")
            instrument = session.execute(text("""
                SELECT id, mic, notional_currency
                FROM instruments
                WHERE isin = :isin
            """), {'isin': quote.isin}).mappings().first()
            if instrument is None:
                raise ValueError(
                    f"Unknown instrument for quote: {quote.isin}"
                )
            if instrument['mic'].strip() != quote.mic:
                raise ValueError(
                    f"MIC mismatch for quote: {quote.isin}"
                )
            expected_currency = instrument['notional_currency'].strip()
            if quote.source == "nasdaq-nordic-delayed-post-trade":
                trading_currency = session.execute(text("""
                    SELECT TRIM(member.trading_currency)
                    FROM instrument_alias_snapshot_heads head
                    JOIN instrument_alias_snapshots snapshot
                      ON snapshot.id = head.snapshot_id
                    JOIN instrument_alias_snapshot_members member
                      ON member.snapshot_id = snapshot.id
                     AND member.instrument_id = :instrument_id
                    JOIN reference_data_entitlements entitlement
                      ON entitlement.id = snapshot.entitlement_id
                    JOIN LATERAL (
                        SELECT id
                        FROM reference_data_snapshots
                        WHERE provider = 'esma-firds'
                          AND mic = head.mic
                        ORDER BY snapshot_date DESC, id DESC
                        LIMIT 1
                    ) latest_reference ON TRUE
                    WHERE head.provider = 'nasdaq-nordic'
                      AND head.mic = :mic
                      AND snapshot.reference_snapshot_id = latest_reference.id
                      AND entitlement.status = 'VALIDATED'
                      AND entitlement.provider = head.provider
                      AND entitlement.mic = head.mic
                      AND entitlement.transport = 'SFTP'
                      AND entitlement.usage_scope =
                        'INTERNAL_ANALYSIS_AND_PAPER'
                      AND entitlement.external_distribution = FALSE
                      AND entitlement.raw_storage_allowed = TRUE
                      AND entitlement.derived_storage_allowed = TRUE
                      AND (
                        NOW() AT TIME ZONE 'Europe/Stockholm'
                      )::date BETWEEN
                        entitlement.valid_from AND entitlement.valid_until
                      AND entitlement.legal_reviewed_at IS NOT NULL
                      AND entitlement.legal_reviewed_at <= NOW()
                      AND entitlement.storage_reviewed_at IS NOT NULL
                      AND entitlement.storage_reviewed_at <= NOW()
                      AND entitlement.entitlement_verified_at IS NOT NULL
                      AND entitlement.entitlement_verified_at <= NOW()
                """), {
                    "instrument_id": instrument['id'],
                    "mic": quote.mic,
                }).scalar_one_or_none()
                if trading_currency is None:
                    raise ValueError(
                        f"Verified Nasdaq alias missing for quote: {quote.isin}"
                    )
                expected_currency = trading_currency
            if expected_currency != quote.currency:
                raise ValueError(
                    f"Currency mismatch for quote: {quote.isin}"
                )

            result = session.execute(text("""
                INSERT INTO market_quotes (
                    instrument_id, event_time, received_at, source,
                    last_price, volume, currency, data_file_id
                )
                VALUES (
                    :instrument_id, :event_time, :received_at, :source,
                    :last_price, :volume, :currency, :data_file_id
                )
                ON CONFLICT (instrument_id, source, event_time)
                DO NOTHING
                RETURNING id
            """), {
                'instrument_id': instrument['id'],
                'event_time': quote.event_time,
                'received_at': quote.received_at,
                'source': quote.source,
                'last_price': quote.last_price,
                'volume': quote.volume,
                'currency': quote.currency,
                'data_file_id': data_file_id,
            })
            quote_id = result.scalar()
            if quote_id is not None:
                inserted += 1
            elif data_file_id is not None:
                existing_file_id = session.execute(text("""
                    SELECT data_file_id
                    FROM market_quotes
                    WHERE
                        instrument_id = :instrument_id
                        AND source = :source
                        AND event_time = :event_time
                """), {
                    'instrument_id': instrument['id'],
                    'source': quote.source,
                    'event_time': quote.event_time,
                }).scalar()
                if existing_file_id != data_file_id:
                    raise MarketDataError(
                        "existing quote has conflicting source-file provenance"
                    )
        return inserted

    def get_latest_market_quote(
        self,
        ticker: str,
    ) -> Optional[QuoteRecord]:
        """Return the newest quote through the stable company mapping."""
        with self.Session() as session:
            row = session.execute(text("""
                SELECT
                    mq.id AS quote_id,
                    i.isin,
                    i.mic,
                    mq.event_time,
                    mq.received_at,
                    mq.source,
                    mq.last_price,
                    mq.currency,
                    mq.volume
                FROM companies c
                JOIN instruments i ON i.id = c.instrument_id
                JOIN LATERAL (
                    SELECT *
                    FROM market_quotes
                    WHERE instrument_id = i.id
                    ORDER BY event_time DESC
                    LIMIT 1
                ) mq ON true
                WHERE c.ticker = :ticker
            """), {'ticker': ticker}).mappings().first()
        if row is None:
            return None
        return QuoteRecord(
            quote_id=int(row['quote_id']),
            isin=row['isin'].strip(),
            mic=row['mic'].strip(),
            event_time=row['event_time'],
            received_at=row['received_at'],
            source=row['source'],
            last_price=row['last_price'],
            currency=row['currency'].strip(),
            volume=row['volume'],
        )

    def get_latest_authorized_market_quote(
        self,
        ticker: str,
    ) -> Optional[QuoteRecord]:
        """Return the newest authorized mark for valuation.

        Last-trade providers use their persisted quote. The public pre-trade
        provider uses the midpoint of the latest sealed two-sided book and
        carries the exact book-state id as provenance.
        """
        ticker = self._required_sync_text(ticker, "ticker").upper()
        with self.Session() as session:
            row = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE},
                last_trade AS (
                    SELECT
                        quote.id AS quote_id,
                        NULL::BIGINT AS book_state_id,
                        instrument.isin,
                        instrument.mic,
                        quote.event_time,
                        quote.received_at,
                        quote.source,
                        NULL::TIMESTAMPTZ AS covered_through,
                        quote.last_price,
                        quote.currency,
                        quote.volume
                    FROM companies company
                    JOIN instruments instrument
                      ON instrument.id = company.instrument_id
                     AND instrument.mic = 'XSTO'
                     AND instrument.status = 'ACTIVE'
                    JOIN authorized_provider provider
                      ON provider.data_type != 'delayed-pre-trade-equity'
                    JOIN LATERAL (
                        SELECT candidate.*
                        FROM market_quotes candidate
                        JOIN market_data_files source_file
                          ON source_file.id = candidate.data_file_id
                         AND source_file.provider_contract_id =
                            provider.contract_id
                        WHERE candidate.instrument_id = instrument.id
                          AND candidate.source LIKE
                              provider.provider || '%'
                          AND candidate.event_time <= NOW()
                          AND candidate.received_at <= NOW()
                        ORDER BY
                            candidate.event_time DESC,
                            candidate.id DESC
                        LIMIT 1
                    ) quote ON TRUE
                    WHERE company.ticker = :ticker
                ),
                pre_trade_mark AS (
                    SELECT
                        NULL::BIGINT AS quote_id,
                        state.id AS book_state_id,
                        instrument.isin,
                        instrument.mic,
                        GREATEST(
                            state.bid_event_time,
                            state.ask_event_time
                        ) AS event_time,
                        state.received_at,
                        state.source,
                        state.covered_through,
                        (state.bid_price + state.ask_price) / 2
                            AS last_price,
                        state.currency,
                        LEAST(
                            state.bid_quantity,
                            state.ask_quantity
                        ) AS volume
                    FROM companies company
                    JOIN instruments instrument
                      ON instrument.id = company.instrument_id
                     AND instrument.mic = 'XSTO'
                     AND instrument.status = 'ACTIVE'
                    JOIN authorized_provider provider
                      ON provider.data_type = 'delayed-pre-trade-equity'
                    JOIN LATERAL (
                        SELECT candidate.*
                        FROM pre_trade_batches batch
                        JOIN pre_trade_stream_cursors seal
                          ON seal.batch_id = batch.id
                        JOIN pre_trade_book_states candidate
                          ON candidate.batch_id = batch.id
                         AND candidate.instrument_id = instrument.id
                        WHERE batch.provider_contract_id =
                            provider.contract_id
                        ORDER BY
                            batch.report_minute DESC,
                            batch.id DESC
                        LIMIT 1
                    ) state ON TRUE
                    WHERE company.ticker = :ticker
                      AND state.bid_price IS NOT NULL
                      AND state.ask_price IS NOT NULL
                      AND state.trading_system = 'CLOB'
                      AND state.trading_phase = 'COTR'
                      AND state.received_at <= NOW()
                )
                SELECT * FROM last_trade
                UNION ALL
                SELECT * FROM pre_trade_mark
                LIMIT 1
            """), {"ticker": ticker}).mappings().first()
        if row is None:
            return None
        return QuoteRecord(
            quote_id=(
                int(row["quote_id"])
                if row["quote_id"] is not None
                else None
            ),
            book_state_id=(
                int(row["book_state_id"])
                if row["book_state_id"] is not None
                else None
            ),
            isin=row["isin"].strip(),
            mic=row["mic"].strip(),
            event_time=row["event_time"],
            received_at=row["received_at"],
            source=row["source"],
            covered_through=row["covered_through"],
            last_price=row["last_price"],
            currency=row["currency"].strip(),
            volume=row["volume"],
        )

    def get_latest_authorized_execution_quote(
        self,
        ticker: str,
        *,
        action: str,
        now: datetime,
    ) -> Optional[QuoteRecord]:
        """Return the exact currently executable paper price evidence."""
        ticker = self._required_sync_text(ticker, "ticker").upper()
        action = self._required_sync_text(action, "action").upper()
        if action not in {"BUY", "SELL"}:
            raise MarketDataError("action must be BUY or SELL")
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise MarketDataError("execution quote clock must be aware")

        with self.Session() as session:
            provider_type = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE}
                SELECT data_type
                FROM authorized_provider
            """)).scalar_one_or_none()
            if provider_type is None:
                return None
            if provider_type != "delayed-pre-trade-equity":
                quote = self.get_latest_authorized_market_quote(ticker)
                if quote is None or quote.quote_id is None:
                    return None
                return quote

            row = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE}
                SELECT
                    state.id AS book_state_id,
                    instrument.isin,
                    instrument.mic,
                    CASE
                        WHEN :action = 'BUY'
                        THEN state.ask_event_time
                        ELSE state.bid_event_time
                    END AS event_time,
                    state.received_at,
                    state.source,
                    state.covered_through,
                    CASE
                        WHEN :action = 'BUY'
                        THEN state.ask_price
                        ELSE state.bid_price
                    END AS execution_price,
                    state.currency,
                    CASE
                        WHEN :action = 'BUY'
                        THEN state.ask_quantity
                        ELSE state.bid_quantity
                    END AS executable_quantity
                FROM companies company
                JOIN instruments instrument
                  ON instrument.id = company.instrument_id
                 AND instrument.mic = 'XSTO'
                 AND instrument.status = 'ACTIVE'
                JOIN authorized_provider provider
                  ON provider.data_type = 'delayed-pre-trade-equity'
                JOIN LATERAL (
                    SELECT candidate.*
                    FROM pre_trade_batches batch
                    JOIN pre_trade_stream_cursors seal
                      ON seal.batch_id = batch.id
                    JOIN pre_trade_book_states candidate
                      ON candidate.batch_id = batch.id
                     AND candidate.instrument_id = instrument.id
                    WHERE batch.provider_contract_id = provider.contract_id
                    ORDER BY
                        batch.report_minute DESC,
                        batch.id DESC
                    LIMIT 1
                ) state ON TRUE
                JOIN market_sessions market_session
                  ON market_session.mic = 'XSTO'
                 AND market_session.status IN ('OPEN', 'HALF_DAY')
                 AND :now >= market_session.opens_at
                 AND :now < market_session.closes_at
                WHERE company.ticker = :ticker
                  AND state.bid_price IS NOT NULL
                  AND state.ask_price IS NOT NULL
                  AND state.bid_quantity > 0
                  AND state.ask_quantity > 0
                  AND state.trading_system = 'CLOB'
                  AND state.trading_phase = 'COTR'
                  AND state.received_at <= :now
                  AND :now - state.received_at <= INTERVAL '60 seconds'
                  AND :now - state.covered_through <= (
                      provider.nominal_delay_seconds
                      + provider.max_transport_lag_seconds
                      + 60
                  ) * INTERVAL '1 second'
                LIMIT 1
            """), {
                "ticker": ticker,
                "action": action,
                "now": now,
            }).mappings().first()
        if row is None:
            return None
        return QuoteRecord(
            book_state_id=int(row["book_state_id"]),
            isin=row["isin"].strip(),
            mic=row["mic"].strip(),
            event_time=row["event_time"],
            received_at=row["received_at"],
            source=row["source"],
            covered_through=row["covered_through"],
            last_price=row["execution_price"],
            currency=row["currency"].strip(),
            volume=row["executable_quantity"],
        )

    def get_authorized_intraday_signal(
        self,
        ticker: str,
        *,
        now: datetime,
        window: int = 20,
    ) -> dict | None:
        """Build a continuous minute SMA from sealed public book states."""
        ticker = self._required_sync_text(ticker, "ticker").upper()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise MarketDataError("intraday signal clock must be aware")
        if (
            isinstance(window, bool)
            or not isinstance(window, int)
            or not 5 <= window <= 120
        ):
            raise MarketDataError("intraday signal window must be 5-120")

        with self.Session() as session:
            rows = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE}
                SELECT
                    batch.report_minute,
                    state.id AS book_state_id,
                    (state.bid_price + state.ask_price) / 2 AS midpoint,
                    state.received_at,
                    provider.nominal_delay_seconds,
                    provider.max_transport_lag_seconds
                FROM companies company
                JOIN instruments instrument
                  ON instrument.id = company.instrument_id
                 AND instrument.mic = 'XSTO'
                 AND instrument.status = 'ACTIVE'
                JOIN authorized_provider provider
                  ON provider.data_type = 'delayed-pre-trade-equity'
                JOIN pre_trade_batches batch
                  ON batch.provider_contract_id = provider.contract_id
                JOIN pre_trade_stream_cursors seal
                  ON seal.batch_id = batch.id
                JOIN pre_trade_book_states state
                  ON state.batch_id = batch.id
                 AND state.instrument_id = instrument.id
                JOIN market_sessions market_session
                  ON market_session.mic = 'XSTO'
                 AND market_session.status IN ('OPEN', 'HALF_DAY')
                 AND batch.report_minute >= market_session.opens_at
                 AND batch.report_minute < market_session.closes_at
                 AND :now >= market_session.opens_at
                 AND :now < market_session.closes_at
                WHERE company.ticker = :ticker
                  AND state.bid_price IS NOT NULL
                  AND state.ask_price IS NOT NULL
                  AND state.bid_quantity > 0
                  AND state.ask_quantity > 0
                  AND state.trading_system = 'CLOB'
                  AND state.trading_phase = 'COTR'
                  AND state.received_at <= :now
                ORDER BY batch.report_minute DESC, batch.id DESC
                LIMIT :window
            """), {
                "ticker": ticker,
                "now": now,
                "window": window,
            }).mappings().all()
        if len(rows) != window:
            return None
        for newer, older in zip(rows, rows[1:]):
            if newer["report_minute"] - older["report_minute"] != timedelta(
                minutes=1
            ):
                return None
        latest = rows[0]
        now_utc = now.astimezone(timezone.utc)
        transport_age = now_utc - latest["received_at"]
        max_transport_age = timedelta(
            seconds=int(latest["max_transport_lag_seconds"])
        )
        market_age = now_utc - latest["report_minute"]
        max_market_age = timedelta(
            seconds=(
                int(latest["nominal_delay_seconds"])
                + int(latest["max_transport_lag_seconds"])
                + 60
            )
        )
        if (
            transport_age < timedelta(0)
            or transport_age > max_transport_age
            or market_age < timedelta(0)
            or market_age > max_market_age
        ):
            return None
        midpoints = [Decimal(row["midpoint"]) for row in rows]
        return {
            "ticker": ticker,
            "session_date": now.astimezone(
                ZoneInfo("Europe/Stockholm")
            ).date(),
            "book_state_id": int(rows[0]["book_state_id"]),
            "latest_price": midpoints[0],
            "sma20": sum(midpoints, Decimal("0")) / Decimal(window),
            "window": window,
            "first_report_minute": rows[-1]["report_minute"],
            "last_report_minute": rows[0]["report_minute"],
        }

    def get_current_authorized_intraday_signals(
        self,
        *,
        now: datetime,
        window: int = 20,
        limit: int = 50,
    ) -> List[dict]:
        """Rank fresh continuous public pre-trade signals across XSTO."""
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise MarketDataError("intraday signal clock must be aware")
        if (
            isinstance(window, bool)
            or not isinstance(window, int)
            or not 5 <= window <= 120
        ):
            raise MarketDataError("intraday signal window must be 5-120")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 200
        ):
            raise MarketDataError("intraday signal limit must be 1-200")

        with self.Session() as session:
            rows = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE},
                ranked_states AS (
                    SELECT
                        company.ticker,
                        company.name,
                        company.sector,
                        provider.provider,
                        provider.nominal_delay_seconds,
                        provider.max_transport_lag_seconds,
                        batch.report_minute,
                        batch.source,
                        state.id AS book_state_id,
                        (
                            state.bid_price + state.ask_price
                        ) / 2 AS midpoint,
                        state.received_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY company.ticker
                            ORDER BY
                                batch.report_minute DESC,
                                batch.id DESC
                        ) AS signal_rank
                    FROM companies company
                    JOIN instruments instrument
                      ON instrument.id = company.instrument_id
                     AND instrument.mic = 'XSTO'
                     AND instrument.status = 'ACTIVE'
                    JOIN authorized_provider provider
                      ON provider.data_type =
                            'delayed-pre-trade-equity'
                    JOIN pre_trade_batches batch
                      ON batch.provider_contract_id =
                            provider.contract_id
                    JOIN pre_trade_stream_cursors seal
                      ON seal.batch_id = batch.id
                    JOIN pre_trade_book_states state
                      ON state.batch_id = batch.id
                     AND state.instrument_id = instrument.id
                    JOIN market_sessions market_session
                      ON market_session.mic = 'XSTO'
                     AND market_session.status IN ('OPEN', 'HALF_DAY')
                     AND batch.report_minute >=
                            market_session.opens_at
                     AND batch.report_minute <
                            market_session.closes_at
                     AND :now >= market_session.opens_at
                     AND :now < market_session.closes_at
                    WHERE state.bid_price IS NOT NULL
                      AND state.ask_price IS NOT NULL
                      AND state.bid_quantity > 0
                      AND state.ask_quantity > 0
                      AND state.trading_system = 'CLOB'
                      AND state.trading_phase = 'COTR'
                      AND state.received_at <= :now
                ),
                signal_windows AS (
                    SELECT
                        ticker,
                        name,
                        sector,
                        provider,
                        MAX(nominal_delay_seconds)
                            AS nominal_delay_seconds,
                        MAX(max_transport_lag_seconds)
                            AS max_transport_lag_seconds,
                        MIN(report_minute)
                            AS first_report_minute,
                        MAX(report_minute)
                            AS last_report_minute,
                        (
                            ARRAY_AGG(
                                source
                                ORDER BY report_minute DESC
                            )
                        )[1] AS source,
                        (
                            ARRAY_AGG(
                                book_state_id
                                ORDER BY report_minute DESC
                            )
                        )[1] AS book_state_id,
                        (
                            ARRAY_AGG(
                                midpoint
                                ORDER BY report_minute DESC
                            )
                        )[1] AS latest_price,
                        AVG(midpoint) AS sma20,
                        (
                            ARRAY_AGG(
                                received_at
                                ORDER BY report_minute DESC
                            )
                        )[1] AS latest_received_at,
                        COUNT(*) AS observed_window
                    FROM ranked_states
                    WHERE signal_rank <= :window
                    GROUP BY ticker, name, sector, provider
                    HAVING COUNT(*) = :window
                       AND COUNT(DISTINCT report_minute) = :window
                       AND MAX(report_minute) - MIN(report_minute)
                            = (:window - 1) * INTERVAL '1 minute'
                ),
                fresh_signals AS (
                    SELECT
                        *,
                        (
                            (latest_price / sma20) - 1
                        ) * 100 AS momentum_pct
                    FROM signal_windows
                    WHERE :now - latest_received_at
                            BETWEEN INTERVAL '0 seconds'
                                AND max_transport_lag_seconds
                                    * INTERVAL '1 second'
                      AND :now - last_report_minute <= (
                            nominal_delay_seconds
                            + max_transport_lag_seconds
                            + 60
                        ) * INTERVAL '1 second'
                )
                SELECT
                    ticker,
                    name,
                    sector,
                    provider,
                    source,
                    book_state_id,
                    latest_price,
                    sma20,
                    momentum_pct,
                    observed_window,
                    first_report_minute,
                    last_report_minute,
                    latest_received_at
                FROM fresh_signals
                ORDER BY ABS(momentum_pct) DESC, ticker
                LIMIT :limit
            """), {
                "now": now,
                "window": window,
                "limit": limit,
            }).mappings().all()

        precision = Decimal("0.00000001")
        return [
            {
                "ticker": row["ticker"].strip(),
                "name": row["name"].strip(),
                "sector": (
                    row["sector"].strip()
                    if row["sector"]
                    else "Unclassified"
                ),
                "provider": row["provider"].strip(),
                "source": row["source"].strip(),
                "book_state_id": int(row["book_state_id"]),
                "latest_price": Decimal(
                    row["latest_price"]
                ).quantize(precision),
                "sma20": Decimal(row["sma20"]).quantize(precision),
                "momentum_pct": Decimal(
                    row["momentum_pct"]
                ).quantize(precision),
                "window": int(row["observed_window"]),
                "first_report_minute": row["first_report_minute"],
                "last_report_minute": row["last_report_minute"],
                "latest_received_at": row["latest_received_at"],
            }
            for row in rows
        ]


    def get_current_authorized_candidate_signals(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> List[dict]:
        """Return fresh 5/20/60-minute evidence for candidate ranking."""
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise MarketDataError("candidate signal clock must be aware")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1_000
        ):
            raise MarketDataError("candidate signal limit must be 1-1000")

        with self.Session() as session:
            rows = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE},
                ranked_states AS (
                    SELECT
                        company.ticker,
                        company.name,
                        company.sector,
                        provider.provider,
                        provider.nominal_delay_seconds,
                        provider.max_transport_lag_seconds,
                        batch.report_minute,
                        batch.source,
                        state.id AS book_state_id,
                        state.bid_price,
                        state.ask_price,
                        state.bid_quantity,
                        state.ask_quantity,
                        (state.bid_price + state.ask_price) / 2 AS midpoint,
                        state.received_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY company.ticker
                            ORDER BY
                                batch.report_minute DESC,
                                batch.id DESC
                        ) AS signal_rank
                    FROM companies company
                    JOIN instruments instrument
                      ON instrument.id = company.instrument_id
                     AND instrument.mic = 'XSTO'
                     AND instrument.status = 'ACTIVE'
                    JOIN authorized_provider provider
                      ON provider.data_type =
                            'delayed-pre-trade-equity'
                    JOIN pre_trade_batches batch
                      ON batch.provider_contract_id =
                            provider.contract_id
                    JOIN pre_trade_stream_cursors seal
                      ON seal.batch_id = batch.id
                    JOIN pre_trade_book_states state
                      ON state.batch_id = batch.id
                     AND state.instrument_id = instrument.id
                    JOIN market_sessions market_session
                      ON market_session.mic = 'XSTO'
                     AND market_session.status IN ('OPEN', 'HALF_DAY')
                     AND batch.report_minute >=
                            market_session.opens_at
                     AND batch.report_minute <
                            market_session.closes_at
                     AND :now >= market_session.opens_at
                     AND :now < market_session.closes_at
                    WHERE state.bid_price IS NOT NULL
                      AND state.ask_price IS NOT NULL
                      AND state.bid_quantity > 0
                      AND state.ask_quantity > 0
                      AND state.trading_system = 'CLOB'
                      AND state.trading_phase = 'COTR'
                      AND state.received_at <= :now
                ),
                signal_windows AS (
                    SELECT
                        ticker,
                        name,
                        sector,
                        provider,
                        MAX(nominal_delay_seconds)
                            AS nominal_delay_seconds,
                        MAX(max_transport_lag_seconds)
                            AS max_transport_lag_seconds,
                        MIN(report_minute) AS first_report_minute,
                        MAX(report_minute) AS last_report_minute,
                        (ARRAY_AGG(
                            source ORDER BY report_minute DESC
                        ))[1] AS source,
                        (ARRAY_AGG(
                            book_state_id ORDER BY report_minute DESC
                        ))[1] AS book_state_id,
                        (ARRAY_AGG(
                            midpoint ORDER BY report_minute DESC
                        ))[1] AS latest_price,
                        AVG(midpoint) FILTER (
                            WHERE signal_rank <= 20
                        ) AS sma20,
                        (ARRAY_AGG(
                            midpoint ORDER BY report_minute DESC
                        ))[6] AS price_5m_ago,
                        (ARRAY_AGG(
                            midpoint ORDER BY report_minute DESC
                        ))[21] AS price_20m_ago,
                        (ARRAY_AGG(
                            midpoint ORDER BY report_minute DESC
                        ))[61] AS price_60m_ago,
                        (ARRAY_AGG(
                            bid_price ORDER BY report_minute DESC
                        ))[1] AS bid_price,
                        (ARRAY_AGG(
                            ask_price ORDER BY report_minute DESC
                        ))[1] AS ask_price,
                        (ARRAY_AGG(
                            bid_quantity ORDER BY report_minute DESC
                        ))[1] AS bid_quantity,
                        (ARRAY_AGG(
                            ask_quantity ORDER BY report_minute DESC
                        ))[1] AS ask_quantity,
                        (
                            MAX(midpoint) FILTER (
                                WHERE signal_rank <= 21
                            )
                            - MIN(midpoint) FILTER (
                                WHERE signal_rank <= 21
                            )
                        ) AS range_20,
                        MAX(midpoint) - MIN(midpoint) AS range_60,
                        (ARRAY_AGG(
                            received_at ORDER BY report_minute DESC
                        ))[1] AS latest_received_at,
                        COUNT(*) AS observed_window
                    FROM ranked_states
                    WHERE signal_rank <= 61
                    GROUP BY ticker, name, sector, provider
                    HAVING COUNT(*) = 61
                       AND COUNT(DISTINCT report_minute) = 61
                       AND MAX(report_minute) - MIN(report_minute)
                            = INTERVAL '60 minutes'
                )
                SELECT
                    ticker,
                    name,
                    sector,
                    provider,
                    source,
                    book_state_id,
                    latest_price,
                    sma20,
                    price_5m_ago,
                    price_20m_ago,
                    price_60m_ago,
                    bid_price,
                    ask_price,
                    bid_quantity,
                    ask_quantity,
                    (range_20 / latest_price) * 10000
                        AS range_20_bps,
                    (range_60 / latest_price) * 10000
                        AS range_60_bps,
                    first_report_minute,
                    last_report_minute,
                    latest_received_at
                FROM signal_windows
                WHERE :now - latest_received_at
                        BETWEEN INTERVAL '0 seconds'
                            AND max_transport_lag_seconds
                                * INTERVAL '1 second'
                  AND :now - last_report_minute <= (
                        nominal_delay_seconds
                        + max_transport_lag_seconds
                        + 60
                    ) * INTERVAL '1 second'
                ORDER BY ticker
                LIMIT :limit
            """), {
                "now": now.astimezone(timezone.utc),
                "limit": limit,
            }).mappings().all()
        return [dict(row) for row in rows]

    def get_authorized_daily_bars(
        self,
        ticker: str,
        *,
        limit: int = 60,
    ) -> List[dict]:
        """Aggregate authorized XSTO quotes into official-session bars."""
        ticker = self._required_sync_text(ticker, "ticker").upper()
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 250
        ):
            raise MarketDataError("bar limit must be between 1 and 250")
        with self.Session() as session:
            rows = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE}
                SELECT
                    (
                        quote.event_time
                        AT TIME ZONE 'Europe/Stockholm'
                    )::date AS date,
                    (ARRAY_AGG(
                        quote.last_price
                        ORDER BY quote.event_time, quote.id
                    ))[1] AS open,
                    MAX(quote.last_price) AS high,
                    MIN(quote.last_price) AS low,
                    (ARRAY_AGG(
                        quote.last_price
                        ORDER BY quote.event_time DESC, quote.id DESC
                    ))[1] AS close,
                    SUM(quote.volume) AS volume,
                    MAX(quote.event_time) AS event_time,
                    MAX(quote.received_at) AS received_at,
                    (ARRAY_AGG(
                        quote.source
                        ORDER BY quote.event_time DESC, quote.id DESC
                    ))[1] AS source
                FROM companies company
                JOIN instruments instrument
                  ON instrument.id = company.instrument_id
                 AND instrument.mic = 'XSTO'
                 AND instrument.status = 'ACTIVE'
                JOIN authorized_provider provider ON TRUE
                JOIN market_quotes quote
                  ON quote.instrument_id = instrument.id
                 AND quote.source LIKE provider.provider || '%'
                 AND quote.event_time <= NOW()
                 AND quote.received_at <= NOW()
                JOIN market_data_files source_file
                  ON source_file.id = quote.data_file_id
                 AND source_file.provider_contract_id =
                    provider.contract_id
                JOIN market_sessions market_session
                  ON market_session.mic = 'XSTO'
                 AND market_session.session_date = (
                     quote.event_time
                     AT TIME ZONE market_session.timezone_name
                 )::date
                 AND market_session.status IN ('OPEN', 'HALF_DAY')
                 AND quote.event_time >= market_session.opens_at
                 AND quote.event_time <= market_session.closes_at
                WHERE company.ticker = :ticker
                GROUP BY (
                    quote.event_time
                    AT TIME ZONE 'Europe/Stockholm'
                )::date
                ORDER BY date DESC
                LIMIT :limit
            """), {
                "ticker": ticker,
                "limit": limit,
            }).mappings().all()
        return [dict(row) for row in rows]

    def get_latest_authorized_prices(self) -> List[dict]:
        """Return each mapped XSTO instrument's latest authorized mark."""
        with self.Session() as session:
            provider_type = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE}
                SELECT data_type FROM authorized_provider
            """)).scalar_one_or_none()
            if provider_type == "delayed-pre-trade-equity":
                rows = session.execute(text(f"""
                    WITH {_AUTHORIZED_XSTO_PROVIDER_CTE},
                    latest_batch AS (
                        SELECT batch.id, batch.report_minute
                        FROM pre_trade_batches batch
                        JOIN pre_trade_stream_cursors seal
                          ON seal.batch_id = batch.id
                        JOIN authorized_provider provider
                          ON batch.provider_contract_id = provider.contract_id
                        ORDER BY
                            batch.report_minute DESC,
                            batch.id DESC
                        LIMIT 1
                    )
                    SELECT
                        company.ticker,
                        (state.bid_price + state.ask_price) / 2 AS close,
                        (
                            batch.report_minute
                            AT TIME ZONE 'Europe/Stockholm'
                        )::date AS date,
                        NULL::NUMERIC AS change_pct,
                        NULL::BIGINT AS quote_id,
                        state.id AS book_state_id,
                        state.source,
                        GREATEST(
                            state.bid_event_time,
                            state.ask_event_time
                        ) AS event_time,
                        state.received_at
                    FROM latest_batch batch
                    JOIN pre_trade_book_states state
                      ON state.batch_id = batch.id
                    JOIN companies company
                      ON company.instrument_id = state.instrument_id
                    WHERE state.bid_price IS NOT NULL
                      AND state.ask_price IS NOT NULL
                      AND state.trading_system = 'CLOB'
                      AND state.trading_phase = 'COTR'
                    ORDER BY company.ticker
                """)).mappings().all()
                return [dict(row) for row in rows]

            rows = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE},
                daily AS (
                    SELECT
                        company.ticker,
                        (
                            quote.event_time
                            AT TIME ZONE 'Europe/Stockholm'
                        )::date AS date,
                        (ARRAY_AGG(
                            quote.last_price
                            ORDER BY quote.event_time DESC, quote.id DESC
                        ))[1] AS close,
                        (ARRAY_AGG(
                            quote.id
                            ORDER BY quote.event_time DESC, quote.id DESC
                        ))[1] AS quote_id,
                        (ARRAY_AGG(
                            quote.source
                            ORDER BY quote.event_time DESC, quote.id DESC
                        ))[1] AS source,
                        MAX(quote.event_time) AS event_time,
                        MAX(quote.received_at) AS received_at
                    FROM companies company
                    JOIN instruments instrument
                      ON instrument.id = company.instrument_id
                     AND instrument.mic = 'XSTO'
                     AND instrument.status = 'ACTIVE'
                    JOIN authorized_provider provider
                      ON provider.data_type != 'delayed-pre-trade-equity'
                    JOIN market_quotes quote
                      ON quote.instrument_id = instrument.id
                     AND quote.source LIKE provider.provider || '%'
                     AND quote.event_time <= NOW()
                     AND quote.received_at <= NOW()
                    JOIN market_data_files source_file
                      ON source_file.id = quote.data_file_id
                     AND source_file.provider_contract_id =
                        provider.contract_id
                    JOIN market_sessions market_session
                      ON market_session.mic = 'XSTO'
                     AND market_session.session_date = (
                         quote.event_time
                         AT TIME ZONE market_session.timezone_name
                     )::date
                     AND market_session.status IN ('OPEN', 'HALF_DAY')
                     AND quote.event_time >= market_session.opens_at
                     AND quote.event_time <= market_session.closes_at
                    GROUP BY
                        company.ticker,
                        (
                            quote.event_time
                            AT TIME ZONE 'Europe/Stockholm'
                        )::date
                ),
                with_previous AS (
                    SELECT
                        daily.*,
                        LEAD(close) OVER (
                            PARTITION BY ticker
                            ORDER BY date DESC
                        ) AS previous_close,
                        ROW_NUMBER() OVER (
                            PARTITION BY ticker
                            ORDER BY date DESC
                        ) AS recency
                    FROM daily
                )
                SELECT
                    ticker,
                    close,
                    date,
                    CASE
                        WHEN previous_close IS NULL OR previous_close = 0
                        THEN NULL
                        ELSE ((close - previous_close) / previous_close) * 100
                    END AS change_pct,
                    quote_id,
                    NULL::BIGINT AS book_state_id,
                    source,
                    event_time,
                    received_at
                FROM with_previous
                WHERE recency = 1
                ORDER BY ticker
            """)).mappings().all()
        return [dict(row) for row in rows]

    def require_operational_market_data(
        self,
        now: datetime,
    ) -> dict:
        """Require current, gap-free, executable XSTO paper evidence."""
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise MarketDataError("operational data clock must be aware")
        with self.Session() as session:
            row = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE},
                universe AS (
                    SELECT
                        COUNT(*) FILTER (
                            WHERE instrument.mic = 'XSTO'
                              AND instrument.status = 'ACTIVE'
                              AND instrument.primary_listing
                              AND instrument.instrument_type IN (
                                  'COMMON_STOCK',
                                  'PREFERRED_STOCK',
                                  'DEPOSITARY_RECEIPT'
                              )
                        )::int AS active_instrument_count,
                        COUNT(DISTINCT company.instrument_id) FILTER (
                            WHERE company.instrument_id IS NOT NULL
                              AND instrument.status = 'ACTIVE'
                              AND instrument.primary_listing
                              AND instrument.instrument_type IN (
                                  'COMMON_STOCK',
                                  'PREFERRED_STOCK',
                                  'DEPOSITARY_RECEIPT'
                              )
                        )::int AS company_mapping_count
                    FROM instruments instrument
                    LEFT JOIN companies company
                      ON company.instrument_id = instrument.id
                    WHERE instrument.mic = 'XSTO'
                ),
                current_session AS (
                    SELECT opens_at, closes_at, status
                    FROM market_sessions
                    WHERE mic = 'XSTO'
                      AND session_date = (
                          :now
                          AT TIME ZONE 'Europe/Stockholm'
                      )::date
                    LIMIT 1
                ),
                latest_sync AS (
                    SELECT sync.status
                    FROM data_sync_runs sync
                    WHERE sync.provider = (
                        SELECT provider FROM authorized_provider
                    )
                      AND sync.data_type = (
                          SELECT data_type FROM authorized_provider
                      )
                    ORDER BY sync.started_at DESC, sync.id DESC
                    LIMIT 1
                ),
                open_gaps AS (
                    SELECT COUNT(*)::int AS count
                    FROM market_data_gaps gap
                    WHERE gap.provider = (
                        SELECT provider FROM authorized_provider
                    )
                      AND gap.data_type = (
                          SELECT data_type FROM authorized_provider
                      )
                      AND gap.status = 'OPEN'
                ),
                latest_quotes AS (
                    SELECT DISTINCT ON (quote.instrument_id)
                        quote.instrument_id,
                        quote.event_time,
                        quote.received_at
                    FROM market_quotes quote
                    JOIN instruments instrument
                      ON instrument.id = quote.instrument_id
                    JOIN authorized_provider provider
                      ON provider.data_type != 'delayed-pre-trade-equity'
                    JOIN market_data_files source_file
                      ON source_file.id = quote.data_file_id
                     AND source_file.provider_contract_id =
                        provider.contract_id
                    WHERE instrument.mic = 'XSTO'
                      AND instrument.status = 'ACTIVE'
                      AND instrument.primary_listing
                      AND instrument.instrument_type IN (
                          'COMMON_STOCK',
                          'PREFERRED_STOCK',
                          'DEPOSITARY_RECEIPT'
                      )
                      AND quote.source LIKE provider.provider || '%'
                      AND quote.event_time <= :now
                      AND quote.received_at <= :now
                    ORDER BY
                        quote.instrument_id,
                        quote.event_time DESC,
                        quote.id DESC
                ),
                latest_pre_trade_batch AS (
                    SELECT batch.id, batch.covered_through, batch.received_at
                    FROM pre_trade_batches batch
                    JOIN pre_trade_stream_cursors seal
                      ON seal.batch_id = batch.id
                    JOIN authorized_provider provider
                      ON provider.data_type = 'delayed-pre-trade-equity'
                     AND batch.provider_contract_id = provider.contract_id
                    ORDER BY batch.report_minute DESC, batch.id DESC
                    LIMIT 1
                ),
                eligible_books AS (
                    SELECT COUNT(*)::int AS count
                    FROM pre_trade_book_states state
                    JOIN latest_pre_trade_batch batch
                      ON batch.id = state.batch_id
                    JOIN authorized_provider provider ON TRUE
                    WHERE state.bid_price IS NOT NULL
                      AND state.ask_price IS NOT NULL
                      AND state.bid_quantity > 0
                      AND state.ask_quantity > 0
                      AND state.trading_system = 'CLOB'
                      AND state.trading_phase = 'COTR'
                      AND state.received_at <= :now
                      AND :now - state.received_at <= (
                          provider.max_transport_lag_seconds
                      ) * INTERVAL '1 second'
                      AND :now - state.covered_through <= (
                          provider.nominal_delay_seconds
                          + provider.max_transport_lag_seconds
                          + 60
                      ) * INTERVAL '1 second'
                )
                SELECT
                    (SELECT provider FROM authorized_provider)
                        AS provider,
                    (SELECT data_type FROM authorized_provider)
                        AS data_type,
                    COALESCE(
                        (SELECT instrument_count FROM latest_reference),
                        0
                    )::int AS expected_instrument_count,
                    universe.active_instrument_count,
                    universe.company_mapping_count,
                    (SELECT status FROM latest_sync)
                        AS latest_sync_status,
                    (SELECT count FROM open_gaps) AS open_gap_count,
                    COALESCE((
                        SELECT COUNT(*)::int
                        FROM latest_quotes quote
                        WHERE quote.event_time >= (
                            SELECT opens_at FROM current_session
                        )
                          AND EXTRACT(EPOCH FROM (
                              :now - quote.event_time
                          )) <= (
                              SELECT
                                  nominal_delay_seconds
                                  + max_transport_lag_seconds
                              FROM authorized_provider
                          )
                    ), 0)::int AS fresh_quote_count,
                    COALESCE(
                        (SELECT count FROM eligible_books),
                        0
                    )::int AS eligible_instrument_count,
                    COALESCE((
                        SELECT
                            status IN ('OPEN', 'HALF_DAY')
                            AND :now >= opens_at
                            AND :now < closes_at
                        FROM current_session
                    ), FALSE) AS session_open
                FROM universe
            """), {"now": now}).mappings().one()

        expected = int(row["expected_instrument_count"])
        data_type = row["data_type"]
        blockers = []
        if row["provider"] is None:
            blockers.append("authorized provider is missing")
        if expected <= 0:
            blockers.append("reference universe is missing")
        if int(row["active_instrument_count"]) != expected:
            blockers.append("active XSTO universe is incomplete")
        if int(row["company_mapping_count"]) != expected:
            blockers.append("XSTO company mapping is incomplete")
        if row["latest_sync_status"] != "SUCCEEDED":
            blockers.append("latest market-data sync did not succeed")
        if int(row["open_gap_count"]) != 0:
            blockers.append("open market-data gaps exist")
        if row["session_open"] is not True:
            blockers.append("XSTO session is not open")
        if data_type == "delayed-pre-trade-equity":
            if int(row["eligible_instrument_count"]) <= 0:
                blockers.append(
                    "no fresh executable two-sided XSTO books are available"
                )
        elif int(row["fresh_quote_count"]) != expected:
            blockers.append("full XSTO quote coverage is missing")
        if blockers:
            raise MarketDataError(
                "operational market data is not ready: "
                + "; ".join(blockers)
            )
        return dict(row)


    def get_authorized_market_data_mode(
        self,
        checked_at: datetime,
    ) -> dict:
        """Return the governed XSTO equity mode without requiring an open session."""
        if (
            not isinstance(checked_at, datetime)
            or checked_at.tzinfo is None
            or checked_at.utcoffset() is None
        ):
            raise MarketDataError("market-data mode clock must be aware")
        with self.Session() as session:
            row = session.execute(text("""
                SELECT
                    contract.provider,
                    contract.data_type,
                    contract.usage_scope,
                    contract.authorization_basis,
                    contract.nominal_delay_seconds,
                    contract.max_transport_lag_seconds
                FROM market_data_provider_contracts contract
                JOIN LATERAL (
                    SELECT candidate.*
                    FROM market_data_provider_validations candidate
                    WHERE candidate.contract_id = contract.id
                      AND candidate.validated_at <= :checked_at
                    ORDER BY candidate.validated_at DESC, candidate.id DESC
                    LIMIT 1
                ) validation ON TRUE
                WHERE contract.mic = 'XSTO'
                  AND contract.data_type IN (
                      'delayed-post-trade-equity',
                      'delayed-pre-trade-equity',
                      'realtime-equity-level-1'
                  )
                  AND contract.status = 'VALIDATED'
                  AND contract.governance_evidence_status = 'VERIFIED'
                  AND (:checked_at AT TIME ZONE 'Europe/Stockholm')::DATE
                        BETWEEN contract.valid_from AND contract.valid_until
                  AND contract.transport_verified_at IS NOT NULL
                  AND validation.status = 'PASSED'
                  AND validation.governance_evidence_status = 'VERIFIED'
                  AND validation.valid_until >= :checked_at
                  AND (
                      (
                          contract.authorization_basis =
                              'NEGOTIATED_CONTRACT'
                          AND contract.legal_reviewed_at IS NOT NULL
                      )
                      OR (
                          contract.authorization_basis =
                              'PUBLIC_NONCOMMERCIAL_TERMS'
                          AND contract.provider = 'nasdaq-nordic'
                          AND contract.data_type =
                              'delayed-pre-trade-equity'
                          AND contract.delivery_mode = 'DELAYED_15M'
                          AND contract.transport = 'PUBLIC_CSV'
                          AND contract.usage_scope =
                              'INTERNAL_ANALYSIS_AND_PAPER'
                          AND contract.raw_storage_allowed = FALSE
                          AND contract.policy_verified_at IS NOT NULL
                          AND contract.policy_verified_by IS NOT NULL
                      )
                  )
                ORDER BY contract.updated_at DESC, contract.id DESC
                LIMIT 1
            """), {"checked_at": checked_at}).mappings().first()
        if row is None:
            raise MarketDataError("authorized XSTO market-data mode is missing")
        return dict(row)

    def upsert_market_session(self, market_session: MarketSession) -> None:
        """Store an explicit exchange session in its named timezone."""
        with self.Session.begin() as session:
            session.execute(text("""
                INSERT INTO market_sessions (
                    mic, session_date, opens_at, closes_at,
                    session_type, status, timezone_name, source,
                    source_updated_at
                )
                VALUES (
                    :mic, :session_date, :opens_at, :closes_at,
                    :session_type, :status, :timezone_name, :source, NOW()
                )
                ON CONFLICT (mic, session_date) DO UPDATE SET
                    opens_at = EXCLUDED.opens_at,
                    closes_at = EXCLUDED.closes_at,
                    session_type = EXCLUDED.session_type,
                    status = EXCLUDED.status,
                    timezone_name = EXCLUDED.timezone_name,
                    source = EXCLUDED.source,
                    source_updated_at = NOW(),
                    updated_at = NOW()
            """), {
                'mic': market_session.mic,
                'session_date': market_session.session_date,
                'opens_at': market_session.opens_at,
                'closes_at': market_session.closes_at,
                'session_type': market_session.session_type,
                'status': market_session.status,
                'timezone_name': market_session.timezone_name,
                'source': market_session.source,
            })

    def sync_market_calendar(
        self,
        snapshot: MarketCalendarSnapshot,
    ) -> CalendarSyncResult:
        """Atomically replace one calendar year and preserve version history."""
        if not isinstance(snapshot, MarketCalendarSnapshot):
            raise MarketDataError(
                "snapshot must be a MarketCalendarSnapshot"
            )
        checksum = snapshot.checksum_sha256
        run_key = (
            f"calendar:{snapshot.mic}:{snapshot.year}:{checksum}"
        )
        year_start = date(snapshot.year, 1, 1)
        year_end = date(snapshot.year + 1, 1, 1)

        with self.Session.begin() as session:
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:run_key, 0))"
                ),
                {"run_key": f"calendar:{snapshot.mic}:{snapshot.year}"},
            )
            current = session.execute(text("""
                SELECT
                    id,
                    checksum_sha256,
                    verified_at,
                    sync_run_id
                FROM market_calendar_snapshots
                WHERE
                    mic = :mic
                    AND year = :year
                    AND is_current
                FOR UPDATE
            """), {
                "mic": snapshot.mic,
                "year": snapshot.year,
            }).mappings().first()
            if current is not None:
                if current["checksum_sha256"].strip() == checksum:
                    return CalendarSyncResult(
                        inserted=False,
                        sessions_upserted=0,
                        sync_run_id=int(current["sync_run_id"]),
                    )
                if snapshot.verified_at <= current["verified_at"]:
                    raise MarketDataError(
                        "calendar correction is not newer than current"
                    )
                previous_version = session.execute(text("""
                    SELECT 1
                    FROM market_calendar_snapshots
                    WHERE
                        mic = :mic
                        AND year = :year
                        AND checksum_sha256 = :checksum
                """), {
                    "mic": snapshot.mic,
                    "year": snapshot.year,
                    "checksum": checksum,
                }).first()
                if previous_version is not None:
                    raise MarketDataError(
                        "calendar correction would restore an old version"
                    )
                session.execute(text("""
                    UPDATE market_calendar_snapshots
                    SET is_current = FALSE
                    WHERE id = :snapshot_id
                """), {"snapshot_id": current["id"]})

            sync_run_id = int(session.execute(text("""
                INSERT INTO data_sync_runs (
                    provider,
                    data_type,
                    started_at,
                    finished_at,
                    status,
                    records_received,
                    records_rejected,
                    records_inserted,
                    run_key
                )
                VALUES (
                    :provider,
                    'market-calendar',
                    NOW(),
                    NOW(),
                    'SUCCEEDED',
                    :records_received,
                    0,
                    :records_inserted,
                    :run_key
                )
                RETURNING id
            """), {
                "provider": snapshot.source,
                "records_received": (
                    len(snapshot.sessions) + len(snapshot.closed_dates)
                ),
                "records_inserted": len(snapshot.sessions),
                "run_key": run_key,
            }).scalar_one())
            session.execute(text("""
                INSERT INTO market_calendar_snapshots (
                    mic,
                    year,
                    source,
                    source_url,
                    verified_at,
                    checksum_sha256,
                    session_count,
                    closed_day_count,
                    half_day_count,
                    sync_run_id,
                    is_current,
                    supersedes_snapshot_id
                )
                VALUES (
                    :mic,
                    :year,
                    :source,
                    :source_url,
                    :verified_at,
                    :checksum,
                    :session_count,
                    :closed_day_count,
                    :half_day_count,
                    :sync_run_id,
                    TRUE,
                    :supersedes_snapshot_id
                )
            """), {
                "mic": snapshot.mic,
                "year": snapshot.year,
                "source": snapshot.source,
                "source_url": snapshot.source_url,
                "verified_at": snapshot.verified_at,
                "checksum": checksum,
                "session_count": len(snapshot.sessions),
                "closed_day_count": len(snapshot.closed_dates),
                "half_day_count": sum(
                    item.status == "HALF_DAY"
                    for item in snapshot.sessions
                ),
                "sync_run_id": sync_run_id,
                "supersedes_snapshot_id": (
                    current["id"] if current is not None else None
                ),
            })
            session.execute(text("""
                DELETE FROM market_sessions
                WHERE
                    mic = :mic
                    AND session_date >= :year_start
                    AND session_date < :year_end
            """), {
                "mic": snapshot.mic,
                "year_start": year_start,
                "year_end": year_end,
            })
            for market_session in snapshot.sessions:
                session.execute(text("""
                    INSERT INTO market_sessions (
                        mic,
                        session_date,
                        opens_at,
                        closes_at,
                        session_type,
                        status,
                        timezone_name,
                        source,
                        source_updated_at
                    )
                    VALUES (
                        :mic,
                        :session_date,
                        :opens_at,
                        :closes_at,
                        :session_type,
                        :status,
                        :timezone_name,
                        :source,
                        :source_updated_at
                    )
                """), {
                    "mic": market_session.mic,
                    "session_date": market_session.session_date,
                    "opens_at": market_session.opens_at,
                    "closes_at": market_session.closes_at,
                    "session_type": market_session.session_type,
                    "status": market_session.status,
                    "timezone_name": market_session.timezone_name,
                    "source": market_session.source,
                    "source_updated_at": snapshot.verified_at,
                })
            return CalendarSyncResult(
                inserted=True,
                sessions_upserted=len(snapshot.sessions),
                sync_run_id=sync_run_id,
            )

    def get_market_session(
        self,
        mic: str,
        session_date: date,
    ) -> Optional[MarketSession]:
        """Return an open/half-day session, or None for closed/missing."""
        with self.Session() as session:
            row = session.execute(text("""
                SELECT
                    mic,
                    session_date,
                    opens_at,
                    closes_at,
                    session_type,
                    timezone_name,
                    source,
                    status
                FROM market_sessions
                WHERE mic = :mic AND session_date = :session_date
            """), {
                'mic': mic,
                'session_date': session_date,
            }).mappings().first()
        if row is None or row['status'] not in {'OPEN', 'HALF_DAY'}:
            return None
        return MarketSession(
            mic=row['mic'].strip(),
            session_date=row['session_date'],
            opens_at=row['opens_at'].astimezone(
                ZoneInfo(row['timezone_name'])
            ),
            closes_at=row['closes_at'].astimezone(
                ZoneInfo(row['timezone_name'])
            ),
            timezone_name=row['timezone_name'],
            source=row['source'],
            session_type=row['session_type'],
            status=row['status'],
        )

    def get_latest_market_session_date(
        self,
        *,
        mic: str,
        on_or_before: date,
    ) -> date | None:
        """Return the latest explicit open/half-day exchange session."""
        if not isinstance(mic, str) or not re.fullmatch(
            r"[A-Z0-9]{4}",
            mic,
        ):
            raise MarketDataError("mic must be a four-character MIC")
        if (
            not isinstance(on_or_before, date)
            or isinstance(on_or_before, datetime)
        ):
            raise MarketDataError("on_or_before must be a date")
        with self.Session() as session:
            return session.execute(text("""
                SELECT MAX(session_date)
                FROM market_sessions
                WHERE mic = :mic
                  AND session_date <= :on_or_before
                  AND status IN ('OPEN', 'HALF_DAY')
            """), {
                "mic": mic,
                "on_or_before": on_or_before,
            }).scalar_one()

    def get_latest_prices(self, tickers: Optional[List[str]] = None) -> pd.DataFrame:
        """Compatibility view over current provider-authorized prices."""
        rows = self.get_latest_authorized_prices()
        if tickers:
            allowed = {
                self._required_sync_text(ticker, "ticker").upper()
                for ticker in tickers
            }
            rows = [
                row
                for row in rows
                if str(row["ticker"]).upper() in allowed
            ]
        return pd.DataFrame(rows)

    def get_portfolio(self) -> pd.DataFrame:
        """Get current portfolio positions."""
        with self.Session() as session:
            result = session.execute(text("SELECT * FROM portfolio"))
            return pd.DataFrame(result.fetchall(), columns=result.keys())

    def get_balance(self) -> dict:
        """Get cash and fail-closed provider-authorized marked value."""
        with self.Session() as session:
            result = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE},
                latest_balance AS (
                    SELECT cash, updated_at
                    FROM balance
                    ORDER BY id DESC
                    LIMIT 1
                ),
                position_marks AS (
                    SELECT
                        position.ticker,
                        position.shares,
                        mark.evidence_id,
                        mark.last_price
                    FROM portfolio position
                    LEFT JOIN companies company
                      ON company.ticker = position.ticker
                    LEFT JOIN instruments instrument
                      ON instrument.id = company.instrument_id
                     AND instrument.mic = 'XSTO'
                     AND instrument.status = 'ACTIVE'
                    LEFT JOIN authorized_provider provider ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT *
                        FROM (
                            SELECT
                                candidate.id AS evidence_id,
                                candidate.last_price
                            FROM market_quotes candidate
                            JOIN market_data_files source_file
                              ON source_file.id = candidate.data_file_id
                             AND source_file.provider_contract_id =
                                provider.contract_id
                            WHERE provider.data_type !=
                                'delayed-pre-trade-equity'
                              AND candidate.instrument_id = instrument.id
                              AND candidate.source LIKE
                                  provider.provider || '%'
                              AND candidate.event_time <= NOW()
                              AND candidate.received_at <= NOW()
                            ORDER BY
                                candidate.event_time DESC,
                                candidate.id DESC
                            LIMIT 1
                        ) last_trade
                        UNION ALL
                        SELECT *
                        FROM (
                            SELECT
                                state.id AS evidence_id,
                                (state.bid_price + state.ask_price) / 2
                                    AS last_price
                            FROM pre_trade_batches batch
                            JOIN pre_trade_stream_cursors seal
                              ON seal.batch_id = batch.id
                            JOIN pre_trade_book_states state
                              ON state.batch_id = batch.id
                             AND state.instrument_id = instrument.id
                            WHERE provider.data_type =
                                'delayed-pre-trade-equity'
                              AND batch.provider_contract_id =
                                provider.contract_id
                              AND state.bid_price IS NOT NULL
                              AND state.ask_price IS NOT NULL
                              AND state.trading_system = 'CLOB'
                              AND state.trading_phase = 'COTR'
                              AND state.received_at <= NOW()
                            ORDER BY
                                batch.report_minute DESC,
                                batch.id DESC
                            LIMIT 1
                        ) pre_trade
                        LIMIT 1
                    ) mark ON TRUE
                    WHERE position.shares > 0
                )
                SELECT
                    balance.cash,
                    balance.cash + COALESCE(
                        SUM(mark.shares * mark.last_price),
                        0
                    ) AS total_value,
                    balance.updated_at,
                    COUNT(*) FILTER (
                        WHERE mark.ticker IS NOT NULL
                          AND mark.evidence_id IS NULL
                    )::int AS missing_quote_count,
                    ARRAY_AGG(mark.ticker ORDER BY mark.ticker) FILTER (
                        WHERE mark.ticker IS NOT NULL
                          AND mark.evidence_id IS NULL
                    ) AS missing_tickers
                FROM latest_balance balance
                LEFT JOIN position_marks mark ON TRUE
                GROUP BY
                    balance.cash,
                    balance.updated_at
            """))
            row = result.mappings().first()
            if row is None:
                raise RuntimeError("Balance row is missing")
            missing_tickers = list(row["missing_tickers"] or [])
            valuation_complete = row["missing_quote_count"] == 0
            return {
                'cash': float(row["cash"]),
                'total_value': (
                    float(row["total_value"])
                    if valuation_complete
                    else None
                ),
                'updated_at': row["updated_at"],
                'valuation_complete': valuation_complete,
                'unpriced_tickers': missing_tickers,
            }

    def get_trading_control(self) -> TradingControlState:
        """Return the singleton operator-owned entry-risk control."""
        with self.Session() as session:
            row = session.execute(text("""
                SELECT
                    status,
                    max_daily_loss_pct,
                    reason,
                    changed_by,
                    changed_at
                FROM trading_controls
                WHERE singleton = TRUE
            """)).mappings().first()
        if row is None:
            raise RuntimeError("Trading risk control is missing")
        return self._trading_control_state(row)

    def update_trading_control(
        self,
        *,
        reason: str,
        changed_by: str,
        status: str | None = None,
        max_daily_loss_pct: float | None = None,
    ) -> TradingControlState:
        """Apply one human-authorized, append-only audited control change."""
        operator = self._operator_identity(changed_by)
        change_reason = self._bounded_identity_text(
            reason,
            "trading control reason",
            500,
        )
        normalized_status = None
        if status is not None:
            if not isinstance(status, str):
                raise ValueError("status must be ACTIVE or HALTED")
            normalized_status = status.strip().upper()
            if normalized_status not in {"ACTIVE", "HALTED"}:
                raise ValueError("status must be ACTIVE or HALTED")

        normalized_limit = None
        if max_daily_loss_pct is not None:
            normalized_limit = self._finite_decimal(
                max_daily_loss_pct,
                "max_daily_loss_pct",
            )
            if not Decimal("0") < normalized_limit <= Decimal("20"):
                raise ValueError(
                    "max_daily_loss_pct must be greater than 0 "
                    "and at most 20"
                )
            normalized_limit = normalized_limit.quantize(Decimal("0.01"))
        if normalized_status is None and normalized_limit is None:
            raise ValueError("At least one trading control change is required")

        with self.Session.begin() as session:
            current = session.execute(text("""
                SELECT
                    status,
                    max_daily_loss_pct,
                    reason,
                    changed_by,
                    changed_at
                FROM trading_controls
                WHERE singleton = TRUE
                FOR UPDATE
            """)).mappings().first()
            if current is None:
                raise RuntimeError("Trading risk control is missing")

            new_status = normalized_status or current["status"]
            new_limit = (
                normalized_limit
                if normalized_limit is not None
                else Decimal(current["max_daily_loss_pct"])
            )
            if (
                new_status != current["status"]
                and new_limit != Decimal(current["max_daily_loss_pct"])
            ):
                event_type = "CONTROL_UPDATE"
            elif new_status == "HALTED" and current["status"] != "HALTED":
                event_type = "HALT"
            elif new_status == "ACTIVE" and current["status"] != "ACTIVE":
                event_type = "RESUME"
            elif new_limit != Decimal(current["max_daily_loss_pct"]):
                event_type = "LIMIT_CHANGE"
            else:
                event_type = "CONTROL_UPDATE"

            stored_reason = change_reason if new_status == "HALTED" else None
            updated = session.execute(text("""
                UPDATE trading_controls
                SET
                    status = :status,
                    max_daily_loss_pct = :max_daily_loss_pct,
                    reason = :stored_reason,
                    changed_by = :changed_by,
                    changed_at = NOW()
                WHERE singleton = TRUE
                RETURNING
                    status,
                    max_daily_loss_pct,
                    reason,
                    changed_by,
                    changed_at
            """), {
                "status": new_status,
                "max_daily_loss_pct": new_limit,
                "stored_reason": stored_reason,
                "changed_by": operator,
            }).mappings().one()
            session.execute(text("""
                INSERT INTO trading_control_events (
                    event_type,
                    previous_status,
                    new_status,
                    previous_max_daily_loss_pct,
                    new_max_daily_loss_pct,
                    reason,
                    changed_by
                )
                VALUES (
                    :event_type,
                    :previous_status,
                    :new_status,
                    :previous_max_daily_loss_pct,
                    :new_max_daily_loss_pct,
                    :reason,
                    :changed_by
                )
            """), {
                "event_type": event_type,
                "previous_status": current["status"],
                "new_status": new_status,
                "previous_max_daily_loss_pct": (
                    current["max_daily_loss_pct"]
                ),
                "new_max_daily_loss_pct": new_limit,
                "reason": change_reason,
                "changed_by": operator,
            })
        return self._trading_control_state(updated)

    def evaluate_entry_risk(
        self,
        *,
        total_value: float,
        evaluated_at: datetime,
    ) -> EntryRiskDecision:
        """Latch a daily mark-to-market loss breach for new entries."""
        current_equity = self._finite_decimal(total_value, "total_value")
        if current_equity < 0:
            raise ValueError("total_value must be non-negative")
        current_equity = current_equity.quantize(Decimal("0.00000001"))
        if (
            not isinstance(evaluated_at, datetime)
            or evaluated_at.tzinfo is None
            or evaluated_at.utcoffset() is None
        ):
            raise ValueError("evaluated_at must be timezone-aware")
        evaluated_at = evaluated_at.astimezone(timezone.utc)
        session_date = evaluated_at.astimezone(
            ZoneInfo("Europe/Stockholm")
        ).date()

        with self.Session.begin() as session:
            control = session.execute(text("""
                SELECT status, max_daily_loss_pct
                FROM trading_controls
                WHERE singleton = TRUE
                FOR UPDATE
            """)).mappings().first()
            if control is None:
                raise RuntimeError("Trading risk control is missing")

            daily = session.execute(text("""
                SELECT
                    opening_equity,
                    limit_breached,
                    first_evaluated_at
                FROM trading_daily_risk
                WHERE session_date = :session_date
                FOR UPDATE
            """), {
                "session_date": session_date,
            }).mappings().first()
            if daily is None:
                prior_close = session.execute(text("""
                    SELECT history.total_value
                    FROM portfolio_history history
                    WHERE (
                        history.recorded_at
                        AT TIME ZONE 'Europe/Stockholm'
                    )::DATE < :session_date
                      AND (
                          history.positions_value = 0
                          OR EXISTS (
                              SELECT 1
                              FROM portfolio_valuation_marks mark
                              WHERE mark.portfolio_history_id = history.id
                              GROUP BY mark.portfolio_history_id
                              HAVING SUM(mark.market_value)
                                  = history.positions_value
                          )
                      )
                    ORDER BY history.recorded_at DESC, history.id DESC
                    LIMIT 1
                """), {
                    "session_date": session_date,
                }).scalar()
                opening_equity = (
                    Decimal(prior_close)
                    if prior_close is not None
                    else current_equity
                ).quantize(Decimal("0.00000001"))
                if opening_equity <= 0:
                    raise ValueError(
                        "opening equity must be greater than 0"
                    )
                already_breached = False
                first_evaluated_at = evaluated_at
            else:
                opening_equity = Decimal(daily["opening_equity"])
                already_breached = bool(daily["limit_breached"])
                first_evaluated_at = daily["first_evaluated_at"]

            decision = evaluate_entry_risk(
                trading_status=control["status"],
                opening_equity=opening_equity,
                current_equity=current_equity,
                max_daily_loss_pct=control["max_daily_loss_pct"],
            )
            if already_breached and decision.reason != "TRADING_HALTED":
                decision = EntryRiskDecision(
                    allowed=False,
                    reason="DAILY_LOSS_LIMIT",
                    daily_return_pct=decision.daily_return_pct,
                )
            daily_return_pct = Decimal(
                str(decision.daily_return_pct)
            ).quantize(Decimal("0.00000001"))
            limit_breached = (
                already_breached
                or decision.reason == "DAILY_LOSS_LIMIT"
            )

            session.execute(text("""
                INSERT INTO trading_daily_risk (
                    session_date,
                    opening_equity,
                    latest_equity,
                    daily_return_pct,
                    limit_breached,
                    first_evaluated_at,
                    last_evaluated_at
                )
                VALUES (
                    :session_date,
                    :opening_equity,
                    :latest_equity,
                    :daily_return_pct,
                    :limit_breached,
                    :first_evaluated_at,
                    :last_evaluated_at
                )
                ON CONFLICT (session_date) DO UPDATE SET
                    latest_equity = EXCLUDED.latest_equity,
                    daily_return_pct = EXCLUDED.daily_return_pct,
                    limit_breached = (
                        trading_daily_risk.limit_breached
                        OR EXCLUDED.limit_breached
                    ),
                    last_evaluated_at = EXCLUDED.last_evaluated_at
            """), {
                "session_date": session_date,
                "opening_equity": opening_equity,
                "latest_equity": current_equity,
                "daily_return_pct": daily_return_pct,
                "limit_breached": limit_breached,
                "first_evaluated_at": first_evaluated_at,
                "last_evaluated_at": evaluated_at,
            })
            session.execute(text("""
                INSERT INTO trading_risk_evaluations (
                    session_date,
                    trading_status,
                    opening_equity,
                    current_equity,
                    daily_return_pct,
                    max_daily_loss_pct,
                    allowed,
                    reason,
                    evaluated_at
                )
                VALUES (
                    :session_date,
                    :trading_status,
                    :opening_equity,
                    :current_equity,
                    :daily_return_pct,
                    :max_daily_loss_pct,
                    :allowed,
                    :reason,
                    :evaluated_at
                )
            """), {
                "session_date": session_date,
                "trading_status": control["status"],
                "opening_equity": opening_equity,
                "current_equity": current_equity,
                "daily_return_pct": daily_return_pct,
                "max_daily_loss_pct": control["max_daily_loss_pct"],
                "allowed": decision.allowed,
                "reason": decision.reason or "ENTRY_ALLOWED",
                "evaluated_at": evaluated_at,
            })
        return decision

    @staticmethod
    def _trading_control_state(row: Any) -> TradingControlState:
        return TradingControlState(
            status=row["status"],
            max_daily_loss_pct=float(row["max_daily_loss_pct"]),
            reason=row["reason"],
            changed_by=row["changed_by"],
            changed_at=row["changed_at"],
        )

    def log_ai_decision(
        self,
        *,
        timestamp: datetime,
        prompt_tokens: int,
        response_tokens: int,
        decisions_json: Any,
        market_data_json: str,
        raw_response: str,
        strategy_version: str,
        strategy_config_hash: str,
        model_backend: str = "legacy",
        model_name: str = "unknown",
        model_provider: str | None = None,
        reasoning_effort: str | None = None,
        response_model: str | None = None,
        response_id: str | None = None,
    ) -> int:
        """Persist one versioned AI audit record and return its stable id."""
        if (
            not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            raise ValueError("timestamp must be timezone-aware")
        normalized_tokens = {}
        for field, value in (
            ("prompt_tokens", prompt_tokens),
            ("response_tokens", response_tokens),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 10_000_000
            ):
                raise ValueError(
                    f"{field} must be a non-negative integer"
                )
            normalized_tokens[field] = value
        try:
            decisions_text = json.dumps(
                decisions_json,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("decisions_json must be valid JSON") from exc
        if len(decisions_text) > 100_000:
            raise ValueError("decisions_json is too large")
        if not isinstance(market_data_json, str):
            raise ValueError("market_data_json must be text")
        if not isinstance(raw_response, str):
            raise ValueError("raw_response must be text")
        if len(market_data_json) > 10_000:
            raise ValueError("market_data_json is too large")
        if len(raw_response) > 5_000:
            raise ValueError("raw_response is too large")
        if (
            not isinstance(strategy_version, str)
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{2,49}",
                strategy_version,
            )
        ):
            raise ValueError("strategy_version has invalid format")
        if (
            not isinstance(strategy_config_hash, str)
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                strategy_config_hash,
            )
        ):
            raise ValueError("strategy_config_hash must be lowercase SHA-256")
        if (
            not isinstance(model_backend, str)
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{1,29}",
                model_backend,
            )
        ):
            raise ValueError("model_backend has invalid format")
        if (
            not isinstance(model_name, str)
            or model_name != model_name.strip()
            or not 1 <= len(model_name) <= 200
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in model_name
            )
        ):
            raise ValueError("model_name has invalid format")
        for field, value, maximum in (
            ("model_provider", model_provider, 80),
            ("response_model", response_model, 200),
            ("response_id", response_id, 200),
        ):
            if value is None:
                continue
            if (
                not isinstance(value, str)
                or value != value.strip()
                or not 1 <= len(value) <= maximum
                or any(
                    ord(character) < 32 or ord(character) == 127
                    for character in value
                )
            ):
                raise ValueError(f"{field} has invalid format")
        if reasoning_effort not in {
            None,
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise ValueError("reasoning_effort is unsupported")

        with self.Session.begin() as session:
            decision_id = session.execute(text("""
                INSERT INTO ai_decisions (
                    timestamp,
                    prompt_tokens,
                    response_tokens,
                    decisions_json,
                    market_data_json,
                    raw_response,
                    strategy_version,
                    strategy_config_hash,
                    model_backend,
                    model_name,
                    model_provider,
                    reasoning_effort,
                    response_model,
                    response_id
                )
                VALUES (
                    :timestamp,
                    :prompt_tokens,
                    :response_tokens,
                    :decisions_json,
                    :market_data_json,
                    :raw_response,
                    :strategy_version,
                    :strategy_config_hash,
                    :model_backend,
                    :model_name,
                    :model_provider,
                    :reasoning_effort,
                    :response_model,
                    :response_id
                )
                RETURNING id
            """), {
                "timestamp": timestamp.astimezone(timezone.utc),
                **normalized_tokens,
                "decisions_json": decisions_text,
                "market_data_json": market_data_json,
                "raw_response": raw_response,
                "strategy_version": strategy_version,
                "strategy_config_hash": strategy_config_hash,
                "model_backend": model_backend,
                "model_name": model_name,
                "model_provider": model_provider,
                "reasoning_effort": reasoning_effort,
                "response_model": response_model,
                "response_id": response_id,
            }).scalar_one()
        return int(decision_id)


    def get_active_candidate_policy(self):
        """Return the hash-verified deterministic candidate gate."""
        from ..core.candidates import (
            CandidatePolicy,
            candidate_policy_hash,
        )

        with self.Session() as session:
            row = session.execute(text("""
                SELECT
                    version,
                    config,
                    RTRIM(config_hash) AS config_hash
                FROM candidate_policy_versions
                WHERE status = 'ACTIVE'
            """)).mappings().one_or_none()
        if row is None:
            raise RuntimeError("No active candidate policy exists")
        policy = CandidatePolicy.from_mapping(
            version=row["version"],
            config=row["config"],
        )
        if candidate_policy_hash(policy) != row["config_hash"]:
            raise RuntimeError("Candidate policy config hash mismatch")
        return policy


    def record_candidate_predictions(
        self,
        *,
        ai_decision_id: int,
        candidates: List[dict],
        model_decisions: List[dict],
    ) -> List[int]:
        """Bind every ranked candidate, including abstentions, to one AI audit."""
        if (
            isinstance(ai_decision_id, bool)
            or not isinstance(ai_decision_id, int)
            or ai_decision_id <= 0
        ):
            raise ValueError("ai_decision_id must be a positive integer")
        if not isinstance(candidates, list) or len(candidates) > 1_000:
            raise ValueError("candidates must be a bounded list")
        if not isinstance(model_decisions, list) or len(model_decisions) > 100:
            raise ValueError("model_decisions must be a bounded list")

        action_by_ticker = {}
        for decision in model_decisions:
            if not isinstance(decision, dict):
                raise ValueError("model decision must be an object")
            ticker = self._required_sync_text(
                decision.get("ticker"),
                "model decision ticker",
            ).upper()
            action = self._required_sync_text(
                decision.get("action"),
                "model decision action",
            ).upper()
            if action not in {"BUY", "SELL", "HOLD"}:
                raise ValueError("model decision action is unsupported")
            if ticker in action_by_ticker:
                raise ValueError("duplicate model decision ticker")
            confidence = self._finite_decimal(
                decision.get("confidence"),
                "model decision confidence",
            )
            if confidence < 0 or confidence > 100:
                raise ValueError("model decision confidence must be 0-100")
            action_by_ticker[ticker] = (action, confidence)

        normalized = []
        seen = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError("candidate must be an object")
            ticker = self._required_sync_text(
                candidate.get("ticker"),
                "candidate ticker",
            ).upper()
            if ticker in seen:
                raise ValueError("duplicate candidate ticker")
            seen.add(ticker)
            policy_version = self._required_sync_text(
                candidate.get("policy_version"),
                "candidate policy_version",
            )
            if not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{2,49}",
                policy_version,
            ):
                raise ValueError("candidate policy_version has invalid format")
            rank = candidate.get("rank")
            if (
                isinstance(rank, bool)
                or not isinstance(rank, int)
                or not 1 <= rank <= 1_000
            ):
                raise ValueError("candidate rank must be 1-1000")
            score = self._finite_decimal(
                candidate.get("signal_score"),
                "candidate signal_score",
            )
            if score < 0 or score > 100:
                raise ValueError("candidate signal_score must be 0-100")
            eligible = candidate.get("eligible")
            if not isinstance(eligible, bool):
                raise ValueError("candidate eligible must be boolean")
            reason_code = self._required_sync_text(
                candidate.get("reason_code"),
                "candidate reason_code",
            )
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,39}", reason_code):
                raise ValueError("candidate reason_code has invalid format")
            book_state_id = candidate.get("book_state_id")
            if (
                isinstance(book_state_id, bool)
                or not isinstance(book_state_id, int)
                or book_state_id <= 0
            ):
                raise ValueError("candidate book_state_id must be positive")
            latest_price = self._positive_decimal(
                candidate.get("latest_price"),
                "candidate latest_price",
            )
            window_start = candidate.get("first_report_minute")
            window_end = candidate.get("last_report_minute")
            for field, value in (
                ("first_report_minute", window_start),
                ("last_report_minute", window_end),
            ):
                if (
                    not isinstance(value, datetime)
                    or value.tzinfo is None
                    or value.utcoffset() is None
                ):
                    raise ValueError(f"candidate {field} must be timezone-aware")
            if window_end - window_start != timedelta(minutes=60):
                raise ValueError("candidate feature window must be 60 minutes")
            feature_json = candidate.get("feature_json")
            if not isinstance(feature_json, dict):
                raise ValueError("candidate feature_json must be an object")
            try:
                feature_text = json.dumps(
                    feature_json,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("candidate feature_json must be valid JSON") from exc
            if len(feature_text) > 20_000:
                raise ValueError("candidate feature_json is too large")
            model_action, model_confidence = action_by_ticker.get(
                ticker,
                ("ABSTAIN", None),
            )
            normalized.append({
                "ticker": ticker,
                "policy_version": policy_version,
                "book_state_id": book_state_id,
                "window_start": window_start.astimezone(timezone.utc),
                "window_end": window_end.astimezone(timezone.utc),
                "latest_price": latest_price,
                "signal_rank": rank,
                "signal_score": score,
                "eligible": eligible,
                "reason_code": reason_code,
                "feature_json": feature_text,
                "model_action": model_action,
                "model_confidence": model_confidence,
            })

        ids = []
        with self.Session.begin() as session:
            parent_exists = session.execute(text("""
                SELECT EXISTS(
                    SELECT 1 FROM ai_decisions WHERE id = :decision_id
                )
            """), {"decision_id": ai_decision_id}).scalar_one()
            if not parent_exists:
                raise ValueError("parent AI decision does not exist")

            for values in normalized:
                inserted_id = session.execute(text("""
                    WITH parent AS (
                        SELECT
                            id,
                            timestamp,
                            strategy_version,
                            strategy_config_hash
                        FROM ai_decisions
                        WHERE id = :decision_id
                    ),
                    evidence AS (
                        SELECT
                            state.id AS state_id,
                            parent.timestamp AS decision_timestamp,
                            COALESCE(
                                ARRAY_AGG(horizon ORDER BY horizon) FILTER (
                                    WHERE market_session.id IS NOT NULL
                                      AND DATE_TRUNC(
                                            'minute',
                                            parent.timestamp
                                          )
                                         + horizon * INTERVAL '1 minute'
                                         < market_session.closes_at
                                ),
                                ARRAY[]::INTEGER[]
                            ) AS expected_horizons
                        FROM pre_trade_book_states state
                        JOIN pre_trade_batches batch
                          ON batch.id = state.batch_id
                        CROSS JOIN parent
                        CROSS JOIN UNNEST(ARRAY[30, 60, 120]) AS horizon
                        LEFT JOIN market_sessions market_session
                          ON market_session.mic = 'XSTO'
                         AND market_session.status IN ('OPEN', 'HALF_DAY')
                         AND DATE_TRUNC('minute', parent.timestamp)
                                >= market_session.opens_at
                         AND DATE_TRUNC('minute', parent.timestamp)
                                < market_session.closes_at
                        WHERE state.id = :book_state_id
                        GROUP BY state.id, parent.timestamp
                    )
                    INSERT INTO candidate_predictions (
                        ai_decision_id,
                        policy_version,
                        strategy_version,
                        strategy_config_hash,
                        ticker,
                        observed_at,
                        source_book_state_id,
                        feature_window_start,
                        feature_window_end,
                        latest_price,
                        signal_rank,
                        signal_score,
                        eligible,
                        reason_code,
                        feature_json,
                        feature_checksum_sha256,
                        model_action,
                        model_confidence,
                        expected_horizons_minutes
                    )
                    SELECT
                        parent.id,
                        :policy_version,
                        parent.strategy_version,
                        parent.strategy_config_hash,
                        :ticker,
                        parent.timestamp,
                        evidence.state_id,
                        :window_start,
                        :window_end,
                        :latest_price,
                        :signal_rank,
                        :signal_score,
                        :eligible,
                        :reason_code,
                        CAST(:feature_json AS JSONB),
                        ENCODE(
                            SHA256(
                                CONVERT_TO(
                                    CAST(:feature_json AS JSONB)::TEXT,
                                    'UTF8'
                                )
                            ),
                            'hex'
                        ),
                        :model_action,
                        :model_confidence,
                        evidence.expected_horizons
                    FROM parent
                    JOIN evidence ON TRUE
                    ON CONFLICT (
                        ai_decision_id,
                        policy_version,
                        ticker
                    ) DO NOTHING
                    RETURNING id
                """), {
                    "decision_id": ai_decision_id,
                    **values,
                }).scalar()
                if inserted_id is None:
                    existing = session.execute(text("""
                        SELECT
                            id,
                            source_book_state_id,
                            feature_checksum_sha256,
                            signal_rank,
                            signal_score,
                            eligible,
                            reason_code,
                            model_action,
                            model_confidence
                        FROM candidate_predictions
                        WHERE ai_decision_id = :decision_id
                          AND policy_version = :policy_version
                          AND ticker = :ticker
                    """), {
                        "decision_id": ai_decision_id,
                        **values,
                    }).mappings().one()
                    expected_checksum = session.execute(text("""
                        SELECT ENCODE(
                            SHA256(
                                CONVERT_TO(
                                    CAST(:feature_json AS JSONB)::TEXT,
                                    'UTF8'
                                )
                            ),
                            'hex'
                        )
                    """), values).scalar_one()
                    expected = (
                        values["book_state_id"],
                        expected_checksum,
                        values["signal_rank"],
                        values["signal_score"],
                        values["eligible"],
                        values["reason_code"],
                        values["model_action"],
                        values["model_confidence"],
                    )
                    actual = (
                        int(existing["source_book_state_id"]),
                        existing["feature_checksum_sha256"],
                        int(existing["signal_rank"]),
                        Decimal(existing["signal_score"]),
                        bool(existing["eligible"]),
                        existing["reason_code"],
                        existing["model_action"],
                        (
                            Decimal(existing["model_confidence"])
                            if existing["model_confidence"] is not None
                            else None
                        ),
                    )
                    if actual != expected:
                        raise ValueError(
                            "candidate prediction idempotency conflict"
                        )
                    inserted_id = existing["id"]
                ids.append(int(inserted_id))
        return ids

    def record_candidate_prediction_outcomes(
        self,
        *,
        evaluated_at: datetime,
    ) -> int:
        """Lock decision-time paper entries, then label exact later books."""
        if (
            not isinstance(evaluated_at, datetime)
            or evaluated_at.tzinfo is None
            or evaluated_at.utcoffset() is None
        ):
            raise ValueError("evaluated_at must be timezone-aware")
        checked_at = evaluated_at.astimezone(timezone.utc)
        with self.Session.begin() as session:
            session.execute(text("""
                WITH due AS (
                    SELECT
                        prediction.id AS prediction_id,
                        DATE_TRUNC('minute', prediction.observed_at)
                            AS requested_entry_event_time,
                        prediction_state.instrument_id,
                        prediction_batch.provider_contract_id
                    FROM candidate_predictions prediction
                    JOIN pre_trade_book_states prediction_state
                      ON prediction_state.id =
                            prediction.source_book_state_id
                    JOIN pre_trade_batches prediction_batch
                      ON prediction_batch.id = prediction_state.batch_id
                    LEFT JOIN candidate_prediction_entries entry
                      ON entry.prediction_id = prediction.id
                    WHERE entry.id IS NULL
                      AND DATE_TRUNC('minute', prediction.observed_at)
                            <= :evaluated_at
                ),
                evidence AS (
                    SELECT
                        due.*,
                        candidate.entry_event_time,
                        candidate.entry_state_id,
                        candidate.entry_price
                    FROM due
                    JOIN LATERAL (
                        SELECT
                            batch.report_minute AS entry_event_time,
                            state.id AS entry_state_id,
                            (state.bid_price + state.ask_price) / 2
                                AS entry_price
                        FROM pre_trade_batches batch
                        JOIN pre_trade_stream_cursors seal
                          ON seal.batch_id = batch.id
                        JOIN pre_trade_book_states state
                          ON state.batch_id = batch.id
                         AND state.instrument_id = due.instrument_id
                        WHERE batch.provider_contract_id =
                                due.provider_contract_id
                          AND batch.report_minute >=
                                due.requested_entry_event_time
                          AND batch.report_minute <=
                                due.requested_entry_event_time
                                + INTERVAL '2 minutes'
                          AND state.bid_price IS NOT NULL
                          AND state.ask_price IS NOT NULL
                          AND state.bid_quantity > 0
                          AND state.ask_quantity > 0
                          AND state.trading_system = 'CLOB'
                          AND state.trading_phase = 'COTR'
                          AND state.received_at <= :evaluated_at
                        ORDER BY
                            batch.report_minute,
                            batch.id,
                            state.id
                        LIMIT 1
                    ) candidate ON TRUE
                )
                INSERT INTO candidate_prediction_entries (
                    prediction_id,
                    entry_event_time,
                    evaluated_at,
                    source_book_state_id,
                    entry_price
                )
                SELECT
                    evidence.prediction_id,
                    evidence.entry_event_time,
                    :evaluated_at,
                    evidence.entry_state_id,
                    evidence.entry_price
                FROM evidence
                ON CONFLICT (prediction_id) DO NOTHING
            """), {"evaluated_at": checked_at})

            inserted = session.execute(text("""
                WITH due AS (
                    SELECT
                        prediction.id AS prediction_id,
                        prediction.source_book_state_id
                            AS prediction_state_id,
                        entry.id AS entry_id,
                        entry.entry_price,
                        entry.entry_event_time,
                        horizon,
                        entry.entry_event_time
                            + horizon * INTERVAL '1 minute'
                            AS target_event_time,
                        prediction_state.instrument_id,
                        prediction_batch.provider_contract_id
                    FROM candidate_predictions prediction
                    JOIN candidate_prediction_entries entry
                      ON entry.prediction_id = prediction.id
                    JOIN pre_trade_book_states prediction_state
                      ON prediction_state.id =
                            prediction.source_book_state_id
                    JOIN pre_trade_batches prediction_batch
                      ON prediction_batch.id = prediction_state.batch_id
                    CROSS JOIN LATERAL UNNEST(
                        prediction.expected_horizons_minutes
                    ) AS horizon
                    LEFT JOIN candidate_prediction_outcomes outcome
                      ON outcome.prediction_id = prediction.id
                     AND outcome.horizon_minutes = horizon
                    WHERE outcome.id IS NULL
                      AND entry.entry_event_time
                            + horizon * INTERVAL '1 minute'
                            <= :evaluated_at
                ),
                evidence AS (
                    SELECT
                        due.*,
                        candidate.actual_target_event_time,
                        candidate.outcome_state_id,
                        candidate.observed_price
                    FROM due
                    JOIN LATERAL (
                        SELECT
                            batch.report_minute
                                AS actual_target_event_time,
                            state.id AS outcome_state_id,
                            (state.bid_price + state.ask_price) / 2
                                AS observed_price
                        FROM pre_trade_batches batch
                        JOIN pre_trade_stream_cursors seal
                          ON seal.batch_id = batch.id
                        JOIN pre_trade_book_states state
                          ON state.batch_id = batch.id
                         AND state.instrument_id = due.instrument_id
                        WHERE batch.provider_contract_id =
                                due.provider_contract_id
                          AND batch.report_minute >= due.target_event_time
                          AND batch.report_minute <=
                                due.target_event_time
                                + INTERVAL '2 minutes'
                          AND state.bid_price IS NOT NULL
                          AND state.ask_price IS NOT NULL
                          AND state.bid_quantity > 0
                          AND state.ask_quantity > 0
                          AND state.trading_system = 'CLOB'
                          AND state.trading_phase = 'COTR'
                          AND state.received_at <= :evaluated_at
                        ORDER BY
                            batch.report_minute,
                            batch.id,
                            state.id
                        LIMIT 1
                    ) candidate ON TRUE
                ),
                inserted AS (
                    INSERT INTO candidate_prediction_outcomes (
                        prediction_id,
                        entry_id,
                        horizon_minutes,
                        target_event_time,
                        evaluated_at,
                        source_book_state_id,
                        observed_price,
                        return_bps
                    )
                    SELECT
                        evidence.prediction_id,
                        evidence.entry_id,
                        evidence.horizon,
                        evidence.actual_target_event_time,
                        :evaluated_at,
                        evidence.outcome_state_id,
                        evidence.observed_price,
                        (
                            evidence.observed_price
                            / evidence.entry_price
                            - 1
                        ) * 10000
                    FROM evidence
                    ON CONFLICT (prediction_id, horizon_minutes)
                    DO NOTHING
                    RETURNING id
                )
                SELECT COUNT(*) FROM inserted
            """), {"evaluated_at": checked_at}).scalar_one()
        return int(inserted)

    def get_continuous_learning_status(
        self,
        *,
        now: datetime,
    ) -> dict:
        """Summarise point-in-time candidate outcomes without claiming causality."""
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("continuous learning clock must be aware")
        checked_at = now.astimezone(timezone.utc)
        with self.Session() as session:
            summary = session.execute(text("""
                SELECT
                    COUNT(*)::INTEGER AS predictions,
                    COALESCE(
                        SUM(CARDINALITY(expected_horizons_minutes)),
                        0
                    )::INTEGER AS expected_outcomes,
                    COUNT(DISTINCT policy_version)::INTEGER
                        AS policy_versions,
                    (ARRAY_AGG(
                        policy_version
                        ORDER BY observed_at DESC, id DESC
                    ))[1] AS policy_version,
                    MAX(observed_at) AS latest_prediction_at
                FROM candidate_predictions
                WHERE observed_at <= :now
            """), {"now": checked_at}).mappings().one()
            outcome_summary = session.execute(text("""
                SELECT
                    COUNT(*)::INTEGER AS labelled_outcomes,
                    COUNT(DISTINCT prediction_id)::INTEGER
                        AS labelled_predictions,
                    MAX(evaluated_at) AS latest_evaluated_at
                FROM candidate_prediction_outcomes
                WHERE evaluated_at <= :now
            """), {"now": checked_at}).mappings().one()
            matured_outcomes = session.execute(text("""
                SELECT COUNT(*)::INTEGER
                FROM candidate_predictions prediction
                CROSS JOIN LATERAL UNNEST(
                    prediction.expected_horizons_minutes
                ) AS horizon
                WHERE prediction.observed_at <= :now
                  AND DATE_TRUNC('minute', prediction.observed_at)
                        + horizon * INTERVAL '1 minute' <= :now
            """), {"now": checked_at}).scalar_one()
            action_rows = session.execute(text("""
                SELECT
                    prediction.model_action AS action,
                    outcome.horizon_minutes,
                    COUNT(outcome.id)::INTEGER AS outcomes,
                    AVG(outcome.return_bps) AS mean_return_bps,
                    100.0 * AVG(
                        CASE WHEN outcome.return_bps > 0 THEN 1.0 ELSE 0.0 END
                    ) AS positive_rate_pct
                FROM candidate_predictions prediction
                JOIN candidate_prediction_outcomes outcome
                  ON outcome.prediction_id = prediction.id
                WHERE prediction.observed_at <= :now
                  AND outcome.evaluated_at <= :now
                GROUP BY
                    prediction.model_action,
                    outcome.horizon_minutes
                ORDER BY
                    prediction.model_action,
                    outcome.horizon_minutes
            """), {"now": checked_at}).mappings().all()

        predictions = int(summary["predictions"] or 0)
        expected = int(summary["expected_outcomes"] or 0)
        matured = int(matured_outcomes or 0)
        labelled = int(outcome_summary["labelled_outcomes"] or 0)
        overdue = max(0, matured - labelled)
        return {
            "policy_version": summary["policy_version"],
            "policy_versions": int(summary["policy_versions"] or 0),
            "predictions": predictions,
            "expected_outcomes": expected,
            "matured_outcomes": matured,
            "labelled_predictions": int(
                outcome_summary["labelled_predictions"] or 0
            ),
            "labelled_outcomes": labelled,
            "pending_outcomes": max(0, expected - labelled),
            "scheduled_outcomes": max(0, expected - matured),
            "overdue_outcomes": overdue,
            "coverage_pct": (
                round(labelled / matured * 100, 2)
                if matured
                else 0.0
            ),
            "latest_prediction_at": summary["latest_prediction_at"],
            "latest_evaluated_at": outcome_summary["latest_evaluated_at"],
            "action_metrics": [
                {
                    "action": row["action"],
                    "horizon_minutes": int(row["horizon_minutes"]),
                    "outcomes": int(row["outcomes"]),
                    "mean_return_bps": round(
                        float(row["mean_return_bps"]),
                        4,
                    ),
                    "positive_rate_pct": round(
                        float(row["positive_rate_pct"]),
                        2,
                    ),
                }
                for row in action_rows
            ],
        }

    def run_candidate_policy_calibration(
        self,
        *,
        evaluated_at: datetime,
    ) -> dict:
        """Persist one idempotent train/forward-validation calibration."""
        from ..core.candidates import (
            CandidatePolicy,
            candidate_policy_hash,
        )
        from ..core.continuous_learning import (
            calibrate_candidate_policy,
        )

        if (
            not isinstance(evaluated_at, datetime)
            or evaluated_at.tzinfo is None
            or evaluated_at.utcoffset() is None
        ):
            raise ValueError("evaluated_at must be timezone-aware")
        checked_at = evaluated_at.astimezone(timezone.utc)
        with self.Session.begin() as session:
            active_row = session.execute(text("""
                SELECT
                    id,
                    version,
                    config,
                    RTRIM(config_hash) AS config_hash,
                    created_at
                FROM candidate_policy_versions
                WHERE status = 'ACTIVE'
                FOR SHARE
            """)).mappings().one_or_none()
            if active_row is None:
                raise RuntimeError("No active candidate policy exists")
            active_policy = CandidatePolicy.from_mapping(
                version=active_row["version"],
                config=active_row["config"],
            )
            if (
                candidate_policy_hash(active_policy)
                != active_row["config_hash"]
            ):
                raise RuntimeError("Candidate policy config hash mismatch")

            cutoff_session = session.execute(text("""
                SELECT session_date, closes_at
                FROM market_sessions
                WHERE mic = 'XSTO'
                  AND status IN ('OPEN', 'HALF_DAY')
                  AND closes_at <= :evaluated_at
                ORDER BY session_date DESC
                LIMIT 1
            """), {
                "evaluated_at": checked_at,
            }).mappings().one_or_none()
            if cutoff_session is None:
                return {
                    "id": None,
                    "status": "WAITING",
                    "reason_code": "COMPLETED_SESSION_UNAVAILABLE",
                }
            cutoff_date = cutoff_session["session_date"]
            if (
                checked_at
                < cutoff_session["closes_at"] + timedelta(minutes=30)
            ):
                return {
                    "id": None,
                    "status": "WAITING",
                    "reason_code": "DELAYED_OUTCOMES_NOT_DUE",
                }
            calibration_key = (
                f"candidate:{active_policy.version}:{cutoff_date.isoformat()}"
            )
            existing = session.execute(text("""
                SELECT *
                FROM candidate_policy_calibration_runs
                WHERE calibration_key = :calibration_key
            """), {
                "calibration_key": calibration_key,
            }).mappings().one_or_none()
            if existing is not None:
                challenger = session.execute(text("""
                    SELECT version
                    FROM candidate_policy_versions
                    WHERE calibration_run_id = :calibration_run_id
                    ORDER BY id DESC
                    LIMIT 1
                """), {
                    "calibration_run_id": existing["id"],
                }).scalar()
                return {
                    **dict(existing),
                    "challenger_policy_version": challenger,
                }

            coverage_row = session.execute(text("""
                WITH expected AS (
                    SELECT
                        prediction.id,
                        DATE_TRUNC('minute', prediction.observed_at)
                            + INTERVAL '30 minutes' AS target_at
                    FROM candidate_predictions prediction
                    WHERE prediction.policy_version = :policy_version
                      AND prediction.created_at >= :policy_started_at
                      AND prediction.observed_at <= :evaluated_at
                      AND prediction.expected_horizons_minutes @> ARRAY[30]
                )
                SELECT
                    COUNT(*) FILTER (
                        WHERE expected.target_at <= :evaluated_at
                    )::INTEGER AS matured,
                    COUNT(outcome.id)::INTEGER AS labelled
                FROM expected
                LEFT JOIN candidate_prediction_outcomes outcome
                  ON outcome.prediction_id = expected.id
                 AND outcome.horizon_minutes = 30
                 AND outcome.evaluated_at <= :evaluated_at
            """), {
                "policy_version": active_policy.version,
                "policy_started_at": active_row["created_at"],
                "evaluated_at": checked_at,
            }).mappings().one()
            matured = int(coverage_row["matured"] or 0)
            labelled = int(coverage_row["labelled"] or 0)
            coverage_pct = (
                labelled / matured * 100
                if matured
                else 0.0
            )
            observations = session.execute(text("""
                SELECT
                    market_session.session_date,
                    prediction.signal_score,
                    outcome.return_bps
                FROM candidate_predictions prediction
                JOIN candidate_prediction_outcomes outcome
                  ON outcome.prediction_id = prediction.id
                 AND outcome.horizon_minutes = 30
                JOIN market_sessions market_session
                  ON market_session.mic = 'XSTO'
                 AND market_session.status IN ('OPEN', 'HALF_DAY')
                 AND prediction.observed_at >= market_session.opens_at
                 AND prediction.observed_at < market_session.closes_at
                 AND outcome.target_event_time < market_session.closes_at
                WHERE prediction.policy_version = :policy_version
                  AND prediction.created_at >= :policy_started_at
                  AND prediction.reason_code != 'SPREAD_TOO_WIDE'
                  AND prediction.observed_at <= :evaluated_at
                  AND outcome.evaluated_at <= :evaluated_at
                ORDER BY
                    market_session.session_date,
                    prediction.id
            """), {
                "policy_version": active_policy.version,
                "policy_started_at": active_row["created_at"],
                "evaluated_at": checked_at,
            }).mappings().all()
            result = calibrate_candidate_policy(
                observations,
                policy_version=active_policy.version,
                active_min_signal_score=(
                    active_policy.min_signal_score
                ),
                coverage_pct=coverage_pct,
                evaluated_at=checked_at,
            )
            calibration_id = session.execute(text("""
                INSERT INTO candidate_policy_calibration_runs (
                    calibration_key,
                    policy_version,
                    evaluated_at,
                    session_cutoff_date,
                    status,
                    reason_code,
                    train_sessions,
                    validation_sessions,
                    train_outcomes,
                    validation_outcomes,
                    coverage_pct,
                    active_min_signal_score,
                    challenger_min_signal_score,
                    baseline_validation_mean_bps,
                    challenger_validation_mean_bps,
                    validation_improvement_bps,
                    metrics
                )
                VALUES (
                    :calibration_key,
                    :policy_version,
                    :evaluated_at,
                    :session_cutoff_date,
                    :status,
                    :reason_code,
                    :train_sessions,
                    :validation_sessions,
                    :train_outcomes,
                    :validation_outcomes,
                    :coverage_pct,
                    :active_min_signal_score,
                    :challenger_min_signal_score,
                    :baseline_validation_mean_bps,
                    :challenger_validation_mean_bps,
                    :validation_improvement_bps,
                    CAST(:metrics AS JSONB)
                )
                RETURNING id
            """), {
                "calibration_key": calibration_key,
                "policy_version": result.policy_version,
                "evaluated_at": checked_at,
                "session_cutoff_date": cutoff_date,
                "status": result.status,
                "reason_code": result.reason_code,
                "train_sessions": result.train_sessions,
                "validation_sessions": result.validation_sessions,
                "train_outcomes": result.train_outcomes,
                "validation_outcomes": result.validation_outcomes,
                "coverage_pct": result.coverage_pct,
                "active_min_signal_score": (
                    result.active_min_signal_score
                ),
                "challenger_min_signal_score": (
                    result.challenger_min_signal_score
                ),
                "baseline_validation_mean_bps": (
                    result.baseline_validation_mean_bps
                ),
                "challenger_validation_mean_bps": (
                    result.challenger_validation_mean_bps
                ),
                "validation_improvement_bps": (
                    result.validation_improvement_bps
                ),
                "metrics": json.dumps(
                    result.metrics,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            }).scalar_one()
            challenger_version = None
            if result.status == "CHALLENGER":
                candidate_policy = CandidatePolicy(
                    version=f"xsto-challenger-{calibration_id}",
                    min_signal_score=(
                        result.challenger_min_signal_score
                    ),
                    max_spread_bps=active_policy.max_spread_bps,
                )
                candidate_hash = candidate_policy_hash(candidate_policy)
                existing_candidate = session.execute(text("""
                    SELECT version
                    FROM candidate_policy_versions
                    WHERE config_hash = :config_hash
                      AND status IN ('DRAFT', 'APPROVED', 'ACTIVE')
                    ORDER BY id DESC
                    LIMIT 1
                """), {"config_hash": candidate_hash}).scalar()
                if existing_candidate is None:
                    session.execute(text("""
                        INSERT INTO candidate_policy_versions (
                            version,
                            status,
                            config,
                            config_hash,
                            parent_version_id,
                            calibration_run_id,
                            proposed_by
                        )
                        VALUES (
                            :version,
                            'DRAFT',
                            CAST(:config AS JSONB),
                            :config_hash,
                            :parent_version_id,
                            :calibration_run_id,
                            'continuous-learning-worker'
                        )
                    """), {
                        "version": candidate_policy.version,
                        "config": json.dumps(
                            candidate_policy.to_config(),
                            ensure_ascii=False,
                            allow_nan=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        "config_hash": candidate_hash,
                        "parent_version_id": active_row["id"],
                        "calibration_run_id": calibration_id,
                    })
                    challenger_version = candidate_policy.version
                else:
                    challenger_version = str(existing_candidate)
            return {
                "id": int(calibration_id),
                "calibration_key": calibration_key,
                "policy_version": result.policy_version,
                "evaluated_at": checked_at,
                "session_cutoff_date": cutoff_date,
                "status": result.status,
                "reason_code": result.reason_code,
                "train_sessions": result.train_sessions,
                "validation_sessions": result.validation_sessions,
                "train_outcomes": result.train_outcomes,
                "validation_outcomes": result.validation_outcomes,
                "coverage_pct": result.coverage_pct,
                "active_min_signal_score": (
                    result.active_min_signal_score
                ),
                "challenger_min_signal_score": (
                    result.challenger_min_signal_score
                ),
                "baseline_validation_mean_bps": (
                    result.baseline_validation_mean_bps
                ),
                "challenger_validation_mean_bps": (
                    result.challenger_validation_mean_bps
                ),
                "validation_improvement_bps": (
                    result.validation_improvement_bps
                ),
                "metrics": result.metrics,
                "challenger_policy_version": challenger_version,
            }

    def record_continuous_learning_run(
        self,
        *,
        run_key: str,
        evaluated_at: datetime,
        status: str,
        outcomes_labelled: int,
        calibration_run_id: int | None,
        error_code: str | None,
    ) -> int:
        """Record one immutable worker result with idempotent slot identity."""
        run_key = self._required_sync_text(run_key, "run_key")
        if not re.fullmatch(r"[a-z0-9][a-z0-9:._-]{2,99}", run_key):
            raise ValueError("run_key has invalid format")
        if (
            not isinstance(evaluated_at, datetime)
            or evaluated_at.tzinfo is None
            or evaluated_at.utcoffset() is None
        ):
            raise ValueError("evaluated_at must be timezone-aware")
        if status not in {"SUCCEEDED", "FAILED"}:
            raise ValueError("learning run status is unsupported")
        if (
            isinstance(outcomes_labelled, bool)
            or not isinstance(outcomes_labelled, int)
            or outcomes_labelled < 0
        ):
            raise ValueError("outcomes_labelled must be non-negative")
        if calibration_run_id is not None:
            calibration_run_id = self._positive_integer(
                calibration_run_id,
                "calibration_run_id",
            )
        if status == "SUCCEEDED":
            if error_code is not None:
                raise ValueError("successful learning run cannot have an error")
        else:
            error_code = self._required_sync_text(
                error_code,
                "error_code",
            )
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,49}", error_code):
                raise ValueError("error_code has invalid format")

        with self.Session.begin() as session:
            run_id = session.execute(text("""
                INSERT INTO continuous_learning_runs (
                    run_key,
                    evaluated_at,
                    status,
                    outcomes_labelled,
                    calibration_run_id,
                    error_code
                )
                VALUES (
                    :run_key,
                    :evaluated_at,
                    :status,
                    :outcomes_labelled,
                    :calibration_run_id,
                    :error_code
                )
                ON CONFLICT (run_key) DO NOTHING
                RETURNING id
            """), {
                "run_key": run_key,
                "evaluated_at": evaluated_at.astimezone(timezone.utc),
                "status": status,
                "outcomes_labelled": outcomes_labelled,
                "calibration_run_id": calibration_run_id,
                "error_code": error_code,
            }).scalar()
            if run_id is None:
                run_id = session.execute(text("""
                    SELECT id
                    FROM continuous_learning_runs
                    WHERE run_key = :run_key
                """), {"run_key": run_key}).scalar_one()
        return int(run_id)

    def record_knowledge_graph_sync_run(
        self,
        *,
        run_key: str,
        synced_at: datetime,
        status: str,
        synced_counts: Mapping[str, int],
        total_nodes: int,
        total_relationships: int,
        error_code: str | None,
    ) -> int:
        """Record immutable, bounded evidence for one graph sync attempt."""
        run_key = self._required_sync_text(run_key, "run_key")
        if not re.fullmatch(
            r"knowledge:[0-9]{8}T[0-9]{12}Z",
            run_key,
        ):
            raise ValueError("knowledge graph run_key has invalid format")
        if (
            not isinstance(synced_at, datetime)
            or synced_at.tzinfo is None
            or synced_at.utcoffset() is None
        ):
            raise ValueError("synced_at must be timezone-aware")
        if status not in {"SUCCEEDED", "FAILED"}:
            raise ValueError("knowledge graph status is unsupported")
        if (
            not isinstance(synced_counts, Mapping)
            or len(synced_counts) > 50
        ):
            raise ValueError("synced_counts must be a bounded mapping")
        normalized_counts = {}
        for name, value in synced_counts.items():
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", name)
                or isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError("synced_counts contains an invalid value")
            normalized_counts[name] = value
        for label, value in (
            ("total_nodes", total_nodes),
            ("total_relationships", total_relationships),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{label} must be non-negative")
        if status == "SUCCEEDED":
            if error_code is not None:
                raise ValueError(
                    "successful knowledge graph sync cannot have an error"
                )
        else:
            error_code = self._required_sync_text(
                error_code,
                "error_code",
            )
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,49}", error_code):
                raise ValueError("error_code has invalid format")

        parameters = {
            "run_key": run_key,
            "synced_at": synced_at.astimezone(timezone.utc),
            "status": status,
            "synced_counts": json.dumps(
                normalized_counts,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "total_nodes": total_nodes,
            "total_relationships": total_relationships,
            "error_code": error_code,
        }
        with self.Session.begin() as session:
            run_id = session.execute(text("""
                INSERT INTO knowledge_graph_sync_runs (
                    run_key,
                    synced_at,
                    status,
                    synced_counts,
                    total_nodes,
                    total_relationships,
                    error_code
                )
                VALUES (
                    :run_key,
                    :synced_at,
                    :status,
                    CAST(:synced_counts AS JSONB),
                    :total_nodes,
                    :total_relationships,
                    :error_code
                )
                ON CONFLICT (run_key) DO NOTHING
                RETURNING id
            """), parameters).scalar()
            if run_id is None:
                run_id = session.execute(text("""
                    SELECT id
                    FROM knowledge_graph_sync_runs
                    WHERE run_key = :run_key
                """), {"run_key": run_key}).scalar_one()
        return int(run_id)

    def get_knowledge_graph_runtime_status(
        self,
        *,
        now: datetime,
    ) -> dict | None:
        """Return the latest bounded graph sync evidence."""
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("now must be timezone-aware")
        checked_at = now.astimezone(timezone.utc)
        with self.Session() as session:
            row = session.execute(text("""
                SELECT
                    status,
                    synced_at,
                    synced_counts,
                    total_nodes,
                    total_relationships,
                    error_code,
                    GREATEST(
                        EXTRACT(EPOCH FROM :now - synced_at),
                        0
                    )::INTEGER AS age_seconds
                FROM knowledge_graph_sync_runs
                WHERE synced_at <= :now
                ORDER BY synced_at DESC, id DESC
                LIMIT 1
            """), {"now": checked_at}).mappings().one_or_none()
        return dict(row) if row else None

    def get_pending_knowledge_shadow_inputs(
        self,
        *,
        now: datetime,
        limit: int = 2,
    ) -> list[dict]:
        """Load bounded decision evidence not yet evaluated in shadow mode."""
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("now must be timezone-aware")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 10
        ):
            raise ValueError("limit must be between 1 and 10")
        checked_at = now.astimezone(timezone.utc)
        with self.Session() as session:
            rows = session.execute(text("""
                SELECT
                    decision.id AS source_decision_id,
                    decision.timestamp AS decision_at,
                    decision.market_data_json AS market_context,
                    decision.strategy_version,
                    decision.model_backend,
                    decision.model_name,
                    decision.model_provider,
                    decision.reasoning_effort,
                    COALESCE(
                        ARRAY_AGG(
                            DISTINCT prediction.ticker
                            ORDER BY prediction.ticker
                        ) FILTER (
                            WHERE prediction.ticker IS NOT NULL
                        ),
                        ARRAY[]::VARCHAR[]
                    ) AS tickers
                FROM ai_decisions decision
                LEFT JOIN candidate_predictions prediction
                  ON prediction.ai_decision_id = decision.id
                WHERE decision.timestamp <= :now
                  AND decision.market_data_json IS NOT NULL
                  AND LENGTH(decision.market_data_json) BETWEEN 1 AND 10000
                  AND decision.strategy_version IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM knowledge_shadow_runs shadow
                      WHERE shadow.source_decision_id = decision.id
                  )
                GROUP BY
                    decision.id,
                    decision.timestamp,
                    decision.market_data_json,
                    decision.strategy_version,
                    decision.model_backend,
                    decision.model_name,
                    decision.model_provider,
                    decision.reasoning_effort
                ORDER BY decision.timestamp DESC, decision.id DESC
                LIMIT :limit
            """), {
                "now": checked_at,
                "limit": limit,
            }).mappings().all()
        return [
            {
                **dict(row),
                "tickers": list(row.get("tickers") or []),
            }
            for row in rows
        ]

    def record_knowledge_shadow_run(
        self,
        *,
        source_decision_id: int,
        evaluated_at: datetime,
        status: str,
        reason_code: str,
        call_order: str,
        context_checksum_sha256: str,
        evidence_provenance: str,
        evidence_as_of: datetime,
        evidence_checksum_sha256: str,
        evidence_fact_count: int,
        candidate_count: int,
        comparison_count: int,
        changed_count: int,
        control_prompt_tokens: int,
        control_response_tokens: int,
        memory_prompt_tokens: int,
        memory_response_tokens: int,
        model_backend: str,
        model_name: str,
        model_provider: str | None,
        reasoning_effort: str | None,
        comparisons: list[dict],
    ) -> int:
        """Persist one append-only shadow result without prompts or reasoning."""
        source_decision_id = self._positive_integer(
            source_decision_id,
            "source_decision_id",
        )
        for field, value in (
            ("evaluated_at", evaluated_at),
            ("evidence_as_of", evidence_as_of),
        ):
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise ValueError(f"{field} must be timezone-aware")
        checked_at = evaluated_at.astimezone(timezone.utc)
        checked_evidence_as_of = evidence_as_of.astimezone(timezone.utc)
        if checked_evidence_as_of > checked_at:
            raise ValueError("evidence_as_of cannot be in the future")

        if status not in {"SUCCEEDED", "SKIPPED", "FAILED"}:
            raise ValueError("knowledge shadow status is unsupported")
        reason_code = self._required_sync_text(
            reason_code,
            "reason_code",
        )
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,49}", reason_code):
            raise ValueError("reason_code has invalid format")
        if call_order not in {
            "NOT_RUN",
            "CONTROL_FIRST",
            "MEMORY_FIRST",
        }:
            raise ValueError("call_order is unsupported")
        for field, value in (
            ("context_checksum_sha256", context_checksum_sha256),
            ("evidence_checksum_sha256", evidence_checksum_sha256),
        ):
            if not isinstance(value, str) or not re.fullmatch(
                r"[0-9a-f]{64}",
                value,
            ):
                raise ValueError(f"{field} must be lowercase SHA-256")
        if evidence_provenance != (
            "neo4j:candidate-outcome-aggregate-v1"
        ):
            raise ValueError("evidence_provenance is unsupported")

        count_limits = {
            "evidence_fact_count": 60,
            "candidate_count": 50,
            "comparison_count": 50,
            "changed_count": 50,
            "control_prompt_tokens": 10_000_000,
            "control_response_tokens": 10_000_000,
            "memory_prompt_tokens": 10_000_000,
            "memory_response_tokens": 10_000_000,
        }
        counts = {}
        for field, maximum in count_limits.items():
            value = locals()[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= maximum
            ):
                raise ValueError(f"{field} is out of range")
            counts[field] = value
        if changed_count > comparison_count:
            raise ValueError("changed_count cannot exceed comparison_count")

        model_backend = self._required_sync_text(
            model_backend,
            "model_backend",
        )
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{1,29}",
            model_backend,
        ):
            raise ValueError("model_backend has invalid format")
        model_name = self._bounded_identity_text(
            model_name,
            "model_name",
            200,
        )
        if model_provider is not None:
            model_provider = self._bounded_identity_text(
                model_provider,
                "model_provider",
                80,
            )
        if reasoning_effort not in {
            None,
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise ValueError("reasoning_effort is unsupported")

        if not isinstance(comparisons, list) or len(comparisons) > 50:
            raise ValueError("comparisons must be a bounded list")
        normalized_comparisons = []
        for comparison in comparisons:
            if not isinstance(comparison, dict):
                raise ValueError("comparison must be an object")
            ticker = str(comparison.get("ticker") or "").strip().upper()
            control_action = str(
                comparison.get("control_action") or ""
            ).upper()
            memory_action = str(
                comparison.get("memory_action") or ""
            ).upper()
            if (
                not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,19}", ticker)
                or control_action
                not in {"BUY", "SELL", "HOLD", "ABSTAIN"}
                or memory_action
                not in {"BUY", "SELL", "HOLD", "ABSTAIN"}
            ):
                raise ValueError("comparison identity is invalid")

            confidences = {}
            for field in ("control_confidence", "memory_confidence"):
                value = comparison.get(field)
                if value is None:
                    confidences[field] = None
                    continue
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float, Decimal))
                    or not math.isfinite(float(value))
                    or not 0 <= float(value) <= 100
                ):
                    raise ValueError(f"{field} is invalid")
                confidences[field] = float(value)
            if (
                control_action == "ABSTAIN"
                and confidences["control_confidence"] is not None
            ) or (
                memory_action == "ABSTAIN"
                and confidences["memory_confidence"] is not None
            ):
                raise ValueError("abstention confidence must be null")
            changed = comparison.get("changed")
            expected_changed = control_action != memory_action
            if not isinstance(changed, bool) or changed != expected_changed:
                raise ValueError("comparison changed flag is invalid")
            normalized_comparisons.append({
                "ticker": ticker,
                "control_action": control_action,
                "memory_action": memory_action,
                **confidences,
                "changed": changed,
            })
        if comparison_count != len(normalized_comparisons):
            raise ValueError("comparison_count does not match comparisons")
        if changed_count != sum(
            1 for comparison in normalized_comparisons
            if comparison["changed"]
        ):
            raise ValueError("changed_count does not match comparisons")

        allowed_skips = {
            "MODEL_CONFIG_MISMATCH",
            "NO_CANDIDATES",
            "NO_ELIGIBLE_EVIDENCE",
            "STRATEGY_UNAVAILABLE",
        }
        allowed_failures = {
            "GRAPH_UNAVAILABLE",
            "INVALID_MODEL_RESPONSE",
            "MODEL_UNAVAILABLE",
        }
        if status == "SUCCEEDED":
            valid_state = (
                reason_code == "COMPARED"
                and call_order != "NOT_RUN"
                and evidence_fact_count > 0
                and comparison_count > 0
            )
        elif status == "SKIPPED":
            valid_state = (
                reason_code in allowed_skips
                and call_order == "NOT_RUN"
                and comparison_count == 0
                and changed_count == 0
                and all(
                    counts[field] == 0
                    for field in (
                        "control_prompt_tokens",
                        "control_response_tokens",
                        "memory_prompt_tokens",
                        "memory_response_tokens",
                    )
                )
            )
        else:
            valid_state = (
                reason_code in allowed_failures
                and comparison_count == 0
                and changed_count == 0
            )
        if not valid_state:
            raise ValueError("knowledge shadow state is inconsistent")

        run_key = f"knowledge-shadow:{source_decision_id}"
        values = {
            "run_key": run_key,
            "source_decision_id": source_decision_id,
            "evaluated_at": checked_at,
            "status": status,
            "reason_code": reason_code,
            "call_order": call_order,
            "context_checksum_sha256": context_checksum_sha256,
            "evidence_provenance": evidence_provenance,
            "evidence_as_of": checked_evidence_as_of,
            "evidence_checksum_sha256": evidence_checksum_sha256,
            **counts,
            "model_backend": model_backend,
            "model_name": model_name,
            "model_provider": model_provider,
            "reasoning_effort": reasoning_effort,
        }
        with self.Session.begin() as session:
            run_id = session.execute(text("""
                INSERT INTO knowledge_shadow_runs (
                    run_key,
                    source_decision_id,
                    evaluated_at,
                    status,
                    reason_code,
                    call_order,
                    context_checksum_sha256,
                    evidence_provenance,
                    evidence_as_of,
                    evidence_checksum_sha256,
                    evidence_fact_count,
                    candidate_count,
                    comparison_count,
                    changed_count,
                    control_prompt_tokens,
                    control_response_tokens,
                    memory_prompt_tokens,
                    memory_response_tokens,
                    model_backend,
                    model_name,
                    model_provider,
                    reasoning_effort
                )
                VALUES (
                    :run_key,
                    :source_decision_id,
                    :evaluated_at,
                    :status,
                    :reason_code,
                    :call_order,
                    :context_checksum_sha256,
                    :evidence_provenance,
                    :evidence_as_of,
                    :evidence_checksum_sha256,
                    :evidence_fact_count,
                    :candidate_count,
                    :comparison_count,
                    :changed_count,
                    :control_prompt_tokens,
                    :control_response_tokens,
                    :memory_prompt_tokens,
                    :memory_response_tokens,
                    :model_backend,
                    :model_name,
                    :model_provider,
                    :reasoning_effort
                )
                ON CONFLICT (source_decision_id) DO NOTHING
                RETURNING id
            """), values).scalar()
            if run_id is None:
                run_id = session.execute(text("""
                    SELECT id
                    FROM knowledge_shadow_runs
                    WHERE source_decision_id = :source_decision_id
                """), {
                    "source_decision_id": source_decision_id,
                }).scalar_one()
                return int(run_id)
            for comparison in normalized_comparisons:
                session.execute(text("""
                    INSERT INTO knowledge_shadow_decisions (
                        shadow_run_id,
                        ticker,
                        control_action,
                        memory_action,
                        control_confidence,
                        memory_confidence,
                        changed
                    )
                    VALUES (
                        :shadow_run_id,
                        :ticker,
                        :control_action,
                        :memory_action,
                        :control_confidence,
                        :memory_confidence,
                        :changed
                    )
                """), {
                    "shadow_run_id": run_id,
                    **comparison,
                })
        return int(run_id)

    def get_knowledge_shadow_runtime_status(
        self,
        *,
        now: datetime,
    ) -> dict | None:
        """Return the latest bounded graph-memory shadow evidence."""
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("now must be timezone-aware")
        checked_at = now.astimezone(timezone.utc)
        with self.Session() as session:
            row = session.execute(text("""
                SELECT
                    status,
                    reason_code,
                    evaluated_at,
                    evidence_fact_count,
                    candidate_count,
                    comparison_count,
                    changed_count,
                    model_backend,
                    model_name,
                    model_provider,
                    reasoning_effort,
                    GREATEST(
                        EXTRACT(EPOCH FROM :now - evaluated_at),
                        0
                    )::INTEGER AS age_seconds
                FROM knowledge_shadow_runs
                WHERE evaluated_at <= :now
                ORDER BY evaluated_at DESC, id DESC
                LIMIT 1
            """), {"now": checked_at}).mappings().one_or_none()
        return dict(row) if row else None

    def get_pending_object_archive_inputs(
        self,
        *,
        limit: int,
    ) -> list[dict]:
        """Return bounded validated blobs not yet mirrored to object storage."""
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
        ):
            raise ValueError("limit must be between 1 and 100")
        with self.Session() as session:
            rows = session.execute(text("""
                WITH pending AS (
                    SELECT
                        'market-data'::TEXT AS source_kind,
                        source.id,
                        source.provider,
                        source.data_type,
                        source.file_name,
                        source.report_minute,
                        source.received_at,
                        source.checksum_sha256,
                        source.content_encoding,
                        source.compressed_content,
                        source.uncompressed_bytes
                    FROM market_data_files source
                    LEFT JOIN market_data_object_archives archived
                        ON archived.market_data_file_id = source.id
                    WHERE archived.id IS NULL

                    UNION ALL

                    SELECT
                        'reference-data'::TEXT AS source_kind,
                        source.id,
                        snapshot.provider,
                        'reference-universe'::TEXT AS data_type,
                        source.file_name,
                        snapshot.received_at AS report_minute,
                        snapshot.received_at,
                        source.checksum_sha256,
                        source.content_encoding,
                        source.compressed_content,
                        source.uncompressed_bytes
                    FROM reference_data_files source
                    JOIN reference_data_snapshots snapshot
                        ON snapshot.id = source.snapshot_id
                    LEFT JOIN reference_data_object_archives archived
                        ON archived.reference_data_file_id = source.id
                    WHERE archived.id IS NULL
                )
                SELECT *
                FROM pending
                ORDER BY source_kind, id
                LIMIT :limit
            """), {"limit": limit}).mappings().all()
        results = []
        for row in rows:
            value = dict(row)
            value["compressed_content"] = bytes(value["compressed_content"])
            value["checksum_sha256"] = value["checksum_sha256"].strip()
            results.append(value)
        return results

    def record_object_archive_run(
        self,
        *,
        run_key: str,
        evaluated_at: datetime,
        status: str,
        reason_code: str,
        selected_count: int,
        archived_count: int,
        uploaded_count: int,
        existing_count: int,
        failed_count: int,
        compressed_bytes: int,
        objects: Sequence[Mapping],
    ) -> int:
        """Append one bounded archive cycle and its verified object evidence."""
        objects = tuple(dict(value) for value in objects)
        values = {
            "run_key": run_key,
            "evaluated_at": evaluated_at,
            "status": status,
            "reason_code": reason_code,
            "selected_count": selected_count,
            "archived_count": archived_count,
            "uploaded_count": uploaded_count,
            "existing_count": existing_count,
            "failed_count": failed_count,
            "compressed_bytes": compressed_bytes,
        }
        with self.Session.begin() as session:
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:run_key, 0))"
                ),
                {"run_key": run_key},
            )
            existing = session.execute(text("""
                SELECT
                    id,
                    evaluated_at,
                    status,
                    reason_code,
                    selected_count,
                    archived_count,
                    uploaded_count,
                    existing_count,
                    failed_count,
                    compressed_bytes
                FROM object_archive_runs
                WHERE run_key = :run_key
            """), {"run_key": run_key}).mappings().one_or_none()
            if existing is not None:
                comparable = {
                    key: existing[key]
                    for key in values
                    if key != "run_key"
                }
                expected = {
                    key: value
                    for key, value in values.items()
                    if key != "run_key"
                }
                if comparable != expected:
                    raise ValueError(
                        "object archive run key has conflicting evidence"
                    )
                existing_objects = session.execute(text("""
                    SELECT
                        'market-data'::TEXT AS source_kind,
                        market_data_file_id AS source_id,
                        bucket,
                        object_key,
                        compressed_checksum_sha256,
                        compressed_bytes,
                        uploaded
                    FROM market_data_object_archives
                    WHERE run_id = :run_id

                    UNION ALL

                    SELECT
                        'reference-data'::TEXT AS source_kind,
                        reference_data_file_id AS source_id,
                        bucket,
                        object_key,
                        compressed_checksum_sha256,
                        compressed_bytes,
                        uploaded
                    FROM reference_data_object_archives
                    WHERE run_id = :run_id
                    ORDER BY source_kind, source_id
                """), {"run_id": existing["id"]}).mappings().all()
                canonical_existing = [
                    {
                        **dict(row),
                        "compressed_checksum_sha256": row[
                            "compressed_checksum_sha256"
                        ].strip(),
                    }
                    for row in existing_objects
                ]
                canonical_objects = sorted(
                    objects,
                    key=lambda item: (
                        item["source_kind"],
                        item["source_id"],
                    ),
                )
                if canonical_existing != canonical_objects:
                    raise ValueError(
                        "object archive run key has conflicting objects"
                    )
                return int(existing["id"])

            run_id = int(session.execute(text("""
                INSERT INTO object_archive_runs (
                    run_key,
                    evaluated_at,
                    status,
                    reason_code,
                    selected_count,
                    archived_count,
                    uploaded_count,
                    existing_count,
                    failed_count,
                    compressed_bytes
                )
                VALUES (
                    :run_key,
                    :evaluated_at,
                    :status,
                    :reason_code,
                    :selected_count,
                    :archived_count,
                    :uploaded_count,
                    :existing_count,
                    :failed_count,
                    :compressed_bytes
                )
                RETURNING id
            """), values).scalar_one())
            for archived in objects:
                source_kind = archived.get("source_kind")
                if source_kind == "market-data":
                    table = "market_data_object_archives"
                    source_column = "market_data_file_id"
                elif source_kind == "reference-data":
                    table = "reference_data_object_archives"
                    source_column = "reference_data_file_id"
                else:
                    raise ValueError("object archive source kind is invalid")
                session.execute(text(f"""
                    INSERT INTO {table} (
                        {source_column},
                        run_id,
                        bucket,
                        object_key,
                        compressed_checksum_sha256,
                        compressed_bytes,
                        uploaded,
                        archived_at
                    )
                    VALUES (
                        :source_id,
                        :run_id,
                        :bucket,
                        :object_key,
                        :compressed_checksum_sha256,
                        :compressed_bytes,
                        :uploaded,
                        :archived_at
                    )
                """), {
                    **archived,
                    "run_id": run_id,
                    "archived_at": evaluated_at,
                })
        return run_id

    def get_object_archive_runtime_status(
        self,
        *,
        now: datetime,
    ) -> dict | None:
        """Return latest archive-cycle evidence and bounded totals."""
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("now must be timezone-aware")
        checked_at = now.astimezone(timezone.utc)
        with self.Session() as session:
            row = session.execute(text("""
                WITH totals AS (
                    SELECT
                        (
                            (SELECT COUNT(*) FROM
                                market_data_object_archives)
                            + (SELECT COUNT(*) FROM
                                reference_data_object_archives)
                        )::INTEGER AS total_object_count,
                        (
                            COALESCE((SELECT SUM(compressed_bytes) FROM
                                market_data_object_archives), 0)
                            + COALESCE((SELECT SUM(compressed_bytes) FROM
                                reference_data_object_archives), 0)
                        )::BIGINT AS total_compressed_bytes
                ),
                pending AS (
                    SELECT (
                        (
                            SELECT COUNT(*)
                            FROM market_data_files source
                            LEFT JOIN market_data_object_archives archived
                                ON archived.market_data_file_id = source.id
                            WHERE archived.id IS NULL
                        )
                        + (
                            SELECT COUNT(*)
                            FROM reference_data_files source
                            LEFT JOIN reference_data_object_archives archived
                                ON archived.reference_data_file_id = source.id
                            WHERE archived.id IS NULL
                        )
                    )::INTEGER AS pending_count
                )
                SELECT
                    run.status,
                    run.reason_code,
                    run.evaluated_at,
                    run.selected_count,
                    run.archived_count,
                    run.uploaded_count,
                    run.existing_count,
                    run.failed_count,
                    run.compressed_bytes,
                    totals.total_object_count,
                    totals.total_compressed_bytes,
                    pending.pending_count,
                    GREATEST(
                        EXTRACT(EPOCH FROM :now - run.evaluated_at),
                        0
                    )::INTEGER AS age_seconds
                FROM object_archive_runs run
                CROSS JOIN totals
                CROSS JOIN pending
                WHERE run.evaluated_at <= :now
                ORDER BY run.evaluated_at DESC, run.id DESC
                LIMIT 1
            """), {"now": checked_at}).mappings().one_or_none()
        return dict(row) if row else None

    def get_continuous_learning_runtime_status(
        self,
        *,
        now: datetime,
    ) -> dict:
        """Return bounded worker, calibration and policy runtime evidence."""
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("now must be timezone-aware")
        checked_at = now.astimezone(timezone.utc)
        with self.Session() as session:
            run = session.execute(text("""
                SELECT
                    id,
                    run_key,
                    evaluated_at,
                    status,
                    outcomes_labelled,
                    calibration_run_id,
                    error_code
                FROM continuous_learning_runs
                WHERE evaluated_at <= :now
                ORDER BY evaluated_at DESC, id DESC
                LIMIT 1
            """), {"now": checked_at}).mappings().one_or_none()
            calibration = session.execute(text("""
                SELECT
                    id,
                    calibration_key,
                    policy_version,
                    evaluated_at,
                    session_cutoff_date,
                    status,
                    reason_code,
                    train_sessions,
                    validation_sessions,
                    train_outcomes,
                    validation_outcomes,
                    coverage_pct,
                    active_min_signal_score,
                    challenger_min_signal_score,
                    baseline_validation_mean_bps,
                    challenger_validation_mean_bps,
                    validation_improvement_bps
                FROM candidate_policy_calibration_runs
                WHERE evaluated_at <= :now
                ORDER BY evaluated_at DESC, id DESC
                LIMIT 1
            """), {"now": checked_at}).mappings().one_or_none()
            policies = session.execute(text("""
                SELECT
                    version,
                    status,
                    config,
                    calibration_run_id,
                    created_at
                FROM candidate_policy_versions
                WHERE status IN ('ACTIVE', 'DRAFT', 'APPROVED')
                ORDER BY
                    CASE status
                        WHEN 'ACTIVE' THEN 0
                        WHEN 'APPROVED' THEN 1
                        ELSE 2
                    END,
                    created_at DESC,
                    id DESC
            """)).mappings().all()
        run_dict = dict(run) if run is not None else None
        if run_dict is not None:
            run_dict["age_seconds"] = max(
                0,
                int((checked_at - run_dict["evaluated_at"]).total_seconds()),
            )
        return {
            "run": run_dict,
            "calibration": (
                dict(calibration)
                if calibration is not None
                else None
            ),
            "policies": [dict(policy) for policy in policies],
        }

    def approve_candidate_policy_version(
        self,
        version: str,
        *,
        reviewed_by: str,
    ) -> None:
        """Approve a forward-gated challenger without activating it."""
        if (
            not isinstance(version, str)
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{2,49}",
                version,
            )
        ):
            raise ValueError("candidate policy version has invalid format")
        reviewer = self._operator_identity(reviewed_by)
        with self.Session.begin() as session:
            updated = session.execute(text("""
                UPDATE candidate_policy_versions policy
                SET
                    status = 'APPROVED',
                    reviewed_by = :reviewed_by,
                    reviewed_at = NOW(),
                    updated_at = NOW()
                FROM candidate_policy_calibration_runs calibration,
                     candidate_policy_versions parent
                WHERE policy.version = :version
                  AND policy.status = 'DRAFT'
                  AND calibration.id = policy.calibration_run_id
                  AND calibration.status = 'CHALLENGER'
                  AND parent.id = policy.parent_version_id
                  AND parent.status = 'ACTIVE'
                RETURNING policy.id
            """), {
                "version": version,
                "reviewed_by": reviewer,
            }).scalar()
            if updated is None:
                raise ValueError(
                    "forward-gated candidate policy draft is unavailable"
                )

    def activate_candidate_policy_version(
        self,
        version: str,
        *,
        activated_by: str,
    ) -> None:
        """Atomically activate an approved child of the active policy."""
        if (
            not isinstance(version, str)
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{2,49}",
                version,
            )
        ):
            raise ValueError("candidate policy version has invalid format")
        operator = self._operator_identity(activated_by)
        with self.Session.begin() as session:
            session.execute(text("""
                SELECT pg_advisory_xact_lock(
                    hashtextextended('candidate-policy-activation', 0)
                )
            """))
            current = session.execute(text("""
                SELECT id
                FROM candidate_policy_versions
                WHERE status = 'ACTIVE'
                FOR UPDATE
            """)).scalar_one_or_none()
            candidate = session.execute(text("""
                SELECT id, status, parent_version_id
                FROM candidate_policy_versions
                WHERE version = :version
                FOR UPDATE
            """), {"version": version}).mappings().one_or_none()
            if current is None:
                raise RuntimeError("No active candidate policy exists")
            if (
                candidate is None
                or candidate["status"] != "APPROVED"
                or candidate["parent_version_id"] != current
            ):
                raise ValueError(
                    "approved child candidate policy is unavailable"
                )
            session.execute(text("""
                UPDATE candidate_policy_versions
                SET
                    status = 'RETIRED',
                    retired_at = NOW(),
                    updated_at = NOW()
                WHERE id = :current_id
            """), {"current_id": current})
            session.execute(text("""
                UPDATE candidate_policy_versions
                SET
                    status = 'ACTIVE',
                    activated_by = :activated_by,
                    activated_at = NOW(),
                    updated_at = NOW()
                WHERE id = :candidate_id
            """), {
                "activated_by": operator,
                "candidate_id": candidate["id"],
            })

    def activate_candidate_policy_automatically(
        self,
        version: str,
        *,
        activated_at: datetime,
    ) -> bool:
        """Atomically review and activate one forward-gated challenger."""
        if (
            not isinstance(version, str)
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{2,49}",
                version,
            )
        ):
            raise ValueError("candidate policy version has invalid format")
        if (
            not isinstance(activated_at, datetime)
            or activated_at.tzinfo is None
            or activated_at.utcoffset() is None
        ):
            raise ValueError("activated_at must be timezone-aware")
        transition_at = activated_at.astimezone(timezone.utc)
        actor = "continuous-learning-worker"
        with self.Session.begin() as session:
            session.execute(text("""
                SELECT pg_advisory_xact_lock(
                    hashtextextended('candidate-policy-activation', 0)
                )
            """))
            candidate = session.execute(text("""
                SELECT
                    policy.id,
                    policy.status,
                    policy.parent_version_id,
                    calibration.status AS calibration_status,
                    parent.status AS parent_status
                FROM candidate_policy_versions policy
                JOIN candidate_policy_calibration_runs calibration
                  ON calibration.id = policy.calibration_run_id
                JOIN candidate_policy_versions parent
                  ON parent.id = policy.parent_version_id
                WHERE policy.version = :version
                FOR UPDATE OF policy, parent
            """), {"version": version}).mappings().one_or_none()
            if candidate is None:
                raise ValueError(
                    "forward-gated candidate policy is unavailable"
                )
            if candidate["status"] == "ACTIVE":
                return False
            if (
                candidate["status"] not in ("DRAFT", "APPROVED")
                or candidate["calibration_status"] != "CHALLENGER"
                or candidate["parent_status"] != "ACTIVE"
            ):
                raise ValueError(
                    "forward-gated candidate policy is unavailable"
                )
            session.execute(text("""
                UPDATE candidate_policy_versions
                SET
                    status = 'RETIRED',
                    retired_at = :transition_at,
                    updated_at = :transition_at
                WHERE id = :parent_id
            """), {
                "parent_id": candidate["parent_version_id"],
                "transition_at": transition_at,
            })
            session.execute(text("""
                UPDATE candidate_policy_versions
                SET
                    status = 'ACTIVE',
                    reviewed_by = COALESCE(reviewed_by, :actor),
                    reviewed_at = COALESCE(reviewed_at, :transition_at),
                    activated_by = :actor,
                    activated_at = :transition_at,
                    updated_at = :transition_at
                WHERE id = :candidate_id
            """), {
                "actor": actor,
                "candidate_id": candidate["id"],
                "transition_at": transition_at,
            })
        return True

    def rollback_candidate_policy_automatically(
        self,
        *,
        evaluated_at: datetime,
    ) -> dict:
        """Restore the parent config after bounded forward regression."""
        from ..core.candidates import (
            CandidatePolicy,
            candidate_policy_hash,
        )
        from ..core.continuous_learning import (
            evaluate_candidate_policy_rollback,
        )

        if (
            not isinstance(evaluated_at, datetime)
            or evaluated_at.tzinfo is None
            or evaluated_at.utcoffset() is None
        ):
            raise ValueError("evaluated_at must be timezone-aware")
        checked_at = evaluated_at.astimezone(timezone.utc)
        actor = "continuous-learning-worker"
        with self.Session.begin() as session:
            session.execute(text("""
                SELECT pg_advisory_xact_lock(
                    hashtextextended('candidate-policy-activation', 0)
                )
            """))
            active = session.execute(text("""
                SELECT
                    id,
                    version,
                    config,
                    RTRIM(config_hash) AS config_hash,
                    parent_version_id,
                    activated_by,
                    activated_at
                FROM candidate_policy_versions
                WHERE status = 'ACTIVE'
                FOR UPDATE
            """)).mappings().one_or_none()
            if active is None:
                raise RuntimeError("No active candidate policy exists")
            if (
                active["parent_version_id"] is None
                or active["activated_by"] != actor
                or active["version"].startswith("xsto-rollback-")
            ):
                return {
                    "status": "WAITING",
                    "reason_code": "AUTOMATED_CHILD_POLICY_UNAVAILABLE",
                    "policy_version": active["version"],
                }
            parent = session.execute(text("""
                SELECT
                    id,
                    version,
                    config,
                    RTRIM(config_hash) AS config_hash
                FROM candidate_policy_versions
                WHERE id = :parent_id
                FOR SHARE
            """), {
                "parent_id": active["parent_version_id"],
            }).mappings().one_or_none()
            if parent is None:
                raise RuntimeError("Active policy parent is unavailable")
            active_policy = CandidatePolicy.from_mapping(
                version=active["version"],
                config=active["config"],
            )
            parent_policy = CandidatePolicy.from_mapping(
                version=parent["version"],
                config=parent["config"],
            )
            if (
                candidate_policy_hash(active_policy)
                != active["config_hash"]
                or candidate_policy_hash(parent_policy)
                != parent["config_hash"]
            ):
                raise RuntimeError("Candidate policy config hash mismatch")

            coverage = session.execute(text("""
                WITH expected AS (
                    SELECT
                        prediction.id,
                        DATE_TRUNC('minute', prediction.observed_at)
                            + INTERVAL '30 minutes' AS target_at
                    FROM candidate_predictions prediction
                    WHERE prediction.policy_version = :policy_version
                      AND prediction.created_at >= :activated_at
                      AND prediction.observed_at <= :evaluated_at
                      AND prediction.expected_horizons_minutes @> ARRAY[30]
                )
                SELECT
                    COUNT(*) FILTER (
                        WHERE expected.target_at <= :evaluated_at
                    )::INTEGER AS matured,
                    COUNT(outcome.id)::INTEGER AS labelled
                FROM expected
                LEFT JOIN candidate_prediction_outcomes outcome
                  ON outcome.prediction_id = expected.id
                 AND outcome.horizon_minutes = 30
                 AND outcome.evaluated_at <= :evaluated_at
            """), {
                "activated_at": active["activated_at"],
                "evaluated_at": checked_at,
                "policy_version": active["version"],
            }).mappings().one()
            metrics = session.execute(text("""
                SELECT
                    COUNT(DISTINCT market_session.session_date)::INTEGER
                        AS completed_sessions,
                    COUNT(*) FILTER (
                        WHERE prediction.signal_score >= :active_threshold
                    )::INTEGER AS active_outcomes,
                    COUNT(*) FILTER (
                        WHERE prediction.signal_score >= :parent_threshold
                    )::INTEGER AS parent_outcomes,
                    AVG(outcome.return_bps) FILTER (
                        WHERE prediction.signal_score >= :active_threshold
                    ) AS active_mean_bps,
                    AVG(outcome.return_bps) FILTER (
                        WHERE prediction.signal_score >= :parent_threshold
                    ) AS parent_mean_bps,
                    100.0 * AVG(
                        CASE
                            WHEN outcome.return_bps > 0 THEN 1.0
                            ELSE 0.0
                        END
                    ) FILTER (
                        WHERE prediction.signal_score >= :parent_threshold
                    ) AS parent_positive_rate_pct
                FROM candidate_predictions prediction
                JOIN candidate_prediction_outcomes outcome
                  ON outcome.prediction_id = prediction.id
                 AND outcome.horizon_minutes = 30
                JOIN market_sessions market_session
                  ON market_session.mic = 'XSTO'
                 AND market_session.status IN ('OPEN', 'HALF_DAY')
                 AND prediction.observed_at >= market_session.opens_at
                 AND prediction.observed_at < market_session.closes_at
                 AND outcome.target_event_time < market_session.closes_at
                WHERE prediction.policy_version = :policy_version
                  AND prediction.created_at >= :activated_at
                  AND prediction.reason_code != 'SPREAD_TOO_WIDE'
                  AND prediction.observed_at <= :evaluated_at
                  AND outcome.evaluated_at <= :evaluated_at
            """), {
                "activated_at": active["activated_at"],
                "active_threshold": active_policy.min_signal_score,
                "evaluated_at": checked_at,
                "parent_threshold": parent_policy.min_signal_score,
                "policy_version": active["version"],
            }).mappings().one()
            matured = int(coverage["matured"] or 0)
            labelled = int(coverage["labelled"] or 0)
            decision = evaluate_candidate_policy_rollback(
                completed_sessions=int(metrics["completed_sessions"] or 0),
                coverage_pct=(
                    labelled / matured * 100 if matured else 0.0
                ),
                active_outcomes=int(metrics["active_outcomes"] or 0),
                parent_outcomes=int(metrics["parent_outcomes"] or 0),
                active_mean_bps=metrics["active_mean_bps"],
                parent_mean_bps=metrics["parent_mean_bps"],
                parent_positive_rate_pct=(
                    metrics["parent_positive_rate_pct"]
                ),
            )
            result = {
                field.name: getattr(decision, field.name)
                for field in fields(decision)
            }
            if decision.status != "ROLLBACK":
                return {
                    **result,
                    "policy_version": active["version"],
                }

            rollback_version = f"xsto-rollback-{active['id']}"
            session.execute(text("""
                UPDATE candidate_policy_versions
                SET
                    status = 'RETIRED',
                    retired_at = :evaluated_at,
                    updated_at = :evaluated_at
                WHERE id = :active_id
            """), {
                "active_id": active["id"],
                "evaluated_at": checked_at,
            })
            session.execute(text("""
                INSERT INTO candidate_policy_versions (
                    version,
                    status,
                    config,
                    config_hash,
                    parent_version_id,
                    proposed_by,
                    reviewed_by,
                    reviewed_at,
                    activated_by,
                    activated_at
                )
                VALUES (
                    :version,
                    'ACTIVE',
                    CAST(:config AS JSONB),
                    :config_hash,
                    :parent_version_id,
                    :actor,
                    :actor,
                    :evaluated_at,
                    :actor,
                    :evaluated_at
                )
            """), {
                "actor": actor,
                "config": json.dumps(
                    parent_policy.to_config(),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "config_hash": parent["config_hash"],
                "evaluated_at": checked_at,
                "parent_version_id": active["id"],
                "version": rollback_version,
            })
            return {
                **result,
                "policy_version": rollback_version,
            }

    def reject_candidate_policy_version(
        self,
        version: str,
        *,
        reviewed_by: str,
        reason: str,
    ) -> None:
        """Reject one draft challenger without changing the active policy."""
        if (
            not isinstance(version, str)
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{2,49}",
                version,
            )
        ):
            raise ValueError("candidate policy version has invalid format")
        reviewer = self._operator_identity(reviewed_by)
        reason = self._bounded_identity_text(reason, "reason", 2000)
        with self.Session.begin() as session:
            updated = session.execute(text("""
                UPDATE candidate_policy_versions
                SET
                    status = 'REJECTED',
                    reviewed_by = :reviewed_by,
                    reviewed_at = NOW(),
                    rejection_reason = :reason,
                    updated_at = NOW()
                WHERE version = :version
                  AND status = 'DRAFT'
                RETURNING id
            """), {
                "version": version,
                "reviewed_by": reviewer,
                "reason": reason,
            }).scalar()
            if updated is None:
                raise ValueError(
                    "candidate policy draft is unavailable"
                )

    def log_trade(self, trade: dict) -> int:
        """Compatibility wrapper returning the stable trade id."""
        return self.log_trade_result(trade).trade_id

    def log_trade_result(self, trade: dict) -> TradeLogResult:
        """Apply a paper trade exactly once and allocate sells FIFO."""
        normalized = self._normalize_trade(trade)
        request_fingerprint = self._trade_request_fingerprint(normalized)

        with self.Session.begin() as session:
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:idempotency_key, 0))"
                ),
                {'idempotency_key': normalized['idempotency_key']},
            )
            existing_trade = session.execute(text("""
                SELECT id, request_fingerprint
                FROM trades
                WHERE idempotency_key = :idempotency_key
                FOR UPDATE
            """), {
                'idempotency_key': normalized['idempotency_key'],
            }).mappings().first()
            if existing_trade is not None:
                if (
                    existing_trade['request_fingerprint'].strip()
                    != request_fingerprint
                ):
                    raise ValueError(
                        "idempotency_key was already used for a "
                        "different trade payload"
                    )
                return TradeLogResult(
                    trade_id=int(existing_trade['id']),
                    inserted=False,
                )

            if normalized['action'] == 'BUY':
                risk_control = session.execute(text("""
                    SELECT status, max_daily_loss_pct
                    FROM trading_controls
                    WHERE singleton = TRUE
                    FOR UPDATE
                """)).mappings().first()
                if risk_control is None:
                    raise RuntimeError("Trading risk control is missing")
                if risk_control["status"] == "HALTED":
                    raise ValueError("Trading is halted by the operator")
                daily_risk = session.execute(text("""
                    SELECT daily_return_pct, limit_breached
                    FROM trading_daily_risk
                    WHERE session_date = (
                        CURRENT_TIMESTAMP
                        AT TIME ZONE 'Europe/Stockholm'
                    )::DATE
                    FOR UPDATE
                """)).mappings().first()
                if (
                    daily_risk is not None
                    and (
                        daily_risk["limit_breached"]
                        or Decimal(daily_risk["daily_return_pct"])
                        <= -Decimal(
                            risk_control["max_daily_loss_pct"]
                        )
                    )
                ):
                    raise ValueError(
                        "Daily loss limit blocks new entry trades"
                    )

            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:ticker, 0))"
                ),
                {'ticker': normalized['ticker']},
            )

            session.execute(
                text(
                    "LOCK TABLE paper_benchmark_experiments "
                    "IN SHARE MODE"
                )
            )
            benchmark = session.execute(text("""
                SELECT
                    id,
                    reference_snapshot_id,
                    execution_price_source,
                    execution_provider_contract_id,
                    fee_bps,
                    spread_bps,
                    slippage_bps
                FROM paper_benchmark_experiments
                WHERE status = 'RUNNING'
            """)).mappings().first()
            cost_model = (
                ExecutionCostModel(
                    fee_bps=Decimal(benchmark['fee_bps']),
                    spread_bps=Decimal(benchmark['spread_bps']),
                    slippage_bps=Decimal(benchmark['slippage_bps']),
                )
                if benchmark is not None
                else ExecutionCostModel.zero()
            )
            source_book = None
            if normalized['source_book_state_id'] is not None:
                if (
                    benchmark is not None
                    and benchmark['execution_price_source']
                    != 'TOP_OF_BOOK_PLUS_SLIPPAGE'
                ):
                    raise ValueError(
                        "running benchmark requires its frozen "
                        "last-trade execution evidence"
                    )
                source_evidence = session.execute(text("""
                    SELECT
                        batch.provider_contract_id,
                        batch.reference_snapshot_id
                    FROM pre_trade_book_states state
                    JOIN pre_trade_batches batch
                      ON batch.id = state.batch_id
                    WHERE state.id = :source_book_state_id
                """), {
                    'source_book_state_id': normalized[
                        'source_book_state_id'
                    ],
                }).mappings().first()
                if source_evidence is None:
                    raise ValueError(
                        "source_book_state_id requires existing "
                        "pre-trade evidence"
                    )
                source_contract_id = int(
                    source_evidence['provider_contract_id']
                )
                if (
                    benchmark is not None
                    and (
                        source_contract_id
                        != int(
                            benchmark[
                                'execution_provider_contract_id'
                            ]
                        )
                        or int(
                            source_evidence['reference_snapshot_id']
                        )
                        != int(benchmark['reference_snapshot_id'])
                    )
                ):
                    raise ValueError(
                        "source_book_state_id does not match the "
                        "benchmark execution provider and snapshot"
                    )
                session.execute(
                    text(
                        "SELECT pg_advisory_xact_lock("
                        "hashtextextended(:lock_key, 0))"
                    ),
                    {
                        'lock_key': (
                            "pre-trade-contract:"
                            f"{source_contract_id}"
                        ),
                    },
                )
                source_book = session.execute(text("""
                    SELECT
                        state.bid_price,
                        state.ask_price,
                        state.bid_quantity,
                        state.ask_quantity,
                        state.bid_event_time,
                        state.bid_publication_time,
                        state.ask_event_time,
                        state.ask_publication_time,
                        state.instrument_id,
                        state.trading_system,
                        state.trading_phase,
                        company.ticker,
                        proof.status AS validation_status,
                        proof.validated_at,
                        proof.valid_until,
                        contract.status AS contract_status
                    FROM pre_trade_book_states state
                    JOIN pre_trade_batches batch
                      ON batch.id = state.batch_id
                    JOIN instruments instrument
                      ON instrument.id = state.instrument_id
                    JOIN companies company
                      ON company.instrument_id = instrument.id
                    JOIN market_data_provider_contracts contract
                      ON contract.id = batch.provider_contract_id
                    JOIN market_data_provider_validations proof
                      ON proof.id = batch.provider_validation_id
                     AND proof.id = (
                        SELECT latest_proof.id
                        FROM market_data_provider_validations latest_proof
                        WHERE latest_proof.contract_id = contract.id
                        ORDER BY
                            latest_proof.validated_at DESC,
                            latest_proof.id DESC
                        LIMIT 1
                    )
                    WHERE
                        state.id = :source_book_state_id
                        AND batch.provider_contract_id =
                            :provider_contract_id
                        AND batch.id = (
                            SELECT latest_batch.id
                            FROM pre_trade_batches latest_batch
                            JOIN pre_trade_stream_cursors seal
                              ON seal.batch_id = latest_batch.id
                            WHERE latest_batch.provider_contract_id =
                                :provider_contract_id
                            ORDER BY
                                latest_batch.report_minute DESC,
                                latest_batch.id DESC
                            LIMIT 1
                        )
                    FOR UPDATE OF state
                """), {
                    'source_book_state_id': normalized[
                        'source_book_state_id'
                    ],
                    'provider_contract_id': source_contract_id,
                }).mappings().first()
                if (
                    source_book is None
                    or source_book['ticker'] != normalized['ticker']
                    or source_book['bid_price'] is None
                    or source_book['ask_price'] is None
                    or source_book['trading_system'] != 'CLOB'
                    or source_book['trading_phase'] != 'COTR'
                    or source_book['validation_status'] != 'PASSED'
                    or source_book['contract_status'] != 'VALIDATED'
                ):
                    raise ValueError(
                        "source_book_state_id requires a matching "
                        "latest sealed two-sided book"
                    )
                open_session = session.execute(text("""
                    SELECT EXISTS (
                        SELECT 1
                        FROM market_sessions market_session
                        WHERE market_session.mic = 'XSTO'
                          AND market_session.status IN (
                              'OPEN',
                              'HALF_DAY'
                          )
                          AND CURRENT_TIMESTAMP >=
                              market_session.opens_at
                          AND CURRENT_TIMESTAMP <
                              market_session.closes_at
                    )
                """)).scalar_one()
                if not open_session:
                    raise ValueError(
                        "paper fill requires an open XSTO session"
                    )
                source_price = Decimal(
                    source_book[
                        'ask_price'
                        if normalized['action'] == 'BUY'
                        else 'bid_price'
                    ]
                ).quantize(Decimal('0.00000001'))
                executable_quantity = Decimal(
                    source_book[
                        'ask_quantity'
                        if normalized['action'] == 'BUY'
                        else 'bid_quantity'
                    ]
                )
                consumed_quantity = Decimal(session.execute(text("""
                    SELECT COALESCE(SUM(trade.shares), 0)
                    FROM trades trade
                    JOIN pre_trade_book_states used_state
                      ON used_state.id = trade.source_book_state_id
                    JOIN pre_trade_batches used_batch
                      ON used_batch.id = used_state.batch_id
                    WHERE
                        used_batch.provider_contract_id =
                            :provider_contract_id
                        AND used_state.instrument_id = :instrument_id
                        AND trade.action = :action
                        AND (
                            (
                                :action = 'BUY'
                                AND used_state.ask_event_time =
                                    :side_event_time
                                AND used_state.ask_publication_time =
                                    :side_publication_time
                                AND used_state.ask_price = :side_price
                                AND used_state.ask_quantity =
                                    :side_quantity
                            )
                            OR (
                                :action = 'SELL'
                                AND used_state.bid_event_time =
                                    :side_event_time
                                AND used_state.bid_publication_time =
                                    :side_publication_time
                                AND used_state.bid_price = :side_price
                                AND used_state.bid_quantity =
                                    :side_quantity
                            )
                        )
                """), {
                    'provider_contract_id': source_contract_id,
                    'instrument_id': int(
                        source_book['instrument_id']
                    ),
                    'action': normalized['action'],
                    'side_event_time': source_book[
                        'ask_event_time'
                        if normalized['action'] == 'BUY'
                        else 'bid_event_time'
                    ],
                    'side_publication_time': source_book[
                        'ask_publication_time'
                        if normalized['action'] == 'BUY'
                        else 'bid_publication_time'
                    ],
                    'side_price': source_price,
                    'side_quantity': executable_quantity,
                }).scalar_one())
                if (
                    consumed_quantity + normalized['shares']
                    > executable_quantity
                ):
                    raise ValueError(
                        "paper fill exceeds remaining executable "
                        "bid/ask quantity"
                    )
                if source_price != normalized['price']:
                    raise ValueError(
                        "trade price does not match the executable "
                        "bid/ask side"
                    )
                normalized['price'] = source_price
            if benchmark is not None:
                if (
                    benchmark['execution_price_source']
                    == 'LAST_TRADE_PLUS_BPS'
                ):
                    if normalized['source_quote_id'] is None:
                        raise ValueError(
                            "source_quote_id is required by the running "
                            "benchmark execution contract"
                        )
                    source_quote = session.execute(text("""
                        SELECT quote.last_price, company.ticker
                        FROM market_quotes quote
                        JOIN market_data_files source_file
                          ON source_file.id = quote.data_file_id
                         AND source_file.provider_contract_id =
                            :execution_provider_contract_id
                        JOIN instruments instrument
                          ON instrument.id = quote.instrument_id
                        JOIN companies company
                          ON company.instrument_id = instrument.id
                        JOIN reference_snapshot_instruments membership
                          ON membership.instrument_id =
                                quote.instrument_id
                         AND membership.snapshot_id =
                                :reference_snapshot_id
                        WHERE quote.id = :source_quote_id
                    """), {
                        'source_quote_id': normalized['source_quote_id'],
                        'execution_provider_contract_id': int(
                            benchmark[
                                'execution_provider_contract_id'
                            ]
                        ),
                        'reference_snapshot_id': int(
                            benchmark['reference_snapshot_id']
                        ),
                    }).mappings().first()
                    if (
                        source_quote is None
                        or source_quote['ticker']
                        != normalized['ticker']
                    ):
                        raise ValueError(
                            "source_quote_id does not match the trade ticker"
                        )
                    source_price = Decimal(
                        source_quote['last_price']
                    ).quantize(Decimal('0.00000001'))
                    if source_price != normalized['price']:
                        raise ValueError(
                            "trade price does not match source_quote_id"
                        )
                    normalized['price'] = source_price
                elif source_book is None:
                    raise ValueError(
                        "source_book_state_id is required by the running "
                        "benchmark execution contract"
                    )
            if benchmark is None and source_book is not None:
                cost_model = ExecutionCostModel.swedish_retail()
            if source_book is not None and (
                benchmark is None
                or benchmark['execution_price_source']
                == 'TOP_OF_BOOK_PLUS_SLIPPAGE'
            ):
                execution = calculate_top_of_book_execution(
                    action=normalized['action'],
                    bid_price=Decimal(source_book['bid_price']),
                    ask_price=Decimal(source_book['ask_price']),
                    shares=normalized['shares'],
                    costs=cost_model,
                )
            else:
                execution = calculate_paper_execution(
                    action=normalized['action'],
                    quote_price=normalized['price'],
                    shares=normalized['shares'],
                    costs=cost_model,
                )
            normalized.update({
                'benchmark_experiment_id': (
                    int(benchmark['id'])
                    if benchmark is not None
                    else None
                ),
                'quote_price': execution.quote_price,
                'price': execution.execution_price,
                'total_value': execution.gross_value,
                'fee_amount': execution.fee_amount,
                'spread_cost': execution.spread_cost,
                'slippage_cost': execution.slippage_cost,
                'net_cash_effect': execution.net_cash_effect,
            })
            if normalized['action'] == 'BUY':
                if normalized['target_pct'] is not None:
                    normalized['target_price'] = (
                        execution.execution_price
                        * (
                            Decimal('1')
                            + normalized['target_pct']
                            / Decimal('100')
                        )
                    ).quantize(
                        Decimal('0.00000001'),
                        rounding=ROUND_HALF_UP,
                    )
                if normalized['stop_loss_pct'] is not None:
                    normalized['stop_loss'] = (
                        execution.execution_price
                        * (
                            Decimal('1')
                            + normalized['stop_loss_pct']
                            / Decimal('100')
                        )
                    ).quantize(
                        Decimal('0.00000001'),
                        rounding=ROUND_HALF_UP,
                    )

            balance = session.execute(text("""
                SELECT id, cash
                FROM balance
                ORDER BY id DESC
                LIMIT 1
                FOR UPDATE
            """)).mappings().first()
            if balance is None:
                raise RuntimeError("Balance row is missing")

            position = session.execute(text("""
                SELECT id, shares, avg_price
                FROM portfolio
                WHERE ticker = :ticker
                FOR UPDATE
            """), {'ticker': normalized['ticker']}).mappings().first()

            entry_trade_id = None
            realized_pnl = None
            closes_position = False
            allocations = []

            if normalized['action'] == 'BUY':
                if (
                    Decimal(balance['cash'])
                    < normalized['net_cash_effect']
                ):
                    raise ValueError(
                        f"Insufficient cash for {normalized['ticker']}: "
                        f"need {normalized['net_cash_effect']}, "
                        f"have {balance['cash']}"
                    )
            else:
                if position is None:
                    raise ValueError(
                        f"Cannot sell {normalized['ticker']}: no open position"
                    )

                held_shares = Decimal(position['shares'])
                if normalized['shares'] > held_shares:
                    raise ValueError(
                        f"Cannot sell {normalized['shares']} {normalized['ticker']}; "
                        f"only {held_shares} held"
                    )

                lots = session.execute(text("""
                    SELECT
                        id,
                        entry_trade_id,
                        remaining_shares,
                        entry_price,
                        remaining_entry_fee,
                        source
                    FROM position_lots
                    WHERE ticker = :ticker AND remaining_shares > 0
                    ORDER BY opened_at, id
                    FOR UPDATE
                """), {
                    'ticker': normalized['ticker'],
                }).mappings().all()
                lot_shares = sum(
                    (Decimal(lot['remaining_shares']) for lot in lots),
                    Decimal('0'),
                )
                if lot_shares != held_shares:
                    raise RuntimeError(
                        f"Position lot mismatch for {normalized['ticker']}: "
                        f"portfolio={held_shares}, lots={lot_shares}"
                    )

                shares_to_allocate = normalized['shares']
                exit_fee_remaining = normalized['fee_amount']
                for lot in lots:
                    if shares_to_allocate == 0:
                        break
                    lot_remaining = Decimal(lot['remaining_shares'])
                    allocated_shares = min(
                        lot_remaining,
                        shares_to_allocate,
                    )
                    entry_price = Decimal(lot['entry_price'])
                    gross_realized_pnl = (
                        (normalized['price'] - entry_price)
                        * allocated_shares
                    ).quantize(
                        Decimal('0.01'),
                        rounding=ROUND_HALF_UP,
                    )
                    lot_entry_fee = Decimal(
                        lot['remaining_entry_fee']
                    )
                    if allocated_shares == lot_remaining:
                        allocated_entry_fee = lot_entry_fee
                    else:
                        allocated_entry_fee = (
                            lot_entry_fee
                            * allocated_shares
                            / lot_remaining
                        ).quantize(
                            Decimal('0.01'),
                            rounding=ROUND_HALF_UP,
                        )
                    remaining_after = (
                        shares_to_allocate - allocated_shares
                    )
                    if remaining_after == 0:
                        allocated_exit_fee = exit_fee_remaining
                    else:
                        allocated_exit_fee = (
                            normalized['fee_amount']
                            * allocated_shares
                            / normalized['shares']
                        ).quantize(
                            Decimal('0.01'),
                            rounding=ROUND_HALF_UP,
                        )
                    exit_fee_remaining -= allocated_exit_fee
                    allocation_pnl = (
                        gross_realized_pnl
                        - allocated_entry_fee
                        - allocated_exit_fee
                    ).quantize(
                        Decimal('0.01'),
                        rounding=ROUND_HALF_UP,
                    )
                    allocations.append({
                        'lot_id': int(lot['id']),
                        'buy_trade_id': (
                            int(lot['entry_trade_id'])
                            if lot['entry_trade_id'] is not None
                            else None
                        ),
                        'shares': allocated_shares,
                        'entry_price': entry_price,
                        'exit_price': normalized['price'],
                        'gross_realized_pnl': gross_realized_pnl,
                        'allocated_entry_fee': allocated_entry_fee,
                        'allocated_exit_fee': allocated_exit_fee,
                        'realized_pnl': allocation_pnl,
                        'new_remaining_shares': (
                            lot_remaining - allocated_shares
                        ),
                        'new_remaining_entry_fee': (
                            lot_entry_fee - allocated_entry_fee
                        ),
                        'source': lot['source'],
                    })
                    shares_to_allocate = remaining_after
                if shares_to_allocate != 0:
                    raise RuntimeError(
                        f"Insufficient lots for {normalized['ticker']}"
                    )

                realized_pnl = sum(
                    (
                        allocation['realized_pnl']
                        for allocation in allocations
                    ),
                    Decimal('0.00'),
                ).quantize(Decimal('0.01'))
                closes_position = normalized['shares'] == held_shares
                entry_trade_id = next(
                    (
                        allocation['buy_trade_id']
                        for allocation in allocations
                        if allocation['buy_trade_id'] is not None
                    ),
                    None,
                )

            insert_trade = dict(normalized)
            if source_book is not None:
                raw_side_price = Decimal(
                    source_book[
                        'ask_price'
                        if normalized['action'] == 'BUY'
                        else 'bid_price'
                    ]
                ).quantize(Decimal('0.00000001'))
                insert_trade.update({
                    'quote_price': None,
                    'price': raw_side_price,
                    'total_value': (
                        raw_side_price * normalized['shares']
                    ).quantize(Decimal('0.01')),
                    'fee_amount': None,
                    'spread_cost': None,
                    'slippage_cost': None,
                    'net_cash_effect': None,
                })

            result = session.execute(text("""
                INSERT INTO trades (
                    ticker, action, shares, quote_price, price, total_value,
                    fee_amount, spread_cost, slippage_cost, net_cash_effect,
                    reasoning, confidence, hypothesis, macro_context,
                    target_price, stop_loss, target_pct, stop_loss_pct,
                    entry_trade_id, pnl, closes_position,
                    idempotency_key, request_fingerprint, strategy_version,
                    benchmark_experiment_id, source_quote_id,
                    source_book_state_id,
                    decision_id, decision_origin
                )
                VALUES (
                    :ticker, :action, :shares, :quote_price, :price,
                    :total_value, :fee_amount, :spread_cost,
                    :slippage_cost, :net_cash_effect,
                    :reasoning, :confidence, :hypothesis,
                    CAST(:macro_context AS JSONB),
                    :target_price, :stop_loss, :target_pct, :stop_loss_pct,
                    :entry_trade_id, :pnl, :closes_position,
                    :idempotency_key, :request_fingerprint, :strategy_version,
                    :benchmark_experiment_id, :source_quote_id,
                    :source_book_state_id,
                    :decision_id, :decision_origin
                )
                RETURNING id, executed_at
            """), {
                **insert_trade,
                'entry_trade_id': entry_trade_id,
                'pnl': realized_pnl,
                'closes_position': closes_position,
                'request_fingerprint': request_fingerprint,
            }).mappings().one()
            trade_id = int(result['id'])

            if normalized['action'] == 'BUY':
                session.execute(text("""
                    UPDATE balance
                    SET cash = cash - :amount, updated_at = NOW()
                    WHERE id = :balance_id
                """), {
                    'amount': normalized['net_cash_effect'],
                    'balance_id': balance['id'],
                })
                session.execute(text("""
                    INSERT INTO portfolio (
                        ticker, shares, avg_price, current_price
                    )
                    VALUES (:ticker, :shares, :price, :price)
                    ON CONFLICT (ticker) DO UPDATE SET
                        avg_price = (
                            portfolio.avg_price * portfolio.shares
                            + EXCLUDED.avg_price * EXCLUDED.shares
                        ) / (portfolio.shares + EXCLUDED.shares),
                        shares = portfolio.shares + EXCLUDED.shares,
                        current_price = EXCLUDED.current_price,
                        updated_at = NOW()
                """), normalized)
                session.execute(text("""
                    INSERT INTO position_lots (
                        ticker,
                        entry_trade_id,
                        original_shares,
                        remaining_shares,
                        entry_price,
                        entry_fee_total,
                        remaining_entry_fee,
                        opened_at,
                        source
                    )
                    VALUES (
                        :ticker,
                        :entry_trade_id,
                        :shares,
                        :shares,
                        :price,
                        :fee_amount,
                        :fee_amount,
                        :opened_at,
                        'TRADE'
                    )
                """), {
                    'ticker': normalized['ticker'],
                    'entry_trade_id': trade_id,
                    'shares': normalized['shares'],
                    'price': normalized['price'],
                    'fee_amount': normalized['fee_amount'],
                    'opened_at': result['executed_at'],
                })
            else:
                session.execute(text("""
                    UPDATE balance
                    SET cash = cash + :amount, updated_at = NOW()
                    WHERE id = :balance_id
                """), {
                    'amount': normalized['net_cash_effect'],
                    'balance_id': balance['id'],
                })

                migrated_lot_closed = False
                for allocation in allocations:
                    session.execute(text("""
                        INSERT INTO trade_lot_allocations (
                            sell_trade_id,
                            lot_id,
                            buy_trade_id,
                            shares,
                            entry_price,
                            exit_price,
                            gross_realized_pnl,
                            allocated_entry_fee,
                            allocated_exit_fee,
                            realized_pnl
                        )
                        VALUES (
                            :sell_trade_id,
                            :lot_id,
                            :buy_trade_id,
                            :shares,
                            :entry_price,
                            :exit_price,
                            :gross_realized_pnl,
                            :allocated_entry_fee,
                            :allocated_exit_fee,
                            :realized_pnl
                        )
                    """), {
                        'sell_trade_id': trade_id,
                        **allocation,
                    })
                    lot_closed = (
                        allocation['new_remaining_shares'] == 0
                    )
                    session.execute(text("""
                        UPDATE position_lots
                        SET
                            remaining_shares = :remaining_shares,
                            remaining_entry_fee =
                                :remaining_entry_fee,
                            closed_at = CASE
                                WHEN :remaining_shares = 0 THEN NOW()
                                ELSE NULL
                            END
                        WHERE id = :lot_id
                    """), {
                        'remaining_shares': (
                            allocation['new_remaining_shares']
                        ),
                        'remaining_entry_fee': (
                            allocation['new_remaining_entry_fee']
                        ),
                        'lot_id': allocation['lot_id'],
                    })
                    buy_trade_id = allocation['buy_trade_id']
                    if buy_trade_id is not None and lot_closed:
                        session.execute(text("""
                            UPDATE trades
                            SET
                                closed_at = NOW(),
                                pnl = (
                                    SELECT COALESCE(
                                        SUM(realized_pnl),
                                        0
                                    )
                                    FROM trade_lot_allocations
                                    WHERE buy_trade_id = :buy_trade_id
                                )
                            WHERE id = :buy_trade_id
                        """), {'buy_trade_id': buy_trade_id})
                    if (
                        allocation['source']
                        == 'MIGRATED_AVERAGE_COST'
                        and lot_closed
                    ):
                        migrated_lot_closed = True

                if migrated_lot_closed:
                    session.execute(text("""
                        UPDATE trades legacy
                        SET
                            closed_at = NOW(),
                            outcome = COALESCE(
                                outcome,
                                'MIGRATED_AVERAGE_COST_CLOSED'
                            )
                        WHERE
                            legacy.ticker = :ticker
                            AND legacy.action = 'BUY'
                            AND legacy.closed_at IS NULL
                            AND NOT EXISTS (
                                SELECT 1
                                FROM position_lots lot
                                WHERE lot.entry_trade_id = legacy.id
                            )
                    """), {'ticker': normalized['ticker']})

                remaining = session.execute(text("""
                    SELECT
                        COALESCE(SUM(remaining_shares), 0) AS shares,
                        CASE
                            WHEN SUM(remaining_shares) > 0
                            THEN SUM(remaining_shares * entry_price)
                                / SUM(remaining_shares)
                            ELSE NULL
                        END AS avg_price
                    FROM position_lots
                    WHERE ticker = :ticker AND remaining_shares > 0
                """), {
                    'ticker': normalized['ticker'],
                }).mappings().one()
                expected_remaining = (
                    held_shares - normalized['shares']
                )
                remaining_shares = Decimal(remaining['shares'])
                if remaining_shares != expected_remaining:
                    raise RuntimeError(
                        f"Post-allocation lot mismatch for "
                        f"{normalized['ticker']}"
                    )

                if closes_position:
                    session.execute(
                        text("DELETE FROM portfolio WHERE id = :position_id"),
                        {'position_id': position['id']},
                    )
                else:
                    session.execute(text("""
                        UPDATE portfolio
                        SET
                            shares = :remaining_shares,
                            avg_price = :avg_price,
                            current_price = :price,
                            updated_at = NOW()
                        WHERE id = :position_id
                    """), {
                        'remaining_shares': remaining_shares,
                        'avg_price': remaining['avg_price'],
                        'price': normalized['price'],
                        'position_id': position['id'],
                    })

            session.execute(text("""
                UPDATE balance b
                SET
                    total_value = b.cash + COALESCE((
                        SELECT SUM(
                            p.shares * COALESCE(
                                p.current_price,
                                p.avg_price
                            )
                        )
                        FROM portfolio p
                    ), 0),
                    updated_at = NOW()
                WHERE b.id = :balance_id
            """), {'balance_id': balance['id']})

            return TradeLogResult(trade_id=trade_id, inserted=True)

    @staticmethod
    def _normalize_trade(trade: dict) -> dict:
        if not isinstance(trade, dict):
            raise ValueError("Trade must be an object")

        ticker = str(trade.get('ticker', '')).strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,19}", ticker):
            raise ValueError("Invalid ticker")

        action = str(trade.get('action', '')).strip().upper()
        if action not in {'BUY', 'SELL'}:
            raise ValueError("Action must be BUY or SELL")

        idempotency_key = trade.get('idempotency_key')
        if (
            not isinstance(idempotency_key, str)
            or idempotency_key != idempotency_key.strip()
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9:._/-]{7,159}",
                idempotency_key,
            )
        ):
            raise ValueError(
                "idempotency_key must be 8-160 safe characters"
            )

        shares = Database._positive_decimal(
            trade.get('shares'),
            'shares',
        ).quantize(Decimal('0.0001'))
        price = Database._positive_decimal(
            trade.get('price'),
            'price',
        ).quantize(Decimal('0.00000001'))
        if shares <= 0 or price <= 0:
            raise ValueError(
                "shares and price must remain positive at database precision"
            )
        calculated_total = (shares * price).quantize(Decimal('0.01'))
        supplied_total = Database._positive_decimal(
            trade.get('total_value'),
            'total_value',
        ).quantize(Decimal('0.01'))
        if abs(calculated_total - supplied_total) > Decimal('0.01'):
            raise ValueError(
                "total_value must equal shares multiplied by price"
            )

        source_quote_id = trade.get('source_quote_id')
        if source_quote_id is not None and (
            isinstance(source_quote_id, bool)
            or not isinstance(source_quote_id, int)
            or source_quote_id <= 0
        ):
            raise ValueError(
                "source_quote_id must be a positive integer"
            )
        source_book_state_id = trade.get('source_book_state_id')
        if source_book_state_id is not None and (
            isinstance(source_book_state_id, bool)
            or not isinstance(source_book_state_id, int)
            or source_book_state_id <= 0
        ):
            raise ValueError(
                "source_book_state_id must be a positive integer"
            )
        if (
            source_quote_id is not None
            and source_book_state_id is not None
        ):
            raise ValueError(
                "trade must use exactly one price evidence source"
            )

        decision_origin = trade.get("decision_origin", "LEGACY")
        if not isinstance(decision_origin, str):
            raise ValueError("decision_origin has invalid value")
        decision_origin = decision_origin.strip().upper()
        if decision_origin not in {
            "AI_DECISION",
            "AUTOMATED_SCAN",
            "MECHANICAL_EXIT",
            "MANUAL",
            "LEGACY",
        }:
            raise ValueError("decision_origin has invalid value")
        decision_id = trade.get("decision_id")
        if decision_origin == "AI_DECISION":
            if (
                isinstance(decision_id, bool)
                or not isinstance(decision_id, int)
                or decision_id <= 0
            ):
                raise ValueError(
                    "decision_id is required for an AI_DECISION trade"
                )
        elif decision_id is not None:
            raise ValueError(
                "decision_id is only allowed for an AI_DECISION trade"
            )

        reasoning = trade.get('reasoning')
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise ValueError("reasoning is required")

        confidence = trade.get('confidence')
        if confidence is not None:
            confidence = Database._finite_decimal(
                confidence,
                'confidence',
            ).quantize(Decimal('0.01'))
            if not 0 <= confidence <= 100:
                raise ValueError("confidence must be between 0 and 100")

        hypothesis = trade.get('hypothesis')
        if hypothesis is not None:
            if not isinstance(hypothesis, str):
                raise ValueError("hypothesis must be a string")
            hypothesis = hypothesis.strip()
            if len(hypothesis) > 2_000:
                raise ValueError("hypothesis is too long")

        macro_context = trade.get('macro_context', {})
        try:
            macro_context_json = json.dumps(
                macro_context,
                ensure_ascii=False,
                allow_nan=False,
                separators=(',', ':'),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("macro_context must be valid JSON") from exc

        strategy_version = trade.get(
            'strategy_version',
            'legacy-unversioned',
        )
        if (
            not isinstance(strategy_version, str)
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{2,49}",
                strategy_version,
            )
        ):
            raise ValueError("strategy_version has invalid format")

        return {
            'ticker': ticker,
            'action': action,
            'shares': shares,
            'price': price,
            'total_value': calculated_total,
            'source_quote_id': source_quote_id,
            'source_book_state_id': source_book_state_id,
            'decision_id': decision_id,
            'decision_origin': decision_origin,
            'idempotency_key': idempotency_key,
            'reasoning': reasoning.strip(),
            'confidence': confidence,
            'hypothesis': hypothesis,
            'macro_context': macro_context_json,
            'strategy_version': strategy_version,
            'target_price': Database._optional_decimal(
                trade.get('target_price'),
                'target_price',
                positive=True,
            ).quantize(Decimal('0.00000001'))
            if trade.get('target_price') is not None
            else None,
            'stop_loss': Database._optional_decimal(
                trade.get('stop_loss'),
                'stop_loss',
                positive=True,
            ).quantize(Decimal('0.00000001'))
            if trade.get('stop_loss') is not None
            else None,
            'target_pct': Database._optional_decimal(
                trade.get('target_pct', 10),
                'target_pct',
            ).quantize(Decimal('0.0001'))
            if trade.get('target_pct', 10) is not None
            else None,
            'stop_loss_pct': Database._optional_decimal(
                trade.get('stop_loss_pct', -5),
                'stop_loss_pct',
            ).quantize(Decimal('0.0001'))
            if trade.get('stop_loss_pct', -5) is not None
            else None,
        }

    @staticmethod
    def _trade_request_fingerprint(normalized: dict) -> str:
        payload = {
            key: (
                str(value)
                if isinstance(value, Decimal)
                else value
            )
            for key, value in normalized.items()
            if (
                key != 'idempotency_key'
                and not (
                    key == 'source_book_state_id'
                    and value is None
                )
            )
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(',', ':'),
            sort_keys=True,
        ).encode('utf-8')
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _positive_decimal(value: Any, field: str) -> Decimal:
        result = Database._finite_decimal(value, field)
        if result <= 0:
            raise ValueError(f"{field} must be greater than 0")
        return result

    @staticmethod
    def _finite_decimal(value: Any, field: str) -> Decimal:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a finite number")
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise ValueError(f"{field} must be a finite number") from exc
        if not result.is_finite():
            raise ValueError(f"{field} must be a finite number")
        return result

    @staticmethod
    def _optional_decimal(
        value: Any,
        field: str,
        *,
        positive: bool = False,
    ) -> Optional[Decimal]:
        if value is None:
            return None
        result = Database._finite_decimal(value, field)
        if positive and result <= 0:
            raise ValueError(f"{field} must be greater than 0")
        return result
    
    def get_trades(self, limit: int = 50) -> pd.DataFrame:
        """Get recent trades."""
        with self.Session() as session:
            result = session.execute(text(
                "SELECT * FROM trades ORDER BY executed_at DESC LIMIT :limit"
            ), {'limit': limit})
            return pd.DataFrame(result.fetchall(), columns=result.keys())
    
    def get_learnings(self, active_only: bool = True) -> List[dict]:
        """Get agent learnings."""
        with self.Session() as session:
            query = "SELECT * FROM learnings"
            if active_only:
                query += " WHERE active = true"
            query += " ORDER BY confidence DESC"
            result = session.execute(text(query))
            return [dict(row._mapping) for row in result.fetchall()]

    def get_active_strategy(self):
        """Return the one active, hash-verified strategy and linked learnings."""
        return self._get_strategy("WHERE s.status = 'ACTIVE'")

    def get_strategy_version(self, version: str):
        """Load any approved/active/retired strategy for audit or backtest."""
        from ..core.strategy import validate_strategy_version

        normalized = validate_strategy_version(version)
        return self._get_strategy(
            """
            WHERE s.version = :version
              AND s.status IN ('APPROVED', 'ACTIVE', 'RETIRED')
            """,
            {'version': normalized},
        )

    def _get_strategy(self, where: str, params: dict | None = None):
        from ..core.strategy import ActiveStrategy

        with self.Session() as session:
            rows = session.execute(text(f"""
                SELECT
                    s.version,
                    s.config,
                    RTRIM(s.config_hash) AS config_hash,
                    l.id AS learning_id,
                    l.content AS learning_content,
                    sl.usage_note
                FROM strategy_versions s
                LEFT JOIN strategy_version_learnings sl
                    ON sl.strategy_version_id = s.id
                LEFT JOIN learnings l
                    ON l.id = sl.learning_id
                {where}
                ORDER BY l.id
            """), params or {}).mappings().all()

        if not rows:
            raise RuntimeError("Requested strategy version does not exist")
        versions = {row['version'] for row in rows}
        if len(versions) != 1:
            raise RuntimeError("More than one active strategy version exists")

        first = rows[0]
        learnings = [
            {
                'id': int(row['learning_id']),
                'content': row['learning_content'],
                'usage_note': row['usage_note'],
            }
            for row in rows
            if row['learning_id'] is not None
        ]
        return ActiveStrategy.from_record({
            'version': first['version'],
            'config': first['config'],
            'config_hash': first['config_hash'],
            'learnings': learnings,
        })

    def create_strategy_change_proposal(
        self,
        *,
        config_patch: dict,
        learning_ids: Sequence[int],
        rationale: str,
        proposed_by: str,
    ) -> int:
        """Create a pending proposal; this never changes the active strategy."""
        from ..core.strategy import (
            ActiveStrategy,
            merge_strategy_patch,
            strategy_config_hash,
        )

        rationale = self._bounded_identity_text(
            rationale,
            'rationale',
            4000,
        )
        proposed_by = self._bounded_identity_text(
            proposed_by,
            'proposed_by',
            100,
        )
        normalized_learning_ids = self._normalize_learning_ids(learning_ids)

        with self.Session.begin() as session:
            base_row = session.execute(text("""
                SELECT
                    id,
                    version,
                    config,
                    RTRIM(config_hash) AS config_hash
                FROM strategy_versions
                WHERE status = 'ACTIVE'
                FOR SHARE
            """)).mappings().one_or_none()
            if base_row is None:
                raise RuntimeError("No active strategy version exists")

            base = ActiveStrategy.from_record({
                'version': base_row['version'],
                'config': base_row['config'],
                'config_hash': base_row['config_hash'],
                'learnings': [],
            })
            candidate = merge_strategy_patch(base.config, config_patch)
            candidate_hash = strategy_config_hash(candidate)

            learning_rows = session.execute(text("""
                SELECT id
                FROM learnings
                WHERE active = TRUE
                  AND LENGTH(BTRIM(content)) BETWEEN 1 AND 2000
                  AND id = ANY(CAST(:learning_ids AS INTEGER[]))
            """), {
                'learning_ids': normalized_learning_ids,
            }).scalars().all()
            if set(map(int, learning_rows)) != set(normalized_learning_ids):
                raise ValueError(
                    "Every strategy proposal must reference active learnings"
                )

            proposal_id = session.execute(text("""
                INSERT INTO strategy_change_proposals (
                    base_version_id,
                    config_patch,
                    config_hash,
                    learning_ids,
                    rationale,
                    proposed_by
                )
                VALUES (
                    :base_version_id,
                    CAST(:config_patch AS JSONB),
                    :config_hash,
                    :learning_ids,
                    :rationale,
                    :proposed_by
                )
                RETURNING id
            """), {
                'base_version_id': base_row['id'],
                'config_patch': json.dumps(
                    config_patch,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(',', ':'),
                    sort_keys=True,
                ),
                'config_hash': candidate_hash,
                'learning_ids': normalized_learning_ids,
                'rationale': rationale,
                'proposed_by': proposed_by,
            }).scalar_one()
            return int(proposal_id)

    def approve_strategy_change_proposal(
        self,
        proposal_id: int,
        *,
        new_version: str,
        reviewed_by: str,
    ) -> int:
        """Approve into an inactive version; activation is a separate action."""
        from ..core.strategy import (
            ActiveStrategy,
            merge_strategy_patch,
            strategy_config_hash,
            validate_strategy_version,
        )

        new_version = validate_strategy_version(new_version)
        reviewed_by = self._operator_identity(reviewed_by)
        proposal_id = self._positive_integer(proposal_id, 'proposal_id')

        with self.Session.begin() as session:
            proposal = session.execute(text("""
                SELECT
                    p.*,
                    s.version AS base_version,
                    s.config AS base_config,
                    RTRIM(s.config_hash) AS base_config_hash,
                    s.status AS base_status
                FROM strategy_change_proposals p
                JOIN strategy_versions s ON s.id = p.base_version_id
                WHERE p.id = :proposal_id
                FOR UPDATE OF p, s
            """), {'proposal_id': proposal_id}).mappings().one_or_none()
            if proposal is None:
                raise ValueError("Strategy proposal does not exist")
            if proposal['status'] != 'PENDING':
                raise ValueError("Strategy proposal is not pending")
            if proposal['base_status'] != 'ACTIVE':
                raise ValueError(
                    "Strategy proposal is stale because its base is no longer active"
                )

            base = ActiveStrategy.from_record({
                'version': proposal['base_version'],
                'config': proposal['base_config'],
                'config_hash': proposal['base_config_hash'],
                'learnings': [],
            })
            candidate = merge_strategy_patch(
                base.config,
                proposal['config_patch'],
            )
            candidate_hash = strategy_config_hash(candidate)
            if proposal['config_hash'].strip() != candidate_hash:
                raise RuntimeError("Strategy proposal config hash mismatch")

            learning_ids = list(map(int, proposal['learning_ids']))
            learning_rows = session.execute(text("""
                SELECT id
                FROM learnings
                WHERE active = TRUE
                  AND LENGTH(BTRIM(content)) BETWEEN 1 AND 2000
                  AND id = ANY(CAST(:learning_ids AS INTEGER[]))
                FOR SHARE
            """), {'learning_ids': learning_ids}).scalars().all()
            if set(map(int, learning_rows)) != set(learning_ids):
                raise ValueError(
                    "Strategy proposal references inactive or missing learnings"
                )

            strategy_id = session.execute(text("""
                INSERT INTO strategy_versions (
                    version,
                    status,
                    config,
                    config_hash,
                    parent_version_id,
                    proposed_by,
                    approved_by,
                    approved_at
                )
                VALUES (
                    :version,
                    'APPROVED',
                    CAST(:config AS JSONB),
                    :config_hash,
                    :parent_version_id,
                    :proposed_by,
                    :approved_by,
                    NOW()
                )
                RETURNING id
            """), {
                'version': new_version,
                'config': json.dumps(
                    candidate.to_dict(),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(',', ':'),
                    sort_keys=True,
                ),
                'config_hash': candidate_hash,
                'parent_version_id': proposal['base_version_id'],
                'proposed_by': proposal['proposed_by'],
                'approved_by': reviewed_by,
            }).scalar_one()

            for learning_id in learning_ids:
                session.execute(text("""
                    INSERT INTO strategy_version_learnings (
                        strategy_version_id,
                        learning_id,
                        usage_note
                    )
                    VALUES (
                        :strategy_version_id,
                        :learning_id,
                        :usage_note
                    )
                """), {
                    'strategy_version_id': strategy_id,
                    'learning_id': learning_id,
                    'usage_note': proposal['rationale'],
                })

            session.execute(text("""
                UPDATE strategy_change_proposals
                SET
                    status = 'APPROVED',
                    proposed_version_id = :strategy_version_id,
                    reviewed_by = :reviewed_by,
                    reviewed_at = NOW(),
                    updated_at = NOW()
                WHERE id = :proposal_id
            """), {
                'strategy_version_id': strategy_id,
                'reviewed_by': reviewed_by,
                'proposal_id': proposal_id,
            })
            return int(strategy_id)

    def activate_strategy_version(
        self,
        version: str,
        *,
        activated_by: str,
    ) -> None:
        """Atomically activate an approved child of the current strategy."""
        from ..core.strategy import validate_strategy_version

        version = validate_strategy_version(version)
        activated_by = self._operator_identity(activated_by)

        with self.Session.begin() as session:
            session.execute(text("""
                SELECT pg_advisory_xact_lock(
                    hashtextextended('strategy-activation', 0)
                )
            """))
            current = session.execute(text("""
                SELECT id, version
                FROM strategy_versions
                WHERE status = 'ACTIVE'
                FOR UPDATE
            """)).mappings().one_or_none()
            candidate = session.execute(text("""
                SELECT id, status, parent_version_id
                FROM strategy_versions
                WHERE version = :version
                FOR UPDATE
            """), {'version': version}).mappings().one_or_none()
            if current is None:
                raise RuntimeError("No active strategy version exists")
            if candidate is None:
                raise ValueError("Strategy version does not exist")
            if candidate['status'] != 'APPROVED':
                raise ValueError("Only an approved strategy can be activated")
            if candidate['parent_version_id'] != current['id']:
                raise ValueError(
                    "Approved strategy is not based on the active version"
                )

            session.execute(text("""
                UPDATE strategy_versions
                SET status = 'RETIRED', retired_at = NOW(), updated_at = NOW()
                WHERE id = :current_id
            """), {'current_id': current['id']})
            session.execute(text("""
                UPDATE strategy_versions
                SET
                    status = 'ACTIVE',
                    activated_by = :activated_by,
                    activated_at = NOW(),
                    updated_at = NOW()
                WHERE id = :candidate_id
            """), {
                'activated_by': activated_by,
                'candidate_id': candidate['id'],
            })

    def reject_strategy_change_proposal(
        self,
        proposal_id: int,
        *,
        reviewed_by: str,
        reason: str,
    ) -> None:
        """Reject a pending proposal without creating a strategy version."""
        proposal_id = self._positive_integer(proposal_id, 'proposal_id')
        reviewed_by = self._operator_identity(reviewed_by)
        reason = self._bounded_identity_text(reason, 'reason', 4000)
        with self.Session.begin() as session:
            updated = session.execute(text("""
                UPDATE strategy_change_proposals
                SET
                    status = 'REJECTED',
                    reviewed_by = :reviewed_by,
                    reviewed_at = NOW(),
                    rejection_reason = :reason,
                    updated_at = NOW()
                WHERE id = :proposal_id AND status = 'PENDING'
                RETURNING id
            """), {
                'reviewed_by': reviewed_by,
                'reason': reason,
                'proposal_id': proposal_id,
            }).scalar_one_or_none()
            if updated is None:
                raise ValueError("Strategy proposal does not exist or is not pending")

    def add_learning(self, learning: dict) -> int:
        """Validate and add bounded evidence that can be rendered safely."""
        if not isinstance(learning, dict):
            raise ValueError("learning must be an object")
        category = self._bounded_identity_text(
            learning.get('category'),
            'learning category',
            50,
        )
        content = self._bounded_identity_text(
            learning.get('content'),
            'learning content',
            2000,
        )
        confidence = self._finite_decimal(
            learning.get('confidence', 50),
            'learning confidence',
        )
        if not Decimal('0') <= confidence <= Decimal('100'):
            raise ValueError(
                "learning confidence must be between 0 and 100"
            )
        raw_source_ids = learning.get('source_trade_ids')
        if raw_source_ids is None:
            source_trade_ids = None
        else:
            if (
                isinstance(raw_source_ids, (str, bytes))
                or not isinstance(raw_source_ids, Sequence)
            ):
                raise ValueError("source_trade_ids must be a sequence")
            source_trade_ids = sorted({
                self._positive_integer(value, 'source trade id')
                for value in raw_source_ids
            })
            if len(source_trade_ids) > 100:
                raise ValueError(
                    "at most 100 source trades may support one learning"
                )

        with self.Session.begin() as session:
            learning_id = session.execute(text("""
                INSERT INTO learnings (category, content, source_trade_ids, confidence)
                VALUES (:category, :content, :source_trade_ids, :confidence)
                RETURNING id
            """), {
                'category': category,
                'content': content,
                'source_trade_ids': source_trade_ids,
                'confidence': confidence,
            }).scalar_one()
        return int(learning_id)

    @staticmethod
    def _normalize_learning_ids(values: Sequence[int]) -> list[int]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError("learning_ids must be a sequence")
        normalized = []
        for value in values:
            normalized.append(Database._positive_integer(value, 'learning_id'))
        normalized = sorted(set(normalized))
        if not normalized:
            raise ValueError("At least one learning is required")
        if len(normalized) > 100:
            raise ValueError("At most 100 learnings can support one strategy")
        return normalized

    @staticmethod
    def _positive_integer(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field} must be a positive integer")
        return value

    @staticmethod
    def _bounded_identity_text(value: Any, field: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be text")
        normalized = value.strip()
        if not normalized or len(normalized) > maximum:
            raise ValueError(f"{field} has invalid length")
        return normalized

    @staticmethod
    def _operator_identity(value: Any) -> str:
        identity = Database._bounded_identity_text(
            value,
            'operator identity',
            100,
        )
        reserved = ('agent:', 'model:', 'student:')
        if identity.lower().startswith(reserved):
            raise ValueError("An AI or student identity cannot approve strategy")
        return identity

    def record_scheduled_routine_event(
        self,
        *,
        routine_key: str,
        routine_name: str,
        scheduled_at: datetime,
        status: str,
        observed_at: datetime,
        failure_code: str | None = None,
    ) -> int:
        """Append one bounded scheduled-routine outcome."""
        allowed_names = {"morning", "open", "midday", "close", "evening"}
        if routine_name not in allowed_names:
            raise ValueError("routine_name is not supported")
        if routine_key != f"{scheduled_at.date().isoformat()}:{routine_name}":
            raise ValueError("routine_key does not match the schedule")
        for value, field in (
            (scheduled_at, "scheduled_at"),
            (observed_at, "observed_at"),
        ):
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise ValueError(f"{field} must be timezone-aware")
        if status not in {"SUCCEEDED", "FAILED"}:
            raise ValueError("status is not supported")
        if status == "SUCCEEDED" and failure_code is not None:
            raise ValueError("successful routines cannot have a failure code")
        if status == "FAILED" and (
            not isinstance(failure_code, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", failure_code)
        ):
            raise ValueError("failed routines require a bounded failure code")
        with self.Session.begin() as session:
            event_id = session.execute(text("""
                INSERT INTO scheduled_routine_events (
                    routine_key,
                    routine_name,
                    scheduled_at,
                    status,
                    failure_code,
                    observed_at
                )
                VALUES (
                    :routine_key,
                    :routine_name,
                    :scheduled_at,
                    :status,
                    :failure_code,
                    :observed_at
                )
                RETURNING id
            """), {
                "routine_key": routine_key,
                "routine_name": routine_name,
                "scheduled_at": scheduled_at,
                "status": status,
                "failure_code": failure_code,
                "observed_at": observed_at,
            }).scalar_one()
        return int(event_id)

    def get_successful_scheduled_routine_keys(
        self,
        session_date: date,
    ) -> tuple[str, ...]:
        """Return successful routine identities for one Stockholm date."""
        if not isinstance(session_date, date):
            raise ValueError("session_date must be a date")
        rows = self.query(
            """
            SELECT DISTINCT routine_key
            FROM scheduled_routine_events
            WHERE status = 'SUCCEEDED'
              AND scheduled_at >= :day_start
              AND scheduled_at < :next_day
            ORDER BY routine_key
            """,
            {
                "day_start": datetime.combine(
                    session_date,
                    datetime.min.time(),
                    ZoneInfo("Europe/Stockholm"),
                ),
                "next_day": datetime.combine(
                    session_date + timedelta(days=1),
                    datetime.min.time(),
                    ZoneInfo("Europe/Stockholm"),
                ),
            },
        )
        return tuple(row["routine_key"] for row in rows)


    def claim_scheduled_job_run(
        self,
        *,
        job_key: str,
        job_name: str,
        scheduled_at: datetime,
        observed_at: datetime,
        run_kind: str,
        lease_seconds: int = 1200,
    ) -> bool:
        """Claim or safely reclaim one durable recurring-job slot."""
        if not isinstance(job_key, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9:._-]{2,159}",
            job_key,
        ):
            raise ValueError("job_key is invalid")
        if job_name not in {"brain_cycle", "student_study"}:
            raise ValueError("job_name is not supported")
        if run_kind not in {"CURRENT", "RECOVERY", "STALE_SKIP"}:
            raise ValueError("run_kind is not supported")
        for value, field in (
            (scheduled_at, "scheduled_at"),
            (observed_at, "observed_at"),
        ):
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise ValueError(f"{field} must be timezone-aware")
        if observed_at < scheduled_at:
            raise ValueError("observed_at cannot precede scheduled_at")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 60 <= lease_seconds <= 3600
        ):
            raise ValueError("lease_seconds must be between 60 and 3600")

        with self.Session.begin() as session:
            claimed = session.execute(text("""
                INSERT INTO scheduled_job_runs (
                    job_key,
                    job_name,
                    scheduled_at,
                    run_kind,
                    status,
                    claimed_at,
                    lease_expires_at
                )
                VALUES (
                    :job_key,
                    :job_name,
                    :scheduled_at,
                    :run_kind,
                    'CLAIMED',
                    :observed_at,
                    :observed_at
                        + :lease_seconds * INTERVAL '1 second'
                )
                ON CONFLICT (job_key) DO UPDATE
                SET
                    run_kind = EXCLUDED.run_kind,
                    status = 'CLAIMED',
                    attempt_count = scheduled_job_runs.attempt_count + 1,
                    claimed_at = EXCLUDED.claimed_at,
                    lease_expires_at = EXCLUDED.lease_expires_at,
                    completed_at = NULL,
                    failure_code = NULL,
                    updated_at = EXCLUDED.claimed_at
                WHERE scheduled_job_runs.job_name = EXCLUDED.job_name
                  AND scheduled_job_runs.scheduled_at = EXCLUDED.scheduled_at
                  AND (
                      scheduled_job_runs.status = 'FAILED'
                      OR (
                          scheduled_job_runs.status = 'CLAIMED'
                          AND scheduled_job_runs.lease_expires_at
                                <= EXCLUDED.claimed_at
                      )
                  )
                RETURNING job_key
            """), {
                "job_key": job_key,
                "job_name": job_name,
                "scheduled_at": scheduled_at,
                "run_kind": run_kind,
                "observed_at": observed_at,
                "lease_seconds": lease_seconds,
            }).scalar_one_or_none()
        return claimed is not None

    def complete_scheduled_job_run(
        self,
        *,
        job_key: str,
        claimed_at: datetime,
        status: str,
        observed_at: datetime,
        failure_code: str | None = None,
    ) -> bool:
        """Complete the exact lease held by the current worker."""
        if status not in {"SUCCEEDED", "FAILED", "SKIPPED_STALE"}:
            raise ValueError("status is not supported")
        for value, field in (
            (claimed_at, "claimed_at"),
            (observed_at, "observed_at"),
        ):
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise ValueError(f"{field} must be timezone-aware")
        if observed_at < claimed_at:
            raise ValueError("observed_at cannot precede claimed_at")
        if status == "FAILED":
            if not isinstance(failure_code, str) or not re.fullmatch(
                r"[A-Z][A-Z0-9_]{2,63}",
                failure_code,
            ):
                raise ValueError("failed jobs require a failure code")
        elif failure_code is not None:
            raise ValueError("successful or skipped jobs cannot have a failure code")

        with self.Session.begin() as session:
            completed = session.execute(text("""
                UPDATE scheduled_job_runs
                SET
                    status = :status,
                    lease_expires_at = NULL,
                    completed_at = :observed_at,
                    failure_code = :failure_code,
                    updated_at = :observed_at
                WHERE job_key = :job_key
                  AND status = 'CLAIMED'
                  AND claimed_at = :claimed_at
                RETURNING job_key
            """), {
                "job_key": job_key,
                "claimed_at": claimed_at,
                "status": status,
                "observed_at": observed_at,
                "failure_code": failure_code,
            }).scalar_one_or_none()
        return completed is not None

    def get_latest_terminal_scheduled_job_at(
        self,
        job_name: str,
    ) -> datetime | None:
        """Return the latest durable completed or stale-skipped slot."""
        if job_name not in {"brain_cycle", "student_study"}:
            raise ValueError("job_name is not supported")
        rows = self.query(
            """
            SELECT scheduled_at
            FROM scheduled_job_runs
            WHERE job_name = :job_name
              AND status IN ('SUCCEEDED', 'SKIPPED_STALE')
            ORDER BY scheduled_at DESC
            LIMIT 1
            """,
            {"job_name": job_name},
        )
        return rows[0]["scheduled_at"] if rows else None


    def get_scheduled_job_runtime_status(
        self,
        *,
        now: datetime,
    ) -> dict:
        """Return bounded scheduler liveness and expired-lease evidence."""
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("now must be timezone-aware")
        rows = self.query(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE run.status = 'CLAIMED'
                      AND run.lease_expires_at <= :now
                )::int AS expired_claim_count,
                MAX(run.scheduled_at) FILTER (
                    WHERE run.job_name = 'brain_cycle'
                      AND run.status IN ('SUCCEEDED', 'SKIPPED_STALE')
                ) AS latest_brain_at,
                MAX(run.scheduled_at) FILTER (
                    WHERE run.job_name = 'student_study'
                      AND run.status IN ('SUCCEEDED', 'SKIPPED_STALE')
                ) AS latest_study_at,
                COALESCE((
                    SELECT
                        session.status IN ('OPEN', 'HALF_DAY')
                        AND :now >= session.opens_at
                        AND :now < session.closes_at
                    FROM market_sessions session
                    WHERE session.mic = 'XSTO'
                      AND session.session_date = (
                          :now AT TIME ZONE 'Europe/Stockholm'
                      )::DATE
                    LIMIT 1
                ), FALSE) AS session_open
            FROM scheduled_job_runs run
            """,
            {"now": now},
        )
        row = rows[0]

        def age_seconds(value):
            return (
                int((now - value).total_seconds())
                if value is not None
                else None
            )

        return {
            "expired_claim_count": int(row["expired_claim_count"]),
            "session_open": bool(row["session_open"]),
            "latest_brain_at": row["latest_brain_at"],
            "latest_brain_age_seconds": age_seconds(row["latest_brain_at"]),
            "latest_study_at": row["latest_study_at"],
            "latest_study_age_seconds": age_seconds(row["latest_study_at"]),
        }

    def sync_operational_alerts(
        self,
        alerts: Sequence[Any],
        *,
        observed_at: datetime,
    ) -> tuple[dict, ...]:
        """Append only OPEN/RESOLVED transitions for current alert evidence."""
        if (
            not isinstance(observed_at, datetime)
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
        ):
            raise ValueError("observed_at must be timezone-aware")
        if isinstance(alerts, (str, bytes)) or not isinstance(alerts, Sequence):
            raise ValueError("alerts must be a sequence")

        normalized = {}
        for alert in alerts:
            values = {
                "alert_key": getattr(alert, "alert_key", None),
                "code": getattr(alert, "code", None),
                "severity": getattr(alert, "severity", None),
                "summary": getattr(alert, "summary", None),
                "runbook": getattr(alert, "runbook", None),
            }
            if (
                not isinstance(values["alert_key"], str)
                or not re.fullmatch(
                    r"[a-z0-9][a-z0-9:._-]{1,99}",
                    values["alert_key"],
                )
            ):
                raise ValueError("alert_key is invalid")
            if (
                not isinstance(values["code"], str)
                or not re.fullmatch(
                    r"[A-Z][A-Z0-9_]{2,63}",
                    values["code"],
                )
            ):
                raise ValueError("alert code is invalid")
            if values["severity"] not in {"PAGE", "TICKET"}:
                raise ValueError("alert severity is invalid")
            if (
                not isinstance(values["summary"], str)
                or not 1 <= len(values["summary"].strip()) <= 240
            ):
                raise ValueError("alert summary is invalid")
            if values["runbook"] != "docs/operations.md":
                raise ValueError("alert runbook is invalid")
            if values["alert_key"] in normalized:
                raise ValueError("alert keys must be unique")
            normalized[values["alert_key"]] = values

        transitions = []
        with self.Session.begin() as session:
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtext('operational-alert-transitions'))"
                )
            )
            rows = session.execute(text("""
                SELECT DISTINCT ON (alert_key)
                    alert_key, code, severity, state, summary, runbook,
                    observed_at
                FROM operational_alert_events
                ORDER BY alert_key, observed_at DESC, id DESC
            """)).mappings().all()
            latest = {row["alert_key"]: dict(row) for row in rows}
            if rows and observed_at < max(
                row["observed_at"] for row in rows
            ):
                raise ValueError(
                    "observed_at is older than persisted alert state"
                )

            for alert_key in sorted(normalized):
                alert = normalized[alert_key]
                if latest.get(alert_key, {}).get("state") == "OPEN":
                    continue
                transitions.append(
                    self._insert_operational_alert_event(
                        session,
                        alert,
                        state="OPEN",
                        observed_at=observed_at,
                    )
                )

            for alert_key in sorted(latest):
                previous = latest[alert_key]
                if previous["state"] != "OPEN" or alert_key in normalized:
                    continue
                transitions.append(
                    self._insert_operational_alert_event(
                        session,
                        previous,
                        state="RESOLVED",
                        observed_at=observed_at,
                    )
                )
        return tuple(transitions)

    @staticmethod
    def _insert_operational_alert_event(
        session,
        alert: Mapping[str, Any],
        *,
        state: str,
        observed_at: datetime,
    ) -> dict:
        return dict(session.execute(text("""
            INSERT INTO operational_alert_events (
                alert_key, code, severity, state, summary, runbook, observed_at
            )
            VALUES (
                :alert_key, :code, :severity, :state, :summary, :runbook,
                :observed_at
            )
            RETURNING alert_key, code, severity, state, summary, runbook
        """), {
            **alert,
            "state": state,
            "observed_at": observed_at,
        }).mappings().one())
    
    def query(self, sql: str, params: dict = None) -> List[dict]:
        """Execute a query and return results as list of dicts."""
        with self.Session() as session:
            result = session.execute(text(sql), params or {})
            return [dict(row._mapping) for row in result.fetchall()]
    
    def execute(self, sql: str, params: tuple = None):
        """Execute a statement (INSERT, UPDATE, DELETE)."""
        with self.Session() as session:
            # Convert tuple params to dict for SQLAlchemy
            if params:
                # Count placeholders and map to params
                placeholders = sql.count('%s')
                param_dict = {f'p{i}': params[i] for i in range(len(params))}
                # Replace %s with :p0, :p1, etc.
                for i in range(placeholders):
                    sql = sql.replace('%s', f':p{i}', 1)
                session.execute(text(sql), param_dict)
            else:
                session.execute(text(sql))
            session.commit()

    def record_post_close_benchmark_observation(
        self,
        *,
        observed_at: datetime,
    ) -> int | None:
        """Derive one idempotent daily benchmark observation from evidence."""
        if (
            not isinstance(observed_at, datetime)
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
        ):
            raise ValueError("observed_at must be timezone-aware")
        session_date = observed_at.astimezone(
            ZoneInfo("Europe/Stockholm")
        ).date()

        with self.Session.begin() as session:
            context = session.execute(text("""
                SELECT
                    experiment.id AS experiment_id,
                    market_session.opens_at,
                    market_session.closes_at
                FROM paper_benchmark_experiments experiment
                JOIN market_sessions market_session
                  ON market_session.mic = 'XSTO'
                 AND market_session.session_date = :session_date
                 AND market_session.status IN ('OPEN', 'HALF_DAY')
                WHERE experiment.status = 'RUNNING'
                FOR UPDATE OF experiment
            """), {
                "session_date": session_date,
            }).mappings().first()
            if context is None or observed_at < context["closes_at"]:
                return None

            existing_id = session.execute(text("""
                SELECT id
                FROM paper_benchmark_daily_observations
                WHERE experiment_id = :experiment_id
                  AND session_date = :session_date
            """), {
                "experiment_id": context["experiment_id"],
                "session_date": session_date,
            }).scalar()
            if existing_id is not None:
                return int(existing_id)

            observation_id = session.execute(text("""
                WITH target AS (
                    SELECT
                        experiment.id AS experiment_id,
                        experiment.reference_snapshot_id,
                        experiment.quote_provider_contract_id,
                        experiment.benchmark_provider_contract_id,
                        experiment.universe_checksum_sha256,
                        snapshot.instrument_count,
                        quote_contract.provider AS quote_provider,
                        quote_contract.data_type AS quote_data_type,
                        quote_contract.nominal_delay_seconds
                            + quote_contract.max_transport_lag_seconds
                            AS quote_delivery_seconds,
                        benchmark_contract.nominal_delay_seconds
                            + benchmark_contract.max_transport_lag_seconds
                            AS benchmark_delivery_seconds,
                        market_session.opens_at,
                        market_session.closes_at
                    FROM paper_benchmark_experiments experiment
                    JOIN reference_data_snapshots snapshot
                      ON snapshot.id = experiment.reference_snapshot_id
                    JOIN market_data_provider_contracts quote_contract
                      ON quote_contract.id =
                        experiment.quote_provider_contract_id
                    JOIN market_data_provider_contracts benchmark_contract
                      ON benchmark_contract.id =
                        experiment.benchmark_provider_contract_id
                    JOIN market_sessions market_session
                      ON market_session.mic = 'XSTO'
                     AND market_session.session_date = :session_date
                     AND market_session.status IN ('OPEN', 'HALF_DAY')
                    WHERE experiment.id = :experiment_id
                      AND experiment.status = 'RUNNING'
                ),
                latest_quotes AS (
                    SELECT
                        membership.instrument_id,
                        latest_quote.quote_id,
                        latest_quote.received_at,
                        latest_quote.file_checksum_sha256
                    FROM target
                    JOIN reference_snapshot_instruments membership
                      ON membership.snapshot_id =
                        target.reference_snapshot_id
                    LEFT JOIN LATERAL (
                        SELECT
                            quote.id AS quote_id,
                            quote.received_at,
                            source_file.checksum_sha256
                                AS file_checksum_sha256
                        FROM market_quotes quote
                        JOIN market_data_files source_file
                          ON source_file.id = quote.data_file_id
                         AND source_file.provider_contract_id =
                            target.quote_provider_contract_id
                        WHERE quote.instrument_id =
                            membership.instrument_id
                          AND quote.source LIKE
                            target.quote_provider || '%'
                          AND quote.event_time >= target.opens_at
                          AND quote.event_time <= target.closes_at
                          AND quote.received_at <= :observed_at
                          AND EXTRACT(
                              EPOCH FROM (
                                  quote.received_at - quote.event_time
                              )
                          ) <= target.quote_delivery_seconds
                        ORDER BY
                            quote.event_time DESC,
                            quote.id DESC
                        LIMIT 1
                    ) latest_quote ON TRUE
                ),
                quote_summary AS (
                    SELECT
                        COUNT(quote_id)::INTEGER AS received_quote_points,
                        MAX(received_at) AS latest_received_at,
                        STRING_AGG(
                            quote_id::TEXT || ':' ||
                                file_checksum_sha256,
                            ','
                            ORDER BY instrument_id
                        ) FILTER (WHERE quote_id IS NOT NULL)
                            AS quote_evidence
                    FROM latest_quotes
                ),
                final_snapshot AS (
                    SELECT portfolio_snapshot.*
                    FROM target
                    JOIN LATERAL (
                        SELECT history.*
                        FROM portfolio_history history
                        WHERE history.benchmark_experiment_id =
                            target.experiment_id
                          AND history.recorded_at >= target.closes_at
                          AND history.recorded_at <= :observed_at
                        ORDER BY
                            history.recorded_at DESC,
                            history.id DESC
                        LIMIT 1
                    ) portfolio_snapshot ON TRUE
                ),
                nav_range AS (
                    SELECT
                        MIN(history.total_value) AS session_low_nav,
                        MAX(history.total_value) AS session_high_nav
                    FROM target
                    JOIN final_snapshot ON TRUE
                    JOIN portfolio_history history
                      ON history.benchmark_experiment_id =
                        target.experiment_id
                     AND history.recorded_at >= target.opens_at
                     AND history.recorded_at <=
                        final_snapshot.recorded_at
                ),
                benchmark_level AS (
                    SELECT level.*
                    FROM target
                    JOIN LATERAL (
                        SELECT candidate.*
                        FROM market_index_levels candidate
                        WHERE candidate.provider_contract_id =
                            target.benchmark_provider_contract_id
                          AND candidate.symbol = 'OMXSGI'
                          AND candidate.mic = 'XSTO'
                          AND candidate.event_time = target.closes_at
                          AND candidate.received_at <= :observed_at
                          AND EXTRACT(
                              EPOCH FROM (
                                  candidate.received_at
                                  - candidate.event_time
                              )
                          ) <= target.benchmark_delivery_seconds
                        ORDER BY
                            candidate.received_at DESC,
                            candidate.id DESC
                        LIMIT 1
                    ) level ON TRUE
                ),
                daily_costs AS (
                    SELECT
                        COALESCE(SUM(trade.fee_amount), 0) AS fees,
                        COALESCE(SUM(trade.spread_cost), 0)
                            AS spread_cost,
                        COALESCE(SUM(trade.slippage_cost), 0)
                            AS slippage_cost
                    FROM target
                    LEFT JOIN trades trade
                      ON trade.benchmark_experiment_id =
                        target.experiment_id
                     AND (
                         trade.executed_at
                         AT TIME ZONE 'Europe/Stockholm'
                     )::DATE = :session_date
                ),
                evidence AS (
                    SELECT
                        target.*,
                        final_snapshot.id AS portfolio_snapshot_id,
                        final_snapshot.total_value AS net_asset_value,
                        final_snapshot.cash,
                        final_snapshot.positions_value
                            AS gross_exposure,
                        nav_range.session_high_nav,
                        nav_range.session_low_nav,
                        benchmark_level.id AS benchmark_level_id,
                        benchmark_level.level AS benchmark_level,
                        benchmark_level.received_at
                            AS benchmark_received_at,
                        benchmark_level.source_checksum_sha256
                            AS benchmark_checksum_sha256,
                        quote_summary.received_quote_points,
                        quote_summary.latest_received_at,
                        quote_summary.quote_evidence,
                        daily_costs.fees,
                        daily_costs.spread_cost,
                        daily_costs.slippage_cost
                    FROM target
                    JOIN final_snapshot ON TRUE
                    JOIN nav_range ON TRUE
                    JOIN benchmark_level ON TRUE
                    JOIN quote_summary ON TRUE
                    JOIN daily_costs ON TRUE
                )
                INSERT INTO paper_benchmark_daily_observations (
                    experiment_id,
                    session_date,
                    net_asset_value,
                    session_high_nav,
                    session_low_nav,
                    cash,
                    gross_exposure,
                    benchmark_level,
                    fees,
                    spread_cost,
                    slippage_cost,
                    expected_quote_points,
                    received_quote_points,
                    event_cutoff_at,
                    data_available_at,
                    source_checksum_sha256
                )
                SELECT
                    evidence.experiment_id,
                    :session_date,
                    evidence.net_asset_value,
                    evidence.session_high_nav,
                    evidence.session_low_nav,
                    evidence.cash,
                    evidence.gross_exposure,
                    evidence.benchmark_level,
                    evidence.fees,
                    evidence.spread_cost,
                    evidence.slippage_cost,
                    evidence.instrument_count,
                    evidence.received_quote_points,
                    evidence.closes_at,
                    GREATEST(
                        evidence.closes_at,
                        evidence.benchmark_received_at,
                        COALESCE(
                            evidence.latest_received_at,
                            evidence.closes_at
                        )
                    ),
                    ENCODE(
                        SHA256(
                            CONVERT_TO(
                                CONCAT_WS(
                                    '|',
                                    evidence.universe_checksum_sha256,
                                    evidence.portfolio_snapshot_id::TEXT,
                                    evidence.benchmark_level_id::TEXT,
                                    evidence.benchmark_checksum_sha256,
                                    COALESCE(
                                        evidence.quote_evidence,
                                        ''
                                    )
                                ),
                                'UTF8'
                            )
                        ),
                        'hex'
                    )
                FROM evidence
                RETURNING id
            """), {
                "experiment_id": context["experiment_id"],
                "session_date": session_date,
                "observed_at": observed_at,
            }).scalar()
            if observation_id is None:
                raise MarketDataError(
                    "post-close benchmark evidence is incomplete"
                )
            return int(observation_id)

    def save_portfolio_snapshot(
        self,
        *,
        recorded_at: datetime | None = None,
    ):
        """Save a portfolio snapshot with exact provider valuation marks."""
        valuation_at = recorded_at or datetime.now(timezone.utc)
        if (
            not isinstance(valuation_at, datetime)
            or valuation_at.tzinfo is None
            or valuation_at.utcoffset() is None
        ):
            raise ValueError("recorded_at must be timezone-aware")
        with self.Session.begin() as session:
            rows = session.execute(text(f"""
                WITH {_AUTHORIZED_XSTO_PROVIDER_CTE},
                latest_balance AS (
                    SELECT cash
                    FROM balance
                    ORDER BY id DESC
                    LIMIT 1
                )
                SELECT
                    balance.cash,
                    position.ticker,
                    position.shares,
                    mark.quote_id,
                    mark.source_book_state_id,
                    mark.price
                FROM latest_balance balance
                LEFT JOIN portfolio position
                  ON position.shares > 0
                LEFT JOIN companies company
                  ON company.ticker = position.ticker
                LEFT JOIN instruments instrument
                  ON instrument.id = company.instrument_id
                 AND instrument.mic = 'XSTO'
                 AND instrument.status = 'ACTIVE'
                LEFT JOIN authorized_provider provider ON TRUE
                LEFT JOIN LATERAL (
                    SELECT *
                    FROM (
                        SELECT
                            candidate.id AS quote_id,
                            NULL::BIGINT AS source_book_state_id,
                            candidate.last_price AS price
                        FROM market_quotes candidate
                        JOIN market_data_files source_file
                          ON source_file.id = candidate.data_file_id
                         AND source_file.provider_contract_id =
                            provider.contract_id
                        WHERE provider.data_type !=
                                'delayed-pre-trade-equity'
                          AND candidate.instrument_id = instrument.id
                          AND candidate.source LIKE
                                provider.provider || '%'
                          AND candidate.event_time <= :valuation_at
                          AND candidate.received_at <= :valuation_at
                        ORDER BY
                            candidate.event_time DESC,
                            candidate.id DESC
                        LIMIT 1
                    ) last_trade
                    UNION ALL
                    SELECT *
                    FROM (
                        SELECT
                            NULL::BIGINT AS quote_id,
                            state.id AS source_book_state_id,
                            (
                                state.bid_price + state.ask_price
                            ) / 2 AS price
                        FROM pre_trade_batches batch
                        JOIN pre_trade_stream_cursors seal
                          ON seal.batch_id = batch.id
                        JOIN pre_trade_book_states state
                          ON state.batch_id = batch.id
                         AND state.instrument_id = instrument.id
                        WHERE provider.data_type =
                                'delayed-pre-trade-equity'
                          AND batch.provider_contract_id =
                                provider.contract_id
                          AND batch.report_minute <= :valuation_at
                          AND state.received_at <= :valuation_at
                          AND state.bid_price IS NOT NULL
                          AND state.ask_price IS NOT NULL
                          AND state.bid_quantity > 0
                          AND state.ask_quantity > 0
                          AND state.trading_system = 'CLOB'
                          AND state.trading_phase = 'COTR'
                        ORDER BY
                            batch.report_minute DESC,
                            batch.id DESC
                        LIMIT 1
                    ) pre_trade
                    LIMIT 1
                ) mark ON TRUE
                ORDER BY position.ticker
            """), {
                "valuation_at": valuation_at,
            }).mappings().all()
            if not rows:
                raise RuntimeError("Balance row is missing")

            cash = Decimal(rows[0]["cash"])
            positions_value = Decimal("0.00")
            marks = []
            for row in rows:
                if row["ticker"] is None:
                    continue
                evidence_ids = (
                    row["quote_id"],
                    row["source_book_state_id"],
                )
                if (
                    sum(value is not None for value in evidence_ids) != 1
                    or row["price"] is None
                ):
                    raise MarketDataError(
                        f"{row['ticker']} is missing an authorized "
                        "provider valuation mark"
                    )
                shares = Decimal(row["shares"])
                price = Decimal(row["price"])
                market_value = (shares * price).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
                positions_value += market_value
                marks.append({
                    "ticker": row["ticker"],
                    "shares": shares,
                    "quote_id": (
                        int(row["quote_id"])
                        if row["quote_id"] is not None
                        else None
                    ),
                    "source_book_state_id": (
                        int(row["source_book_state_id"])
                        if row["source_book_state_id"] is not None
                        else None
                    ),
                    "price": price,
                    "market_value": market_value,
                })
            total_value = cash + positions_value
            snapshot_id = session.execute(text("""
                INSERT INTO portfolio_history (
                    total_value,
                    cash,
                    positions_value,
                    benchmark_experiment_id,
                    recorded_at
                )
                VALUES (
                    :total_value,
                    :cash,
                    :positions_value,
                    (
                        SELECT id
                        FROM paper_benchmark_experiments
                        WHERE status = 'RUNNING'
                    ),
                    :valuation_at
                )
                RETURNING id
            """), {
                "total_value": total_value,
                "cash": cash,
                "positions_value": positions_value,
                "valuation_at": valuation_at,
            }).scalar_one()
            for mark in marks:
                session.execute(text("""
                    INSERT INTO portfolio_valuation_marks (
                        portfolio_history_id,
                        ticker,
                        quote_id,
                        source_book_state_id,
                        shares,
                        price,
                        market_value
                    )
                    VALUES (
                        :portfolio_history_id,
                        :ticker,
                        :quote_id,
                        :source_book_state_id,
                        :shares,
                        :price,
                        :market_value
                    )
                """), {
                    "portfolio_history_id": snapshot_id,
                    **mark,
                })
            session.execute(text("""
                UPDATE balance
                SET
                    total_value = :total_value,
                    updated_at = :valuation_at
                WHERE id = (
                    SELECT id FROM balance ORDER BY id DESC LIMIT 1
                )
            """), {
                'total_value': total_value,
                'valuation_at': valuation_at,
            })
            logger.info("📸 Portfolio snapshot saved")
