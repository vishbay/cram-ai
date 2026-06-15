"""Single source of truth for cram's token-cost model.

Used by `cram benchmark` and the MCP health tool so the numbers never diverge. Token counts are the len/4 heuristic — fine for relative comparison,
not billing.
"""
from __future__ import annotations
import os
from dataclasses import dataclass

# Base input price per 1M tokens (platform.claude.com).
MODEL_BASE = {
    'Opus 4.8':   5.00,
    'Sonnet 4.6': 3.00,
    'Haiku 4.5':  1.00,
}
# Per-provider pricing so dollar attribution is pluggable, not hardcoded to
# Sonnet pricing. Representative defaults (mid-2026). Real prices vary per
# model and change often — treat these as relative-comparison defaults and
# override via env for billing-grade numbers.
#   anthropic: explicit prompt caching; 5-min-TTL writes bill at 1.25×,
#              reads at 0.10× base input.
#   openai:    prompt caching is automatic with no write premium; cached
#              input is billed at ~50% on common models.
#   gemini:    implicit caching discounts cached input (~75% off); explicit
#              caches add a storage cost not modeled here.
#   local:     zero-dollar — the relevant cost is latency, not money.
PROVIDER_PRICING = {
    'anthropic': {'input_per_mtok': 3.00, 'cache_write_mult': 1.25, 'cache_read_mult': 0.10},
    'openai':    {'input_per_mtok': 2.50, 'cache_write_mult': 1.00, 'cache_read_mult': 0.50},
    'gemini':    {'input_per_mtok': 1.25, 'cache_write_mult': 1.00, 'cache_read_mult': 0.25},
    'local':     {'input_per_mtok': 0.00, 'cache_write_mult': 0.00, 'cache_read_mult': 0.00},
    # Enterprise / hosted variants share the same model pricing as their base providers.
    # Vertex AI hosts Gemini models; Bedrock hosts Claude; Azure hosts OpenAI.
    'vertex_ai': {'input_per_mtok': 1.25, 'cache_write_mult': 1.00, 'cache_read_mult': 0.25},
    'bedrock':   {'input_per_mtok': 3.00, 'cache_write_mult': 1.25, 'cache_read_mult': 0.10},
    'azure':     {'input_per_mtok': 2.50, 'cache_write_mult': 1.00, 'cache_read_mult': 0.50},
}

# Anthropic multipliers, kept as module constants — other modules import them.
# Defined from the table so the two can't diverge.
WRITE_MULT = PROVIDER_PRICING['anthropic']['cache_write_mult']   # 5-min-TTL cache write
READ_MULT  = PROVIDER_PRICING['anthropic']['cache_read_mult']    # cache read

# Env overrides for individual pricing fields, applied after table lookup.
_PRICING_ENV_OVERRIDES = {
    'input_per_mtok':   'CRAM_PRICE_INPUT_PER_MTOK',
    'cache_write_mult': 'CRAM_CACHE_WRITE_MULT',
    'cache_read_mult':  'CRAM_CACHE_READ_MULT',
}


def resolve_provider(provider: str | None = None) -> str:
    """Resolve a provider name: argument → $CRAM_PROVIDER → 'anthropic'.

    Unknown names fall back to 'anthropic' (never raise — the audit must not
    crash on a typo'd env var).
    """
    if provider is None:
        provider = os.environ.get('CRAM_PROVIDER', 'anthropic')
    key = provider.strip().lower()
    return key if key in PROVIDER_PRICING else 'anthropic'


def get_provider_pricing(provider: str | None = None) -> dict:
    """Pricing dict for a provider: input_per_mtok, cache_write_mult, cache_read_mult.

    Provider resolution per resolve_provider(). Field-level env overrides win
    over the table; unparseable values are ignored. Returns a fresh dict each
    call (callers may mutate).
    """
    pricing = dict(PROVIDER_PRICING[resolve_provider(provider)])
    for field, env_var in _PRICING_ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None:
            continue
        try:
            pricing[field] = float(raw)
        except ValueError:
            pass  # ignore unparseable overrides, keep table value
    return pricing

# Workload assumptions (overridable via env).
SESSIONS_PER_DAY  = int(os.environ.get('AICONTEXT_SESSIONS_PER_DAY',  '4'))
TASKS_PER_SESSION = int(os.environ.get('AICONTEXT_TASKS_PER_SESSION', '4'))

# Orientation model: without cram, the agent cold-starts each SESSION by reading
# ~N raw files to orient. This is the cost cram removes — NOT a full-repo
# rewrite, and billed at base input (tool-result reads), not cache write.
ORIENT_FILES = int(os.environ.get('AICONTEXT_ORIENT_FILES', '8'))


@dataclass
class CostInputs:
    repo_tokens: int
    repo_files:  int
    frozen_tok:  int


def orientation_tokens(repo_tokens: int, repo_files: int) -> int:
    """Tokens read to cold-start orient, per session, without cram."""
    if repo_files <= 0 or repo_tokens <= 0:
        return 0
    avg_file = repo_tokens / repo_files
    return int(min(repo_tokens, ORIENT_FILES * avg_file))


# Soft per-file token budgets for the frozen context layer. Warnings only.
# Calibrated to cram's output (ARCHITECTURE is line-budgeted ~300 lines), NOT
# the 400-tok external target. SYMBOLS scales with repo size → no flat cap.
FILE_BUDGETS = {
    'ARCHITECTURE.md': int(os.environ.get('CRAM_BUDGET_ARCHITECTURE', '3000')),
    'DECISIONS.md':    int(os.environ.get('CRAM_BUDGET_DECISIONS',    '1800')),
    'GOTCHAS.md':      int(os.environ.get('CRAM_BUDGET_GOTCHAS',      '800')),
    'CURRENT_TASK.md': int(os.environ.get('CRAM_BUDGET_TASK',         '2000')),
}


def budget_status(fname: str, tokens: int) -> str:
    """'ok' | 'near' (≥80%) | 'over' (>100%) | 'none' (no budget for this file)."""
    limit = FILE_BUDGETS.get(fname)
    if not limit:
        return 'none'
    if tokens > limit:
        return 'over'
    if tokens >= 0.8 * limit:
        return 'near'
    return 'ok'


def daily_costs(inp: CostInputs, base_price: float) -> dict:
    """Return modeled daily costs for one model's base input price."""
    base  = base_price / 1_000_000
    write = base * WRITE_MULT
    read  = base * READ_MULT
    S, T  = SESSIONS_PER_DAY, TASKS_PER_SESSION

    orient = orientation_tokens(inp.repo_tokens, inp.repo_files)
    # Without cram: re-orient once per session at base input price.
    nocram = S * orient * base
    # With cram: frozen layer write-once/session + read (T-1)×; orientation gone.
    cram   = S * (inp.frozen_tok * write + (T - 1) * inp.frozen_tok * read)
    return {
        'orient_tokens': orient,
        'nocram_daily':  nocram,
        'cram_daily':    cram,
        'daily_saving':  max(0.0, nocram - cram),
    }
