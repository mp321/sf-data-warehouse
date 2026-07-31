#!/usr/bin/env bash
#
# leak-check.sh
#
# Greps the working tree for credential shapes and exits nonzero on a hit.
# Runs in CI on every pull request and as a pre-commit hook. Ignore rules are
# the wrong last line of defence for credentials; this is the backstop.
#
# Scope: tracked and untracked files, excluding what .gitignore already
# covers. It deliberately does NOT scan git history; use gitleaks or
# trufflehog for that.
#
# If this fires: rotate the credential FIRST, then clean the tree. A key that
# reached a file has to be assumed compromised, and the order matters because
# cleaning first tempts you into deciding rotation is no longer urgent.
#
# Usage:
#   scripts/leak-check.sh            # scan the repo
#   scripts/leak-check.sh --staged   # scan staged files only (pre-commit)

set -uo pipefail

RED=$'\033[0;31m'
YELLOW=$'\033[0;33m'
GREEN=$'\033[0;32m'
RESET=$'\033[0m'
if [ ! -t 1 ] || [ -n "${NO_COLOR:-}" ]; then
    RED=""; YELLOW=""; GREEN=""; RESET=""
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT" || exit 2

MODE="tree"
if [ "${1:-}" = "--staged" ]; then
    MODE="staged"
fi

# ---------------------------------------------------------------------------
# Patterns. Each entry is "label|extended regex".
#
# Two kinds live here: credential shapes (things that look like a secret no
# matter where they appear) and project-specific shapes (things that are only
# a secret in this repo).
# ---------------------------------------------------------------------------
PATTERNS=(
    "private key block|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    "GCP service account json|\"type\"[[:space:]]*:[[:space:]]*\"service_account\""
    "GCP private_key_id|\"private_key_id\"[[:space:]]*:[[:space:]]*\"[a-f0-9]{20,}\""
    "GCP api key|AIza[0-9A-Za-z_-]{35}"
    "GCP oauth client id|[0-9]{10,}-[0-9a-z]{20,}\.apps\.googleusercontent\.com"
    "AWS access key id|(A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}"
    "AWS secret access key|aws_secret_access_key[[:space:]]*=[[:space:]]*[A-Za-z0-9/+=]{40}"
    "GitHub token|gh[pousr]_[A-Za-z0-9]{36,}"
    "Slack token|xox[abprs]-[0-9A-Za-z-]{10,}"
    "Stripe secret key|sk_live_[0-9a-zA-Z]{24,}"
    "OpenAI or Anthropic key|(sk-ant-|sk-proj-|sk-)[A-Za-z0-9_-]{32,}"
    "JWT|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\."
    "generic assigned secret|(password|passwd|secret|token|api_?key)[[:space:]]*[:=][[:space:]]*[\"'][^\"']{12,}[\"']"
    "postgres or mysql url with password|(postgres|postgresql|mysql)://[^:[:space:]]+:[^@[:space:]]+@"
)

# Files that are allowed to contain pattern-shaped text: this script defines
# the patterns, and the docs describe them.
ALLOWLIST_REGEX='^(scripts/leak-check\.sh|\.pre-commit-config\.yaml|docs/.*|CLAUDE\.md)$'

# ---------------------------------------------------------------------------
# Build the file list
#
# Read with a while loop rather than mapfile: mapfile needs bash 4, and macOS
# ships bash 3.2, where this script is also a pre-commit hook. FILE_COUNT is
# tracked by hand for the same reason, because bash 3.2 under `set -u` treats
# ${#FILES[@]} on an empty array as an unbound variable.
# ---------------------------------------------------------------------------
FILES=()
FILE_COUNT=0
while IFS= read -r path; do
    FILES[FILE_COUNT]="$path"
    FILE_COUNT=$((FILE_COUNT + 1))
done < <(
    if [ "$MODE" = "staged" ]; then
        git diff --cached --name-only --diff-filter=ACM
    else
        # Tracked files plus untracked-but-not-ignored files. This catches a
        # key sitting in the working tree before anyone has run git add.
        git ls-files --cached --others --exclude-standard
    fi
)

if [ "$FILE_COUNT" -eq 0 ]; then
    echo "${GREEN}leak-check: no files to scan${RESET}"
    exit 0
fi

FOUND=0

# ---------------------------------------------------------------------------
# Check 1: files that should never exist in the tree at all, by name.
# A key file is a problem whether or not we can parse its contents.
# ---------------------------------------------------------------------------
for f in "${FILES[@]}"; do
    case "$f" in
        keys/*|*/keys/*|.env|*/.env|*sa.json|*service-account*.json|*credentials*.json|*.pem|*.p12|*.pfx)
            echo "${RED}LEAK${RESET} forbidden filename: ${f}"
            FOUND=1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Check 2: content patterns.
# ---------------------------------------------------------------------------
for entry in "${PATTERNS[@]}"; do
    label="${entry%%|*}"
    regex="${entry#*|}"

    for f in "${FILES[@]}"; do
        [ -f "$f" ] || continue
        [[ "$f" =~ $ALLOWLIST_REGEX ]] && continue

        # -I skips binary files, -n gives line numbers, -E extended regex.
        # -e and -- are load bearing: the private key pattern begins with
        # "-----", which grep otherwise parses as options, so the single
        # most important pattern in this list silently never matches.
        if matches=$(grep -InE -e "$regex" -- "$f" 2>/dev/null); then
            while IFS= read -r line; do
                lineno="${line%%:*}"
                echo "${RED}LEAK${RESET} ${label}: ${f}:${lineno}"
                FOUND=1
            done <<< "$matches"
        fi
    done
done

# ---------------------------------------------------------------------------
# Check 3: a .gitignore that ignores itself. It then never gets committed, so
# a fresh clone has no ignore rules and nothing stops keys/sa.json being added.
# ---------------------------------------------------------------------------
if [ -f .gitignore ] && grep -qxE '\.gitignore' .gitignore; then
    echo "${RED}LEAK${RESET} .gitignore ignores itself, so it will not be committed and a fresh clone will have no ignore rules"
    FOUND=1
fi

for required in 'keys/' '.env'; do
    if [ -f .gitignore ] && ! grep -qxF "$required" .gitignore; then
        echo "${YELLOW}WARN${RESET} .gitignore no longer contains '${required}'"
        FOUND=1
    fi
done

# ---------------------------------------------------------------------------
if [ "$FOUND" -ne 0 ]; then
    echo ""
    echo "${RED}leak-check failed.${RESET}"
    echo "Rotate the credential first, then remove it from the tree."
    echo "If a match is a false positive, add the file to ALLOWLIST_REGEX in"
    echo "scripts/leak-check.sh with a comment explaining why."
    exit 1
fi

echo "${GREEN}leak-check: clean (${FILE_COUNT} files scanned)${RESET}"
exit 0
