import hashlib
import json

import pytest

from src.historical_data_preflight import preflight_delivery


REQUIRED_ROLES = (
    "PROVIDER_TERMS",
    "UNIVERSE_HISTORY",
    "DAILY_OHLCV",
    "CORPORATE_ACTIONS",
    "OMXSGI_TOTAL_RETURN",
    "MARKET_CALENDAR",
)


def _delivery(tmp_path):
    files = []
    for role in REQUIRED_ROLES:
        path = tmp_path / f"{role.lower()}.dat"
        payload = f"{role}\nverified fixture\n".encode()
        path.write_bytes(payload)
        files.append({
            "role": role,
            "path": path.name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "media_type": "application/octet-stream",
        })
    manifest = {
        "schema_version": 1,
        "contract_key": "nasdaq-xsto-history-v1",
        "provider": "nasdaq",
        "product_name": "licensed-history-delivery",
        "terms_url": "https://example.test/terms",
        "mic": "XSTO",
        "period_start": "2024-01-01",
        "period_end": "2025-12-31",
        "data_cutoff": "2026-08-10T18:00:00Z",
        "declared_trading_sessions": 500,
        "benchmark_symbol": "OMXSGI",
        "risk_index_symbol": "OMXSGI",
        "prepared_by": "operator:test",
        "declarations": {
            "rights_confirmed_for_internal_backtest": True,
            "raw_storage_allowed": True,
            "derived_storage_allowed": True,
            "raw_unadjusted_prices": True,
            "corporate_actions_complete": True,
            "universe_history_complete": True,
            "includes_inactive_and_delisted": True,
            "benchmark_is_total_return": True,
        },
        "files": files,
    }
    manifest_path = tmp_path / "delivery.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, manifest


def test_preflight_accepts_complete_checksum_bound_delivery(tmp_path):
    manifest_path, _manifest = _delivery(tmp_path)

    result = preflight_delivery(manifest_path)

    assert result["ready_for_adapter_mapping"] is True
    assert result["ready_for_backtest"] is False
    assert result["contract_key"] == "nasdaq-xsto-history-v1"
    assert result["verified_roles"] == list(REQUIRED_ROLES)
    assert len(result["delivery_checksum_sha256"]) == 64


@pytest.mark.parametrize(
    "mutation",
    ("missing_role", "false_declaration", "wrong_checksum"),
)
def test_preflight_rejects_incomplete_or_unverified_delivery(
    tmp_path,
    mutation,
):
    manifest_path, manifest = _delivery(tmp_path)
    if mutation == "missing_role":
        manifest["files"].pop()
    elif mutation == "false_declaration":
        manifest["declarations"]["corporate_actions_complete"] = False
    else:
        manifest["files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError):
        preflight_delivery(manifest_path)


def test_preflight_rejects_paths_outside_delivery_and_symlinks(tmp_path):
    manifest_path, manifest = _delivery(tmp_path)
    manifest["files"][0]["path"] = "../outside.dat"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="relative delivery path"):
        preflight_delivery(manifest_path)

    manifest_path, manifest = _delivery(tmp_path)
    target = tmp_path / manifest["files"][0]["path"]
    target.unlink()
    target.symlink_to(tmp_path / manifest["files"][1]["path"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="regular file"):
        preflight_delivery(manifest_path)
