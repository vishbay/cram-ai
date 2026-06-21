# `cram audit --json` contract

`cram audit` and its variants emit a stable, versioned JSON document so consumers
(the GitHub Action, the `cram rig` leaderboard, dashboards) can rely on the shape.

## Versioning

Every JSON document carries `schema_version` (currently **`audit/2`**). The top-level
key set of the aggregate document is **stable** — a key is present with `null` rather
than omitted when it has no value. Bump `schema_version` on any breaking shape change;
gate your consumer on it. (`audit/2` added the real-$ keys below.)

## Documents

| Command | Shape |
|---|---|
| `cram audit --json` | the aggregate document (below) |
| `cram audit --session ID --json` | `{schema_version, ...per-request timeline}` |
| `cram audit --layer NAME --json` | `{schema_version, layer, rows}` |
| `cram audit --compare A B --json` | `{schema_version, days, a:{path,data}, b:{path,data}}` |

## Aggregate document (selected keys)

- `schema_version` — contract version, e.g. `"audit/1"`.
- `days`, `sessions` — window and number of sessions analysed.
- `avg_reads_before_edit`, `avg_ratio`, `ratio_band` — orientation signal.
- `avg_cache_writes`, `avg_cache_reads`, `cache_blind_sessions` — cache engagement.
- `avg_requests`, `avg_context_per_request`, `peak_context`, `avg_context_growth`.
- `pre_edit_spend_share`, `pre_edit_spend_cost`, `pre_edit_measured_sessions` — measured orientation.
- `token_spine`, `spine_tree` — effective-input composition (measured).
- `layer_costs` — overlapping waste diagnostics; each row has its own `basis`
  (`measured` / `estimated` / `count`).
- `total_eff_cost`, `monthly_cost`, `cost_per_measured_session`, `cost_measured_sessions` —
  **measured**, model-aware: effective input-side $ priced per each session's own model
  (`claude-opus-4-8`, `gpt-5`, …), falling back to the provider flat rate when the model is
  unrecorded (e.g. Cursor). A floor on real spend (input-side only).
- `model_mix` — `{model_id: session_count}` over the measured pool.
- `biggest_avoidable` — the single most expensive avoidable layer `{layer, basis,
  cost_per_session, monthly_cost, note}` (the largest single layer, not a sum — layers overlap).
- `orient_cost_per_session`, `monthly_orient_cost` — **estimated** (assumed tokens/file model).
- `top_read_files`, `leaderboard`, `top_failed_commands`, `weekly`, `recent`, `projects`.
- `findings` — deterministic rules; each finding has `id`, `severity`, `evidence`, `fix`,
  `sample_n`, `preliminary` (True when based on fewer than 3 measured sessions), and `verify`
  (`{command, expect}` — how to prove the fix worked; the referee loop, e.g. `cram rig` or
  `cram audit --compare`).
- `parse_failures` — count of transcripts that failed to parse this run (numbers may be
  incomplete; a locked Cursor DB shows up here).
- `bases` — machine-readable measured/estimated basis for the headline cost aggregates.
- `cursor_estimated`, `est_cursor_read_tokens` — present when `--estimate-cursor` is on
  (always `estimated` basis; never folded into measured aggregates).

## Basis vocabulary

- **measured** — derived from real token usage in the transcripts.
- **estimated** — modelled (e.g. assumed tokens/file, Cursor file-size estimation).
- **count** — a frequency we trust but do not dollar-cost.

Honour the basis: never present an `estimated` number as `measured`.
