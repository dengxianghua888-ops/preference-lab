# Iteration C contracts and migration

`0.4.0a1` is the C candidate. The published B checkpoint/data formats remain readable.
No historical journal, machine stage, pair or dataset is automatically migrated.
The external-evidence correction rejects both hit and miss without materials. Old
exports that relied on an external miss can fail current verification; preserve
them as historical data and regenerate/review with the corrected gate. The built-in
demo now produces one pair and four machine abstentions (exit 3), not three pairs.
Resume still requires the original code fingerprint; finish a B run with B before
reviewing it in C. Run inspection and review use a read-only connection to B data.

## Human review

The source checkout dashboard has three work areas: Run, Review and Dataset.
Read the source and applicable rules, then accept, correct, abstain or exclude.
The reviewer name and reason are required. Corrections use declared rule judgments
and exact Unicode character spans; labels/actions follow the policy decision table.
Corrections must pass the evidence and rule gate. Missing external materials still
cannot be confirmed. A human action cannot bypass that constraint.

Each run gains `reviews.sqlite3` **only on explicit review submission**. It stores
append-only revisions, timestamp, operation ID, parent/review hash, original case
binding, reviewer, reason, optional error type and source/template families. The
name is self-declared in a trusted local workspace, not authentication or an
independent gold annotation. Do not expose this write-capable UI to an untrusted
network. Concurrent execution/review uses the run lock; stale revision submissions
fail instead of overwriting another person's work. Repeating an identical operation
ID is idempotent. A new decision supersedes the previous decision without deleting it.

Programmatic decisions use `ReviewDecision` and `submit_review`. CLI:

```bash
python -m ecoalign_forge review data/demo/runs/RUN_ID --case-id CASE_ID
python -m ecoalign_forge review data/demo/runs/RUN_ID --decision decision.json
```

`decision.json` example (replace the IDs with values from inspection):

```json
{"operation_id":"review-session-001","case_id":"CASE_ID","expected_revision":0,"action":"accept","reviewer":"local-reviewer","reason":"Checked original text and rule evidence"}
```

`correct` additionally requires a complete `corrected` CandidateEvaluation;
`accept` uses the displayed effective final. After abstain/exclude, supply a
correction to reinstate a case. Existing machine failure/completion counts never
change when a human acts. Human review counts belong to the curated version.

## Dataset versions and partitions

```bash
python -m ecoalign_forge dataset data/demo/runs/RUN_ID --output-root datasets
python -m ecoalign_forge verify datasets/demo/curated/DATASET_VERSION
python -m ecoalign_forge report data/demo/runs/RUN_ID --dataset datasets/demo/curated/DATASET_VERSION --output-root reports
```

The default dataset includes only human accepts/corrections that produce a label or
action disagreement against an original candidate. `--include-unreviewed` produces
an explicitly labelled **candidate preview**, never a human-reviewed dataset.
Abstain/exclude remains excluded even in preview. Corrections can create, change or
remove a pair. No disagreement means no pair; acceptance is not a promise of yield.

Select multiple runs as positional arguments, but modes and complete policy hashes
must match. `--select-file selection.json` takes a list of `RUN_ID:CASE_ID` keys.
The UI supports dimensions, labels, hit rules, manually marked false-positive /
false-negative issues, declared families, duplicates and source mode. These marks
are human diagnostics, not measured error rates.

Exact original Unicode text SHA-256 performs deduplication. Conflicting accepted
labels/rule judgments/actions for identical text are excluded, not silently ranked.
Near-duplicate semantic detection is **not implemented**. Source/template family
IDs supplied in review are additive. Duplicate texts, declared families and
same-generation-batch cases form connected groups, kept wholly in train or eval.
Unselected cases still participate in family grouping to prevent broken bridges.
A seeded group hash assigns splits; small datasets may have an empty split. New
versions may regroup when sources change. Eval is a development partition, **not**
D's independent frozen holdout. Do not compare changing versions as a fixed test set.

`DATASETS_DIR/<mode>/curated/<version>/` freezes the selection, source text, all
candidates, exact review revisions, policy, code identity, grouping and split recipe.
`pairs.jsonl` is linked to `sources.jsonl`; both root consumer views and separate
`train/` / `eval/` views are present. `manifest.json` binds counts, exclusion reasons,
license and SHA-256. TRL standard/conversational and ShareGPT preserve chosen order.
Root consumer files contain **all** pairs: use partition subdirectories for training.
Data license defaults to unspecified, independently of the Apache-2.0 code license.

Curated manifest schema 4 (`curated-dataset-2`) binds every manifest field other
than the recipe and dataset version into the recipe's `audit_hash`, including
selection, exclusion reasons, source-run status/counts/budget and file checksums.
Identical snapshots and configuration reproduce the same version; a changed run
audit summary creates a new version even when the selected pairs are unchanged.
Run ordering is canonical by run ID, independently of input order or local paths.

Schema 3 curated exports did not bind all audit metadata. Verification now rejects
them explicitly rather than certifying unprotected fields. Preserve old directories
as historical artifacts and rerun the dataset command against the original source
runs and desired selection/configuration to create a schema 4 version. This does
not modify old exports, journals or reviews; it captures the current source/review
snapshot, which may differ from the historical one. Without the original source
runs, unbound historical audit values cannot be retroactively verified. These
hashes detect changes relative to a recorded version; they are not signatures or
independent proof of source truth.

A version publishes via one directory rename after verification. A crash before
publication exposes no dataset; after publication a retry verifies the existing
version. Incomplete directories under `.curated-staging/` are not published or
shown in the UI. Do not reuse/rename those partial directories as datasets.

Legacy `export RUN_DIR` still exports the original machine view for compatibility;
use `dataset` for review-aware exports. It is intentional that those counts differ.

## Reports, persona and run controls

```bash
python -m ecoalign_forge run --demo --num-samples 5 --persona strict_paranoid
python -m ecoalign_forge pause data/demo/runs/RUN_ID
python -m ecoalign_forge resume data/demo/runs/RUN_ID
python -m ecoalign_forge report data/demo/runs/RUN_ID --output-root reports
```

One configured persona is used per run: `naive` (default), `strict_paranoid`,
`lax_overlooker`, or `keyword_matcher`. There is no automatic four-persona ensemble.
Demo fixtures stay prerecorded, whatever persona is recorded in their prompt.

Pause stops scheduling at the next batch boundary; current requests finish and
persist. It does not kill potentially billed requests. Resume UI/CLI respects B's
unknown-request pause, explicit retry/skip and upward-only budget/deadline changes.

Reports include persisted machine counts, budget, source/code/policy identifiers,
review counts, and an accompanying exact `pairs.jsonl`, `report.json` and
`SHA256SUMS`. With `--dataset`, dataset counts (potentially across multiple runs)
are labelled separately from the selected run's machine counts. HTML escapes data.

## Acceptance boundary

Deterministic fixtures and browser automation are engineering evidence. They are
not the three first-time human users required by F13. See [the user walkthrough](first-user-acceptance.md).
Real-model quality, human preference accuracy and training outcomes remain F14/D.
