# Offline evaluation comparison (preview)

Compare saved behavior on explicitly identified cases without model calls, paid
services, new dependencies, or changes to any original run. This is a diagnostic,
not an accuracy benchmark or a replacement for human acceptance. PR #16 and its
first-user 0/3 acceptance requirements are unchanged.

## Try the complete example

From an installed source checkout (see CONTRIBUTING.md):

```bash
python -m ecoalign_forge eval-freeze examples/evaluation/input.json > /tmp/eval-set.json
python -m ecoalign_forge compare /tmp/eval-set.json \
  examples/evaluation/baseline.json examples/evaluation/candidate.json --format text
python -m ecoalign_forge compare /tmp/eval-set.json \
  examples/evaluation/baseline.json examples/evaluation/candidate.json > /tmp/comparison.json
```

The checked-in results are explicitly synthetic, not generated model results.
The report shows three evaluation cases, two content-matched cases, one missing
candidate, and one abstention. An action change and an abstention both count as
behavior changes; neither is automatically a regression or improvement. The full
JSON retains both versions of labels, evidence, and reasons for inspection.

## Input and identity contract

`eval-freeze` takes a JSON object with:

- `cases`: nonempty list of `{ "case_id": "stable-id", "content": "exact text" }`.
  IDs must be unique, including identical duplicate rows. Choose stable external
  IDs before evaluation; do not use line numbers or independently generated run IDs.
- `scorer_spec`: nonempty JSON object describing the comparison/scoring protocol
  and version. This is a declarative identity, never executable code.
- Optional per-case `reference_labels`, plus `reference_label_revision` and
  `source_kind` (`machine_candidate` or `human_reviewed`) when labels are supplied.
  Unlabeled sets use `source_kind: "unlabeled"` by default.

The frozen file contains `evaluation_set` and recomputed `identity` with three
SHA-256 digests: case IDs plus source-content hashes; scorer specification; and
reference labels plus revision/source kind. Case order does not affect identity.
Changing any content, scoring protocol, label, revision, or label source changes
its relevant digest. Loading a frozen file revalidates these digests.

A result snapshot has `schema_version: 1`, `identity`, `provenance`, optional
`budget`, and `records`. Each record contains:

- `case_id`, `source_hash` (SHA-256 of exact UTF-8 source text)
- `status`: `accepted`, `excluded`, `abstain`, `failed`, `unattempted`, or `in_progress`
- Optional `action`, `labels`, `evidence`, and `reason`

A missing record is reported as `missing`, never converted to a failure or a
label. Missing source hashes are unknown, not matching. Same ID with different
content is not paired. Extra IDs outside the frozen set are reported explicitly.
All three identity hashes must match the frozen set to report
`same_evaluation_set`; missing/mismatched identities produce `overlap_only`.
Missing results are allowed in a bound evaluation and reported in coverage.

Result producers must attach the identity of the frozen set actually used and
record their run/policy/code/environment identity in `provenance`. These are
producer declarations, not cryptographic proof of execution or human review.
This preview provides the comparison contract, not a new fixed-case inference
runner. Do not paste hashes onto arbitrary random runs to claim a shared
experiment. Human reference labels do not enable an accuracy metric here.

## Existing runs: read-only compatibility

Stop the run's writer before extracting a snapshot:

```bash
python -m ecoalign_forge eval-snapshot /path/to/stopped-run > /tmp/baseline.json
python -m ecoalign_forge eval-snapshot /path/to/other-stopped-run > /tmp/candidate.json
python -m ecoalign_forge compare /tmp/eval-set.json \
  /tmp/baseline.json /tmp/candidate.json --format text
```

The adapter copies the checkpoint and any WAL to a temporary directory before
opening SQLite, so it never creates WAL/SHM files or updates the source run.
It rejects a detected change during copying; a stopped writer is required rather
than promising an atomic snapshot of an actively changing multi-file database.
The original manifest, run-local IDs, budget estimates, and unresolved reservations
are preserved. Legacy runs have no frozen evaluation identity: the adapter emits
`identity: null`, never invents historical scorer/reference identity, and only
reports overlap. Two independently generated runs ordinarily have different
run-local IDs and therefore no match. There is no automatic matching by text or
row position, and no implicit remapping adapter for PR #16's curated dataset.

## Reading the report

- `coverage.evaluation_cases`: full frozen-set denominator.
- `content_matched_cases`: records present on both sides with matching content.
- `changed_cases` / `change_rate_denominator`: changes among those content-matched
  cases only; no matches gives `change_rate: null`, not zero.
- `status_counts`: separate counts for all statuses and missing results.
- `cases`: both records, changed fields, and explanations of unavailable matches.
- `identity_issues`: reasons that only overlap diagnostics can be reported.
- `budgets`: unchanged run-wide reported estimates and unresolved reservations;
  they are not per-case costs or actual invoices. No cost reduction/improvement
  percentage is computed, so zero baselines and unknown bills are not disguised.

`--format text` is a concise readable summary; JSON is the default audit format.
All outputs go to stdout: redirect outside your original run and input files.
A successful diagnostic command exits 0 even when it reports overlap or missing
results. Invalid schemas/duplicate IDs/tampered sets cause a nonzero argument
error. Consumers must inspect `comparison`, `identity_issues`, and `coverage`;
exit 0 is not a quality gate or evidence that a candidate improved.

Design references: Promptfoo's explainable assertion results and MLflow's
content/scorer identities. No upstream code was copied and neither dependency
nor any hosted evaluation platform is used.
