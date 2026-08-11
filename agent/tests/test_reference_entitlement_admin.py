from datetime import datetime, timezone
import hashlib
import json

import pytest

from src.reference_entitlement_admin import (
    _parser,
    approval_from_files,
)


def _manifest():
    return {
        "contract_key": "nasdaq-nordic-reference-xsto-v1",
        "provider": "nasdaq-nordic",
        "product_name": "Nordic Equity Reference Data Files",
        "mic": "XSTO",
        "retention_policy": "Internal retention permitted by agreement.",
        "terms_url": "https://example.com/agreement",
        "valid_from": "2026-07-01",
        "valid_until": "2026-12-31",
        "host_key_algorithm": "ssh-ed25519",
        "host_key_fingerprint_sha256": "SHA256:" + "A" * 43,
    }


def test_approval_hashes_exact_terms_and_fixes_safe_usage_scope(tmp_path):
    manifest_path = tmp_path / "approval.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    terms_path = tmp_path / "agreement.pdf"
    terms_content = b"reviewed agreement bytes"
    terms_path.write_bytes(terms_content)
    reviewed_at = datetime(2026, 7, 30, 5, 0, tzinfo=timezone.utc)

    approval = approval_from_files(
        evidence_json_path=str(manifest_path),
        terms_file_path=str(terms_path),
        reviewed_by="operator:diffen",
        reviewed_at=reviewed_at,
    )

    assert approval.contract_key == _manifest()["contract_key"]
    assert approval.transport == "SFTP"
    assert approval.usage_scope == "INTERNAL_ANALYSIS_AND_PAPER"
    assert approval.external_distribution is False
    assert approval.raw_storage_allowed is True
    assert approval.derived_storage_allowed is True
    assert approval.terms_checksum_sha256 == hashlib.sha256(
        terms_content
    ).hexdigest()
    assert approval.reviewed_at == reviewed_at


def test_approval_rejects_unknown_fields_relative_paths_and_symlinks(
    tmp_path,
    monkeypatch,
):
    terms_path = tmp_path / "agreement.pdf"
    terms_path.write_bytes(b"agreement")
    manifest_path = tmp_path / "approval.json"
    invalid = _manifest()
    invalid["private_key"] = "must-never-be-accepted"
    manifest_path.write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(ValueError, match="fields"):
        approval_from_files(
            evidence_json_path=str(manifest_path),
            terms_file_path=str(terms_path),
            reviewed_by="operator:diffen",
            reviewed_at=datetime.now(timezone.utc),
        )

    valid_path = tmp_path / "valid.json"
    valid_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="absolute"):
        approval_from_files(
            evidence_json_path="valid.json",
            terms_file_path=str(terms_path),
            reviewed_by="operator:diffen",
            reviewed_at=datetime.now(timezone.utc),
        )

    symlink_path = tmp_path / "terms-link"
    symlink_path.symlink_to(terms_path)
    with pytest.raises(ValueError, match="symlink"):
        approval_from_files(
            evidence_json_path=str(valid_path),
            terms_file_path=str(symlink_path),
            reviewed_by="operator:diffen",
            reviewed_at=datetime.now(timezone.utc),
        )


def test_validate_command_requires_all_legal_confirmations():
    parser = _parser()
    arguments = [
        "validate",
        "--evidence-json",
        "/secure/approval.json",
        "--terms-file",
        "/secure/agreement.pdf",
        "--operator",
        "operator:diffen",
    ]

    with pytest.raises(SystemExit):
        parser.parse_args(arguments)

    args = parser.parse_args(
        arguments
        + [
            "--confirm-internal-paper-use",
            "--confirm-raw-storage",
            "--confirm-derived-storage",
            "--confirm-host-key-independent",
        ]
    )
    assert args.command == "validate"
    assert args.confirm_host_key_independent is True


def test_revoke_command_requires_operator_and_bounded_reason():
    args = _parser().parse_args(
        [
            "revoke",
            "nasdaq-nordic-reference-xsto-v1",
            "--operator",
            "operator:diffen",
            "--reason",
            "Contract terminated by the operator.",
        ]
    )

    assert args.command == "revoke"
    assert args.operator == "operator:diffen"
    assert args.reason == "Contract terminated by the operator."
