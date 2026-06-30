# Contributing to cram-ai

Thanks for your interest in cram-ai — the profiler and referee for AI coding-agent tokens.
Contributions of all sizes are welcome: bug reports, fixes, docs, fixtures, and new audit
findings.

## Ground rules

- **Be honest with evidence.** cram's value is that it measures rather than promises. Keep the
  `measured` / `estimated` / `count` distinction intact, never present an estimate as a
  measurement, and frame benchmark numbers as reproducible reference points, not proof.
- **Determinism where it counts.** The `cram audit` profiler is 100% local and requires no API
  key. Keep it that way — don't introduce network or model calls into the audit/report path.
- Keep changes focused and covered by tests.

## Dev setup

```bash
git clone https://github.com/vishbay/cram-ai
cd cram-ai
python -m venv .venv && source .venv/bin/activate
pip install -e '.[mcp,dev]'        # editable install + MCP + pytest
cram doctor                        # sanity-check your setup
```

Requires Python ≥ 3.10.

## Running tests

```bash
python -m pytest -q                # full suite
python -m pytest tests/test_rig.py -q
```

All PRs must keep the suite green. CI runs the matrix on Python 3.10–3.13 plus a package
build/smoke job and a version-drift guard (`tests/test_version.py`).

## Linting

CI runs `ruff` on every push. Run it locally before opening a PR:

```bash
ruff check cram/ tests/
```

`ruff` is included in the `dev` extras (`pip install -e '.[mcp,dev]'`). Config lives in
`pyproject.toml` — a small set of ignored rules for deliberate compact-style patterns.

## Workflow

1. Branch off `main` with a descriptive name: `feat/...`, `fix/...`, `docs/...`, `chore/...`.
2. Make your change with tests. Run `python -m pytest -q` locally.
3. Add a bullet to the `## [Unreleased]` section of [CHANGELOG.md](CHANGELOG.md).
4. Open a PR against `main`. CI must pass before merge.

`main` is protected; all changes land via pull request.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/) prefixes (`feat:`, `fix:`,
`docs:`, `chore:`, `test:`). When a change is co-authored with an AI assistant, end the commit
body with the appropriate `Co-Authored-By:` trailer.

## Releases

Releases are tag-driven: pushing a `vX.Y.Z` tag triggers `.github/workflows/publish.yml`, which
builds and publishes to PyPI via trusted publishing. Bump `version` in `pyproject.toml`, move
the `## [Unreleased]` entries under the new version in `CHANGELOG.md`, then tag.

## Where to reach out

All contact is through GitHub — no email needed:

- **Bugs / features:** open an issue with the templates under [.github/ISSUE_TEMPLATE](.github/ISSUE_TEMPLATE).
- **Questions / ideas:** use [GitHub Discussions](https://github.com/vishbay/cram-ai/discussions) (if enabled), otherwise open an issue.
- **Security or other private matters:** use GitHub's private vulnerability reporting — see [SECURITY.md](SECURITY.md). Do not open a public issue for these.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating you agree
to uphold it.
