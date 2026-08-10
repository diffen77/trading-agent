"""Strict loading of runtime secrets from environment values or locked files."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
import re
import stat


_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class RuntimeSecretError(RuntimeError, ValueError):
    """Raised when a runtime secret is missing, ambiguous, or unsafe."""


class _RuntimeSecretFileMissing(RuntimeSecretError):
    pass


def _validate_value(
    name: str,
    value: str,
    *,
    required: bool,
    default: str | None,
    max_bytes: int,
) -> str | None:
    if len(value.encode("utf-8")) > max_bytes:
        raise RuntimeSecretError(f"{name} exceeds the size limit")
    if value.endswith("\n"):
        value = value[:-1]
    if "\n" in value or "\r" in value:
        raise RuntimeSecretError(f"{name} must be a single line")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise RuntimeSecretError(f"{name} contains control characters")
    if not value:
        if required:
            raise RuntimeSecretError(f"{name} is required")
        return default
    return value


def _read_locked_file(name: str, path_value: str, max_bytes: int) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        raise RuntimeSecretError(f"{name}_FILE must be absolute")
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise _RuntimeSecretFileMissing(
            f"{name}_FILE is unavailable"
        ) from error
    except OSError as error:
        raise RuntimeSecretError(f"{name}_FILE is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeSecretError(f"{name}_FILE must not be a symlink")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as error:
        raise _RuntimeSecretFileMissing(
            f"{name}_FILE is unavailable"
        ) from error
    except OSError as error:
        raise RuntimeSecretError(f"{name}_FILE is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeSecretError(f"{name}_FILE must be a regular file")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise RuntimeSecretError(
                f"{name}_FILE permissions must allow only the owner"
            )
        if metadata.st_size > max_bytes:
            raise RuntimeSecretError(f"{name}_FILE exceeds the size limit")
        payload = os.read(descriptor, max_bytes + 1)
        if len(payload) > max_bytes:
            raise RuntimeSecretError(f"{name}_FILE exceeds the size limit")
    finally:
        os.close(descriptor)
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimeSecretError(f"{name}_FILE must contain UTF-8") from error


def read_runtime_secret(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    required: bool = False,
    default: str | None = None,
    max_bytes: int = 16_384,
) -> str | None:
    """Read one bounded secret while rejecting ambiguous configuration."""
    if not _NAME_PATTERN.fullmatch(name):
        raise RuntimeSecretError("secret name is invalid")
    if max_bytes < 1:
        raise RuntimeSecretError("max_bytes must be positive")

    values = os.environ if environ is None else environ
    inline_present = name in values
    file_name = f"{name}_FILE"
    file_present = file_name in values and bool(values[file_name])
    if inline_present and file_present:
        raise RuntimeSecretError(
            f"{name} and {file_name} are both configured"
        )

    if file_present:
        try:
            value = _read_locked_file(name, values[file_name], max_bytes)
        except _RuntimeSecretFileMissing:
            if required:
                raise
            return default
    elif inline_present:
        value = values[name]
    else:
        if required:
            raise RuntimeSecretError(f"{name} is required")
        return default

    return _validate_value(
        name,
        value,
        required=required,
        default=default,
        max_bytes=max_bytes,
    )
