# Read-only recovery and budget explanations

Run `python -m ecoalign_forge inspect PATH_TO_RUN` to print the existing run
snapshot plus a `recovery` object (report schema version 1). This command only
reads the journal. It does not invoke a model, migrate legacy accounting
projections, settle costs, resolve unknown attempts, or resume execution.
The budget and recovery views use the same SQLite read transaction. Existing
schema-version rejection still applies; old checkpoints are not upgraded by
inspection. Database and WAL bytes are preserved (SQLite may manage its normal
shared-memory reader bookkeeping).

## What the fields mean

- `persisted_responses`: attempts with stored raw response bytes, including
  responses later rejected by parsing. Their presence does not establish valid
  output or settled usage.
- `response_replay_candidates`: `response_saved` attempts with raw response
  bytes. Resume can try local parsing, but validation may fail and later work
  can require another call. This is not a guarantee that the entire run can
  finish offline. Identity checks and execution limits still apply.
- `request_parsed_persisted`: whether the parent request has a parsed checkpoint.
  This is request-level evidence and may originate from a different attempt.
- `in_flight_intents`: locally recorded intent without a committed completion.
  The executor may still be active, or may have died before or after sending.
  Inspect does not decide which and does not mark it `unknown`.
- `unknown_remote_outcomes`: `in_flight` and `unknown` attempts, including those
  explicitly resolved by retry or skip. Local decisions cannot establish what
  happened remotely.
- `unresolved_unknown_decisions`: `unknown` attempts without an explicit choice.
  Resume requires those choices; this is separate from unresolved billing.
- `unresolved_reservations` and `unresolved_reserved_usd`: attempts whose cost is
  still null, and the exact decimal sum of their retained reservations. A
  reservation can be zero, or belong to a saved/failed response, not just an
  unknown request. Retry and skip both retain it.
- `possibly_billable_attempts`: in recorded live mode, attempts with unresolved
  costs or positive cost estimates. These are not confirmed charges. Demo/mock
  are marked offline; absent or unknown execution mode leaves billing unknown.
- `actual_billed_usd`: always null; no provider billing reconciliation is done.
  `estimated_usd` remains the existing local estimate, not an invoice. Even a
  zero estimate is not invoice verification.

Each attempt lists its stable attempt/request IDs, stage, state, request state,
resolution, response/parsed/settled-usage presence, estimate, reservation and
explicit recovery/billing boundary. Bodies, prompts, parsed content and provider
error strings are omitted. Counts overlap and must not be added together.
Output order is deterministic by request ID and attempt number.

## Unknown means unknown

A request may have completed remotely and incurred a charge before the response
could be saved. Retrying can duplicate external work or charges; skipping does
not cancel a remote request or prove zero cost. The report explicitly sets
`external_exactly_once_guaranteed` to false. It provides evidence for a decision,
not a new recovery policy or an automatic decision.

This addition is an offline usability improvement. It does not establish
preference quality, training benefit, or completion of the separate PR16 human
review acceptance checks.
