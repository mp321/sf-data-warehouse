#!/usr/bin/env bash
#
# fake-bq-key.sh
#
# Mints a throwaway service account key and prints its path. Nothing else in
# this project uses it: it exists so that `make compile-bigquery` can render
# every model as BigQuery SQL without credentials AND without depending on dbt
# never opening a connection.
#
# Why this is not a workaround.
#
# The BigQuery compile gate used to rest on the claim "compiling does not open a
# warehouse connection". That is a property of dbt's internals, not of anything
# in this repo, and it broke twice on dbt upgrades. It is also invisible locally,
# because a developer who has sourced .env has GOOGLE_APPLICATION_CREDENTIALS
# set and every connection dbt opens quietly succeeds. The failure only ever
# appears in CI, which is the worst place to learn about it.
#
# So stop defending that property and remove the need for it. With a key file
# present, dbt takes the `service-account` branch in profiles.yml, and building
# a BigQuery client from a service account key is entirely local: google-auth
# parses the PEM and constructs the client, and no token is fetched until a
# request is actually made. Opening a connection therefore costs nothing and
# reaches nothing. Compiling still issues no queries, so nothing ever
# authenticates, and if that stops being true one day the failure is loud and
# specific rather than a credentials error at startup.
#
# What this key is: 2048 bits of locally generated RSA, a placeholder project,
# and an email at a domain that does not exist. It is not a credential. It
# authorises nothing, it is never committed, and it is regenerated on any
# machine that needs one. leak-check.sh allowlists this file by name, and the
# key it writes lands outside the repo so the scan never sees it.
#
# Usage:
#   scripts/fake-bq-key.sh          # print the path, minting it if missing
#   scripts/fake-bq-key.sh PATH     # write it to PATH instead

set -euo pipefail

OUT="${1:-${TMPDIR:-/tmp}/sf-dw-compile-only-key.json}"

# Reuse an existing one. Key generation takes a second or so, and this target
# runs on every `make check`.
if [ -s "$OUT" ]; then
    echo "$OUT"
    exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
    echo "fake-bq-key.sh: openssl not found; it is needed to generate a key" >&2
    exit 2
fi

PEM="$(mktemp)"
trap 'rm -f "$PEM"' EXIT

# PKCS#8, which is what `genpkey` emits and what google-auth expects.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$PEM" 2>/dev/null

# Python rather than a heredoc because the PEM has to be JSON-escaped, and
# hand-rolling that is exactly the kind of thing that works until it does not.
OUT="$OUT" PEM="$PEM" python3 - <<'PY'
import json
import os
import pathlib

out = pathlib.Path(os.environ["OUT"])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(
    json.dumps(
        {
            "type": "service_account",
            "project_id": "compile-only-no-connection",
            "private_key_id": "0" * 40,
            "private_key": pathlib.Path(os.environ["PEM"]).read_text(),
            "client_email": (
                "compile-only@compile-only-no-connection.iam.gserviceaccount.invalid"
            ),
            "client_id": "0" * 21,
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        indent=2,
    )
    + "\n"
)
out.chmod(0o600)
PY

echo "$OUT"
