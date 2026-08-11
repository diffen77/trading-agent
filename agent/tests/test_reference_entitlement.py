from datetime import date, datetime, timedelta, timezone

import pytest

from src.data.market_data import MarketDataError
from src.data.provider_governance import (
    ReferenceDataEntitlement,
    assert_reference_entitlement_ready,
)


_FINGERPRINT = "SHA256:" + "A" * 43


def _entitlement(**overrides) -> ReferenceDataEntitlement:
    values = {
        "entitlement_id": 7,
        "contract_key": "nasdaq-nordic-reference-xsto-v1",
        "provider": "nasdaq-nordic",
        "product_name": "Nordic Equity Reference Data Files",
        "mic": "XSTO",
        "transport": "SFTP",
        "usage_scope": "INTERNAL_ANALYSIS_AND_PAPER",
        "external_distribution": False,
        "raw_storage_allowed": True,
        "derived_storage_allowed": True,
        "retention_policy": "Internal retention permitted by agreement.",
        "terms_url": "https://example.com/agreement",
        "terms_checksum_sha256": "a" * 64,
        "status": "VALIDATED",
        "valid_from": date(2026, 7, 1),
        "valid_until": date(2026, 12, 31),
        "legal_reviewed_at": datetime(
            2026,
            7,
            1,
            tzinfo=timezone.utc,
        ),
        "storage_reviewed_at": datetime(
            2026,
            7,
            1,
            tzinfo=timezone.utc,
        ),
        "entitlement_verified_at": datetime(
            2026,
            7,
            1,
            tzinfo=timezone.utc,
        ),
        "host_key_algorithm": "ssh-ed25519",
        "host_key_fingerprint_sha256": _FINGERPRINT,
        "reviewed_by": "operator:test",
    }
    values.update(overrides)
    return ReferenceDataEntitlement(**values)


def test_reference_entitlement_accepts_complete_current_legal_evidence():
    entitlement = _entitlement()
    now = datetime(2026, 7, 30, 5, 0, tzinfo=timezone.utc)

    ready = assert_reference_entitlement_ready(
        entitlement,
        now=now,
        contract_key="nasdaq-nordic-reference-xsto-v1",
        provider="nasdaq-nordic",
        mic="XSTO",
        transport="SFTP",
        usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
    )

    assert ready is entitlement
    assert ready.host_key_fingerprint_sha256 == _FINGERPRINT


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"status": "DRAFT"}, "not validated"),
        ({"raw_storage_allowed": False}, "raw storage"),
        ({"derived_storage_allowed": False}, "derived storage"),
        ({"external_distribution": True}, "external distribution"),
        ({"host_key_fingerprint_sha256": "bad"}, "fingerprint"),
        (
            {"valid_until": date(2026, 7, 29)},
            "expired",
        ),
        (
            {
                "legal_reviewed_at": datetime(
                    2026,
                    7,
                    31,
                    tzinfo=timezone.utc,
                )
            },
            "future",
        ),
    ),
)
def test_reference_entitlement_rejects_incomplete_or_stale_evidence(
    overrides,
    message,
):
    with pytest.raises(MarketDataError, match=message):
        entitlement = _entitlement(**overrides)
        assert_reference_entitlement_ready(
            entitlement,
            now=datetime(
                2026,
                7,
                30,
                5,
                0,
                tzinfo=timezone.utc,
            ),
            contract_key="nasdaq-nordic-reference-xsto-v1",
            provider="nasdaq-nordic",
            mic="XSTO",
            transport="SFTP",
            usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
        )


def test_reference_entitlement_rejects_runtime_contract_mismatch():
    entitlement = _entitlement()

    with pytest.raises(MarketDataError, match="provider"):
        assert_reference_entitlement_ready(
            entitlement,
            now=entitlement.entitlement_verified_at + timedelta(days=1),
            contract_key=entitlement.contract_key,
            provider="another-provider",
            mic=entitlement.mic,
            transport=entitlement.transport,
            usage_scope=entitlement.usage_scope,
        )
