from datetime import date, datetime, timedelta, timezone

import pytest

from src.data.market_data import MarketDataError
from src.data.provider_governance import (
    ProviderContract,
    ProviderValidation,
    assert_provider_ready,
)


NOW = datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc)


def _contract(**overrides):
    values = {
        "contract_key": "nasdaq-nordic-delayed-xsto-v1",
        "provider": "nasdaq-nordic",
        "product_name": "Nasdaq Nordic Equity Level 1 delayed",
        "data_type": "delayed-post-trade-equity",
        "mic": "XSTO",
        "delivery_mode": "DELAYED_15M",
        "transport": "PUBLIC_CSV",
        "nominal_delay_seconds": 900,
        "max_transport_lag_seconds": 30,
        "usage_scope": "INTERNAL_ANALYSIS_AND_PAPER",
        "non_display_category": "NONE",
        "external_distribution": False,
        "reference_symbols_included": False,
        "terms_url": (
            "https://www.nasdaq.com/docs/"
            "Nasdaq_European_Data_Policies_April_2026"
        ),
        "status": "VALIDATED",
        "valid_from": date(2026, 4, 1),
        "valid_until": date(2026, 8, 31),
        "legal_reviewed_at": NOW - timedelta(days=1),
        "transport_verified_at": NOW - timedelta(hours=1),
        "governance_evidence_status": "VERIFIED",
        "terms_checksum_sha256": "b" * 64,
        "raw_storage_allowed": True,
        "derived_storage_allowed": True,
        "retention_policy": "Internal paper-trading retention only.",
        "proposed_by": "operator:test",
        "reviewed_by": "operator:test",
    }
    values.update(overrides)
    return ProviderContract(**values)


def _validation(**overrides):
    values = {
        "contract_key": "nasdaq-nordic-delayed-xsto-v1",
        "status": "PASSED",
        "validated_at": NOW - timedelta(minutes=10),
        "valid_until": NOW + timedelta(days=7),
        "expected_instruments": 416,
        "product_covered_instruments": 416,
        "symbol_mapped_instruments": 416,
        "sample_file_count": 3,
        "sample_quote_count": 20,
        "max_observed_delivery_seconds": 918,
        "evidence_checksum_sha256": "a" * 64,
        "validated_by": "operator:test",
        "governance_evidence_status": "VERIFIED",
        "reference_snapshot_id": 42,
        "reference_checksum_sha256": "c" * 64,
        "acceptance_session_count": 5,
        "first_session_date": date(2026, 7, 20),
        "last_session_date": date(2026, 7, 24),
        "acceptance_checksum_sha256": "d" * 64,
    }
    values.update(overrides)
    return ProviderValidation(**values)


def test_validated_delayed_contract_is_runtime_ready():
    contract = _contract()
    validation = _validation()

    result = assert_provider_ready(
        contract,
        validation,
        now=NOW,
        expected_instruments=416,
    )

    assert result is contract


def test_public_noncommercial_policy_uses_continuous_file_validation():
    contract = _contract(
        data_type="delayed-pre-trade-equity",
        authorization_basis="PUBLIC_NONCOMMERCIAL_TERMS",
        legal_reviewed_at=None,
        raw_storage_allowed=False,
        policy_verified_at=NOW - timedelta(hours=1),
        policy_verified_by="operator:codex",
    )
    validation = _validation(
        validation_basis="CONTINUOUS_FILE_VALIDATION",
        acceptance_session_count=1,
        first_session_date=NOW.date(),
        last_session_date=NOW.date(),
    )

    result = assert_provider_ready(
        contract,
        validation,
        now=NOW,
        expected_instruments=416,
    )

    assert result is contract


def test_index_validation_is_ready_without_an_equity_snapshot():
    contract = _contract(data_type="delayed-index-level")
    validation = _validation(
        expected_instruments=1,
        product_covered_instruments=1,
        symbol_mapped_instruments=1,
        reference_snapshot_id=None,
        reference_checksum_sha256=None,
    )

    assert_provider_ready(
        contract,
        validation,
        now=NOW,
        expected_instruments=1,
    )

    with pytest.raises(MarketDataError, match="must not claim"):
        assert_provider_ready(
            contract,
            _validation(
                expected_instruments=1,
                product_covered_instruments=1,
                symbol_mapped_instruments=1,
            ),
            now=NOW,
            expected_instruments=1,
        )


def test_realtime_contract_requires_non_display_authorization():
    with pytest.raises(MarketDataError, match="non-display"):
        _contract(
            delivery_mode="REALTIME",
            transport="WEB_API",
            nominal_delay_seconds=0,
            non_display_category="NONE",
        )


@pytest.mark.parametrize(
    ("contract_overrides", "validation_overrides", "message"),
    [
        ({"status": "DRAFT"}, {}, "validated"),
        (
            {"governance_evidence_status": "LEGACY_UNVERIFIED"},
            {},
            "governance",
        ),
        ({"terms_checksum_sha256": None}, {}, "terms"),
        ({"raw_storage_allowed": False}, {}, "storage"),
        ({"transport_verified_at": None}, {}, "transport"),
        ({"valid_until": date(2026, 7, 28)}, {}, "expired"),
        ({}, {"status": "FAILED"}, "passed"),
        (
            {},
            {"governance_evidence_status": "LEGACY_UNVERIFIED"},
            "governance",
        ),
        ({}, {"acceptance_session_count": 4}, "five"),
        ({}, {"reference_snapshot_id": None}, "reference"),
        ({}, {"valid_until": NOW - timedelta(seconds=1)}, "expired"),
        ({}, {"product_covered_instruments": 415}, "coverage"),
        ({}, {"symbol_mapped_instruments": 415}, "symbol"),
        ({}, {"sample_quote_count": 0}, "sample"),
        ({}, {"max_observed_delivery_seconds": 931}, "delivery"),
    ],
)
def test_provider_gate_fails_closed(
    contract_overrides,
    validation_overrides,
    message,
):
    contract = _contract(**contract_overrides)
    validation = _validation(**validation_overrides)

    with pytest.raises(MarketDataError, match=message):
        assert_provider_ready(
            contract,
            validation,
            now=NOW,
            expected_instruments=416,
        )


def test_provider_gate_rejects_validation_for_wrong_contract_or_universe():
    contract = _contract()

    with pytest.raises(MarketDataError, match="contract"):
        assert_provider_ready(
            contract,
            _validation(contract_key="different-contract"),
            now=NOW,
            expected_instruments=416,
        )

    with pytest.raises(MarketDataError, match="universe"):
        assert_provider_ready(
            contract,
            _validation(),
            now=NOW,
            expected_instruments=417,
        )
