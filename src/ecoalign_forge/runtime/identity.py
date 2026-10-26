"""Versioned execution environment identity for auditable checkpoint recovery."""

from __future__ import annotations

import platform
import sqlite3
import sys
from importlib import metadata

_MISSING = object()

# Direct runtime requirements plus SDK/validation/tokenization dependencies that
# can change provider requests, response parsing or token accounting.
RUNTIME_DISTRIBUTIONS = (
    "litellm",
    "pydantic",
    "pydantic-core",
    "pydantic-settings",
    "orjson",
    "rich",
    "tenacity",
    "python-dotenv",
    "openai",
    "httpx",
    "tiktoken",
    "tokenizers",
)


def environment_identity():
    dependencies = {}
    for name in RUNTIME_DISTRIBUTIONS:
        try:
            dependencies[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            dependencies[name] = None
    return dict(
        schema_version=1,
        python=dict(implementation=sys.implementation.name, version=platform.python_version()),
        sqlite_version=sqlite3.sqlite_version,
        dependencies=dependencies,
    )


def environment_differences(expected, current, prefix="environment"):
    """Report each missing or changed field without paths, secrets or machine IDs."""
    if isinstance(expected, dict) and isinstance(current, dict):
        differences = []
        for key in sorted(expected.keys() | current.keys()):
            differences.extend(
                environment_differences(
                    expected.get(key, _MISSING), current.get(key, _MISSING), f"{prefix}.{key}"
                )
            )
        return differences
    if expected == current:
        return []
    expected_label = "<missing>" if expected is _MISSING else repr(expected)
    current_label = "<missing>" if current is _MISSING else repr(current)
    return [f"{prefix}: checkpoint={expected_label}, current={current_label}"]
