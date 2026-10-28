"""Reports use the exact verified dataset bytes, never a later unchecked read."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ecoalign_forge.engine.kernel import SynthesisKernel
from ecoalign_forge.policy.builtin import builtin_pack
from ecoalign_forge.schemas.kernel import RunConfig, text_hash
from ecoalign_forge.workbench import report
from ecoalign_forge.workbench.dataset import DatasetConfig, build_dataset, verify_dataset


class ReportDatasetSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="preference-report-snapshot-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        kernel = SynthesisKernel(
            data_dir=self.root / "runs", datasets_dir=self.root / "machine-datasets"
        )
        result = await kernel.run(builtin_pack(), RunConfig())
        self.assertEqual(result["exit_code"], 3)
        self.run = Path(result["run_dir"])
        self.dataset = build_dataset(
            [self.run], self.root / "datasets", DatasetConfig(include_unreviewed=True)
        )

    async def test_dataset_pair_bytes_changed_after_verification_are_refused(self):
        pairs = self.dataset / "pairs.jsonl"
        original = pairs.read_bytes()
        machine = (self.run / "run.sqlite3").read_bytes()
        output = self.root / "reports"

        def change_after_verify(path):
            manifest = verify_dataset(path)
            # Valid JSON and the same pair count: count-only checks cannot detect this.
            pairs.write_bytes(original.rstrip(b"\n") + b" \n")
            return manifest

        with (
            patch.object(report, "verify_dataset", side_effect=change_after_verify),
            self.assertRaisesRegex(ValueError, "Dataset pairs changed"),
        ):
            report.build_report(self.run, output, dataset=self.dataset)
        self.assertFalse(output.exists())
        self.assertEqual((self.run / "run.sqlite3").read_bytes(), machine)
        self.assertNotEqual(text_hash(pairs.read_text()), text_hash(original.decode()))

    async def test_unchanged_dataset_report_preserves_exact_pair_bytes(self):
        original = (self.dataset / "pairs.jsonl").read_bytes()
        output = report.build_report(self.run, self.root / "reports", dataset=self.dataset)
        body = json.loads((output.parent / "report.json").read_text())
        self.assertEqual((output.parent / "pairs.jsonl").read_bytes(), original)
        self.assertEqual(body["pairs_sha256"], body["dataset"]["pairs_sha256"])
        self.assertEqual(body["reported_pairs"], body["dataset"]["pairs"])
        self.assertEqual(
            report.build_report(self.run, self.root / "reports", dataset=self.dataset), output
        )
