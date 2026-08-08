# sf-data-warehouse
#
# Entry point for every routine task. See CLAUDE.md for context and
# docs/decisions/ for why things are the way they are.
#
# The pipeline is five steps, and they are separate on purpose (ADR-4, ADR-5):
#
#   make ingest    APIs      -> raw zone             needs network, no creds
#   make spatial   raw zone  -> derived zone         needs neither
#   make load      both      -> DuckDB or BigQuery   needs neither (DuckDB)
#   make build     dbt run + test on the warehouse   needs neither (DuckDB)
#   make publish   warehouse -> published/           needs neither (local)
#
# `spatial` sits between ingest and load because it reads the Parquet zone
# and writes another one. Forgetting it does not break the build: the spatial
# models come out empty and the marts come out with no rows, which is a
# quieter failure than it sounds, so `make all` exists to run the four in
# order.
#
# WHERE THE ZONES ARE. `data/raw` and `data/derived` by default, and a bucket
# when RAW_ZONE_URI and DERIVED_ZONE_URI are set (ADR-9). There is no target
# for the remote zones and there does not need to be one: every target below
# reads the environment, so `set -a; source .env; set +a; make all` runs the
# whole pipeline against the bucket, and a shell that has not sourced .env runs
# it against data/. A run writes to one zone or the other, never to both, so
# after a remote run `data/` holds whatever the last local run left there and is
# not a copy of the bucket. Credentials: the remote zones need
# GOOGLE_APPLICATION_CREDENTIALS, the local ones need nothing.
#
# ci-build is the exception that has to stay an exception. It sets the DIR
# variables on every command, and DIR beats URI, so `make check` is local and
# credential-free even in a shell that has sourced a .env full of URIs.
#
# The BigQuery targets need Google Cloud credentials and are marked (creds).
# Load them with:
#   set -a; source .env; set +a

.DEFAULT_GOAL := help

VENV        := .venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
DBT         := $(CURDIR)/$(VENV)/bin/dbt
DBT_DIR     := dbt
DATA_DIR    := data
RAW_DIR     := $(DATA_DIR)/raw
DOCS_ARTIFACTS := docs/dbt

# ci-build runs the whole pipeline from fixtures, and must not touch the real
# raw zone while doing it. It gets its own zone and its own warehouse file,
# both under the gitignored data/ tree. Without this, `make check` quietly
# appends fixture rows to data/raw, which is a slow-acting mess: the rows are
# plausible, they survive into every later build, and nothing points at where
# they came from.
CI_DIR        := $(CURDIR)/$(DATA_DIR)/ci
CI_RAW        := $(CI_DIR)/raw
CI_DERIVED    := $(CI_DIR)/derived
CI_DB         := $(CI_DIR)/sf.duckdb
CI_PUBLISHED  := $(CI_DIR)/published

# dbt reads profiles.yml from here; every dbt target sets it explicitly so
# these work regardless of what is in your shell.
export DBT_PROFILES_DIR := $(CURDIR)/$(DBT_DIR)

# Absolute, and exported, because dbt-duckdb resolves a relative path against
# the current directory. profiles.yml defaults to ../data/sf.duckdb, which is
# right when you run dbt from dbt/ and wrong from anywhere else: sqlfluff runs
# from the repo root and was resolving it to a sibling of the repo. One
# absolute value here means every tool opens the same file.
export DUCKDB_PATH := $(CURDIR)/$(DATA_DIR)/sf.duckdb

.PHONY: help setup all ingest spatial load load-bigquery build build-bigquery \
        publish test test-python docs docs-serve lint fmt leak-check compile-duckdb \
        compile-bigquery ci-build rebuild clean clean-warehouse clean-derived check check-derived \
        check-runs parity-check parity-columns context-pack context-pack-check

# `make build` refuses to run against a derived zone that is behind the raw
# zone. Set DERIVED_CHECK=0 to build anyway, which is worth doing only when you
# already know the geography is incomplete and are building for some other
# reason. See ingestion/check_derived.py for what the check compares.
DERIVED_CHECK ?= 1
BUILD_PREREQS  := $(if $(filter-out 0,$(DERIVED_CHECK)),check-derived,)

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Targets marked (creds) need GCP_PROJECT_ID and GOOGLE_APPLICATION_CREDENTIALS."

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

setup: ## Create venv, install deps, install dbt packages and git hooks
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt
	mkdir -p $(RAW_DIR)
	cd $(DBT_DIR) && $(DBT) deps || true
	$(VENV)/bin/pre-commit install || echo "pre-commit hook not installed (not a git repo?)"
	@chmod +x scripts/leak-check.sh
	@echo ""
	@echo "Setup complete. Next:"
	@echo "  make ci-build   # full pipeline from fixtures, no network, no creds"
	@echo "  make ingest     # pull real data from DataSF (network, no creds)"

# ---------------------------------------------------------------------------
# Ingestion: Socrata -> Parquet. Needs network, but no credentials.
# ---------------------------------------------------------------------------

ingest: ## Pull every dataset from DataSF and TIGERweb into data/raw as Parquet
	$(PY) ingestion/ingest.py --all

# ---------------------------------------------------------------------------
# Spatial precompute: raw zone -> derived zone (ADR-5, ADR-6).
#
# Pure function of data/raw plus the code, so it is always safe to delete
# data/derived and re-run. About 23 seconds on the full local raw zone,
# measured 2026-08-05: 19 of them in the oracle sample's exact
# point-in-polygon, 1 in the H3 cells, and the rest in reading and writing.
#
# RE-RUNNING IS CHEAP AND DOES NOT REBUILD WHAT HAS NOT MOVED (PLAN-5 step 9).
# A run compares the raw zone's ingest_date partitions and a stamp over the
# source of every module that decides the zone against what the last run
# recorded in the manifest, and rebuilds only what those say has changed. A
# second run on an unchanged zone takes 0.3 seconds and writes no Parquet at
# all. Editing any of those modules, comment or code, invalidates the whole
# zone on purpose: see ingestion/derived_state.py for why that trade is the
# right way round. Force a rebuild with `make spatial SPATIAL_ARGS=--full`, or
# with `make clean-derived`.
# ---------------------------------------------------------------------------

SPATIAL_ARGS ?=

spatial: ## Compute H3 cells and boundary membership into data/derived
	$(PY) ingestion/spatial.py --all $(SPATIAL_ARGS)

# The other half of "forgetting spatial does not error", and now of "editing
# spatial.py does not error either". spatial.py records in
# data/derived/_manifest.json the raw row count it read per dataset and the
# code version it ran, and this compares both against the raw zone and the code
# as they are now. Three verdicts: STALE (rows with no geography, exit 3),
# RECODED (built by code that no longer exists, exit 4) and DRIFT (a warning).
# It is a prerequisite of `make build` rather than advice, because the failures
# it replaces were four not_null failures naming no step, and an accepted_values
# failure in BigQuery four models downstream of a derived zone holding r9 cells
# ADR-10 had deleted.
check-derived: ## Check data/derived against data/raw and the code. Nonzero if not current.
	@$(PY) ingestion/check_derived.py --strict

# The raw zone's own consistency: does each run manifest describe the Parquet
# beside it? Two verdicts, MISCOUNTED (exit 3) and UNRECORDED (exit 4). Unlike
# check-derived this is NOT a prerequisite of anything, and the difference is
# what the two failures do to a build. A stale derived zone makes a build wrong;
# a manifest that misdescribes the zone makes mart_pipeline_freshness wrong and
# every model correct, so wedging a build on it would cost more than it saves.
# It runs in ci-build instead, on the fixture zone, where it needs no
# credentials and no bucket. PLAN-7 step 1.
check-runs: ## Check the raw zone's run manifests against the Parquet they describe.
	@$(PY) ingestion/check_runs.py --strict

# The one thing in this project that deletes part of the record, and the second
# exception to ADR-4's append-only rule after `ingest.py --full-refresh`.
# ADR-14 is the argument; PLAN-9 is why it was needed. It only ever considers
# datasets the registry marks `refresh: snapshot`, it proves per partition that
# a surviving later one holds every grain_key at values no older, and it exits 3
# rather than deleting anything it cannot prove.
#
# BY HAND AND NOT ON A SCHEDULE, which is PLAN-9's first open question answered
# in ADR-14. It is the same way `make publish` is operated and for a stronger
# reason: a cron that deletes data is a different risk appetite from a cron that
# writes some.
#
# Two targets rather than a flag, because the safe one has to be the one that is
# easy to type. `make prune-raw` reports and deletes nothing. `make prune-raw-apply`
# deletes. Point them at the bucket the way everything else here is pointed at
# it: `set -a; source .env; set +a`.
prune-raw: ## Report which raw partitions a later one supersedes. Deletes nothing.
	$(PY) ingestion/prune_raw.py $(PRUNE_ARGS)

prune-raw-apply: ## (destructive) Delete the raw partitions prune-raw proved superseded.
	$(PY) ingestion/prune_raw.py --apply $(PRUNE_ARGS)

# ---------------------------------------------------------------------------
# Load: both zones -> warehouse. Idempotent, so re-running is always safe.
# ---------------------------------------------------------------------------

load: ## Load data/raw and data/derived into the local DuckDB file
	$(PY) ingestion/load.py --all --target duckdb

load-bigquery: ## (creds) Load data/raw into BigQuery
	$(PY) ingestion/load.py --all --target bigquery

# ---------------------------------------------------------------------------
# dbt. DuckDB is the default target (ADR-1); BigQuery is the named one.
# ---------------------------------------------------------------------------

build: $(BUILD_PREREQS) ## dbt build against DuckDB: run + test in dependency order
	cd $(DBT_DIR) && $(DBT) build

build-bigquery: $(BUILD_PREREQS) ## (creds) dbt build against BigQuery
	cd $(DBT_DIR) && $(DBT) build --target bigquery

test: ## dbt test only, against DuckDB
	cd $(DBT_DIR) && $(DBT) test

# ---------------------------------------------------------------------------
# Cross-engine agreement. Neither of these is in CI and neither should be:
# both need credentials, and ADR-1 requires the PR gate to run without any.
#
# The two ask different questions, and parity-columns is the cheap one to reach
# for first. It compares the zone's column sets against the BigQuery tables and
# needs no local build at all, so it can run straight after load-bigquery.
# parity-check compares rows, so both engines must have been built from THE SAME
# ZONE first, and that is easy to get wrong: a DuckDB file loaded from data/raw
# against BigQuery reading the bucket reports a zone difference as a defect. Run
# `make load build` in the same shell as `make load-bigquery`, or point it at a
# throwaway file with DUCKDB_PATH=... to leave your local warehouse alone.
# ---------------------------------------------------------------------------

parity-columns: ## (creds) Compare the zone's column sets against BigQuery. PLAN-7 step 2.
	$(PY) scripts/parity-check.py --columns

parity-check: ## (creds) Compare staging models row for row across both engines
	$(PY) scripts/parity-check.py --all-staging

# ---------------------------------------------------------------------------
# Publish: warehouse -> one Parquet file per mart, plus a manifest (ADR-8,
# ADR-12).
#
# Local by default and blocked on nothing. Add a bucket with:
#   make publish PUBLISH_DEST=gs://my-bucket/sf
#   make publish PUBLISH_DEST=r2://my-bucket/sf
#
# ONE FILE PER MART, NOT HIVE PARTITIONS, AND THAT IS A MEASUREMENT (ADR-12).
# The two activity marts were partitioned by month over a range starting in
# 1849, which cost 2,280 objects a publish against a free tier of 5,000 Class A
# operations a month. The deciding number was the bytes rather than the count:
# month partitioning cost 5.8x the bytes of the same data, because the median
# partition held 40 rows and a 5 KB Parquet file is mostly footer. A publish is
# now 7 objects and 3.0 MB. The partitioning mechanism stays for the day a mart
# has enough rows per partition to want it; see PUBLISHED_MARTS in
# publish/export.py.
#
# THE UPLOADER COPIES AND NEVER DELETES, which is why the bucket held the 2,280
# objects of the pre-ADR-12 partitioned layout beside the 7 of the current one,
# plus a mart ADR-10 cut. PUBLISH_PRUNE=1 removes what the export did not write,
# after the manifest lands so ADR-8's ordering survives, printing every object.
# It is off by default and only meaningful with PUBLISH_DEST:
#   make publish PUBLISH_DEST=gs://my-bucket/sf PUBLISH_PRUNE=1
# Run it when MANIFEST_VERSION changes or a mart leaves PUBLISHED_MARTS, which
# are the two ways an orphan gets created. PLAN-9 step 6.
# ---------------------------------------------------------------------------

PUBLISH_DEST ?=
PUBLISH_PRUNE ?=

publish: ## Export marts to published/ as one Parquet file each with a manifest
	$(PY) publish/export.py --all \
		$(if $(PUBLISH_DEST),--destination $(PUBLISH_DEST),) \
		$(if $(PUBLISH_PRUNE),--prune,)

# ---------------------------------------------------------------------------
# The context pack: what a model must know about this warehouse, and what it
# must refuse to answer (PLAN-6, docs/specs/context-pack.md).
#
# ONE PACK PER TARGET, TWO OF THEM GENERATED, AND ONE TARGET HERE WITH A
# VARIABLE. `make context-pack` is the duckdb pack and
# `make context-pack TARGET=published` is the export's; the same variable works
# on the check. That is PLAN-8's first open question, answered once the second
# pack existed rather than before (2026-08-07): the two commands are the same
# command apart from the target name, and what differs between them is which
# artifact has to exist first, which generate.py already refuses with the right
# sentence when it does not. A target per pack would have meant two copies of
# the comment below, and that comment is the part that must not drift.
#
# The published pack reads $PUBLISH_DIR rather than the warehouse, so it needs
# `make publish` and not `make build`. Neither needs credentials, which is what
# lets CI check both; bigquery is declared in pack_target.py, needs credentials,
# has no schema hash, and is PLAN-8 step 6.
#
# Both artifacts are COMMITTED, unlike everything else this repo generates.
# That is the point of PLAN-6 step 4: a model change that moves the pack shows
# up as a diff in the pull request rather than as a pack nobody regenerated.
#
# Generation reads the dbt manifest and the warehouse, so `make build` has to
# have run. It fails, rather than warning, on a model with no grain sentence, a
# refusal citing something this target does not have, an example query that
# errors or whose SQL was edited without re-verification, and a markdown
# rendering that cannot fit the budget with every refusal present.
#
# RUN IT AFTER `make build` AND NOT AFTER `make check`. The pack records the
# dbt invocation in dbt/target/manifest.json as the build its numbers came
# from, and `make ci-build` overwrites that manifest with the fixture build's
# while leaving data/sf.duckdb alone. Generating in between produces a pack
# whose row counts are the real warehouse's and whose invocation id is a
# fixture run's. Nothing detects that, because comparing invocation ids in
# context-pack-check would fail after every dbt run.
#
# TOKEN_BUDGET is the markdown budget in estimated tokens. The default is in
# generate.py beside the measurement that chose it.
# ---------------------------------------------------------------------------

TOKEN_BUDGET ?=
TARGET ?= duckdb

context-pack: ## Generate the TARGET context pack (duckdb or published) into context-pack/
	$(PY) tools/context_pack/generate.py --target $(TARGET) \
		$(if $(TOKEN_BUDGET),--token-budget $(TOKEN_BUDGET),)

# The drift gate, and the thing to run before opening a pull request that
# touched a model. Compares the committed pack's integrity block against the
# live warehouse and exits 3 when they disagree. Row counts are not compared:
# they move on every ingest, and a gate that fires daily gets switched off.
context-pack-check: ## Does the committed TARGET pack still describe its target? Nonzero if not.
	$(PY) tools/context_pack/generate.py --target $(TARGET) --check

# compile renders every model to real SQL without touching a warehouse, which
# is what catches engine-specific syntax that slipped past the cross_engine
# macros. Both of these run without real credentials, including the BigQuery
# one. That is what makes a cross-engine check possible on a fork pull request.
compile-duckdb: ## Parse and compile against DuckDB. No credentials needed.
	mkdir -p $(RAW_DIR)
	cd $(DBT_DIR) && $(DBT) parse --target duckdb
	cd $(DBT_DIR) && $(DBT) compile --target duckdb

# Three things are load bearing here, and the order matters.
#
# All three were measured on 2026-08-03 rather than reasoned about, because a
# comment asserting a property a dependency bump can revoke is a test that never
# runs. Neither of the first two is cargo; do not delete one to tidy up.
#
# The throwaway key. This target used to rest on "compiling does not open a
# connection", a property of dbt's internals that broke on two separate dbt
# upgrades. It is false: the dbt.log from the green CI run on 2034062 shows
# 197 opens on one compile, one `master` from before_run and one per node,
# fired at "Began executing node". scripts/fake-bq-key.sh replaces that
# property with one this repo owns: building a BigQuery client from a service
# account key is local and offline, so all 197 cost nothing and reach nothing.
# The same command without the key is the failure of 2026-08-02, "Your default
# credentials were not found".
#
# --no-populate-cache. The only thing still standing between this target and
# the network. Dropping it exits 2 with
# populate_adapter_cache -> set_relations_cache -> list_relations_without_caching
# -> RefreshError "invalid_grant: Invalid grant: account not found", which is
# Google refusing the fake key at token fetch. Compiling itself issues no query
# at all: that same log has 197 opens and zero statements.
#
# The environment is overridden, not inherited. Both variables are set here so
# that this target runs identically in CI and in a shell that has sourced .env.
# Inheriting them is what hid the CI failure for two sessions, and the reason is
# narrower than "the developer sourced .env": dbt itself runs
# `load_dotenv(find_dotenv(usecwd=True), override=False)` at CLI startup, so any
# bare `dbt compile --target bigquery` run from inside this repo silently picks
# up the real GOOGLE_APPLICATION_CREDENTIALS out of .env and passes for the
# wrong reason. override=False is why exporting them here still wins, and why
# this target is the only trustworthy local reproduction of the CI job.
compile-bigquery: ## Parse and compile against BigQuery. No credentials needed.
	@key="$$(bash scripts/fake-bq-key.sh)"; \
	export GOOGLE_APPLICATION_CREDENTIALS="$$key"; \
	export GCP_PROJECT_ID=compile-only-no-connection; \
	cd $(DBT_DIR) && \
	$(DBT) parse --target bigquery && \
	$(DBT) compile --target bigquery --no-populate-cache

# ---------------------------------------------------------------------------
# Docs
#
# dbt writes docs into dbt/target/, which is gitignored and always will be.
# The two machine-readable artifacts are copied to docs/dbt/ and committed, so
# the project graph and column catalogue are readable from a clone without
# building anything. index.html is deliberately not copied: it is a 1.7 MB
# bundled viewer that is rewritten wholesale on every dbt upgrade.
# ---------------------------------------------------------------------------

docs: ## Generate dbt docs and refresh the committed artifacts in docs/dbt/
	cd $(DBT_DIR) && $(DBT) docs generate
	mkdir -p $(DOCS_ARTIFACTS)
	cp $(DBT_DIR)/target/manifest.json $(DBT_DIR)/target/catalog.json $(DOCS_ARTIFACTS)/
	@echo "Refreshed $(DOCS_ARTIFACTS)/manifest.json and catalog.json"

docs-serve: docs ## Generate docs and serve the browsable site locally
	cd $(DBT_DIR) && $(DBT) docs serve

# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------

# The only Python unit tests in the project, and the fastest gate in the set:
# a tenth of a second, no warehouse, no fixtures, no dbt and no network. What
# they cover is ingestion/geometry.py, the hand-written point-in-polygon and
# spherical area that every boundary assignment rests on and that every other
# test in this project can only reach through SQL. `check` runs it first and
# ci.yml gates the end-to-end dbt job on it, for the same reason: when the
# geometry is wrong, everything downstream of it is wrong in a way that reads
# as a modelling failure.
test-python: ## Run the pytest suite. No warehouse, no fixtures, no network.
	$(VENV)/bin/pytest tests -q

# The sqlfluff line delegates to the script rather than spelling the command
# out, so `make lint` and the pre-commit hook cannot disagree about which
# models get linted or with which options. The script sets DBT_PROFILES_DIR
# and DUCKDB_PATH itself and honours the ones exported above, so running it
# from here is the same run either way.
lint: ## Run ruff and sqlfluff
	@bash scripts/check-lint-pins.sh
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	bash scripts/sqlfluff-lint.sh

fmt: ## Auto-fix what ruff and sqlfluff can fix
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .
	$(VENV)/bin/sqlfluff fix $(DBT_DIR)/models --processes 4

leak-check: ## Scan the working tree for credentials. Exits nonzero on a hit.
	@bash scripts/leak-check.sh

# What CI runs on a pull request, end to end and credential-free: build a raw
# zone from fixtures, reconcile its run manifests against it, load it, run and
# test every model, then drop the warehouse and do the load and build again to
# prove the Parquet is genuinely the source of truth. Isolated in data/ci/, so
# it never touches data/raw.
ci-build: ## Full pipeline from fixtures, isolated. No network, no creds.
	rm -rf $(CI_DIR)
	mkdir -p $(CI_RAW)
	RAW_ZONE_DIR=$(CI_RAW) $(PY) ingestion/ingest.py --all --fixtures tests/fixtures/socrata
	RAW_ZONE_DIR=$(CI_RAW) $(PY) ingestion/check_runs.py --strict
	RAW_ZONE_DIR=$(CI_RAW) DERIVED_ZONE_DIR=$(CI_DERIVED) $(PY) ingestion/spatial.py --all
	RAW_ZONE_DIR=$(CI_RAW) DERIVED_ZONE_DIR=$(CI_DERIVED) DUCKDB_PATH=$(CI_DB) \
		$(PY) ingestion/load.py --all --target duckdb
	RAW_ZONE_DIR=$(CI_RAW) DERIVED_ZONE_DIR=$(CI_DERIVED) \
		$(PY) ingestion/check_derived.py --strict
	cd $(DBT_DIR) && DUCKDB_PATH=$(CI_DB) $(DBT) build
	PUBLISH_DIR=$(CI_PUBLISHED) DUCKDB_PATH=$(CI_DB) $(PY) publish/export.py --all
	@echo ""
	@echo "Dropping the warehouse and rebuilding from the zones alone..."
	rm -f $(CI_DB) $(CI_DB).wal
	RAW_ZONE_DIR=$(CI_RAW) DERIVED_ZONE_DIR=$(CI_DERIVED) DUCKDB_PATH=$(CI_DB) \
		$(PY) ingestion/load.py --all --target duckdb
	cd $(DBT_DIR) && DUCKDB_PATH=$(CI_DB) $(DBT) build

check: test-python lint leak-check compile-bigquery ci-build ## Everything CI runs on a PR, locally

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

# The point of the split raw zone: this drops the warehouse and rebuilds every
# model from Parquet, without going near the API. If it does not reproduce
# what you had, the raw zone is not the source of truth it claims to be.
rebuild: clean-warehouse spatial load build ## Rebuild the warehouse from data/raw. No network.

# The whole pipeline in order, for when you want the lot and do not want to
# remember which step feeds which.
all: ingest spatial load build ## ingest, spatial, load, build. Needs network for the first.

clean-warehouse: ## Delete the DuckDB file. Leaves data/raw alone.
	rm -f $(DATA_DIR)/*.duckdb $(DATA_DIR)/*.duckdb.wal

# The derived zone is a pure function of the raw zone plus the code, so unlike
# data/raw it is always safe to delete: `make spatial` reproduces it exactly.
# It is also the blunt way to force a full rebuild;
# `make spatial SPATIAL_ARGS=--full` is the same thing without the delete.
clean-derived: ## Delete data/derived. Recreate it with `make spatial`.
	rm -rf $(DATA_DIR)/derived

clean: clean-warehouse ## Remove build artifacts, the venv, and the local DuckDB file
	rm -rf $(VENV)
	rm -rf $(DBT_DIR)/target $(DBT_DIR)/dbt_packages $(DBT_DIR)/logs
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "Clean. Note: $(RAW_DIR) was left alone; it is the raw zone (ADR-1, ADR-4)."
