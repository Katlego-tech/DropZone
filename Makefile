IMAGE ?= dropzone:local
PORT  ?= 8000

.DEFAULT_GOAL := help
.PHONY: help install lint format test cov ci image serve container clean \
	contracts-build contracts-test contracts-fmt contracts-lint contracts anvil deploy-local

help:  ## Show the available targets
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

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

ci: lint test contracts image  ## Everything the pipeline runs - reproduce a CI failure locally

image:  ## Build the container image
	docker build -t $(IMAGE) .

serve:  ## Run the API locally with reload
	uv run uvicorn dropzone.api.app:app --reload --port $(PORT)

container:  ## Run the built image
	docker run --rm -p $(PORT):8000 $(IMAGE)

clean:  ## Remove build and test artefacts
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	rm -rf contracts/out cache broadcast
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# --- Solidity -----------------------------------------------------------------
# Foundry is rooted at the repository (see foundry.toml), so these run from here
# rather than from contracts/.

contracts-build:  ## Compile the contracts
	forge build

contracts-test:  ## Run the contract test suite
	forge test

contracts-fmt:  ## Check Solidity formatting
	forge fmt --check

contracts-lint:  ## Run forge lint
	forge lint

# Tests before the linter: forge lint updates the build cache without writing
# artefacts, so linting first leaves forge test with nothing to run, and a suite
# that runs zero tests looks exactly as green as one that passes.
contracts: contracts-fmt contracts-build contracts-test contracts-lint  ## Everything CI runs against the contracts

anvil:  ## Run a local chain in the foreground
	anvil

# ACCOUNT is anvil's first well-known development key. It is public, funded only
# on a throwaway local chain, and must never be what deploys anywhere else -
# use --account with a keystore for any real network.
LOCAL_KEY ?= 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
RPC_URL   ?= http://127.0.0.1:8545

deploy-local: ## Deploy to a running anvil (make anvil in another shell)
	forge script contracts/script/Deploy.s.sol \
		--rpc-url $(RPC_URL) --broadcast --private-key $(LOCAL_KEY)
