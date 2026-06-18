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

Render a ranked table from the committed `results/`:

```bash
cram rig --leaderboard 'examples/rig/bench/results/*.json'
```

Ranking is by tokens-at-fixed-success: highest success rate first, then cheapest effective
tokens. `vs base` is the delta against the `baseline` provider **within the same run**.

### Current board

<!-- LEADERBOARD:START -->
| # | Provider | Run | Success | Eff tokens (±σ) | vs base | N |
|--:|---|---|--:|--:|--:|--:|
| 1 | baseline | claude-opus-4-8 | 100% | 15,818 ±1,425 | +0% | 8 |
| 2 | cram | claude-opus-4-8 | 100% | 15,985 ±1,254 | +1% | 8 |
| 3 | repomix | claude-opus-4-8 | 100% | 17,971 ±1,418 | +14% | 8 |
<!-- LEADERBOARD:END -->

_Seed run: `cram-bench-v1` on `claude-opus-4-8`, N=2/cell, 2026-06-18. Read: on these small
tasks every arm still passes, and **repomix's whole-repo packing costs +14% tokens for no
benefit** — exactly the kind of "saving that wasn't" the referee exists to catch. (The `cram`
arm ran without `cram init` on the synthetic fixtures, so it had nothing to pre-load and lands
≈ baseline.)_

## Submit your optimizer's score

The leaderboard is open — run the benchmark with your context optimizer and open a PR adding
your result file. The `bench leaderboard` workflow validates it and renders the updated board.

1. Add your optimizer as a `cram rig` provider (the generic `CommandAdapter` covers any tool
   that emits context on stdout — e.g. `repomix`, `files-to-prompt`).
2. Run the pinned corpus **including a `baseline` arm** (required — it's the comparable metric):
   ```bash
   cram rig examples/rig/bench/corpus.bench.json \
       --providers baseline,<your-optimizer> --runner claude --repeats 3 --json \
       > examples/rig/bench/results/cram-bench-v1-<optimizer>-<model>-<date>.json
   ```
3. Wrap it (or hand-edit) so the file has a `meta` block: `model`, `cram_version`, `runner`,
   `repeats`, `date`. Validate locally: `python scripts/validate_bench_result.py <file>`.
4. Open a PR. CI checks the schema and re-renders the board.

### Rules that keep it honest

- **Success first, then tokens.** A run that saves tokens by failing the task is never ranked
  above a higher-success run — the referee reports tokens *at fixed success*.
- **A `baseline` arm is required.** Absolute token counts are not comparable across machines;
  only the within-run `vs base` delta is. Rows are grouped by declared `model` + `cram_version`.
- **N ≥ 3 recommended** (LLM nondeterminism); report `eff_tokens_stdev`.
- **Pinned, small corpus → directional.** The 4-task bench is overfittable; treat it as a
  signal, not a verdict. A "verified" tier (maintainer re-run) may follow.

## Honest reproducibility caveats

- Token counts depend on the live agent and are **not** deterministic; committed results are
  reference points, not exact reproductions.
- Absolute token counts are comparable only **within one run** (same model + cram version).
  Cross-machine comparison requires matching `meta.model` and `meta.cram_version`.
- Keep the `measured` framing: these are measured effective tokens from real transcripts, not
  estimates.
