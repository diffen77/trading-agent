"""Validate a licensed historical-data delivery before adapter work."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
from typing import Any


_MANIFEST_FIELDS = frozenset({
    "schema_version",
    "contract_key",
    "provider",
    "product_name",
    "terms_url",
    "mic",
    "period_start",
    "period_end",
    "data_cutoff",
    "declared_trading_sessions",
    "benchmark_symbol",
    "risk_index_symbol",
    "prepared_by",
    "declarations",
    "files",
})
_DECLARATION_FIELDS = frozenset({
    "rights_confirmed_for_internal_backtest",
    "raw_storage_allowed",
    "derived_storage_allowed",
    "raw_unadjusted_prices",
    "corporate_actions_complete",
    "universe_history_complete",
    "includes_inactive_and_delisted",
    "benchmark_is_total_return",
})
_FILE_FIELDS = frozenset({"role", "path", "sha256", "media_type"})
_REQUIRED_ROLES = (
    "PROVIDER_TERMS",
    "UNIVERSE_HISTORY",
    "DAILY_OHLCV",
    "CORPORATE_ACTIONS",
    "OMXSGI_TOTAL_RETURN",
    "MARKET_CALENDAR",
)
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,99}$")
_TEXT_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")
_MEDIA_TYPE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}$"
)
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OPERATOR_PATTERN = re.compile(r"^operator:[A-Za-z0-9._-]{1,80}$")
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_ARTIFACT_BYTES = 10_000_000_000
_HASH_CHUNK_BYTES = 1024 * 1024


def preflight_delivery(manifest_path: Path | str) -> dict[str, Any]:
    """Verify bounded raw artifacts without importing or mapping them."""
    source = _regular_file(
        Path(manifest_path),
        label="delivery manifest",
        maximum=_MAX_MANIFEST_BYTES,
        require_absolute=True,
    )
    try:
        manifest = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("delivery manifest must be UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS:
        raise ValueError("delivery manifest fields do not match")

    _validate_manifest_metadata(manifest)
    declarations = manifest["declarations"]
    if (
        not isinstance(declarations, dict)
        or set(declarations) != _DECLARATION_FIELDS
        or any(value is not True for value in declarations.values())
    ):
        raise ValueError("delivery declarations are incomplete")

    raw_files = manifest["files"]
    if not isinstance(raw_files, list):
        raise ValueError("delivery files must be a list")
    by_role: dict[str, dict[str, str]] = {}
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or set(raw_file) != _FILE_FIELDS:
            raise ValueError("delivery file fields do not match")
        role = raw_file["role"]
        if role not in _REQUIRED_ROLES or role in by_role:
            raise ValueError("delivery file role is missing or duplicated")
        by_role[role] = raw_file
    if set(by_role) != set(_REQUIRED_ROLES):
        raise ValueError("delivery is missing a required artifact role")

    root = source.parent.resolve()
    artifact_evidence = []
    for role in _REQUIRED_ROLES:
        raw_file = by_role[role]
        relative_path = _relative_delivery_path(raw_file["path"])
        media_type = raw_file["media_type"]
        checksum = raw_file["sha256"]
        if (
            not isinstance(media_type, str)
            or _MEDIA_TYPE_PATTERN.fullmatch(media_type) is None
        ):
            raise ValueError("delivery media_type is invalid")
        if (
            not isinstance(checksum, str)
            or _CHECKSUM_PATTERN.fullmatch(checksum) is None
        ):
            raise ValueError("delivery checksum is invalid")
        candidate = root / relative_path
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            raise ValueError("delivery artifact is unavailable") from None
        if not resolved.is_relative_to(root):
            raise ValueError(
                "artifact path must be a relative delivery path"
            )
        artifact = _regular_file(
            candidate,
            label="delivery artifact",
            maximum=_MAX_ARTIFACT_BYTES,
            require_absolute=True,
        )
        observed_checksum = _sha256_file(artifact)
        if observed_checksum != checksum:
            raise ValueError("delivery artifact checksum does not match")
        artifact_evidence.append({
            "role": role,
            "media_type": media_type,
            "bytes": artifact.stat().st_size,
            "sha256": observed_checksum,
        })

    canonical_manifest = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "ready_for_adapter_mapping": True,
        "ready_for_backtest": False,
        "contract_key": manifest["contract_key"],
        "verified_roles": list(_REQUIRED_ROLES),
        "artifacts": artifact_evidence,
        "delivery_checksum_sha256": hashlib.sha256(
            canonical_manifest
        ).hexdigest(),
        "next_step": "implement and review the selected provider format adapter",
    }


def _validate_manifest_metadata(manifest: dict[str, Any]) -> None:
    if manifest["schema_version"] != 1:
        raise ValueError("delivery schema_version must be 1")
    if (
        not isinstance(manifest["contract_key"], str)
        or _KEY_PATTERN.fullmatch(manifest["contract_key"]) is None
    ):
        raise ValueError("delivery contract_key is invalid")
    for field in ("provider", "product_name"):
        value = manifest[field]
        if not isinstance(value, str) or _TEXT_PATTERN.fullmatch(value) is None:
            raise ValueError(f"delivery {field} is invalid")
    if (
        not isinstance(manifest["terms_url"], str)
        or not manifest["terms_url"].startswith("https://")
        or len(manifest["terms_url"]) > 2000
    ):
        raise ValueError("delivery terms_url must use HTTPS")
    if manifest["mic"] != "XSTO":
        raise ValueError("delivery mic must be XSTO")
    try:
        period_start = date.fromisoformat(manifest["period_start"])
        period_end = date.fromisoformat(manifest["period_end"])
        cutoff = datetime.fromisoformat(
            manifest["data_cutoff"].replace("Z", "+00:00")
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("delivery dates must use ISO-8601") from exc
    if (
        period_end < period_start
        or cutoff.tzinfo is None
        or cutoff.utcoffset() is None
        or period_end > cutoff.date()
    ):
        raise ValueError("delivery period or cutoff is invalid")
    sessions = manifest["declared_trading_sessions"]
    if (
        not isinstance(sessions, int)
        or isinstance(sessions, bool)
        or sessions < 315
    ):
        raise ValueError("delivery must declare at least 315 sessions")
    if manifest["benchmark_symbol"] != "OMXSGI":
        raise ValueError("delivery benchmark must be OMXSGI")
    risk_symbol = manifest["risk_index_symbol"]
    if risk_symbol != "OMXSGI":
        raise ValueError("delivery risk index must be OMXSGI in schema 1")
    if (
        not isinstance(manifest["prepared_by"], str)
        or _OPERATOR_PATTERN.fullmatch(manifest["prepared_by"]) is None
    ):
        raise ValueError("delivery prepared_by must start with operator:")


def _relative_delivery_path(value: Any) -> Path:
    if not isinstance(value, str) or not value or len(value) > 500:
        raise ValueError("artifact path must be a relative delivery path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("artifact path must be a relative delivery path")
    return path


def _regular_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    require_absolute: bool,
) -> Path:
    if require_absolute and not path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    try:
        file_stat = path.lstat()
    except OSError:
        raise ValueError(f"{label} is unavailable") from None
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_size <= 0
        or file_stat.st_size > maximum
    ):
        raise ValueError(f"{label} must be a bounded regular file")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a licensed historical-data delivery.",
    )
    parser.add_argument("--manifest", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = preflight_delivery(Path(args.manifest))
    except Exception as exc:
        print(f"historical data preflight error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
