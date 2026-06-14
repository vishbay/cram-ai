"""Recommendation registry — maps a waste class to the optimizer that fixes it.

cram stays advisory: it diagnoses token waste (audit_findings), *recommends* the
persona-appropriate optimizer here, and later *verifies* the metric dropped once
the user wires it. cram never mutates anyone's pipeline.

Scope is deliberately narrow — the user persona is "a developer using a coding
agent" (Claude Code / Cursor / Codex), so optimizers that need you to own the
inference path (semantic caches, gateways, KV reuse) are out of scope. What's
left acts on context *before it's sent*: cram's own context layer, tool-output
truncation/compression, and retrieval pruning.

Three pieces:
  WASTE_CLASS_OF   finding id  → coarse waste class
  CLASS_OPTIMIZERS waste class → optimizers, primary first
  OPTIMIZERS       id          → Optimizer (wiring recipe + verify hint + an
                                 optional transcript `detector` the verify loop
                                 uses to tell whether the optimizer is active)

derive_findings() calls recommend_for() to attach `waste_class` + `recommended`
to each finding. The fields are additive; existing finding consumers ignore them.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(slots=True, frozen=True)
class Optimizer:
    """One recommendable optimizer.

    kind:     'cram'     — cram ships it; you control detection end-to-end
              'config'   — a settings/prompt fix, no third-party dependency
              'external' — a third-party tool the user wires (recipe only)
    detector: transcript fingerprint the verify loop matches to decide "is this
              optimizer active this session?" — e.g. {'kind': 'mcp_tool',
              'match': 'get_context'}. None means no signature is wired yet
              (the verify A/B can't auto-detect it; the user tags runs manually).
    """
    id: str
    title: str
    kind: str
    addresses: tuple[str, ...]
    wiring: str
    verify: str
    detector: dict | None = None


# ── Optimizer registry ───────────────────────────────────────────────────────
# Coding-agent persona, advisory only. Primary-first ordering lives in
# CLASS_OPTIMIZERS below; this is the catalog.

_OPTIMIZER_LIST: tuple[Optimizer, ...] = (
    Optimizer(
        id='cram-context-layer',
        title="cram's repo/task context layer",
        kind='cram',
        addresses=('orientation',),
        wiring='Run `cram init`, fill DECISIONS.md/GOTCHAS.md, and wire the MCP '
               'server so the agent calls get_context() at session start.',
        verify='Re-audit: repeated cross-session reads and pre-edit context '
               'share should drop. cram detects get_context() calls in the '
               'transcript and A/Bs sessions with the layer on vs off.',
        # Generalizes the existing context-mode ctx_* detection to cram's own
        # MCP tool — this is the signature the verify loop will match.
        detector={'kind': 'mcp_tool', 'match': 'get_context'},
    ),
    Optimizer(
        id='output-protection',
        title='Tool-output truncation (head/tail + byte caps)',
        kind='config',
        addresses=('context-bloat',),
        wiring='Enable cram output protection (already emitted for file-based '
               'targets): byte-cap unknown commands, use head/tail not raw cat, '
               'route large output to a temp file. Tune in '
               '.ai-context/config.toml [output].',
        verify='Re-audit: oversized-result count and carried-read cost should '
               'fall. Zero-dependency; the cheapest fix to try first.',
        detector=None,
    ),
    Optimizer(
        id='llmlingua-tool-output',
        title='LLMLingua compression on tool output',
        kind='external',
        addresses=('context-bloat',),
        wiring='Add a Claude Code hook (or MCP proxy) that pipes large tool '
               'results through LLMLingua before they re-enter context. Recipe '
               'TBD — this is the first external adapter on the roadmap.',
        verify='Re-audit: carried-read cost should drop without the task '
               'failing (cram rig scores tokens at fixed success). Detector '
               'signature not wired yet — tag runs manually for the A/B.',
        detector=None,
    ),
    Optimizer(
        id='prompt-cache-stability',
        title='Stabilize the cached prefix',
        kind='config',
        addresses=('cache',),
        wiring='Keep the frozen prefix byte-stable and above the cache floor '
               '(2,048 tok Sonnet / 4,096 Opus); deliver per-task context as a '
               'tool result, not a prefix rewrite. `cram benchmark` flags a '
               'sub-floor prefix.',
        verify='Re-audit: cache-blind sessions (wrote cache, never read it) '
               'should approach zero and cache reads should appear.',
        detector=None,
    ),
    Optimizer(
        id='knowledge-capture',
        title='Capture failing commands as decisions/gotchas',
        kind='cram',
        addresses=('retry',),
        wiring='Record the recurring failure with `cram gotcha "..."` / '
               '`cram decide "..."` so it ships in context and the agent stops '
               'rediscovering it.',
        verify='Re-audit: failed tool calls per session and same-file re-edit '
               'churn should decline.',
        detector=None,
    ),
)

OPTIMIZERS: dict[str, Optimizer] = {o.id: o for o in _OPTIMIZER_LIST}


# ── Taxonomy ─────────────────────────────────────────────────────────────────
# finding id (audit_findings) → coarse waste class.
WASTE_CLASS_OF: dict[str, str] = {
    'repeated-reads':    'orientation',
    'high-orientation':  'orientation',
    'oversized-results': 'context-bloat',
    'context-bloat':     'context-bloat',
    'cache-blind':       'cache',
    'retry-loops':       'retry',
    'edit-churn':        'retry',
}

# waste class → optimizer ids, primary first. The primary is the lowest-risk /
# lowest-dependency option; alternatives trail it.
CLASS_OPTIMIZERS: dict[str, tuple[str, ...]] = {
    'orientation':   ('cram-context-layer',),
    'context-bloat': ('output-protection', 'llmlingua-tool-output'),
    'cache':         ('prompt-cache-stability',),
    'retry':         ('knowledge-capture',),
}


def waste_class_for(finding_id: str) -> str | None:
    """Coarse waste class for a finding id, or None if unmapped."""
    return WASTE_CLASS_OF.get(finding_id)


def recommend_for(finding_id: str) -> dict | None:
    """Primary recommendation for a finding id (+ alternative optimizer ids).

    Returns a JSON-friendly dict: optimizer id/title/kind, the wiring recipe,
    the verify hint, and a list of alternative optimizer ids for the same class.
    None when the finding id maps to no optimizer (keeps unknown ids inert).
    """
    cls = WASTE_CLASS_OF.get(finding_id)
    if cls is None:
        return None
    ids = CLASS_OPTIMIZERS.get(cls)
    if not ids:
        return None
    primary = OPTIMIZERS[ids[0]]
    return {
        'optimizer':    primary.id,
        'title':        primary.title,
        'kind':         primary.kind,
        'wiring':       primary.wiring,
        'verify':       primary.verify,
        'alternatives': list(ids[1:]),
    }


def attach_recommendations(findings: list[dict]) -> list[dict]:
    """Annotate findings in place with `waste_class` + `recommended`.

    Additive: leaves the existing id/severity/evidence/fix untouched. A finding
    with no mapping gets waste_class=None and recommended=None rather than being
    dropped, so the report still shows it.
    """
    for f in findings:
        fid = f.get('id', '')
        f['waste_class'] = waste_class_for(fid)
        f['recommended'] = recommend_for(fid)
    return findings
