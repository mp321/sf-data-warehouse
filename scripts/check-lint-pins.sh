#!/usr/bin/env bash
#
# check-lint-pins.sh
#
# Asserts that the ruff version in requirements-dev.txt matches the ruff rev
# in .pre-commit-config.yaml. Runs as a pre-commit hook and in CI.
#
# Why this exists: ruff is installed twice by design. pre-commit builds its
# own isolated environment from the pinned `rev`, which is the approach ruff
# upstream recommends and which keeps the hook working on a machine that has
# never run `make setup`. CI and `make lint` install ruff from
# requirements-dev.txt instead. Two installs is fine. Two installs that can
# silently be different versions is not.
#
# It went wrong exactly once and is worth restating: requirements-dev.txt said
# `ruff>=0.8` while the hook pinned v0.8.6. pip resolved 0.16.1, ruff 0.16
# stabilised PLC0415, and the result was a commit that passed every local hook
# and failed the CI lint job on a rule the local hook had never heard of. The
# versions did not drift because anyone edited them; they drifted because one
# side floated and the other did not, so time alone was enough.
#
# sqlfluff needs no equivalent check. Its hook resolves the venv's sqlfluff
# through scripts/sqlfluff-lint.sh, so there is only one install to be wrong.
#
# Usage:
#   scripts/check-lint-pins.sh

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT" || exit 2

REQ_FILE="requirements-dev.txt"
CFG_FILE=".pre-commit-config.yaml"
RUFF_REPO="https://github.com/astral-sh/ruff-pre-commit"

# The exact pin in requirements-dev.txt. A float will not match this pattern,
# which is deliberate: "ruff>=x" is the failure mode, not a passing state.
req_version="$(
    sed -n 's/^ruff==\([0-9][0-9A-Za-z.]*\)[[:space:]]*$/\1/p' "$REQ_FILE" | head -1
)"

# The rev on the ruff-pre-commit block, leading "v" stripped. Scoped to that
# repo so the other pinned revs in the file cannot be picked up by accident.
hook_version="$(
    awk -v repo="$RUFF_REPO" '
        index($0, repo) { found = 1 }
        found && /^[[:space:]]*rev:/ {
            sub(/^[[:space:]]*rev:[[:space:]]*/, "")
            sub(/^v/, "")
            sub(/[[:space:]]*(#.*)?$/, "")
            print
            exit
        }
    ' "$CFG_FILE"
)"

if [ -z "$req_version" ]; then
    echo "check-lint-pins: no exact ruff pin found in $REQ_FILE." >&2
    echo "check-lint-pins: expected a line reading 'ruff==<version>'." >&2
    echo "check-lint-pins: a floating 'ruff>=<version>' is what this check exists to prevent." >&2
    exit 1
fi

if [ -z "$hook_version" ]; then
    echo "check-lint-pins: no rev found for $RUFF_REPO in $CFG_FILE." >&2
    exit 1
fi

if [ "$req_version" != "$hook_version" ]; then
    echo "check-lint-pins: ruff versions disagree." >&2
    echo "" >&2
    echo "  $REQ_FILE       ruff==$req_version   (what CI and 'make lint' run)" >&2
    echo "  $CFG_FILE   rev: v$hook_version   (what the pre-commit hook runs)" >&2
    echo "" >&2
    echo "Set both to the same version. Local hooks and CI disagreeing about" >&2
    echo "the linter is how a red build reaches a branch that looked green." >&2
    exit 1
fi

echo "check-lint-pins: ruff $req_version in both $REQ_FILE and $CFG_FILE"
