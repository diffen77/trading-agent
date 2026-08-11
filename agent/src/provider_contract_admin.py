"""Strict operator workflow for market-data contracts and acceptance evidence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor

from .data.market_data import MarketDataError
from .data.provider_governance import ProviderContract
from .runtime_secrets import read_runtime_secret


_CONTRACT_FIELDS = frozenset({
    "contract_key",
    "provider",
    "product_name",
    "data_type",
    "mic",
    "delivery_mode",
    "transport",
    "nominal_delay_seconds",
    "max_transport_lag_seconds",
    "non_display_category",
    "reference_symbols_included",
    "terms_url",
    "valid_from",
    "valid_until",
})
_ACCEPTANCE_FIELDS = frozenset({
    "contract_key",
    "reference_snapshot_id",
    "reference_checksum_sha256",
    "validation_valid_until",
    "retention_policy",
    "raw_storage_allowed",
    "derived_storage_allowed",
    "transport_verified",
    "correction_handling_verified",
    "restart_verified",
    "gap_recovery_verified",
    "kill_switch_verified",
    "sessions",
})
_SESSION_FIELDS = frozenset({
    "session_date",
    "expected_instruments",
    "product_covered_instruments",
    "symbol_mapped_instruments",
    "sample_file_count",
    "sample_quote_count",
    "max_observed_delivery_seconds",
    "evidence_checksum_sha256",
})
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,99}$")
_OPERATOR_PATTERN = re.compile(r"^operator:[A-Za-z0-9._-]{1,80}$")
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_JSON_BYTES = 1_048_576
_MAX_TERMS_BYTES = 20 * 1024 * 1024
_STOCKHOLM = ZoneInfo("Europe/Stockholm")


@dataclass(frozen=True)
class ProviderContractProposal:
    contract: ProviderContract
    proposed_by: str


@dataclass(frozen=True)
class ProviderAcceptanceSession:
    session_date: date
    expected_instruments: int
    product_covered_instruments: int
    symbol_mapped_instruments: int
    sample_file_count: int
    sample_quote_count: int
    max_observed_delivery_seconds: int
    evidence_checksum_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.session_date, date):
            raise ValueError("acceptance session_date must be a date")
        expected = _positive_integer(
            self.expected_instruments,
            "expected_instruments",
        )
        covered = _positive_integer(
            self.product_covered_instruments,
            "product_covered_instruments",
        )
        mapped = _positive_integer(
            self.symbol_mapped_instruments,
            "symbol_mapped_instruments",
        )
        if covered != expected:
            raise ValueError("acceptance product coverage must be complete")
        if mapped != expected:
            raise ValueError("acceptance symbol mapping must be complete")
        _positive_integer(self.sample_file_count, "sample_file_count")
        _positive_integer(self.sample_quote_count, "sample_quote_count")
        delivery = _positive_integer(
            self.max_observed_delivery_seconds,
            "max_observed_delivery_seconds",
            maximum=86_400,
        )
        checksum = _checksum(
            self.evidence_checksum_sha256,
            "session evidence checksum",
        )
        object.__setattr__(self, "expected_instruments", expected)
        object.__setattr__(
            self,
            "product_covered_instruments",
            covered,
        )
        object.__setattr__(self, "symbol_mapped_instruments", mapped)
        object.__setattr__(
            self,
            "max_observed_delivery_seconds",
            delivery,
        )
        object.__setattr__(
            self,
            "evidence_checksum_sha256",
            checksum,
        )


@dataclass(frozen=True)
class ProviderAcceptanceApproval:
    contract_key: str
    reference_snapshot_id: int | None
    reference_checksum_sha256: str | None
    validation_valid_until: datetime
    retention_policy: str
    raw_storage_allowed: bool
    derived_storage_allowed: bool
    transport_verified: bool
    correction_handling_verified: bool
    restart_verified: bool
    gap_recovery_verified: bool
    kill_switch_verified: bool
    sessions: tuple[ProviderAcceptanceSession, ...]
    terms_checksum_sha256: str
    acceptance_checksum_sha256: str
    evidence_checksum_sha256: str
    validated_by: str

    def __post_init__(self) -> None:
        key = _contract_key(self.contract_key)
        snapshot_id = self.reference_snapshot_id
        reference_checksum = self.reference_checksum_sha256
        if (snapshot_id is None) != (reference_checksum is None):
            raise ValueError(
                "reference snapshot id and checksum must be paired"
            )
        if snapshot_id is not None:
            snapshot_id = _positive_integer(
                snapshot_id,
                "reference_snapshot_id",
            )
            reference_checksum = _checksum(
                reference_checksum,
                "reference checksum",
            )
        valid_until = _aware_datetime(
            self.validation_valid_until,
            "validation_valid_until",
        )
        retention = _bounded_text(
            self.retention_policy,
            "retention_policy",
            maximum=200,
        )
        confirmations = {
            "raw storage": self.raw_storage_allowed,
            "derived storage": self.derived_storage_allowed,
            "transport": self.transport_verified,
            "correction handling": self.correction_handling_verified,
            "restart": self.restart_verified,
            "gap recovery": self.gap_recovery_verified,
            "kill switch": self.kill_switch_verified,
        }
        if any(value is not True for value in confirmations.values()):
            missing = ", ".join(
                label
                for label, accepted in confirmations.items()
                if accepted is not True
            )
            raise ValueError(
                f"acceptance storage and operational checks are incomplete: "
                f"{missing}"
            )
        if not isinstance(self.sessions, tuple) or len(self.sessions) < 5:
            raise ValueError(
                "acceptance requires at least five sessions"
            )
        if not all(
            isinstance(item, ProviderAcceptanceSession)
            for item in self.sessions
        ):
            raise ValueError("acceptance sessions are invalid")
        dates = tuple(item.session_date for item in self.sessions)
        if dates != tuple(sorted(set(dates))):
            raise ValueError(
                "acceptance session dates must be unique and ordered"
            )
        expected = self.sessions[0].expected_instruments
        if any(
            item.expected_instruments != expected
            for item in self.sessions
        ):
            raise ValueError(
                "acceptance sessions must use one frozen universe"
            )
        object.__setattr__(self, "contract_key", key)
        object.__setattr__(self, "reference_snapshot_id", snapshot_id)
        object.__setattr__(
            self,
            "reference_checksum_sha256",
            reference_checksum,
        )
        object.__setattr__(
            self,
            "validation_valid_until",
            valid_until,
        )
        object.__setattr__(self, "retention_policy", retention)
        object.__setattr__(
            self,
            "terms_checksum_sha256",
            _checksum(self.terms_checksum_sha256, "terms checksum"),
        )
        object.__setattr__(
            self,
            "acceptance_checksum_sha256",
            _checksum(
                self.acceptance_checksum_sha256,
                "acceptance checksum",
            ),
        )
        object.__setattr__(
            self,
            "evidence_checksum_sha256",
            _checksum(
                self.evidence_checksum_sha256,
                "combined evidence checksum",
            ),
        )
        object.__setattr__(
            self,
            "validated_by",
            _operator_identity(self.validated_by),
        )

    @property
    def expected_instruments(self) -> int:
        return self.sessions[0].expected_instruments

    @property
    def sample_file_count(self) -> int:
        return sum(item.sample_file_count for item in self.sessions)

    @property
    def sample_quote_count(self) -> int:
        return sum(item.sample_quote_count for item in self.sessions)

    @property
    def max_observed_delivery_seconds(self) -> int:
        return max(
            item.max_observed_delivery_seconds
            for item in self.sessions
        )


def proposal_from_file(
    *,
    contract_json_path: str,
    proposed_by: str,
) -> ProviderContractProposal:
    operator = _operator_identity(proposed_by)
    manifest = _load_json_object(
        contract_json_path,
        label="contract JSON",
    )
    if set(manifest) != _CONTRACT_FIELDS:
        raise ValueError("provider contract manifest fields do not match")
    try:
        contract = ProviderContract(
            contract_key=manifest["contract_key"],
            provider=manifest["provider"],
            product_name=manifest["product_name"],
            data_type=manifest["data_type"],
            mic=manifest["mic"],
            delivery_mode=manifest["delivery_mode"],
            transport=manifest["transport"],
            nominal_delay_seconds=manifest["nominal_delay_seconds"],
            max_transport_lag_seconds=manifest[
                "max_transport_lag_seconds"
            ],
            usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
            non_display_category=manifest["non_display_category"],
            external_distribution=False,
            reference_symbols_included=manifest[
                "reference_symbols_included"
            ],
            terms_url=manifest["terms_url"],
            status="DRAFT",
            valid_from=date.fromisoformat(manifest["valid_from"]),
            valid_until=date.fromisoformat(manifest["valid_until"]),
            legal_reviewed_at=None,
            transport_verified_at=None,
            governance_evidence_status="PENDING",
            terms_checksum_sha256=None,
            raw_storage_allowed=False,
            derived_storage_allowed=False,
            retention_policy=None,
            proposed_by=operator,
            reviewed_by=None,
            revoked_at=None,
            revoked_by=None,
            revocation_reason=None,
        )
    except (AttributeError, TypeError, ValueError, MarketDataError) as exc:
        raise ValueError("provider contract manifest is invalid") from exc
    return ProviderContractProposal(
        contract=contract,
        proposed_by=operator,
    )


def acceptance_from_files(
    *,
    acceptance_json_path: str,
    terms_file_path: str,
    validated_by: str,
) -> ProviderAcceptanceApproval:
    acceptance_bytes = _read_regular_file(
        acceptance_json_path,
        maximum=_MAX_JSON_BYTES,
        label="acceptance JSON",
    )
    manifest = _decode_json_object(
        acceptance_bytes,
        label="acceptance JSON",
    )
    if set(manifest) != _ACCEPTANCE_FIELDS:
        raise ValueError("provider acceptance manifest fields do not match")
    raw_sessions = manifest["sessions"]
    if not isinstance(raw_sessions, list):
        raise ValueError("provider acceptance sessions must be a list")
    sessions = []
    try:
        for raw_session in raw_sessions:
            if (
                not isinstance(raw_session, dict)
                or set(raw_session) != _SESSION_FIELDS
            ):
                raise ValueError(
                    "provider acceptance session fields do not match"
                )
            sessions.append(
                ProviderAcceptanceSession(
                    session_date=date.fromisoformat(
                        raw_session["session_date"]
                    ),
                    expected_instruments=raw_session[
                        "expected_instruments"
                    ],
                    product_covered_instruments=raw_session[
                        "product_covered_instruments"
                    ],
                    symbol_mapped_instruments=raw_session[
                        "symbol_mapped_instruments"
                    ],
                    sample_file_count=raw_session[
                        "sample_file_count"
                    ],
                    sample_quote_count=raw_session[
                        "sample_quote_count"
                    ],
                    max_observed_delivery_seconds=raw_session[
                        "max_observed_delivery_seconds"
                    ],
                    evidence_checksum_sha256=raw_session[
                        "evidence_checksum_sha256"
                    ],
                )
            )
        valid_until = _parse_datetime(
            manifest["validation_valid_until"],
            "validation_valid_until",
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            f"provider acceptance manifest is invalid: {exc}"
        ) from exc
    terms_bytes = _read_regular_file(
        terms_file_path,
        maximum=_MAX_TERMS_BYTES,
        label="terms file",
    )
    terms_checksum = hashlib.sha256(terms_bytes).hexdigest()
    acceptance_checksum = hashlib.sha256(acceptance_bytes).hexdigest()
    operator = _operator_identity(validated_by)
    combined = _canonical_checksum({
        "schema_version": 1,
        "contract_key": manifest["contract_key"],
        "terms_checksum_sha256": terms_checksum,
        "acceptance_checksum_sha256": acceptance_checksum,
        "validated_by": operator,
    })
    return ProviderAcceptanceApproval(
        contract_key=manifest["contract_key"],
        reference_snapshot_id=manifest["reference_snapshot_id"],
        reference_checksum_sha256=manifest[
            "reference_checksum_sha256"
        ],
        validation_valid_until=valid_until,
        retention_policy=manifest["retention_policy"],
        raw_storage_allowed=manifest["raw_storage_allowed"],
        derived_storage_allowed=manifest["derived_storage_allowed"],
        transport_verified=manifest["transport_verified"],
        correction_handling_verified=manifest[
            "correction_handling_verified"
        ],
        restart_verified=manifest["restart_verified"],
        gap_recovery_verified=manifest["gap_recovery_verified"],
        kill_switch_verified=manifest["kill_switch_verified"],
        sessions=tuple(sessions),
        terms_checksum_sha256=terms_checksum,
        acceptance_checksum_sha256=acceptance_checksum,
        evidence_checksum_sha256=combined,
        validated_by=operator,
    )


class ProviderContractAdmin:
    """Create, approve, inspect, and revoke provider contracts."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_url = database_url or read_runtime_secret(
            "DATABASE_URL",
            required=True,
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create_draft(
        self,
        proposal: ProviderContractProposal,
    ) -> dict[str, Any]:
        if not isinstance(proposal, ProviderContractProposal):
            raise ValueError("proposal must be ProviderContractProposal")
        contract = proposal.contract
        with self._connect() as connection:
            with connection.cursor(
                cursor_factory=RealDictCursor,
            ) as cursor:
                cursor.execute(
                    """
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
                        governance_evidence_status,
                        proposed_by
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, 'DRAFT', %s, %s, 'PENDING',
                        %s
                    )
                    ON CONFLICT (contract_key) DO NOTHING
                    RETURNING id, contract_key, status, created_at
                    """,
                    (
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
                        contract.valid_from,
                        contract.valid_until,
                        proposal.proposed_by,
                    ),
                )
                row = cursor.fetchone()
                if row is not None:
                    result = dict(row)
                    result["inserted"] = True
                    result["proposed_by"] = proposal.proposed_by
                    return result
                existing = self._contract_row(
                    cursor,
                    contract.contract_key,
                )
                if (
                    existing is None
                    or self._provider_contract(existing) != contract
                ):
                    raise ValueError(
                        "provider contract_key already has different evidence"
                    )
                return {
                    "id": int(existing["id"]),
                    "contract_key": existing["contract_key"],
                    "status": existing["status"],
                    "created_at": existing["created_at"],
                    "inserted": False,
                    "proposed_by": proposal.proposed_by,
                }

    def validate_contract(
        self,
        approval: ProviderAcceptanceApproval,
    ) -> dict[str, Any]:
        if not isinstance(approval, ProviderAcceptanceApproval):
            raise ValueError(
                "approval must be ProviderAcceptanceApproval"
            )
        reviewed_at = _aware_datetime(self._clock(), "clock")
        with self._connect() as connection:
            with connection.cursor(
                cursor_factory=RealDictCursor,
            ) as cursor:
                existing = self._contract_row(
                    cursor,
                    approval.contract_key,
                    for_update=True,
                )
                if existing is None:
                    raise ValueError("provider contract draft does not exist")
                contract = self._provider_contract(existing)
                existing_validation = self._validation_by_checksum(
                    cursor,
                    int(existing["id"]),
                    approval.evidence_checksum_sha256,
                )
                if (
                    contract.status == "VALIDATED"
                    and existing_validation is not None
                ):
                    result = dict(existing_validation)
                    result["inserted"] = False
                    return result
                if contract.status != "DRAFT":
                    raise ValueError(
                        "only a draft provider contract can be validated"
                    )
                self._validate_acceptance(
                    cursor,
                    contract=contract,
                    approval=approval,
                    reviewed_at=reviewed_at,
                )
                cursor.execute(
                    """
                    UPDATE market_data_provider_contracts
                    SET
                        status = 'VALIDATED',
                        governance_evidence_status = 'VERIFIED',
                        terms_checksum_sha256 = %s,
                        raw_storage_allowed = TRUE,
                        derived_storage_allowed = TRUE,
                        retention_policy = %s,
                        legal_reviewed_at = %s,
                        transport_verified_at = %s,
                        reviewed_by = %s,
                        updated_at = %s
                    WHERE id = %s
                      AND status = 'DRAFT'
                    RETURNING id
                    """,
                    (
                        approval.terms_checksum_sha256,
                        approval.retention_policy,
                        reviewed_at,
                        reviewed_at,
                        approval.validated_by,
                        reviewed_at,
                        int(existing["id"]),
                    ),
                )
                if cursor.fetchone() is None:
                    raise ValueError(
                        "provider contract changed during validation"
                    )
                notes = self._validation_notes(approval)
                cursor.execute(
                    """
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
                        acceptance_checksum_sha256
                    )
                    VALUES (
                        %s, 'PASSED', %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, 'VERIFIED', %s, %s, %s,
                        %s, %s, %s
                    )
                    RETURNING
                        id,
                        contract_id,
                        status,
                        validated_at,
                        valid_until,
                        evidence_checksum_sha256,
                        acceptance_session_count,
                        first_session_date,
                        last_session_date
                    """,
                    (
                        int(existing["id"]),
                        reviewed_at,
                        approval.validation_valid_until,
                        approval.expected_instruments,
                        approval.expected_instruments,
                        approval.expected_instruments,
                        approval.sample_file_count,
                        approval.sample_quote_count,
                        approval.max_observed_delivery_seconds,
                        approval.evidence_checksum_sha256,
                        approval.validated_by,
                        notes,
                        approval.reference_snapshot_id,
                        approval.reference_checksum_sha256,
                        len(approval.sessions),
                        approval.sessions[0].session_date,
                        approval.sessions[-1].session_date,
                        approval.acceptance_checksum_sha256,
                    ),
                )
                result = dict(cursor.fetchone())
                result["inserted"] = True
                return result

    def list_contracts(
        self,
        contract_key: str | None = None,
    ) -> list[dict[str, Any]]:
        parameters: tuple[Any, ...] = ()
        condition = ""
        if contract_key is not None:
            condition = "WHERE contract.contract_key = %s"
            parameters = (_contract_key(contract_key),)
        with self._connect() as connection:
            with connection.cursor(
                cursor_factory=RealDictCursor,
            ) as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        contract.id,
                        contract.contract_key,
                        contract.provider,
                        contract.product_name,
                        contract.data_type,
                        contract.mic,
                        contract.delivery_mode,
                        contract.transport,
                        contract.status,
                        contract.governance_evidence_status,
                        contract.valid_from,
                        contract.valid_until,
                        contract.terms_url,
                        contract.terms_checksum_sha256,
                        contract.raw_storage_allowed,
                        contract.derived_storage_allowed,
                        contract.retention_policy,
                        contract.proposed_by,
                        contract.reviewed_by,
                        contract.revoked_at,
                        contract.revoked_by,
                        contract.revocation_reason,
                        validation.id AS latest_validation_id,
                        validation.status AS latest_validation_status,
                        validation.acceptance_session_count,
                        validation.valid_until AS validation_valid_until,
                        validation.evidence_checksum_sha256
                            AS validation_evidence_checksum_sha256
                    FROM market_data_provider_contracts contract
                    LEFT JOIN LATERAL (
                        SELECT candidate.*
                        FROM market_data_provider_validations candidate
                        WHERE candidate.contract_id = contract.id
                        ORDER BY
                            candidate.validated_at DESC,
                            candidate.id DESC
                        LIMIT 1
                    ) validation ON TRUE
                    {condition}
                    ORDER BY contract.id DESC
                    LIMIT 100
                    """,
                    parameters,
                )
                return [dict(row) for row in cursor.fetchall()]

    def revoke_contract(
        self,
        contract_key: str,
        *,
        revoked_by: str,
        reason: str,
    ) -> dict[str, Any]:
        key = _contract_key(contract_key)
        operator = _operator_identity(revoked_by)
        normalized_reason = _bounded_text(
            reason,
            "reason",
            maximum=500,
        )
        revoked_at = _aware_datetime(self._clock(), "clock")
        with self._connect() as connection:
            with connection.cursor(
                cursor_factory=RealDictCursor,
            ) as cursor:
                cursor.execute(
                    """
                    UPDATE market_data_provider_contracts
                    SET
                        status = 'REVOKED',
                        revoked_at = %s,
                        revoked_by = %s,
                        revocation_reason = %s,
                        updated_at = %s
                    WHERE contract_key = %s
                      AND status = 'VALIDATED'
                    RETURNING
                        id,
                        contract_key,
                        status,
                        revoked_at,
                        revoked_by,
                        revocation_reason
                    """,
                    (
                        revoked_at,
                        operator,
                        normalized_reason,
                        revoked_at,
                        key,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(
                        "validated provider contract does not exist"
                    )
                return dict(row)

    def _validate_acceptance(
        self,
        cursor,
        *,
        contract: ProviderContract,
        approval: ProviderAcceptanceApproval,
        reviewed_at: datetime,
    ) -> None:
        if contract.contract_key != approval.contract_key:
            raise ValueError(
                "acceptance belongs to a different provider contract"
            )
        if (
            contract.usage_scope != "INTERNAL_ANALYSIS_AND_PAPER"
            or contract.external_distribution
        ):
            raise ValueError(
                "provider contract is outside internal paper use"
            )
        if (
            reviewed_at.date() < contract.valid_from
            or reviewed_at.date() > contract.valid_until
        ):
            raise ValueError("provider contract is not currently valid")
        if approval.validation_valid_until <= reviewed_at:
            raise ValueError("provider validation expiry is not in the future")
        if (
            approval.validation_valid_until.astimezone(
                _STOCKHOLM
            ).date()
            > contract.valid_until
        ):
            raise ValueError(
                "provider validation outlives the contract"
            )
        maximum_delivery = (
            contract.nominal_delay_seconds
            + contract.max_transport_lag_seconds
        )
        for session in approval.sessions:
            if not (
                contract.nominal_delay_seconds
                <= session.max_observed_delivery_seconds
                <= maximum_delivery
            ):
                raise ValueError(
                    "acceptance delivery is outside contract bounds"
                )
        if contract.data_type == "delayed-index-level":
            if (
                approval.reference_snapshot_id is not None
                or approval.reference_checksum_sha256 is not None
                or approval.expected_instruments != 1
            ):
                raise ValueError(
                    "index acceptance must cover one index without "
                    "an equity snapshot"
                )
        else:
            if (
                approval.reference_snapshot_id is None
                or approval.reference_checksum_sha256 is None
            ):
                raise ValueError(
                    "equity acceptance requires a frozen reference snapshot"
                )
            cursor.execute(
                """
                SELECT
                    snapshot.id,
                    snapshot.mic,
                    snapshot.universe_checksum_sha256,
                    snapshot.instrument_count,
                    COUNT(membership.instrument_id)::INTEGER
                        AS membership_count,
                    COUNT(membership.instrument_id) FILTER (
                        WHERE membership.frozen_evidence_status = 'VERIFIED'
                    )::INTEGER AS verified_count
                FROM reference_data_snapshots snapshot
                LEFT JOIN reference_snapshot_instruments membership
                  ON membership.snapshot_id = snapshot.id
                WHERE snapshot.mic = %s
                GROUP BY snapshot.id
                ORDER BY snapshot.snapshot_date DESC, snapshot.id DESC
                LIMIT 1
                """,
                (contract.mic,),
            )
            snapshot = cursor.fetchone()
            if (
                snapshot is None
                or int(snapshot["id"]) != approval.reference_snapshot_id
                or snapshot["universe_checksum_sha256"].strip()
                    != approval.reference_checksum_sha256
                or int(snapshot["instrument_count"])
                    != approval.expected_instruments
                or int(snapshot["membership_count"])
                    != approval.expected_instruments
                or int(snapshot["verified_count"])
                    != approval.expected_instruments
            ):
                raise ValueError(
                    "acceptance does not match latest frozen "
                    "reference snapshot"
                )
        first_date = approval.sessions[0].session_date
        last_date = approval.sessions[-1].session_date
        cursor.execute(
            """
            SELECT session_date
            FROM market_sessions
            WHERE mic = %s
              AND status IN ('OPEN', 'HALF_DAY')
              AND session_date BETWEEN %s AND %s
            ORDER BY session_date
            """,
            (contract.mic, first_date, last_date),
        )
        official_dates = tuple(
            row["session_date"] for row in cursor.fetchall()
        )
        acceptance_dates = tuple(
            item.session_date for item in approval.sessions
        )
        if official_dates != acceptance_dates:
            raise ValueError(
                "acceptance sessions are not consecutive official sessions"
            )
        if last_date > reviewed_at.astimezone(_STOCKHOLM).date():
            raise ValueError("acceptance session cannot be in the future")

    @staticmethod
    def _contract_row(cursor, contract_key: str, *, for_update=False):
        cursor.execute(
            f"""
            SELECT *
            FROM market_data_provider_contracts
            WHERE contract_key = %s
            {"FOR UPDATE" if for_update else ""}
            """,
            (contract_key,),
        )
        return cursor.fetchone()

    @staticmethod
    def _provider_contract(row) -> ProviderContract:
        return ProviderContract(
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
            status=row["status"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            legal_reviewed_at=row["legal_reviewed_at"],
            transport_verified_at=row["transport_verified_at"],
            governance_evidence_status=row[
                "governance_evidence_status"
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

    @staticmethod
    def _validation_by_checksum(cursor, contract_id: int, checksum: str):
        cursor.execute(
            """
            SELECT
                id,
                contract_id,
                status,
                validated_at,
                valid_until,
                evidence_checksum_sha256,
                acceptance_session_count,
                first_session_date,
                last_session_date
            FROM market_data_provider_validations
            WHERE contract_id = %s
              AND evidence_checksum_sha256 = %s
            """,
            (contract_id, checksum),
        )
        return cursor.fetchone()

    @staticmethod
    def _validation_notes(
        approval: ProviderAcceptanceApproval,
    ) -> str:
        notes = (
            f"terms_sha256={approval.terms_checksum_sha256};"
            f"acceptance_sha256={approval.acceptance_checksum_sha256};"
            f"sessions={len(approval.sessions)};"
            f"retention={approval.retention_policy}"
        )
        if len(notes) > 500:
            raise ValueError("provider validation notes exceed storage limit")
        return notes

    def _connect(self):
        return psycopg2.connect(self.database_url)


def _load_json_object(path: str, *, label: str) -> dict[str, Any]:
    return _decode_json_object(
        _read_regular_file(
            path,
            maximum=_MAX_JSON_BYTES,
            label=label,
        ),
        label=label,
    )


def _decode_json_object(content: bytes, *, label: str) -> dict[str, Any]:
    def strict_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must contain valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one object")
    return value


def _read_regular_file(
    value: str,
    *,
    maximum: int,
    label: str,
) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} path is required")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    if path.is_symlink():
        raise ValueError(f"{label} path cannot be a symlink")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError(f"{label} is unavailable") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if not 1 <= metadata.st_size <= maximum:
            raise ValueError(f"{label} size is invalid")
        payload = os.read(descriptor, maximum + 1)
        if len(payload) != metadata.st_size or len(payload) > maximum:
            raise ValueError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def _parse_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} is invalid") from exc
    return _aware_datetime(parsed, field)


def _contract_key(value: Any) -> str:
    if not isinstance(value, str) or not _KEY_PATTERN.fullmatch(value):
        raise ValueError("contract_key has invalid format")
    return value


def _operator_identity(value: Any) -> str:
    if not isinstance(value, str) or not _OPERATOR_PATTERN.fullmatch(value):
        raise ValueError("operator identity must start with operator:")
    return value


def _checksum(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = value.lower()
    if not _CHECKSUM_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be lowercase SHA-256")
    return normalized


def _positive_integer(
    value: Any,
    field: str,
    *,
    maximum: int = 1_000_000_000,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ValueError(
            f"{field} must be an integer between 1 and {maximum}"
        )
    return value


def _bounded_text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(
            f"{field} must contain between 1 and {maximum} characters"
        )
    return normalized


def _aware_datetime(value: Any, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _canonical_checksum(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage market-data provider contracts.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status")
    status.add_argument("--contract-key")

    draft = commands.add_parser("draft")
    draft.add_argument("--contract-json", required=True)
    draft.add_argument("--operator", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--acceptance-json", required=True)
    validate.add_argument("--terms-file", required=True)
    validate.add_argument("--operator", required=True)
    for flag in (
        "confirm-internal-paper-use",
        "confirm-no-external-distribution",
        "confirm-raw-storage",
        "confirm-derived-storage",
        "confirm-transport-tested",
        "confirm-five-consecutive-sessions",
        "confirm-restart-and-gap-recovery",
        "confirm-kill-switch",
    ):
        validate.add_argument(
            f"--{flag}",
            action="store_true",
            required=True,
        )

    revoke = commands.add_parser("revoke")
    revoke.add_argument("contract_key")
    revoke.add_argument("--operator", required=True)
    revoke.add_argument("--reason", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        admin = ProviderContractAdmin()
        if args.command == "status":
            result: Any = admin.list_contracts(args.contract_key)
        elif args.command == "draft":
            proposal = proposal_from_file(
                contract_json_path=args.contract_json,
                proposed_by=args.operator,
            )
            result = admin.create_draft(proposal)
        elif args.command == "validate":
            approval = acceptance_from_files(
                acceptance_json_path=args.acceptance_json,
                terms_file_path=args.terms_file,
                validated_by=args.operator,
            )
            result = admin.validate_contract(approval)
        elif args.command == "revoke":
            result = admin.revoke_contract(
                args.contract_key,
                revoked_by=args.operator,
                reason=args.reason,
            )
        else:
            raise RuntimeError("unsupported command")
    except (MarketDataError, ValueError) as exc:
        print(f"provider contract admin error: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "provider contract admin error: operation failed",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
