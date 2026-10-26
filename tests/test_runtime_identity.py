"""Resume never silently changes the interpreter or request/validation stack."""

import copy
import json
import subprocess
import sys
from importlib import metadata

import pytest

from ecoalign_forge.policy.builtin import builtin_pack
from ecoalign_forge.runtime.identity import RUNTIME_DISTRIBUTIONS, environment_identity
from ecoalign_forge.runtime.journal import Journal
from ecoalign_forge.schemas.kernel import RunConfig
from tests.test_iteration_b_kernel import kernel


def test_environment_identity_records_python_and_runtime_requirements(monkeypatch):
    def version(name):
        if name == "tokenizers":
            raise metadata.PackageNotFoundError(name)
        return "1.2.3"

    monkeypatch.setattr("ecoalign_forge.runtime.identity.metadata.version", version)
    value = environment_identity()
    assert value["python"]["implementation"] == sys.implementation.name
    assert set(value["dependencies"]) == set(RUNTIME_DISTRIBUTIONS)
    assert value["dependencies"]["litellm"] == "1.2.3"
    assert value["dependencies"]["tokenizers"] is None
    assert value["sqlite_version"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "section,key",
    [("dependencies", "litellm"), ("dependencies", "pydantic"), ("python", "version")],
)
async def test_changed_environment_blocks_before_requests_and_requires_audited_choice(
    tmp_path, monkeypatch, section, key
):
    k = kernel(tmp_path)
    initial = await k.run(builtin_pack(), RunConfig(num_samples=1))
    original = environment_identity()
    changed = copy.deepcopy(original)
    changed[section][key] = "different-test-version"
    monkeypatch.setattr("ecoalign_forge.engine.kernel.environment_identity", lambda: changed)
    j = Journal(k.last_run_dir)
    manifest = j.get("manifest")
    attempts = j.attempts()
    j.close()
    assert manifest["environment"] == original
    with pytest.raises(ValueError, match=f"environment.{section}.{key}"):
        await k.resume(k.last_run_dir)
    j = Journal(k.last_run_dir)
    assert j.attempts() == attempts
    assert j.get("manifest") == manifest and j.get("effective_environment") is None
    j.close()
    resumed = await k.resume(k.last_run_dir, allow_environment_change=True)
    assert resumed["counts"] == initial["counts"]
    await k.resume(k.last_run_dir)
    j = Journal(k.last_run_dir)
    assert j.get("manifest") == manifest  # original identity is immutable
    assert j.get("effective_environment") == changed
    events = j.db.execute(
        "SELECT value FROM events WHERE kind='resume_environment_revision'"
    ).fetchall()
    assert len(events) == 1
    assert json.loads(events[0][0])["previous"] == original
    assert json.loads(events[0][0])["current"] == changed
    assert j.attempts() == attempts
    j.close()


@pytest.mark.asyncio
async def test_legacy_missing_environment_requires_explicit_choice(tmp_path):
    k = kernel(tmp_path)
    await k.run(builtin_pack(), RunConfig(num_samples=1))
    j = Journal(k.last_run_dir)
    manifest = j.get("manifest")
    del manifest["environment"]
    j.set("manifest", manifest)
    j.close()
    with pytest.raises(ValueError, match="checkpoint=None"):
        await k.resume(k.last_run_dir)
    await k.resume(k.last_run_dir, allow_environment_change=True)
    j = Journal(k.last_run_dir)
    assert j.get("effective_environment") == environment_identity()
    j.close()


@pytest.mark.asyncio
async def test_environment_override_does_not_bypass_source_guard(tmp_path):
    k = kernel(tmp_path)
    await k.run(builtin_pack(), RunConfig(num_samples=1))
    j = Journal(k.last_run_dir)
    manifest = j.get("manifest")
    manifest["code"]["source_sha256"] = "different"
    j.set("manifest", manifest)
    j.close()
    with pytest.raises(ValueError, match="Code/template differs"):
        await k.resume(k.last_run_dir, allow_environment_change=True)


@pytest.mark.asyncio
async def test_cli_explicit_environment_acceptance(tmp_path):
    k = kernel(tmp_path)
    original = await k.run(builtin_pack(), RunConfig(num_samples=1))
    j = Journal(k.last_run_dir)
    manifest = j.get("manifest")
    manifest["environment"]["dependencies"]["litellm"] = "older-test-version"
    j.set("manifest", manifest)
    j.close()
    command = [sys.executable, "-m", "ecoalign_forge", "resume", str(k.last_run_dir)]
    blocked = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert blocked.returncode == 2
    assert "environment.dependencies.litellm" in blocked.stderr
    accepted = subprocess.run(
        [*command, "--allow-environment-change"], capture_output=True, text=True, timeout=30
    )
    assert accepted.returncode == original["exit_code"], accepted.stderr
    j = Journal(k.last_run_dir)
    assert j.get("effective_environment") == environment_identity()
    j.close()


@pytest.mark.parametrize(
    "expected,current,message",
    [
        ({}, {"dependency": None}, "checkpoint=<missing>, current=None"),
        ({"dependency": None}, {}, "checkpoint=None, current=<missing>"),
        ({}, {"dependency": "<missing>"}, "checkpoint=<missing>, current='<missing>'"),
    ],
)
def test_missing_environment_key_is_distinct_from_null(expected, current, message):
    from ecoalign_forge.runtime.identity import environment_differences

    assert environment_differences(expected, current) == [f"environment.dependency: {message}"]
    assert environment_differences({"dependency": None}, {"dependency": None}) == []


@pytest.mark.asyncio
async def test_missing_nullable_dependency_requires_explicit_resume_acceptance(
    tmp_path, monkeypatch
):
    current = environment_identity()
    current["dependencies"]["tokenizers"] = None
    monkeypatch.setattr("ecoalign_forge.engine.kernel.environment_identity", lambda: current)
    k = kernel(tmp_path)
    await k.run(builtin_pack(), RunConfig(num_samples=1))
    j = Journal(k.last_run_dir)
    manifest = j.get("manifest")
    del manifest["environment"]["dependencies"]["tokenizers"]
    j.set("manifest", manifest)
    before = j.attempts()
    j.close()
    with pytest.raises(
        ValueError,
        match=r"environment\.dependencies\.tokenizers: checkpoint=<missing>, current=None",
    ):
        await k.resume(k.last_run_dir)
    j = Journal(k.last_run_dir)
    assert j.get("effective_environment") is None
    assert j.attempts() == before
    j.close()
    await k.resume(k.last_run_dir, allow_environment_change=True)
    j = Journal(k.last_run_dir)
    assert j.get("effective_environment")["dependencies"]["tokenizers"] is None
    assert "tokenizers" not in j.get("manifest")["environment"]["dependencies"]
    events = j.db.execute(
        "SELECT value FROM events WHERE kind='resume_environment_revision'"
    ).fetchall()
    assert len(events) == 1 and "checkpoint=<missing>" in json.loads(events[0][0])["differences"][0]
    assert j.attempts() == before
    j.close()
