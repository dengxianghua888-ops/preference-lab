# Quota admission after SQLite write-lock waits

Base: `1267bad8bc66b914975ed6f18fd6a38d275c236b` (PR #31 merged).

## Reproduced issue

`Quota.acquire` sampled wall time before `BEGIN IMMEDIATE`. SQLite may block
there while another process owns the write lock. The stale time was then used
to check the run deadline, expire leases, count the 60-second rate window and
timestamp the new lease.

Two independent SQLite connections reproduced the failure without model calls:

- Hold the write lock for 200 ms with 50 ms remaining on the run deadline.
  Before the fix, admission returned after about 229 ms and created one lease
  past the deadline. After the fix, it paused with zero leases.
- Hold the write lock for 1.3 seconds with a 100 ms request timeout and a
  one-request concurrency cap. Before the fix, the first lease was expired
  on arrival and a second lease was granted while the first was unreleased.
  After the fix, the second waiter paused and only one lease existed.

## Change and validation

Resample time immediately after acquiring the SQLite write transaction and
recheck the run deadline before any quota mutation. All expiration, window and
new-lease calculations use that refreshed value. The early deadline check is
retained to avoid starting a known-expired wait.

Four deterministic regressions use real SQLite transactions and a controlled
clock to cover deadline refusal, grant-time timestamps, concurrency exclusion
and rolling-window refresh. All four fail against the base implementation and
pass with this change. The existing cross-process shared-quota test is retained.
Final suite, build and exact-head CI results are recorded in the pull request.

This preserves the quota schema, public API and configured limits. It does not
make SQLite lock waits asynchronous or hard-bounded by the run timeout. It
does not change wall-clock adjustment behavior, remote request cancellation,
provider accounting or exactly-once guarantees. Live services, independent
human labels and training effectiveness remain unverified.

## Open-source practice and license

[redis-py Lock.do_acquire](https://github.com/redis/redis-py/blob/ed805593c77085249ce01a7df1f97953743ba061/redis/lock.py)
uses atomic `SET NX PX` to couple successful acquisition and expiry in Redis.
Its [license](https://github.com/redis/redis-py/blob/ed805593c77085249ce01a7df1f97953743ba061/LICENSE)
is MIT. The relevant principle is to attach lease validity to actual admission,
not to time sampled before waiting for ownership. Here that means sampling
inside the already-acquired SQLite transaction. No Redis integration, new
dependency or third-party source code is added.
