# Iteration B migration (0.3.0a2)

## Entry points and configuration

`SynthesisKernel.run(PolicyPack, RunConfig)` is the durable Python entry point. `python -m ecoalign_forge run --demo` and the historical `--demo --num-samples 5` spelling use this same kernel. Default mode is now **demo**. Live requires `--mode live`, an explicit USD cap and prices for all configured models. No API calls are made by the acceptance fixtures.

```python
import asyncio
from ecoalign_forge.engine import SynthesisKernel
from ecoalign_forge.policy.builtin import builtin_pack
from ecoalign_forge.schemas.kernel import RunConfig

result = asyncio.run(SynthesisKernel().run(builtin_pack(), RunConfig()))
assert result['exit_code'] == 3  # Four cases abstain without external evidence.
assert result['counts']['dpo_pairs'] == 1
```

Parameters resolve from explicit arguments, JSON configuration, `ECOALIGN_<FIELD>` environment variables, then defaults. Nested model and price mappings are complete mappings, not partial merges. `models` defines generator, moderator, judge and reviewer with model, temperature, max_tokens and optional reasoning_effort. Provider credentials remain environment-only. Historical `CHAOS_CREATOR_MODEL`, `JUDGE_MODEL`, `DEFAULT_*` and `PARSE_*` configure only old adapters; use `ECOALIGN_MODELS` or JSON for the new kernel.

Live configuration must include `execution_mode: live`, positive `max_run_cost`, and `prices` keyed by each requested model ID. Each price declares `input_per_million`, `output_per_million` in USD and `max_input_tokens`. Supply verified provider prices yourself; this release supplies no market-price claims. The input reservation uses a conservative UTF-8 byte bound and refuses prompts exceeding the declared input allowance. Fees are estimates from the frozen requested-model price; actual returned model and provider usage remain separate evidence. Missing usage keeps the reservation unresolved. No supplier bill is inferred.

Defaults: 5 concurrent requests, RPM 60, TPM 100000, 3 total attempts including parsing retries, 120 seconds per request, 30 seconds idle stream timeout, 3600 seconds per run. Group limits coordinate this tool's processes sharing `DATA_DIR` and `quota_group`. Different cap settings require a different explicit group. They do not govern other clients on the provider account. Demo/mock have zero model fees and token reservations.

`min_preference_gap` warns and aliases `min_label_distance`; this is a **semantic change**. Distance counts differing dimension labels and final action, divided by the number of dimensions plus one. Only positive disagreement can form a pair even with threshold zero. More rule citations alone never create a new-kernel pair.

Old `AgentOrchestrator` is a deprecated fixture-only API. Its live path is rejected so it cannot bypass the new budget controls. Old `SupremeJudge.run` remains a deprecated built-in A/B adapter. Historical `JudgeEvaluation`, A artifacts and readers remain available; they do not accept arbitrary PolicyPacks.

## Policy and evidence

PolicyPack schema version 1 declares arbitrary language, dimension labels, actions, rule text, evidence requirements, scores, exceptions and ordered decisions. See [English example](../examples/contact_policy.en.json). Compile freezes canonical JSON and full SHA-256. No code is loaded from packs. Rule/label/score comparisons and `all/any/not` are the only operations. Exceptions suppress rules based on original facts; dimension labels and decisions then use effective facts. Every decision table ends with a default. Unknown facts propagate when they affect a result.

Legacy PolicyInput adapts only the packaged Chinese A/B handbook. Its descriptive fields cannot replace rules. The built-in pack encodes weighted thresholds, matrix, firsthand AI-assistance exception and T3 admission. Generation focus is an intention, never gold truth.

All stages bind the same policy hash. The weak Moderator's full-rule visibility is a fixed configuration; the judge, source reviewer and chosen prompt use the full snapshot. Evidence uses exact Unicode code-point offsets, original text SHA-256 and source ID. No normalization is hidden. Whole-document evidence requires source review. External material is not currently supplied: external rules must remain `unknown`; either `hit` or `miss` makes the gate abstain. Unknowns only permit acceptance when all labels and the decision can be determined independently. Gate acceptance establishes structure, source references and policy consistency, **not semantic truth**.

The corrected `iteration-b-2` demo therefore yields one completed case/pair and four failed cases with `unknown_affects_decision`, returning `partial_failed` / exit 3. Earlier three-pair demo results relied on unverified external misses and are historical, not evidence for the corrected gate.

## Journals, recovery and statuses

New runs: `DATA_DIR/<mode>/runs/<run_id>/run.sqlite3` (WAL, FULL synchronous). JSON summary is derived, not the authority. Requests, raw complete responses, parsed stage results, cases, pair IDs, costs and events are separately committed. A POSIX file lock permits one executor per run; supported release acceptance covers macOS/Linux, not Windows. Use local filesystems supporting SQLite WAL and file locks.

```bash
python -m ecoalign_forge inspect data/demo/runs/RUN_ID
python -m ecoalign_forge resume data/demo/runs/RUN_ID
python -m ecoalign_forge resume data/live/runs/RUN_ID --resolve 'ATTEMPT_ID=retry'
python -m ecoalign_forge resume data/live/runs/RUN_ID --resolve 'ATTEMPT_ID=skip'
python -m ecoalign_forge resume data/live/runs/RUN_ID --max-run-cost 20 --extend-seconds 600
python -m ecoalign_forge export data/demo/runs/RUN_ID
```

Inspect lists unknown attempt IDs. Unknown remote outcomes pause by default. Retry creates a new attempt and retains the old unresolved reservation; skip records an explicit exclusion from further attempts and marks the affected case failed. Saved complete responses are parsed locally first. Only increased budgets and extended deadlines are allowed; code fingerprint, prompt version, models, policy and schema must match. Resume restores the original data and dataset roots from its checkpoint; changing the shared quota root is refused.

Budget checks read a constant-size, transactionally maintained Decimal projection. Attempts remain authoritative: old journals rebuild this projection once from cost/reservation/usage columns, and legacy writer changes invalidate it. Raw responses are not loaded for budget checks. Unknown attempts retain their reservation after either retry or skip; settlement and its projection commit or roll back together.

New run manifests also record the Python implementation/version, SQLite version and key runtime distribution versions (request SDK, validation and tokenization included). Resume compares the effective environment before dispatch and lists changed fields. Restore the recorded environment or start a new run to retain strict reproducibility. If you deliberately accept an environment change, use:

```bash
python -m ecoalign_forge resume data/demo/runs/RUN_ID --allow-environment-change
```

The equivalent Python option is `allow_environment_change=True`. This records previous/current identities and the differences in a `resume_environment_revision` event, preserves the original manifest, and makes the accepted identity effective for later resumes. It does not waive code/template/policy checks, prove compatibility, or upgrade old source checkpoints. A missing environment identity is unknown, so it also requires explicit acceptance when all other identity checks pass. This is a targeted runtime fingerprint, not a complete transitive dependency lock or a guarantee against editable dependency changes.

Counts rebuild from committed state:

```
completed + failed + unattempted + in_progress = requested
accepted_cases + excluded_cases = completed
```

Completed includes normal semantic exclusions. Failed/abstaining review is failed, not accepted. `no_signal` counts accepted cases without a pair. Historical counts default `in_progress=0`. Codes: completed 0, failed 1, arguments 2, partial_failed 3, paused 4, cancelled 130. Pauses distinguish unknown, budget and deadline. Saving failure cannot return success.

Exports are immutable versions at `DATASETS_DIR/<mode>/<run_id>/<dataset_hash>/`. Temporary files and atomic replacement precede a manifest. No historical files are migrated or overwritten. `pairs.jsonl` is the provenance-rich authority view; TRL standard/conversational, ShareGPT, dataset_info, policy snapshot and a data card are projected views. The manifest hashes each file. Data license defaults to unspecified, independently of Apache-2.0 code licensing.

The public ShareGPT and dataset-info exporters stream JSON into an exclusive file inside a private `.atomic-*` sibling directory, then publish one complete file with replacement. Existing ordinary single-link targets retain owner/group, ordinary permission bits and supported Linux POSIX access ACLs; new files retain the OS creation policy (umask and supported directory default ACLs). Unsafe target types, special mode bits and metadata that cannot be restored are rejected before replacement. This does not preserve inode/hardlink identity, timestamps, security labels, unrelated xattrs or non-Linux ACLs, and does not isolate same-UID or privileged processes. The two exports are separate publications, not a transaction across both files. A directory-fsync error after replacement reports failure even though the complete new file is already visible.

Handled failures clean only the current writer's owned candidate and stage. A process kill can leave a private `.atomic-*` directory; it is ignored by Git, is never reused, and is not swept or deleted by a later run. Treat any retained contents as private run data when reviewing storage; do not add them to version control. No automatic garbage collector removes unknown directories or user files.

## Metrics and consumers

Severity is nullable and exists only when the pack supplies a mapping. No severity trend or category distribution measures model improvement. IAA uses coincidence normalization and nominal/ordinal/interval distances. Arbitrary ordinal labels need an explicit order; non-estimable values return null with a reason. Deliberately biased candidates only provide disagreement diagnostics, never independent-annotator reliability or automatic data selection.

Consumer environments are separate and locked under `scripts/consumers/`: TRL 1.14.0 with Transformers 5.6.0 and LLaMA-Factory 0.9.5 with its own compatible dependencies. Torch and torchaudio both pin 2.8.0 in the LLaMA environment. `verify.py` uses the actual consumer preprocessing and collator, an entirely local byte tokenizer, a hard network guard, and no training or pretrained model. These checks validate data consumption, not optimization outcomes.

## Changed historical assertions

- A CLI tests now patch SynthesisKernel and its result dictionary; the same exit-code behavior remains tested.
- Empty raw agreement/kappa/alpha are null instead of zero. Single-category alpha is unestimable instead of perfect.
- `low_confidence` remains a null compatibility field; it no longer grades intentionally biased candidates.
- New-kernel pairing uses label distance; A fixture-only adapter assertions remain unchanged.
- Conversational prompt is a message list, not a string mixed with chosen/rejected message lists.
