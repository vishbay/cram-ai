# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Real, model-aware $ in `cram audit`**: each session's effective input is now priced by the
  actual model it ran on (`claude-opus-4-8`, `gpt-5`, …; falls back to the provider rate when
  unrecorded), instead of a flat Sonnet rate. The report leads with a money headline — "~$X
  effective input · ~$Y/mo · biggest avoidable: <layer> ~$Z/mo" — across text, markdown, and the
  HTML dashboard. New JSON keys (`schema_version` → `audit/2`): `total_eff_cost`, `monthly_cost`,
  `cost_per_measured_session`, `cost_measured_sessions`, `model_mix`, `biggest_avoidable`. New
  `cost_model.resolve_model_price` (override the table via `CRAM_MODEL_PRICES`).

### Fixed
- `cram rig` no longer silently miscounts failures. Each cell now records a precise status
  (`ran` / `no_transcript` / `unavailable` / `setup_error` / `run_error` / `oracle_timeout`):
  a runner crash, a workdir/clone error, and an oracle **timeout** are excluded from the success
  rate instead of being read as a task loss; a passing run with no measurable transcript is
  counted toward success but its tokens are **not** averaged in as a phantom 0.

### Changed
- Repositioned the context layer as the **reference optimizer** `cram rig` benchmarks, not a
  product claim: `cram --help` now groups commands ("Profile & referee" / "Setup" / "Optional —
  context layer"); `recommend.py`'s context-layer entry is labelled experimental (one remediation,
  verify don't assume); "token savings" softened to "model the cache-write cost" in `cram
  benchmark` help, the `run_benchmark` MCP docstring, and the README.

### Added
- `cram audit`: a **versioned, stable JSON contract** — every `--json` document (aggregate,
  `--session`, `--layer`, `--compare`) carries `schema_version` (`audit/1`), the aggregate
  top-level key set is stable (null, not omitted), and a `bases` map marks each headline cost
  metric measured/estimated. Documented in `docs/AUDIT_JSON.md`.
- `cram audit` findings now carry `sample_n` + `preliminary`; a finding based on fewer than 3
  measured sessions is tagged preliminary (fired, but not read as a verdict off N=1).
- `cram rig`: live per-cell progress on stderr; self-describing `meta` (model/runner/version/…)
  auto-embedded in `--json`; `--clean-cache`, `--keep-workdirs`, `--model`, and `--no-baseline`
  flags; a required-baseline guard; one clone retry on transient failure. `summarize()` now
  reports a per-provider `failures` breakdown and `unmeasured` count.

## [0.8.3] — 2026-06-20

Open-source-reveal hygiene release.

### Changed
- Removed the maintainer email from package metadata and docs — all contributor contact now
  goes through GitHub (private vulnerability reporting, issues, discussions). This release scrubs
  the email from the PyPI project page (0.8.2's metadata still showed it).
- README restructured so the core (audit + referee) leads and the context layer is grouped under
  an explicit "Optional" divider; "Evidence so far" trimmed to a summary linking the case study.
- `action.yml` default `cram-version` → 0.8.3.

### Removed
- Untracked machine-specific dogfooding configs that carried local absolute paths (`.mcp.json`,
  `.claude/settings.json`, `.claude/docs.yml`, `.codex/config.toml`, `.codex/hooks.json`,
  `CLAUDE.md`, `AGENTS.md`) and the internal `PLAN_CARRIED_OUTPUT.md`; moved `CASE_STUDY_RUNBOOK.md`
  under `docs/`. No tracked file contains a local absolute path.

## [0.8.2] — 2026-06-19

The "referee" release: `cram rig` becomes a reproducible, gamified, third-party-capable
benchmark — and the case study reports cram's own context layer honestly (including losses).

### Added
- **Reproducible referee benchmark** `cram-bench-v1` (`examples/rig/bench/`): a self-contained,
  tiered corpus (small/medium/large) whose fixtures ship red — no external clone needed.
- **Real-repo benchmark tier** (`corpus.real.json`): a `cram rig` task pinned to `pallets/click`
  at a bugfix commit's parent, with a clean-room regression-test overlay as a red→green oracle.
  `Task` gains git-source fields (`repo`/`ref`/`overlay`/`env`); `_prepare_workdir` clones from a
  shared cache + applies the overlay.
- **Generic `CommandAdapter`** — referee any context-packer that emits context on stdout, with
  key-free presets `repomix` (`npx -y repomix --stdout`) and `files-to-prompt`.
- **Open leaderboard / "submit your optimizer" flow**: committed result files, a submission
  validator (`scripts/validate_bench_result.py` — baseline arm + declared model/version +
  success-first), a `bench leaderboard` CI workflow, and a submission guide. `render_leaderboard`
  groups per benchmark.
- `cram rig` gains `--repeats N`, `--tier`, and `--leaderboard <glob>`; `summarize()` adds
  `n_runs` and `eff_tokens_stdev`.
- **Opt-in Cursor token estimation**: `cram audit --estimate-cursor` (or `CRAM_CURSOR_ESTIMATE=1`)
  estimates read-token cost from file sizes, always labelled `estimated`, tunable via
  `CRAM_CHARS_PER_TOKEN`, kept out of measured aggregates.
- **GitHub Action** (`action.yml`) — posts a token-waste audit as a sticky PR comment and can
  gate a PR on the `cram rig` referee. Key-free, backed by `cram/ci.py`. `cram init --team` drops
  a starter `cram-audit.yml`.
- **Contributor scaffolding**: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`,
  `CHANGELOG.md`, a PR template, and bug/feature issue templates.

### Changed
- `cram rig`: the `cram` arm now genuinely competes — `_prepare_workdir` `git init`s the workdir
  and `CramAdapter` runs `cram init` + `cram task` for the active runner, instead of silently
  degrading to baseline.
- README now leads with the zero-config wedge ("Try it in 10 seconds — no API key" + badge) and a
  `cram rig` referee demo GIF (`scripts/demo/`).
- `CASE_STUDY.md`: added a 0.8.1 profiler pilot and the real-repo referee run (cram +20%, an
  honest negative), and reconciled the Codex cross-runner section.

### Fixed
- `cram rig --runner claude` (`LiveRunner`) now passes `--dangerously-skip-permissions`, so a
  headless run can actually edit files and run the oracle (previously every arm did zero work).
- `cram rig --repeats` workdir names broke transcript resolution (Claude dashes `#`), making every
  repeat measure 0 tokens — switched to a `-rep<n>` suffix.
- `cram rig` no longer copies fixture `__pycache__`/`*.pyc` into run workdirs (stale bytecode could
  shadow an agent's edit and corrupt the oracle).

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

[Unreleased]: https://github.com/vishbay/cram-ai/compare/v0.8.3...HEAD
[0.8.3]: https://github.com/vishbay/cram-ai/compare/v0.8.2...v0.8.3
[0.8.2]: https://github.com/vishbay/cram-ai/compare/v0.8.1...v0.8.2
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
