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

### Issue #2786 · sonnet · N=3 per arm
_Pending — running. Baseline is the profiler #2786 rows above (~14× `core.py`
re-reads, ~28 reads before first edit); the after arm with cram context is
in progress and will be filled in here._

---

## Honesty checklist
- [x] Pinned: `repo@8a1b1a3`, issue URLs, model=sonnet, cram 0.5.1
- [x] Framed as a case study, not validation/proof
- [x] Single-session numbers are descriptive, not causal
- [x] N=3 (#3571); spread shown, weak/truncated runs disclosed not hidden
- [x] Before/after compared at equal task success (light "fix lands + tests pass" oracle)
- [x] Upfront cost of the context layer shown, not hidden
- [ ] Caveat: one repo, small N — does not generalize
