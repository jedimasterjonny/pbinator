set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

sync:
    uv sync --locked

lint:
    uv run ruff check .

lint-fix:
    uv run ruff check --fix .

fmt:
    uv run ruff format .

fmt-check:
    uv run ruff format --check .

typecheck:
    uv run ty check .

test:
    uv run pytest

check: lint fmt-check typecheck test

hooks:
    uv run pre-commit install

update-template:
    uvx copier update --trust

pre-commit:
    uv run pre-commit run --all-files

build:
    uv build

clean:
    rm -rf .venv .ruff_cache .pytest_cache .ty_cache htmlcov .coverage coverage.xml junit.xml dist build
