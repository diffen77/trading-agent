#!/usr/bin/env python3
"""Validate production runtime secret paths without exposing their values."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat


REQUIRED_SECRET_PATHS = (
    "POSTGRES_PASSWORD_FILE",
    "DATABASE_URL_FILE",
    "DASHBOARD_AUTH_USERNAME_FILE",
    "DASHBOARD_AUTH_PASSWORD_FILE",
)
OPTIONAL_SECRET_PATHS = (
    "ANTHROPIC_API_KEY_FILE",
    "HERMES_API_KEY_FILE",
    "OPENAI_COMPATIBLE_API_KEY_FILE",
    "TELEGRAM_BOT_TOKEN_FILE",
    "TELEGRAM_CHAT_ID_FILE",
    "NEO4J_PASSWORD_FILE",
    "S3_ACCESS_KEY_ID_FILE",
    "S3_SECRET_ACCESS_KEY_FILE",
)
INLINE_SECRET_NAMES = {
    "DB_PASSWORD",
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
    "DASHBOARD_AUTH_USERNAME",
    "DASHBOARD_AUTH_PASSWORD",
    "ANTHROPIC_API_KEY",
    "HERMES_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "NEO4J_PASSWORD",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
}
_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_MAX_SECRET_BYTES = 16_384


def _read_locked_bytes(
    path: Path,
    *,
    label: str,
    allow_empty: bool,
    single_line: bool = True,
) -> bytes:
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ValueError(f"{label} is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"{label} must not be a symlink")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"{label} is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if metadata.st_uid != os.geteuid():
            raise ValueError(f"{label} must be owned by the deploy user")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError(
                f"{label} permissions must allow only the owner"
            )
        if metadata.st_size > _MAX_SECRET_BYTES:
            raise ValueError(f"{label} exceeds the size limit")
        payload = os.read(descriptor, _MAX_SECRET_BYTES + 1)
    finally:
        os.close(descriptor)

    if len(payload) > _MAX_SECRET_BYTES:
        raise ValueError(f"{label} exceeds the size limit")
    if single_line:
        if payload.endswith(b"\n"):
            payload = payload[:-1]
        if b"\n" in payload or b"\r" in payload:
            raise ValueError(f"{label} must contain a single line")
    try:
        decoded = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} must contain UTF-8") from error
    allowed_controls = {"\n"} if not single_line else set()
    if any(
        (ord(character) < 32 or ord(character) == 127)
        and character not in allowed_controls
        for character in decoded
    ):
        raise ValueError(f"{label} contains control characters")
    if not decoded and not allow_empty:
        raise ValueError(f"{label} must not be empty")
    return payload


def _read_runtime_env(path: Path) -> list[tuple[str, str]]:
    payload = _read_locked_bytes(
        path,
        label="runtime.env",
        allow_empty=True,
        single_line=False,
    )
    entries: list[tuple[str, str]] = []
    for line_number, raw_line in enumerate(
        payload.decode("utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"runtime.env line {line_number} is not KEY=VALUE"
            )
        key, value = line.split("=", 1)
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError(
                f"runtime.env line {line_number} has an invalid key"
            )
        entries.append((key, value))
    return entries


def read_runtime_secret_paths(path: Path) -> dict[str, Path]:
    """Return validated secret paths, never their contents."""
    known_path_names = set(REQUIRED_SECRET_PATHS + OPTIONAL_SECRET_PATHS)
    configured: dict[str, Path] = {}
    for key, value in _read_runtime_env(Path(path)):
        if key in INLINE_SECRET_NAMES:
            raise ValueError(
                f"runtime.env contains forbidden inline secret {key}"
            )
        if key not in known_path_names:
            continue
        if key in configured:
            raise ValueError(f"runtime.env contains duplicate key {key}")
        if not value or not _PATH_PATTERN.fullmatch(value):
            raise ValueError(f"{key} must be a literal absolute path")
        secret_path = Path(value)
        _read_locked_bytes(
            secret_path,
            label=key,
            allow_empty=False,
        )
        configured[key] = secret_path

    missing = sorted(set(REQUIRED_SECRET_PATHS) - set(configured))
    if missing:
        raise ValueError(
            "runtime.env is missing required secret paths: "
            + ", ".join(missing)
        )
    return configured


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument(
        "--get",
        choices=REQUIRED_SECRET_PATHS + OPTIONAL_SECRET_PATHS,
    )
    arguments = parser.parse_args()
    paths = read_runtime_secret_paths(arguments.path)
    if arguments.get and arguments.get in paths:
        print(paths[arguments.get])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"runtime secret configuration error: {error}", file=os.sys.stderr)
        raise SystemExit(2) from error
