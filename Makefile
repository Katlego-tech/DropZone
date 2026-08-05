IMAGE ?= dropzone:local
PORT  ?= 8000

.DEFAULT_GOAL := help
.PHONY: help install lint format test cov ci image serve container clean

help:  ## Show the available targets
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install:  ## Install the pinned dependency set
	uv sync --locked

lint:  ## Check formatting and lint rules
	uv run ruff format --check .
	uv run ruff check .

format:  ## Apply formatting and safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .

test:  ## Run the test suite
	uv run pytest

cov:  ## Run the test suite with a coverage report
	uv run pytest --cov=dropzone --cov-report=term-missing

ci: lint test image  ## Everything the pipeline runs — reproduce a CI failure locally

image:  ## Build the container image
	docker build -t $(IMAGE) .

serve:  ## Run the API locally with reload
	uv run uvicorn dropzone.api.app:app --reload --port $(PORT)

container:  ## Run the built image
	docker run --rm -p $(PORT):8000 $(IMAGE)

clean:  ## Remove build and test artefacts
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
