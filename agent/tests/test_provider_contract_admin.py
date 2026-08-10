from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import pytest

from src.provider_contract_admin import (
    _parser,
    acceptance_from_files,
    proposal_from_file,
)


NOW = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)


def _contract_manifest():
    return {
        "contract_key": "licensed-delayed-pretrade-xsto-v1",
        "provider": "nasdaq-nordic",
        "product_name": "Licensed delayed XSTO Level 1",
        "data_type": "delayed-pre-trade-equity",
        "mic": "XSTO",
        "delivery_mode": "DELAYED_15M",
        "transport": "AUTHORIZED_VENDOR",
        "nominal_delay_seconds": 900,
        "max_transport_lag_seconds": 30,
        "non_display_category": "NONE",
        "reference_symbols_included": True,
        "terms_url": "https://example.test/provider-terms",
        "valid_from": "2026-07-01",
        "valid_until": "2026-12-31",
    }


def _acceptance_manifest():
    sessions = []
    for offset in range(5):
        session_date = date(2026, 7, 20) + timedelta(days=offset)
        sessions.append(
            {
                "session_date": session_date.isoformat(),
                "expected_instruments": 416,
                "product_covered_instruments": 416,
                "symbol_mapped_instruments": 416,
                "sample_file_count": 390,
                "sample_quote_count": 50_000,
                "max_observed_delivery_seconds": 925,
                "evidence_checksum_sha256": (
                    f"{offset + 1:064x}"
                ),
            }
        )
    return {
        "contract_key": "licensed-delayed-pretrade-xsto-v1",
        "reference_snapshot_id": 42,
        "reference_checksum_sha256": "a" * 64,
        "validation_valid_until": "2026-08-31T23:59:00Z",
        "retention_policy": (
            "Raw and derived data retained internally under agreement."
        ),
        "raw_storage_allowed": True,
        "derived_storage_allowed": True,
        "transport_verified": True,
        "correction_handling_verified": True,
        "restart_verified": True,
        "gap_recovery_verified": True,
        "kill_switch_verified": True,
        "sessions": sessions,
    }


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_provider_proposal_is_strict_and_fixes_safe_usage(tmp_path):
    manifest_path = _write_json(
        tmp_path / "contract.json",
        _contract_manifest(),
    )

    proposal = proposal_from_file(
        contract_json_path=str(manifest_path),
        proposed_by="operator:diffen",
    )

    assert proposal.contract.status == "DRAFT"
    assert proposal.contract.usage_scope == "INTERNAL_ANALYSIS_AND_PAPER"
    assert proposal.contract.external_distribution is False
    assert proposal.contract.proposed_by == "operator:diffen"
    assert proposal.proposed_by == "operator:diffen"

    unsafe = _contract_manifest()
    unsafe["api_key"] = "must-never-be-accepted"
    _write_json(manifest_path, unsafe)
    with pytest.raises(ValueError, match="fields"):
        proposal_from_file(
            contract_json_path=str(manifest_path),
            proposed_by="operator:diffen",
        )


def test_acceptance_binds_exact_terms_and_five_full_sessions(tmp_path):
    acceptance_path = _write_json(
        tmp_path / "acceptance.json",
        _acceptance_manifest(),
    )
    terms_path = tmp_path / "terms.pdf"
    terms_content = b"exact reviewed provider agreement"
    terms_path.write_bytes(terms_content)

    approval = acceptance_from_files(
        acceptance_json_path=str(acceptance_path),
        terms_file_path=str(terms_path),
        validated_by="operator:diffen",
    )

    assert len(approval.sessions) == 5
    assert approval.expected_instruments == 416
    assert approval.sample_file_count == 1_950
    assert approval.sample_quote_count == 250_000
    assert approval.max_observed_delivery_seconds == 925
    assert approval.terms_checksum_sha256 == hashlib.sha256(
        terms_content
    ).hexdigest()
    assert approval.acceptance_checksum_sha256 == hashlib.sha256(
        acceptance_path.read_bytes()
    ).hexdigest()
    assert len(approval.evidence_checksum_sha256) == 64


def test_index_acceptance_does_not_claim_an_equity_snapshot(tmp_path):
    manifest = _acceptance_manifest()
    manifest["reference_snapshot_id"] = None
    manifest["reference_checksum_sha256"] = None
    for session in manifest["sessions"]:
        session["expected_instruments"] = 1
        session["product_covered_instruments"] = 1
        session["symbol_mapped_instruments"] = 1
    acceptance_path = _write_json(
        tmp_path / "index-acceptance.json",
        manifest,
    )
    terms_path = tmp_path / "index-terms.pdf"
    terms_path.write_bytes(b"reviewed OMXSGI terms")

    approval = acceptance_from_files(
        acceptance_json_path=str(acceptance_path),
        terms_file_path=str(terms_path),
        validated_by="operator:diffen",
    )

    assert approval.reference_snapshot_id is None
    assert approval.reference_checksum_sha256 is None
    assert approval.expected_instruments == 1


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["sessions"].pop(),
            "five",
        ),
        (
            lambda value: value["sessions"][0].update(
                {"product_covered_instruments": 415}
            ),
            "coverage",
        ),
        (
            lambda value: value.update({"raw_storage_allowed": False}),
            "storage",
        ),
        (
            lambda value: value["sessions"][1].update(
                {"session_date": value["sessions"][0]["session_date"]}
            ),
            "ordered",
        ),
    ],
)
def test_acceptance_rejects_incomplete_or_ambiguous_evidence(
    tmp_path,
    mutate,
    message,
):
    manifest = _acceptance_manifest()
    mutate(manifest)
    acceptance_path = _write_json(
        tmp_path / "acceptance.json",
        manifest,
    )
    terms_path = tmp_path / "terms.pdf"
    terms_path.write_bytes(b"reviewed terms")

    with pytest.raises(ValueError, match=message):
        acceptance_from_files(
            acceptance_json_path=str(acceptance_path),
            terms_file_path=str(terms_path),
            validated_by="operator:diffen",
        )


def test_validate_command_requires_every_operator_confirmation():
    parser = _parser()
    base = [
        "validate",
        "--acceptance-json",
        "/secure/acceptance.json",
        "--terms-file",
        "/secure/terms.pdf",
        "--operator",
        "operator:diffen",
    ]

    with pytest.raises(SystemExit):
        parser.parse_args(base)

    args = parser.parse_args(
        base
        + [
            "--confirm-internal-paper-use",
            "--confirm-no-external-distribution",
            "--confirm-raw-storage",
            "--confirm-derived-storage",
            "--confirm-transport-tested",
            "--confirm-five-consecutive-sessions",
            "--confirm-restart-and-gap-recovery",
            "--confirm-kill-switch",
        ]
    )

    assert args.command == "validate"
    assert args.confirm_five_consecutive_sessions is True
