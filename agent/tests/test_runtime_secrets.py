from pathlib import Path

import pytest

from src.runtime_secrets import RuntimeSecretError, read_runtime_secret


def _write_secret(path: Path, value: str, mode: int = 0o600) -> None:
    path.write_text(value)
    path.chmod(mode)


def test_runtime_secret_reads_one_bounded_value_from_secure_file(tmp_path):
    secret_file = tmp_path / "service-token"
    _write_secret(secret_file, "synthetic-token\n")

    value = read_runtime_secret(
        "SERVICE_TOKEN",
        environ={"SERVICE_TOKEN_FILE": str(secret_file)},
        required=True,
    )

    assert value == "synthetic-token"


def test_runtime_secret_rejects_inline_and_file_ambiguity(tmp_path):
    secret_file = tmp_path / "service-token"
    _write_secret(secret_file, "synthetic-token\n")

    with pytest.raises(RuntimeSecretError, match="both"):
        read_runtime_secret(
            "SERVICE_TOKEN",
            environ={
                "SERVICE_TOKEN": "inline-token",
                "SERVICE_TOKEN_FILE": str(secret_file),
            },
        )


def test_runtime_secret_rejects_symlink_open_permissions_and_multiline(
    tmp_path,
):
    secret_file = tmp_path / "service-token"
    _write_secret(secret_file, "synthetic-token\n", mode=0o640)

    with pytest.raises(RuntimeSecretError, match="permissions"):
        read_runtime_secret(
            "SERVICE_TOKEN",
            environ={"SERVICE_TOKEN_FILE": str(secret_file)},
        )

    secret_file.chmod(0o600)
    link = tmp_path / "service-token-link"
    link.symlink_to(secret_file)
    with pytest.raises(RuntimeSecretError, match="symlink"):
        read_runtime_secret(
            "SERVICE_TOKEN",
            environ={"SERVICE_TOKEN_FILE": str(link)},
        )

    _write_secret(secret_file, "first\nsecond\n")
    with pytest.raises(RuntimeSecretError, match="single line"):
        read_runtime_secret(
            "SERVICE_TOKEN",
            environ={"SERVICE_TOKEN_FILE": str(secret_file)},
        )


def test_optional_empty_secret_file_uses_default(tmp_path):
    secret_file = tmp_path / "optional-token"
    _write_secret(secret_file, "")

    assert read_runtime_secret(
        "OPTIONAL_TOKEN",
        environ={"OPTIONAL_TOKEN_FILE": str(secret_file)},
        default="disabled",
    ) == "disabled"


def test_optional_unmaterialized_secret_mount_uses_default(tmp_path):
    missing_mount = tmp_path / "optional-secret"

    assert read_runtime_secret(
        "OPTIONAL_TOKEN",
        environ={"OPTIONAL_TOKEN_FILE": str(missing_mount)},
        default="disabled",
    ) == "disabled"

    with pytest.raises(RuntimeSecretError, match="unavailable"):
        read_runtime_secret(
            "OPTIONAL_TOKEN",
            environ={"OPTIONAL_TOKEN_FILE": str(missing_mount)},
            required=True,
        )


def test_required_runtime_secret_rejects_missing_value():
    with pytest.raises(RuntimeSecretError, match="required"):
        read_runtime_secret(
            "SERVICE_TOKEN",
            environ={},
            required=True,
        )
