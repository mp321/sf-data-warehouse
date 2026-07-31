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
   repository secret. Create three secrets:
   - `GCP_PROJECT_ID`: your project id
   - `GCP_SA_KEY`: open `keys/sa.json`, copy the entire JSON, paste it
   - `SOCRATA_APP_TOKEN`: your token, or create the secret with an empty
     value if you skipped it
2. Actions tab: enable workflows if prompted.
3. Open the `ingest` workflow, Run workflow (this is the manual trigger)
   and watch it go green. Do the same for `dbt`.

From now on ingestion runs daily and dbt builds plus tests weekly, with
no laptop involved.

Checkpoint: both workflows have a green manual run.

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
- dbt cannot find the profile: you are not in the `dbt/` folder, or
  `DBT_PROFILES_DIR` is not set to it.
- Socrata 429 responses: you are being rate limited; add a free
  `SOCRATA_APP_TOKEN` to `.env`.
- Workflow fails on the key step: the `GCP_SA_KEY` secret must contain
  the raw JSON exactly as it appears in the file.
