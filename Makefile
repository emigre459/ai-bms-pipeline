## Run pytest with uv package manager to see how tests go
tests:
	uv run pytest -v --tb=short -n auto

## Set up python interpreter environment
env_create:
	@echo "Creating new environment"
	@uv sync

## Run black on key folders
black:
	@poetry run black tests/ roadtrip_tools/ apis/ app/ scripts/

## Run black in check mode
black_check:
	@uv run black --check tests/ src/ scripts/

## Test to run to ensure PR readiness
pr_check: black black_check tests