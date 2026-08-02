# Setup guide

> **Out of date as of 2026-07-31, and Phase 1 is no longer required.** ADR-4
> split the pipeline into ingest (Socrata to Parquet), load (Parquet to a
> warehouse) and build (dbt), and made DuckDB the default target. None of
> those need Google Cloud. To get a working warehouse now:
>
> ```
> make setup
> make ci-build     # whole pipeline from fixtures. No network, no account.
> make ingest       # real data from DataSF. Network, still no account.
> make load
> make build
> ```
>
> Phases 1 and 2 below are only needed for the optional BigQuery target
> (`make load-bigquery`, `make build-bigquery`). Phase 3 onward still
> describes the shape of things but names commands that have moved.
> `CLAUDE.md` is authoritative until this file is rewritten; see the
> follow-ups in `docs/dev-notes/2026-07-31.md`.

Follow this top to bottom the first time. Every step has a checkpoint so
you know it worked before moving on. Total cost of everything here: $0.
Budget roughly 60 to 90 minutes for the first full pass.

Prerequisites: Python 3.10+, git, a Google account, a GitHub account.

---

## Phase 1: Google Cloud and BigQuery (optional since ADR-4)

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

### 1.3 Cloud Storage bucket and IAM (PLAN-4)

This is where the Parquet raw zone lives once PLAN-4 lands. Until then it
is optional and nothing breaks without it.

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

### 2.1 Get the code onto GitHub and your machine

1. On GitHub, create a new public repo named `sf-data-warehouse`. Do not
   initialize it with a README.
2. Unzip the project scaffold, then from inside the folder:

```bash
git init
git add .
git commit -m "Scaffold: ingestion, dbt project, CI workflows"
git branch -M main
git remote add origin git@github.com:YOUR_USERNAME/sf-data-warehouse.git
git push -u origin main
```

### 2.2 Python environment

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

(Windows PowerShell: `.venv\Scripts\Activate.ps1` instead of the source
line.)

### 2.3 Wire up credentials

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
to your shell profile). Ensure `keys/` folder is gitignored.

Checkpoint: `echo $GCP_PROJECT_ID` prints your project id.

---

## Phase 3: First ingestion run

Start with the small fun dataset to prove the plumbing, then the real
one:

```bash
python ingestion/ingest.py film_locations
python ingestion/ingest.py 311_cases
```

The 311 backfill loads everything updated since 2024-01-01 and takes a
few minutes. Progress prints every 50,000 rows. Run it a second time
afterwards and it should say "already up to date": that is the
incremental watermark working.

Then load the other two:

```bash
python ingestion/ingest.py building_permits city_budget
```

Checkpoint: in the BigQuery console you see a `raw_datasf` dataset
containing four tables. Run this and eyeball the output:

```sql
select service_request_id, requested_datetime, status_description
from `YOUR_PROJECT_ID.raw_datasf.raw_311_cases`
limit 10;
```

Notice every column is a string. That is deliberate: raw stays untyped,
and staging models do the casting.

---

## Phase 4: dbt

Two-minute orientation: dbt is just a tool that runs SQL files against your warehouse in the right order. Each `.sql` file in `models/` becomes a view or table named after the file. Three ideas cover 90 percent of it:

1. `{{ source('raw_datasf', 'raw_311_cases') }}` points at a raw table
   loaded by something outside dbt (our Python script).
2. `{{ ref('stg_datasf__311_cases') }}` points at another dbt model, and
   tells dbt to build that one first.
3. Tests are assertions in YAML (unique, not_null) that dbt turns into
   SQL checks and runs for you.

The project has two layers: `staging` (one view per raw table: rename,
cast, deduplicate, nothing else) and `marts` (analysis-ready tables you
will hand-write).

### 4.1 Run it

```bash
cd dbt
export DBT_PROFILES_DIR="$(pwd)"
dbt debug
```

`dbt debug` checks the connection. All green? Then:

```bash
dbt run          # builds stg_datasf__311_cases as a view
dbt test         # runs the tests defined in _datasf__models.yml
dbt build        # run + test in dependency order (use this day to day)
```

If `dbt run` fails with a "column not found" style error, DataSF renamed
a field. Query the raw table with `limit 10`, find the real name, and
fix it in `stg_datasf__311_cases.sql`. That is normal analytics
engineering maintenance, not a broken project.

Checkpoint: `dbt build` finishes with all green, and BigQuery now shows
a `dbt_dev` dataset containing the `stg_datasf__311_cases` view.

### 4.2 Docs

```bash
dbt docs generate
dbt docs serve
```

Opens a browsable site of every model, column description, test, and a
lineage graph. This is a great screen-share artifact in interviews.

---

## Phase 5: Automation on GitHub

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

## Phase 6: Your turn

The scaffold ends here on purpose. Open `dbt/models/marts/README.md` for
the hand-written SQL roadmap and the review workflow. First task:
staging is only built for 311, so writing `stg_datasf__film_locations`
yourself, using `stg_datasf__311_cases.sql` as the pattern, is the ideal
warm-up.

---

## Troubleshooting

- "Missing required environment variable": rerun
  `set -a; source .env; set +a` in this terminal.
- 403 or permission errors from BigQuery: the service account is missing
  the BigQuery Admin role, or `GOOGLE_APPLICATION_CREDENTIALS` points to
  the wrong path.
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
