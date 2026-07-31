# sf-data-warehouse
#
# Entry point for every routine task. See CLAUDE.md for context and
# docs/decisions/ for why things are the way they are.
#
# Targets that need Google Cloud credentials are marked. Load them with:
#   set -a; source .env; set +a

.DEFAULT_GOAL := help

VENV        := .venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
DBT         := $(CURDIR)/$(VENV)/bin/dbt
DBT_DIR     := dbt
DATA_DIR    := data
# dbt reads profiles.yml from here; every dbt target sets it explicitly so
# these work regardless of what is in your shell.
export DBT_PROFILES_DIR := $(CURDIR)/$(DBT_DIR)

.PHONY: help setup ingest build test docs lint fmt leak-check \
        compile-duckdb build-duckdb export-parquet rebuild clean check

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
	mkdir -p $(DATA_DIR)
	cd $(DBT_DIR) && $(DBT) deps || true
	$(VENV)/bin/pre-commit install || echo "pre-commit hook not installed (not a git repo?)"
	@chmod +x scripts/leak-check.sh
	@echo ""
	@echo "Setup complete. Next:"
	@echo "  make compile-duckdb   # no credentials needed, proves the install works"
	@echo "  make build            # needs credentials, see .env.example"

# ---------------------------------------------------------------------------
# Ingestion (creds)
# ---------------------------------------------------------------------------

ingest: ## (creds) Ingest all registered datasets into BigQuery
	$(PY) ingestion/ingest.py --all

export-parquet: ## (creds) Dump raw BigQuery tables to data/*.parquet (opt-in, ADR-1)
	$(PY) ingestion/export_parquet.py --all --out-dir $(DATA_DIR)

# ---------------------------------------------------------------------------
# dbt: BigQuery is the default target today (ADR-1)
# ---------------------------------------------------------------------------

build: ## (creds) dbt build against BigQuery: run + test in dependency order
	cd $(DBT_DIR) && $(DBT) build

test: ## (creds) dbt test only
	cd $(DBT_DIR) && $(DBT) test

docs: ## (creds) Generate and serve the dbt docs site
	cd $(DBT_DIR) && $(DBT) docs generate && $(DBT) docs serve

# ---------------------------------------------------------------------------
# dbt: DuckDB target. No credentials, no network.
#
# compile-duckdb is the credential-free gate CI runs on every PR: it parses
# the project and renders every model to SQL, which catches Jinja errors,
# broken refs, bad macro calls, and engine-specific syntax that slipped past
# the cross_engine macros.
#
# build-duckdb needs actual data in DuckDB and will fail until the Parquet
# raw zone is wired up. That is step 3 of
# docs/plans/plan-1-duckdb-parquet.md.
# ---------------------------------------------------------------------------

compile-duckdb: ## Parse and compile against DuckDB. No credentials needed.
	mkdir -p $(DATA_DIR)
	cd $(DBT_DIR) && $(DBT) parse --target duckdb
	cd $(DBT_DIR) && $(DBT) compile --target duckdb

build-duckdb: ## Full dbt build against DuckDB. Needs data/, see PLAN-1.
	mkdir -p $(DATA_DIR)
	cd $(DBT_DIR) && $(DBT) build --target duckdb

# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------

lint: ## Run ruff and sqlfluff
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/sqlfluff lint $(DBT_DIR)/models --processes 4

fmt: ## Auto-fix what ruff and sqlfluff can fix
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .
	$(VENV)/bin/sqlfluff fix $(DBT_DIR)/models --processes 4

leak-check: ## Scan the working tree for credentials. Exits nonzero on a hit.
	@bash scripts/leak-check.sh

check: lint leak-check compile-duckdb ## Everything CI runs on a PR, locally

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

rebuild: clean setup ingest build ## (creds) Full local rebuild from scratch

clean: ## Remove build artifacts, the venv, and the local DuckDB file
	rm -rf $(VENV)
	rm -rf $(DBT_DIR)/target $(DBT_DIR)/dbt_packages $(DBT_DIR)/logs
	rm -f $(DATA_DIR)/*.duckdb $(DATA_DIR)/*.duckdb.wal
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "Clean. Note: data/*.parquet was left alone; it is the raw zone (ADR-1)."
