# Setup guide

The human onboarding path, longer and more hand-held than `CLAUDE.md`.
`CLAUDE.md` is authoritative on architecture; where this file disagrees with
it, this file is the one that is wrong.

> **Google Cloud is optional and Phase 1 can be skipped entirely.** DuckDB is
> the default target (ADR-1) and both Parquet zones are local by default, so
> the whole pipeline and the entire CI gate run on a fresh clone with no cloud
> account:
>
> ```
> make setup
> make ci-build     # whole pipeline from fixtures. No network, no account.
> make all          # ingest, spatial, load, build against real DataSF data
> ```
>
> Phase 1 is needed only for the optional BigQuery target
> (`make load-bigquery`, `make build-bigquery`) and for keeping the zones in a
> bucket rather than on this machine (ADR-9).

Follow this top to bottom the first time. Every step has a checkpoint so
you know it worked before moving on. Total cost of everything here: $0.
Budget roughly 60 to 90 minutes for the first full pass.

Prerequisites: Python 3.10+, git, a GitHub account. A Google account only if
you want Phase 1.

---

## Phase 1: Google Cloud and BigQuery (optional)

### 1.1 Create a project

1. Go to https://console.cloud.google.com and sign in.
2. Top bar, project dropdown, New Project. Name it `sf-data-warehouse`
   (or anything). Note the auto-generated Project ID, something like
   `sf-data-warehouse-447215`. You need the ID, not the display name.
3. Open BigQuery from the left menu (or search "BigQuery"). If prompted,
   you can use the BigQuery Sandbox, which is free and needs no credit
   card.

Sandbox caveat worth knowing: sandbox tables auto-expire after 60 days.
That is fine while you build. When the project becomes portfolio-facing,
attach a billing account. The free tier (10 GB storage, 1 TB of queries
per month) covers this project many times over, so it still costs
nothing, but tables stop expiring.

Checkpoint: the BigQuery console opens and shows your project id in the
left panel.

### 1.2 Create a service account and key

This is the robot identity that the ingestion script, dbt, and GitHub
Actions all use.

1. Console menu: IAM and Admin, then Service Accounts, then Create
   Service Account.
2. Name: `sf-dw-pipeline`. Continue.
3. Grant it the role BigQuery Admin (simplest for a personal project;
   you can tighten to BigQuery Data Editor plus BigQuery Job User later).
   Done.
4. Click the new account, Keys tab, Add Key, Create new key, JSON. A
   JSON file downloads.

Checkpoint: you have a `.json` key file downloaded. Treat it like a
password.

### 1.3 Cloud Storage bucket and IAM

This is where both Parquet zones live when `RAW_ZONE_URI` and
`DERIVED_ZONE_URI` are set, and what BigQuery's external tables read (ADR-9).
Still optional: with the variables unset, the zones are `data/raw` and
`data/derived` and nothing here is needed.

Everything below assumes `set -a; source .env; set +a` has been run, so
`GCP_PROJECT_ID` is set. Substitute your own bucket name for
`sf-data-bucket-mp`.

**Bucket settings that matter for staying free.** Always-free Cloud
Storage is 5 GB-month of Standard storage, 5,000 Class A and 50,000 Class
B operations per month, and it applies only to the `us-central1`,
`us-west1` and `us-east1` single regions. A multi-region location such as
`US` is not covered, so check the bucket is a single region and not the
multi-region that the console offers first.

```bash
# Confirm what you actually created. Location should read US-CENTRAL1,
# storage class STANDARD, and uniform bucket-level access true.
gcloud storage buckets describe gs://sf-data-bucket-mp \
  --format="yaml(location,locationType,storageClass,uniformBucketLevelAccess,softDeletePolicy,versioning)"
```

Three settings to correct if they are not already right:

```bash
# 1. Soft delete is ON by default with 7 days of retention, and
#    soft-deleted objects keep billing for those 7 days. This pipeline
#    deletes and rewrites objects routinely: `ingest.py --full-refresh`
#    swaps whole trees and `make publish` rewrites every mart directory.
#    With soft delete on, a rewrite temporarily doubles stored bytes,
#    which is how a 1 GB zone quietly becomes 2 GB against a 5 GB
#    allowance. Set retention to zero.
gcloud storage buckets update gs://sf-data-bucket-mp --clear-soft-delete

# 2. Object versioning must stay off, for the same reason, more so.
gcloud storage buckets update gs://sf-data-bucket-mp --no-versioning

# 3. Uniform bucket-level access and public access prevention on.
gcloud storage buckets update gs://sf-data-bucket-mp \
  --uniform-bucket-level-access --public-access-prevention
```

**IAM: grant at the bucket, not the project.** The service account needs
to create, read, list and delete objects in this one bucket, and needs
nothing else in Cloud Storage.

```bash
SA=$(gcloud iam service-accounts list \
  --project "$GCP_PROJECT_ID" --format="value(email)" | head -1)
echo "$SA"   # sanity check before granting anything

gcloud storage buckets add-iam-policy-binding gs://sf-data-bucket-mp \
  --member="serviceAccount:$SA" --role="roles/storage.objectUser"
```

`roles/storage.objectUser` covers create, delete, get and list without
granting the ability to change the bucket's IAM. `roles/storage.objectAdmin`
also works and is the older equivalent. Do not grant `roles/storage.admin`.

**BigQuery roles.** SETUP.md section 1.2 above suggests BigQuery Admin as
the simplest option. Tighten it now, because external tables do not need
it:

```bash
gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
  --member="serviceAccount:$SA" --role="roles/bigquery.user"
gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
  --member="serviceAccount:$SA" --role="roles/bigquery.dataEditor"

# then remove the broad one
gcloud projects remove-iam-policy-binding "$GCP_PROJECT_ID" \
  --member="serviceAccount:$SA" --role="roles/bigquery.admin"
```

`bigquery.user` runs jobs and creates datasets; `bigquery.dataEditor`
creates and replaces tables inside them. A BigQuery external table over
GCS is read with the querying identity's own credentials, so the
`storage.objectUser` binding above is what lets BigQuery read the
Parquet. No BigQuery connection resource and no BigLake setup is needed
for plain external tables.

**APIs.** Both are normally on already; enabling twice is harmless.

```bash
gcloud services enable bigquery.googleapis.com storage.googleapis.com \
  --project "$GCP_PROJECT_ID"
```

**Verify end to end before trusting any of it.** Authenticate as the
service account using the key from section 1.2. This is the same
credential path `load.py` and `publish/export.py` use, so a green probe
here means the pipeline will work, which impersonation does not prove.

```bash
gcloud auth activate-service-account \
  --key-file="$GOOGLE_APPLICATION_CREDENTIALS"

echo hello > /tmp/probe.txt
gcloud storage cp /tmp/probe.txt gs://sf-data-bucket-mp/probe.txt
gcloud storage rm gs://sf-data-bucket-mp/probe.txt

gcloud config set account YOUR_EMAIL@example.com   # switch back
```

That last line matters. `activate-service-account` changes the active
gcloud account globally, not just for this shell, so every later gcloud
command runs as the robot until you switch back. Run `gcloud auth list`
if you are unsure which account is active.

Impersonation (`--impersonate-service-account="$SA"`) is the other way
to run the probe, but it needs a grant on *your* account that nothing
above gives you, and it fails with `iam.serviceAccounts.getAccessToken
denied` without it. The bucket binding grants the service account access
to the bucket; it does not grant you the right to act as the service
account. If you want impersonation anyway:

```bash
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member="user:YOUR_EMAIL@example.com" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project="$GCP_PROJECT_ID"
```

Allow about a minute for the binding to propagate before retrying. This
needs Owner or Service Account Admin on the project; if the command
itself is denied, check `gcloud auth list` against the account that
created the project, because a mismatch there is the usual cause.

Add the bucket to `.env` and `.env.example`:

```
GCS_BUCKET=sf-data-bucket-mp
RAW_ZONE_URI=gs://sf-data-bucket-mp/raw
```

Checkpoint: the probe file uploads and deletes as the service account,
`softDeletePolicy` reports zero retention, and the location is a single
region.

---

## Phase 2: Local setup

### 2.1 Get the code

```bash
git clone git@github.com:YOUR_USERNAME/sf-data-warehouse.git
cd sf-data-warehouse
```

### 2.2 Python environment

From the repo root:

```bash
make setup
```

That creates `.venv`, installs `requirements.txt` and
`requirements-dev.txt`, runs `dbt deps` and installs the pre-commit hooks.
Do it this way rather than by hand: the dev requirements are what
`make test-python` and `make lint` need, and installing only
`requirements.txt` leaves both failing for a reason that looks like a
broken repo.

(Windows PowerShell: activate with `.venv\Scripts\Activate.ps1`.)

Checkpoint: `make ci-build` runs the whole pipeline from committed
fixtures and finishes green, with no network and no cloud account. If that
works, everything below is optional.

### 2.3 Wire up credentials (Phase 1 only)

Skip this unless you did Phase 1.

```bash
mkdir -p keys
mv ~/Downloads/YOUR_DOWNLOADED_KEY.json keys/sa.json
cp .env.example .env
```

Edit `.env`: set `GCP_PROJECT_ID` to your project id and
`GOOGLE_APPLICATION_CREDENTIALS` to the absolute path of `keys/sa.json`.
Then load it:

```bash
set -a; source .env; set +a
```

Rerun that load line in every new terminal session (or add it
to your shell profile). `keys/` and `.env` are already gitignored, and
`make leak-check` blocks a commit that would carry either.

Checkpoint: `echo $GCP_PROJECT_ID` prints your project id, and
`make ci-build` is still green in that same shell. It has to be: `make
ci-build` sets the zone DIR variables and DIR beats URI, so sourcing a
`.env` full of `gs://` URIs must not change what the CI gate tests.

---

## Phase 3: First ingestion run

Ingestion writes Parquet and nothing else. Getting those files into a
warehouse is a separate step (ADR-4), so nothing in this phase needs a
Google account even if you did Phase 1.

Start with the small dataset to prove the plumbing, then the large one:

```bash
python ingestion/ingest.py film_locations
python ingestion/ingest.py 311_cases
```

The 311 backfill loads everything updated since 2024-01-01 and takes a
few minutes. Progress prints every 50,000 rows. Run it a second time
afterwards and it should say "already up to date": that is the
incremental watermark working, and the watermark comes from the zone
itself rather than from any bookkeeping.

Then the rest. There are seven datasets and `--all` is the normal way to
run it:

```bash
python ingestion/ingest.py --all
```

Checkpoint: `data/raw/` holds one directory per raw table, each with an
`ingest_date=YYYY-MM-DD/` partition of Parquet inside it and a `_runs/`
directory of run manifests. Eyeball a table:

```bash
python -c "import duckdb; print(duckdb.sql(\"select * from read_parquet('data/raw/raw_311_cases/**/*.parquet', hive_partitioning=true) limit 5\"))"
```

Notice every column is a string. That is deliberate: raw stays untyped,
and staging models do the casting.

---

## Phase 4: The spatial precompute

**Do not skip this, and it is the step most easily forgotten.** It reads
the raw zone, computes H3 cells and boundary membership in Python, and
writes `data/derived/`. H3 is computed here rather than in SQL because
BigQuery has no H3 support of any kind, so there is nothing to dispatch
to and both engines instead read the same precomputed BIGINTs (ADR-5).

```bash
make spatial
```

Skipping it does not error. The spatial models build empty and the marts
come out with no rows. Worse is running it once and then ingesting again,
which leaves the new rows with null geography and surfaces as a `not_null`
failure several models downstream. `make build` therefore runs
`make check-derived` first, which compares the derived zone against the
raw zone and against the code that built it.

Checkpoint: `make check-derived` exits zero, and `data/derived/` holds six
Parquet files plus `_manifest.json`.

---

## Phase 5: Load and dbt

Two-minute orientation: dbt is just a tool that runs SQL files against your warehouse in the right order. Each `.sql` file in `models/` becomes a view or table named after the file. Three ideas cover 90 percent of it:

1. `{{ source('raw_datasf', 'raw_311_cases') }}` points at a raw table
   loaded by something outside dbt (our Python script).
2. `{{ ref('stg_datasf__311_cases') }}` points at another dbt model, and
   tells dbt to build that one first.
3. Tests are assertions in YAML (unique, not_null) that dbt turns into
   SQL checks and runs for you.

The project has three layers: `staging` (one view per raw or derived
table: rename, cast, deduplicate, nothing else), `intermediate` (models
that reshape staging models and that nothing queries directly), and
`marts` (analysis-ready tables, hand-written).

### 5.1 Run it

```bash
make load     # both zones -> the local DuckDB file
make build    # dbt run + test in dependency order
```

Those are the two you want day to day, and `make all` runs ingest,
spatial, load and build in order so you do not have to remember which
feeds which. To drive dbt directly instead:

```bash
cd dbt
export DBT_PROFILES_DIR="$(pwd)"
dbt debug     # checks the connection
dbt build     # run + test in dependency order
```

If `dbt run` fails with a "column not found" style error, DataSF renamed
a field. Query the raw table, find the real name, and fix it in the
staging model. That is normal analytics engineering maintenance, not a
broken project.

Checkpoint: `dbt build` reports `PASS=171 ERROR=0` and `data/sf.duckdb`
holds the `raw_datasf`, `derived_spatial` and `dbt_dev` schemas. That is
19 models, 148 data tests and 4 project hooks.

### 5.2 Prove the Parquet is the source of truth

```bash
make rebuild
```

This drops the DuckDB file and rebuilds every model from the zones alone,
without going near the API. If it does not reproduce what you had, the raw
zone is not the record it claims to be. CI runs the same idea against
committed fixtures on every pull request, which is `make ci-build`.

### 5.3 Docs

```bash
make docs        # regenerate and refresh the committed docs/dbt/ artifacts
make docs-serve  # the browsable site
```

A browsable site of every model, column description, test, and a lineage
graph. Good screen-share artifact in interviews.

---

## Phase 6: Automation on GitHub

1. In your GitHub repo: Settings, Secrets and variables, Actions, New
   repository secret. Create four secrets:
   - `GCP_PROJECT_ID`: your project id
   - `GCP_SA_KEY`: open `keys/sa.json`, copy the entire JSON, paste it
   - `GCS_BUCKET`: the bucket name alone, no `gs://` and no path. The
     ingest workflow builds `gs://<bucket>/raw` and `gs://<bucket>/derived`
     from it, and those are the zones it reads and writes (ADR-9). A bucket
     name is not really a secret, but it goes here rather than in the
     workflow file because project identifiers do not belong in the repo.
   - `SOCRATA_APP_TOKEN`: your token. **If you skipped it, do not create this
     secret at all.** GitHub will not store an empty secret value, so the
     obvious workaround is to type a space, and a space is not nothing: it
     becomes an `X-App-Token: " "` header that `requests` refuses to send, and
     the error talks about header whitespace rather than about tokens. An
     absent secret renders as an empty string, which ingestion reads as "no
     token" and runs anonymously. Recommended for the scheduled job, though:
     see the note below.
2. Actions tab: enable workflows if prompted.
3. Open the `ingest` workflow, Run workflow (this is the manual trigger)
   and watch it go green. Do the same for `dbt`.

From now on ingestion runs daily and dbt builds plus tests weekly, with
no laptop involved.

Those secrets are for `ingest.yml` and `dbt.yml` only. `ci.yml`, the gate on
every pull request, needs none of them and must keep needing none: it runs
the whole pipeline from fixtures against DuckDB and local zones, which is
what lets a fork pull request run it (ADR-1).

Checkpoint: both workflows have a green manual run.

### Get the Socrata token for the scheduled job

Optional locally and worth two minutes for CI, for a reason that is about
GitHub rather than about Socrata. Anonymous Socrata requests are rate
limited **per IP address**, and GitHub's hosted runners share their IPs
across every customer using them, so the anonymous budget you are drawing
on is not yours and you cannot see how much of it is left. A token moves
you onto a per-token budget you control. The nightly `--all` run is
roughly 100 requests, which is small either way, but the failure mode of
running out is a `429` partway through a dataset rather than a clean stop.

**An invalid token is worse than no token.** Anonymous requests are
served normally; a token Socrata does not recognise is refused outright
with `403 permission_denied, "Invalid app_token specified"`. So do not
set this secret speculatively, and check a token before you rely on it.

The page at https://data.sfgov.org/profile/edit/developer_settings
issues **two** values, and only one of them works here:

| Value | Length | Use |
|---|---|---|
| **App Token** | ~25 chars | This. Goes in `X-App-Token`. |
| Secret Token | longer | Not this. For OAuth flows, and Socrata rejects it as an app token. |

Verify before setting the secret. This prints the HTTP status and
nothing else, and 200 is the only acceptable answer:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "X-App-Token: PASTE_THE_APP_TOKEN_HERE" \
  'https://data.sfgov.org/resource/vw6y-z8j6.json?$limit=1'
```

Then set `SOCRATA_APP_TOKEN` to that value, alone, with no quotes and no
trailing newline. Ingestion strips whitespace before deciding whether a
token is present, so a stray space means "no token" rather than a failed
run, and a rejected token now fails immediately with a message naming the
variable rather than with a bare 403.

---

## Phase 7: Where to go next

The warehouse is complete: seven datasets, 19 models, 148 tests. What is
worth reading rather than building:

- `dbt/models/marts/README.md` for the mart layer's rules, particularly why
  every count mart carries a normalised companion measure.
- `dbt/models/staging/datasf/stg_datasf__311_cases.sql`, the reference
  staging model that every other one follows.
- `docs/decisions/` in number order for why any of it is the way it is.
  ADR-5 and ADR-6 are the two that explain the spatial layer.
- `CLAUDE.md` for the canonical architecture, and `USER-NOTES.md` for the
  same thing written to be read outside the repo.

Adding a dataset is the natural first change, and it is four files: an
entry in `vars.pipeline_sources` in `dbt/dbt_project.yml`, a source table
in the relevant `_<system>__sources.yml`, a staging model, and a fixture
under `tests/fixtures/socrata/`. `tests/test_dataset_registry.py` fails in
a tenth of a second on any of the four being missing.

---

## Troubleshooting

- "Missing required environment variable": rerun
  `set -a; source .env; set +a` in this terminal.
- 403 or permission errors from BigQuery: `GOOGLE_APPLICATION_CREDENTIALS`
  points at the wrong path, or the service account is missing
  `bigquery.user` and `bigquery.dataEditor`. Section 1.3 grants both and
  removes the broader `bigquery.admin` that section 1.2 starts you with.
- A model errors with "Unrecognized name" on BigQuery but builds fine on
  DuckDB: the external table's schema has stopped being a view of the whole
  zone. `make parity-columns` names the table and the columns.
- Marts build with zero rows, or `not_null` fails on a geography column:
  the derived zone is missing or behind. `make check-derived` says which,
  and `make spatial && make load && make build` is the fix.
- `Failed to impersonate ... iam.serviceAccounts.getAccessToken denied`:
  your user account lacks `roles/iam.serviceAccountTokenCreator` on the
  service account. See the end of section 1.3; the key-based probe there
  avoids impersonation entirely.
- gcloud commands unexpectedly running as the service account: you ran
  `gcloud auth activate-service-account` and did not switch back. Fix
  with `gcloud config set account YOUR_EMAIL@example.com`.
- dbt cannot find the profile: you are not in the `dbt/` folder, or
  `DBT_PROFILES_DIR` is not set to it.
- Socrata 429 responses: you are being rate limited; add a free
  `SOCRATA_APP_TOKEN` to `.env`.
- Workflow fails on the key step: the `GCP_SA_KEY` secret must contain
  the raw JSON exactly as it appears in the file.
