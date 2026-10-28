# Project status

## Current source and releases

The default branch includes the [external-evidence correction in PR #15](https://github.com/dengxianghua888-ops/ecoalign-forge/pull/15).
External rules cannot claim either `hit` or `miss` when their required material
is absent. They must remain `unknown`; a candidate is accepted only if the policy
can resolve its labels and action independently.

| Version | State | Meaning |
| --- | --- | --- |
| `0.3.0a2` | [Published maintenance Alpha](https://github.com/dengxianghua888-ops/ecoalign-forge/releases/tag/v0.3.0a2) | Corrected evidence gate and recorded demo |
| `0.3.0a1` | Withdrawn pre-release; original tag/assets retained | Its 5-completed / 3-pair demo relied on unverified external misses |
| `0.2.1a1` | Historical A Alpha | Legacy contracts; not a substitute for the B kernel |
| `0.4.0a1` | [C preview, PR #16](https://github.com/dengxianghua888-ops/ecoalign-forge/pull/16) | Review workbench; three-user completion USER_REPORTED, not published |

The corrected built-in demo produces **one completed case/pair and four machine
abstentions**, `partial_failed` / exit 3. Previous journals and exported bytes are
not rewritten. Data that relied on an external `miss` needs regeneration/review;
old engineering acceptance cannot establish that those decisions were valid.

## Scope and remaining work

- F01–F11 retain their original acceptance history. F09's missing-external-material
  regression is corrected in PR #15; release verification is version-bound.
- F12's C workbench implementation lives on the preview branch and follows the
  corrected gate; it is separate from the published B kernel.
- F13's historical status was 0/3. At 2026-10-28 08:28:53 UTC the user confirmed
  three users had passed; this is [USER_REPORTED completion](first-user-acceptance-user-report-20261028.md),
  not independent verification of participant attachments. Agent/browser automation
  still cannot satisfy the human condition.
- F14 remains open: independent human preference quality, real-model performance,
  cross-language effectiveness and training benefit have not been demonstrated.

No paid inference or training is required for the engineering demo or tests. The
project does not publish to PyPI. Current choices and failure history remain in
the [original finding ledger](review-findings.csv); new work does not reset those IDs.

The `v0.3.0a2` tag is bound to `26760ca62d9560f9b072ede635831a9ee36ec6b5`. Its wheel, sdist and sanitized evidence were downloaded again after publication; all `SHA256SUMS` entries and fixture manifest hashes matched. See [maintenance acceptance](iteration-b-acceptance.md).
