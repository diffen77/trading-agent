#!/bin/sh
set -eu

if [ -n "${DATABASE_URL_FILE:-}" ]; then
    if [ "${DATABASE_URL+x}" = "x" ]; then
        echo "DATABASE_URL and DATABASE_URL_FILE cannot both be set" >&2
        exit 2
    fi
    case "$DATABASE_URL_FILE" in
        /*) ;;
        *) echo "DATABASE_URL_FILE must be absolute" >&2; exit 2 ;;
    esac
    if [ ! -f "$DATABASE_URL_FILE" ] || [ -L "$DATABASE_URL_FILE" ]; then
        echo "DATABASE_URL_FILE is unavailable or unsafe" >&2
        exit 2
    fi
    DATABASE_URL=$(cat -- "$DATABASE_URL_FILE")
    export DATABASE_URL
fi

: "${DATABASE_URL:?DATABASE_URL or DATABASE_URL_FILE must be set}"

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

core_relation_count=$(
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -Atc "
        SELECT COUNT(*)
        FROM UNNEST(ARRAY[
            'companies',
            'prices',
            'fundamentals',
            'macro',
            'portfolio',
            'trades',
            'learnings',
            'reviews',
            'balance',
            'input_dependencies',
            'trade_outcomes'
        ]) AS relation_name
        WHERE TO_REGCLASS('public.' || relation_name) IS NOT NULL
    "
)

if [ "$core_relation_count" = "0" ]; then
    unexpected_relation_count=$(
        psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -Atc "
            SELECT COUNT(*)
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename <> 'schema_migrations'
        "
    )
    if [ "$unexpected_relation_count" != "0" ]; then
        echo "refusing to initialize over a partial unknown schema" >&2
        exit 1
    fi
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
        -f "$script_dir/init/001_schema.sql"
elif [ "$core_relation_count" != "11" ]; then
    echo "legacy baseline is partial; manual recovery is required" >&2
    exit 1
fi

ai_decisions_exists=$(
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -Atc "
        SELECT TO_REGCLASS('public.ai_decisions') IS NOT NULL
    "
)
if [ "$ai_decisions_exists" != "t" ]; then
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
        -f "$script_dir/init/002_ai_decisions.sql"
fi

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO schema_migrations (version, name)
VALUES
    (2, 'legacy-baseline-company-dependencies'),
    (3, 'legacy-baseline-seed-companies-not-replayed'),
    (4, 'legacy-baseline-technical-signals-rebuilt-by-006'),
    (5, 'legacy-baseline-pattern-recognition-rebuilt-by-006')
ON CONFLICT (version) DO NOTHING;
SQL

for migration in "$script_dir"/migrations/*.sql; do
    filename=$(basename "$migration")
    version=${filename%%_*}

    case "$version" in
        ''|*[!0-9]*) continue ;;
    esac

    if [ "$version" -lt 6 ]; then
        continue
    fi

    applied=$(
        psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -Atc \
            "SELECT 1 FROM schema_migrations WHERE version = $version"
    )
    if [ "$applied" = "1" ]; then
        continue
    fi

    {
        echo "BEGIN;"
        echo "\\i $migration"
        echo "INSERT INTO schema_migrations (version, name) VALUES ($version, '$filename');"
        echo "COMMIT;"
    } | psql "$DATABASE_URL" -v ON_ERROR_STOP=1
done
