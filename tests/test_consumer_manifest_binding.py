"""Consumer evidence must describe the actual bytes passed to the loader."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "consumer_verify", Path(__file__).parents[1] / "scripts/consumers/verify.py"
)
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def dataset(path):
    files = {
        "trl_standard.jsonl": b'{"chosen":"original"}\n',
        "trl_conversational.jsonl": b'{"chosen":[]}\n',
        "train_sharegpt.json": b"[]",
        "dataset_info.json": json.dumps(
            {"ecoalign_forge_dpo": {"file_name": "train_sharegpt.json"}}
        ).encode(),
    }
    for name, content in files.items():
        (path / name).write_bytes(content)
    manifest = {"files": {n: hashlib.sha256(c).hexdigest() for n, c in files.items()}}
    (path / "manifest.json").write_text(json.dumps(manifest))
    return manifest


@pytest.mark.parametrize("consumer", ["trl", "llamafactory"])
def test_verified_copy_is_bound_to_captured_manifest(tmp_path, consumer):
    manifest = dataset(tmp_path)
    original = (tmp_path / "manifest.json").read_bytes()
    with verify.verified_snapshot(tmp_path, consumer) as (snapshot, captured, digest):
        (tmp_path / "trl_standard.jsonl").write_text("replacement")
        (tmp_path / "manifest.json").write_text("{}")
        assert snapshot != tmp_path
        assert captured == manifest
        assert digest == hashlib.sha256(original).hexdigest()
        for name, expected in captured["files"].items():
            assert hashlib.sha256((snapshot / name).read_bytes()).hexdigest() == expected
    assert not snapshot.exists()


@pytest.mark.parametrize(
    "consumer,name", [("trl", "trl_standard.jsonl"), ("llamafactory", "train_sharegpt.json")]
)
def test_tampered_input_rejected_before_consumption(tmp_path, consumer, name):
    dataset(tmp_path)
    (tmp_path / name).write_text("changed")
    with (
        pytest.raises(ValueError, match="checksum mismatch"),
        verify.verified_snapshot(tmp_path, consumer),
    ):
        pytest.fail("must not reach consumer")


@pytest.mark.parametrize("name", ["trl_standard.jsonl", "trl_conversational.jsonl"])
def test_missing_consumer_hash_rejected(tmp_path, name):
    manifest = dataset(tmp_path)
    del manifest["files"][name]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="missing"), verify.verified_snapshot(tmp_path, "trl"):
        pytest.fail("must not reach consumer")


@pytest.mark.parametrize("name", ["../escape.json", "/tmp/escape.json", "manifest.json"])
def test_unsafe_manifest_paths_rejected(tmp_path, name):
    manifest = dataset(tmp_path)
    manifest["files"][name] = "unused"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unsafe"), verify.verified_snapshot(tmp_path, "trl"):
        pytest.fail("must not reach consumer")


def test_sharegpt_loader_cannot_redirect_to_unverified_file(tmp_path):
    manifest = dataset(tmp_path)
    content = json.dumps({"ecoalign_forge_dpo": {"file_name": "../outside.json"}}).encode()
    (tmp_path / "dataset_info.json").write_bytes(content)
    manifest["files"]["dataset_info.json"] = hashlib.sha256(content).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with (
        pytest.raises(ValueError, match="Unexpected"),
        verify.verified_snapshot(tmp_path, "llamafactory"),
    ):
        pytest.fail("must not reach consumer")
