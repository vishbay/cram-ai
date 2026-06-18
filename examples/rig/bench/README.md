# cram-bench-v1 — a reproducible referee benchmark

A self-contained benchmark for `cram rig`. Every task ships **red** (its tests fail) and is
solved by editing source until `pytest -q` passes. There is **no external repo to clone** — the
fixtures live here — so anyone can reproduce the numbers.

The metric is **tokens at fixed success**: a provider's effective-token cost is averaged only
over runs that actually passed the task's oracle. A tool that "saves" tokens by failing the task
is not credited.

## Tiers

| Tier | Task | Shape |
|---|---|---|
| small | `fix-failing-test` | one-file localized bug (median) |
| small | `add-cli-flag` | one-file feature to a spec |
| medium | `shapes-area` | bug in a multi-module package |
| large | `ledger-cli` | bug spanning store / commands / cli |

## Run it

```bash
# Dry run — resolve the grid + provider availability, no execution:
cram rig examples/rig/bench/corpus.bench.json --dry-run

# One tier, N repeats per cell, JSON out (needs a live runner: claude or codex):
cram rig examples/rig/bench/corpus.bench.json \
    --tier small --repeats 3 --runner claude --json \
    > examples/rig/bench/results/claude-sonnet-$(date +%F).json

# Whole corpus:
cram rig examples/rig/bench/corpus.bench.json --repeats 3 --runner claude --json > result.json
```

`--repeats N` runs each (task × provider) cell N times in isolated workdirs so the summary can
report variance (`eff_tokens_stdev`).

## Leaderboard

Commit result JSON files under `results/`, then render a ranked table:

```bash
cram rig --leaderboard 'examples/rig/bench/results/*.json'
```

Ranking is by tokens-at-fixed-success: highest success rate first, then cheapest effective
tokens. `vs base` is the delta against the `baseline` provider **within the same run**.

## Honest reproducibility caveats

- Token counts depend on the live agent and are **not** deterministic; committed results are
  reference points, not exact reproductions.
- Absolute token counts are comparable only **within one run** (same model + cram version).
  Cross-machine comparison requires matching `meta.model` and `meta.cram_version`.
- Keep the `measured` framing: these are measured effective tokens from real transcripts, not
  estimates.
