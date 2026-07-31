# sf-data-warehouse
#
# Entry point for every routine task. See CLAUDE.md for context and
# docs/decisions/ for why things are the way they are.
#
# The pipeline is five steps, and they are separate on purpose (ADR-4, ADR-5):
#
#   make ingest    APIs     -> data/raw/*.parquet    needs network, no creds
#   make spatial   data/raw -> data/derived          needs neither
#   make load      both     -> DuckDB or BigQuery    needs neither (DuckDB)
#   make build     dbt run + test on the warehouse   needs neither (DuckDB)
#   make publish   warehouse -> published/           needs neither (local)
#
# `spatial` sits between ingest and load because it reads the Parquet zone
# and writes another one. Forgetting it does not break the build: the spatial
# models come out empty and the marts come out with no rows, which is a
# quieter failure than it sounds, so `make all` exists to run the four in
# order.
#
# Only the BigQuery targets need Google Cloud credentials, and they are
# marked (creds). Load them with:
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
        publish test docs docs-serve lint fmt leak-check compile-duckdb compile-bigquery \
        ci-build rebuild clean clean-warehouse clean-derived check check-derived

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
# Pure function of data/raw plus ingestion/spatial.py, so it is always safe to
# delete data/derived and re-run. Takes a few minutes on the full raw zone,
# most of it in the exact point-in-polygon refinement and the oracle sample.
# ---------------------------------------------------------------------------

spatial: ## Compute H3 cells and boundary membership into data/derived
	$(PY) ingestion/spatial.py --all

# The other half of "forgetting spatial does not error". spatial.py records the
# raw row count it read per dataset in data/derived/_manifest.json, and this
# compares that against the raw zone now. It is a prerequisite of `make build`
# rather than advice, because the failure it replaces was four not_null
# failures and fifty-one skips, none of which named the step that was skipped.
check-derived: ## Check data/derived is current with data/raw. Nonzero if stale.
	@$(PY) ingestion/check_derived.py --strict

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
# Publish: warehouse -> partitioned Parquet plus a manifest (ADR-8).
#
# Local by default and blocked on nothing. Add a bucket with:
#   make publish PUBLISH_DEST=r2://my-bucket/sf
# ---------------------------------------------------------------------------

PUBLISH_DEST ?=

publish: ## Export marts to published/ as partitioned Parquet with a manifest
	$(PY) publish/export.py --all \
		$(if $(PUBLISH_DEST),--destination $(PUBLISH_DEST),)

# compile renders every model to real SQL without touching a warehouse, which
# is what catches engine-specific syntax that slipped past the cross_engine
# macros. Both of these run without credentials, including the BigQuery one:
# compiling does not open a connection. That is what makes a cross-engine
# check possible on a fork pull request.
compile-duckdb: ## Parse and compile against DuckDB. No credentials needed.
	mkdir -p $(RAW_DIR)
	cd $(DBT_DIR) && $(DBT) parse --target duckdb
	cd $(DBT_DIR) && $(DBT) compile --target duckdb

compile-bigquery: ## Parse and compile against BigQuery. No credentials needed.
	cd $(DBT_DIR) && $(DBT) parse --target bigquery
	cd $(DBT_DIR) && $(DBT) compile --target bigquery

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
# zone from fixtures, load it, run and test every model, then drop the
# warehouse and do the load and build again to prove the Parquet is genuinely
# the source of truth. Isolated in data/ci/, so it never touches data/raw.
ci-build: ## Full pipeline from fixtures, isolated. No network, no creds.
	rm -rf $(CI_DIR)
	mkdir -p $(CI_RAW)
	RAW_ZONE_DIR=$(CI_RAW) $(PY) ingestion/ingest.py --all --fixtures tests/fixtures/socrata
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

check: lint leak-check compile-bigquery ci-build ## Everything CI runs on a PR, locally

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

# The derived zone is a pure function of the raw zone, so unlike data/raw it
# is always safe to delete: `make spatial` reproduces it exactly.
clean-derived: ## Delete data/derived. Recreate it with `make spatial`.
	rm -rf $(DATA_DIR)/derived

clean: clean-warehouse ## Remove build artifacts, the venv, and the local DuckDB file
	rm -rf $(VENV)
	rm -rf $(DBT_DIR)/target $(DBT_DIR)/dbt_packages $(DBT_DIR)/logs
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "Clean. Note: $(RAW_DIR) was left alone; it is the raw zone (ADR-1, ADR-4)."
