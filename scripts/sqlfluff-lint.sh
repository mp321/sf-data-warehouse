#!/usr/bin/env bash
#
# sqlfluff-lint.sh
#
# Lints the dbt models with the project venv's sqlfluff. Runs as a pre-commit
# hook and is the same command `make lint` and the CI lint job run.
#
# Why this is a system hook rather than the upstream sqlfluff pre-commit hook:
# that hook pins sqlfluff, dbt-core and dbt-duckdb in additional_dependencies,
# so pre-commit builds a second toolchain that requirements-dev.txt does not
# constrain. The two drifted, and the drift was invisible to CI, which does
# not use pre-commit for sqlfluff at all. The pinned dbt-core 1.9.1 capped
# mashumaro below the first version that imports on Python 3.14, so the hook
# could not run at all on a 3.14 machine while CI, on 3.11, stayed green.
# Resolving sqlfluff from the venv means the hook and CI install from the same
# requirements-dev.txt by construction and cannot disagree about versions.
#
# The trade: this needs `make setup` to have run. A hook that cannot drift, in
# exchange for one that cannot bootstrap itself.
#
# Scope: lints all of dbt/models, not just the staged files. The dbt templater
# compiles the whole project before it lints anything, so linting one model
# costs what linting every model costs. .pre-commit-config.yaml decides when
# to run this; this script decides what a run covers.
#
# Usage:
#   scripts/sqlfluff-lint.sh          # lint dbt/models

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT" || exit 2

# dbt reads profiles.yml from here. Set explicitly so the hook works no matter
# what is in the committing shell, which for a GUI git client is nothing.
export DBT_PROFILES_DIR="$REPO_ROOT/dbt"

# Absolute, because dbt-duckdb resolves a relative path against the current
# directory. profiles.yml defaults to ../data/sf.duckdb, which is right when
# you run dbt from dbt/ and wrong here: sqlfluff runs from the repo root, so
# the default resolves to a sibling of the repo. Same reasoning as the
# Makefile, which sets this for the same reason. An existing value wins, so
# `set -a; source .env; set +a` still steers this.
export DUCKDB_PATH="${DUCKDB_PATH:-$REPO_ROOT/data/sf.duckdb}"

# The templater opens the warehouse to compile. A missing parent directory is
# a connection error, not a lint failure, and reads as one. CI does this too.
mkdir -p "$(dirname "$DUCKDB_PATH")"

if [ -x "$REPO_ROOT/.venv/bin/sqlfluff" ]; then
    SQLFLUFF="$REPO_ROOT/.venv/bin/sqlfluff"
elif command -v sqlfluff >/dev/null 2>&1; then
    # Not the venv, so not necessarily the version CI resolves. Allowed, since
    # failing outright would block committing for anyone using a different
    # environment manager, but say so: a version disagreement here is exactly
    # the class of bug this hook was rewritten to prevent.
    SQLFLUFF="$(command -v sqlfluff)"
    echo "sqlfluff-lint: .venv not found, falling back to $SQLFLUFF" >&2
    echo "sqlfluff-lint: this may not match the version CI runs" >&2
else
    echo "sqlfluff-lint: no sqlfluff found in .venv or on PATH." >&2
    echo "sqlfluff-lint: run 'make setup', or skip with SKIP=sqlfluff-lint." >&2
    exit 2
fi

exec "$SQLFLUFF" lint dbt/models --processes 4
