"""Local quota accounting uses known token components without provider calls."""

import json
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ecoalign_forge.runtime.control import (
    LiteLLMTransport,
    Quota,
    RequestController,
    RunPausedError,
)
from ecoalign_forge.runtime.journal import Journal
from ecoalign_forge.schemas.kernel import RunConfig


class QuotaUsageFloorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="quota-usage-floor-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def quota(self, name="quota", **settings):
        quota = Quota(self.root / name, RunConfig(tpm=100, rpm=10, max_concurrent=1, **settings))
        self.addCleanup(quota.close)
        return quota

    async def test_small_total_cannot_reopen_the_shared_tpm_window(self):
        quota = self.quota()
        await quota.acquire("first", 100, time.time() + 1)
        usage = {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 0}
        quota.release("first", usage)
        peer = self.quota()
        with self.assertRaisesRegex(RunPausedError, "run_deadline"):
            await peer.acquire("second", 1, time.time() + 0.08)
        row = dict(quota.db.execute("SELECT * FROM leases WHERE id='first'").fetchone())
        self.assertEqual(row["tokens"], 100)
        self.assertEqual(row["active"], 0)
        self.assertEqual(usage["total_tokens"], 0)  # Preserve raw supplier evidence.

    async def test_larger_total_or_complete_components_remain_authoritative(self):
        for index, (usage, expected) in enumerate(
            [
                ({"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 60}, 60),
                ({"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}, 40),
                ({"prompt_tokens": 30, "completion_tokens": 10}, 40),
                ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0),
                ({"total_tokens": 10}, 10),
            ]
        ):
            with self.subTest(usage=usage):
                quota = self.quota(str(index))
                await quota.acquire("one", 100, time.time() + 1)
                quota.release("one", usage)
                self.assertEqual(quota.db.execute("SELECT tokens FROM leases").fetchone()[0], expected)
                await quota.acquire("boundary", 100 - expected, time.time() + 1)
                quota.release("boundary")
                with self.assertRaisesRegex(RunPausedError, "run_deadline"):
                    await quota.acquire("exceeds", 1, time.time() + 0.06)

    async def test_missing_or_invalid_usage_retains_reservation_and_releases_slot(self):
        for index, usage in enumerate(
            [None, {}, {"prompt_tokens": 20}, {"total_tokens": -1}, {"total_tokens": True},
             {"total_tokens": "10"}, {"prompt_tokens": -1, "completion_tokens": 10},
             {"prompt_tokens": True, "completion_tokens": 10}]
        ):
            with self.subTest(usage=usage):
                quota = self.quota(str(index))
                await quota.acquire("one", 100, time.time() + 1)
                quota.release("one", usage)
                row = quota.db.execute("SELECT tokens,active FROM leases").fetchone()
                self.assertEqual(tuple(row), (100, 0))
                with self.assertRaisesRegex(RunPausedError, "run_deadline"):
                    await quota.acquire("two", 1, time.time() + 0.05)

    async def test_real_transport_and_controller_preserve_raw_usage_and_exact_cost(self):
        config = RunConfig(
            execution_mode="live", max_run_cost=1, tpm=1000,
            models={role: {"model": "offline-fixture", "max_tokens": 100}
                    for role in RunConfig().models},
            prices={"offline-fixture": {"input_per_million": 1, "output_per_million": 2,
                                        "max_input_tokens": 900}},
        )
        journal = Journal(self.root / "run")
        self.addCleanup(journal.close)
        journal.initialize({"run_id": "quota-fixture"}, [])
        quota = Quota(self.root, config)
        self.addCleanup(quota.close)
        usage = {"prompt_tokens": 800, "completion_tokens": 200, "total_tokens": 0}

        class Stream:
            def __init__(self):
                self.sent = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.sent:
                    raise StopAsyncIteration
                self.sent = True
                return SimpleNamespace(model="offline-fixture", id="fixture-id", usage=usage,
                    choices=[SimpleNamespace(delta=SimpleNamespace(content='{"ok":true}'),
                                             finish_reason="stop")])

            async def aclose(self):
                pass

        sdk_call = AsyncMock(return_value=Stream())
        # Replace only the uninstalled SDK boundary, retaining the real adapter,
        # controller, journal and shared quota. This is not real-provider testing.
        with patch.dict("sys.modules", {"litellm": SimpleNamespace(acompletion=sdk_call)}):
            controller = RequestController(journal, quota, config, LiteLLMTransport())
            self.assertEqual(await controller.call("first", "judge", [], [], json.loads), {"ok": True})
            controller.deadline = time.time() + 0.08
            with self.assertRaisesRegex(RunPausedError, "run_deadline"):
                await controller.call("next", "judge", [], [], json.loads)
        self.assertEqual(sdk_call.await_count, 1)
        attempt = journal.attempts()[0]
        self.assertEqual(json.loads(attempt["usage"]), usage)
        self.assertEqual(Decimal(attempt["cost"]), Decimal("0.0012"))
        self.assertEqual(journal.budget_snapshot()["supplier_reported_usage"]["total_tokens"], 0)
        self.assertEqual(quota.db.execute("SELECT tokens FROM leases").fetchone()[0], 1000)
        self.assertEqual(len(journal.attempts()), 1)


if __name__ == "__main__":
    unittest.main()
