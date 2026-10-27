"""Real controller and SQLite budgets must not depend on caller Decimal settings."""

import json
import tempfile
import unittest
from decimal import (
    ROUND_DOWN,
    ROUND_UP,
    Decimal,
    localcontext,
)
from fractions import Fraction
from pathlib import Path

from ecoalign_forge.runtime.control import (
    Quota,
    RequestController,
    RequestFailedError,
    RunPausedError,
    TransportError,
    budget_snapshot,
)
from ecoalign_forge.runtime.journal import Journal
from ecoalign_forge.schemas.kernel import GenerationResult, RunConfig

EXACT_COST = Decimal("0.00038517")


class TokenCostPrecisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls = []

    def controller(self, budget="1", input_rate="0.37", output_rate="0.41", unknown=False):
        config = RunConfig(
            execution_mode="live",
            max_run_cost=Decimal(budget),
            max_attempts=1,
            models={
                role: {"model": "synthetic-model", "max_tokens": 37}
                for role in RunConfig().models
            },
            prices={
                "synthetic-model": {
                    "input_per_million": input_rate,
                    "output_per_million": output_rate,
                    "max_input_tokens": 1000,
                }
            },
        )
        journal = Journal(self.root / "run")
        journal.initialize({"run_id": "precision-fixture"}, [])
        quota = Quota(self.root, config)
        self.addCleanup(journal.close)
        self.addCleanup(quota.close)

        async def transport(payload, attempt_id, settings):
            # The live-mode budget path is exercised without network/model calls.
            self.calls.append(attempt_id)
            if unknown:
                raise TransportError("unknown")
            return self.response(attempt_id)

        return RequestController(journal, quota, config, transport), journal

    @staticmethod
    def response(attempt_id="synthetic-attempt"):
        return GenerationResult(
            content="{}",
            model="synthetic-model",
            attempt_id=attempt_id,
            finish_reason="stop",
            usage={"prompt_tokens": 1000, "completion_tokens": 37, "total_tokens": 1037},
        )

    def test_reservation_and_settlement_ignore_hostile_decimal_context(self):
        controller, _ = self.controller()
        for rounding in (ROUND_DOWN, ROUND_UP):
            for trap in (False, True):
                with self.subTest(rounding=rounding, traps=trap), localcontext() as context:
                    context.prec = 2
                    context.rounding = rounding
                    context.Emin, context.Emax = -2, 2
                    for signal in context.traps:
                        context.traps[signal] = trap
                    context.clear_flags()
                    flags = dict(context.flags)
                    reservation, tokens = controller.reservation("judge", [])
                    self.assertEqual(reservation, EXACT_COST)
                    self.assertEqual(tokens, 1037)
                    self.assertEqual(controller.cost("judge", self.response()), EXACT_COST)
                    self.assertEqual(dict(context.flags), flags)

    async def test_insufficient_exact_budget_never_dispatches(self):
        controller, journal = self.controller(budget="0.000382")
        with localcontext() as context:
            context.prec = 2
            with self.assertRaisesRegex(RunPausedError, "budget_exhausted"):
                await controller.call("request", "judge", [], [], json.loads)
        self.assertEqual(self.calls, [])
        self.assertEqual(journal.attempts(), [])

    async def test_exact_budget_boundary_settles_once_and_rejects_next_request(self):
        controller, journal = self.controller(budget=str(EXACT_COST))
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.Emin, context.Emax = -2, 2
            for signal in context.traps:
                context.traps[signal] = True
            self.assertEqual(await controller.call("first", "judge", [], [], json.loads), {})
            totals = budget_snapshot(journal)
            self.assertEqual(Decimal(totals["estimated_usd"]), EXACT_COST)
            self.assertEqual(Decimal(totals["total_committed_usd"]), EXACT_COST)
            self.assertEqual(totals["unresolved_reserved_usd"], "0")
            with self.assertRaisesRegex(RunPausedError, "budget_exhausted"):
                await controller.call("next", "judge", [], [], json.loads)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(journal.attempts()), 1)

    def test_rates_beyond_default_precision_keep_all_digits(self):
        input_rate = "0.123456789012345678901234567890123456789"
        output_rate = "0.000000000000000000000000000000000000001"
        controller, _ = self.controller(input_rate=input_rate, output_rate=output_rate)
        expected = (
            Fraction(Decimal(input_rate)) * 1000 + Fraction(Decimal(output_rate)) * 37
        ) / 1_000_000
        for precision in (2, 28):
            with self.subTest(precision=precision), localcontext() as context:
                context.prec = precision
                reservation, _ = controller.reservation("judge", [])
                self.assertEqual(Fraction(reservation), expected)
                self.assertEqual(Fraction(controller.cost("judge", self.response())), expected)

    async def test_unknown_outcome_keeps_exact_reservation_after_explicit_skip(self):
        controller, journal = self.controller(unknown=True)
        with localcontext() as context:
            context.prec = 2
            with self.assertRaisesRegex(RunPausedError, "unknown_requests"):
                await controller.call("unknown", "judge", [], [], json.loads)
            attempt_id = journal.unknown()[0]["id"]
            before = budget_snapshot(journal)
            self.assertEqual(Decimal(before["unresolved_reserved_usd"]), EXACT_COST)
            journal.resolve_unknown({attempt_id: "skip"})
            self.assertEqual(budget_snapshot(journal), before)
            with self.assertRaisesRegex(RequestFailedError, "explicitly_skipped"):
                await controller.call("unknown", "judge", [], [], json.loads)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(before["actual_billed_usd"])


if __name__ == "__main__":
    unittest.main()
