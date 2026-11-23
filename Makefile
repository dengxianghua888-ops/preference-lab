.PHONY: help install test lint type-check format run dashboard clean

help: ## Show help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install in editable mode with all extras
	pip install -e ".[all]"

run: ## Run quickstart example
	python examples/quickstart.py

dashboard: ## Start Streamlit dashboard
	python -m streamlit run dashboard/app.py --server.port 8501

test: ## Run tests
	pytest tests/ -v --cov=ecoalign_forge --cov-report=term-missing

lint: ## Lint check
	ruff check .
	ruff format --check .

type-check: ## Type-check the package (see [tool.mypy] overrides for remaining exemptions)
	python -m mypy

format: ## Auto-format
	ruff check --fix .
	ruff format .

clean: ## Clean artifacts
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
