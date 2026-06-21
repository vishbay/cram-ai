# cram-ai

[![PyPI](https://img.shields.io/pypi/v/cram-ai?color=%237b2fff&style=flat-square)](https://pypi.org/project/cram-ai/)
[![Python](https://img.shields.io/pypi/pyversions/cram-ai?color=%2300f5d4&style=flat-square)](https://pypi.org/project/cram-ai/)
[![License](https://img.shields.io/github/license/vishbay/cram-ai?color=%23f72585&style=flat-square)](LICENSE)
[![profiler: no API key](https://img.shields.io/badge/profiler-no%20API%20key-00f5d4?style=flat-square)](#try-it-in-10-seconds--no-api-key)

**The profiler and referee for AI coding-agent tokens.**

cram tells you where Claude Code, Cursor, and Codex sessions spend tokens, points at
avoidable waste, and verifies whether an optimization actually helped at equal task
success. It is a profiler first and a referee second: measure the run, name the waste,
then prove whether any proposed fix helped without lowering task success.

Most token tools promise savings. cram asks the useful engineering question:

> Did this reduce token spend without making the agent worse?

It is local-first, transcript-based, and honest about what is measured versus estimated.

## Try it in 10 seconds — no API key

The profiler reads the agent transcripts already on your disk. No signup, no API key, no config:

```bash
pip install cram-ai
cd your-repo
cram audit                 # where did this repo's agent sessions spend tokens?
cram audit --report-html   # same, as a shareable HTML dashboard
```

`cram audit` and every variant (`--report`, `--report-html`, `--session`, `--compare`, `--json`)
are 100% local and deterministic — they never call a model. Only the optional context layer
(`cram task`) uses an LLM.

[`cram audit --report-html`](#html-report) renders the whole audit as one self-contained file:

![cram audit HTML report](docs/img/report-walkthrough.gif)

---

## Why cram exists

AI coding agents do not only spend tokens writing code. They spend a surprising amount of
context on:

- re-discovering the same repo structure every session
- reading the same central files repeatedly before the first edit
- carrying oversized tool outputs through later turns
- retrying failed shell commands or broken test invocations
- stuffing stale or excessive context into long agent loops

cram gives that waste a profile.

| What you want to know | cram command |
|---|---|
| Where did this session's tokens go? | `cram audit --session <id>` |
| Which sessions are wasting orientation tokens? | `cram audit` |
| Which files get re-read across sessions? | `cram audit --report` |
| Want a shareable visual report? | `cram audit --report-html` |
| Did cram context, claude-context, or another optimizer help? | `cram rig ...` |
| Did a real session use fewer tokens after a change? | `cram audit --compare A B` |
| Is optional repo context stale or too large? | `cram status` |

---

## How cram is different

General LLM observability tools show traces, latency, request cost, and app-level quality
signals. cram is narrower: it profiles coding-agent work loops from local transcripts and
explains why an agent spent tokens before making useful progress.

It speaks in developer-native waste classes:

- startup context
- orientation before first edit
- repeated file reads
- oversized tool output carried forward
- retry loops and failed commands
- same-file edit churn
- cache blind spots
- optimizer-on vs optimizer-off

The goal is not only "what did this cost?" It is "why did the agent spend that much, what
would reduce it, and did that fix preserve task success?"

---

## What it does

**1. Profiles real agent transcripts**

`cram audit` reads the transcripts already on your disk and reports orientation cost,
pre-edit context share, context bloat, repeated reads, oversized carried results, retry
loops, edit churn, and cache engagement.

Supported transcript sources today:

| Tool | Reads/edits | Token usage |
|---|---:|---:|
| Claude Code | yes | measured |
| Codex | yes | measured when token usage is present |
| Cursor | yes | no real usage in the transcript; opt-in `--estimate-cursor` adds a clearly-labelled *estimate* from file sizes |

Cursor transcripts carry no token counts, so by default its token metrics show `—`. Pass
`cram audit --estimate-cursor` (or set `CRAM_CURSOR_ESTIMATE=1`) to estimate read-token cost
from the sizes of files each session read. It is always labelled **estimated**, tunable via
`CRAM_CHARS_PER_TOKEN` (default 4), and never mixed into the measured aggregates.

It reports **real, model-aware dollars** (each session priced by the model it ran on), leads
with the single most expensive avoidable layer, and shows a **trend** (is your waste improving
or worsening week over week?).

**2. Turns numbers into fixes — and proves them**

Findings are deterministic rules, not LLM judgment. Each one carries evidence → fix → a `verify`
recipe (how to prove the fix worked — usually `cram rig`, welding the profiler to the referee):

| Finding | Evidence | Likely fix |
|---|---|---|
| Repeated cross-session reads | same files read in many sessions | put durable context in repo briefing |
| Oversized carried result | large tool output re-read by later turns | cap command output |
| Cache blind session | cache write without cache read | stabilize prefix / fix cache config |
| Retry loop | failed commands or repeated same-file edits | record gotcha / improve task recipe |
| Context growth | late turns keep paying for old output | trim results / tune compaction |

Findings based on very few sessions are tagged **preliminary** — a hint, not a verdict.

**3. Referees optimizers**

`cram rig` compares token usage at fixed success. If an optimization saves tokens by failing
the task, cram does not count that as a win. It can test cram's own context layer, a third-party
optimizer, or no optimizer at all.

**4. Offers an optional repo context layer**

cram can maintain a small `.ai-context/` directory with:

- `ARCHITECTURE.md`: generated repo map
- `SYMBOLS.md`: deterministic file-to-symbol index
- `DECISIONS.md`: architectural decisions humans want agents to remember
- `GOTCHAS.md`: non-obvious traps that grep cannot reveal
- `CURRENT_TASK.md`: focused excerpts for the current task

Agents can load that context through MCP (`get_context()`) or file-based startup rules
(`cram task "..." --target codex`, `--target cursor`, etc.).

This layer is experimental as a token-saving mechanism. Use it when audits show repeated
re-discovery or when you have durable human knowledge to share with agents; verify it with
`cram rig` or `cram audit --compare` before treating it as a win.

---

## Evidence so far

cram ships a reproducible case study against `pallets/click` — full per-session tables in
[CASE_STUDY.md](CASE_STUDY.md), method in [docs/CASE_STUDY_RUNBOOK.md](docs/CASE_STUDY_RUNBOOK.md).

The honest result is **not** "cram context always saves tokens" — it doesn't. The result is that
cram shows **exactly when** an optimization helped, did nothing, or made the run worse. Across the
runs measured, the auto-generated context layer helped on **one** localized Claude bug (−44%
requests at 3/3 success) and was **neutral-to-negative everywhere else** — including a controlled
`cram rig` run where it "saved" tokens but failed more of the task, and so **lost on pass rate**,
which comes first. (The manual `DECISIONS.md` / `GOTCHAS.md` knowledge path is a separate, still
untested claim.)

**Bottom line:** lead with the **audit + referee**; treat the context layer as optional and
unproven for auto-orientation. It's most plausible on unfamiliar repos, natural-language bug
reports where the file isn't obvious, and long-running / repeated / multi-agent loops — and
weakest on tiny edits or prompts that already name the file. The numbers behind all of this,
including the losses, are in [CASE_STUDY.md](CASE_STUDY.md).

---

## Install

```bash
# Standard install with MCP support
pip install 'cram-ai[mcp]'

# Extra provider support through LiteLLM
pip install 'cram-ai[mcp,multi-provider]'
```

Requires Python 3.10+.

---

## Quick start

**1. Profile** — local, no API key. Where do your agent's tokens go?

```bash
cd your-repo
cram audit                  # headline $ + trend + findings (each with a verify recipe)
cram audit --report-html    # shareable dashboard
```

**2. Referee** — did an optimization actually help, at fixed task success?

```bash
cram rig <corpus.json> --providers baseline,cram --dry-run
cram rig <corpus.json> --providers baseline,cram
```

**3. A/B two real checkouts** — before vs after a change:

```bash
cram audit --compare ../before ../after
```

Optional: if audits show repeated re-discovery, try the [context layer](#optional-the-context-layer)
— and verify it earns its keep with step 2.

---

## Session audit

`cram audit` answers: how much work happened before the first edit, how much context was
carried forward, and which patterns look avoidable?

```bash
cram audit                         # last 30 days for this repo
cram audit --days 7                # narrower window
cram audit --all                   # all known projects
cram audit --json                  # machine-readable output
cram audit --report [FILE]         # shareable markdown
cram audit --report-html [FILE]    # standalone HTML report (opens in your browser)
cram audit --layer NAME            # drill into one waste class (orientation, repeated, ...)
cram audit --compare PATH_A PATH_B # compare two repo checkouts side by side
cram audit --reingest              # ignore cache and re-parse
```

It leads with money and direction, then the findings — each with how to **prove** a fix worked:

```text
💸 ~$48.20 effective input over 22 sessions · ~$64.30/mo · biggest avoidable: orientation ~$18.40/mo (measured)
📈 reads→edit over 8 wks  ▁▃▄▆▆▇█  2.1→5.8 (+38% ↑ worsening)

  Avg reads before first edit:    5.8  ← primary metric
  Pre-edit context share:         31% of 1,580,000 eff. input tokens

  Findings (1):
    ⚠ high-orientation   31% of input-side spend lands before the first edit
      → fix:    Front-load repo context instead of re-discovering it each session.
      ✓ verify: cram rig <corpus.json> --providers baseline,cram
                → pre-edit context share drops at fixed task success
```

So the audit answers the whole loop: **what it costs · what to fix first · how to prove the fix
helped · whether it's improving over time.** Dollars are model-aware (each session priced by the
model it ran on); the trend is a session-weighted recent-vs-prior delta.

The audit is deliberately conservative:

- no-edit sessions are excluded from the headline pre-edit share
- sessions without token usage are counted as unmeasured
- output tokens are not included in input-side spend
- dollar attribution is provider-configurable and labeled as an estimate
- file attribution for Codex is limited because shell reads are not structured like Claude/Cursor tool calls

Parsed transcripts are cached in a local SQLite event store at
`~/.local/share/cram-ai/audit.db` unless `CRAM_AUDIT_DB` is set. The store is only a cache;
transcripts remain the source of truth.

---

## Per-session waterfall

For one session, use:

```bash
cram audit --session <id>
cram audit --session <id> --json
```

This shows each request's input, cache-read, cache-write, output, context delta, and tool
activity. It also attributes waste to concrete causes:

```text
Carried waste:
  cram/audit_events.py: 12,307 tok x 18 later turns = 221,526 carried tok

Redundant re-reads: 2x cram/audit_events.py
Failed tool calls: 1
```

Use this when an aggregate finding is too abstract and you need the exact turn or file that
caused the cost.

### Layer drilldown

To expand one waste class into its concrete contributors across sessions:

```bash
cram audit --layer orientation   # sessions with the most reads before first edit
cram audit --layer repeated      # files re-read across sessions (briefing candidates)
cram audit --layer redundant     # files re-read within a session
cram audit --layer carried       # sessions carrying oversized tool output
cram audit --layer retries       # sessions with failed tool calls
cram audit --layer churn         # files re-edited within a session
```

Each lists the worst offenders (files or sessions), so you can go from "context bloat is high"
to the exact files/sessions causing it. Add `--json` for structured output.

---

## Verify optimizers with `cram rig`

`cram rig` is the referee. It compares token usage only among runs that still pass a success
oracle.

![cram rig referee demo](docs/img/referee-demo.gif)

An "optimization" that saves tokens by failing the task is not a win — the referee reports
tokens **at fixed success**, so a cheap-but-broken arm is never credited. (Reproduce the clip
above with `python scripts/demo/referee_demo.py`.)

```bash
cram rig <corpus.json> --providers baseline,cram,claude-context
cram rig <corpus.json> --repeats 3 --tier small   # N runs/cell, one tier
cram rig <corpus.json> --dry-run
cram rig <corpus.json> --runner codex
cram rig --observe cram --days 30
cram rig --leaderboard 'examples/rig/bench/results/*.json'
cram rig --clean-cache                              # drop the shared clone cache
```

A self-contained, tiered benchmark ships in [`examples/rig/bench/`](examples/rig/bench) —
`cram-bench-v1`, small/medium/large tasks that ship red, no external repo to clone. Run it,
commit the result JSON (it carries self-describing `meta` — model, runner, version), and render
a ranked leaderboard with `--leaderboard`. `--repeats N` runs each cell N times so the summary
reports variance.

The referee is honest about its own failure modes: each cell records a precise status, so a
run error, an oracle timeout, or a missing transcript is **excluded** from the success rate
rather than miscounted as a task loss or a 0-token "win". A baseline arm is required (pass
`--no-baseline` to override); `--keep-workdirs` retains per-run dirs for debugging.

Modes:

| Mode | What it means |
|---|---|
| Controlled | fixed corpus, fixture repo, success command, token comparison at equal success |
| Observational | split real sessions by whether the optimizer was used; useful signal, not proof |

Providers:

| Provider | Status |
|---|---|
| `baseline` | no optimizer |
| `cram` | cram context layer |
| `claude-context` | third-party semantic code-search MCP |
| `headroom`, `context-mode` | stubs that report what wiring is missing |

Runners (controlled mode — pick with `--runner`):

| Runner | Agent | Notes |
|---|---|---|
| `claude` (default) | Claude Code headless (`claude -p`) | reuses your Claude login |
| `codex` | Codex noninteractive (`codex exec`) | reuses your Codex login; routes the `cram` provider through `AGENTS.md` |

Both reuse the existing CLI login (no API key). More agent runners can be added behind the same
corpus/oracle interface.

---

## HTML report

```bash
cram audit --report-html        # writes ./cram-audit-report.html and opens it
cram audit --report-html FILE   # write to a specific path
cram audit --report-html --no-open
```

A single self-contained HTML file — inline CSS/JS, no external fonts or fetches — so it
travels: open it locally, attach it to a PR, drop it in Slack. A restrained dark data
dashboard (think Grafana / GitHub Actions logs, not a frosted-glass AI console) with a
light/dark toggle. It renders:

- a KPI **stat strip** and the pre-edit **headline**
- **coverage & confidence** (sessions found / measured / excluded / parse failures, source mix, measured·estimated·count legend)
- the **token waterfall** with per-component `$`/session
- **retry loops** — the same command failing repeatedly
- **cost by waste layer** ($-ranked, with basis)
- the **session leaderboard** with expandable per-turn drilldowns (carried results, failed commands)
- **waste layers** with collapsible top-contributor lists
- **findings** with fix → verify
- **context on/off** A/B when the window contains both
- **key metrics**

It's built from the same `collect_audit()` data as `cram audit --report`, so it makes no
claim the text report doesn't.

![cram audit HTML report — light](docs/img/report-light.png)

---

## Continuous integration (GitHub Action)

cram ships a GitHub Action that turns an audit into a **sticky pull-request comment** and can
**gate** a PR on the rig referee. Because agent transcripts never exist in a stock CI runner,
the action consumes committed/uploaded JSON (produced by `cram audit --json` /
`cram audit --compare A B --json` / `cram rig --json`) rather than live transcripts.

```yaml
# .github/workflows/cram-audit.yml
name: cram audit
on: pull_request
jobs:
  audit:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
      - uses: vishbay/cram-ai@v1
        with:
          mode: compare              # compare | report | rig
          file-a: .cram/baseline.json
          file-b: .cram/candidate.json
```

| Mode | Input | Effect |
|---|---|---|
| `compare` | two audit JSONs | posts a token-waste delta table |
| `report` | one `cram audit --json` | posts the markdown audit report |
| `rig` | baseline + candidate `cram rig --json` | **fails the check** if candidate success drops more than `tolerance` |

`cram init --team` drops a starter `cram-audit.yml` (and `cram-sync.yml`) into
`.github/workflows/`. On fork PRs where commenting is blocked, the action falls back to the job
summary. The action is key-free.

---

## Optional: the context layer

Everything above is the core — the **profiler** and the **referee**. The context layer is an
**optional, experimental** add-on: the reference optimizer `cram rig` benchmarks. It maintains a
small `.ai-context/` directory and delivers it to your agent. Reach for it when an audit shows
repeated re-discovery — but **verify it with `cram rig` / `cram audit --compare` before relying
on it** (in the case study it helped one localized bug and was neutral-to-negative elsewhere).

```text
.ai-context/
  ARCHITECTURE.md  generated repo map      DECISIONS.md  manual architectural decisions
  SYMBOLS.md       deterministic symbols    GOTCHAS.md    manual foot-guns / prod traps
  CURRENT_TASK.md  generated per-task context
```

`cram init` creates it; `cram task "<task>"` (or MCP `get_context("<task>")`) picks relevant
files and writes `CURRENT_TASK.md`; `cram sync` refreshes generated files; `cram status` reports
a 0–10 staleness score. The highest-value files are the **manual** ones (`DECISIONS.md`,
`GOTCHAS.md`) — tacit knowledge not discoverable from syntax; that claim is separate from the
(unproven) auto-orientation claim.

**Delivery** — two paths:

- **MCP** (Claude Code, Cursor, Windsurf, Zed, Codex CLI): one server, then have the agent call
  `get_context("<task>")` at the start of work.
  ```json
  { "mcpServers": { "cram-ai": { "command": "cram",
      "args": ["mcp", "--repo", "/absolute/path/to/your-repo"] } } }
  ```
- **File-based** (startup instruction files): `cram task "<task>" --target claude|codex|cursor|
  windsurf|copilot|gemini|all`. Custom targets go in `.ai-context/config.toml`.

Concurrent agents on one repo are supported (per-task slot files); hosted team dashboards are
not built yet. Full MCP tool list and budgets: `cram mcp` / `cram status`.

---

## Model providers (context layer only)

**`cram audit` is fully local and calls no model.** Only the optional context layer
(`cram init`, `cram sync`, `cram task`, `cram decisions --mine`) uses a context model — and it
can send repo summaries / code excerpts to whatever you configure.

A Claude or Codex **subscription** works via your existing CLI login (no API key). Auto-discovery
also checks Ollama, LM Studio, Bedrock, Vertex, Azure, and direct Anthropic/OpenAI/Gemini keys.
Force a CLI with `CRAM_CONTEXT_PROVIDER=claude|codex`, or set an explicit model in
`~/.config/cram-ai/settings.json`:

```json
{ "context_model": "openai/gpt-4o-mini" }   // or cli/haiku, gemini/…, ollama/…, proxy/custom
```

Enterprise gateways: add a `proxy` block (`base_url` + `headers`) and `"context_model":
"proxy/custom"`.

---

## Commands

| Command | Purpose |
|---|---|
| `cram audit` | Profile agent sessions |
| `cram audit --session <id>` | Inspect one session's token waterfall |
| `cram audit --layer <name>` | Drill into one waste class (orientation/repeated/redundant/carried/retries/churn) |
| `cram audit --report [FILE]` | Write a shareable markdown report |
| `cram audit --report-html [FILE]` | Write a standalone HTML report (opens in your browser) |
| `cram audit --compare A B` | Compare two checkouts |
| `cram init` | Create `.ai-context/` |
| `cram task "..."` | Build task context |
| `cram add <file>` | Add a file to current task context |
| `cram sync` | Refresh generated context |
| `cram continue` | Extend the task grace period before a commit resets context |
| `cram status` | Check freshness and budgets |
| `cram decide "..."` | Add a decision |
| `cram gotcha "..."` | Add a gotcha |
| `cram decisions --mine` | Mine git history for decision candidates |
| `cram benchmark` | Model the cache-write cost of delivering repo context |
| `cram rig ...` | Verify optimizers |
| `cram mcp` | Start the MCP server |
| `cram hook install\|uninstall` | Manage the git post-commit sync hook |
| `cram doctor` | Check setup |

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CRAM_CONTEXT_PROVIDER` | auto | Prefer `codex` or `claude` CLI in auto context-model selection |
| `CRAM_PROVIDER` | `anthropic` | Pricing table for audit dollar attribution |
| `CRAM_AUDIT_DB` | `~/.local/share/cram-ai/audit.db` | Audit cache path; `:memory:` accepted |
| `CRAM_PRICE_INPUT_PER_MTOK` | provider default | Override input price for cost estimates |
| `CRAM_CACHE_WRITE_MULT` | provider default | Override cache-write multiplier |
| `CRAM_CACHE_READ_MULT` | provider default | Override cache-read multiplier |
| `CRAM_AUDIT_TOK_PER_FILE` | `2500` | Tokens assumed per orientation file read in older cost modeling |
| `CRAM_AUDIT_BIG_RESULT_BYTES` | `20000` | Threshold for oversized tool result findings |
| `AICONTEXT_MAX_FILES` | `5` | Max files in task context |
| `AICONTEXT_MAX_LINES` | `300` | Max excerpt lines per file |
| `CRAM_TASK_GRACE_SECONDS` | `600` | Grace period before commit resets task context |
| `CRAM_STALE_CRITICAL_COMMITS` | `10` | Commits that map to critical staleness |
| `CRAM_BUDGET_ARCHITECTURE` | `3000` | Soft token budget |
| `CRAM_BUDGET_DECISIONS` | `1800` | Soft token budget |
| `CRAM_BUDGET_GOTCHAS` | `800` | Soft token budget |
| `CRAM_BUDGET_TASK` | `2000` | Soft token budget |

`AICONTEXT_MODEL` is still supported by the older `call_model()` fallback path, but explicit
context-model routing should use `~/.config/cram-ai/settings.json`.

---

## What cram is not

cram is not an automatic universal token reducer.

It will not magically make every agent run cheaper. It gives you measurements, points at
avoidable patterns, and lets you verify whether cram's optional context layer, a third-party
optimizer, or a config change actually helped.

That is the product boundary: profiler and referee first; context memory is optional and must
be measured.

---

## Contributing

Issues and PRs are welcome.

```bash
pip install -e '.[mcp]' pytest
pytest
```

No API key is required for tests; model calls are mocked.

Audit-metric changes should be additive and clearly labeled as measured or estimated.

---

## License

Apache-2.0. See [LICENSE](LICENSE).

cram is open source and local-first: the whole single-developer workflow — audit, event store,
findings, referee, reports, and the optional context layer — is open source. Concurrent agents
on one repo are supported today; hosted team analytics are not built yet.
