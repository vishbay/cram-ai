# cram-ai

[![PyPI](https://img.shields.io/pypi/v/cram-ai?color=%237b2fff&style=flat-square)](https://pypi.org/project/cram-ai/)
[![Python](https://img.shields.io/pypi/pyversions/cram-ai?color=%2300f5d4&style=flat-square)](https://pypi.org/project/cram-ai/)
[![License](https://img.shields.io/github/license/vishbay/cram-ai?color=%23f72585&style=flat-square)](LICENSE)

cram audits your AI coding-agent sessions — **Claude Code, Cursor, Codex** — straight from
the transcripts already on your disk, and shows where the tokens went: how much spend lands
before the first edit, which files agents re-read session after session, oversized tool
results carried turn after turn, retry loops. Every number is labeled **measured** or
**estimated**, and deterministic findings pair evidence with a concrete fix. No setup, no
instrumentation, and the audit is fully local — nothing leaves your machine.

One of those fixes ships with cram: a **context layer** that pre-loads focused project, task,
symbol, decision, and gotcha context so your tool arrives oriented instead of re-discovering
the codebase each session. Apply a fix — cram's or any other — then re-audit to verify it
actually helped. (Unlike the audit, the context layer calls your configured model provider:
`cram init` and `get_context` send code excerpts to it to build that context.)

The context layer works with **Claude Code, Cursor, Windsurf, Zed, Codex, GitHub Copilot,
Gemini CLI**, and any tool that reads a file on startup. Custom tool targets are supported
via config.

---

## When cram helps (and when it doesn't)

The benefit to agentic coding is real but uneven. The honest split:

**Where cram clearly helps:**

- **Knowledge an agent cannot discover by searching.** An agent can grep code, but it can't
  grep *"we chose cursor pagination over offset because offset breaks under concurrent writes"*
  or *"users.email is nullable in prod despite the schema."* `DECISIONS.md`/`GOTCHAS.md` are
  external tacit memory — this value is window-independent and the most durable.
- **Cross-session re-discovery — which `cram audit` measures.** Agents start every session
  amnesiac and re-derive the same orientation (grep → read → read). Front-loading it is a
  direct hit on an *empirically measured* waste bucket, not an assumed one.
- **Long-running / autonomous loops**, where context bloat compounds and dominates cost.
- **Multi-agent / parallel fan-out**, where a shared briefing saves N agents from each
  independently re-deriving the architecture.

**Where the benefit is weaker — be honest about it:**

- **Single agent, single session, familiar code.** Modern agents navigate well on their own;
  per-task excerpts can be redundant with what they'd find anyway — sometimes net-neutral.
- **Staleness is a real liability, not just a caveat.** A context layer that drifts from the
  code is *worse* than none. The [staleness score](#context-health) mitigates this, but it's a
  maintenance tax — and the auto-generated files (`ARCHITECTURE`/`SYMBOLS`) are exactly the
  ones an agent could regenerate itself.
- **Large context windows erode the excerpt-trimming value.** When a model ingests the whole
  repo cheaply, "focused excerpts to save tokens" matters less. What *doesn't* erode is the
  tacit knowledge above.

**The framing:** cram's durable edge is **memory and judgment that lives outside the code**
(decisions, gotchas) plus **audit-driven waste reduction** — not "helping the agent read
files," which agents already do well. The more *autonomous, long-running, multi-session, or
multi-agent* your coding gets, the more cram helps; the more it's *single-shot interactive
editing in familiar code*, the closer to break-even.

---

## Install

```bash
# Standard — includes MCP server for Claude Code / Cursor / Windsurf / Zed
pip install 'cram-ai[mcp]'

# With TUI dashboard
pip install 'cram-ai[mcp,tui]'

# With additional model providers (OpenAI, Gemini, Bedrock, Ollama …)
pip install 'cram-ai[mcp,multi-provider]'
```

---

## Quick start

**Audit first — zero setup.** cram reads the session transcripts your tools already write:

```bash
cd your-repo
cram audit             # where do tokens go? evidence + findings
cram audit --report    # the same, as shareable markdown
```

**Then, if the findings point at repeated re-discovery,** set up the context layer:

```bash
cd your-repo

# 1. One-time setup
cram init
#   → scans your repo: ARCHITECTURE.md via a cheap model, SYMBOLS.md deterministically
#   → scaffolds DECISIONS.md + GOTCHAS.md for you to fill in
#   → installs a git post-commit hook to keep context fresh

# 2. Fill in the manual files — this is where cram's real value lives
vim .ai-context/DECISIONS.md   # architectural invariants, naming conventions
vim .ai-context/GOTCHAS.md     # non-obvious traps that burned your team

# Or mine your git history for decisions automatically:
cram decisions --mine

# 3. Commit so teammates get the context layer too
git add .ai-context/ CLAUDE.md
git commit -m "chore: init cram-ai context layer"
```

Then wire up your tool of choice — [MCP](#mcp-delivery) or [file-based delivery](#file-based-delivery).

---

## Session audit

`cram audit` measures how much of each session is spent on navigation vs. actual work:

```bash
cram audit           # last 30 days for this repo
cram audit --days 7  # tighter window
cram audit --all     # all projects
cram audit --json    # structured output for dashboards / scripts
cram audit --report [FILE]           # shareable markdown report
cram audit --compare PATH_A PATH_B   # side-by-side A/B of two checkouts
cram audit --reingest                # ignore the cache, re-parse everything
```

`--compare` prints both checkouts' metrics with deltas — built for before/after
experiments around *any* change: a context layer, prompt changes, tool-output
truncation, model routing. Alternate tasks between the checkouts and compare.

Output includes:

```
Avg reads before first edit:    8.2  ← primary metric
Avg edits/session:              3.1
Avg read-to-edit ratio:         2.6×  ~ normal
Cache engagement:               18/24 sessions read from cache

Pre-edit context share (measured):
  Edit sessions:                16/24  (8 no-edit sessions excluded)
  Pre-edit context share:       31%  of 1,580,000 eff. input tokens
  Pre-edit spend/session:       ~41,200 eff. tokens  (~$0.1236, anthropic pricing)

Ratio guide: < 2× good · 2–5× normal · > 5× context isn't landing
```

Every number is labeled **measured** or **estimated**. The **pre-edit context share**
is measured and deliberately *descriptive*: it is the input-side token spend (input +
cache-weighted traffic, provider multipliers applied) of all requests before the
session's first edit, divided by the session's total input-side spend — summed across
sessions, so long sessions weigh more. Pre-edit reading is not automatically waste
(sometimes inspecting unfamiliar code *is* the work), which is why the share is just
a description; the *avoidable* patterns — repeated cross-session reads, oversized
carried results, retry loops — are called out separately as findings. Conservatisms:
no-edit sessions are excluded (reviews, Q&A, abandoned runs — reading may have been
the job), sessions without token usage in their transcripts are excluded and counted as
unmeasured — in practice this makes the measured headline **Claude Code/Codex-only**:
Cursor transcripts carry no token usage, so Cursor sessions always show as unmeasured
(a `—` rather than a number) —
output-token spend is not included, and with fewer than 5 measured sessions the share
is marked preliminary. The older per-file cost lines remain labeled as estimates.

The cache-engagement line is the silent-failure check: a session that wrote cache but
never read it back paid the 1.25× write price for nothing — a signal that prompt
caching isn't engaging (prefix instability, sub-floor prefix, or a misconfigured proxy).

The report also measures **context bloat** — usually the largest waste bucket: average
context re-read per request, the share of read-cost in the final third of turns
(33% = flat; higher means the context is growing), the *carried cost* of oversized
tool results (a big result entering at turn k is re-read by every later turn), and
redundant same-file reads. Thresholds: `CRAM_AUDIT_BIG_RESULT_BYTES` (default 20000).

**Retry loops** are reported when present: failed tool calls per session (`is_error`
tool results — each usually means a retry follows) and same-file re-edits per session
(a couple is normal; sustained churn means the agent is thrashing).

**Top repeated files** lists the files agents read most, with how many sessions read
them — cross-session repetition is the concrete evidence for what belongs in a repo
briefing. (File paths come from Claude/Cursor tool calls; Codex shell reads don't
carry structured paths and aren't attributed.)

**Findings** close the loop from numbers to action: deterministic rules (no LLM
judging) that fire only above conservative thresholds, each pairing evidence with a
fix — repeated cross-session reads → put them in a repo briefing; oversized carried
tool results → truncate output; cache written but never read → fix caching config;
sustained failed commands → capture a gotcha; heavy context growth → trim results or
tune compaction. Findings appear in the report and under `findings` in `--json`.

Dollar attribution is **provider-pluggable**: set `CRAM_PROVIDER` to `anthropic`
(default), `openai`, `gemini`, or `local` (zero-dollar — the cost is latency). Prices
are representative defaults; override per field with `CRAM_PRICE_INPUT_PER_MTOK`,
`CRAM_CACHE_WRITE_MULT`, `CRAM_CACHE_READ_MULT` for billing-grade numbers. Pricing and
thresholds are applied at query time, so changing them never requires a re-parse.

Parsed transcripts are cached in a local SQLite **event store**
(`~/.local/share/cram-ai/audit.db`, or `$XDG_DATA_HOME/cram-ai/audit.db`; override the
path with `CRAM_AUDIT_DB`, `:memory:` accepted). Transcripts are re-parsed only when
they change, so repeat audits are fast. The store is a cache, never source data: it
rebuilds itself automatically when the schema or parser changes, and if it can't be
opened at all the audit still runs (uncached) with a note on stderr. `--reingest`
(alias `--no-cache`) forces a full re-parse.

Re-audit after applying any fix to verify it actually moved the numbers.

### Per-session waterfall — `cram audit --session`

Where the aggregate audit shows averages across sessions, `--session` drills into
one session and shows a **per-request token waterfall** — exactly which turn blew
up the context and why:

```bash
cram audit --session <id>        # id = a session-id prefix, or a transcript path
cram audit --session <id> --json
```

```
Session ba4ba1d1 · myrepo · 2026-06-14 14:49 · 24 requests

  Turn     Input     CacheR     CacheW   Output    Context         Δ  Note
  --------------------------------------------------------------------------
     7         2     31,578         12      693     31,592    +1,647  Read audit.py; Read events.py
     8         2     38,254     29,842    3,370     68,098   +29,713 ⚠  ⚠ events.py → 12,307 tok result (49 KB)
   ...
  Carried waste (oversized results re-read every later turn):
    cram/events.py: 12,307 tok × 18 later turns = 221,526 carried tok
    → est. carried read cost: ~$0.1070 (anthropic pricing)
  Redundant re-reads:   2× cram/events.py
  Failed tool calls (retry loops): 1
```

Each row carries the token classes (input / cache-read / cache-write / output),
the context delta vs the previous request, and the tool activity that caused a
jump. Below the table it attributes the session's wasted tokens to concrete
causes — oversized results carried by every later turn, redundant re-reads,
failed calls. Use it to turn an aggregate finding ("oversized results") into the
exact turn and file to fix.

---

## Verify an optimizer — `cram rig`

The audit tells you *where* tokens go and recommends a fix. `cram rig` answers the
next question: **did the fix (or a third-party tool) actually help — without
breaking the task?** The metric is **tokens at fixed success**: a tool that drops
tokens by failing the task isn't saving anything, so token cost is only compared
among runs that still pass an oracle.

It verifies any context optimizer two ways — your own cram context layer, or a
third-party tool like [claude-context](https://github.com/zilliztech/claude-context):

**Observational** — A/B your *real* sessions, no setup. Splits sessions by whether
the optimizer was actually used and compares cost:

```bash
cram rig --observe cram              # did cram's context layer help your sessions?
cram rig --observe claude-context    # did claude-context deliver its claimed savings?
```

> Observational is a *signal*, not proof — the two groups aren't matched on task
> difficulty (it says so in the output). For proof, use the controlled mode.

**Controlled** — run a fixed task corpus through each provider, score success with
an oracle (the task's own tests), and compare tokens at equal success:

```bash
cram rig <corpus.json> --providers baseline,cram,claude-context
cram rig <corpus.json> --dry-run     # resolve the grid + availability, run nothing
```

A corpus is a JSON list of tasks, each with a prompt, a fixture directory, and a
`check` command that defines "done" (exit 0 = success). See
[`examples/rig/corpus.example.json`](examples/rig/corpus.example.json) for two
runnable, spec-driven fixtures.

| Provider | What it is |
|---|---|
| `baseline` | no context tool — the control arm |
| `cram` | cram's own context layer (`cram task`) |
| `claude-context` | semantic code-search MCP server (third-party) |
| `headroom`, `context-mode` | stubs — report what's needed to wire them |

Live runs drive Claude Code headless (`claude -p`) and **reuse your existing
Claude Code login — no API key required**. Each provider's `availability()`
reports exactly what's missing (e.g. claude-context needs an embedding key + a
vector DB), so the grid never silently benchmarks nothing.

cram stays **advisory**: it recommends and verifies optimizers but never mutates
your pipeline — you wire the fix, cram confirms it landed.

---

## The context layer

cram maintains five files in `.ai-context/`. Two are auto-generated. Two are manual. One is
generated per task.

```
your-repo/
└── .ai-context/
    ├── ARCHITECTURE.md   ← auto  · repo structure, tech stack, key files
    ├── SYMBOLS.md        ← auto  · every source file mapped to its public identifiers
    ├── DECISIONS.md      ← manual · architectural commitments your team has made
    ├── GOTCHAS.md        ← manual · non-obvious traps, foot-guns, things that burn people
    └── CURRENT_TASK.md   ← per-task · focused excerpts for the current work
```

**Auto-generated** (`ARCHITECTURE.md`, `SYMBOLS.md`):
- Generated by `cram init`, refreshed automatically via the git post-commit hook after each commit
- `SYMBOLS.md` is the candidate pool for file selection — every source file mapped to its public
  identifiers via regex. Deterministic, no LLM cost, byte-stable across runs. The LLM picks
  from this index rather than scanning raw source files, so it stays grounded in real symbols.
- `ARCHITECTURE.md` uses a cheap model (Haiku / Gemini Flash / GPT-4o Mini)

**Manual** (`DECISIONS.md`, `GOTCHAS.md`):
- Scaffolded by `cram init` — you fill them in over time
- `DECISIONS.md`: "we use X", "never do Y", naming conventions, non-obvious invariants
- `GOTCHAS.md`: silent side effects, middleware gaps, surprising nulls — things grep can't tell you
- Append entries with `cram decide "..."`, `cram gotcha "..."`, or mine git history with `cram decisions --mine`
- Agents can propose decisions mid-session using the `propose_decision` MCP tool

**Output protection by default:**
Every file cram generates for file-based targets includes command output protection rules.
Your agent won't accidentally read 80 KB of build output mid-session:

- Unknown commands are byte-capped to 6,000 bytes by default (`COMMAND 2>&1 | head -c 6000`)
- File inspection uses `head`/`tail`, never raw `cat`
- Large outputs go to a temp file you inspect in ranges
- Configurable in `.ai-context/config.toml` under `[output]` (`byte_cap`, `line_cap`, `temp_file`)

---

**Per-task** (`CURRENT_TASK.md`):
When you call `get_context("task description")`, cram runs a four-stage pipeline:

1. **Symbol index** — reads `SYMBOLS.md` (every public identifier, by file) as the candidate pool
2. **LLM selection** — a cheap model picks relevant files from the symbol index and names key identifiers
3. **Excerpt extraction** — pulls identifier-focused excerpts from selected files (not full files)
4. **Context write** — assembles into `CURRENT_TASK.md` (~800–1,500 tokens)

> **What this replaces:** the agent spending 3–5 tool calls grep-ing and reading files to orient
> itself at the start of every session. With cram the context arrives in one call and includes
> knowledge — decisions, gotchas — that the agent can't discover by searching.

> **Switch tasks mid-session — no restart.** `get_context("next task")` re-runs the pipeline
> inline and loads focused context for the new task. You do **not** need to open the TUI, run
> `cram task`, or start a fresh session per task — keep one session open and call `get_context`
> each time the task changes. (The TUI/`cram task` path exists for the file-based delivery flow
> and to populate the startup banner; it is not required when using MCP.)

---

## MCP delivery

If your tool supports MCP (Claude Code, Cursor, Windsurf, Zed, Codex CLI), wire up the cram MCP
server once and the tool can call context tools directly.

**One-time server config** (same format for all MCP clients):

```json
{
  "mcpServers": {
    "cram-ai": {
      "command": "cram",
      "args": ["mcp", "--repo", "/absolute/path/to/your-repo"]
    }
  }
}
```

| Client | Config file |
|---|---|
| Claude Code | `.mcp.json` at repo root |
| Cursor | `.cursor/mcp.json` or Cursor Settings → MCP |
| Windsurf | Windsurf MCP settings |
| Zed | Zed assistant settings → context servers |
| Codex CLI | `~/.codex/config.yaml` → `mcpServers` |

**Available MCP tools:**

| Tool | What it returns | When to call it |
|---|---|---|
| `get_context(task='')` | Runs symbol lookup → file selection → excerpt extraction. No-arg: returns last CURRENT_TASK.md without re-running the LLM. Prepends a staleness warning when context is stale or critical. | First thing every session — **and again whenever the task changes mid-session (no restart needed)** |
| `get_architecture()` | ARCHITECTURE.md — repo structure, tech stack, key files | Orientation in an unfamiliar area |
| `get_symbols(query='')` | SYMBOLS.md — source files mapped to public identifiers, optionally filtered | Finding where a function is defined |
| `get_decisions()` | DECISIONS.md — architectural commitments | Before making a design choice |
| `get_gotchas()` | GOTCHAS.md — non-obvious traps and foot-guns | Before touching an unfamiliar area |
| `propose_decision(text, reason='', alternatives='')` | Appends a `[PENDING]` entry to DECISIONS.md for owner review. Logs to `suggestions.jsonl` for `cram ui`. | When you make an architectural choice worth recording |
| `add_file(path, identifiers='')` | Appends a file's excerpts to CURRENT_TASK.md | When a mid-task discovery needs new context |
| `get_health()` | Deterministic markdown: staleness score (0–10), commits since last sync, per-file token counts vs soft budgets. Safe to cache. | Before trusting loaded context on a long-running branch |
| `run_benchmark()` | Token-savings summary for the repo — full repo vs cram context, with the per-session cost breakdown. | To quantify what the context layer is saving |
| `get_task_history(limit=20)` | Recent `cram task` invocations as a markdown list, newest first. | Recalling what was worked on recently |

---

## Agent write-back

Agents can propose decisions directly to DECISIONS.md without leaving their session:

```
propose_decision(
  text="use JWT over session cookies",
  reason="stateless — scales horizontally without a session store",
  alternatives="redis session store, cookie-based sessions"
)
```

This appends a `[PENDING]` entry. The entry is surfaced in `cram ui` where you approve or
discard it with a single keystroke. Nothing is written to the canonical record without owner
review.

---

## File-based delivery

For tools that don't support MCP, run `cram task "..." --target <tool>` before your session.
cram writes focused context into the file the tool auto-loads at startup.

```bash
# GitHub Copilot
cram task "add pagination to the users endpoint" --target copilot
# → writes to .github/cram-task.md (one-time: add an include line to copilot-instructions.md)

# Cursor (no-MCP fallback)
cram task "add pagination to the users endpoint" --target cursor
# → writes to .cursor/rules/cram-task.md

# Gemini CLI
cram task "refactor the auth module" --target gemini
# → writes to GEMINI.md (marker-based, preserves your content outside cram's section)

# All targets at once
cram task "add pagination to the users endpoint" --target all
```

| Target | File written |
|---|---|
| `cursor` | `.cursor/rules/cram-task.md` |
| `windsurf` | `.windsurf/rules/cram-task.md` |
| `copilot` | `.github/cram-task.md` |
| `codex` | `AGENTS.md` (repo root) |
| `gemini` | `GEMINI.md` (repo root, marker-based upsert) |
| `claude` | `CLAUDE.md` (escape hatch for Claude Code; prefer MCP) |
| `all` | All detected targets |

**Custom targets** — for tools with non-standard instruction files (e.g., an enterprise IDE with
`ACME.md`), add a section to `.ai-context/config.toml`:

```toml
[targets.acme]
file      = "ACME.md"
indicator = "acme.config.json"   # optional: file/dir that signals tool is active
upsert    = true                 # optional: use cram markers to preserve your content
```

Then `cram task "..." --target acme` works like any built-in target.

**Enterprise gateways** — for internal model proxies that use SSO tokens instead of API keys,
add to `~/.config/cram-ai/settings.json`:

```json
{
  "proxy": {
    "base_url": "https://gateway.corp/v1",
    "headers": { "X-Corp-Token": "your-sso-token" }
  }
}
```

cram passes `base_url` as `api_base`, `headers` as `extra_headers`, and uses a dummy
`api_key` so litellm doesn't abort. No API key needed.

---

## Decisions mining

DECISIONS.md is the hardest file to keep current — it depends entirely on human discipline.
`cram decisions --mine` automates the first draft:

```bash
cram decisions --mine          # scan last 90 days of git history
cram decisions --mine --days 180
```

It scans git log for decision-shaped language, runs a cheap model to extract structured
entries, then walks you through them one at a time (`git add -p` style):

```
── Draft 1/3 ──────────────────────────────────────
  Decision: use JWT over session cookies
  Reason:   reduces server-side state, scales horizontally
  [a]ccept  [s]kip  [e]dit  [q]uit >
```

Accepted entries are appended to DECISIONS.md immediately.

---

## TUI dashboard

`cram ui` opens a Textual terminal dashboard:

```bash
pip install 'cram-ai[tui]'
cram ui
```

Six tabs:

- **Audit** (default) — the session-audit numbers for the last 30 days: pre-edit
  context share (measured), no-edit session split, reads before first edit,
  read-to-edit ratio with band, cache engagement (sessions that wrote cache but
  never read it back), context-bloat metrics (context per request, read-cost tail
  share, carried cost of oversized tool results, redundant re-reads), top repeated
  files, and a weekly trend. The dashboard opens on the number, not the knobs.
- **Decisions** — pending agent proposals at top, accepted history below.
  Press `a` to approve the focused entry, `d` to delete it. Badge shows pending count.
- **Sessions** — recent Claude Code sessions with reads, edits, read-to-edit ratio, and
  the **task that was active** during each session. Task names are inferred from
  `TASK_HISTORY.jsonl` by matching each session's timestamp against task time windows.
  Ratio > 5× is flagged — context isn't landing for those sessions.
- **Health** — staleness score, commits since last sync, per-file token budgets.
- **History** — recent `cram task` invocations with timestamps. The **active session
  task** is shown at the top in green (it lives in `session.json` and hasn't been
  archived yet). This is a recall aid ("what was I working on last Tuesday?"), not a
  project management tool.
- **Actions** — run `cram sync`, `cram task`, `cram benchmark`, or `cram doctor` from
  inside the TUI. An animated progress bar shows while a command is running.

Each tab refreshes its data when you switch to it. Auto-refreshes every 30 seconds.
`r` forces a full refresh, `q` quits.

---

## Daily workflow

```bash
# Before a session — MCP path (Claude Code / Cursor / Windsurf / Zed)
# Nothing to run. The agent calls get_context() itself.
# To switch tasks mid-session, just ask the agent to load the next task —
# it calls get_context("next task") inline. No new session, no TUI.

# Before a session — file-based delivery (Copilot / no-MCP tools)
cram task "fix the rate limiter" --target copilot

# Log a decision while working
cram decide "use cursor-based pagination, not offset — offset breaks under concurrent writes"

# Mine git history for past decisions
cram decisions --mine

# Log a gotcha you just found
cram gotcha "the users.email column is nullable in prod despite NOT NULL in schema.prisma"

# Extend grace period if you commit mid-task (prevents context reset)
cram continue

# Check context freshness
cram status

# Review pending agent proposals + session efficiency
cram ui
```

After every commit the git post-commit hook runs `cram sync` automatically to refresh
`ARCHITECTURE.md` and `SYMBOLS.md`. A session grace period prevents sync from firing while
you're mid-task.

---

## Context health

cram tracks how stale your context is with a **0–10 staleness score** derived from git — the
number of commits on HEAD since ARCHITECTURE.md was last regenerated. No new state files; the
score is always correct after a teammate pull.

The post-commit hook writes ARCHITECTURE.md to disk but does not commit it. The health check
detects this correctly: if the file has uncommitted changes (i.e., it was rewritten by `cram sync`
after the last commit), the score is reported as 0 — not stale.

| Score | Band | Meaning |
|---|---|---|
| 0–2 | `fresh` | Up to date — work freely |
| 3–5 | `acceptable` | Drifting slightly — fine to continue |
| 6–7 | `stale` | Update before next session |
| 8–10 | `critical` | Sync now — context may mislead |

The score falls back to an mtime check when git is unavailable. The critical threshold defaults to
10 commits and is tunable via `CRAM_STALE_CRITICAL_COMMITS`.

**Where health surfaces:**
- `cram status` — per-file age table + health line with score, band, and commit count
- `cram ui` → Health tab — staleness score + per-file token budgets + active task slots
- `get_health()` MCP tool — deterministic markdown block the agent can call before trusting context
- `get_context()` — prepends a one-line staleness warning when band is `stale` or `critical`
- `cram sync` — warns to stderr after regenerating if any frozen file exceeds its soft token budget

**Soft token budgets** (warnings only — nothing is ever truncated):

| File | Default budget | Override |
|---|---|---|
| `ARCHITECTURE.md` | 3,000 tok | `CRAM_BUDGET_ARCHITECTURE` |
| `DECISIONS.md` | 1,500 tok | `CRAM_BUDGET_DECISIONS` |
| `GOTCHAS.md` | 800 tok | `CRAM_BUDGET_GOTCHAS` |
| `CURRENT_TASK.md` | 2,000 tok | `CRAM_BUDGET_TASK` |
| `SYMBOLS.md` | no budget | scales with repo size |

---

## Team and concurrency

cram is designed for **one developer, one repo checkout**. The five context files in
`.ai-context/` are meant to be committed and shared via git (that's how teammates get
them); session/runtime state inside `.ai-context/` is gitignored by an ignore file
`cram init` writes. There is no live coordination between checkouts.

| Scenario | Works? |
|---|---|
| One developer, one agent | Yes — designed for this |
| One developer, parallel agents (same session) | Yes — each `get_context()` call gets its own slot under `.ai-context/tasks/` |
| Multiple developers, separate checkouts | Independent — no sharing or coordination |
| Multiple developers wanting shared context | Not supported |

The slot system protects against concurrent MCP calls within a single server process. It is
**not** a collaboration feature — there is no shared state, sync, or conflict resolution
across different checkouts or machines.

---

## CLI reference

| Command | What it does |
|---|---|
| `cram init [path] [--team]` | One-time setup — scans repo, generates context files, installs git hook |
| `cram mcp [--repo PATH]` | Start MCP server (stdio). Wire into your tool's settings once; clients launch it automatically. |
| `cram task "..." [--target T]` | Run context pipeline, write CURRENT_TASK.md, optionally inject into tool's auto-loaded file |
| `cram add <file> [file ...] [--replace]` | Append (or replace) files in the current session's CURRENT_TASK.md context |
| `cram decisions [--mine] [--days N]` | Show DECISIONS.md, or mine git history for decision-shaped commits and review interactively |
| `cram sync [path]` | Refresh ARCHITECTURE.md + SYMBOLS.md from current repo state. If the session grace period has expired, archives the current task to `TASK_HISTORY.jsonl` and resets the task context in all target files (your instructions are untouched — only the cram-managed task section is cleared). |
| `cram decide "..." [path]` | Append a dated architectural decision to DECISIONS.md |
| `cram gotcha "..." [path]` | Append a non-obvious trap to GOTCHAS.md |
| `cram continue [path]` | Extend grace period — keep context across a mid-task commit |
| `cram status [path]` | Show each context file with age, line count, and token budget status |
| `cram audit [--days N] [--all] [--json] [--report [FILE]] [--compare A B] [--session ID] [--reingest]` | Audit Claude Code / Cursor / Codex transcripts: pre-edit context share, context bloat, retry loops, findings; `--report` emits shareable markdown; `--session ID` shows a per-request token waterfall for one session |
| `cram rig <corpus.json> [--providers ...] [--dry-run]` / `cram rig --observe <optimizer> [--days N]` | Verify a context optimizer — controlled (tokens at fixed success over a corpus) or observational (A/B an optimizer over your real sessions) |
| `cram ui [path]` | TUI dashboard — pending decisions, session efficiency, context health (requires `cram-ai[tui]`) |
| `cram benchmark [path]` | Show token and cost comparison across delivery strategies |
| `cram doctor [path]` | Health check — models, hooks, git, context files |
| `cram hook install\|uninstall` | Manage the git post-commit hook manually |

---

## Model providers

cram uses a cheap model for its maintenance calls (generating ARCHITECTURE.md, selecting files,
extracting excerpts). Set `AICONTEXT_MODEL` to any provider:

```bash
# Inside Claude Code — zero config, uses session credentials
cram init

# Anthropic API key
export ANTHROPIC_API_KEY=sk-...
export AICONTEXT_MODEL=anthropic/claude-haiku-4-5

# Google Gemini
export GEMINI_API_KEY=...
export AICONTEXT_MODEL=gemini/gemini-2.0-flash

# OpenAI
export OPENAI_API_KEY=sk-...
export AICONTEXT_MODEL=openai/gpt-4o-mini

# Ollama (local, free, no key needed)
export AICONTEXT_MODEL=ollama/mistral
cram init
```

Also supports: AWS Bedrock, GCP Vertex AI, Azure OpenAI, custom LiteLLM proxies with
`proxy.base_url` + `proxy.headers` (install `cram-ai[multi-provider]`).

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `AICONTEXT_MODEL` | auto-detected | Model for context tasks — bare alias (`haiku`) or `provider/model` |
| `ANTHROPIC_API_KEY` | — | Optional inside Claude Code (uses session credentials) |
| `AICONTEXT_MAX_FILES` | `5` | Max files included in CURRENT_TASK.md per task |
| `AICONTEXT_MAX_LINES` | `300` | Max lines per file when extracting excerpts |
| `AICONTEXT_TASKS_PER_SESSION` | `4` | Assumed tasks per cache window (used by `cram benchmark`) |
| `CRAM_TASK_GRACE_SECONDS` | `600` | Seconds after `cram task` before a commit resets context |
| `CRAM_STALE_CRITICAL_COMMITS` | `10` | Commits since last sync that maps to staleness score 10 (critical). Lower = more sensitive. |
| `CRAM_BUDGET_ARCHITECTURE` | `3000` | Soft token budget for ARCHITECTURE.md — warns in `cram status` and `cram sync` when exceeded |
| `CRAM_BUDGET_DECISIONS` | `1500` | Soft token budget for DECISIONS.md |
| `CRAM_BUDGET_GOTCHAS` | `800` | Soft token budget for GOTCHAS.md |
| `CRAM_BUDGET_TASK` | `2000` | Soft token budget for CURRENT_TASK.md |
| `CRAM_AUDIT_DB` | `~/.local/share/cram-ai/audit.db` | Audit event-store cache location (`:memory:` accepted) |
| `CRAM_PROVIDER` | `anthropic` | Pricing table for audit dollar attribution: `anthropic` / `openai` / `gemini` / `local` / `bedrock` / `vertex_ai` / `azure` |
| `CRAM_PRICE_INPUT_PER_MTOK` | per provider | Override base input price ($/1M tokens) for audit cost estimates |
| `CRAM_CACHE_WRITE_MULT` | per provider | Override cache-write multiplier (1.25 on Anthropic) |
| `CRAM_CACHE_READ_MULT` | per provider | Override cache-read multiplier (0.10 on Anthropic) |
| `CRAM_AUDIT_TOK_PER_FILE` | `2500` | Assumed tokens per orientation file read in `cram audit` cost modeling |
| `CRAM_AUDIT_BIG_RESULT_BYTES` | `20000` | Serialized size above which a tool result counts as oversized in `cram audit` |

---

## 💰 Where session tokens go (illustrative model)

The numbers below are a model, not a measurement — run `cram audit` for your real ones.
Without context pre-loading, an agent typically spends the first exchanges of a session
re-discovering the codebase — reading files, running searches, building orientation from scratch.

**What a typical session consumes (no cram):**

| Phase | What happens | Tokens |
|---|---|---|
| Session start | System prompt + tool definitions + rules files | 3–8K |
| Orientation | `find` / `grep` / `read` calls to discover relevant files cold | 20–60K |
| Active work | Conversation, edits, test runs | 20–50K |
| Output | Code written, explanations | 5–15K |
| **Per task total** | | **50–130K** |

Some of the orientation phase is necessary (inspecting unfamiliar code is sometimes the
work); some of it is re-discovery — the same files re-read session after session. cram
doesn't guess which is which: `cram audit` measures your actual pre-edit context share and
the findings point at the avoidable patterns (repeated cross-session reads, oversized
carried results, retry loops), each with a suggested fix.

**What the context layer targets:**

One of those fixes is cram's own context layer: a `get_context()` call returning ~1–2K
tokens of targeted excerpts instead of cold re-reads. It targets repeated re-discovery
only — it does **not** replace the agent's productive reads (edits, tests, active work).
Whether it helps in your repo is measurable: apply it, re-audit, and compare.

Run `cram benchmark` for a full token and cost breakdown across all three delivery strategies
and model tiers. Run `cram audit` to measure your actual read-to-edit ratio.

---

<details>
<summary>💸 <strong>Claude Code users: cache-write bonus</strong></summary>

This section is specific to Claude Code + Anthropic. The context layer is useful for any tool,
but Claude's prompt caching gives MCP delivery an additional cost advantage.

Anthropic's prompt cache has a 5-minute TTL. Content in the conversation **prefix** gets
cache-written at 1.25× the base input price on every new session and every TTL expiry.
Content that doesn't touch the prefix — like MCP tool results — isn't.

**File-based delivery vs MCP:**

| | File-based delivery (`--target claude`) | MCP (`get_context()`) |
|---|---|---|
| Where context lands | CLAUDE.md → front of prefix | Conversation tail (tool result) |
| Cache writes per session | N × task context tokens | 1 × tool definitions (~1–2K tokens) |
| Per-task context cost | 1.25× write per task change | 0.1× read after first session write |
| 10K-token context, 4 tasks | ~$0.09–0.15 in cache writes | ~$0.01 in cache writes |

The larger your context and the more tasks per session, the more the MCP path saves.

Run `cram benchmark` to model the exact numbers for your repo.

**The floor check:** the frozen prefix must exceed 2,048 tokens (Sonnet 4.6) or 4,096 tokens
(Opus 4.8 / Haiku 4.5) to cache at all. `cram benchmark` flags this if your context files are
below the threshold.

</details>

---

## Contributing

Issues and PRs welcome. Every PR runs the test suite on Python 3.10–3.13 via GitHub
Actions; please keep it green. Audit-metric changes deserve special care — legacy
metrics are pinned by a parity suite (`tests/test_audit_parity.py`), and new metrics
should be additive and labeled measured or estimated.

**Running tests:**

```bash
pip install -e '.[mcp]' pytest
pytest
```

No API key required — all model calls are mocked.

---

## License

Apache-2.0 — see [LICENSE](LICENSE).

cram is open source and local-first. **The local single-user audit workflow —
transcript ingestion, the event store, the audit CLI/TUI, findings, and markdown
reports — will remain open source.** We may later offer paid hosted or team features
(shared dashboards, org-wide aggregation, scheduled reports, enterprise support)
built around the open core.
