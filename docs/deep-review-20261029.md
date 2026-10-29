# Byte identity at export and download boundaries — 2026-10-29

Baseline: `30bc7de3dadebc3161ca165c01f548a74d8213ed`. PRs #16 and #29
are merged; C's three-user completion is USER_REPORTED. F14 remains separate D
work. This review does not publish C or replace real user/model validation.

## Reproduced gaps

- Existing machine exports compared decoded text. Replacing LF with CRLF in
  `pairs.jsonl`, a TRL view, `policy.json` or `README.md` still allowed reuse,
  although their actual SHA-256 no longer matched the manifest. Immutable report
  reuse similarly accepted newline drift in all four report files. Comparisons
  now use actual UTF-8 bytes and reject drift without repairing or overwriting it.
- The dashboard verified a dataset, then reopened its paths to render trace and
  write ZIP entries. A same-count JSONL edit after verification entered the ZIP
  with the old manifest hash. The archive builder now verifies the dataset, then
  hashes the exact bytes supplied to each ZIP entry. Trace and download consume
  that finished archive. Changes before capture are rejected; changes after
  capture cannot change those archived bytes.

The archive keeps one file buffer plus the existing compressed ZIP, rather than
retaining all uncompressed export views. It preserves the existing manifest and
dataset format, dependencies, review gate and original machine records. This is
an in-memory consumer snapshot, not a filesystem lock, multi-file transaction or
guarantee that files cannot change afterward. The archived manifest is serialized
from the verified manifest; all listed file bytes retain their exact hashes.

## Verification and boundaries

`tests/test_export_byte_identity.py` uses actual demo journals, immutable exports,
reports and ZIP readback. It covers newline drift, changed bytes between
verification and archive capture, all ZIP member hashes, preservation of prior
bytes, and unchanged reuse. Existing report snapshot, budget and CLI regressions
also run. Provider calls, training, browser and real-user results are not inferred
from these deterministic tests. Exact final-head matrix/static/build/installed
demo/locked-consumer results are reported in the PR.

Mechanism reference only: [python-tuf's same-data verification and parsing](https://github.com/theupdateframework/python-tuf/blob/2c771a035620b29c6e5f90030f501ae92d64cf83/tuf/ngclient/_internal/trusted_metadata_set.py),
[`LICENSE` at that revision](https://github.com/theupdateframework/python-tuf/blob/2c771a035620b29c6e5f90030f501ae92d64cf83/LICENSE)
is Apache-2.0 (the referenced source also carries `MIT OR Apache-2.0`). No code
or dependency was copied. The repository's Apache-2.0 code license does not grant
rights to source datasets with an unspecified data license; that existing boundary
is unchanged.
