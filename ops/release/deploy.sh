#!/bin/sh
set -eu

umask 077

deploy_root=${1:?deployment root is required}
requested_sha=${2:?release SHA is required}
image_prefix=${3:?image prefix is required}
project_name=trading-agent-production
runtime_env="$deploy_root/runtime.env"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

case "$deploy_root" in
    /*) ;;
    *) echo "deployment root must be absolute" >&2; exit 2 ;;
esac
case "$deploy_root" in
    *[!A-Za-z0-9._/-]*) echo "deployment root is invalid" >&2; exit 2 ;;
esac
case "$requested_sha" in
    *[!0-9a-f]*|'') echo "release SHA is invalid" >&2; exit 2 ;;
esac
if [ "${#requested_sha}" -ne 40 ]; then
    echo "release SHA is invalid" >&2
    exit 2
fi
if [ ! -f "$runtime_env" ]; then
    echo "runtime.env is missing; deployment did not modify it" >&2
    exit 2
fi
runtime_profiles=$(
    python3 "$script_dir/runtime_profiles.py" --path "$runtime_env"
) || exit 2
python3 "$script_dir/runtime_secrets.py" --path "$runtime_env" || exit 2

runtime_secret_value() {
    key=$1
    fallback=$2
    secret_path=$(
        python3 "$script_dir/runtime_secrets.py" \
            --path "$runtime_env" \
            --get "$key"
    ) || exit 2
    if [ -z "$secret_path" ]; then
        printf '%s' "$fallback"
        return
    fi
    cat -- "$secret_path"
}

TRADING_AGENT_POSTGRES_PASSWORD_SECRET=$(
    runtime_secret_value POSTGRES_PASSWORD_FILE ""
)
TRADING_AGENT_DATABASE_URL_SECRET=$(
    runtime_secret_value DATABASE_URL_FILE ""
)
TRADING_AGENT_DASHBOARD_AUTH_USERNAME_SECRET=$(
    runtime_secret_value DASHBOARD_AUTH_USERNAME_FILE ""
)
TRADING_AGENT_DASHBOARD_AUTH_PASSWORD_SECRET=$(
    runtime_secret_value DASHBOARD_AUTH_PASSWORD_FILE ""
)
TRADING_AGENT_ANTHROPIC_API_KEY_SECRET=$(
    runtime_secret_value ANTHROPIC_API_KEY_FILE ""
)
TRADING_AGENT_HERMES_API_KEY_SECRET=$(
    runtime_secret_value HERMES_API_KEY_FILE ""
)
TRADING_AGENT_OPENAI_COMPATIBLE_API_KEY_SECRET=$(
    runtime_secret_value OPENAI_COMPATIBLE_API_KEY_FILE "local-no-auth"
)
TRADING_AGENT_TELEGRAM_BOT_TOKEN_SECRET=$(
    runtime_secret_value TELEGRAM_BOT_TOKEN_FILE ""
)
TRADING_AGENT_TELEGRAM_CHAT_ID_SECRET=$(
    runtime_secret_value TELEGRAM_CHAT_ID_FILE ""
)
TRADING_AGENT_NEO4J_PASSWORD_SECRET=$(
    runtime_secret_value NEO4J_PASSWORD_FILE ""
)
TRADING_AGENT_S3_ACCESS_KEY_ID_SECRET=$(
    runtime_secret_value S3_ACCESS_KEY_ID_FILE ""
)
TRADING_AGENT_S3_SECRET_ACCESS_KEY_SECRET=$(
    runtime_secret_value S3_SECRET_ACCESS_KEY_FILE ""
)
export \
    TRADING_AGENT_POSTGRES_PASSWORD_SECRET \
    TRADING_AGENT_DATABASE_URL_SECRET \
    TRADING_AGENT_DASHBOARD_AUTH_USERNAME_SECRET \
    TRADING_AGENT_DASHBOARD_AUTH_PASSWORD_SECRET \
    TRADING_AGENT_ANTHROPIC_API_KEY_SECRET \
    TRADING_AGENT_HERMES_API_KEY_SECRET \
    TRADING_AGENT_OPENAI_COMPATIBLE_API_KEY_SECRET \
    TRADING_AGENT_TELEGRAM_BOT_TOKEN_SECRET \
    TRADING_AGENT_TELEGRAM_CHAT_ID_SECRET \
    TRADING_AGENT_NEO4J_PASSWORD_SECRET \
    TRADING_AGENT_S3_ACCESS_KEY_ID_SECRET \
    TRADING_AGENT_S3_SECRET_ACCESS_KEY_SECRET

profile_enabled() {
    profile=$1
    case "
$runtime_profiles
" in
        *"
$profile
"*) return 0 ;;
    esac
    return 1
}

release_dir() {
    printf '%s/releases/%s' "$deploy_root" "$1"
}

manifest_value() {
    release=$1
    key=$2
    directory=$(release_dir "$release")
    python3 "$directory/ops/release/release_manifest.py" get \
        --path "$directory/release.env" \
        --image-prefix "$image_prefix" \
        --release-sha "$release" \
        --key "$key"
}

validate_release() {
    release=$1
    directory=$(release_dir "$release")
    python3 "$directory/ops/release/release_manifest.py" validate \
        --path "$directory/release.env" \
        --image-prefix "$image_prefix" \
        --release-sha "$release"
    test -f "$directory/ops/release/compose.production.yml"
    test -f "$directory/db/migrate.sh"
}

compose_release() {
    release=$1
    shift
    directory=$(release_dir "$release")
    docker compose \
        --project-name "$project_name" \
        --file "$directory/ops/release/compose.production.yml" \
        --env-file "$runtime_env" \
        --env-file "$directory/release.env" \
        "$@"
}

schema_is_compatible() {
    release=$1
    schema_version=$(
        compose_release "$release" exec -T db \
            psql -U trading -d trading_agent -Atc \
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    )
    schema_min=$(manifest_value "$release" SCHEMA_MIN)
    schema_max=$(manifest_value "$release" SCHEMA_MAX)
    test "$schema_version" -ge "$schema_min" &&
        test "$schema_version" -le "$schema_max"
}

verify_image_provenance() {
    release=$1
    expected_source="https://github.com/${image_prefix#ghcr.io/}"
    for key in AGENT_IMAGE DASHBOARD_IMAGE; do
        image=$(manifest_value "$release" "$key") || return 1
        revision=$(docker image inspect --format \
            '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
            "$image") || return 1
        source=$(docker image inspect --format \
            '{{ index .Config.Labels "org.opencontainers.image.source" }}' \
            "$image") || return 1
        if [ "$revision" != "$release" ] || [ "$source" != "$expected_source" ]; then
            echo "image provenance does not match release $release" >&2
            return 1
        fi
    done
}

smoke_release() {
    release=$1
    attempts=0
    while [ "$attempts" -lt 30 ]; do
        if compose_release "$release" exec -T agent \
            python -m src.healthcheck readiness >/dev/null 2>&1 &&
            compose_release "$release" exec -T dashboard \
                node --input-type=module -e "
import { readRuntimeSecret } from './runtime-secrets.mjs';
const base = 'http://127.0.0.1:3000';
const user = readRuntimeSecret(
  process.env,
  'DASHBOARD_AUTH_USERNAME',
  {required: true},
);
const pass = readRuntimeSecret(
  process.env,
  'DASHBOARD_AUTH_PASSWORD',
  {required: true},
);
const auth = 'Basic ' + Buffer.from(user + ':' + pass).toString('base64');
Promise.all([
  fetch(base + '/api/health').then(r => r.status),
  fetch(base + '/api/portfolio').then(r => r.status),
  fetch(base + '/api/portfolio', {
    headers: {authorization: auth},
  }).then(r => r.status),
]).then(statuses => {
  if (statuses[0] !== 200 || statuses[1] !== 401 || statuses[2] !== 200) {
    process.exit(1);
  }
}).catch(() => process.exit(1));
" >/dev/null 2>&1 &&
            profile_health_ok "$release"
        then
            return 0
        fi
        attempts=$((attempts + 1))
        sleep 2
    done
    return 1
}

profile_health_ok() {
    release=$1
    if profile_enabled knowledge-graph; then
        compose_release "$release" \
            --profile knowledge-graph exec -T knowledge-worker \
            python -m src.knowledge_worker health \
            >/dev/null 2>&1 || return 1
        compose_release "$release" \
            --profile knowledge-graph exec -T knowledge-shadow-worker \
            python -m src.knowledge_shadow_worker health \
            >/dev/null 2>&1 || return 1
    fi
    if profile_enabled object-storage; then
        compose_release "$release" \
            --profile object-storage exec -T object-archive-worker \
            python -m src.object_archive health \
            >/dev/null 2>&1 || return 1
    fi
    return 0
}

activate_release() {
    release=$1
    validate_release "$release" || return 1
    compose_release "$release" \
        pull db agent dashboard monitor || return 1
    verify_image_provenance "$release" || return 1
    if profile_enabled market-data; then
        compose_release "$release" --profile market-data \
            pull universe-sync market-sync || return 1
    fi
    if profile_enabled nasdaq-reference; then
        compose_release "$release" --profile nasdaq-reference \
            pull universe-sync nasdaq-alias-sync || return 1
    fi
    if profile_enabled object-storage; then
        compose_release "$release" --profile object-storage \
            pull object-archive-worker || return 1
    fi
    compose_release "$release" up -d db || return 1
    compose_release "$release" run --rm migrate || return 1
    schema_is_compatible "$release" || {
        echo "database schema is incompatible with release $release" >&2
        return 1
    }
    compose_release "$release" run --rm calendar-sync || return 1
    compose_release "$release" \
        up -d agent dashboard learning-worker monitor || return 1
    if profile_enabled knowledge-graph; then
        compose_release "$release" --profile knowledge-graph \
            up -d knowledge-worker knowledge-shadow-worker || return 1
    else
        compose_release "$release" --profile knowledge-graph \
            stop knowledge-worker knowledge-shadow-worker || return 1
    fi
    if profile_enabled object-storage; then
        compose_release "$release" --profile object-storage \
            up -d object-archive-worker || return 1
    else
        compose_release "$release" --profile object-storage \
            stop object-archive-worker || return 1
    fi
    if profile_enabled market-data; then
        compose_release "$release" --profile market-data \
            up -d universe-sync market-sync || return 1
    else
        compose_release "$release" --profile market-data \
            stop market-sync || return 1
    fi
    if profile_enabled nasdaq-reference; then
        compose_release "$release" --profile nasdaq-reference \
            up -d universe-sync nasdaq-alias-sync || return 1
    else
        compose_release "$release" --profile nasdaq-reference \
            stop nasdaq-alias-sync || return 1
    fi
    if ! profile_enabled market-data &&
        ! profile_enabled nasdaq-reference
    then
        compose_release "$release" \
            --profile market-data \
            --profile nasdaq-reference \
            stop universe-sync || return 1
    fi
    smoke_release "$release"
}

read_pointer() {
    pointer=$1
    if [ ! -f "$pointer" ]; then
        return 0
    fi
    value=$(sed -n '1p' "$pointer")
    case "$value" in
        *[!0-9a-f]*|'') return 1 ;;
    esac
    if [ "${#value}" -ne 40 ]; then
        return 1
    fi
    printf '%s' "$value"
}

write_pointer() {
    pointer=$1
    value=$2
    temporary="$pointer.tmp.$$"
    printf '%s\n' "$value" > "$temporary"
    mv "$temporary" "$pointer"
}

current_pointer="$deploy_root/current-release"
previous_pointer="$deploy_root/previous-release"
previous_release=$(read_pointer "$current_pointer") || {
    echo "current release pointer is invalid" >&2
    exit 2
}

if activate_release "$requested_sha"; then
    if [ -n "$previous_release" ] &&
        [ "$previous_release" != "$requested_sha" ]
    then
        write_pointer "$previous_pointer" "$previous_release"
    fi
    write_pointer "$current_pointer" "$requested_sha"
    echo "release $requested_sha is healthy"
    exit 0
fi

echo "release $requested_sha failed; attempting compatible rollback" >&2
if [ -n "$previous_release" ] &&
    [ "$previous_release" != "$requested_sha" ] &&
    activate_release "$previous_release"
then
    echo "rollback to $previous_release succeeded" >&2
else
    echo "automatic rollback was unavailable or failed" >&2
fi
exit 1
