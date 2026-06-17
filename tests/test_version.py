"""Guard against version / metadata drift.

`cram.__version__` is derived from installed package metadata, which is the
same value setuptools reads out of ``pyproject.toml`` at build time. If the
two diverge, a stale ``*.egg-info`` / build artifact has poisoned the local
environment — exactly the kind of drift that silently breaks `--version`,
release tagging, and any test that asserts on the version string.
"""

from __future__ import annotations

import os

import pytest

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

import cram

_PYPROJECT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pyproject.toml")


def _pyproject_version() -> str:
    with open(_PYPROJECT, "rb") as f:
        return tomllib.load(f)["project"]["version"]


def test_pyproject_declares_a_version():
    # Sanity: the field exists and looks like a release version, not a sentinel.
    version = _pyproject_version()
    assert version
    assert version[0].isdigit()


def test_installed_metadata_matches_pyproject():
    """Installed metadata must match pyproject — or be the dev sentinel.

    In CI and any `pip install -e .` checkout the two must agree. A raw source
    tree that was never installed reports the ``0.0.0+dev`` sentinel, which is
    fine; anything else means a stale build artifact is shadowing the source.
    """
    if cram.__version__ == "0.0.0+dev":
        pytest.skip("package not installed (raw source checkout)")
    assert cram.__version__ == _pyproject_version()
