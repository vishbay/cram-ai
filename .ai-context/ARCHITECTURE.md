# Architecture

## Overview
cram-ai is a profiler and referee for AI coding-agent tokens. It audits agent sessions from real transcripts to show where tokens go (pre-edit context share, context bloat, retry loops), provides evidence-backed fixes, and verifies whether any optimization actually reduces token use at fixed task success. It also initializes project configuration, maintains Claude-specific settings, and provides a context layer for zero-key integration workflows.

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
- `audit.py` - Measures orientation tax (reads vs. edits) from Claude Code transcripts; dispatches to audit subsystem
- `audit_events.py` - Parse Claude/Cursor/Codex transcripts into typed events and per-session metadata
- `audit_findings.py` - Analyze audit data, derive context-mode findings and recommendations
- `audit_report.py` - Render audit findings, waste attribution, and timeline reports
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
Test suite for the package functionality

## Key Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Python package metadata and build configuration |
| `setup.py` | setuptools shim for pip compatibility |
| `requirements.txt` | Python dependencies |
| `CASE_STUDY_RUNBOOK.md` | Reproducible runbook for GitHub-issues case study; demonstrates profiler (where tokens go) and referee (whether fixes work) |
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
- **Referee**: Verify whether any optimization (cram's or third-party) reduces tokens at fixed task success; controlled or observational A/B over real sessions
- Context extraction from arbitrary codebases with identifier-focused excerpts
- Project initialization with templated configuration and ARCHITECTURE.md generation
- Context synchronization across backends with automated git hooks
- **Output protection by default**: Command outputs byte-capped to prevent token waste
- Repository status monitoring (file freshness, sync state, token budgets)
- Claude integration via MCP server without API key management
- Task slot namespacing for concurrent agent invocations
- **Architectural decision tracking**: Record and mine decisions from git history
- **Gotcha documentation**: Maintain repository-specific non-obvious traps and workarounds
- **Optimization recommendations**: Typed waste-class detectors with recommended fixes
- **Carried-output loop** (Option A, advisory-tightening): Detect oversized tool results, auto-tighten `[output]` caps in config, verify with `cram rig --observe`
- **Agentic testing framework (cram rig)**: Verify optimizer correctness via task corpus, fixture execution, and oracle validation
- **Interactive TUI dashboard** (5 tabs): Decisions, Sessions, Health, History, Actions; auto-refresh every 30s
- **Structure-hash deduplication**: Skip ARCHITECTURE.md LLM regeneration when repo structure is unchanged
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
- `cram audit [--days N] [--all] [--session]` - Measure orientation tax from Claude Code transcripts; per-session breakdown with context-mode detection
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

cram is designed for **one developer, one repo checkout**. Task slot namespacing (`.ai-context/tasks/`) protects against concurrent MCP calls within a single server process, allowing multiple agents to invoke `get_context()` in parallel without collision.

## Dependencies

All Python dependencies specified in `requirements.txt` and `pyproject.toml`. Install with `pip install -e .` or `pip install cram-ai`.

Optional extras:
- `cram[tui]` - Textual dashboard (depends on textual>=0.80)
- `cram[mcp]` - MCP server support (depends on mcp>=1.0.0)
- `cram[multi-provider]` - Multi-provider LLM support (depends on litellm>=1.40.0)

<!-- cram:structure-hash d93da3112f35050ee49235586dc3dd807764499ad77043dbc01d347df16bf2f9 -->
