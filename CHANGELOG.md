# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.0] — 2026-06-25

### Added
- **Richer HTML report** (`cram audit --report-html`): an SVG composition donut on the token
  waterfall, an SVG trend sparkline + pre-edit-share gauge in the headline, inline magnitude bars
  in the session leaderboard, severity-colored waste-layer bars, and gauge bars on key metrics —
  all self-contained SVG/CSS, dark + light, no external assets.
- **Ruff** lint in dev deps and a CI `lint` job (`ruff check cram/ tests/`). Config in
  `pyproject.toml` (ignores deliberate compact-style rules). Cleared the existing 90 findings
  (unused imports, placeholder-less f-strings, unused vars).

### Fixed
- **Per-model prompt-cache floor**: `utils.cache_min_tokens()` under-reported the cacheable-prefix
  minimum for non-Opus models (returned 1024 for Sonnet 4.6 and Haiku 4.5; real Anthropic floors are
  2048 and 4096). Corrected to the per-family floors and unified with `benchmark.py`'s cacheable-prefix
  check so there is one source of truth.
- `docs/AUDIT_JSON.md`: the `schema_version` example said `audit/1`; now `audit/3`.

### Changed
- `cram hook install|uninstall` now manages the git hooks only; the global `~/.claude/CLAUDE.md`
  SessionStart block is managed separately via `cram hook global-install|global-uninstall`. Removing
  the post-commit hook no longer strips the global block (and vice-versa). `cram init` still wires up
  both.

## [0.9.0] — 2026-06-21

The profiler+referee hardening release: the profiler answers "what is this costing me, what do
I fix first, did the fix work, and is it improving?" — and the referee stops miscounting itself.

### Added
- **Real, model-aware $** in `cram audit`: each session's effective input is priced by the actual
  model it ran on (`claude-opus-4-8`, `gpt-5`, …; falls back to the provider rate when unrecorded)
  instead of a flat Sonnet rate. The report leads with a money headline — "~$X effective input ·
  ~$Y/mo · biggest avoidable: <layer> ~$Z/mo". New `cost_model.resolve_model_price`
  (`CRAM_MODEL_PRICES` to override); new keys `total_eff_cost`, `monthly_cost`,
  `cost_per_measured_session`, `cost_measured_sessions`, `model_mix`, `biggest_avoidable`.
- **Finding → verify loop**: every finding carries a structured `verify` `{command, expect}` —
  how to prove the fix worked — welding the profiler to the referee (`cram rig` for optimizer-style
  fixes, `cram audit --compare` / re-audit for config fixes). In text, markdown, HTML, and JSON.
- **Trend over time**: a sparkline + session-weighted recent-vs-prior direction
  (`worsening`/`improving`/`flat`) on the primary metric (reads-before-edit). New `trend` key.
- **Versioned, stable JSON contract**: `schema_version` (now **`audit/3`**) on the aggregate dict
  and the `--session`/`--layer`/`--compare` wrappers; stable top-level key set (null, not omitted);
  a `bases` measured/estimated map. Documented in `docs/AUDIT_JSON.md`.
- **Findings sample-size gating**: each finding carries `sample_n` + `preliminary` (fewer than 3
  measured sessions → flagged, not read as a verdict off N=1).
- `cram rig` UX: live per-cell progress on stderr; self-describing `meta` auto-embedded in
  `--json`; `--clean-cache`, `--keep-workdirs`, `--model`, `--no-baseline` flags; a
  required-baseline guard; one clone retry on transient failure.

### Fixed
- `cram rig` no longer silently miscounts failures: each cell records a precise status (`ran` /
  `no_transcript` / `unavailable` / `setup_error` / `run_error` / `oracle_timeout`). A runner
  crash, clone error, or oracle **timeout** is excluded from the success rate instead of read as
  a task loss; a passing run with no measurable transcript counts toward success but its tokens
  are not averaged in as a phantom 0.

### Changed
- Repositioned the context layer as the **reference optimizer** `cram rig` benchmarks, not a
  product claim: grouped `cram --help` ("Profile & referee" / "Setup" / "Optional — context
  layer"); `recommend.py` labels it experimental; "token savings" softened to "model the
  cache-write cost" in `cram benchmark`, the MCP docstring, and the README.

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

[Unreleased]: https://github.com/vishbay/cram-ai/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/vishbay/cram-ai/compare/v0.8.3...v0.9.0
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
