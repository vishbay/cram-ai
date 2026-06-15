# Architecture

## Overview
cram-ai is a utility for discovering, initializing, and synchronizing context information for AI coding assistants. It scans codebases to extract relevant context, manages project configuration, and maintains Claude-specific settings for zero-key integration workflows.

## Directory Structure

### `cram/`
Core Python package containing main functionality:
- `cli.py` - Single entry point dispatching `cram <subcommand>` to appropriate modules
- `context_dir.py` - Context directory resolution (`.ai-context/` preferred, `.cram-ai-context/` legacy fallback)
- `find_context.py` - Scans and extracts relevant context from codebases; populates CURRENT_TASK.md and archives to TASK_HISTORY.jsonl
- `init.py` - Initializes project configuration and CLAUDE.md files; triggers hook installation
- `sync_context.py` - Synchronizes context with external systems and backends; error-resilient ARCHITECTURE.md updates
- `status.py` - Shows .ai-context/ file freshness and repo sync state
- `hooks.py` - Git post-commit and commit-msg hook installer for automated sync and decision recording
- `mcp_server.py` - MCP server for Claude Code integration with task slot namespacing, usage logging, and decision proposals
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

### `.claude/`
Claude-specific configuration and settings:
- `settings.local.json` - Local settings for Claude integration and behavior
- `hooks/` - SessionStart hook for auto-injecting context

### `examples/`
Example corpora and test fixtures for cram rig:
- `rig/` - cram rig example framework
  - `corpus.example.json` - Example task corpus
  - `fixtures/` - Pre-made testing fixtures (with TASK.md, code, tests)

### `templates/`
Template files for project initialization

### `tests/`
Test suite for the package functionality

## Key Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Python package metadata and build configuration |
| `setup.py` | setuptools shim for pip compatibility |
| `requirements.txt` | Python dependencies |
| `PROJECT_CONTEXT.md` | Project goals and context documentation |
| `.gitignore` | Git exclusion rules |

## Tech Stack

- **Language**: Python 3.10+
- **Configuration Format**: JSON (Claude settings), TOML (cram config)
- **Package Management**: pip / setuptools
- **Testing**: pytest
- **TUI**: Textual (optional, cram[tui])
- **Database**: SQLite (audit store, optional)

## Primary Features

- Context extraction from arbitrary codebases with identifier-focused excerpts
- Project initialization with templated configuration and ARCHITECTURE.md generation
- Context synchronization across backends with automated git hooks
- **Output protection by default**: Command outputs byte-capped to prevent token waste
- Repository status monitoring (file freshness, sync state, token budgets)
- Claude integration via MCP server without API key management
- Task slot namespacing for concurrent agent invocations
- **Architectural decision tracking**: Record and mine decisions from git history
- **Gotcha documentation**: Maintain repository-specific non-obvious traps and workarounds
- **Orientation tax audit**: Measure reads-vs-edits efficiency from transcripts; per-session context-mode analysis
- **Optimization recommendations**: Typed waste-class detectors with recommended fixes
- **Agentic testing framework (cram rig)**: Verify optimizer correctness via task corpus, fixture execution, and oracle validation
- **Interactive TUI dashboard** (5 tabs): Decisions, Sessions, Health, History, Actions; auto-refresh every 30s
- Usage logging (task, tokens, timestamp) in JSONL format
- Task history archiving (per-session task invocations with timestamps)
- Multi-provider LLM support (Claude CLI / Ollama / OpenAI-compat / Gemini) with configurable timeouts

## Entry Points

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

| File | Purpose | Managed by |
|------|---------|-----------|
| `ARCHITECTURE.md` | Repo structure, tech stack, key files | `cram sync` |
| `SYMBOLS.md` | Public identifiers per source file | `cram init` / `cram sync` |
| `DECISIONS.md` | Architectural invariants and decisions | Manual + `cram decide` |
| `GOTCHAS.md` | Non-obvious traps and workarounds | Manual + `cram gotcha` |
| `CURRENT_TASK.md` | Active task context (per-session) | `cram task` |
| `config.toml` | Output protection, task defaults, custom targets | Manual |
| `tasks/` | Per-task slot files for concurrent agents | MCP server |
| `TASK_HISTORY.jsonl` | Per-session task archive | `cram task` / MCP server |
| `usage.jsonl` | Usage log (task, tokens, timestamp) | MCP server |
| `suggestions.jsonl` | Proposed decisions from agents | MCP server |
| `.gitignore` | Excludes CURRENT_TASK.md (per-developer) | `cram init` |

## Concurrency Model

cram is designed for **one developer, one repo checkout**. Task slot namespacing (`.ai-context/tasks/`) protects against concurrent MCP calls within a single server process, allowing multiple agents to invoke `get_context()` in parallel without collision. This is not a collaboration feature—each developer maintains independent context files in their local checkout.

## Staleness Detection

The post-commit hook writes ARCHITECTURE.md to disk but does not commit it. The health check detects this correctly: if the file has uncommitted changes (rewritten by `cram sync` after the last commit), staleness score is reported as 0—not stale.

## Git Hooks

cram installs two hooks to automate context synchronization:

- **post-commit**: Runs `cram sync` to update ARCHITECTURE.md and SYMBOLS.md on every commit
- **commit-msg**: Processes decision-language prompts in commit messages; enables recording architectural decisions inline during commits

Install via `cram init` or `cram hook install`.

## Dependencies

All Python dependencies specified in `requirements.txt` and `pyproject.toml`. Install with `pip install -e .` or `pip install cram-ai`.

Optional extras:
- `cram[tui]` - Textual dashboard (depends on textual>=0.80)
- `cram[mcp]` - MCP server support (depends on mcp>=1.0.0)
- `cram[multi-provider]` - Multi-provider LLM support (depends on litellm>=1.40.0)