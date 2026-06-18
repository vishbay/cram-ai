# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **GitHub Action** (`action.yml`) that posts a token-waste audit as a sticky PR comment and can
  gate a PR on the `cram rig` referee. Key-free; consumes committed/uploaded audit/rig JSON
  (transcripts never exist in CI). Modes: `compare`, `report`, `rig`. Backed by a new key-free
  `cram/ci.py` engine (`render_compare_comment`, `render_report_comment`, `evaluate_rig_gate`,
  `python -m cram.ci`). `cram init --team` now also drops a starter `cram-audit.yml`.
- Reproducible referee benchmark (`examples/rig/bench/`): a self-contained, tiered corpus
  (`cram-bench-v1`, small/medium/large) with fixtures that ship red — no external clone needed.
- `cram rig` gains `--repeats N` (run each cell N times for variance), `--tier` (filter by
  corpus tier), and `--leaderboard <glob>` (render a ranked markdown leaderboard from committed
  result JSON). `summarize()` now reports `n_runs` and `eff_tokens_stdev` (additive).
- Contributor scaffolding: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`,
  `CHANGELOG.md`, a pull-request template, and bug/feature issue templates.

### Changed
- `CASE_STUDY.md`: reconciled the Codex cross-runner section — separated the orientation metric
  (reads before edit, flat on Codex) from the convergence metric (the N=1 −47.5% effective-token
  result), and rewrote the synthesis so it no longer reads as self-contradictory.

### Fixed
- `cram rig` no longer copies fixture `__pycache__`/`*.pyc` into run workdirs (stale bytecode
  could shadow an agent's edit and corrupt the oracle).

## [0.8.1] — 2026-06-17
### Changed
- `cram doctor` now requires Python ≥ 3.10, matching `requires-python`.
- ARCHITECTURE.md generator grounded on the real repo structure (prunes phantom file entries).
### Added
- Version-drift guard (`tests/test_version.py`) and a CI `package` build/smoke job.
- `gemini` added to the top-level `--target` help.
- Codex case study (Issue #2786) and workflow-fit hypothesis in `CASE_STUDY.md`.

## [0.8.0] — 2026-06-17
### Added
- Failed-command / retry-loop drilldown across `audit`, `--session`, `--json`, `--report`, and
  `--layer retries`.
- Per-layer cost attribution (`$`/session) and a "Cost by waste layer" table.
- HTML report reworked into a dark data dashboard with coverage/confidence, retry loops, cost by
  layer, context on/off A/B, and embedded session drilldowns.
### Fixed
- `cram -V` derives the version from package metadata instead of a hardcoded value.

## [0.7.0] — 2026-06-16
### Added
- Standalone HTML audit report (`cram audit --report-html`).
### Removed
- The Textual TUI, in favour of the HTML report.

## [0.6.3] — 2026-06-16
### Fixed
- TUI vertical scroll.

## [0.6.2] — 2026-06-16
### Added
- Codex `--session` drill-in.

## [0.6.1] — 2026-06-16
### Fixed
- Session-count rows in the report.

## [0.6.0] — 2026-06-16
### Added
- The `cram audit` profiler surface.

## [0.5.1] — 2026-06-15
### Added
- `cram --version` / `-V` flag.

## [0.5.0] — 2026-06-15
### Added
- Optimizer verification (`cram rig`), deterministic context sync, and honest framing of the
  context layer.

## [0.4.0] — 2026-06-12
### Added
- Audit-first repositioning: see where your agent tokens go.

[Unreleased]: https://github.com/vishbay/cram-ai/compare/v0.8.1...HEAD
[0.8.1]: https://github.com/vishbay/cram-ai/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/vishbay/cram-ai/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/vishbay/cram-ai/compare/v0.6.3...v0.7.0
[0.6.3]: https://github.com/vishbay/cram-ai/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/vishbay/cram-ai/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/vishbay/cram-ai/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/vishbay/cram-ai/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/vishbay/cram-ai/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/vishbay/cram-ai/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/vishbay/cram-ai/releases/tag/v0.4.0
