import json

import pytest

from src.benchmark_admin import (
    _load_json_object,
    _parser,
    incident_from_mapping,
    registration_from_mapping,
)


def registration_mapping():
    return {
        "experiment_key": "xsto-forward-2026",
        "strategy_version": "momentum-report-swing-v1",
        "strategy_config_hash": "1" * 64,
        "release_sha": "a" * 40,
        "release_manifest_sha256": "2" * 64,
        "agent_image_digest_sha256": "3" * 64,
        "model_backend": "openai-compatible",
        "model_name": "frozen-model",
        "model_evidence_sha256": "4" * 64,
        "reference_snapshot_id": 7,
        "universe_checksum_sha256": "b" * 64,
        "quote_provider_contract_key": "xsto-quotes-v1",
        "execution_price_source": "LAST_TRADE_PLUS_BPS",
        "execution_provider_contract_key": "xsto-quotes-v1",
        "benchmark_provider_contract_key": "omxsgi-index-v1",
        "benchmark_source_url": (
            "https://indexes.nasdaqomx.com/Index/Overview/OMXSGI"
        ),
        "benchmark_terms_url": "https://example.test/terms",
        "fee_bps": 5,
        "spread_bps": 10,
        "slippage_bps": 5,
        "proposed_by": "operator:diffen",
    }


def incident_mapping():
    return {
        "incident_key": "feed-gap-2026-07-29",
        "session_date": "2026-07-29",
        "severity": "CRITICAL",
        "description": "Quote coverage fell below the threshold.",
        "detected_at": "2026-07-29T15:45:00Z",
        "source_checksum_sha256": "f" * 64,
    }


def test_cli_mapping_builders_are_strict_and_timezone_aware():
    registration = registration_from_mapping(registration_mapping())
    incident = incident_from_mapping(incident_mapping())

    assert registration.experiment_key == "xsto-forward-2026"
    assert incident.detected_at.utcoffset().total_seconds() == 0

    invalid_registration = registration_mapping()
    invalid_registration["unexpected"] = True
    with pytest.raises(ValueError):
        registration_from_mapping(invalid_registration)

    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "observe",
                "--experiment",
                "xsto-forward-2026",
                "--observation-json",
                "/tmp/observation.json",
            ]
        )


def test_json_loader_rejects_non_object_and_large_input(tmp_path):
    array_path = tmp_path / "array.json"
    array_path.write_text(json.dumps([]), encoding="utf-8")
    with pytest.raises(ValueError):
        _load_json_object(str(array_path))

    large_path = tmp_path / "large.json"
    large_path.write_text(
        json.dumps({"value": "x" * 1_000_001}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        _load_json_object(str(large_path))
