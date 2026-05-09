PYTHONPATH := $(shell pwd)
export PYTHONPATH

ifneq (,$(wildcard .env))
    include .env
    export
endif

.PHONY: all ingest clean-data transform test lint docs clean

# Full pipeline end to end
all: ingest clean-data load transform test

# Fetch all FRED series from API
ingest:
	python scripts/ingest.py

# Clean raw data
clean-data:
	python scripts/clean_data.py

# Load cleaned parquet into Postgres raw schema
load:
	python scripts/load_to_postgres.py

# Run dbt transformations
transform:
	cd dbt_project && dbt run

# Run all tests
test:
	pytest tests/ -v --tb=short --cov=ingestion --cov=transforms --cov-report=term-missing

# Lint
lint:
	ruff check ingestion/ transforms/

# Generate and serve dbt docs
docs:
	cd dbt_project && dbt docs generate && dbt docs serve

# Clean generated files
clean:
	rm -rf data/raw/*.parquet data/raw/_revision_hashes.json
	rm -rf data/cleaned/*.parquet
	rm -rf dbt_project/target
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
