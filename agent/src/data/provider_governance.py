"""Fail-closed governance for market-data products and runtime use."""

from dataclasses import dataclass
from datetime import date, datetime
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from src.data.market_data import MarketDataError


_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,99}$")
_MIC_PATTERN = re.compile(r"^[A-Z0-9]{4}$")
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OPERATOR_PATTERN = re.compile(r"^operator:[A-Za-z0-9._-]{1,80}$")
_DELIVERY_MODES = {"DELAYED_15M", "REALTIME"}
_TRANSPORTS = {
    "AUTHORIZED_VENDOR",
    "DIRECT_FEED",
    "PUBLIC_CSV",
    "SFTP",
    "WEB_API",
}
_USAGE_SCOPES = {
    "AUTOMATED_LIVE_TRADING",
    "INTERNAL_ANALYSIS_AND_PAPER",
}
_NON_DISPLAY_CATEGORIES = {"NONE", "CATEGORY_1", "CATEGORY_2"}
_CONTRACT_STATUSES = {"DRAFT", "VALIDATED", "REVOKED"}
_VALIDATION_STATUSES = {"PASSED", "FAILED"}
_AUTHORIZATION_BASES = {
    "NEGOTIATED_CONTRACT",
    "PUBLIC_NONCOMMERCIAL_TERMS",
}
_VALIDATION_BASES = {
    "FIVE_SESSION_ACCEPTANCE",
    "CONTINUOUS_FILE_VALIDATION",
}
_GOVERNANCE_EVIDENCE_STATUSES = {
    "LEGACY_UNVERIFIED",
    "PENDING",
    "VERIFIED",
}
_REFERENCE_ENTITLEMENT_STATUSES = {"DRAFT", "VALIDATED", "REVOKED"}
_REFERENCE_TRANSPORTS = {"SFTP"}
_REFERENCE_USAGE_SCOPES = {"INTERNAL_ANALYSIS_AND_PAPER"}
_HOST_KEY_ALGORITHM_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9@._+-]{2,99}$"
)
_HOST_KEY_FINGERPRINT_PATTERN = re.compile(
    r"^SHA256:[A-Za-z0-9+/]{43}$"
)
_STOCKHOLM = ZoneInfo("Europe/Stockholm")


@dataclass(frozen=True)
class ProviderContract:
    """Public, non-secret facts governing one provider product."""

    contract_key: str
    provider: str
    product_name: str
    data_type: str
    mic: str
    delivery_mode: str
    transport: str
    nominal_delay_seconds: int
    max_transport_lag_seconds: int
    usage_scope: str
    non_display_category: str
    external_distribution: bool
    reference_symbols_included: bool
    terms_url: str
    status: str
    valid_from: date
    valid_until: date
    legal_reviewed_at: datetime | None
    transport_verified_at: datetime | None
    authorization_basis: str = "NEGOTIATED_CONTRACT"
    policy_verified_at: datetime | None = None
    policy_verified_by: str | None = None
    governance_evidence_status: str = "LEGACY_UNVERIFIED"
    terms_checksum_sha256: str | None = None
    raw_storage_allowed: bool = False
    derived_storage_allowed: bool = False
    retention_policy: str | None = None
    proposed_by: str | None = None
    reviewed_by: str | None = None
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revocation_reason: str | None = None

    def __post_init__(self) -> None:
        contract_key = _key(self.contract_key, "contract_key")
        provider = _key(self.provider, "provider")
        product_name = _text(self.product_name, "product_name", maximum=255)
        data_type = _text(self.data_type, "data_type", maximum=50)
        mic = _text(self.mic, "mic", maximum=4).upper()
        delivery_mode = _choice(
            self.delivery_mode,
            "delivery_mode",
            _DELIVERY_MODES,
        )
        transport = _choice(self.transport, "transport", _TRANSPORTS)
        usage_scope = _choice(
            self.usage_scope,
            "usage_scope",
            _USAGE_SCOPES,
        )
        non_display_category = _choice(
            self.non_display_category,
            "non_display_category",
            _NON_DISPLAY_CATEGORIES,
        )
        status = _choice(self.status, "status", _CONTRACT_STATUSES)
        governance_status = _choice(
            self.governance_evidence_status,
            "governance_evidence_status",
            _GOVERNANCE_EVIDENCE_STATUSES,
        )
        authorization_basis = _choice(
            self.authorization_basis,
            "authorization_basis",
            _AUTHORIZATION_BASES,
        )

        if not _MIC_PATTERN.fullmatch(mic):
            raise MarketDataError("mic must be a four-character MIC")
        nominal_delay = _integer(
            self.nominal_delay_seconds,
            "nominal_delay_seconds",
            minimum=0,
            maximum=900,
        )
        transport_lag = _integer(
            self.max_transport_lag_seconds,
            "max_transport_lag_seconds",
            minimum=0,
            maximum=300,
        )
        if (
            delivery_mode == "DELAYED_15M"
            and nominal_delay != 900
        ):
            raise MarketDataError(
                "15-minute delayed products must declare 900 seconds"
            )
        if delivery_mode == "REALTIME" and nominal_delay != 0:
            raise MarketDataError(
                "real-time products must declare zero nominal delay"
            )
        if (
            delivery_mode == "REALTIME"
            and non_display_category == "NONE"
        ):
            raise MarketDataError(
                "real-time automated use requires non-display authorization"
            )
        if (
            usage_scope == "AUTOMATED_LIVE_TRADING"
            and non_display_category != "CATEGORY_2"
        ):
            raise MarketDataError(
                "automated live trading requires non-display category 2"
            )
        if not isinstance(self.external_distribution, bool):
            raise MarketDataError("external_distribution must be boolean")
        if not isinstance(self.reference_symbols_included, bool):
            raise MarketDataError(
                "reference_symbols_included must be boolean"
            )
        for field in (
            "raw_storage_allowed",
            "derived_storage_allowed",
        ):
            if not isinstance(getattr(self, field), bool):
                raise MarketDataError(f"{field} must be boolean")

        terms_url = _https_url(self.terms_url, "terms_url")
        if not isinstance(self.valid_from, date):
            raise MarketDataError("valid_from must be a date")
        if not isinstance(self.valid_until, date):
            raise MarketDataError("valid_until must be a date")
        if self.valid_until < self.valid_from:
            raise MarketDataError("valid_until cannot precede valid_from")
        _optional_aware(
            self.legal_reviewed_at,
            "legal_reviewed_at",
        )
        _optional_aware(
            self.transport_verified_at,
            "transport_verified_at",
        )
        _optional_aware(
            self.policy_verified_at,
            "policy_verified_at",
        )
        policy_verified_by = self.policy_verified_by
        if policy_verified_by is not None:
            policy_verified_by = _text(
                policy_verified_by,
                "policy_verified_by",
                maximum=100,
            )
            if _OPERATOR_PATTERN.fullmatch(policy_verified_by) is None:
                raise MarketDataError(
                    "policy_verified_by must identify an operator"
                )
        if authorization_basis == "PUBLIC_NONCOMMERCIAL_TERMS" and (
            delivery_mode != "DELAYED_15M"
            or usage_scope != "INTERNAL_ANALYSIS_AND_PAPER"
            or self.external_distribution
            or self.raw_storage_allowed
        ):
            raise MarketDataError(
                "public terms authorization must be delayed, internal, "
                "non-distributed and must not retain raw files"
            )
        _optional_aware(self.revoked_at, "revoked_at")
        terms_checksum = self.terms_checksum_sha256
        if terms_checksum is not None:
            terms_checksum = _text(
                terms_checksum,
                "terms_checksum_sha256",
                maximum=64,
            ).lower()
            if not _CHECKSUM_PATTERN.fullmatch(terms_checksum):
                raise MarketDataError(
                    "terms_checksum_sha256 must be lowercase SHA-256"
                )
        retention_policy = self.retention_policy
        if retention_policy is not None:
            retention_policy = _text(
                retention_policy,
                "retention_policy",
                maximum=500,
            )
        proposed_by = self.proposed_by
        if proposed_by is not None:
            proposed_by = _text(
                proposed_by,
                "proposed_by",
                maximum=100,
            )
            if _OPERATOR_PATTERN.fullmatch(proposed_by) is None:
                raise MarketDataError(
                    "proposed_by must identify an operator"
                )
        reviewed_by = self.reviewed_by
        if reviewed_by is not None:
            reviewed_by = _text(
                reviewed_by,
                "reviewed_by",
                maximum=100,
            )
        revoked_by = self.revoked_by
        if revoked_by is not None:
            revoked_by = _text(
                revoked_by,
                "revoked_by",
                maximum=100,
            )
        revocation_reason = self.revocation_reason
        if revocation_reason is not None:
            revocation_reason = _text(
                revocation_reason,
                "revocation_reason",
                maximum=500,
            )
        revocation_values = (
            self.revoked_at,
            revoked_by,
            revocation_reason,
        )
        if status == "REVOKED":
            if any(value is None for value in revocation_values):
                raise MarketDataError(
                    "revoked provider contract lacks revocation evidence"
                )
        elif any(value is not None for value in revocation_values):
            raise MarketDataError(
                "active provider contract has revocation evidence"
            )

        object.__setattr__(self, "contract_key", contract_key)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "product_name", product_name)
        object.__setattr__(self, "data_type", data_type)
        object.__setattr__(self, "mic", mic)
        object.__setattr__(self, "delivery_mode", delivery_mode)
        object.__setattr__(self, "transport", transport)
        object.__setattr__(
            self,
            "nominal_delay_seconds",
            nominal_delay,
        )
        object.__setattr__(
            self,
            "max_transport_lag_seconds",
            transport_lag,
        )
        object.__setattr__(self, "usage_scope", usage_scope)
        object.__setattr__(
            self,
            "non_display_category",
            non_display_category,
        )
        object.__setattr__(self, "terms_url", terms_url)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "authorization_basis",
            authorization_basis,
        )
        object.__setattr__(
            self,
            "policy_verified_by",
            policy_verified_by,
        )
        object.__setattr__(
            self,
            "governance_evidence_status",
            governance_status,
        )
        object.__setattr__(
            self,
            "terms_checksum_sha256",
            terms_checksum,
        )
        object.__setattr__(
            self,
            "retention_policy",
            retention_policy,
        )
        object.__setattr__(self, "proposed_by", proposed_by)
        object.__setattr__(self, "reviewed_by", reviewed_by)
        object.__setattr__(self, "revoked_by", revoked_by)
        object.__setattr__(
            self,
            "revocation_reason",
            revocation_reason,
        )


@dataclass(frozen=True)
class ProviderValidation:
    """Bounded runtime evidence for one provider contract."""

    contract_key: str
    status: str
    validated_at: datetime
    valid_until: datetime
    expected_instruments: int
    product_covered_instruments: int
    symbol_mapped_instruments: int
    sample_file_count: int
    sample_quote_count: int
    max_observed_delivery_seconds: int | None
    evidence_checksum_sha256: str
    validated_by: str
    validation_basis: str = "FIVE_SESSION_ACCEPTANCE"
    governance_evidence_status: str = "LEGACY_UNVERIFIED"
    reference_snapshot_id: int | None = None
    reference_checksum_sha256: str | None = None
    acceptance_session_count: int | None = None
    first_session_date: date | None = None
    last_session_date: date | None = None
    acceptance_checksum_sha256: str | None = None

    def __post_init__(self) -> None:
        contract_key = _key(self.contract_key, "contract_key")
        status = _choice(
            self.status,
            "validation status",
            _VALIDATION_STATUSES,
        )
        governance_status = _choice(
            self.governance_evidence_status,
            "governance_evidence_status",
            _GOVERNANCE_EVIDENCE_STATUSES,
        )
        validation_basis = _choice(
            self.validation_basis,
            "validation_basis",
            _VALIDATION_BASES,
        )
        _aware(self.validated_at, "validated_at")
        _aware(self.valid_until, "valid_until")
        if self.valid_until < self.validated_at:
            raise MarketDataError(
                "validation valid_until cannot precede validated_at"
            )
        for field in (
            "expected_instruments",
            "product_covered_instruments",
            "symbol_mapped_instruments",
            "sample_file_count",
            "sample_quote_count",
        ):
            value = _integer(
                getattr(self, field),
                field,
                minimum=0,
                maximum=1_000_000,
            )
            object.__setattr__(self, field, value)
        if (
            self.product_covered_instruments
            > self.expected_instruments
            or self.symbol_mapped_instruments
            > self.expected_instruments
        ):
            raise MarketDataError(
                "provider validation counts exceed expected universe"
            )
        if self.max_observed_delivery_seconds is not None:
            observed = _integer(
                self.max_observed_delivery_seconds,
                "max_observed_delivery_seconds",
                minimum=0,
                maximum=86_400,
            )
            object.__setattr__(
                self,
                "max_observed_delivery_seconds",
                observed,
            )
        checksum = _text(
            self.evidence_checksum_sha256,
            "evidence_checksum_sha256",
            maximum=64,
        ).lower()
        if not _CHECKSUM_PATTERN.fullmatch(checksum):
            raise MarketDataError(
                "evidence_checksum_sha256 must be lowercase SHA-256"
            )
        validated_by = _text(
            self.validated_by,
            "validated_by",
            maximum=100,
        )
        reference_snapshot_id = self.reference_snapshot_id
        if reference_snapshot_id is not None:
            reference_snapshot_id = _integer(
                reference_snapshot_id,
                "reference_snapshot_id",
                minimum=1,
                maximum=9_223_372_036_854_775_807,
            )
        reference_checksum = self.reference_checksum_sha256
        if reference_checksum is not None:
            reference_checksum = _text(
                reference_checksum,
                "reference_checksum_sha256",
                maximum=64,
            ).lower()
            if not _CHECKSUM_PATTERN.fullmatch(reference_checksum):
                raise MarketDataError(
                    "reference_checksum_sha256 must be lowercase SHA-256"
                )
        session_count = self.acceptance_session_count
        if session_count is not None:
            session_count = _integer(
                session_count,
                "acceptance_session_count",
                minimum=0,
                maximum=10_000,
            )
        for field in ("first_session_date", "last_session_date"):
            value = getattr(self, field)
            if value is not None and (
                not isinstance(value, date)
                or isinstance(value, datetime)
            ):
                raise MarketDataError(f"{field} must be a date")
        if (
            self.first_session_date is not None
            and self.last_session_date is not None
            and self.last_session_date < self.first_session_date
        ):
            raise MarketDataError(
                "last_session_date cannot precede first_session_date"
            )
        acceptance_checksum = self.acceptance_checksum_sha256
        if acceptance_checksum is not None:
            acceptance_checksum = _text(
                acceptance_checksum,
                "acceptance_checksum_sha256",
                maximum=64,
            ).lower()
            if not _CHECKSUM_PATTERN.fullmatch(acceptance_checksum):
                raise MarketDataError(
                    "acceptance_checksum_sha256 must be lowercase SHA-256"
                )
        object.__setattr__(self, "contract_key", contract_key)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "governance_evidence_status",
            governance_status,
        )
        object.__setattr__(
            self,
            "evidence_checksum_sha256",
            checksum,
        )
        object.__setattr__(self, "validated_by", validated_by)
        object.__setattr__(
            self,
            "validation_basis",
            validation_basis,
        )
        object.__setattr__(
            self,
            "reference_snapshot_id",
            reference_snapshot_id,
        )
        object.__setattr__(
            self,
            "reference_checksum_sha256",
            reference_checksum,
        )
        object.__setattr__(
            self,
            "acceptance_session_count",
            session_count,
        )
        object.__setattr__(
            self,
            "acceptance_checksum_sha256",
            acceptance_checksum,
        )


@dataclass(frozen=True)
class ReferenceDataEntitlement:
    """Public, non-secret legal evidence for one reference product."""

    entitlement_id: int
    contract_key: str
    provider: str
    product_name: str
    mic: str
    transport: str
    usage_scope: str
    external_distribution: bool
    raw_storage_allowed: bool
    derived_storage_allowed: bool
    retention_policy: str
    terms_url: str
    terms_checksum_sha256: str
    status: str
    valid_from: date
    valid_until: date
    legal_reviewed_at: datetime | None
    storage_reviewed_at: datetime | None
    entitlement_verified_at: datetime | None
    host_key_algorithm: str | None
    host_key_fingerprint_sha256: str | None
    reviewed_by: str

    def __post_init__(self) -> None:
        entitlement_id = _integer(
            self.entitlement_id,
            "entitlement_id",
            minimum=1,
            maximum=9_223_372_036_854_775_807,
        )
        contract_key = _key(self.contract_key, "contract_key")
        provider = _key(self.provider, "provider")
        product_name = _text(
            self.product_name,
            "product_name",
            maximum=255,
        )
        mic = _text(self.mic, "mic", maximum=4).upper()
        if not _MIC_PATTERN.fullmatch(mic):
            raise MarketDataError("mic must be a four-character MIC")
        transport = _choice(
            self.transport,
            "reference transport",
            _REFERENCE_TRANSPORTS,
        )
        usage_scope = _choice(
            self.usage_scope,
            "reference usage_scope",
            _REFERENCE_USAGE_SCOPES,
        )
        status = _choice(
            self.status,
            "reference entitlement status",
            _REFERENCE_ENTITLEMENT_STATUSES,
        )
        for field in (
            "external_distribution",
            "raw_storage_allowed",
            "derived_storage_allowed",
        ):
            if not isinstance(getattr(self, field), bool):
                raise MarketDataError(f"{field} must be boolean")
        retention_policy = _text(
            self.retention_policy,
            "retention_policy",
            maximum=500,
        )
        terms_url = _https_url(self.terms_url, "terms_url")
        terms_checksum = _text(
            self.terms_checksum_sha256,
            "terms_checksum_sha256",
            maximum=64,
        ).lower()
        if not _CHECKSUM_PATTERN.fullmatch(terms_checksum):
            raise MarketDataError(
                "terms_checksum_sha256 must be lowercase SHA-256"
            )
        for field in ("valid_from", "valid_until"):
            value = getattr(self, field)
            if not isinstance(value, date) or isinstance(value, datetime):
                raise MarketDataError(f"{field} must be a date")
        if self.valid_until < self.valid_from:
            raise MarketDataError("valid_until cannot precede valid_from")
        for field in (
            "legal_reviewed_at",
            "storage_reviewed_at",
            "entitlement_verified_at",
        ):
            _optional_aware(getattr(self, field), field)

        algorithm = self.host_key_algorithm
        fingerprint = self.host_key_fingerprint_sha256
        if algorithm is not None:
            algorithm = _text(
                algorithm,
                "host_key_algorithm",
                maximum=100,
            )
            if _HOST_KEY_ALGORITHM_PATTERN.fullmatch(algorithm) is None:
                raise MarketDataError("host key algorithm is invalid")
        if fingerprint is not None:
            fingerprint = _text(
                fingerprint,
                "host_key_fingerprint_sha256",
                maximum=50,
            )
            if _HOST_KEY_FINGERPRINT_PATTERN.fullmatch(
                fingerprint
            ) is None:
                raise MarketDataError(
                    "host key fingerprint is invalid"
                )
        if (algorithm is None) != (fingerprint is None):
            raise MarketDataError(
                "host key algorithm and fingerprint must be paired"
            )
        reviewed_by = _text(
            self.reviewed_by,
            "reviewed_by",
            maximum=100,
        )

        object.__setattr__(self, "entitlement_id", entitlement_id)
        object.__setattr__(self, "contract_key", contract_key)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "product_name", product_name)
        object.__setattr__(self, "mic", mic)
        object.__setattr__(self, "transport", transport)
        object.__setattr__(self, "usage_scope", usage_scope)
        object.__setattr__(self, "retention_policy", retention_policy)
        object.__setattr__(self, "terms_url", terms_url)
        object.__setattr__(
            self,
            "terms_checksum_sha256",
            terms_checksum,
        )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "host_key_algorithm", algorithm)
        object.__setattr__(
            self,
            "host_key_fingerprint_sha256",
            fingerprint,
        )
        object.__setattr__(self, "reviewed_by", reviewed_by)


def assert_reference_entitlement_ready(
    entitlement: ReferenceDataEntitlement,
    *,
    now: datetime,
    contract_key: str,
    provider: str,
    mic: str,
    transport: str,
    usage_scope: str,
) -> ReferenceDataEntitlement:
    """Require current legal, storage, entitlement, and host-key proof."""
    if not isinstance(entitlement, ReferenceDataEntitlement):
        raise MarketDataError("reference entitlement is missing")
    _aware(now, "now")
    expected = {
        "contract_key": _key(contract_key, "contract_key"),
        "provider": _key(provider, "provider"),
        "mic": _text(mic, "mic", maximum=4).upper(),
        "transport": _choice(
            transport,
            "reference transport",
            _REFERENCE_TRANSPORTS,
        ),
        "usage_scope": _choice(
            usage_scope,
            "reference usage_scope",
            _REFERENCE_USAGE_SCOPES,
        ),
    }
    for field, value in expected.items():
        if getattr(entitlement, field) != value:
            raise MarketDataError(
                f"reference entitlement {field} does not match runtime"
            )
    if entitlement.status != "VALIDATED":
        raise MarketDataError("reference entitlement is not validated")
    if entitlement.external_distribution:
        raise MarketDataError(
            "reference entitlement allows external distribution"
        )
    if not entitlement.raw_storage_allowed:
        raise MarketDataError(
            "reference entitlement does not allow raw storage"
        )
    if not entitlement.derived_storage_allowed:
        raise MarketDataError(
            "reference entitlement does not allow derived storage"
        )
    verification_times = (
        entitlement.legal_reviewed_at,
        entitlement.storage_reviewed_at,
        entitlement.entitlement_verified_at,
    )
    if any(value is None for value in verification_times):
        raise MarketDataError(
            "reference entitlement review evidence is incomplete"
        )
    if any(value > now for value in verification_times):
        raise MarketDataError(
            "reference entitlement verification timestamp is in the future"
        )
    stockholm_date = now.astimezone(_STOCKHOLM).date()
    if stockholm_date < entitlement.valid_from:
        raise MarketDataError("reference entitlement is not yet valid")
    if stockholm_date > entitlement.valid_until:
        raise MarketDataError("reference entitlement has expired")
    if (
        entitlement.host_key_algorithm is None
        or entitlement.host_key_fingerprint_sha256 is None
    ):
        raise MarketDataError(
            "reference entitlement host key is missing"
        )
    return entitlement


def assert_provider_ready(
    contract: ProviderContract,
    validation: ProviderValidation | None,
    *,
    now: datetime,
    expected_instruments: int,
) -> ProviderContract:
    """Return the contract or reject every incomplete runtime state."""
    if not isinstance(contract, ProviderContract):
        raise MarketDataError("provider contract is missing")
    _aware(now, "now")
    expected = _integer(
        expected_instruments,
        "expected_instruments",
        minimum=1,
        maximum=1_000_000,
    )
    if contract.status != "VALIDATED":
        raise MarketDataError("provider contract is not validated")
    if contract.governance_evidence_status != "VERIFIED":
        raise MarketDataError(
            "provider contract governance evidence is not verified"
        )
    if contract.terms_checksum_sha256 is None:
        raise MarketDataError("provider exact terms checksum is missing")
    if not contract.derived_storage_allowed:
        raise MarketDataError(
            "provider derived storage rights are incomplete"
        )
    if (
        contract.authorization_basis == "NEGOTIATED_CONTRACT"
        and not contract.raw_storage_allowed
    ):
        raise MarketDataError(
            "provider raw storage rights are incomplete"
        )
    if contract.retention_policy is None:
        raise MarketDataError("provider retention policy is missing")
    if contract.proposed_by is None:
        raise MarketDataError("provider proposer is missing")
    if contract.reviewed_by is None:
        raise MarketDataError("provider reviewer is missing")
    if (
        contract.usage_scope != "INTERNAL_ANALYSIS_AND_PAPER"
        or contract.external_distribution
    ):
        raise MarketDataError(
            "provider contract is outside internal paper use"
        )
    if contract.authorization_basis == "NEGOTIATED_CONTRACT":
        if contract.legal_reviewed_at is None:
            raise MarketDataError("provider legal terms are not reviewed")
        verification_times = (contract.legal_reviewed_at,)
    else:
        if (
            contract.policy_verified_at is None
            or contract.policy_verified_by is None
        ):
            raise MarketDataError(
                "provider public policy evidence is not verified"
            )
        verification_times = (contract.policy_verified_at,)
    if contract.transport_verified_at is None:
        raise MarketDataError("provider transport is not verified")
    if any(
        verified_at > now
        for verified_at in (*verification_times, contract.transport_verified_at)
    ):
        raise MarketDataError(
            "provider verification timestamp is in the future"
        )
    stockholm_date = now.astimezone(_STOCKHOLM).date()
    if stockholm_date < contract.valid_from:
        raise MarketDataError("provider contract is not yet valid")
    if stockholm_date > contract.valid_until:
        raise MarketDataError("provider contract has expired")
    if validation is None:
        raise MarketDataError("provider validation evidence is missing")
    if validation.contract_key != contract.contract_key:
        raise MarketDataError(
            "provider validation belongs to a different contract"
        )
    if (
        (
            contract.authorization_basis
            == "PUBLIC_NONCOMMERCIAL_TERMS"
            and validation.validation_basis
            != "CONTINUOUS_FILE_VALIDATION"
        )
        or (
            contract.authorization_basis == "NEGOTIATED_CONTRACT"
            and validation.validation_basis
            != "FIVE_SESSION_ACCEPTANCE"
        )
    ):
        raise MarketDataError(
            "provider validation basis does not match authorization"
        )
    if validation.status != "PASSED":
        raise MarketDataError("provider validation has not passed")
    if validation.governance_evidence_status != "VERIFIED":
        raise MarketDataError(
            "provider validation governance evidence is not verified"
        )
    if (
        contract.data_type != "delayed-index-level"
        and (
            validation.reference_snapshot_id is None
            or validation.reference_checksum_sha256 is None
        )
    ):
        raise MarketDataError(
            "provider validation reference evidence is missing"
        )
    if (
        contract.data_type == "delayed-index-level"
        and (
            validation.reference_snapshot_id is not None
            or validation.reference_checksum_sha256 is not None
        )
    ):
        raise MarketDataError(
            "index validation must not claim an equity snapshot"
        )
    minimum_sessions = (
        1
        if (
            contract.authorization_basis
            == "PUBLIC_NONCOMMERCIAL_TERMS"
            and validation.validation_basis
            == "CONTINUOUS_FILE_VALIDATION"
        )
        else 5
    )
    if (
        validation.acceptance_session_count is None
        or validation.acceptance_session_count < minimum_sessions
    ):
        raise MarketDataError(
            "provider acceptance requires five sessions"
            if minimum_sessions == 5
            else "provider continuous validation requires one session"
        )
    if (
        validation.first_session_date is None
        or validation.last_session_date is None
        or validation.acceptance_checksum_sha256 is None
    ):
        raise MarketDataError(
            "provider acceptance evidence is incomplete"
        )
    if validation.last_session_date > stockholm_date:
        raise MarketDataError(
            "provider acceptance session is in the future"
        )
    if validation.validated_at > now:
        raise MarketDataError(
            "provider validation timestamp is in the future"
        )
    if validation.valid_until < now:
        raise MarketDataError("provider validation has expired")
    if (
        validation.valid_until.astimezone(_STOCKHOLM).date()
        > contract.valid_until
    ):
        raise MarketDataError(
            "provider validation outlives the contract"
        )
    if validation.expected_instruments != expected:
        raise MarketDataError(
            "provider validation universe does not match runtime"
        )
    if validation.product_covered_instruments != expected:
        raise MarketDataError(
            "provider product coverage is incomplete"
        )
    if validation.symbol_mapped_instruments != expected:
        raise MarketDataError(
            "provider symbol mapping is incomplete"
        )
    if (
        validation.sample_file_count < 1
        or validation.sample_quote_count < 1
    ):
        raise MarketDataError("provider validation sample is empty")
    if validation.max_observed_delivery_seconds is None:
        raise MarketDataError(
            "provider delivery observation is missing"
        )
    maximum_delivery = (
        contract.nominal_delay_seconds
        + contract.max_transport_lag_seconds
    )
    if not (
        contract.nominal_delay_seconds
        <= validation.max_observed_delivery_seconds
        <= maximum_delivery
    ):
        raise MarketDataError(
            "provider observed delivery is outside the contract"
        )
    return contract


def _key(value: object, field: str) -> str:
    result = _text(value, field, maximum=100).lower()
    if not _KEY_PATTERN.fullmatch(result):
        raise MarketDataError(f"{field} has invalid format")
    return result


def _text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketDataError(f"{field} is required")
    result = value.strip()
    if len(result) > maximum:
        raise MarketDataError(f"{field} exceeds {maximum} characters")
    return result


def _choice(value: object, field: str, allowed: set[str]) -> str:
    result = _text(value, field, maximum=100).upper()
    if result not in allowed:
        raise MarketDataError(f"{field} is invalid")
    return result


def _integer(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise MarketDataError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise MarketDataError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return value


def _https_url(value: object, field: str) -> str:
    result = _text(value, field, maximum=2_000)
    parsed = urlparse(result)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise MarketDataError(f"{field} must be an absolute HTTPS URL")
    return result


def _aware(value: object, field: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise MarketDataError(f"{field} must be timezone-aware")


def _optional_aware(value: object, field: str) -> None:
    if value is not None:
        _aware(value, field)
