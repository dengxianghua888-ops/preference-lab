"""Build real release archives and check resource loading outside the checkout."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RESOURCE = "ecoalign_forge/resources/guidelines.md"


@pytest.fixture(scope="module")
def distributions(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Hatchling is a dev dependency; build locally without downloads or installs."""
    output = tmp_path_factory.mktemp("distributions")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "hatchling",
            "build",
            "--target",
            "wheel",
            "--target",
            "sdist",
            "--directory",
            str(output),
        ],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    wheels = list(output.glob("*.whl"))
    sdists = list(output.glob("*.tar.gz"))
    assert len(wheels) == len(sdists) == 1
    return wheels[0], sdists[0]


def test_wheel_and_sdist_include_identical_handbook(distributions: tuple[Path, Path]) -> None:
    wheel, sdist = distributions
    expected = (_ROOT / "src" / _RESOURCE).read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read(_RESOURCE) == expected
        assert not any(name.startswith("dashboard/") for name in archive.namelist())
    with tarfile.open(sdist) as archive:
        resources = [name for name in archive.getnames() if name.endswith(f"/src/{_RESOURCE}")]
        assert len(resources) == 1
        resource = archive.extractfile(resources[0])
        assert resource is not None
        assert resource.read() == expected
        # The source dashboard needs its native theme alongside the custom dark
        # shell; source releases must keep the same startup configuration.
        themes = [name for name in archive.getnames() if name.endswith("/.streamlit/config.toml")]
        assert len(themes) == 1
        theme = archive.extractfile(themes[0])
        assert theme is not None
        assert theme.read() == (_ROOT / ".streamlit/config.toml").read_bytes()


def test_wheel_loads_resources_without_source_checkout(
    distributions: tuple[Path, Path], tmp_path: Path
) -> None:
    wheel, _ = distributions
    # Import directly from the actual wheel in an isolated interpreter. This also
    # exercises Traversable resources from a zip, where pathlib assumptions fail.
    script = """
import hashlib
import json
import sys
sys.path.insert(0, sys.argv[1])
import ecoalign_forge
from ecoalign_forge._guidelines import get_guidelines_text, get_known_rule_ids
print(json.dumps({
    "module": ecoalign_forge.__file__,
    "version": ecoalign_forge.__version__,
    "hash": hashlib.sha256(get_guidelines_text().encode()).hexdigest(),
    "rules": sorted(get_known_rule_ids()),
}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(wheel)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    loaded = json.loads(result.stdout)
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert loaded["module"].startswith(str(wheel))
    assert loaded["version"] == project["version"]
    assert loaded["hash"] == hashlib.sha256((_ROOT / "src" / _RESOURCE).read_bytes()).hexdigest()
    assert {"A-001", "A-006", "B-001", "B-006"} <= set(loaded["rules"])
