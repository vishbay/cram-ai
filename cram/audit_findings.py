"""Findings: deterministic rules over the audit aggregates.

Each finding pairs evidence (numbers already in the audit data, never an LLM
judgment) with a concrete remediation lever. Thresholds are conservative on
purpose: a finding that fires on healthy repos gets the whole report ignored.

A finding dict: {'id', 'severity' ('warn' for now), 'evidence', 'fix'} plus the
additive {'waste_class', 'recommended'} attached from cram.recommend — the typed
class and the optimizer cram recommends for it. Order is fixed (most actionable
first) so output is stable run-to-run.
"""

from __future__ import annotations

from cram.recommend import attach_recommendations

# Thresholds (module-level so they're visible and testable; deliberately not
# env-tunable until someone needs it — fewer knobs, more comparable reports).
HIGH_ORIENTATION_PCT = 0.25   # measured pre-edit share of input-side spend
RETRY_LOOP_ERRORS    = 1.0    # avg failed tool calls per session
EDIT_CHURN_LIMIT     = 2.0    # avg same-file re-edits per session
CONTEXT_GROWTH_LIMIT = 5.0    # peak/start context ratio


def derive_findings(data: dict) -> list[dict]:
    """Return findings for a collect_audit aggregate dict (possibly empty)."""
    findings: list[dict] = []
    total = data['sessions']

    # Files re-read across sessions → repo-briefing candidates.
    hot = [(fp, r, n) for fp, r, n in data['top_read_files'] if n >= 2]
    if hot:
        fp, r, n = hot[0]
        more = f" (+{len(hot) - 1} more)" if len(hot) > 1 else ''
        findings.append({
            'id': 'repeated-reads',
            'severity': 'warn',
            'evidence': f"{fp} read {r}× across {n} sessions{more}",
            'fix': 'Summarize these files into a repo briefing '
                   '(CLAUDE.md / cram task) so agents stop re-reading them.',
        })

    # Measured orientation share above threshold.
    pct = data['pre_edit_spend_share']
    if pct is not None and pct >= HIGH_ORIENTATION_PCT:
        findings.append({
            'id': 'high-orientation',
            'severity': 'warn',
            'evidence': f"{pct:.0%} of input-side spend lands before the first "
                        f"edit ({data['pre_edit_measured_sessions']} sessions measured)",
            'fix': 'Front-load repo context (architecture summary, briefing) '
                   'instead of letting each session rediscover it.',
        })

    # Oversized tool results carried by every later turn.
    if data['sessions_with_big_results']:
        kb = data['big_result_bytes'] // 1000
        findings.append({
            'id': 'oversized-results',
            'severity': 'warn',
            'evidence': f"{data['sessions_with_big_results']}/{total} sessions "
                        f"carried a tool result > {kb} KB "
                        f"(~${data['carried_cost_per_session']:.4f}/session in re-reads)",
            'fix': 'Truncate tool output (head/tail, line limits) or tighten '
                   'MCP server responses; big results are re-paid every turn.',
        })

    # Cache written but never read back.
    if data['cache_blind_sessions']:
        findings.append({
            'id': 'cache-blind',
            'severity': 'warn',
            'evidence': f"{data['cache_blind_sessions']}/{total} sessions wrote "
                        f"prompt cache but never read it",
            'fix': 'Check prompt caching: unstable or sub-minimum prefixes pay '
                   'the write premium and never hit.',
        })

    # Failed tool calls → likely retry loops.
    if data['avg_error_results'] >= RETRY_LOOP_ERRORS:
        findings.append({
            'id': 'retry-loops',
            'severity': 'warn',
            'evidence': f"{data['avg_error_results']:.1f} failed tool calls/session "
                        f"({data['sessions_with_errors']}/{total} sessions had failures)",
            'fix': 'Capture the failing commands as gotchas/recipes so agents '
                   'stop rediscovering them.',
        })

    # Same-file re-edit churn → thrashing.
    if data['avg_edit_churn'] >= EDIT_CHURN_LIMIT:
        findings.append({
            'id': 'edit-churn',
            'severity': 'warn',
            'evidence': f"{data['avg_edit_churn']:.1f} same-file re-edits/session",
            'fix': 'Sustained churn means thrashing — tighten the task brief or '
                   'add the relevant invariants/tests to context.',
        })

    # Context ballooning over the session.
    growth = data['avg_context_growth']
    if growth is not None and growth > CONTEXT_GROWTH_LIMIT:
        findings.append({
            'id': 'context-bloat',
            'severity': 'warn',
            'evidence': f"context grows {growth:.1f}× from session start to peak "
                        f"({data['context_growth_measured']} sessions measured)",
            'fix': 'Heavy growth usually means accumulated tool output — trim '
                   'results, or tune compaction before the window fills.',
        })

    return attach_recommendations(findings)
