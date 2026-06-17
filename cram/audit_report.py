"""Shareable markdown report for cram audit.

render_report() is a pure function over the collect_audit() aggregate dict —
the same numbers as the terminal report, formatted to travel: paste into a PR,
an issue, or Slack. Headline first, findings second, then the evidence tables.
Every number carries a measured/estimated basis so the report makes no claim
the transcripts can't back.
"""

from __future__ import annotations
import datetime
import os

from cram.audit_events import repo_rel

# Finding id → how to verify the fix worked. Closes the profiler→referee loop:
# every finding says evidence → fix → how to confirm it landed.
_VERIFY = {
    'repeated-reads':   'add the briefing, then `cram audit --compare <before> <after>` '
                        '(or `cram rig`) — cross-session reads should drop.',
    'high-orientation': 'front-load context, then re-run `cram audit` and watch the pre-edit '
                        'share; confirm with `cram rig --providers baseline,cram`.',
    'oversized-results':'cap the output, then `cram audit --session <id>` — the carried cost '
                        'should be gone.',
    'cache-blind':      'fix the prefix/cache config, then `cram audit` — cache-engaged '
                        'sessions should rise.',
    'retry-loops':      'record the gotcha, then re-audit — failed tool calls/session should fall.',
    'edit-churn':       'tighten the task brief, then re-audit — same-file re-edits should fall.',
    'context-bloat':    'trim results / tune compaction, then `cram audit --session <id>` — '
                        'growth should drop.',
}


def render_report(data: dict, repo_root: str) -> str:
    """Return a markdown report for a collect_audit() result."""
    name = os.path.basename(repo_root.rstrip(os.sep)) or repo_root
    total = data['sessions']
    today = datetime.date.today().isoformat()

    lines: list[str] = []
    lines.append(f'# Agent session audit — {name}')
    lines.append('')
    lines.append(f'*Last {data["days"]} days · {total} session'
                 f'{"s" if total != 1 else ""} · generated {today} · '
                 f'{data["provider"]} pricing*')

    # ── Headline ──────────────────────────────────────────────────────────────
    lines.append('')
    lines.append('## Headline')
    lines.append('')
    if data['pre_edit_spend_share'] is not None:
        n_meas = data['pre_edit_measured_sessions']
        prelim = (f'**Preliminary** — only {n_meas} measured edit session'
                  f'{"s" if n_meas != 1 else ""}. '
                  if data.get('pre_edit_preliminary') else '')
        lines.append(f'{prelim}**Pre-edit context share: '
                     f'{data["pre_edit_spend_share"]:.0%}** of '
                     f'{data["pre_edit_eff_total_tokens"]:,.0f} effective input '
                     f'tokens across {n_meas} measured edit session'
                     f'{"s" if n_meas != 1 else ""}. This is descriptive: it says '
                     f'how much context-gathering precedes editing, not that all '
                     f'of it is waste — see Findings for the avoidable patterns.')
        if data['pre_edit_spend_eff_tokens'] is not None:
            lines.append(f'Pre-edit spend: ~{data["pre_edit_spend_eff_tokens"]:,.0f} '
                         f'eff. tokens/session (~${data["pre_edit_spend_cost"]:.4f}).')
    else:
        lines.append('**Pre-edit context share not measurable** — no edit sessions '
                     'with token usage in this window (estimates below).')
    lines.append('')
    seg = (f'{total} total — {data["edit_sessions"]} edit session'
           f'{"s" if data["edit_sessions"] != 1 else ""}')
    if data['pre_edit_measured_sessions']:
        seg += f' ({data["pre_edit_measured_sessions"]} measured)'
    if data['read_only_sessions']:
        seg += (f', {data["read_only_sessions"]} no-edit sessions '
                f'(excluded — reviews, Q&A, or runs with no counted edit)')
    lines.append(f'Sessions: {seg}.')

    # ── Token waterfall ─────────────────────────────────────────────────────────
    tree = data.get('spine_tree')
    spine = data.get('token_spine') or {}
    spine_total = spine.get('total', 0) or 0
    if tree or spine_total > 0:
        lines.append('')
        lines.append('## Token waterfall')
        lines.append('')

    if tree:
        n = tree['pool_sessions']
        spine_eff_total = tree['total']
        lines.append('**Measured spine** — effective input by composition × pre/post-edit, over '
                     f'{n} edit session{"s" if n != 1 else ""} with token usage. '
                     'Children sum to their parent.')
        lines.append('')
        lines.append('```')
        lines.append(f'eff input  {spine_eff_total:>13,.0f}')
        comps = sorted(tree['components'], key=lambda c: c['eff'], reverse=True)
        for i, c in enumerate(comps):
            last = i == len(comps) - 1
            branch, childpfx = ('└─', '   ') if last else ('├─', '│  ')
            pct = c['eff'] / spine_eff_total * 100 if spine_eff_total else 0
            lines.append(f'{branch} {c["label"]:<11} {pct:3.0f}%  {c["eff"]:>13,.0f}')
            pre_pct = c['pre'] / c['eff'] * 100 if c['eff'] else 0
            lines.append(f'{childpfx}├─ pre-edit   {pre_pct:3.0f}%')
            lines.append(f'{childpfx}└─ post-edit  {100 - pre_pct:3.0f}%')
        lines.append('```')
        lines.append('')
    elif spine_total > 0:
        # No measured edit sessions → pre/post-edit is undefined; show the
        # composition-only spine (still measured and exhaustive) over all sessions.
        lines.append('**Measured spine** — effective input-side composition (all sessions). '
                     'Children sum to the total.')
        lines.append('')
        lines.append('```')
        lines.append(f'eff input  {spine_total:>13,.0f}')
        comps = sorted(
            [('cache read', spine.get('cache_read', 0)),
             ('fresh input', spine.get('fresh_input', 0)),
             ('cache write', spine.get('cache_write', 0))],
            key=lambda kv: kv[1], reverse=True,
        )
        for i, (label, val) in enumerate(comps):
            branch = '└─' if i == len(comps) - 1 else '├─'
            pct = val / spine_total * 100 if spine_total else 0
            lines.append(f'{branch} {label:<11} {pct:3.0f}%  {val:>13,.0f}')
        lines.append('```')
        lines.append('')

    if tree or spine_total > 0:
        lines.append('**Estimated / overlapping attribution** — modeled and not mutually '
                     'exclusive, so these do **not** sum to the spine.')
        lines.append('')
        lines.append('| Attribution | Value | Basis |')
        lines.append('|---|---:|---|')
        if data.get('pre_edit_spend_share') is not None:
            lines.append(f'| Pre-edit (orientation) share | '
                         f'{data["pre_edit_spend_share"]:.0%} of input-side spend | measured |')
        lines.append(f'| ~ orientation file reads | '
                     f'~${data["orient_cost_per_session"]:.4f}/session '
                     f'| estimated (tokens/file model) |')
        if data.get('sessions_with_big_results'):
            lines.append(f'| ~ carried oversized output | '
                         f'~${data["carried_cost_per_session"]:.4f}/session '
                         f'| estimated (measured tok × price) |')
        if data.get('avg_redundant_reads', 0) >= 0.5:
            lines.append(f'| Redundant same-file reads | '
                         f'{data["avg_redundant_reads"]:.1f}/session | measured count |')
        if data.get('avg_error_results', 0) > 0:
            lines.append(f'| Failed tool calls (retries) | '
                         f'{data["avg_error_results"]:.1f}/session | measured count |')
        if data.get('avg_context_growth') is not None:
            lines.append(f'| Context growth (peak/start) | '
                         f'{data["avg_context_growth"]:.1f}× | measured |')

    # ── Findings ──────────────────────────────────────────────────────────────
    findings = data.get('findings') or []
    if findings:
        lines.append('')
        lines.append(f'## Findings ({len(findings)})')
        lines.append('')
        for i, fd in enumerate(findings, 1):
            lines.append(f'{i}. **{fd["id"]}** — {fd["evidence"]}')
            lines.append(f'   → fix: {fd["fix"]}')
            verify = _VERIFY.get(fd['id'])
            if verify:
                lines.append(f'   → verify: {verify}')

    # ── Session leaderboard ─────────────────────────────────────────────────────
    board = data.get('leaderboard') or []
    if board:
        lines.append('')
        lines.append('## Session leaderboard')
        lines.append('')
        lines.append('Heaviest measured sessions by fresh input-side tokens. '
                     'Drill in with `cram audit --session <id>` (Claude and Codex).')
        lines.append('')
        lines.append('| Session | Source | Input tok | Reads→edit | Ctx growth | Retries | Redundant |')
        lines.append('|---|---|---:|---:|---:|---:|---:|')
        for s in board:
            sid = str(s.get('session_id', ''))[:8] or '—'
            src = s.get('source', 'claude')
            growth = s.get('context_growth_factor')
            growth_s = f'{growth:.1f}×' if growth else '—'
            lines.append(
                f'| `{sid}` | {src} | {s.get("input_tokens", 0):,} '
                f'| {s.get("reads_before_edit", 0)} | {growth_s} '
                f'| {s.get("error_results", 0)} | {s.get("redundant_reads", 0)} |'
            )

    # ── Retry loops (most-retried failed commands) ────────────────────────────
    failed_cmds = [c for c in data.get('top_failed_commands', []) if c['failures'] > 1]
    if failed_cmds:
        lines.append('')
        lines.append('## Retry loops')
        lines.append('')
        lines.append('The same command failing repeatedly — wasted tokens on re-runs. '
                     'Drill in with `cram audit --layer retries`.')
        lines.append('')
        lines.append('| Failures | Sessions | Command |')
        lines.append('|---------:|---------:|---------|')
        for c in failed_cmds[:10]:
            cmd = c['cmd'].replace('|', '\\|').replace('`', "'")
            lines.append(f"| {c['failures']} | {c['sessions']} | `{cmd}` |")

    # ── Top repeated files ────────────────────────────────────────────────────
    repeated = [t for t in data.get('top_read_files', []) if t[1] > 1]
    if repeated:
        lines.append('')
        lines.append('## Top repeated files')
        lines.append('')
        lines.append('| Reads | Sessions | File |')
        lines.append('|------:|---------:|------|')
        for fp, r, n in repeated[:10]:
            # Escape pipes so a path can't break the markdown table row.
            cell = repo_rel(fp, repo_root).replace('|', '\\|')
            lines.append(f'| {r} | {n} | `{cell}` |')

    # ── Key metrics ───────────────────────────────────────────────────────────
    lines.append('')
    lines.append('## Key metrics')
    lines.append('')
    lines.append('| Metric | Value | Basis |')
    lines.append('|---|---:|---|')

    def row(label: str, value: str, basis: str) -> None:
        lines.append(f'| {label} | {value} | {basis} |')

    row('Reads before first edit (avg)', f'{data["avg_reads_before_edit"]:.1f}',
        'measured')
    row('Read-to-edit ratio', f'{data["avg_ratio"]:.1f}× ({data["ratio_band"]})',
        'measured')
    if data['avg_cache_writes'] or data['avg_cache_reads']:
        row('Cache writes / session', f'{data["avg_cache_writes"]:,.0f} tok', 'measured')
        row('Cache reads / session', f'{data["avg_cache_reads"]:,.0f} tok', 'measured')
    if data['avg_requests']:
        row('Requests / session', f'{data["avg_requests"]:.0f}', 'measured')
        row('Context / request', f'{data["avg_context_per_request"]:,.0f} tok '
            f'(peak {data["peak_context"]:,})', 'measured')
        if data['avg_context_growth'] is not None:
            row('Context growth (peak/start)', f'{data["avg_context_growth"]:.1f}×',
                'measured')
        if data['bloat_tail_share'] is not None:
            row('Read-cost share, last ⅓ of turns',
                f'{data["bloat_tail_share"]:.0%}', 'measured (33% = flat)')
        if data['sessions_with_big_results']:
            row('Oversized tool results',
                f'{data["sessions_with_big_results"]}/{total} sessions '
                f'(> {data["big_result_bytes"] // 1000} KB)', 'measured')
            row('Carried cost of oversized results',
                f'${data["carried_cost_per_session"]:.4f}/session',
                'measured tokens × price')
    if data['avg_redundant_reads'] >= 0.5:
        row('Redundant same-file reads / session',
            f'{data["avg_redundant_reads"]:.1f}', 'measured')
    if data['avg_error_results'] > 0:
        row('Failed tool calls / session', f'{data["avg_error_results"]:.1f} '
            f'({data["sessions_with_errors"]}/{total} sessions)', 'measured')
    if data['avg_edit_churn'] > 0:
        row('Same-file re-edits / session', f'{data["avg_edit_churn"]:.1f}',
            'measured')
    row('Orientation cost / session',
        f'${data["orient_cost_per_session"]:.4f}',
        'estimated (assumed tokens/file model)')

    # ── By source ─────────────────────────────────────────────────────────────
    projects = data.get('projects') or []
    if len(projects) > 1:
        coverage = {
            'claude': 'tokens + file paths',
            'cursor': 'file paths only (no token data)',
            'codex':  'tokens; edits attributed, reads not file-attributed',
        }
        lines.append('')
        lines.append('## By source')
        lines.append('')
        lines.append('| Source | Sessions | Reads/session | Reads before edit | Coverage |')
        lines.append('|---|---:|---:|---:|---|')
        for src, n, avg_r, avg_rbe, _cw in projects:
            label = src if src in ('cursor', 'codex') else 'claude'
            lines.append(f'| {label} | {n} | {avg_r:.1f} | {avg_rbe:.1f} '
                         f'| {coverage[label]} |')

    # ── Weekly trend ──────────────────────────────────────────────────────────
    if len(data.get('weekly') or []) > 1:
        lines.append('')
        lines.append('## Weekly trend — reads before first edit')
        lines.append('')
        lines.append('| Week | Avg | Sessions |')
        lines.append('|---|---:|---:|')
        for wk, avg, n in data['weekly']:
            lines.append(f'| {wk} | {avg:.1f} | {n} |')

    # ── Methodology ───────────────────────────────────────────────────────────
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('*Methodology: the pre-edit context share is the input-side '
                 'token spend (input + cache traffic weighted by provider '
                 'multipliers) of all requests before each session\'s first '
                 'edit, divided by total input-side spend, summed across '
                 'measured sessions. It is descriptive, not a waste claim. '
                 'Conservative by construction: no-edit sessions are excluded '
                 '(reading may have been the job), sessions without token usage are '
                 'excluded and reported as unmeasured, and output-token spend is '
                 'not counted. Rows marked estimated use the assumed tokens/file '
                 'model and are not measurements. Generated by `cram audit '
                 '--report`.*')
    lines.append('')
    return '\n'.join(lines)
