"""Immutable outputs and downloads retain the actual verified byte identity."""

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from ecoalign_forge.engine.kernel import SynthesisKernel
from ecoalign_forge.policy.builtin import builtin_pack
from ecoalign_forge.runtime.journal import Journal
from ecoalign_forge.schemas.kernel import RunConfig
from ecoalign_forge.workbench import dataset
from ecoalign_forge.workbench.report import build_report


class ExportByteIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="preference-export-bytes-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        kernel = SynthesisKernel(
            data_dir=self.root / "runs", datasets_dir=self.root / "machine"
        )
        result = await kernel.run(builtin_pack(), RunConfig())
        self.assertEqual(result["exit_code"], 3)
        self.run = Path(result["run_dir"])
        self.machine = Path(result["output_path"])

    def test_machine_export_rejects_newline_drift_without_repairing_files(self):
        journal = Journal(self.run)
        self.addCleanup(journal.close)
        original_files = {p: p.read_bytes() for p in self.machine.parent.iterdir()}
        for name in ("pairs.jsonl", "trl_standard.jsonl", "policy.json", "README.md"):
            with self.subTest(name=name):
                target = self.machine.parent / name
                changed = original_files[target].replace(b"\n", b"\r\n")
                self.assertNotEqual(changed, original_files[target])
                target.write_bytes(changed)
                with self.assertRaisesRegex(ValueError, "Immutable export"):
                    journal.export(self.root / "machine")
                for path, content in original_files.items():
                    self.assertEqual(path.read_bytes(), changed if path == target else content)
                target.write_bytes(original_files[target])
        self.assertEqual(journal.export(self.root / "machine"), self.machine)

    def test_report_reuse_rejects_newline_drift_without_repairing_files(self):
        report = build_report(self.run, self.root / "reports")
        original_files = {p: p.read_bytes() for p in report.parent.iterdir()}
        for name in ("pairs.jsonl", "report.json", "report.html", "SHA256SUMS"):
            with self.subTest(name=name):
                target = report.parent / name
                changed = original_files[target].replace(b"\n", b"\r\n")
                self.assertNotEqual(changed, original_files[target])
                target.write_bytes(changed)
                with self.assertRaisesRegex(ValueError, "Immutable report conflict"):
                    build_report(self.run, self.root / "reports")
                for path, content in original_files.items():
                    self.assertEqual(path.read_bytes(), changed if path == target else content)
                target.write_bytes(original_files[target])
        self.assertEqual(build_report(self.run, self.root / "reports"), report)

    def curated(self):
        return dataset.build_dataset(
            [self.run],
            self.root / "curated",
            dataset.DatasetConfig(include_unreviewed=True),
        )

    def test_download_snapshot_rejects_changed_bytes_after_verification(self):
        path = self.curated()
        verify = dataset.verify_dataset
        original = (path / "pairs.jsonl").read_bytes()
        machine_before = (self.run / "run.sqlite3").read_bytes()

        def change_after_verify(directory):
            manifest = verify(directory)
            # Same JSON records and count; only the byte identity changes.
            (directory / "pairs.jsonl").write_bytes(original.rstrip(b"\n") + b" \n")
            return manifest

        with (
            patch.object(dataset, "verify_dataset", side_effect=change_after_verify),
            self.assertRaisesRegex(ValueError, "Dataset changed after verification"),
        ):
            dataset.build_dataset_archive(path)
        self.assertEqual((self.run / "run.sqlite3").read_bytes(), machine_before)

    def test_download_archive_retains_the_exact_verified_snapshot(self):
        path = self.curated()
        original = {
            p.relative_to(path).as_posix(): p.read_bytes()
            for p in path.rglob("*")
            if p.is_file()
        }
        manifest, content = dataset.build_dataset_archive(path)
        # The saved directory can change after capture; trace and ZIP use the
        # captured bytes, never read a later unchecked path.
        (path / "pairs.jsonl").write_bytes(b"changed after snapshot\n")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertEqual(set(archive.namelist()), {"manifest.json", *manifest["files"]})
            self.assertEqual(json.loads(archive.read("manifest.json")), manifest)
            for name, expected in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
                self.assertEqual(archive.read(name), original[name])


if __name__ == "__main__":
    unittest.main()
