"""cram-ai package.

__version__ is derived from the installed package metadata (single source of
truth: the version in pyproject.toml) so it can never drift from the release.
Falls back to a sentinel when running from a source tree that was never
installed (e.g. `python -m cram.cli` straight from a checkout).
"""
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("cram-ai")
except PackageNotFoundError:  # not installed (raw source checkout)
    __version__ = "0.0.0+dev"
