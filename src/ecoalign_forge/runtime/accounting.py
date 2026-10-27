"""Exact, incremental budget projections; raw attempts remain authoritative."""

from __future__ import annotations

import json
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)

BUDGET_KEY = "budget_totals_v1"


def _exact_context(precision):
    return Context(
        prec=precision,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        traps=[Inexact, InvalidOperation, Overflow],
    )


def decimal_add(left, right):
    """Add finite decimal strings without SQLite REAL or context rounding."""
    left, right = Decimal(left), Decimal(right)
    if not left.is_finite() or not right.is_finite():
        raise ValueError("Budget amounts must be finite")
    exponent = min(left.as_tuple().exponent, right.as_tuple().exponent)
    precision = max(28, max(left.adjusted(), right.adjusted()) - exponent + 2)
    with localcontext(_exact_context(precision)):
        return left + right


def token_cost(input_tokens, output_tokens, input_per_million, output_per_million):
    """Compute the frozen token-price estimate without caller context rounding."""
    rates = (Decimal(input_per_million), Decimal(output_per_million))
    counts = (input_tokens, output_tokens)
    if any(type(count) is not int or count < 0 for count in counts) or any(
        not rate.is_finite() or rate < 0 for rate in rates
    ):
        raise ValueError("Token counts and prices must be finite and nonnegative")
    # Each product needs at most the sum of its operand coefficient lengths.
    # decimal_add increases this for the aligned sum. Use a fresh context so a
    # host application's rounding, exponent bounds and traps cannot alter fees.
    precision = 28
    for count, rate in zip(counts, rates, strict=True):
        digits = len(rate.as_tuple().digits) + len(Decimal(count).as_tuple().digits)
        precision = max(precision, digits)
    with localcontext(_exact_context(precision)):
        total = decimal_add(input_tokens * rates[0], output_tokens * rates[1])
        sign, digits, exponent = total.as_tuple()
        # Dividing by 10**6 changes only the exponent; it must not round again.
        return Decimal((sign, digits, exponent - 6))


def empty_budget():
    return dict(
        estimated_usd="0",
        unresolved_reserved_usd="0",
        total_committed_usd="0",
        attempts=0,
        attempts_without_usage_or_settlement=0,
        actual_billed_usd=None,
        supplier_reported_usage=None,
        attempts_with_usage=0,
    )


def adjust_budget(totals, attempt, direction=1):
    """Apply one row's accounting contribution, independent of state/resolution."""
    totals["attempts"] += direction
    unresolved = attempt["cost"] is None
    field = "unresolved_reserved_usd" if unresolved else "estimated_usd"
    amount = Decimal(attempt["reservation"] if unresolved else attempt["cost"])
    # copy_negate avoids rounding that Decimal unary minus can perform.
    totals[field] = str(
        decimal_add(totals[field], amount if direction == 1 else amount.copy_negate())
    )
    if unresolved:
        totals["attempts_without_usage_or_settlement"] += direction
    if attempt["usage"]:
        totals["attempts_with_usage"] += direction
        usage = totals["supplier_reported_usage"] or dict(
            prompt_tokens=0, completion_tokens=0, total_tokens=0
        )
        for key, value in json.loads(attempt["usage"]).items():
            if key in usage:
                usage[key] += direction * value
        totals["supplier_reported_usage"] = usage if totals["attempts_with_usage"] else None
    if not totals["attempts_without_usage_or_settlement"]:
        totals["unresolved_reserved_usd"] = "0"
    if totals["attempts"] == totals["attempts_without_usage_or_settlement"]:
        totals["estimated_usd"] = "0"
    totals["total_committed_usd"] = str(
        decimal_add(totals["estimated_usd"], totals["unresolved_reserved_usd"])
    )
    return totals
