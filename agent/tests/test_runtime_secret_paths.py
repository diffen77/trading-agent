from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "ops" / "release" / "runtime_secrets.py"


def _load_module():
    spec = spec_from_file_location("runtime_secrets", MODULE_PATH)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write(path: Path, content: str, mode: int = 0o600) -> None:
    path.write_text(content)
    path.chmod(mode)


def _runtime_env(tmp_path: Path) -> Path:
    values = {
        "POSTGRES_PASSWORD_FILE": "db-password",
        "DATABASE_URL_FILE": "database-url",
        "DASHBOARD_AUTH_USERNAME_FILE": "dashboard-user",
        "DASHBOARD_AUTH_PASSWORD_FILE": "dashboard-password",
        "OPERATIONS_READ_TOKEN_FILE": "operations-read-token",
    }
    lines = []
    for key, file_name in values.items():
        secret_file = tmp_path / file_name
        _write(secret_file, f"synthetic-{file_name}\n")
        lines.append(f"{key}={secret_file}\n")
    runtime_env = tmp_path / "runtime.env"
    _write(runtime_env, "".join(lines))
    return runtime_env


def test_release_runtime_secret_paths_are_absolute_locked_and_complete(
    tmp_path,
):
    module = _load_module()
    runtime_env = _runtime_env(tmp_path)

    paths = module.read_runtime_secret_paths(runtime_env)

    assert set(paths) == {
        "POSTGRES_PASSWORD_FILE",
        "DATABASE_URL_FILE",
        "DASHBOARD_AUTH_USERNAME_FILE",
        "DASHBOARD_AUTH_PASSWORD_FILE",
        "OPERATIONS_READ_TOKEN_FILE",
    }
    assert all(path.is_absolute() for path in paths.values())


def test_release_runtime_secrets_reject_inline_values_and_symlinks(tmp_path):
    module = _load_module()
    runtime_env = _runtime_env(tmp_path)
    runtime_env.write_text(
        runtime_env.read_text() + "DB_PASSWORD=inline-secret\n"
    )
    runtime_env.chmod(0o600)

    with pytest.raises(ValueError, match="inline secret"):
        module.read_runtime_secret_paths(runtime_env)

    runtime_env = _runtime_env(tmp_path)
    database_url = tmp_path / "database-url"
    database_url.unlink()
    target = tmp_path / "database-url-target"
    _write(target, "postgresql://synthetic\n")
    database_url.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        module.read_runtime_secret_paths(runtime_env)


def test_release_runtime_secrets_reject_missing_or_open_required_file(
    tmp_path,
):
    module = _load_module()
    runtime_env = _runtime_env(tmp_path)
    (tmp_path / "database-url").chmod(0o640)

    with pytest.raises(ValueError, match="permissions"):
        module.read_runtime_secret_paths(runtime_env)

    (tmp_path / "database-url").unlink()
    with pytest.raises(ValueError, match="unavailable"):
        module.read_runtime_secret_paths(runtime_env)
