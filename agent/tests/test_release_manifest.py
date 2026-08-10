from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import os
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "ops" / "release" / "release_manifest.py"
RUNTIME_PROFILES_PATH = ROOT / "ops" / "release" / "runtime_profiles.py"


def _load_module():
    spec = spec_from_file_location("release_manifest", MODULE_PATH)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_runtime_profiles():
    spec = spec_from_file_location(
        "runtime_profiles",
        RUNTIME_PROFILES_PATH,
    )
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_runtime_env(path, content):
    path.write_text(content)
    path.chmod(0o600)


def _runtime_secret_env_content(tmp_path, extra=""):
    values = {
        "POSTGRES_PASSWORD_FILE": (
            "postgres-password",
            "test-only\n",
        ),
        "DATABASE_URL_FILE": (
            "database-url",
            "postgresql://trading:test-only@db:5432/trading_agent\n",
        ),
        "DASHBOARD_AUTH_USERNAME_FILE": (
            "dashboard-username",
            "operator\n",
        ),
        "DASHBOARD_AUTH_PASSWORD_FILE": (
            "dashboard-password",
            "test-only-dashboard-password\n",
        ),
        "S3_ACCESS_KEY_ID_FILE": (
            "s3-access-key-id",
            "test-only-s3-access\n",
        ),
        "S3_SECRET_ACCESS_KEY_FILE": (
            "s3-secret-access-key",
            "test-only-s3-secret\n",
        ),
    }
    lines = []
    for key, (file_name, value) in values.items():
        path = tmp_path / file_name
        path.write_text(value)
        path.chmod(0o600)
        lines.append(f"{key}={path}\n")
    return "".join(lines) + extra


def _values():
    return {
        "release_sha": "a" * 40,
        "agent_image": (
            "ghcr.io/diffen77/trading-agent/agent@sha256:" + "b" * 64
        ),
        "dashboard_image": (
            "ghcr.io/diffen77/trading-agent/dashboard@sha256:" + "c" * 64
        ),
        "schema_min": 43,
        "schema_max": 43,
        "created_at": "2026-07-29T12:00:00Z",
    }


def test_agent_runtime_image_excludes_test_only_dependencies_and_state():
    dockerignore = (ROOT / "agent" / ".dockerignore").read_text().splitlines()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "tests" in dockerignore
    assert "requirements-test.txt" in dockerignore
    assert ".hypothesis" in dockerignore
    assert "*.pem" in dockerignore
    assert "*.key" in dockerignore
    assert "known_hosts" in dockerignore
    assert 'pip install --upgrade "pip>=26.2,<27"' in ci
    assert "python -m pip_audit -r requirements-test.txt" in ci


def test_nasdaq_reference_sync_is_isolated_and_fail_closed_in_compose():
    compose = (ROOT / "docker-compose.yml").read_text()

    service = compose.split("  nasdaq-alias-sync:", 1)[1].split(
        "\n  dashboard:",
        1,
    )[0]
    assert 'profiles: ["nasdaq-reference"]' in service
    assert (
        "ENABLE_NASDAQ_REFERENCE_SYNC: "
        "${ENABLE_NASDAQ_REFERENCE_SYNC:-false}"
    ) in service
    assert (
        "NASDAQ_REFERENCE_CONTRACT_KEY: "
        "${NASDAQ_REFERENCE_CONTRACT_KEY:-}"
    ) in service
    assert (
        "NASDAQ_NDL_PRIVATE_KEY_FILE: "
        "/run/nasdaq-ndl/private_key"
    ) in service
    assert (
        "NASDAQ_NDL_KNOWN_HOSTS_FILE: "
        "/run/nasdaq-ndl/known_hosts"
    ) in service
    assert (
        "NASDAQ_REFERENCE_SYNC_INTERVAL_SECONDS: "
        "${NASDAQ_REFERENCE_SYNC_INTERVAL_SECONDS:-3600}"
    ) in service
    assert service.count("read_only: true") == 2
    assert "NASDAQ_NDL_PASSWORD" not in service


def test_runtime_profiles_parse_only_explicit_strict_flags(tmp_path):
    module = _load_runtime_profiles()
    runtime_env = tmp_path / "runtime.env"
    _write_runtime_env(
        runtime_env,
        (
            "# Unrelated values are never evaluated\n"
            "DB_PASSWORD=literal-$-value\n"
            "ENABLE_NASDAQ_DELAYED_INGESTION=true\n"
            "ENABLE_NASDAQ_REFERENCE_SYNC='false'\n"
        ),
    )

    assert module.read_runtime_profiles(runtime_env) == ("market-data",)


def test_runtime_profiles_enable_public_pretrade_once(tmp_path):
    module = _load_runtime_profiles()
    runtime_env = tmp_path / "runtime.env"
    _write_runtime_env(
        runtime_env,
        (
            "ENABLE_NASDAQ_PUBLIC_PRETRADE=true\n"
            "ENABLE_NASDAQ_DELAYED_INGESTION=false\n"
            "ENABLE_NASDAQ_REFERENCE_SYNC=false\n"
        ),
    )

    assert module.read_runtime_profiles(runtime_env) == ("market-data",)

    _write_runtime_env(
        runtime_env,
        (
            "ENABLE_NASDAQ_PUBLIC_PRETRADE=true\n"
            "ENABLE_NASDAQ_DELAYED_INGESTION=true\n"
        ),
    )
    assert module.read_runtime_profiles(runtime_env) == ("market-data",)


def test_runtime_profiles_enable_the_trading_knowledge_graph(tmp_path):
    module = _load_runtime_profiles()
    runtime_env = tmp_path / "runtime.env"
    _write_runtime_env(
        runtime_env,
        (
            "ENABLE_KNOWLEDGE_GRAPH=true\n"
            "ENABLE_NASDAQ_PUBLIC_PRETRADE=false\n"
        ),
    )

    assert module.read_runtime_profiles(runtime_env) == ("knowledge-graph",)


def test_runtime_profiles_enable_the_isolated_object_archive(tmp_path):
    module = _load_runtime_profiles()
    runtime_env = tmp_path / "runtime.env"
    _write_runtime_env(
        runtime_env,
        (
            "ENABLE_OBJECT_ARCHIVE=true\n"
            "ENABLE_NASDAQ_PUBLIC_PRETRADE=false\n"
        ),
    )

    assert module.read_runtime_profiles(runtime_env) == ("object-storage",)


@pytest.mark.parametrize(
    "content",
    [
        (
            "ENABLE_NASDAQ_DELAYED_INGESTION=true\n"
            "ENABLE_NASDAQ_DELAYED_INGESTION=false\n"
        ),
        "ENABLE_NASDAQ_DELAYED_INGESTION=TRUE\n",
        "ENABLE_NASDAQ_DELAYED_INGESTION=${UNTRUSTED}\n",
        "ENABLE_NASDAQ_DELAYED_INGESTION=true # ambiguous\n",
        "ENABLE_NASDAQ_REFERENCE_SYNC\n",
    ],
)
def test_runtime_profiles_reject_ambiguous_flags(tmp_path, content):
    module = _load_runtime_profiles()
    runtime_env = tmp_path / "runtime.env"
    _write_runtime_env(runtime_env, content)

    with pytest.raises(ValueError):
        module.read_runtime_profiles(runtime_env)


def test_runtime_profiles_reject_symlink_and_open_permissions(tmp_path):
    module = _load_runtime_profiles()
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text("ENABLE_NASDAQ_REFERENCE_SYNC=false\n")
    runtime_env.chmod(0o640)

    with pytest.raises(ValueError, match="permissions"):
        module.read_runtime_profiles(runtime_env)

    runtime_env.chmod(0o600)
    link = tmp_path / "runtime-link.env"
    link.symlink_to(runtime_env)
    with pytest.raises(ValueError, match="symlink"):
        module.read_runtime_profiles(link)


def test_release_manifest_round_trip_is_strict_and_shell_safe(tmp_path):
    module = _load_module()
    manifest_path = tmp_path / "release.env"

    module.write_manifest(manifest_path, **_values())
    manifest = module.read_manifest(
        manifest_path,
        expected_image_prefix="ghcr.io/diffen77/trading-agent",
        expected_release_sha="a" * 40,
    )

    assert manifest.release_sha == "a" * 40
    assert manifest.schema_min == 43
    assert manifest.schema_max == 43
    assert manifest.agent_image.endswith("b" * 64)
    assert manifest_path.read_text().splitlines() == [
        f"RELEASE_SHA={'a' * 40}",
        (
            "AGENT_IMAGE=ghcr.io/diffen77/trading-agent/"
            f"agent@sha256:{'b' * 64}"
        ),
        (
            "DASHBOARD_IMAGE=ghcr.io/diffen77/trading-agent/"
            f"dashboard@sha256:{'c' * 64}"
        ),
        "SCHEMA_MIN=43",
        "SCHEMA_MAX=43",
        "CREATED_AT=2026-07-29T12:00:00Z",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "agent_image",
            "ghcr.io/diffen77/trading-agent/agent:latest",
        ),
        ("release_sha", "main"),
        ("schema_max", 30),
        ("created_at", "not-a-timestamp"),
    ],
)
def test_release_manifest_rejects_mutable_or_incompatible_values(
    tmp_path,
    field,
    value,
):
    module = _load_module()
    values = _values()
    values[field] = value

    with pytest.raises(ValueError):
        module.write_manifest(tmp_path / "release.env", **values)


def test_release_manifest_rejects_unknown_or_duplicate_keys(tmp_path):
    module = _load_module()
    manifest_path = tmp_path / "release.env"
    module.write_manifest(manifest_path, **_values())
    original = manifest_path.read_text()

    manifest_path.write_text(original + "UNKNOWN=value\n")
    with pytest.raises(ValueError, match="keys"):
        module.read_manifest(
            manifest_path,
            expected_image_prefix="ghcr.io/diffen77/trading-agent",
        )

    manifest_path.write_text(original + f"RELEASE_SHA={'a' * 40}\n")
    with pytest.raises(ValueError, match="duplicate"):
        module.read_manifest(
            manifest_path,
            expected_image_prefix="ghcr.io/diffen77/trading-agent",
        )


def test_release_manifest_rejects_image_from_another_repository(tmp_path):
    module = _load_module()
    manifest_path = tmp_path / "release.env"
    module.write_manifest(manifest_path, **_values())

    with pytest.raises(ValueError, match="repository"):
        module.read_manifest(
            manifest_path,
            expected_image_prefix="ghcr.io/attacker/project",
        )


def test_deployment_workflows_require_immutable_release_and_approval():
    build = (ROOT / ".github/workflows/build-push.yml").read_text()
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text()
    compose = (
        ROOT / "ops/release/compose.production.yml"
    ).read_text()
    local_compose = (ROOT / "docker-compose.yml").read_text()

    assert ":latest" not in build
    assert "steps.agent.outputs.digest" in build
    assert "steps.dashboard.outputs.digest" in build
    assert "actions/attest@v4" in build
    assert "actions/upload-artifact@v4" in build
    assert "--schema-min 44" in build
    assert "--schema-max 44" in build
    assert "workflow_dispatch:" in deploy
    assert "environment: production" in deploy
    assert "concurrency:" in deploy
    assert "actions/download-artifact@v5" in deploy
    assert "run-id:" in deploy
    assert "ssh-keyscan" not in deploy
    assert "DEPLOY_KNOWN_HOSTS" in deploy
    assert 'cp ops/release/runtime_profiles.py "$bundle/ops/release/"' in deploy
    assert "image: ${AGENT_IMAGE:?" in compose
    assert "image: ${DASHBOARD_IMAGE:?" in compose
    assert "build:" not in compose
    assert "./agent:/app" not in compose
    assert "  monitor:" in compose
    assert '["python", "-m", "src.operational_monitor", "daemon"]' in compose
    assert "  learning-worker:" in compose
    assert '["python", "-m", "src.learning_worker", "daemon"]' in compose
    assert "  knowledge-worker:" in compose
    assert '["python", "-m", "src.knowledge_worker", "daemon"]' in compose
    assert 'profiles: ["knowledge-graph"]' in compose
    assert "  object-archive-worker:" in compose
    assert '["python", "-m", "src.object_archive", "daemon"]' in compose
    assert 'profiles: ["object-storage"]' in compose
    assert "  nasdaq-alias-sync:" in compose
    assert 'profiles: ["nasdaq-reference"]' in compose
    assert "  monitor:" in local_compose
    assert "  learning-worker:" in local_compose
    assert "  knowledge-worker:" in local_compose
    assert "  object-archive-worker:" in local_compose
    object_service = local_compose.split(
        "  object-archive-worker:",
        1,
    )[1].split("\n  monitor:", 1)[0]
    assert "S3_ENDPOINT: ${S3_ENDPOINT:-}" in object_service
    assert "S3_ACCESS_KEY_ID: ${S3_ACCESS_KEY_ID:-}" in object_service
    assert "S3_SECRET_ACCESS_KEY: ${S3_SECRET_ACCESS_KEY:-}" in object_service
    assert "S3_BUCKET: ${S3_BUCKET:-}" in object_service
    assert "S3_PREFIX" not in object_service


def test_production_release_uses_only_runtime_secret_mounts():
    compose = (
        ROOT / "ops/release/compose.production.yml"
    ).read_text()
    deploy = (ROOT / "ops/release/deploy.sh").read_text()
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    migrate = (ROOT / "db/migrate.sh").read_text()

    for inline_name in (
        "DB_PASSWORD:",
        "DATABASE_URL:",
        "DASHBOARD_AUTH_USERNAME:",
        "DASHBOARD_AUTH_PASSWORD:",
        "ANTHROPIC_API_KEY:",
        "HERMES_API_KEY:",
        "OPENAI_COMPATIBLE_API_KEY:",
        "TELEGRAM_BOT_TOKEN:",
        "TELEGRAM_CHAT_ID:",
        "NEO4J_PASSWORD:",
        "S3_ACCESS_KEY_ID:",
        "S3_SECRET_ACCESS_KEY:",
    ):
        assert inline_name not in compose

    assert "POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password" in compose
    assert "DATABASE_URL_FILE: /run/secrets/database_url" in compose
    assert "HERMES_API_KEY_FILE: /run/secrets/hermes_api_key" in compose
    assert "environment: TRADING_AGENT_HERMES_API_KEY_SECRET" in compose
    assert "NEO4J_PASSWORD_FILE: /run/secrets/neo4j_password" in compose
    assert "environment: TRADING_AGENT_NEO4J_PASSWORD_SECRET" in compose
    assert "S3_ACCESS_KEY_ID_FILE: /run/secrets/s3_access_key_id" in compose
    assert (
        "S3_SECRET_ACCESS_KEY_FILE: /run/secrets/s3_secret_access_key"
        in compose
    )
    assert "environment: TRADING_AGENT_S3_ACCESS_KEY_ID_SECRET" in compose
    assert "environment: TRADING_AGENT_S3_SECRET_ACCESS_KEY_SECRET" in compose
    assert (
        "DASHBOARD_AUTH_PASSWORD_FILE: "
        "/run/secrets/dashboard_auth_password"
    ) in compose
    assert "environment: TRADING_AGENT_DATABASE_URL_SECRET" in compose
    assert "runtime_secrets.py" in deploy
    assert "TRADING_AGENT_DATABASE_URL_SECRET" in deploy
    assert 'cp ops/release/runtime_secrets.py "$bundle/ops/release/"' in workflow
    assert "DATABASE_URL_FILE" in migrate
    assert ". \"$DATABASE_URL_FILE\"" not in migrate
    assert "--uid 10001" in (ROOT / "agent/Dockerfile").read_text()
    assert (
        'CMD ["node", "runtime-entrypoint.mjs"]'
        in (ROOT / "dashboard/Dockerfile").read_text()
    )


def _create_release(root, release_sha):
    module = _load_module()
    release = root / "releases" / release_sha
    (release / "ops/release").mkdir(parents=True)
    (release / "db").mkdir()
    shutil.copy2(
        ROOT / "ops/release/release_manifest.py",
        release / "ops/release/release_manifest.py",
    )
    (release / "ops/release/compose.production.yml").write_text(
        "services: {}\n"
    )
    (release / "db/migrate.sh").write_text("#!/bin/sh\n")
    values = _values()
    values["release_sha"] = release_sha
    module.write_manifest(release / "release.env", **values)


def _fake_docker(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
  *" exec -T db psql "*) printf '43\n' ;;
  *" exec -T agent python -m src.healthcheck readiness"*)
    if [ -n "$FAIL_SHA" ]; then
      case "$*" in
        *"$FAIL_SHA"*) exit 1 ;;
      esac
    fi
    ;;
esac
exit 0
"""
    )
    docker.chmod(0o755)
    sleep = fake_bin / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    return fake_bin


def _run_deploy(tmp_path, requested_sha, *, fail_sha=""):
    log = tmp_path / "docker.log"
    fake_bin = _fake_docker(tmp_path)
    environ = os.environ.copy()
    environ.update(
        {
            "PATH": f"{fake_bin}:{environ['PATH']}",
            "FAKE_DOCKER_LOG": str(log),
            "FAIL_SHA": fail_sha,
        }
    )
    result = subprocess.run(
        [
            "/bin/sh",
            str(ROOT / "ops/release/deploy.sh"),
            str(tmp_path),
            requested_sha,
            "ghcr.io/diffen77/trading-agent",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environ,
    )
    return result, log.read_text() if log.exists() else ""


def test_deploy_rejects_unsafe_runtime_env_before_docker(tmp_path):
    release_sha = "7" * 40
    _create_release(tmp_path, release_sha)
    (tmp_path / "runtime.env").write_text(
        "ENABLE_NASDAQ_DELAYED_INGESTION=true\n"
    )

    result, log = _run_deploy(tmp_path, release_sha)

    assert result.returncode == 2
    assert "permissions" in result.stderr
    assert log == ""
    assert not (tmp_path / "current-release").exists()


def test_deploy_promotes_only_after_smoke_passes(tmp_path):
    old_sha = "1" * 40
    new_sha = "2" * 40
    _create_release(tmp_path, old_sha)
    _create_release(tmp_path, new_sha)
    _write_runtime_env(
        tmp_path / "runtime.env",
        _runtime_secret_env_content(tmp_path),
    )
    (tmp_path / "current-release").write_text(f"{old_sha}\n")

    result, log = _run_deploy(tmp_path, new_sha)

    assert result.returncode == 0
    assert (tmp_path / "current-release").read_text().strip() == new_sha
    assert (tmp_path / "previous-release").read_text().strip() == old_sha
    assert new_sha in log
    assert "test-only" not in log
    assert " pull db agent dashboard monitor" in log
    assert " up -d agent dashboard learning-worker monitor" in log
    assert "--profile market-data pull" not in log
    assert "--profile market-data up" not in log
    assert "--profile nasdaq-reference pull" not in log
    assert "--profile nasdaq-reference up" not in log
    assert "--profile market-data stop market-sync" in log
    assert "--profile nasdaq-reference stop nasdaq-alias-sync" in log
    assert (
        "--profile market-data --profile nasdaq-reference "
        "stop universe-sync"
    ) in log


def test_deploy_activates_only_explicit_runtime_profiles(tmp_path):
    old_sha = "5" * 40
    new_sha = "6" * 40
    _create_release(tmp_path, old_sha)
    _create_release(tmp_path, new_sha)
    _write_runtime_env(
        tmp_path / "runtime.env",
        _runtime_secret_env_content(
            tmp_path,
            (
                "ENABLE_NASDAQ_DELAYED_INGESTION=true\n"
                "ENABLE_NASDAQ_REFERENCE_SYNC=true\n"
                "ENABLE_OBJECT_ARCHIVE=true\n"
                "S3_ENDPOINT=http://100.116.226.27:3900\n"
                "S3_BUCKET=orders\n"
            ),
        ),
    )
    (tmp_path / "current-release").write_text(f"{old_sha}\n")

    result, log = _run_deploy(tmp_path, new_sha)

    assert result.returncode == 0
    assert (
        "--profile market-data pull universe-sync market-sync"
        in log
    )
    assert (
        "--profile market-data up -d universe-sync market-sync"
        in log
    )
    assert (
        "--profile nasdaq-reference pull universe-sync nasdaq-alias-sync"
        in log
    )
    assert (
        "--profile nasdaq-reference up -d universe-sync nasdaq-alias-sync"
        in log
    )
    assert (
        "--profile object-storage pull object-archive-worker"
        in log
    )
    assert (
        "--profile object-storage up -d object-archive-worker"
        in log
    )


def test_failed_release_rolls_images_back_and_keeps_current_pointer(tmp_path):
    old_sha = "3" * 40
    bad_sha = "4" * 40
    _create_release(tmp_path, old_sha)
    _create_release(tmp_path, bad_sha)
    _write_runtime_env(
        tmp_path / "runtime.env",
        _runtime_secret_env_content(tmp_path),
    )
    (tmp_path / "current-release").write_text(f"{old_sha}\n")

    result, log = _run_deploy(
        tmp_path,
        bad_sha,
        fail_sha=bad_sha,
    )

    assert result.returncode == 1
    assert (tmp_path / "current-release").read_text().strip() == old_sha
    assert f"rollback to {old_sha} succeeded" in result.stderr
    assert bad_sha in log
    assert old_sha in log
