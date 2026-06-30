# Runbook: GitHub-issues token-waste case study

**Purpose.** Produce an honest, reproducible case study: take a real repo and
two real open issues, run the usual fix path with `claude -p`, and use `cram
audit` to show **where the tokens went** and **which remediation layer** would
address it. Doubles as cram's first demo asset and an end-to-end harness check.

**What this is — and isn't.**
- It IS a *descriptive* case study. The audit measures where spend went; it
  needs **no success oracle**, so it dodges the "did it fix the issue correctly"
  problem entirely.
- It is NOT proof cram saves tokens in general. n=2 issues on one repo is an
  anecdote, not a benchmark. Label it as a case study everywhere.

---

## 1. Selection criteria

**Repo** (pick one, pin the commit SHA):
- Real and reasonably large (enough that orientation actually costs — small
  repos show no waste and prove nothing).
- Permissive license (so the write-up can quote code/paths).
- Has a test suite (lets you optionally confirm "task still completes" later).
- Not one the model has obviously memorized verbatim (avoid trivializing).

**Issues** (pick two):
- Well-scoped and self-contained — a localized bug fix or small feature, not an
  architecture change.
- Touch code an agent must *find* (so orientation/bloat can show up).
- Open at the pinned SHA. Record issue numbers + URLs.

**Task mix matters.** cram is expected to help when the agent has real
orientation work, and to be neutral or worse when the prompt already gives away
the exact file and test.

| Issue / task type | Share of agentic coding work | Expected cram impact |
|---|---:|---:|
| Exact file + exact test known | 20–30% | Low: −5% to +10% |
| Natural issue, likely area known but not exact fix | 35–45% | Strong: 20–50% savings |
| Large unfamiliar repo / vague bug | 10–20% | Very strong: 30–60%+ savings |
| Tiny obvious one-file task | 10–15% | Neutral or negative |
| Long-running multi-step agent task | 10–20% | Mixed: helps orientation; output/context bloat still needs separate fixes |
| Repeated sessions / multi-agent same repo | 5–15% | High cumulative value |

For the strongest before/after test, prefer a natural issue prompt: enough detail
to define the bug and success condition, but not the exact implementation file
or line unless a normal developer would already know it.

Record: `repo@SHA`, the two issue URLs, model id, cram version, date.

---

## 2. Setup

```bash
git clone <repo> case-study && cd case-study
git checkout <SHA>                 # pin for reproducibility
cram init                          # optional — only if testing the context layer arm
```
- Pin the model (e.g. a fixed Claude model id) — do not let it float.
- Confirm your agent writes transcripts to disk (cram audits those).
- Run each issue in a clean session so transcripts don't bleed together.

---

## 3. Run the fix path

For each issue, drive the *usual* path with headless Claude:

```bash
claude -p "Fix this issue: <paste issue title + body>. Make the change and run the tests."
```
- Let it flail naturally — orientation reads, retries, big tool outputs are the
  signal, not noise. A failed attempt is still a valid transcript (it surfaces
  retry loops).
- Do **N runs per issue** (≥3) — LLM nondeterminism means a single run's token
  count is meaningless. You'll report spread, not a point value.

---

## 4. Measure (the descriptive core)

```bash
cram audit --report case_study_report.md     # shareable markdown
cram audit --json > case_study.json          # structured, for the write-up
cram audit --session <ID>                    # per-request waterfall for one session
```

Report these (each labeled **measured**/**estimated** as cram already does):
- Pre-edit context share (where spend lands before the first edit)
- Context-bloat: context/request, tail share, **carried cost** of oversized
  tool results, redundant re-reads
- Retry loops: failed tool calls, same-file re-edit churn
- Top repeated files (cross-session re-reads)
- **Findings**: the evidence→fix rules that fired, and *which layer* each points
  at (briefing / output cap / caching config / gotcha capture / compaction)

This is the headline of the case study: *"a real agent fixing a real issue
spent ~$X before its first edit / carried a Y KB result for Z turns — and here's
the fix cram names."*

---

## 5. Optional: before/after (the cure, not just the diagnosis)

To show a remediation *reduces* the waste, run a second arm with the fix applied
(e.g. the cram context layer, or a tightened output cap), same issues, same N:

```bash
cram audit --compare baseline/ remediated/   # side-by-side with deltas
# or, observational over real sessions:
cram rig --observe claude-context --days 30
```
- Here a **light oracle** returns — but only "the task still completes" (tests
  still pass / the diff still lands), NOT "optimal quality." Report it alongside
  the token delta so a saving can't be a saving-by-doing-less.
- Report the delta in the *descriptive* metric (carried cost / pre-edit share),
  with variance across the N runs.

---

## 6. Honesty checklist (before publishing)

- [ ] Everything pinned: `repo@SHA`, issue URLs, model id, cram version.
- [ ] Framed as a **case study**, never "validation" or "proof."
- [ ] Single-run audit numbers presented as *descriptive*, not causal.
- [ ] N≥3 runs; report spread, not a lone number.
- [ ] measured/estimated labels preserved from cram's output.
- [ ] If a before/after is shown, the "task still completes" oracle is stated.
- [ ] Caveats section: one repo, two issues, doesn't generalize.

---

## 7. What to publish

A short narrative + the numbers + the findings + the caveats. The compelling
artifact is concrete: *"Watch a real agent fix `repo#1234` — here's the spend
before its first edit, the result it carried for 18 turns, and the three fixes
cram named."* That demonstrates the **profiler** (where spend goes) and, if you
include §5, the **referee** (did the fix actually help) — both with current
functionality, no overclaim.
