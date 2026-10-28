# Preference Lab engineering review — 2026-10-28

This review continues existing C PR #16 from
`b79434c79a9e4d1b74d996e27195d6f9aff5944d`. Maintenance main is
`dc5f17c25ffd34232e9360241a59968aae686c4e`, including merged budget PR #28.
The exact-budget repair is already present in both; it was not repeated.

## Acceptance and finding

The current review checks durable request/budget identities, source-bound review,
immutable datasets, exact report bytes, CLI discoverability, packaging/consumer
contracts, and truthful acceptance boundaries. Current remote PR/review/comment
and CI results are checked separately from historical evidence.

The dataset report first verified an export, then read `pairs.jsonl` again and
checked only its line count. A controlled change after verification kept valid
JSON and the same pair count but added a space. Reporting still succeeded with
the old dataset manifest/version and a different reported pair checksum. This was
reproduced with a real isolated demo journal and dataset, without provider calls.

The report now reads the JSONL bytes once, decodes UTF-8 without universal-newline
normalization, and compares their checksum with the verified manifest before
writing any report file. Observed drift raises an explicit error. Unchanged
exports retain their exact bytes, count, dataset identity and report idempotence.
This validates the captured report snapshot; it is not filesystem isolation from
an arbitrary same-user process or a guarantee that source files never change later.

## Verification and limits

Two new real-journal tests cover the same-count post-verification drift and the
unchanged/idempotent path. The first failed before the patch and passes after it.
Five existing exact-budget/UNKNOWN regressions and two CLI-help regressions also
pass locally. A fresh SQLite C walkthrough separately checks the demo's expected
exit 3, one pair/four abstentions, review idempotence/stale refusal, exports 1 to 0,
preserved prior dataset and machine bytes, report binding and inactive pause refusal.

Local full pytest, Ruff, mypy, wheel/sdist tooling, interactive Streamlit and locked
consumer dependencies are absent and were not installed. Full CI at the new head
must independently establish its configured tests/static/build/actual-consumer
results; previous green CI does not certify this change.

F13 remains **0/3 actual first-time users**, and F14 real-model quality, independent
human preference validity and training benefit remain unverified. Automated reviews
are not human acceptance. Existing PR #16 is non-Draft and was left unchanged under
the owner's coordination instruction; neither that state nor green CI permits merge
or publication. No provider calls, training, merge, release or deployment occurred.

## Public implementation research and licenses

- [Datasets fingerprint implementation](https://github.com/huggingface/datasets/blob/ef61228a5756dcebfff8ee245d89c72822fb6ae6/src/datasets/fingerprint.py)
  binds dataset state and transform arguments; file modification timestamps also
  participate. Its cache identity is not an integrity check of a later report read,
  reviewer authentication or label truth. Preserve this project's SHA-256 audit
  identity and validate the actual captured bytes.
  [Apache-2.0 license](https://github.com/huggingface/datasets/blob/ef61228a5756dcebfff8ee245d89c72822fb6ae6/LICENSE).
- [TRL DPO trainer/collator](https://github.com/huggingface/trl/blob/c886cf23d74acd8dfed95b63943a8dba0f25d85f/trl/trainer/dpo_trainer.py)
  constructs prompt-plus-chosen/rejected sequences with distinct completion masks.
  This supports retaining the existing isolated real-consumer checks rather than
  treating field names as sufficient compatibility evidence. The researched main
  snapshot is not the locked CI consumer version or evidence of training benefit.
  [Apache-2.0 license](https://github.com/huggingface/trl/blob/c886cf23d74acd8dfed95b63943a8dba0f25d85f/LICENSE).

No external code, prompts, dependencies or license terms were introduced. The code's
Apache-2.0 license does not license arbitrary exported source data; an unspecified
data license remains unspecified.
