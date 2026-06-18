# Case study: token-waste profile + context-layer before/after

A real, reproducible run of cram against a third-party repo: profile where an
agent's tokens go fixing real issues, then test whether pre-loading cram's
context layer reduces that waste **at equal task success**.

> **This is a case study, not proof.** Single repo, two issues, small N,
> descriptive. It demonstrates the *profiler* (where tokens go) and the
> *referee* (did a fix help), with caveats kept honest. See
> [CASE_STUDY_RUNBOOK.md](CASE_STUDY_RUNBOOK.md) for the method.

## Setup (pinned)
- **Repo:** `pallets/click` @ `8a1b1a33d739be05b7e91251e3c0dde77c5e152f`
- **Issues:** [#3571](https://github.com/pallets/click/issues/3571) (progressbar completion, localized to `_termui_impl.py`) · [#2786](https://github.com/pallets/click/issues/2786) (option-callback override, central `core.py`)
- **Agent:** `claude -p`, model `sonnet`, `--dangerously-skip-permissions` (disposable clone)
- **Tool:** cram-ai 0.5.1
- **Context delivery (after arm):** `cram init` + `cram task --target claude --inject` — cram's own pipeline selected the files from the task description; no hand-curation.

---

## 0.8.1 re-run pilot — profiler validation (baseline, N=3)

A fresh pilot on **2026-06-18** to confirm the profiler pipeline still reproduces end-to-end on
the current release, and to seed a future full re-run.

- **Tool:** cram-ai 0.8.1 · **Runner:** `claude -p` · **Model:** `claude-opus-4-8`
- **Issue:** #3571 (the localized `_termui_impl.py` bug) · **Arm:** baseline only · **N=3**
- Raw per-run metrics: [`examples/case-study/results/3571-baseline-opus4.8-2026-06-18.json`](examples/case-study/results/3571-baseline-opus4.8-2026-06-18.json)

| Run | Requests | Reads before edit | Peak ctx | `_termui_impl.py` reads | Redundant reads |
|---|---:|---:|---:|---:|---:|
| 1 | 16 | 3 | 17,652 | 4 | 3 |
| 2 | 13 | 5 | 18,455 | 3 | 2 |
| 3 | 11 | 4 | 16,891 | 2 | 1 |
| **mean** | **13.3** | **4.0** | **17,666** | **3** | **2.0** |

**Read:** the pipeline reproduces — all three runs landed the fix in `_termui_impl.py` (the exact
file the original profile pointed at), and `cram audit` measured each session. The qualitative
finding holds: a localized bug still drives repeated re-reads of the central file (~3×) with
fast orientation (~4 reads before the first edit).

**Not comparable to the 0.5.1 tables below.** This pilot ran on **`claude-opus-4-8`**, a stronger
model than the original **`sonnet`** runs, so the lower absolute waste (peak ~17.7k vs ~32.5k,
~13 vs ~21 requests) reflects the model, **not** a cram change — it is not a like-for-like
before/after. The before/after (cram-context) arm, issue #2786, and the Codex runner were **not**
re-run in this pilot; the 0.5.1 results below stand as the recorded before/after.

---

## Where cram should help

The expected savings depend on how much orientation the agent must do. These
are rough workflow estimates for AI-assisted coding tasks, not a universal
benchmark:

| Issue / task type | Share of agentic coding work | Expected cram impact |
|---|---:|---:|
| Exact file + exact test known | 20–30% | Low: −5% to +10% |
| Natural issue, likely area known but not exact fix | 35–45% | Strong: 20–50% savings |
| Large unfamiliar repo / vague bug | 10–20% | Very strong: 30–60%+ savings |
| Tiny obvious one-file task | 10–15% | Neutral or negative |
| Long-running multi-step agent task | 10–20% | Mixed: helps orientation; output/context bloat still needs separate fixes |
| Repeated sessions / multi-agent same repo | 5–15% | High cumulative value |

The useful product claim is therefore scoped: cram should help most when a
natural issue description forces the agent to discover repo structure and likely
files. It may be neutral or worse when the prompt already names the exact file,
test, and fix location.

---

## Profiler — where the tokens go

### Issue #3571 · localized bug (`_termui_impl.py`) · N=3 ✓
| Session | Requests | First req | Peak ctx | Re-reads `_termui_impl.py` | First edit |
|---|---|---|---|---|---|
| `313ce78c` | 27 | 18,196 | 35,021 | 4× | turn 5 |
| `43a04fc3` | 20 | 18,172 | 30,257 | 5× | turn 5 |
| `4c5daca8` | 17 | 18,172 | 32,368 | 6× | turn 5 |
| **mean** | **21.3** | **18,180** | **32,549** | **~5×** | **turn 5** |

### Issue #2786 · central-hub bug (`core.py`) · N=2 ✓ (+1 truncated, excluded)
| Session | Requests | First req | Peak ctx | Reads before 1st edit | Re-reads `core.py` |
|---|---|---|---|---|---|
| `6342a339` | 33 | 18,163 | 56,978 | 27 | 13× |
| `04614317` | 37 | 18,163 | 62,117 | 28 | 15× (+2× `parser.py`) |
| **mean** | **35** | **18,163** | **59,548** | **~28** | **~14×** |
| ~~`2d2f2d5d`~~ | ~~7~~ | — | 24,215 | none | 2× — *truncated, no fix (hit limit); excluded* |

**Finding:** the dominant waste is one reproducible pattern — the agent re-reads
the central implementation file repeatedly. It scales with how central the file
is: localized bug → `_termui_impl.py` ~5×; central-hub bug → `core.py` ~14×, with
~28 reads before the first edit and context ballooning ~3.3×.

---

## Before/after — does pre-loading context cut the waste?

### Issue #3571 · sonnet · N=3 per arm · equal success (3/3 fixed both arms)

**BEFORE — no cram (agent re-discovers)**
| Session | Requests | First req | Peak ctx | Re-reads `_termui_impl.py` | First edit |
|---|---|---|---|---|---|
| `313ce78c` | 27 | 18,196 | 35,021 | 4× | turn 5 |
| `43a04fc3` | 20 | 18,172 | 30,257 | 5× | turn 5 |
| `4c5daca8` | 17 | 18,172 | 32,368 | 6× | turn 5 |
| **mean** | **21.3** | **18,180** | **32,549** | **~5×** | **turn 5** |

**AFTER — cram context pre-loaded in `CLAUDE.md`**
| Session | Requests | First req | Peak ctx | Re-reads `_termui_impl.py` | First edit |
|---|---|---|---|---|---|
| `6d741b2f` | 23 | 20,904 | 38,354 | 4× | turn 3 |
| `aac3e8b6` | 6 | 20,904 | 23,560 | 0× | turn 3 |
| `795f547d` | 7 | 20,904 | 25,845 | 2× | turn 3 |
| **mean** | **12.0** | **20,904** | **29,253** | **~2×** | **turn 3** |

**Δ (after vs before)**
| Metric | Before | After | Change |
|---|---|---|---|
| Requests/session | 21.3 | 12.0 | **−44%** |
| Redundant re-reads | ~5× | ~2× | **−60%** |
| Reads before first edit | 4 (turn 5) | 2 (turn 3) | **oriented sooner** |
| Peak context | 32,549 | 29,253 | **−10%** |
| Startup context | 18,180 | 20,904 | **+2,724** (cost of pre-loading) |
| Task success | 3/3 | 3/3 | unchanged |

**Read:** a real, directional win — modest and variable. Robust across all 3 runs:
first edit moves turn 5→3 and turns drop ~44%. Re-reads ~halved but with spread
(one after-run still 4×, two near-zero). The profiler makes the tradeoff visible:
pre-loading adds ~2.7k startup tokens, so peak only drops ~10% despite turns
nearly halving — that fixed cost amortizes better on larger tasks.

### Issue #2786 · sonnet · N=2 before / N=3 after — equal-ish success (fixes produced)

The valid re-run (after the `--inject` bug fix, 6.7 KB context delivered). This is the
**honest negative**: for a central-hub bug, the context layer did **not** help.

**BEFORE — no cram (N=2 valid; +1 truncated, excluded)**
| Session | Req | First | Peak | Reads before edit | `core.py` re-reads |
|---|---:|---:|---:|---:|---:|
| `6342a339` | 33 | 18,163 | 56,978 | 27 | 13× |
| `04614317` | 37 | 18,163 | 62,117 | 28 | 15× |
| **mean** | **35** | **18,163** | **59,548** | **~28** | **~14×** |

**AFTER — cram context (N=3 valid)**
| Session | Req | First | Peak | Reads before edit | `core.py` re-reads |
|---|---:|---:|---:|---:|---:|
| `c074394e` | 31 | 20,137 | 67,737 | ~23 | 15× |
| `7b850fb3` | 32 | 20,109 | 62,896 | ~19 | 12× |
| `12b8045f` | 29 | 20,137 | 65,926 | ~19 | 13× |
| **mean** | **30.7** | **20,128** | **65,520** | **~20** | **~13×** |

**Δ:** requests −12%, reads-before-edit ~28→~20, but **`core.py` re-reads flat (~14×→~13×)**
and **peak context +10% (worse)**. Why: the fix spans click's 3k-line `core.py`; pre-loaded
*excerpts* don't substitute for reading the hub, so the agent re-read it just as much and the
pre-loaded context only added to the total. Cram oriented slightly faster but did not touch
the dominant waste. **Net neutral-to-negative.**

---

## Cross-runner check (Codex) — N=1 per cell, directional only

Same fixtures, run through Codex CLI instead of `claude -p`. Codex reads are shell-based, so
there is no `core.py` re-read attribution; compare orientation (reads before edit) and total
context, plus — where token usage was captured — effective tokens and request count.

| Cell | Reads before edit (base→cram) | Peak ctx (base→cram) | Orientation verdict |
|---|---|---|---|
| #3571 localized | 6 → **8** | 50,785 → **56,481** | no benefit |
| #2786 explicit | 33 → **39** | 150,990 → 128,030 | no benefit |
| #2786 natural | 26 → 28 | 146,227 → 138,722 | no benefit (flat) |
| pilot natural | 31 → **38** | 166,499 → 164,627 | no benefit |

On the **orientation metric (reads before edit)** Codex never improved — every cell is flat or
slightly worse. Total context was usually lower, but on its own that did not convert into fewer
reads.

### One exception, on a *different* metric: #2786 natural prompt (N=1)

The #2786 *natural-prompt* cell is the single case where total convergence — not orientation —
moved. The prompt described the shared-option/callback bug but did **not** name `core.py` or
the regression test; the hidden oracle still checked the focused test with `PYTHONPATH=src`.
Measured by **effective tokens and request count** (rather than reads-before-edit):

| Arm | Success | Effective tokens | Requests | Peak ctx |
|---|---:|---:|---:|---:|
| baseline | 1/1 | 2,484,265 | 42 | 146,227 |
| cram context in `AGENTS.md` | 1/1 | 1,304,527 | 24 | 138,722 |
| **Δ** | unchanged | **−47.5%** | **−43%** | **−5%** |

**Read (with caveats):** the two metrics *disagree* on this one run. Reads-before-edit barely
moved (26→28, the row above), yet the cram arm converged in far fewer requests and effective
tokens. So this is a *convergence* signal, not an *orientation* signal, and it is **N=1, one
prompt, one runner** — directional only, **not** robust, and **not** corroborated by the
orientation metric the rest of this study leads with. Over-specified Codex prompts that named
the exact file/test also passed, but cram was slightly more expensive there (extra context
overhead outweighed navigation savings).

---

## What the evidence says

Across the before/after cells (two runners, two issues, prompt variants), **exactly one is a
clean orientation win** (claude `-p` #3571, a localized bug whose file cram could name). #2786
(central hub) is neutral-to-negative on claude. On Codex, **no cell improved orientation (reads
before edit)**; a single natural-prompt cell showed a large effective-token / request reduction
(−47.5% / −43%), but at **N=1**, unreplicated, and not reflected in the orientation metric. So
the **auto-orientation / excerpt** value of the context layer is **not supported** as a robust
result by these data — it helps mainly when the target file is localized and nameable, and even
then only on one runner here; the lone Codex token reduction is suggestive but stands alone.

**Important scope limit:** every cell exercised the *auto-generated* half
(`ARCHITECTURE`/`SYMBOLS`/excerpts) with `DECISIONS.md`/`GOTCHAS.md` **empty**. The
human-curated tacit-knowledge half (facts an agent cannot grep) was **not tested**, so it is
neither supported nor refuted here. The honest conclusion: lead with the **audit + referee**;
treat the context layer as optional, and as unproven for auto-orientation specifically.

---

## Honesty checklist
- [x] Pinned: `repo@8a1b1a3`, issue URLs, model=sonnet, cram 0.5.1
- [x] Framed as a case study, not validation/proof
- [x] Single-session numbers are descriptive, not causal
- [x] N=3 (#3571); spread shown, weak/truncated runs disclosed not hidden
- [x] Codex cross-runner cells are N=1, directional only; the one token reduction is unreplicated
- [x] Metric disagreement disclosed (Codex #2786-natural: flat orientation, lower convergence)
- [x] Before/after compared at equal task success (light "fix lands + tests pass" oracle)
- [x] Upfront cost of the context layer shown, not hidden
- [ ] Caveat: one repo, small N — does not generalize
