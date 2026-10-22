"""A failed public export must preserve the last complete consumer file."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ecoalign_forge.export.sharegpt_format import export_dataset_info, export_sharegpt
from ecoalign_forge.schemas.dpo import DPO_Pair


class ShareGPTAtomicExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pair = DPO_Pair(
            prompt="联系示例 🐦",
            chosen="good",
            rejected="bad",
            chosen_score=1,
            rejected_score=0,
            preference_gap=1,
            dimension="contact",
            difficulty="easy",
            source_case_id="one",
        )

    def test_sharegpt_failed_replace_preserves_previous_file(self):
        path = self.root / "train_sharegpt.json"
        previous = b'[{"previous":"complete"}]\n'
        path.write_bytes(previous)
        with patch("ecoalign_forge.runtime.journal.os.replace", side_effect=OSError("disk fault")):
            with self.assertRaises(OSError):
                export_sharegpt([self.pair], path)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_dataset_info_failed_replace_preserves_previous_file(self):
        path = self.root / "dataset_info.json"
        previous = b'{"previous":"complete"}\n'
        path.write_bytes(previous)
        with patch("ecoalign_forge.runtime.journal.os.replace", side_effect=OSError("disk fault")):
            with self.assertRaises(OSError):
                export_dataset_info(self.root)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_failed_first_export_leaves_no_partial_consumer_files(self):
        for export in (
            lambda: export_sharegpt([self.pair], self.root / "train_sharegpt.json"),
            lambda: export_dataset_info(self.root),
        ):
            with self.subTest(export=export):
                with patch(
                    "ecoalign_forge.runtime.journal.os.replace", side_effect=OSError("disk fault")
                ):
                    with self.assertRaises(OSError):
                        export()
                self.assertEqual(list(self.root.iterdir()), [])

    def test_encoding_failure_preserves_previous_exports(self):
        invalid_pair = self.pair.model_copy(update={"prompt": "invalid \ud800 text"})
        for name, export in (
            (
                "train_sharegpt.json",
                lambda: export_sharegpt([invalid_pair], self.root / "train_sharegpt.json"),
            ),
            ("dataset_info.json", lambda: export_dataset_info(self.root, dataset_name="\ud800")),
        ):
            with self.subTest(file=name):
                path = self.root / name
                previous = b'{"previous":"complete"}\n'
                path.write_bytes(previous)
                with self.assertRaises(UnicodeEncodeError):
                    export()
                self.assertEqual(path.read_bytes(), previous)
                self.assertFalse(list(self.root.glob("*.tmp")))

    def test_successful_exports_keep_unicode_and_existing_consumer_format(self):
        data_path = export_sharegpt([self.pair], self.root / "train_sharegpt.json")
        info_path = export_dataset_info(self.root, dataset_name="contact")
        self.assertIn(self.pair.prompt, data_path.read_text(encoding="utf-8"))
        record = json.loads(data_path.read_text(encoding="utf-8"))[0]
        self.assertEqual(record["conversations"], [{"from": "human", "value": self.pair.prompt}])
        info = json.loads(info_path.read_text(encoding="utf-8"))
        self.assertEqual(info["contact"]["file_name"], "train_sharegpt.json")
        self.assertTrue(info["contact"]["ranking"])
        self.assertEqual({p.name for p in self.root.iterdir()}, {data_path.name, info_path.name})


if __name__ == "__main__":
    unittest.main()
