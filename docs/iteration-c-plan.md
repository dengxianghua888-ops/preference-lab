# Iteration C — reviewable data workbench

Baseline: `a329682322ae94a3130a75b1000777bf211ce5cb` / `v0.3.0a1`.
Scope follows the original F12–F13 ledger. D/F14 remains separate.

1. Append-only human review: accept, correct, abstain, exclude; source, rule,
   candidate and evidence trace; optimistic revision checks and gated corrections.
2. Immutable curated datasets: explicit run/case selection, human-reviewed default,
   exact text deduplication, conflict exclusion, family-grouped train/eval splits,
   reconstruction manifest and independently verifiable exports.
3. Extend the existing Streamlit surface with Run / Review / Dataset navigation.
   Preserve source-mode selection and historical read-only views. No automatic
   refresh while editing. Keep original text and decision beside review controls.
4. Real persisted-run HTML reports, explicit persona CLI option, working developer
   checks, contributor/release/migration guides, and offline first-user walkthrough.
5. Python 3.11/3.12 regressions, concurrent/stale review and export fault tests,
   locked consumer loading, clean distributions, CI and actual browser interaction.

Review is a trusted local workflow: the reviewer name is self-declared, not an
authentication claim. Human review is not automatically a gold label. Machine
stages and B exports remain immutable; C writes a separate review database. A
dataset freezes review revisions, sources, policy and split configuration; later
reviews create another version. No model calls or training are required for C.

Deduplication means exact Unicode text hash; near-duplicate semantic detection is
not claimed. All members of a declared source/template family and generation
batch, joined through duplicates, stay in one partition. Eval is a development
partition, not D's independent holdout. Candidate previews require explicit opt-in;
default curated export requires an accepted human decision.

F13 also requires three actual first-time users to complete the walkthrough.
Automated/browser checks are separate evidence and cannot close that condition.
Target package version is `0.4.0a1`; release readiness is tracked in the original
F ledger, with unmet human acceptance stated explicitly.
