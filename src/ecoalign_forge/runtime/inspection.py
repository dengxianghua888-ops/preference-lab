"""Offline explanations of journal evidence, never recovery or provider reconciliation."""

from __future__ import annotations

from ecoalign_forge.runtime.accounting import decimal_add


def recovery_report(journal):
    """Describe persisted evidence without loading prompts, response bodies or errors.

    The caller holds a read transaction when combining this with other snapshots.
    A stored response is evidence of bytes, not proof that parsing will succeed.
    """
    manifest = journal.get("manifest", {})
    mode = manifest.get("execution_mode")
    live = mode == "live"
    offline = mode in {"mock", "demo"}
    attempts = []
    counts = dict(
        total_attempts=0,
        persisted_responses=0,
        response_replay_candidates=0,
        unresolved_reservations=0,
        unknown_remote_outcomes=0,
        unresolved_unknown_decisions=0,
        in_flight_intents=0,
        possibly_billable_attempts=0,
    )
    reserved = "0"
    rows = journal.db.execute("""
        SELECT a.id,a.request_id,a.number,a.state,a.reservation,a.cost,a.resolution,
               a.result IS NOT NULL AS response_persisted,
               a.usage IS NOT NULL AS usage_persisted,
               r.stage,r.state AS request_state,r.parsed IS NOT NULL AS parsed_persisted
        FROM attempts a JOIN requests r ON r.id=a.request_id
        ORDER BY a.request_id,a.number,a.id
    """)
    for row in rows:
        a = dict(row)
        response = bool(a.pop("response_persisted"))
        usage = bool(a.pop("usage_persisted"))
        parsed = bool(a.pop("parsed_persisted"))
        unresolved = a["cost"] is None
        unknown = a["state"] in {"in_flight", "unknown"}
        replay = a["state"] == "response_saved" and response
        if a["state"] == "in_flight":
            boundary = "may_be_active_or_interrupted_no_remote_outcome_proof"
        elif a["state"] == "unknown":
            boundary = "remote_outcome_unknown_even_after_retry_or_skip"
        elif replay:
            boundary = "saved_response_can_be_parsed_locally_validation_may_fail"
        elif parsed:
            boundary = "request_has_parsed_checkpoint_revalidation_required"
        elif response:
            boundary = "response_preserved_but_not_automatically_replayable"
        else:
            boundary = "no_saved_response_recovery_depends_on_request_state_and_limits"
        # A zero settled estimate can record a provider rejection; it is still
        # not a supplier invoice. Missing mode is never silently treated as mock.
        possible = live and (unresolved or decimal_add(a["cost"], "0") > 0)
        billing = (
            "no_external_charge_in_recorded_offline_mode"
            if offline
            else "possibly_billable_not_invoice_verified"
            if possible
            else "zero_estimate_not_invoice_verified"
            if live
            else "execution_mode_unknown_billing_unknown"
        )
        counts["total_attempts"] += 1
        counts["persisted_responses"] += response
        counts["response_replay_candidates"] += replay
        counts["unresolved_reservations"] += unresolved
        counts["unknown_remote_outcomes"] += unknown
        counts["unresolved_unknown_decisions"] += (
            a["state"] == "unknown" and a["resolution"] is None
        )
        counts["in_flight_intents"] += a["state"] == "in_flight"
        counts["possibly_billable_attempts"] += possible
        if unresolved:
            reserved = str(decimal_add(reserved, a["reservation"]))
        attempts.append(
            dict(
                attempt_id=a["id"],
                request_id=a["request_id"],
                attempt_number=a["number"],
                stage=a["stage"],
                state=a["state"],
                request_state=a["request_state"],
                resolution=a["resolution"],
                response_persisted=response,
                request_parsed_persisted=parsed,
                settled_usage_persisted=usage,
                estimated_usd=a["cost"],
                unresolved_reserved_usd=a["reservation"] if unresolved else "0",
                recovery_boundary=boundary,
                billing_boundary=billing,
            )
        )
    return dict(
        schema_version=1,
        source="persisted_journal_only",
        execution_mode=mode,
        counts=counts,
        unresolved_reserved_usd=reserved,
        actual_billed_usd=None,
        external_exactly_once_guaranteed=False,
        limitations=[
            "Inspection does not call models, settle costs, resolve attempts or validate response bodies.",
            "A committed intent does not prove a request was sent; in-flight work may still be active.",
            "Saved responses can support local parsing; successful recovery still depends on validation, identity and execution limits.",
            "Unknown remote outcomes cannot guarantee exactly-once external calls; retry may duplicate work or charges.",
            "Retry and skip decisions retain unresolved reservations; skip does not cancel a provider request or prove zero charge.",
            "Estimates and reservations are budget accounting, not invoices or proof of actual billing.",
            "Counts overlap: saved responses may also have unresolved reservations or possible charges.",
        ],
        attempts=attempts,
    )
