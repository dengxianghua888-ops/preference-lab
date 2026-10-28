# Iteration C engineering review — 2026-10-26

This review continues PR #16 on `codex/iteration-c`. Its starting head was
`e2a9a446fd811b3583e49e772d08d8cdb885d849`. The maintenance baseline
`154ed806d9b7b28205462ee3271b4057f5a4c79c` was merged without conflicts,
preserving the newer read-only inspection, budget accounting, environment identity,
retry timing, atomic export and dashboard fixes. No default-branch push, merge,
release or deployment is part of this review.

## Acceptance criteria

- Source, policy and machine-stage bindings survive review; failed, stale and
  concurrent submissions cannot overwrite a decision or append a false success.
- Reviewed datasets freeze exact revisions, preserve prior versions, verify audit
  metadata and consumer projections, and keep machine journals unchanged.
- Demo, review, dataset, verification, reports and read-only inspection work together
  on the integrated source. Unknown remote requests must not silently retry.
- CLI help exposes available workflows while retaining the documented demo shorthand.
- Applicable regression, static, build and consumer checks have explicit outcomes.
  Automated fixtures do not satisfy first-user acceptance or model-quality claims.

## Findings and changes

- `--help` was rewritten to `run --help`, hiding review, dataset, verify, report,
  inspect and recovery commands. Preserve top-level long help; short help, explicit
  run help and the `--demo` shorthand remain supported. Two subprocess regressions
  exercise the actual CLI.
- `guidelines.md` incorrectly said custom rule packs were unavailable. It now
  links the implemented declarative PolicyPack flow and states that saved policy
  snapshots cannot be switched in place.
- PR #16 is remotely marked ready while its body says it remains draft. The state
  was left unchanged under the owner's coordination instruction. This discrepancy
  does not remove the release gate: **three real first-user walkthroughs remain 0/3**.
- The existing audit-metadata review thread remains unresolved. A fresh engineering
  reproduction rejects changed audit metadata; this is independent evidence, not
  a claim that the reviewer has approved or closed the thread.

## Validation boundaries

Python 3.12.14 was available with the kernel's offline dependencies. A direct
engineering walkthrough exercised real demo journals and the workbench APIs:
one completed case/pair and four abstentions (expected demo exit 3), unreviewed
exclusion, gate refusal without revision, operation idempotence, stale/owner-lock
refusal, reviewed export from one pair to zero, preservation of every previous
dataset file and the machine SQLite bytes, report count/hash and audit-tamper
rejection. No external model request or human participant was involved.

The two new CLI regressions run with `PYTHONPATH=src python -m unittest
tests.test_cli_help -v`. All Python sources parse with `ast.parse`; `git diff
--check` passes. The documented offline comparison example works after freezing
the input set.

Local full pytest, Ruff, mypy, wheel/sdist builds, Streamlit browser checks and
locked consumers were **not run**: their tools/dependencies were absent, and this
review did not install software. Existing CI on the starting head passed; it is
historical evidence. See the PR for the exact new head and its own CI results.
Clean-install, real-user acceptance, paid-model quality and training benefit are
not established by these engineering checks.

## Open-source practices and licenses

- [TRL's DPO format contract](https://github.com/huggingface/trl/blob/main/docs/source/dpo_trainer.md)
  supports explicit prompt/chosen/rejected and conversational projections. Keep
  the existing isolated real-consumer checks rather than declaring compatibility
  from JSON field names alone. [TRL license](https://github.com/huggingface/trl/blob/main/LICENSE): Apache-2.0.
- [Datasets fingerprints](https://github.com/huggingface/datasets/blob/main/src/datasets/fingerprint.py)
  bind prior state, transforms and arguments. This supports retaining version-bound
  provenance and explicit configuration in the existing immutable dataset recipe.
  A fingerprint does not authenticate a reviewer or establish label truth.
  [Datasets license](https://github.com/huggingface/datasets/blob/main/LICENSE): Apache-2.0.
- [LLaMA-Factory data guidance](https://github.com/hiyouga/LlamaFactory/blob/main/data/README.md)
  provides the separate ShareGPT ranking consumer contract;
  [license](https://github.com/hiyouga/LlamaFactory/blob/main/LICENSE): Apache-2.0.

No upstream source was copied or new dependency introduced. This repository's
Apache-2.0 code license does not grant rights to arbitrary exported datasets;
unspecified data licenses remain unspecified.
