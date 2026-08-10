#!/usr/bin/env python3
"""Select Docker Compose profiles from a locked-down runtime.env file."""

import argparse
import os
from pathlib import Path
import stat
import sys


_PROFILE_FLAGS = (
    ("ENABLE_NASDAQ_PUBLIC_PRETRADE", "market-data"),
    ("ENABLE_NASDAQ_DELAYED_INGESTION", "market-data"),
    ("ENABLE_NASDAQ_REFERENCE_SYNC", "nasdaq-reference"),
    ("ENABLE_KNOWLEDGE_GRAPH", "knowledge-graph"),
    ("ENABLE_OBJECT_ARCHIVE", "object-storage"),
)
_FLAG_KEYS = frozenset(key for key, _ in _PROFILE_FLAGS)
_MAX_RUNTIME_ENV_BYTES = 1_000_000


def read_runtime_profiles(path: str | Path) -> tuple[str, ...]:
    """Return only profiles backed by an explicit literal true flag."""
    content = _read_secure_runtime_env(path)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("runtime.env must be valid UTF-8") from exc
    if text.startswith("\ufeff"):
        raise ValueError("runtime.env cannot contain a byte-order mark")

    flags: dict[str, bool] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            if stripped in _FLAG_KEYS:
                raise ValueError(
                    f"runtime profile flag on line {line_number} "
                    "must have an explicit value"
                )
            continue
        raw_key, raw_value = line.split("=", 1)
        key = raw_key.strip()
        if key not in _FLAG_KEYS:
            continue
        if key in flags:
            raise ValueError(
                f"runtime profile flag {key} is duplicated"
            )
        flags[key] = _parse_flag(raw_value, key)

    return tuple(dict.fromkeys(
        profile
        for key, profile in _PROFILE_FLAGS
        if flags.get(key, False)
    ))


def _read_secure_runtime_env(path_value: str | Path) -> bytes:
    path = Path(path_value)
    if not path.is_absolute():
        raise ValueError("runtime.env path must be absolute")
    if path.is_symlink():
        raise ValueError("runtime.env cannot be a symlink")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError("runtime.env is unavailable") from None
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("runtime.env must be a regular file")
        if file_stat.st_mode & 0o077:
            raise ValueError(
                "runtime.env permissions must deny group and other access"
            )
        if not 1 <= file_stat.st_size <= _MAX_RUNTIME_ENV_BYTES:
            raise ValueError("runtime.env size is invalid")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            content = handle.read(_MAX_RUNTIME_ENV_BYTES + 1)
        if len(content) != file_stat.st_size:
            raise ValueError("runtime.env changed while being read")
        return content
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parse_flag(raw_value: str, key: str) -> bool:
    value = raw_value.strip()
    if (
        len(value) >= 2
        and value[0] in {"'", '"'}
        and value[-1] == value[0]
    ):
        value = value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"runtime profile flag {key} must be true or false")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select safe Docker Compose runtime profiles.",
    )
    parser.add_argument("--path", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        profiles = read_runtime_profiles(args.path)
    except ValueError as exc:
        print(f"runtime profile error: {exc}", file=sys.stderr)
        return 2
    for profile in profiles:
        print(profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
