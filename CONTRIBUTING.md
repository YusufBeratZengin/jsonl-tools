# Contributing

Use Python 3.10+ and synthetic examples. Work from a local source checkout.

```console
python -m pip install -e ".[dev]"
python -m ruff check .
python -m mypy src
python -m pytest --cov=jsonl_tools --cov-report=term-missing --cov-fail-under=85
python scripts/make-demo.py --check
python -m build
python -m twine check dist/*
```

Tests must run without network on Windows and Linux. Add behavior-based regression
tests and preserve at least 85% coverage including branches. Regenerate demo
artifacts with `python scripts/make-demo.py` whenever relevant output changes.
Keep English and Turkish READMEs aligned. Use Conventional Commits (`feat`, `fix`,
`test`, `docs`, `ci`, `chore`). Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Never include private data, credentials, machine-specific paths or generated caches.

## Offline and restricted environments

Runtime uses only Python's standard library. Without a build backend, set
`PYTHONPATH=src` in your shell and use `python -m jsonl_tools`. On PowerShell the
equivalent is `$env:PYTHONPATH = 'src'`; on POSIX shells use `export PYTHONPATH=src`.
Pytest already includes `src` in its configured import path. Development checks
need the tools declared in `.[dev]`; use installed versions when package installation
is unavailable. Missing tools or blocked filesystem/subprocess tests must be
reported as unverified, never marked as passing. Keep temporary work under `is/`.

## Releases

Only maintainers release. Update CHANGELOG, bump `pyproject.toml` and
`src/jsonl_tools/__init__.py` together, run all checks and inspect the distributions
with `twine check`. Commit as `chore(release): vX.Y.Z`; tag `vX.Y.Z` only when the
version matches and the release is authorized. The publish workflow builds and
checks the tag, then uses the protected `pypi` environment with PyPI trusted
publishing (OIDC). Configure that pending publisher and environment approval
before any release; no API token is required. Pushing a release tag triggers
publication, so do not push tags during normal development.

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
