#!/usr/bin/env python3
"""Create and validate immutable Trading Agent release manifests."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import argparse
import os
import re
import sys
import tempfile


REQUIRED_SCHEMA = 43
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_PREFIX_PATTERN = re.compile(
    r"^ghcr\.io/[a-z0-9](?:[a-z0-9_.-]*/?)+[a-z0-9_.-]$"
)
_KEYS = (
    "RELEASE_SHA",
    "AGENT_IMAGE",
    "DASHBOARD_IMAGE",
    "SCHEMA_MIN",
    "SCHEMA_MAX",
    "CREATED_AT",
)


@dataclass(frozen=True)
class ReleaseManifest:
    release_sha: str
    agent_image: str
    dashboard_image: str
    schema_min: int
    schema_max: int
    created_at: str

    def as_environment(self) -> dict[str, str]:
        return {
            "RELEASE_SHA": self.release_sha,
            "AGENT_IMAGE": self.agent_image,
            "DASHBOARD_IMAGE": self.dashboard_image,
            "SCHEMA_MIN": str(self.schema_min),
            "SCHEMA_MAX": str(self.schema_max),
            "CREATED_AT": self.created_at,
        }


def write_manifest(
    path: Path,
    *,
    release_sha: str,
    agent_image: str,
    dashboard_image: str,
    schema_min: int,
    schema_max: int,
    created_at: str,
) -> ReleaseManifest:
    """Validate and atomically write a shell-safe environment manifest."""
    manifest = _validated_manifest(
        release_sha=release_sha,
        agent_image=agent_image,
        dashboard_image=dashboard_image,
        schema_min=schema_min,
        schema_max=schema_max,
        created_at=created_at,
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        f"{key}={manifest.as_environment()[key]}\n"
        for key in _KEYS
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return manifest


def read_manifest(
    path: Path,
    *,
    expected_image_prefix: str,
    expected_release_sha: str | None = None,
) -> ReleaseManifest:
    """Parse a manifest as data; never source or evaluate its contents."""
    values: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line or "=" not in line:
            raise ValueError("release manifest contains an invalid line")
        key, value = line.split("=", 1)
        if key in values:
            raise ValueError("release manifest contains a duplicate key")
        values[key] = value
    if set(values) != set(_KEYS):
        raise ValueError("release manifest keys do not match the contract")

    manifest = _validated_manifest(
        release_sha=values["RELEASE_SHA"],
        agent_image=values["AGENT_IMAGE"],
        dashboard_image=values["DASHBOARD_IMAGE"],
        schema_min=_integer(values["SCHEMA_MIN"], "SCHEMA_MIN"),
        schema_max=_integer(values["SCHEMA_MAX"], "SCHEMA_MAX"),
        created_at=values["CREATED_AT"],
    )
    prefix = _validated_prefix(expected_image_prefix)
    if manifest.agent_image.split("/agent@", 1)[0] != prefix:
        raise ValueError("agent image belongs to another repository")
    if manifest.dashboard_image.split("/dashboard@", 1)[0] != prefix:
        raise ValueError("dashboard image belongs to another repository")
    if (
        expected_release_sha is not None
        and manifest.release_sha != expected_release_sha
    ):
        raise ValueError("release manifest SHA does not match the request")
    return manifest


def _validated_manifest(
    *,
    release_sha: str,
    agent_image: str,
    dashboard_image: str,
    schema_min: int,
    schema_max: int,
    created_at: str,
) -> ReleaseManifest:
    if not isinstance(release_sha, str) or not _SHA_PATTERN.fullmatch(
        release_sha
    ):
        raise ValueError("RELEASE_SHA must be a lowercase 40-character SHA")
    if not isinstance(agent_image, str):
        raise ValueError("AGENT_IMAGE must be a string")
    if not isinstance(dashboard_image, str):
        raise ValueError("DASHBOARD_IMAGE must be a string")
    _validate_image(agent_image, "agent")
    _validate_image(dashboard_image, "dashboard")
    schema_min = _integer(schema_min, "SCHEMA_MIN")
    schema_max = _integer(schema_max, "SCHEMA_MAX")
    if not schema_min <= REQUIRED_SCHEMA <= schema_max:
        raise ValueError(
            f"schema range must include required schema {REQUIRED_SCHEMA}"
        )
    if not isinstance(created_at, str):
        raise ValueError("CREATED_AT must be a string")
    try:
        parsed_created_at = datetime.fromisoformat(
            created_at.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("CREATED_AT must be an ISO-8601 timestamp") from exc
    if (
        parsed_created_at.tzinfo is None
        or parsed_created_at.utcoffset() is None
    ):
        raise ValueError("CREATED_AT must include a timezone")
    return ReleaseManifest(
        release_sha=release_sha,
        agent_image=agent_image,
        dashboard_image=dashboard_image,
        schema_min=schema_min,
        schema_max=schema_max,
        created_at=created_at,
    )


def _validate_image(value: str, component: str) -> None:
    marker = f"/{component}@"
    if marker not in value:
        raise ValueError(
            f"{component} image must use an immutable digest reference"
        )
    prefix, digest = value.rsplit(marker, 1)
    _validated_prefix(prefix)
    if not _DIGEST_PATTERN.fullmatch(digest):
        raise ValueError(
            f"{component} image must use a sha256 digest"
        )


def _validated_prefix(value: str) -> str:
    if not isinstance(value, str) or not _PREFIX_PATTERN.fullmatch(value):
        raise ValueError("expected image prefix is invalid")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if not 1 <= parsed <= 9999:
        raise ValueError(f"{field} is outside the supported range")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--path", required=True, type=Path)
    create.add_argument("--release-sha", required=True)
    create.add_argument("--agent-image", required=True)
    create.add_argument("--dashboard-image", required=True)
    create.add_argument("--schema-min", required=True, type=int)
    create.add_argument("--schema-max", required=True, type=int)
    create.add_argument("--created-at", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--path", required=True, type=Path)
    validate.add_argument("--image-prefix", required=True)
    validate.add_argument("--release-sha")

    get_value = commands.add_parser("get")
    get_value.add_argument("--path", required=True, type=Path)
    get_value.add_argument("--image-prefix", required=True)
    get_value.add_argument("--release-sha")
    get_value.add_argument("--key", choices=_KEYS, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        write_manifest(
            args.path,
            release_sha=args.release_sha,
            agent_image=args.agent_image,
            dashboard_image=args.dashboard_image,
            schema_min=args.schema_min,
            schema_max=args.schema_max,
            created_at=args.created_at,
        )
        return 0

    manifest = read_manifest(
        args.path,
        expected_image_prefix=args.image_prefix,
        expected_release_sha=args.release_sha,
    )
    if args.command == "get":
        print(manifest.as_environment()[args.key])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"invalid release manifest: {error}", file=sys.stderr)
        sys.exit(2)
