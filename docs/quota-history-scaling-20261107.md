# Quota admission with retained lease history

Base: `6c6fe629e1f5314592acaae6cd663a432c2f57ce` (PR #32 merged).

## Reproduced problem

Every admission or retry poll previously updated **all** expired leases, including
already-inactive historical rows, then scanned the lease table for concurrency and
rolling-window counts. The shared database retains leases across runs, so this
synchronous work grew with historical traffic even when no requests were active.
It also held SQLite's writer transaction for that work.

A local Python 3.12.14 / real SQLite probe seeded inactive, two-hour-old leases
split equally between the current and another group, then admitted one request:

| Historical leases | Before VM instructions | After VM instructions | Before changed rows | After changed rows |
| --- | ---: | ---: | ---: | ---: |
| 0 | <100 | <100 | 1 | 1 |
| 5,000 | 132,500 | <100 | 5,001 | 1 |
| 50,000 | 1,325,000 | <100 | 50,001 | 1 |

Instruction counts use SQLite's progress handler at 100-instruction intervals;
`total_changes` measures matched updates plus the insertion, not physical disk
writes. One 50,000-row local timing was 13.47 ms before and 0.33 ms after. These
are controlled local observations, not production throughput or latency promises.

## Change and acceptance

- An idempotent partial index on `(group_name, deadline) WHERE active=1` supports
  current-group expiration and active concurrency counts.
- A covering index on `(group_name, created, tokens)` supports the 60-second
  RPM/TPM window, including inactive but recently charged requests.
- Expiration updates only expired, active leases in the admitting group. Other
  groups reconcile their expired active flags on their own next admission.
- Existing tables, lease IDs, all history, token settlement, quota limits and
  transaction/clock ordering remain intact. Old databases acquire the indexes
  when opened; no migration command, new dependency or data deletion is needed.

The five regression cases in `tests/test_quota_history.py` require bounded VM
work with 6,000 inactive historical rows, no redundant historical updates, group
isolation, unchanged recent RPM/TPM charges and idempotent legacy-database index
creation. Against the base source: **3 failed, 2 passed**. After the change:
**5 passed**. The existing cross-process quota, lock-wait timing, token usage and
controller tests also pass (27 focused tests total). Full-suite and exact-head
CI results are recorded in the PR.

The query plans use indexed searches for all three admission statements. Runtime
cost still depends on the active leases and current rate window. This does not
bound total database storage, make SQLite asynchronous, alter provider quotas,
provide hard real-time deadlines or prove live-model quality. The initial index
creation scans existing history and can take a writer lock; large databases may
need a quiet startup window. Later opens retain the indexes. Existing older
writers remain compatible, but retain their own inefficient admission queries.

## Open-source reference and license

[PyrateLimiter's SQLite bucket at 341b2c8](https://github.com/vutran1710/PyrateLimiter/blob/341b2c81f869fb53aa893f07f02182745248c062/pyrate_limiter/buckets/sqlite_bucket.py)
creates an index on its item timestamp and evaluates counts over a bounded time
window. Its [license is MIT](https://github.com/vutran1710/PyrateLimiter/blob/341b2c81f869fb53aa893f07f02182745248c062/LICENSE).
That indexed-window approach informs this change. Its bucket leakage/deletion
mechanism is not adopted: preference-lab retains lease rows for late usage
settlement. This implementation is original SQL and tests; no upstream source
or dependency is copied. No external model calls or training were performed.
