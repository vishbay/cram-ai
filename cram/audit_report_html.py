"""Self-contained HTML report for `cram audit --report-html`.

render_report_html() is a pure function over the same collect_audit() dict the
markdown report uses, plus a per-layer drilldown map and optional per-session
timelines. It emits one standalone HTML file — no external CSS, fonts, or JS —
so it travels: open locally, attach to a PR, drop in Slack.

The theme is a restrained dark data dashboard (Datadog / Grafana / GitHub
Actions logs) with terminal accents: dense scannable tables, monospace data,
ASCII tree ticks in the waterfall, severity colour only where it earns its
place, and a measured/estimated/count basis on every number. No glass, no blur
— cram is an engineering instrument, not an AI command center.

Every number traces to a measured/estimated basis, exactly like the markdown
report — this renderer only changes presentation, never the claims.
"""

from __future__ import annotations
import datetime
import html
import os
import re

from cram.audit_events import repo_rel
from cram.audit_report import _VERIFY
from cram.cost_model import get_provider_pricing, resolve_provider

# Pricing resolution mirrors cram.audit so the HTML and text reports agree.
_PROVIDER = resolve_provider()
_P = get_provider_pricing(_PROVIDER)
_BASE_PRICE = float(os.environ.get(
    'CRAM_AUDIT_BASE_PRICE', str(_P['input_per_mtok'] / 1_000_000)))
_CR_MULT = _P['cache_read_mult']
_CW_MULT = _P['cache_write_mult']

# Component → bar colour class (CSS classes defined per theme below).
_COMP_CLS = {'cache read': ('cr', 'crp', 'crq'),
             'fresh input': ('fr', 'frp', 'frq'),
             'cache write': ('cw', 'cw', 'cw')}
_COMP_GRAD = {'cache read': '#5b9dff', 'fresh input': '#3fb950', 'cache write': '#d29922'}

_LAYER_DESC = {
    'orientation': 'reads before first edit',
    'repeated':    'files re-read across sessions',
    'redundant':   'files re-read within a session',
    'churn':       'files re-edited within a session',
    'carried':     'oversized output re-read every turn',
    'retries':     'failed tool calls per session',
}
_LAYER_ORDER = ('orientation', 'repeated', 'redundant', 'churn', 'carried', 'retries')


# ── helpers ──────────────────────────────────────────────────────────────────

def _esc(s) -> str:
    return html.escape(str(s))


def _code(text: str) -> str:
    return re.sub(r'`([^`]+)`', r'<code>\1</code>', html.escape(text))


def _sev(value: float, mid: float, hi: float) -> str:
    return 'sev-hi' if value >= hi else ('sev-md' if value >= mid else 'sev-lo')


def _spend(data: dict) -> tuple[float, float, float]:
    """(total input-side spend, $/session, cache-hit %) from the measured spine."""
    spine = data.get('token_spine') or {}
    total_eff = spine.get('total', 0) or 0
    total_spend = total_eff * _BASE_PRICE
    n = max(1, data['sessions'])
    raw_cr = (spine.get('cache_read', 0) or 0) / _CR_MULT if _CR_MULT else 0
    raw_cw = (spine.get('cache_write', 0) or 0) / _CW_MULT if _CW_MULT else 0
    raw_in = spine.get('fresh_input', 0) or 0
    denom = raw_cr + raw_cw + raw_in
    cache_hit = (raw_cr / denom * 100) if denom else 0
    return total_spend, total_spend / n, cache_hit


# ── stat strip ───────────────────────────────────────────────────────────────

def _strip(data: dict) -> str:
    total_spend, per_session, cache_hit = _spend(data)
    share = data.get('pre_edit_spend_share')
    growth = data.get('avg_context_growth')
    cells = [
        ('a', f'{share:.0%}' if share is not None else 'n/a', 'pre-edit share'),
        ('', f'${total_spend:,.2f}', f'est spend {data["days"]}d'),
        ('g', f'{cache_hit:.0f}%', 'cache hit'),
        ('a', f'{data["avg_reads_before_edit"]:.1f}', 'reads→edit'),
        ('r', f'{growth:.1f}×' if growth is not None else '—', 'ctx growth'),
        ('', f'{data["avg_requests"]:.0f}' if data.get('avg_requests') else '—', 'req/session'),
    ]
    out = []
    for cls, val, lbl in cells:
        out.append(f'<div class="stat"><div class="stat-v {cls}">{_esc(val)}</div>'
                   f'<div class="stat-l">{_esc(lbl)}</div></div>')
    return f'<div class="strip">{"".join(out)}</div>'


# ── headline ─────────────────────────────────────────────────────────────────

def _headline(data: dict) -> str:
    share = data.get('pre_edit_spend_share')
    if share is not None:
        n = data['pre_edit_measured_sessions']
        eff = data.get('pre_edit_eff_total_tokens') or 0
        big = f'{share:.0%}'
        unit = 'pre-edit context share'
        desc = (f'<b>{eff:,.0f} effective input tokens</b> across {n} measured edit '
                f'session{"s" if n != 1 else ""}. {share:.0%} of agent spend goes to '
                f'context-gathering before the first file edit — descriptive, not all '
                f'waste. The avoidable portion is in the findings below.')
    else:
        big, unit = 'n/a', 'pre-edit share not measurable'
        desc = ('No edit sessions with token usage in this window — counts and '
                'estimates below are still valid.')
    return f"""
    <div class="panel"><div class="ph">Headline</div>
      <div class="pb headline">
        <div><div class="hl-big">{_esc(big)}</div><div class="hl-u">{_esc(unit)}</div></div>
        <div class="hl-d">{desc}</div>
      </div></div>"""


# ── coverage ─────────────────────────────────────────────────────────────────

def _coverage(data: dict) -> str:
    total = data['sessions']
    mix = []
    for row in (data.get('projects') or []):
        src, n = row[0], row[1]
        mix.append(f'{src if src in ("cursor", "codex") else "claude"} {n}')
    mix_str = ' · '.join(mix) if mix else 'claude'
    thin = total < 5
    items = [
        (total, 'found'), (data.get('measured_pool_sessions', 0), 'with tokens'),
        (data.get('pre_edit_measured_sessions', 0), 'edit measured'),
        (data.get('read_only_sessions', 0), 'read-only excl'),
        (data.get('parse_failures', 0), 'parse fails'),
    ]
    chips = ''.join(f'<span><b class="mono">{_esc(v)}</b> <span class="dim">{_esc(l)}</span></span>'
                    for v, l in items)
    warn = ('<div class="warnbox">⚠ Small sample — fewer than 5 sessions. Treat shares '
            'as directional, not conclusive.</div>' if thin else '')
    return f"""
    <div class="panel" id="cov"><div class="ph">Coverage &amp; confidence</div>
      <div class="pb covbar">{chips}
        <span class="dim">{_esc(mix_str)}</span>
        <span class="legend"><span class="b measured">● measured</span> <span class="b estimated">● estimated</span> <span class="b count">● count</span></span>
      </div>
      {warn}
      <div class="note">Input-side spend is shown throughout. Output-token spend is reported
        separately (see metrics) and is <b>not</b> in the waterfall or cost model. Cursor
        sessions carry file paths but no token data.</div></div>"""


# ── waterfall ────────────────────────────────────────────────────────────────

def _wf_row(cls: str, tick: str, label: str, width: float, fill_cls: str,
            fill_txt: str, tok: float, pct: str, cost: str) -> str:
    return f"""
      <div class="wf-row {cls}"><span class="wf-l{' root' if cls == 'root' else ''}"><span class="tick">{_esc(tick)}</span>{_esc(label)}</span>
        <div class="wf-bar"><div class="wf-f {fill_cls}" style="width:{max(0, min(100, width)):.2f}%">{_esc(fill_txt)}</div></div>
        <span class="wf-t">{tok:,.0f}</span><span class="wf-p">{_esc(pct)}</span><span class="wf-c">{_esc(cost)}</span></div>"""


def _waterfall(data: dict) -> str:
    tree = data.get('spine_tree')
    spine = data.get('token_spine') or {}
    spine_total = spine.get('total', 0) or 0
    if not tree and spine_total <= 0:
        return ''
    base = data.get('base_price') or _BASE_PRICE
    rows = []
    if tree:
        total = tree['total']
        pool = tree['pool_sessions'] or 1
        comps = sorted(tree['components'], key=lambda c: c['eff'], reverse=True)
        stops, cum = [], 0.0
        for c in comps:
            w = (c['eff'] / total * 100) if total else 0
            stops.append(f'{_COMP_GRAD.get(c["label"], "#5b9dff")} {cum:.1f}% {cum + w:.1f}%')
            cum += w
        grad = f'linear-gradient(90deg,{",".join(stops)})'
        rows.append(f"""
      <div class="wf-row root"><span class="wf-l root"><span class="tick">● </span>eff input</span>
        <div class="wf-bar"><div class="wf-f" style="width:100%;background:{grad}"></div></div>
        <span class="wf-t">{total:,.0f}</span><span class="wf-p">100%</span><span class="wf-c">~${total * base / pool:.3f}/s</span></div>""")
        for i, c in enumerate(comps):
            last = i == len(comps) - 1
            cls = _COMP_CLS.get(c['label'], ('cr', 'crp', 'crq'))
            pct = (c['eff'] / total) if total else 0
            rows.append(_wf_row('s1', '└─ ' if last else '├─ ', c['label'], pct * 100,
                                cls[0], f'{pct * 100:.0f}%', c['eff'], f'{pct * 100:.0f}%',
                                f'~${c["eff"] * base / pool:.3f}/s'))
            if c['label'] != 'cache write' and c['eff'] > 0:
                pre, post = c['pre'], c['eff'] - c['pre']
                stem = '   ' if last else '│  '
                rows.append(_wf_row('s2', stem + '├─ ', 'pre-edit', pre / c['eff'] * 100,
                                    cls[1], f'{pre / c["eff"] * 100:.0f}%', pre,
                                    f'{pre / total * 100:.0f}%', ''))
                rows.append(_wf_row('s2', stem + '└─ ', 'post-edit', post / c['eff'] * 100,
                                    cls[2], f'{post / c["eff"] * 100:.0f}%', post,
                                    f'{post / total * 100:.0f}%', ''))
        sub = (f'measured spine · {pool} edit session{"s" if pool != 1 else ""} · '
               f'children sum to parent · $/s at {data["provider"]} pricing')
    else:
        total = spine_total
        n_all = max(1, data['sessions'])
        comps = sorted([('cache read', spine.get('cache_read', 0)),
                        ('fresh input', spine.get('fresh_input', 0)),
                        ('cache write', spine.get('cache_write', 0))],
                       key=lambda kv: kv[1], reverse=True)
        stops, cum = [], 0.0
        for label, val in comps:
            w = (val / total * 100) if total else 0
            stops.append(f'{_COMP_GRAD.get(label, "#5b9dff")} {cum:.1f}% {cum + w:.1f}%')
            cum += w
        grad = f'linear-gradient(90deg,{",".join(stops)})'
        rows.append(f"""
      <div class="wf-row root"><span class="wf-l root"><span class="tick">● </span>eff input</span>
        <div class="wf-bar"><div class="wf-f" style="width:100%;background:{grad}"></div></div>
        <span class="wf-t">{total:,.0f}</span><span class="wf-p">100%</span><span class="wf-c">~${total * base / n_all:.3f}/s</span></div>""")
        for i, (label, val) in enumerate(comps):
            cls = _COMP_CLS.get(label, ('cr', '', ''))
            pct = (val / total) if total else 0
            rows.append(_wf_row('s1', '└─ ' if i == len(comps) - 1 else '├─ ', label, pct * 100,
                                cls[0], f'{pct * 100:.0f}%', val, f'{pct * 100:.0f}%',
                                f'~${val * base / n_all:.3f}/s'))
        sub = 'all sessions · composition only (no measured edit pool for pre/post)'
    return f"""
    <div class="panel" id="wf"><div class="ph">Token waterfall <span class="sub">{_esc(sub)}</span></div>
      <div class="pb wf">{''.join(rows)}</div></div>"""


# ── retry loops ──────────────────────────────────────────────────────────────

def _retry_loops(data: dict) -> str:
    fc = [c for c in (data.get('top_failed_commands') or []) if c['failures'] > 1]
    if not fc:
        return ''
    rows = ''.join(
        f'<tr><td class="num {_sev(c["failures"], 2, 4)}">{c["failures"]}</td>'
        f'<td class="num">{c["sessions"]}</td><td class="mono">{_esc(c["cmd"])}</td></tr>'
        for c in fc[:10])
    return f"""
    <div class="panel" id="retry"><div class="ph">Retry loops <span class="sub">same command failing repeatedly</span></div>
      <table><thead><tr><th class="r">Fails</th><th class="r">Sessions</th><th>Command</th></tr></thead>
      <tbody>{rows}</tbody></table>
      <div class="note">Drill in → <code>cram audit --layer retries</code></div></div>"""


# ── cost by layer ────────────────────────────────────────────────────────────

def _cost_by_layer(data: dict) -> str:
    costs = data.get('layer_costs') or []
    if not costs:
        return ''
    rows = []
    for r in costs:
        basis = r['basis']
        if basis == 'count':
            tok, cost = '<span class="dim">—</span>', f'{r.get("count_per_session", 0):.1f}/sess'
        else:
            eff, c = r.get('eff_tokens_per_session'), r.get('cost_per_session')
            tok = f'{eff:,.0f}' if eff else '<span class="dim">—</span>'
            cost = f'~${c:.4f}' if c is not None else '<span class="dim">—</span>'
        rows.append(f'<tr><td class="mono">{_esc(r["layer"])}</td><td class="num">{tok}</td>'
                    f'<td class="num">{cost}</td><td><span class="b {basis}">{_esc(basis)}</span></td>'
                    f'<td class="dim">{_esc(r.get("note", ""))}</td></tr>')
    return f"""
    <div class="panel" id="cost"><div class="ph">Cost by waste layer <span class="sub">overlapping diagnostics — do not sum to the spine</span></div>
      <table><thead><tr><th>Layer</th><th class="r">Eff tok/sess</th><th class="r">$/sess</th><th>Basis</th><th>Notes</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""


# ── leaderboard + embedded drilldown ─────────────────────────────────────────

def _drilldown(tl: dict, repo_root: str) -> str:
    """Embedded per-session detail: notable turns, carried, redundant, failed cmds."""
    # show turns that carry a note or a big context jump, capped
    notable = [r for r in tl['rows'] if r['notes'] or abs(r['delta']) > 8000][:8]
    if not notable:
        notable = tl['rows'][:5]
    trows = ''
    for r in notable:
        d = r['delta']
        dcls = 'sev-hi' if d > 8000 else ('sev-md' if d > 0 else 'dim')
        dstr = f'+{d:,}' if d > 0 else (f'{d:,}' if d < 0 else '—')
        note = '; '.join(r['notes'][:2]) if r['notes'] else ''
        ncls = ' style="color:var(--red)"' if note.startswith('✗') else ''
        trows += (f'<tr><td>{r["turn"]}</td><td class="num">{r["input"]:,}</td>'
                  f'<td class="num">{r["cache_read"]:,}</td><td class="num">{r["context"]:,}</td>'
                  f'<td class="num {dcls}">{dstr}</td><td{ncls}>{_esc(note)}</td></tr>')
    chips = [f'peak ctx <b>{tl["peak_context"]:,}</b>']
    if tl.get('carried_read_tokens'):
        chips.append(f'carried <b>{tl["carried_read_tokens"]:,} tok</b>')
    if tl.get('redundant'):
        fp, n = tl['redundant'][0]
        chips.append(f'redundant <b>{_esc(repo_rel(fp, repo_root))} ×{n}</b>')
    for fcmd in (tl.get('failed_commands') or [])[:2]:
        chips.append(f'<span style="color:var(--red)">✗ {_esc(fcmd["cmd"][:50])} ×{fcmd["failures"]}</span>')
    chip_html = ' &nbsp; '.join(f'<span class="dim">{c}</span>' for c in chips)
    return f"""<div class="drill">
        <table class="mini"><thead><tr><th>Turn</th><th class="r">Input</th><th class="r">CacheR</th><th class="r">Context</th><th class="r">Δ</th><th>Note</th></tr></thead>
        <tbody>{trows}</tbody></table>
        <div class="chips">{chip_html}</div></div>"""


def _leaderboard(data: dict, drilldowns: dict, repo_root: str) -> str:
    board = data.get('leaderboard') or []
    if not board:
        return ''
    rows = []
    for s in board:
        sid = str(s.get('session_id', ''))
        sid8 = _esc(sid[:8] or '—')
        src = s.get('source', 'claude')
        badge = 'codex' if src == 'codex' else ('cursor' if src == 'cursor' else 'claude')
        inp, rbe = s.get('input_tokens', 0), s.get('reads_before_edit', 0)
        g = s.get('context_growth_factor')
        cr, cw = s.get('cache_reads', 0), s.get('cache_writes', 0)
        denom = inp + cr + cw
        hit = f'{cr / denom * 100:.0f}%' if denom else '—'
        rows.append(f'<tr><td class="mono">{sid8}</td><td><span class="badge {badge}">{_esc(src)}</span></td>'
                    f'<td class="num">{inp:,}</td><td class="num {_sev(rbe, 8, 15)}">{rbe}</td>'
                    f'<td class="num">{hit}</td><td class="num {_sev(g or 0, 2, 4)}">{f"{g:.1f}×" if g else "—"}</td>'
                    f'<td class="num {_sev(s.get("error_results", 0), 1, 3)}">{s.get("error_results", 0)}</td></tr>')
        tl = drilldowns.get(sid) or drilldowns.get(sid8)
        if tl:
            rows.append(f'<tr class="drill-row"><td colspan="7"><details><summary>{sid8} — '
                        f'per-turn timeline · carried · failed commands</summary>'
                        f'{_drilldown(tl, repo_root)}</details></td></tr>')
    hint = ('expand a row to drill in' if drilldowns
            else 'drill in → cram audit --session &lt;id&gt;')
    return f"""
    <div class="panel" id="board"><div class="ph">Session leaderboard <span class="sub">{hint}</span></div>
      <table><thead><tr><th>Session</th><th>Src</th><th class="r">Input</th><th class="r">Reads→edit</th>
        <th class="r">Cache</th><th class="r">Growth</th><th class="r">Retries</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""


# ── waste layers ─────────────────────────────────────────────────────────────

def _layer_fill_value(layer: str, data: dict, rows: list) -> tuple[float, str]:
    if layer == 'orientation':
        v = data.get('avg_reads_before_edit', 0); return min(1, v / 10), f'{v:.1f} reads/sess'
    if layer == 'repeated':
        n = len(rows); return min(1, n / 10), f'{n} file{"s" if n != 1 else ""}'
    if layer == 'redundant':
        v = data.get('avg_redundant_reads', 0); return min(1, v / 5), f'{v:.1f}/sess'
    if layer == 'churn':
        v = data.get('avg_edit_churn', 0); return min(1, v / 5), f'{v:.1f}/sess'
    if layer == 'carried':
        n = data.get('sessions_with_big_results', 0)
        return min(1, n / max(1, data['sessions'])), f'{n} session{"s" if n != 1 else ""}'
    if layer == 'retries':
        v = data.get('avg_error_results', 0); return min(1, v / 5), f'{v:.1f}/sess'
    return 0, ''


def _layers(data: dict, layers: dict, repo_root: str) -> str:
    from cram.audit import format_layer_row
    blocks = []
    for name in _LAYER_ORDER:
        contrib = layers.get(name) or []
        fill, val = _layer_fill_value(name, data, contrib)
        blocks.append(f'<div class="lay"><span class="lay-n">{_esc(name)}</span>'
                      f'<span class="lay-d">{_esc(_LAYER_DESC[name])}</span>'
                      f'<div class="lay-tr"><div class="lay-fl" style="width:{fill * 100:.0f}%"></div></div>'
                      f'<span class="lay-v">{_esc(val)}</span></div>')
        if contrib:
            items = ''.join(f'<tr><td>{_esc(format_layer_row(name, r, repo_root))}</td></tr>'
                            for r in contrib[:5])
            blocks.append(f'<details><summary>{_esc(name)} · top contributors '
                          f'({min(len(contrib), 5)} of {len(contrib)})</summary>'
                          f'<div class="drill"><table class="mini"><tbody>{items}</tbody></table></div></details>')
    return f"""
    <div class="panel" id="layers"><div class="ph">Waste layers <span class="sub">diagnostics &amp; top contributors</span></div>
      {''.join(blocks)}</div>"""


# ── findings ─────────────────────────────────────────────────────────────────

def _findings(data: dict) -> str:
    findings = data.get('findings') or []
    if not findings:
        return ''
    cards = []
    for fd in findings:
        verify = _VERIFY.get(fd['id'])
        ver = (f'<div class="fa"><span class="tag ver">VERIFY</span><span>{_code(verify)}</span></div>'
               if verify else '')
        cards.append(f"""
        <div class="find"><div class="find-t"><span class="fid">{_esc(fd['id'])}</span>
          <span>{_code(fd['evidence'])}</span></div>
          <div class="find-a"><div class="fa"><span class="tag fix">FIX</span><span>{_code(fd['fix'])}</span></div>{ver}</div></div>""")
    return f"""
    <div class="panel" id="find"><div class="ph">Findings <span class="sub">{len(findings)}</span></div>
      <div class="pb">{''.join(cards)}</div></div>"""


# ── context on/off (referee A/B) ─────────────────────────────────────────────

def _context_ab(data: dict) -> str:
    seg = data.get('context_mode_segment')
    if not seg:
        return ''
    on, off = seg['on'], seg['off']

    def delta(a, b):
        # lower is better for these waste metrics → negative Δ is good
        if not b:
            return '—', ''
        pct = (a - b) / b * 100
        cls = 'delta-good' if pct < 0 else ('delta-bad' if pct > 0 else '')
        return f'{pct:+.0f}%', cls

    def row(label, key, fmt, money=False):
        a, b = on.get(key), off.get(key)
        if a is None or b is None:
            return ''
        d, cls = delta(a, b)
        fa = (f'${a:{fmt}}' if money else f'{a:{fmt}}')
        fb = (f'${b:{fmt}}' if money else f'{b:{fmt}}')
        return (f'<tr><td>{_esc(label)}</td><td class="num">{fa}</td>'
                f'<td class="num">{fb}</td><td class="num {cls}">{d}</td></tr>')
    rows = ''.join([
        row('Reads before first edit', 'avg_reads_before_edit', '.1f'),
        row('Carried result cost/session', 'carried_cost_per_session', '.4f', money=True),
        row('Sessions w/ oversized result', 'sessions_with_big_results', 'd'),
        row('Avg peak context (tokens)', 'avg_peak_context', ',.0f'),
    ])
    if not rows:
        return ''
    return f"""
    <div class="panel" id="ab"><div class="ph">Context layer: on vs off <span class="sub">observational split — not a controlled trial</span></div>
      <table class="ab"><thead><tr><th>Metric</th><th class="r">ctx on ({on.get('sessions', 0)})</th>
        <th class="r">ctx off ({off.get('sessions', 0)})</th><th class="r">Δ</th></tr></thead>
      <tbody>{rows}</tbody></table>
      <div class="warnbox">⚠ Observational, not causal — sessions self-selected into ctx-on.
        Confirm with <code>cram rig --providers baseline,cram</code> before treating as causal.</div></div>"""


# ── key metrics ──────────────────────────────────────────────────────────────

def _metric(val, label, basis, cls=''):
    return (f'<div class="m"><div class="m-v {cls}">{_esc(val)}</div>'
            f'<div class="m-l">{_esc(label)}</div><div class="m-b">{_esc(basis)}</div></div>')


def _metrics(data: dict) -> str:
    total_spend, per_session, cache_hit = _spend(data)
    cards = [
        _metric(f'${total_spend:,.2f}', f'Est input-side spend, {data["days"]}d', 'measured × price'),
        _metric(f'${per_session:.4f}', 'Avg cost / session', f'{data["sessions"]} sessions'),
        _metric(f'{cache_hit:.0f}%', 'Cache hit rate', 'cache read / total', 'g'),
        _metric(f'{data["avg_reads_before_edit"]:.1f}', 'Reads before first edit', 'measured avg'),
        _metric(f'{data["avg_ratio"]:.1f}×', 'Read-to-edit ratio', f'measured · {data["ratio_band"]}'),
    ]
    if data.get('avg_context_growth') is not None:
        g = data['avg_context_growth']
        cards.append(_metric(f'{g:.1f}×', 'Context growth, peak/start', 'measured', 'r' if g > 3 else ''))
    if data.get('peak_context'):
        pk = data['peak_context']
        cards.append(_metric(f'{pk / 200_000 * 100:.0f}%', 'Ctx window used', f'{pk:,} / 200k'))
    if data.get('avg_requests'):
        cards.append(_metric(f'{data["avg_requests"]:.0f}', 'Requests / session', 'measured'))
    return f"""
    <div class="panel" id="metrics"><div class="ph">Key metrics</div>
      <div class="pb" style="padding:0"><div class="mgrid">{''.join(cards)}</div></div></div>"""


# ── assembly ─────────────────────────────────────────────────────────────────

def render_report_html(data: dict, layers: dict, repo_root: str,
                       drilldowns: dict | None = None) -> str:
    """Return a standalone HTML report for a collect_audit() result.

    layers   maps each waste-layer name to its ranked contributor rows.
    drilldowns maps a session_id (or its 8-char prefix) to a
    derive_session_timeline() dict, embedded under the matching leaderboard row.
    """
    name = os.path.basename(repo_root.rstrip(os.sep)) or repo_root
    today = datetime.date.today().isoformat()
    drilldowns = drilldowns or {}

    # sidebar links — only for sections that render
    nav = [('sum', 'Summary'), ('cov', 'Coverage'), ('wf', 'Token waterfall')]
    if _retry_loops(data):
        nav.append(('retry', 'Retry loops'))
    nav.append(('cost', 'Cost by layer'))
    nav += [('board', 'Leaderboard'), ('layers', 'Waste layers'), ('find', 'Findings')]
    if _context_ab(data):
        nav.append(('ab', 'Context on/off'))
    nav.append(('metrics', 'Key metrics'))
    side = ''.join(f'<a href="#{i}" class="{"on" if k == 0 else ""}">{_esc(t)}</a>'
                   for k, (i, t) in enumerate(nav))

    body = ''.join([
        f'<section id="sum">{_strip(data)}{_headline(data)}</section>',
        _coverage(data), _waterfall(data), _retry_loops(data), _cost_by_layer(data),
        _leaderboard(data, drilldowns, repo_root), _layers(data, layers, repo_root),
        _findings(data), _context_ab(data), _metrics(data),
    ])

    return _SHELL.format(
        css=_CSS, js=_JS, repo=_esc(name), days=data['days'],
        sessions=data['sessions'], today=_esc(today), provider=_esc(data['provider']),
        side=side, body=body)


# ── static assets ────────────────────────────────────────────────────────────

_CSS = r"""
:root{--bg:#0a0c10;--panel:#11141a;--panel2:#171b22;--panel3:#1c212a;--border:#222831;--border2:#2c333d;--muted:#646e7e;--text:#bcc3cd;--heading:#f0f3f6;--accent:#5b9dff;--green:#3fb950;--amber:#d29922;--red:#f85149;--cyan:#56b6c2;--mono:'JetBrains Mono','SF Mono',ui-monospace,monospace;--sans:-apple-system,BlinkMacSystemFont,'Inter',system-ui,sans-serif;--radius:6px}
[data-theme="light"]{--bg:#f6f7f9;--panel:#fff;--panel2:#f1f3f5;--panel3:#e9ecef;--border:#e1e4e8;--border2:#d0d7de;--muted:#8b949e;--text:#3a414a;--heading:#0a0c10;--accent:#3b6db8;--green:#2c7d38;--amber:#9a6700;--red:#cf222e;--cyan:#357b85}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:13px;line-height:1.55}
.topbar{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:12px;padding:9px 18px;background:var(--panel);border-bottom:1px solid var(--border)}
.brand{font-family:var(--mono);font-size:12px;font-weight:700;color:var(--accent);letter-spacing:.03em}
.crumb{font-size:13px;color:var(--heading);font-weight:600}.crumb .dim{color:var(--muted);font-weight:400}
.tb-right{margin-left:auto;display:flex;align-items:center;gap:16px}
.tb-meta{display:flex;gap:14px;font-family:var(--mono);font-size:11px;color:var(--muted)}
.toggle{font-family:var(--mono);font-size:11px;background:var(--panel2);border:1px solid var(--border2);color:var(--muted);border-radius:5px;padding:3px 9px;cursor:pointer}
.toggle:hover{color:var(--accent);border-color:var(--accent)}
.shell{display:grid;grid-template-columns:188px 1fr;max-width:1180px;margin:0 auto}
.side{position:sticky;top:43px;height:calc(100vh - 43px);overflow-y:auto;padding:18px 0;border-right:1px solid var(--border)}
.side-h{font-size:9.5px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);padding:0 18px 6px}
.side a{display:block;padding:5px 18px;font-size:12px;color:var(--muted);text-decoration:none;border-left:2px solid transparent}
.side a:hover{color:var(--heading);background:var(--panel2)}
.side a.on{color:var(--accent);border-left-color:var(--accent);background:var(--panel2)}
.main{padding:20px 26px 80px;min-width:0}
.strip{display:grid;grid-template-columns:repeat(6,1fr);gap:1px;background:var(--border);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;margin-bottom:22px}
.stat{background:var(--panel);padding:11px 13px}
.stat-v{font-family:var(--mono);font-size:19px;font-weight:700;color:var(--heading);line-height:1.1}
.stat-v.g{color:var(--green)}.stat-v.r{color:var(--red)}.stat-v.a{color:var(--amber)}
.stat-l{font-size:9.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-top:4px}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:14px;scroll-margin-top:54px}
.ph{display:flex;align-items:center;gap:8px;padding:9px 13px;border-bottom:1px solid var(--border);font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.ph .sub{margin-left:auto;font-family:var(--mono);font-weight:400;text-transform:none;letter-spacing:0;color:var(--muted);font-size:11px}
.pb{padding:13px}
.note{font-size:11px;color:var(--muted);padding:8px 13px;border-top:1px solid var(--border);line-height:1.5}.note b{color:var(--text)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);padding:6px 10px;border-bottom:1px solid var(--border2);background:var(--panel2)}
th.r{text-align:right}
td{padding:7px 10px;border-bottom:1px solid var(--border)}
tr:last-child td{border-bottom:none}
tbody tr:hover td{background:var(--panel2)}
.mono{font-family:var(--mono);font-size:12px;color:var(--heading)}
.num{text-align:right;font-family:var(--mono);font-size:12px}
.dim{color:var(--muted)}
.sev-hi{color:var(--red);font-weight:600}.sev-md{color:var(--amber)}.sev-lo{color:var(--muted)}
.badge{font-family:var(--mono);font-size:10px;padding:1px 6px;border-radius:3px;border:1px solid var(--border2);color:var(--muted)}
.badge.claude{color:var(--accent);border-color:#26415f}.badge.codex{color:var(--green);border-color:#214a2b}
.b{font-family:var(--mono);font-size:10px;padding:1px 7px;border-radius:3px;display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
.b.measured{color:var(--green);background:rgba(63,185,80,.08);border:1px solid rgba(63,185,80,.22)}
.b.estimated{color:var(--amber);background:rgba(210,153,34,.08);border:1px solid rgba(210,153,34,.22)}
.b.count{color:var(--muted);background:var(--panel2);border:1px solid var(--border2)}
code{font-family:var(--mono);font-size:11px;background:var(--panel2);border:1px solid var(--border);padding:0 4px;border-radius:3px;color:var(--text)}
.headline{display:grid;grid-template-columns:auto 1fr;gap:22px;align-items:center}
.hl-big{font-family:var(--mono);font-size:46px;font-weight:700;color:var(--accent);line-height:1}
.hl-u{font-size:12px;color:var(--muted);margin-top:5px}
.hl-d{font-size:13px;color:var(--text)}.hl-d b{color:var(--heading)}
.covbar{display:flex;gap:22px;flex-wrap:wrap;font-family:var(--mono);font-size:11.5px;align-items:center}
.covbar .legend{margin-left:auto;display:flex;gap:6px}
.warnbox{background:rgba(210,153,34,.06);border:1px solid rgba(210,153,34,.22);color:var(--amber);font-size:11.5px;padding:8px 12px;border-radius:5px;margin:10px 13px}
.wf{font-family:var(--mono);font-size:12px}
.wf-row{display:grid;grid-template-columns:128px 1fr 100px 46px 78px;align-items:center;gap:10px;padding:3px 0}
.wf-row.s1{padding-left:14px}.wf-row.s2{padding-left:30px;opacity:.85}
.wf-l{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.wf-l.root{color:var(--heading);font-weight:600}
.tick{color:var(--border2);white-space:pre}
.wf-bar{height:17px;background:var(--panel2);border:1px solid var(--border);border-radius:2px;overflow:hidden}
.wf-row.s2 .wf-bar{height:12px}
.wf-f{height:100%;display:flex;align-items:center;padding:0 6px;font-size:9px;font-weight:700;color:#0a0c10;white-space:nowrap}
.cr{background:#5b9dff}.crp{background:#3b6db8}.crq{background:#27466e}.fr{background:#3fb950}.frp{background:#2c7d38}.frq{background:#1c5325}.cw{background:#d29922}
.wf-t{text-align:right;color:var(--text)}.wf-p{text-align:right;color:var(--muted)}.wf-c{text-align:right;color:var(--muted);font-size:11px}
.wf-row.root .wf-t{color:var(--heading);font-weight:600}.wf-row.root .wf-c{color:var(--heading)}
.find{border:1px solid var(--border);border-radius:5px;margin-bottom:8px;overflow:hidden}.find:last-child{margin-bottom:0}
.find-t{padding:10px 12px;display:flex;gap:10px;align-items:baseline}
.fid{font-family:var(--mono);font-size:11px;color:var(--accent);background:rgba(91,157,255,.08);border:1px solid #26415f;padding:1px 7px;border-radius:3px;flex-shrink:0}
.find-a{border-top:1px solid var(--border);display:flex}
.fa{flex:1;padding:8px 12px;font-size:11.5px;color:var(--muted);border-right:1px solid var(--border);display:flex;gap:7px}.fa:last-child{border-right:none}
.tag{font-family:var(--mono);font-size:9px;padding:1px 6px;border-radius:3px;flex-shrink:0;height:fit-content;margin-top:1px}
.tag.fix{color:var(--green);border:1px solid rgba(63,185,80,.28)}.tag.ver{color:var(--accent);border:1px solid rgba(91,157,255,.28)}
details{border-top:1px solid var(--border)}
.drill-row td{padding:0!important}.drill-row details{border-top:none}
summary{cursor:pointer;padding:7px 12px;font-family:var(--mono);font-size:11px;color:var(--muted);list-style:none;user-select:none}
summary:hover{color:var(--accent)}summary::before{content:'▸';color:var(--border2);margin-right:7px}
details[open] summary::before{content:'▾'}
.drill{padding:0 12px 12px}
.mini{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11px}
.mini th{background:transparent;padding:4px 8px;font-size:9px}.mini td{padding:3px 8px;border-bottom:1px solid var(--border)}
.chips{margin-top:8px;display:flex;gap:18px;flex-wrap:wrap;font-size:11px}.chips b{color:var(--heading)}
.lay{display:grid;grid-template-columns:96px 200px 1fr 120px;align-items:center;gap:12px;padding:7px 13px;border-bottom:1px solid var(--border)}
.lay-n{font-family:var(--mono);font-size:11px;color:var(--heading)}.lay-d{font-size:11px;color:var(--muted)}
.lay-tr{height:5px;background:var(--panel3);border-radius:3px;overflow:hidden}.lay-fl{height:100%;background:var(--accent);opacity:.75;border-radius:3px}
.lay-v{font-family:var(--mono);font-size:11px;color:var(--muted);text-align:right}
.mgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--border);border-radius:var(--radius);overflow:hidden}
.m{background:var(--panel);padding:12px 14px}
.m-v{font-family:var(--mono);font-size:18px;font-weight:700;color:var(--heading)}.m-v.g{color:var(--green)}.m-v.r{color:var(--red)}
.m-l{font-size:10.5px;color:var(--muted);margin-top:4px}.m-b{font-family:var(--mono);font-size:9.5px;color:var(--border2);margin-top:4px}
.ab .delta-good{color:var(--green)}.ab .delta-bad{color:var(--red)}
.foot{margin-top:26px;padding:12px 0;border-top:1px solid var(--border);font-family:var(--mono);font-size:11px;color:var(--muted);display:flex;justify-content:space-between;align-items:center}
.foot .u{color:var(--green)}.foot .p{color:var(--accent)}
.cur{display:inline-block;width:7px;height:13px;background:var(--green);vertical-align:middle;animation:bl 1.1s steps(1) infinite}
@keyframes bl{50%{opacity:0}}
@media(max-width:880px){.shell{grid-template-columns:1fr}.side{position:static;height:auto;border-right:none;border-bottom:1px solid var(--border);display:flex;flex-wrap:wrap;padding:8px 0}.side-h{display:none}.side a{border-left:none;padding:5px 12px}.strip{grid-template-columns:repeat(3,1fr)}.mgrid{grid-template-columns:repeat(2,1fr)}.headline{grid-template-columns:1fr}.wf-row{grid-template-columns:104px 1fr 78px 70px}.wf-p{display:none}.lay{grid-template-columns:90px 1fr 90px}.lay-d{display:none}}
"""

_JS = r"""
function tt(){var h=document.documentElement,b=document.getElementById('tg');if(h.dataset.theme==='dark'){h.dataset.theme='light';b.textContent='◑ dark';}else{h.dataset.theme='dark';b.textContent='◐ light';}}
var ss=document.querySelectorAll('section[id],.panel[id]'),ls=document.querySelectorAll('.side a');
var ob=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){ls.forEach(function(l){l.classList.remove('on')});var a=document.querySelector('.side a[href="#'+e.target.id+'"]');if(a)a.classList.add('on');}})},{rootMargin:'-15% 0px -75% 0px'});
ss.forEach(function(s){ob.observe(s)});
"""

_SHELL = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>cram audit · {repo}</title>
<style>{css}</style>
</head>
<body>
<div class="topbar">
  <span class="brand">◆ cram</span>
  <span class="crumb">{repo} <span class="dim">/ agent session audit</span></span>
  <div class="tb-right">
    <div class="tb-meta"><span>{sessions} sessions</span><span>{days}d</span><span>{today}</span></div>
    <button class="toggle" id="tg" onclick="tt()">◐ light</button>
  </div>
</div>
<div class="shell">
  <nav class="side"><div class="side-h">Report</div>{side}</nav>
  <main class="main">{body}
    <div class="foot">
      <span><span class="u">cram</span><span class="p">@{repo}</span>$ cram audit --report-html<span class="cur"></span></span>
      <span>{provider} pricing · conservative methodology</span>
    </div>
  </main>
</div>
<script>{js}</script>
</body>
</html>
"""
