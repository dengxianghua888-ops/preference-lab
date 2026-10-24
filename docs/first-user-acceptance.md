# C first-user acceptance — three actual participants required

Status: **0/3 verified**. Automated fixtures, agents and browser automation do not
count as first-time human users. Use participant IDs rather than publishing names.
The project owner may keep original feedback privately; public evidence should be
redacted. Participants need Python 3.11/3.12 and macOS/Linux, no model key or budget.

## Walkthrough (about 15–25 minutes)

1. Check out the exact candidate commit supplied with the acceptance record. Create
   a new venv and run `python -m pip install -e '.[dashboard]'` from the repository root.
   Use a fresh checkout (not an older packaged candidate); replace `CANDIDATE_SHA`
   below with the supplied full commit SHA, not the literal placeholder:

   ```bash
   git clone https://github.com/dengxianghua888-ops/ecoalign-forge.git
   cd ecoalign-forge
   git checkout --detach CANDIDATE_SHA
   python3.12 -m venv .venv  # python3.11 is also supported
   source .venv/bin/activate
   python -m pip install -e '.[dashboard]'
   git rev-parse HEAD
   python --version
   python -m pip show ecoalign-forge
   ```

   Installation needs network access to the package index, but no model key.
   Run all following commands in this checkout with this venv active. Do not copy
   an existing `.env`; use default `./data` storage for this walkthrough. Record
   installation output and exit status; a dependency failure is not demo exit 3.
2. Run `python -m ecoalign_forge run --demo --num-samples 5`. Confirm demo, one
   completed case/pair and four abstentions (`partial_failed`, exit 3). Immediately
   run `echo $?` to record the exit status; do not use `&&` to chain the next step
   or treat this expected 3 as installation failure. Record the JSON `run_id`,
   `run_dir`, status and counts, plus any help needed. Default run storage is
   `data/demo/runs/RUN_ID`; the CLI's machine export is not the later curated export.
   Demo is a recorded fixture, not a live model call; do not switch to live/resume
   as a workaround for the expected abstentions.
3. Run `python -m streamlit run dashboard/app.py --server.port 8501`. Select demo,
   your run and Review. Open a source and identify its rule/evidence span.
   Open the printed local URL in a browser; keep the server terminal running and
   use a second activated terminal for later CLI commands. In the sidebar choose
   `数据来源 = demo`, `存储格式 = 新内核运行`, the recorded `运行记录`, then
   `工作区 = 样本复核`. Expand `原文与规则来源` and `Judge 原候选`; record Case ID,
   source SHA-256, policy SHA-256, rule ID and evidence `[start, end)` (zero-based
   Unicode character positions, end excluded), quote and reason.
4. Accept case 4 (the gate-accepted fixture) with your participant ID and a reason.
   Correct its evidence/reason after checking rules, preserving external facts as
   unknown; missing material cannot be resolved by claiming miss. Try accepting a
   gate-abstained case and confirm refusal, then record a human abstention on it. Confirm the revision history preserves the original.
   Here “case 4” means display ordinal 4 (internal ordinal 3), beginning
   `众所周知，坚持努力就会成功。`; record its actual Case ID rather than entering
   `4` as an ID. Use `接受当前判决`, fill `复核者名称` with the participant ID and
   `复核理由（必填）`, then `校验并保存复核`. Saved cases leave the default `待复核`
   queue: switch `复核队列` to `全部` to reopen the same case and choose `改判`.
   Check each rule and adjust supported evidence/reason, leaving external rules
   unknown. Save and inspect `复核记录与改判差异`; record revision/review_id before
   and after. For another gate-abstained case, capture the refusal from attempting
   acceptance, then save `弃权` with a reason. Refusal must not append a revision.
5. In Dataset, export the human-reviewed selection. Record counts and exclusions.
   Trace one included pair to source, evidence, review and policy. A zero-pair
   result is valid if your reviewed cases have no preference disagreement.
   Choose `工作区 = 数据集`, include only your run and leave
   `包含未复核机器结果（仅候选预览）` unchecked. Keep filters clear for this fixture
   (filters select cases, not just displayed rows). Click `生成不可变数据集版本`.
   Record the full version from the code block/manifest, not the truncated dropdown
   label, selection, pair/train/eval counts and `排除原因`. Expand `逐对溯源` and
   connect the pair's lineage to `sources.jsonl` review history and `policy.json`.
   Default versions are in `data/datasets/demo/curated/DATASET_VERSION`.
   Download the first version ZIP before changing the review. If zero pairs are
   legitimate, record the exclusion and source/review evidence instead of inventing
   an included pair; missing acceptance or an unintended filter is not that case.
6. Change a previously accepted review, export again and confirm the first version
   stays unchanged. Download the ZIP, extract it and run
   `python -m ecoalign_forge verify PATH_TO_EXTRACTED_DATASET`.
   Reopen case 4 via `复核队列 = 全部`, choose `弃权`, provide a reason and save.
   Return to Dataset, keep the same selection/settings and generate again: this
   fixture should go from one pair to zero, with a different full dataset version.
   Select each saved version explicitly; compare the first version's manifest and
   every exported file to the first downloaded ZIP, not only its pair count.
   Extract each ZIP into a separate directory. The verify argument is the directory
   directly containing `manifest.json` (not the ZIP or its parent). Success prints
   the verified manifest and exits 0; record stdout/stderr and exit code for both
   versions. A verification error is a failed step, not the expected demo exit 3.
7. Generate a data report. Compare JSONL line count/hash with `report.json` and
   the HTML report. Explain in your own words why this is not a model-quality score.
   Use Dataset's `生成本版数据报告` for the explicitly selected version, not Run's
   `生成此运行报告` (which reports machine pairs). For reproducible JSON/HTML access:

   ```bash
   python -m ecoalign_forge report "RUN_DIR" --output-root ./first-user-reports --dataset "PATH_TO_EXTRACTED_DATASET"
   ```

   Replace both quoted placeholders with the paths recorded above. The printed
   `report` path points to `report.html`; its directory also contains `report.json`,
   `pairs.jsonl` and `SHA256SUMS`. Compare `reported_pairs`, `pairs_sha256`,
   `dataset.dataset_version` and `pair_scope = selected_dataset_all_runs` with the
   exact extracted dataset and HTML. On macOS/Linux, `wc -l < PATH/pairs.jsonl`
   counts lines; use `shasum -a 256 PATH/pairs.jsonl` on macOS or
   `sha256sum PATH/pairs.jsonl` on Linux. Empty JSONL has zero lines and a valid
   SHA-256. Record report paths/hashes and the participant's own explanation.

## Result record

Copy [first-user-result.template.json](first-user-result.template.json) for each
participant. Record actual commit/version/OS/Python, start/end time, run IDs,
dataset versions, pass/fail per step, unassisted/assisted completion, help requested,
errors and improvement requests. Attach redacted screenshots/logs and their SHA-256.
A reviewer verifies the evidence; a checked checkbox without evidence is insufficient.

All three participants must complete the critical source → review → next-version
export → verify path. Reproduce and repair critical failures, then have affected
participants rerun those steps. Do not pre-fill results or backdate acceptance.
F13 stays open until these records exist. Paid models and training are outside C.

### Filling the existing template

Keep the template's top-level fields. In `steps`, record seven actual entries with
`step`, `status` (`pass`/`fail`/`blocked`), `completion` (`unassisted`/`assisted`),
`observed`, and `evidence` (relative redacted attachment paths). Use
`assistance_received` for the step, help requested and help actually given. In
`issues`, record step, command/UI action, expected/observed result, exit code or
exact redacted error, run/Case/revision/version identifiers and improvement request.
Do not record a requested action as successfully saved without checking history.
Map each attachment path to its actual SHA-256 in `evidence_sha256`; hash the
redacted file being attached. Keep `verification_status = pending` and
`verified_by = null` until a reviewer checks the evidence. Never put credentials or
unredacted personal material in public attachments.
