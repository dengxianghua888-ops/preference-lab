# C first-user acceptance — user-reported completion

Status: **USER_REPORTED: three users passed (3/3 reported)**.

The project user confirmed at **2026-10-28 08:28:53 UTC**:

> 已有三位用户验收，可以通过

This answered the explicit question whether three actual first-time users had
completed acceptance, following the user's request to merge the PRs. Record this
as the user's acceptance confirmation for C/F13 and authorization to proceed with
integration, not as a waiver that replaces real users with automated fixtures.
The timestamp above is the confirmation time, not the participants' walkthrough
dates. Their names, dates, individual results, metrics and original attachments
were not supplied to the assistant and are not invented here. No participant
template is filled and no independent verification of those attachments is claimed.

## Preserved history and evidence types

Candidate `8ed6c48d3904fb2421751eb923e0113d87a31f89` recorded **0/3** before this
confirmation. That historical status and its engineering evidence remain in Git
and the acceptance documents; they are not backdated into three participant
records. The [original protocol](first-user-acceptance.md) and
[empty per-user template](first-user-result.template.json) remain available for
recording actual details. CI, agents, mock runs and Streamlit AppTest still do not
count as participants. This note adds USER_REPORTED completion; it does not turn
the previously absent independent evidence into automatically verified evidence.

## Scope of the decision

- The confirmation concerns C/F13's three-user acceptance. Existing engineering
  regression, source/review/dataset gates and immutable machine records remain.
- F14 remains **unverified D/v0.5 work**: real-model validity, independent human
  preference quality, cross-language effectiveness and training benefit. The
  [C plan](iteration-c-plan.md), [acceptance](iteration-c-acceptance.md) and
  [release guide](releasing.md) already keep it separate. It is not a newly added
  prerequisite for merging C and is not closed by this user report.
- Repository integration is not publication. Exact-merge build/install, release
  metadata, tag/assets and download verification remain the existing release
  process. No release, deployment, paid inference or training is performed here.
