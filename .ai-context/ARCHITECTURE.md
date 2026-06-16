# Architecture

## Overview
cram-ai is a profiler and referee for AI coding-agent tokens. It audits agent sessions from real transcripts to show where tokens go (pre-edit context share, context bloat, retry loops), provides evidence-backed fixes, drills into concrete contributors for each waste class, and verifies whether any optimization actually reduces token use at fixed task success. It also emits shareable Markdown and standalone HTML audit reports for review. It initializes project configuration, maintains Claude-specific settings, and provides an optional repo-local context layer for zero-key integration workflows when audits show repeated re-discovery.

## Directory Structure

### `cram/`
Core Python package containing main functionality:
- `cli.py` - Single entry point dispatching `cram <subcommand>` to appropriate modules
- `context_dir.py` - Context directory resolution (`.ai-context/` preferred, `.cram-ai-context/` legacy fallback)
- `find_context.py` - Scans and extracts relevant context from codebases; populates CURRENT_TASK.md and archives to TASK_HISTORY.jsonl
- `init.py` - Initializes project configuration and CLAUDE.md files; triggers hook installation
- `sync_context.py` - Synchronizes context with external systems and backends; error-resilient ARCHITECTURE.md updates with structure-hash deduplication
- `status.py` - Shows .ai-context/ file freshness and repo sync state
- `hooks.py` - Git post-commit and commit-msg hook installer for automated sync and decision recording
- `mcp_server.py` - MCP server for Claude Code integration with task slot namespacing and decision proposals
- `session.py` - Session state management: task archiving, slot tracking, grace period handling
- `targets.py` - Target-specific output generation with byte-cap command protection rules
- `symbols.py` - Public identifier extraction for SYMBOLS.md
- `audit.py` - Measures orientation tax (reads vs. edits) from Claude/Cursor/Codex transcripts; dispatches to audit subsystem; provides per-session, per-layer drilldowns, Markdown reports, and standalone HTML reports
- `audit_events.py` - Parse Claude/Cursor/Codex transcripts into typed events and per-session metadata
- `audit_findings.py` - Analyze audit data, derive context-mode findings and recommendations
- `audit_report.py` - Render audit findings, waste attribution, and timeline reports
- `audit_report_html.py` - Render self-contained HTML audit reports with token waterfall, findings, leaderboard, waste layers, and key metrics
- `audit_store.py` - Persist and incrementally manage audit database (SQLite)
- `decisions.py` - Mine architectural decisions from git history; show DECISIONS.md
- `decide.py` - Decision recording and management; append to DECISIONS.md
- `gotcha.py` - Non-obvious trap documentation; append to GOTCHAS.md
- `recommend.py` - Optimization recommendations registry; typed waste-class detectors and fixes
- `rig.py` - Testing framework for agentic optimizer verification (task corpus, fixtures, providers, runners, oracle)
- `ui.py` - Textual TUI dashboard for decisions, sessions, health, task history, and command execution
- `benchmark.py` - Cache-write cost modeling and token savings benchmarking
- `cost_model.py` - Multi-provider pricing model; orientation cost computation with provider selection
- `utils.py` - Shared utilities: model discovery, LLM routing (Claude CLI / Ollama / OpenAI-compat / Gemini), Ollama timeout handling
- `__init__.py` - Package initialization

### `templates/` and `examples/`
- `templates/` - Template files for project initialization
- `examples/rig/` - Example task corpus and pre-made fixtures for cram rig testing

### `tests/`
Test suite for the package functionality, including audit aggregate/report coverage, HTML report rendering, and layer drilldown tests.

## Key Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Python package metadata and build configuration |
| `setup.py` | setuptools shim for pip compatibility |
| `requirements.txt` | Python dependencies |
| `CASE_STUDY.md` | Real-world case study demonstrating profiler (token measurement) and referee (before/after verification) on GitHub issues |
| `CASE_STUDY_RUNBOOK.md` | Reproducible runbook for case study; demonstrates profiler and referee methodology |
| `PROJECT_CONTEXT.md` | Project goals and context documentation |
| `PLAN_CARRIED_OUTPUT.md` | Carried-output optimization loop design (advisory-tightening phase) |

## Tech Stack

- **Language**: Python 3.10+
- **Configuration Format**: JSON (Claude settings), TOML (cram config)
- **Package Management**: pip / setuptools
- **Testing**: pytest
- **TUI**: Textual (optional, cram[tui])
- **Database**: SQLite (audit store, optional)

## Primary Features

- **Profiler**: Audit AI agent sessions from transcripts; measure pre-edit context share, context bloat, retry loops, and oversized tool results
- **Layer drilldown**: Expand one waste class into ranked concrete contributors with `cram audit --layer`; supports orientation, repeated, redundant, carried, retries, and churn
- **Shareable reports**: Emit Markdown reports with `cram audit --report` and self-contained visual HTML reports with `cram audit --report-html`
- **Referee**: Verify whether any optimization (cram's or third-party) reduces tokens at fixed task success; controlled or observational A/B over real sessions
- Optional context extraction from arbitrary codebases with identifier-focused excerpts
- Project initialization with templated configuration and ARCHITECTURE.md generation
- Context synchronization across backends with automated git hooks
- **Output protection by default**: Command outputs byte-capped to prevent token waste
- Repository status monitoring (file freshness, sync state, token budgets)
- Claude and Codex CLI integration via existing subscription login; direct API providers require keys
- Task slot namespacing for concurrent agent invocations
- **Architectural decision tracking**: Record and mine decisions from git history
- **Gotcha documentation**: Maintain repository-specific non-obvious traps and workarounds
- **Optimization recommendations**: Typed waste-class detectors with recommended fixes
- **Carried-output loop** (Option A, advisory-tightening): Detect oversized tool results, auto-tighten `[output]` caps in config, verify with `cram rig --observe`
- **Agentic testing framework (cram rig)**: Verify optimizer correctness via task corpus, fixture execution, and oracle validation
- **Interactive TUI dashboard** (5 tabs): Decisions, Sessions, Health, History, Actions; auto-refresh every 30s
- **Structure-hash deduplication**: Skip ARCHITECTURE.md LLM regeneration when repo structure is unchanged
- **Staleness bands**: Map freshness scores to fresh, acceptable, stale, and critical; ARCHITECTURE.md staleness only advances on structure-changing commits
- Task history archiving (per-session task invocations with timestamps)
- Multi-provider LLM support (Claude CLI / Ollama / OpenAI-compat / Gemini) with configurable timeouts

## Entry Points

### Commands

CLI commands dispatched through unified `cram` entry point:
- `cram init [path]` - Bootstrap project configuration and install git hooks
- `cram task "<description>"` - Populate CURRENT_TASK.md before coding session
- `cram add <files>` - Add files to current task context
- `cram continue [path]` - Extend grace period for current task
- `cram sync [path]` - Update ARCHITECTURE.md and SYMBOLS.md after a commit. If session grace period expired, archives current task to TASK_HISTORY.jsonl and resets task context in target files
- `cram status [path]` - Show .ai-context/ freshness and output protection status
- `cram decide "<statement>"` - Append architectural decision to DECISIONS.md
- `cram decisions [--mine] [--days N]` - Show or mine decisions from git history
- `cram gotcha "<trap>"` - Append non-obvious trap to GOTCHAS.md
- `cram audit [--days N] [--all] [--session] [--layer NAME]` - Measure orientation tax from Claude/Cursor/Codex transcripts; per-session breakdown and per-layer contributor drilldown
- `cram audit --report [FILE]` - Emit a shareable Markdown audit report
- `cram audit --report-html [FILE] [--no-open]` - Emit a standalone HTML audit report; defaults to `cram-audit-report.html` and opens in a browser when interactive
- `cram benchmark [--days N]` - Show token savings vs full-repo auto-indexing
- `cram rig <corpus-path> [--observe]` - Run task fixtures against agentic optimizer; verify correctness and cost
- `cram doctor [path]` - Check setup: models, hooks, git, context files
- `cram hook install|uninstall [path]` - Manage git post-commit and commit-msg hooks
- `cram mcp [--repo PATH]` - Start MCP server (stdio) for Claude Code / agents
- `cram ui [path]` - Launch TUI dashboard (requires cram-ai[tui])

## Context Directory

`.ai-context/` (canonical) is created at repo root by `cram init`.

| File | Purpose | Tracked |
|------|---------|---------|
| `ARCHITECTURE.md` | Repo structure, tech stack, key files | Yes |
| `SYMBOLS.md` | Public identifiers per source file | Yes |
| `DECISIONS.md` | Architectural invariants and decisions | Yes |
| `GOTCHAS.md` | Non-obvious traps and workarounds | Yes |
| `CURRENT_TASK.md` | Active task context (per-session) | No |
| `config.toml` | Output protection, task defaults, custom targets | Yes |
| `tasks/` | Per-task slot files for concurrent agents | No |
| `TASK_HISTORY.jsonl` | Per-session task archive | Yes |
| `usage.jsonl` | Usage log (task, tokens, timestamp) | No |
| `suggestions.jsonl` | Proposed decisions from agents | No |

## Concurrency Model

cram supports concurrent agents working in one repo checkout. Each `get_context("task")` or `cram task` call writes an isolated slot file under `.ai-context/tasks/<task>.md`, so simultaneous agents do not overwrite each other's active task context.

Shared context files (`ARCHITECTURE.md`, `DECISIONS.md`, `GOTCHAS.md`, `SYMBOLS.md`) are read-mostly and committed through normal version control. cram does not currently provide hosted multi-developer team dashboards or cross-developer audit rollups; it runs on a single developer's machine.

## Dependencies

All Python dependencies specified in `requirements.txt` and `pyproject.toml`. Install with `pip install -e .` or `pip install cram-ai`.

Optional extras:
- `cram[tui]` - Textual dashboard (depends on textual>=0.80)
- `cram[mcp]` - MCP server support (depends on mcp>=1.0.0)
- `cram[multi-provider]` - Multi-provider LLM support (depends on litellm>=1.40.0)

<!-- cram:structure-hash 2df131444328c8a1a57d0864ea997813745bcd704ad06bac0eea4007d2cec1e1 -->
