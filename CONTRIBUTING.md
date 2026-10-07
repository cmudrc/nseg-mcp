# Contributing to `nseg-mcp`

Thanks for improving the project.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
make dev
```

The `dev` extra installs linting, formatting, typing, testing, and pre-commit tooling.

## Local Quality Checks

Run these before opening a pull request:

```bash
make fmt
make lint
make type
make test
```

Optional but recommended:

```bash
pre-commit install
pre-commit run --all-files
```

## Pull Request Guidelines

- Keep changes focused.
- Add or update tests for behavior changes.
- Update docs and examples when user-facing workflows change.
- Describe what changed and how you validated it.

## Code Style

- Python 3.12+ target.
- Ruff for formatting and linting.
- Mypy for static type checking.
- Pytest for tests.

## Backend

NSEG: built-in segment physics, with no extra dependencies. NASA Aviary
missions are a separate server,
[`aviary-cpacs-mcp`](https://github.com/cmudrc/aviary-cpacs-mcp); the caller
chooses one of the two per run.
