# Contributing

Use Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/) on macOS/Linux. From the repository root, run:

```bash
uv sync --locked --python 3.12 --extra dev --extra dashboard
source .venv/bin/activate
python -m pytest tests -q
python -m ruff check . && python -m ruff format --check .
make type-check
python scripts/accept_iteration_c.py --output /tmp/ecoalign-c
```

Use `--python 3.11` instead if that is your installed version. If you use pip
instead, create and activate a Python 3.11 or 3.12 virtual environment before running
`python -m pip install -e '.[dev,dashboard]'`. This alternative is unlocked: pip
does not use `uv.lock` and may resolve different versions.

CI uses `uv.lock` for the project environment's locked dependency versions and hashes. After changing
dependencies in `pyproject.toml`, run `uv lock` and commit the updated lock file;
Dependabot proposes upgrades weekly. Keep the litellm exclusions for known
compromised releases when raising its upper bound.

The whole repository is formatted with `ruff format`. The type check covers the
whole package, including untyped function bodies; modules listed in the
`[[tool.mypy.overrides]]` block of `pyproject.toml` still have known errors and are
exempt. Remove a module from that list once it passes; do not add new ones.

Tests block external LLM calls. Do not add secrets, real customer text or provider
responses to fixtures. Add a failing contract regression for behavioral changes:
review revisions, evidence gates, counts, mode separation and immutable exports are
public contracts. API mistakes must fail explicitly, not silently drop options.

Describe the concrete problem, resulting behavior, compatibility impact and actual
verification in your PR. Preserve F01–F14 IDs in docs/review-findings.csv; mark only
verified scope complete. Keep model quality, browser checks, human acceptance and
training results distinct. New model/network integrations need controlled test
doubles and cost/unknown-request accounting before any live validation.

For bugs, provide version/commit, Python/OS, a redacted reproducer, expected/actual
behavior, run state and relevant hashes. Never send credentials. Report suspected
credential exposure privately through GitHub security reporting when available;
do not publish the secret in an issue.

See [release gates](docs/releasing.md), [C contracts](docs/iteration-c-migration.md)
and [first-user protocol](docs/first-user-acceptance.md).
