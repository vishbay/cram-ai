# Plan: carried-output closed loop (detect → cap → verify)

**Status:** approved scope — **Option A only** (advisory tightening). Option B
(active runtime truncation) is deferred behind evidence; see the appendix and
DECISION-011.

## Goal
Ship cram's first automatic optimization: reduce the carried cost of oversized
tool results, and **prove the reduction with `cram rig`**. This moves cram from
"diagnoses/guides" toward "diagnoses → acts → verifies" for one waste class.

**Success:** on a repo where the `oversized-results` finding fires, applying
cram's intervention measurably lowers carried cost in a follow-up audit, with no
manual editing.

## The waste class
A large tool result entering at turn _k_ is re-read by every later request in the
session — its cost is carried for the rest of the session. cram already measures
this; the loop tightens the guardrail that prevents it and verifies the effect.

## What already exists (this is connective tissue, not greenfield)
- **Detector** — `audit_findings.py` emits the `oversized-results` finding from
  `sessions_with_big_results`, `big_result_bytes`, `carried_cost_per_session`;
  threshold `CRAM_AUDIT_BIG_RESULT_BYTES` (`cram/audit.py`).
- **Intervention substrate** — `[output]` config + `_byte_cap_block` injection
  (`cram/targets.py`): `byte_cap=6000`, `line_cap=50`, temp-file pattern, already
  written into file-based context.
- **Verification** — `observe_optimizer` / `cram rig --observe` (`cram/rig.py`)
  for observational A/B over real transcripts; plus `cram audit --compare`.

The gap is the **act** step and the wiring that turns three loose pieces into one
command.

## Scope decision: Option A (advisory tightening)
cram is **out-of-band** — it audits transcripts and serves context; it does not
sit in the agent's Bash/Read tool path. Option A tunes the advisory `[output]`
caps from observed waste and verifies the result. Enforcement still depends on
the agent obeying the cap (`| head -c`), so A is honestly "automatic guardrail
tuning + verification," a notch below runtime enforcement — but it ships the loop
with zero session-corruption risk and produces the evidence needed to judge B.

## Work breakdown (Option A)
**Phase 1 — Structured detector.** Extend the finding to emit machine-actionable
offenders, not just prose: per-tool / per-command byte sizes, frequency, carried
cost. New `cram audit --json` field `carried_output_offenders[]`. (Small — the
aggregates already exist.)

**Phase 2 — The `act` step.** `cram optimize carried-output` (name TBD) that:
- reads the offenders,
- writes/tightens `[output]` in `config.toml` (e.g. lowers `byte_cap` for the
  offending command class, adds a targeted rule),
- regenerates the injected guardrail block,
- prints a diff; idempotent and reversible.

**Phase 3 — Auto-verify.** Wire `cram rig --observe` into the flow so `optimize`
ends by reporting the projected carried-cost delta, and `cram audit --compare`
confirms it on real subsequent sessions.

## Verification methodology
- **Projection (immediate):** `rig --observe` replays the optimizer signature over
  existing transcripts → estimated carried-cost reduction.
- **Confirmation (after real use):** `audit --compare` between the pre- and
  post-intervention windows. Metric that must move: **carried cost / session**,
  plus the **tail-share** of context bloat.

## Success & kill criteria
- **Success:** ≥30% carried-cost reduction on offender sessions; no regression in
  task-success proxies (edits/session, retry loops); zero manual config edits.
- **Kill / escalate:** if post-audit shows the tightened caps are systematically
  ignored, A cannot close this class out-of-band → that is the *evidence* that
  justifies reconsidering Option B (see appendix).

## Risks & mitigations
- **Capping output that was actually needed** → target only *repeat* offenders
  (same command class across sessions); keep the temp-file escape hatch so full
  output is one command away.
- **Compression loses signal** → truncate-with-pointer, not lossy summarization.
- **Scope creep** → this loop touches *only* carried tool output. Long-session
  bloat and retry loops stay advisory until this pattern proves out.

## Scope boundary
One waste class. One command. One verifiable metric. No change to the audit's
measured/estimated labeling. No new runtime dependency.

---

## Appendix: Option B (active runtime truncation) — DEFERRED

**What it would be:** a cram-provided Claude Code `PostToolUse` hook that caps or
compresses large tool outputs before they are carried — hard runtime enforcement
rather than an advisory cap.

**Why deferred (DECISION-011):** B would put cram in the *silent-failure path of
another tool*. If it drops output the agent actually needed, the agent acts on
incomplete context and nothing surfaces that it happened — the one failure mode
that contradicts cram's measured/advisory ethos. A stale doc erodes trust slowly;
a bad truncation corrupts a session invisibly.

**Open question to resolve before B is ever attempted:** can a `PostToolUse` hook
*rewrite/replace* a tool result entering context, or only emit feedback/block?
cram uses PostToolUse today only for a `systemMessage` (`cram_post_context.py`).
If hooks cannot mutate `tool_response`, B is infeasible and A is the ceiling.

**Preconditions for ever building B:**
1. Evidence from A that advisory caps are systematically ignored.
2. Confirmed hook capability to rewrite tool output.
3. Lossless design only: truncate-with-pointer to a temp file, never summarize;
   high threshold; opt-in.
