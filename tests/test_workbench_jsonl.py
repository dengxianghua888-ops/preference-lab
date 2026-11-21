"""Physical LF boundaries must not split Unicode text inside records."""

import json

import pytest

from ecoalign_forge.workbench.jsonl import read_records, record_lines


@pytest.mark.parametrize("character", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize("separator", ["\n", "\r\n"])
def test_unicode_content_is_not_a_record_boundary(character, separator):
    rows = [{"content": f"甲{character}乙", "start": 2, "end": 3}, {"content": "末"}]
    content = separator.join(json.dumps(row, ensure_ascii=False) for row in rows) + separator
    assert len(record_lines(content)) == 2
    assert read_records(content.encode("utf-8")) == rows


def test_empty_file_final_line_and_invalid_blank_record():
    assert read_records(b"") == []
    assert read_records(b"{}") == [{}]
    assert read_records(b"{}\n") == [{}]
    with pytest.raises(json.JSONDecodeError):
        read_records(b"{}\n\n")


def test_unicode_frozen_source_archive_and_report(tmp_path):
    import asyncio
    import hashlib
    import io
    import zipfile

    from ecoalign_forge.policy.models import PolicyPack
    from ecoalign_forge.runtime.journal import Journal
    from ecoalign_forge.schemas.kernel import RunConfig
    from ecoalign_forge.workbench.dataset import (
        build_dataset,
        build_dataset_archive,
        verify_dataset,
    )
    from ecoalign_forge.workbench.report import build_report
    from tests.test_iteration_b_kernel import RecordedTransport, kernel
    from tests.test_iteration_b_policy import pack_data
    from tests.test_iteration_c_workbench import decide

    sample = "Contact 甲\u2028乙\u2029丙\u0085丁"
    pack = PolicyPack.model_validate(pack_data())
    client = kernel(tmp_path, RecordedTransport(pack, text=sample))
    asyncio.run(client.run(pack, RunConfig(execution_mode="mock", num_samples=1)))
    run = client.last_run_dir
    journal = Journal.readonly(run)
    try:
        case = journal.cases()[0]
    finally:
        journal.close()
    first = build_dataset([run], tmp_path / "sets")
    assert verify_dataset(first)["pairs"] == 0
    assert read_records((first / "sources.jsonl").read_bytes())[0]["source"]["content"] == sample
    decide(run, case["id"])
    second = build_dataset([run], tmp_path / "sets")
    manifest, payload = build_dataset_archive(second)
    assert manifest["pairs"] == 1
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        original = archive.read("pairs.jsonl")
        assert len(read_records(original)) == 1
        assert read_records(archive.read("sources.jsonl"))[0]["source"]["content"] == sample
        assert hashlib.sha256(original).hexdigest() == manifest["pairs_sha256"]
    report = build_report(run, tmp_path / "reports", dataset=second)
    assert (report.parent / "pairs.jsonl").read_bytes() == original
    assert json.loads((report.parent / "report.json").read_bytes())["reported_pairs"] == 1
    assert verify_dataset(first)["pairs"] == 0
