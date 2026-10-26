# Runtime accounting and recovery identity

## Behavior

- Budget checks read one constant-size projection row instead of loading, sorting and transferring all historical raw responses twice per snapshot.
- New attempts and settlements update exact Decimal totals in the same SQLite transaction as their authoritative rows. No SQLite floating-point sums are used. Amount addition also avoids the ambient Decimal precision rounding away a small reservation.
- Unknown attempts keep their reservations even after explicit retry or skip. A saved response remains reserved until settlement; repeated settlement replaces, rather than duplicates, its contribution.
- Older schema-2 journals rebuild the projection once from `cost`, `reservation` and `usage` only. Invalidation triggers cover older writers and direct attempt changes. Read-only inspection can compute a missing projection without writing it. Responses, requests and exports are unchanged.
- New manifests record Python implementation/version, SQLite version and selected request, validation and tokenizer distribution versions. Resume blocks changed or missing identities by default and names the differing fields. An explicit `--allow-environment-change` / `allow_environment_change=True` choice preserves the original manifest and appends an auditable environment revision. Code/template/policy checks still apply.

See [recovery choices and compatibility limits](iteration-b-migration.md#journals-recovery-and-statuses). This is a targeted fingerprint, not a complete dependency lock or a claim that cross-version resumes are equivalent.

## Reproducible scale check

Run `python scripts/benchmark_budget.py`. This no-network fake-transport benchmark uses the real request controller, journal, response persistence, parsing and accounting. Each synthetic sample makes three stage requests; each group of 100 adds one generation request. Shared quota waiting is replaced by an immediate local stub to isolate accounting. It is not a full synthesis/semantic-quality benchmark and does not measure an old/new speedup.

Observed on Linux / Python 3.12.14 in the cloud test workspace, 2026-10-15, with 4,111-byte JSON response bodies:

| Samples | Requests | Budget SELECTs / returned rows | Budget response bytes returned | Budget time | Total time |
| --- | ---: | ---: | ---: | ---: | ---: |
| 100 | 301 | 1,204 / 1,204 | 347,518 | 0.011 s | 0.10 s |
| 1,000 | 3,010 | 12,040 / 12,040 | 3,537,577 | 0.134 s | 1.11 s |
| 10,000 | 30,100 | 120,400 / 120,400 | 35,979,958 | 1.171 s | 9.98 s |

The four budget reads per request are the two pre-dispatch budget gates, attempt creation and settlement. Each reads one small summary row, with no historical response body. Times are machine-dependent observations; the deterministic regression asserts one returned row per snapshot at 100/1,000/10,000 historical attempts, rather than a fragile timing limit.

## Regression coverage

`tests/test_runtime_accounting.py` covers exact long Decimal amounts under reduced context precision, reservation retention, repeated settlement, transaction rollback, read-only legacy inspection, invalidation/rebuild, and constant-row hot-path reads. Existing SIGKILL/recovery tests continue to exercise request/response/stage/export boundaries.

`tests/test_runtime_identity.py` covers manifest identity, missing distributions, dependency/Python drift, no new attempts on rejection, explicit audit events without rewriting the original manifest, repeat resumes in the accepted environment, missing legacy identity, CLI acceptance, and the source-identity guard remaining mandatory.
