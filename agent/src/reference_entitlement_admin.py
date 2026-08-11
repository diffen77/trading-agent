"""Operator CLI for validated Nasdaq reference-data entitlements."""

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

import psycopg2
from psycopg2.extras import RealDictCursor

from .runtime_secrets import read_runtime_secret
from .data.market_data import MarketDataError
from .data.provider_governance import (
    ReferenceDataEntitlement,
    assert_reference_entitlement_ready,
)


_CONTRACT_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,99}$")
_OPERATOR_PATTERN = re.compile(r"^operator:[A-Za-z0-9._-]{1,80}$")
_MANIFEST_FIELDS = frozenset({
    "contract_key",
    "provider",
    "product_name",
    "mic",
    "retention_policy",
    "terms_url",
    "valid_from",
    "valid_until",
    "host_key_algorithm",
    "host_key_fingerprint_sha256",
})
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_TERMS_BYTES = 20_000_000


@dataclass(frozen=True)
class ReferenceEntitlementApproval:
    """Validated non-secret approval input before database insertion."""

    contract_key: str
    provider: str
    product_name: str
    mic: str
    retention_policy: str
    terms_url: str
    terms_checksum_sha256: str
    valid_from: date
    valid_until: date
    host_key_algorithm: str
    host_key_fingerprint_sha256: str
    reviewed_by: str
    reviewed_at: datetime
    transport: str = "SFTP"
    usage_scope: str = "INTERNAL_ANALYSIS_AND_PAPER"
    external_distribution: bool = False
    raw_storage_allowed: bool = True
    derived_storage_allowed: bool = True

    def __post_init__(self) -> None:
        operator = _operator_identity(self.reviewed_by)
        reviewed_at = _aware_datetime(
            self.reviewed_at,
            "reviewed_at",
        )
        entitlement = self.as_entitlement(
            entitlement_id=1,
            reviewed_by=operator,
            reviewed_at=reviewed_at,
        )
        assert_reference_entitlement_ready(
            entitlement,
            now=reviewed_at,
            contract_key=entitlement.contract_key,
            provider=entitlement.provider,
            mic=entitlement.mic,
            transport="SFTP",
            usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
        )
        object.__setattr__(self, "contract_key", entitlement.contract_key)
        object.__setattr__(self, "provider", entitlement.provider)
        object.__setattr__(self, "product_name", entitlement.product_name)
        object.__setattr__(self, "mic", entitlement.mic)
        object.__setattr__(
            self,
            "retention_policy",
            entitlement.retention_policy,
        )
        object.__setattr__(self, "terms_url", entitlement.terms_url)
        object.__setattr__(
            self,
            "terms_checksum_sha256",
            entitlement.terms_checksum_sha256,
        )
        object.__setattr__(self, "reviewed_by", operator)
        object.__setattr__(self, "reviewed_at", reviewed_at)

    def as_entitlement(
        self,
        *,
        entitlement_id: int,
        reviewed_by: str | None = None,
        reviewed_at: datetime | None = None,
    ) -> ReferenceDataEntitlement:
        timestamp = reviewed_at or self.reviewed_at
        return ReferenceDataEntitlement(
            entitlement_id=entitlement_id,
            contract_key=self.contract_key,
            provider=self.provider,
            product_name=self.product_name,
            mic=self.mic,
            transport=self.transport,
            usage_scope=self.usage_scope,
            external_distribution=self.external_distribution,
            raw_storage_allowed=self.raw_storage_allowed,
            derived_storage_allowed=self.derived_storage_allowed,
            retention_policy=self.retention_policy,
            terms_url=self.terms_url,
            terms_checksum_sha256=self.terms_checksum_sha256,
            status="VALIDATED",
            valid_from=self.valid_from,
            valid_until=self.valid_until,
            legal_reviewed_at=timestamp,
            storage_reviewed_at=timestamp,
            entitlement_verified_at=timestamp,
            host_key_algorithm=self.host_key_algorithm,
            host_key_fingerprint_sha256=(
                self.host_key_fingerprint_sha256
            ),
            reviewed_by=reviewed_by or self.reviewed_by,
        )


class ReferenceEntitlementAdmin:
    """Create, inspect, and revoke entitlement evidence atomically."""

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

    def validate_reference_entitlement(
        self,
        approval: ReferenceEntitlementApproval,
    ) -> dict[str, Any]:
        if not isinstance(approval, ReferenceEntitlementApproval):
            raise ValueError(
                "approval must be ReferenceEntitlementApproval"
            )
        now = self._now()
        evidence = approval.as_entitlement(entitlement_id=1)
        assert_reference_entitlement_ready(
            evidence,
            now=now,
            contract_key=evidence.contract_key,
            provider=evidence.provider,
            mic=evidence.mic,
            transport="SFTP",
            usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
        )
        try:
            with self._connect() as connection:
                with connection.cursor(
                    cursor_factory=RealDictCursor,
                ) as cursor:
                    cursor.execute(
                        """
                        INSERT INTO reference_data_entitlements (
                            contract_key,
                            provider,
                            product_name,
                            mic,
                            transport,
                            usage_scope,
                            external_distribution,
                            raw_storage_allowed,
                            derived_storage_allowed,
                            retention_policy,
                            terms_url,
                            terms_checksum_sha256,
                            status,
                            valid_from,
                            valid_until,
                            legal_reviewed_at,
                            storage_reviewed_at,
                            entitlement_verified_at,
                            host_key_algorithm,
                            host_key_fingerprint_sha256,
                            reviewed_by
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, 'VALIDATED', %s, %s, %s, %s, %s,
                            %s, %s, %s
                        )
                        RETURNING
                            id,
                            contract_key,
                            provider,
                            product_name,
                            mic,
                            status,
                            valid_from,
                            valid_until,
                            reviewed_by,
                            created_at
                        """,
                        (
                            evidence.contract_key,
                            evidence.provider,
                            evidence.product_name,
                            evidence.mic,
                            evidence.transport,
                            evidence.usage_scope,
                            evidence.external_distribution,
                            evidence.raw_storage_allowed,
                            evidence.derived_storage_allowed,
                            evidence.retention_policy,
                            evidence.terms_url,
                            evidence.terms_checksum_sha256,
                            evidence.valid_from,
                            evidence.valid_until,
                            evidence.legal_reviewed_at,
                            evidence.storage_reviewed_at,
                            evidence.entitlement_verified_at,
                            evidence.host_key_algorithm,
                            evidence.host_key_fingerprint_sha256,
                            evidence.reviewed_by,
                        ),
                    )
                    return dict(cursor.fetchone())
        except psycopg2.errors.UniqueViolation:
            raise ValueError(
                "reference entitlement contract_key already exists"
            ) from None

    def list_reference_entitlements(
        self,
        contract_key: str | None = None,
    ) -> list[dict[str, Any]]:
        parameters: tuple[Any, ...] = ()
        condition = ""
        if contract_key is not None:
            condition = "WHERE contract_key = %s"
            parameters = (_contract_key(contract_key),)
        with self._connect() as connection:
            with connection.cursor(
                cursor_factory=RealDictCursor,
            ) as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        id,
                        contract_key,
                        provider,
                        product_name,
                        mic,
                        transport,
                        usage_scope,
                        external_distribution,
                        raw_storage_allowed,
                        derived_storage_allowed,
                        retention_policy,
                        terms_url,
                        terms_checksum_sha256,
                        status,
                        valid_from,
                        valid_until,
                        legal_reviewed_at,
                        storage_reviewed_at,
                        entitlement_verified_at,
                        host_key_algorithm,
                        host_key_fingerprint_sha256,
                        reviewed_by,
                        revoked_at,
                        revoked_by,
                        revocation_reason,
                        created_at,
                        updated_at
                    FROM reference_data_entitlements
                    {condition}
                    ORDER BY id DESC
                    LIMIT 100
                    """,
                    parameters,
                )
                return [dict(row) for row in cursor.fetchall()]

    def revoke_reference_entitlement(
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
        revoked_at = self._now()
        with self._connect() as connection:
            with connection.cursor(
                cursor_factory=RealDictCursor,
            ) as cursor:
                cursor.execute(
                    """
                    UPDATE reference_data_entitlements
                    SET
                        status = 'REVOKED',
                        revoked_at = %s,
                        revoked_by = %s,
                        revocation_reason = %s
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
                        key,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError(
                        "validated reference entitlement does not exist"
                    )
                return dict(row)

    def _now(self) -> datetime:
        return _aware_datetime(self._clock(), "clock")

    def _connect(self):
        return psycopg2.connect(self.database_url)


def approval_from_files(
    *,
    evidence_json_path: str,
    terms_file_path: str,
    reviewed_by: str,
    reviewed_at: datetime,
) -> ReferenceEntitlementApproval:
    manifest = _load_json_object(evidence_json_path)
    if set(manifest) != _MANIFEST_FIELDS:
        raise ValueError(
            "reference entitlement manifest fields do not match contract"
        )
    terms_checksum = hashlib.sha256(
        _read_regular_file(
            terms_file_path,
            maximum=_MAX_TERMS_BYTES,
            label="terms file",
        )
    ).hexdigest()
    try:
        return ReferenceEntitlementApproval(
            contract_key=manifest["contract_key"],
            provider=manifest["provider"],
            product_name=manifest["product_name"],
            mic=manifest["mic"],
            retention_policy=manifest["retention_policy"],
            terms_url=manifest["terms_url"],
            terms_checksum_sha256=terms_checksum,
            valid_from=date.fromisoformat(manifest["valid_from"]),
            valid_until=date.fromisoformat(manifest["valid_until"]),
            host_key_algorithm=manifest["host_key_algorithm"],
            host_key_fingerprint_sha256=manifest[
                "host_key_fingerprint_sha256"
            ],
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            "reference entitlement manifest is invalid"
        ) from exc


def _load_json_object(path: str) -> dict[str, Any]:
    content = _read_regular_file(
        path,
        maximum=_MAX_MANIFEST_BYTES,
        label="evidence JSON",
    )

    def strict_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("evidence JSON contains duplicate keys")
            value[key] = item
        return value

    try:
        result = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("evidence JSON must contain valid UTF-8 JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("evidence JSON must contain one object")
    return result


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
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError(f"{label} is unavailable") from None
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if not 1 <= file_stat.st_size <= maximum:
            raise ValueError(f"{label} size is invalid")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            content = handle.read(maximum + 1)
        if len(content) != file_stat.st_size or len(content) > maximum:
            raise ValueError(f"{label} changed while being read")
        return content
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _contract_key(value: Any) -> str:
    if not isinstance(value, str) or not _CONTRACT_KEY_PATTERN.fullmatch(
        value,
    ):
        raise ValueError("contract_key has invalid format")
    return value


def _operator_identity(value: Any) -> str:
    if not isinstance(value, str) or not _OPERATOR_PATTERN.fullmatch(value):
        raise ValueError("operator identity must start with operator:")
    return value


def _bounded_text(value: Any, name: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(
            f"{name} must contain between 1 and {maximum} characters"
        )
    return normalized


def _aware_datetime(value: Any, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage validated Nasdaq reference entitlements.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status")
    status.add_argument("--contract-key")

    validate = commands.add_parser("validate")
    validate.add_argument("--evidence-json", required=True)
    validate.add_argument("--terms-file", required=True)
    validate.add_argument("--operator", required=True)
    for flag in (
        "confirm-internal-paper-use",
        "confirm-raw-storage",
        "confirm-derived-storage",
        "confirm-host-key-independent",
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
        admin = ReferenceEntitlementAdmin()
        if args.command == "status":
            result: Any = admin.list_reference_entitlements(
                args.contract_key,
            )
        elif args.command == "validate":
            approval = approval_from_files(
                evidence_json_path=args.evidence_json,
                terms_file_path=args.terms_file,
                reviewed_by=args.operator,
                reviewed_at=datetime.now(timezone.utc),
            )
            result = admin.validate_reference_entitlement(approval)
        elif args.command == "revoke":
            result = admin.revoke_reference_entitlement(
                args.contract_key,
                revoked_by=args.operator,
                reason=args.reason,
            )
        else:
            raise RuntimeError("unsupported command")
    except (MarketDataError, ValueError) as exc:
        print(f"reference entitlement admin error: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "reference entitlement admin error: operation failed",
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
