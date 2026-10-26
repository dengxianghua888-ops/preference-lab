"""No-network controller accounting benchmark, excluding shared quota scheduling.

One synthetic sample makes moderator/judge/reviewer requests, plus a generator
request per 100 samples. This isolates accounting, not end-to-end synthesis speed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import tempfile
import time
from pathlib import Path

from ecoalign_forge.runtime.control import RequestController
from ecoalign_forge.runtime.journal import Journal
from ecoalign_forge.schemas.kernel import GenerationResult, RunConfig


class ProfileJournal(Journal):
    def __init__(self, path):
        self.profile = dict(
            calls=0, select_statements=0, returned_rows=0, returned_bytes=0, seconds=0.0
        )
        super().__init__(path)
        self.profile = dict.fromkeys(self.profile, 0)

    def budget_snapshot(self):
        previous_factory = self.db.row_factory

        def row_factory(cursor, row):
            self.profile["returned_rows"] += 1
            self.profile["returned_bytes"] += sum(len(str(value).encode()) for value in row)
            return sqlite3.Row(cursor, row)

        def trace(sql):
            if sql.lstrip().upper().startswith("SELECT"):
                self.profile["select_statements"] += 1

        self.db.row_factory = row_factory
        self.db.set_trace_callback(trace)
        started = time.perf_counter()
        try:
            self.profile["calls"] += 1
            return super().budget_snapshot()
        finally:
            self.profile["seconds"] += time.perf_counter() - started
            self.db.row_factory = previous_factory
            self.db.set_trace_callback(None)


class ImmediateQuota:
    async def acquire(self, *_):
        pass

    def release(self, *_):
        pass


async def benchmark(samples, response_bytes):
    content = json.dumps({"fixture": "x" * response_bytes})

    async def transport(payload, attempt_id, config):
        return GenerationResult(
            content=content,
            model="fixture",
            finish_reason="fixture",
            attempt_id=attempt_id,
            usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    defaults = RunConfig()
    config = RunConfig(
        execution_mode="live",
        num_samples=samples,
        batch_size=100,
        max_run_cost=1000,
        prices={
            m.model: dict(input_per_million=1, output_per_million=2)
            for m in defaults.models.values()
        },
    )
    with tempfile.TemporaryDirectory(prefix="ecoalign-accounting-") as root:
        journal = ProfileJournal(Path(root))
        journal.initialize(dict(run_id="benchmark"), [])
        controller = RequestController(journal, ImmediateQuota(), config, transport)
        started = time.perf_counter()
        requests = 0
        try:
            for sample in range(samples):
                stages = ["moderator", "judge", "reviewer"]
                if sample % 100 == 0:
                    stages.insert(0, "generator")
                for stage in stages:
                    await controller.call(f"{sample}:{stage}", stage, [], [], json.loads)
                    requests += 1
            wall_seconds = time.perf_counter() - started
            budget = journal.get("budget_totals_v1")
            assert budget["attempts"] == requests
            assert budget["attempts_without_usage_or_settlement"] == 0
            assert budget["supplier_reported_usage"]["total_tokens"] == requests * 2
            return dict(
                samples=samples,
                requests=requests,
                response_bytes=len(content),
                wall_seconds=wall_seconds,
                budget_reads=journal.profile,
                budget=budget,
            )
        finally:
            journal.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", nargs="+", type=int, default=[100, 1000, 10000])
    parser.add_argument("--response-bytes", type=int, default=4096)
    args = parser.parse_args()
    for samples in args.samples:
        print(json.dumps(asyncio.run(benchmark(samples, args.response_bytes))), flush=True)


if __name__ == "__main__":
    main()
