"""Self-contained HTML report for `cram audit --report-html`.

render_report_html() is a pure function over the same collect_audit() dict the
markdown report uses, plus a per-layer drilldown map. It emits one standalone
HTML file — no external CSS, fonts, or JS — so it travels: open locally, attach
to a PR, drop in Slack. It is the visual surface of what cram can pull for a
developer: the token waterfall, the findings with fix/verify, the heaviest
sessions, and the waste layers with their concrete top contributors.

Every number traces to a measured/estimated basis, exactly like the markdown
report — this renderer only changes the presentation, never the claims.
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

# Component → bar colour class (CSS vars defined per theme below).
_COMP_CLASS = {
    'cache read':  ('wf-cr', 'wf-cr-pre', 'wf-cr-po'),
    'fresh input': ('wf-fr', 'wf-fr-pre', 'wf-fr-po'),
    'cache write': ('wf-cw', 'wf-cw', 'wf-cw'),
}
_COMP_VAR = {'cache read': 'var(--wf-cr)', 'fresh input': 'var(--wf-fr)',
             'cache write': 'var(--wf-cw)'}

# Layer → (human description, how to read the drilldown rows).
_LAYER_DESC = {
    'orientation': 'reads before first edit',
    'repeated':    'files re-read across sessions',
    'redundant':   'files re-read within a session',
    'churn':       'files re-edited within a session',
    'carried':     'oversized tool output re-read every turn',
    'retries':     'failed tool calls per session',
}
_LAYER_ORDER = ('orientation', 'repeated', 'redundant', 'churn', 'carried', 'retries')


# ── small helpers ────────────────────────────────────────────────────────────

def _esc(s) -> str:
    return html.escape(str(s))


def _code(text: str) -> str:
    """Escape, then turn `backtick spans` into <code> elements."""
    return re.sub(r'`([^`]+)`', r'<code>\1</code>', html.escape(text))


def _heat(value: float, mid: float, hi: float) -> str:
    """CSS class for a leaderboard cell: lo/md/hi by threshold."""
    if value >= hi:
        return 'heat-hi'
    if value >= mid:
        return 'heat-md'
    return 'heat-lo'


# ── section renderers ────────────────────────────────────────────────────────

def _headline(data: dict) -> str:
    total = data['sessions']
    if data['pre_edit_spend_share'] is not None:
        share = data['pre_edit_spend_share']
        n_meas = data['pre_edit_measured_sessions']
        eff = data['pre_edit_eff_total_tokens'] or 0
        prelim = ('<span class="tag">preliminary</span>'
                  if data.get('pre_edit_preliminary') else '')
        desc = (f'<strong>{eff:,.0f} effective input tokens</strong> across '
                f'{n_meas} measured edit session{"s" if n_meas != 1 else ""}. '
                f'{int(round(share * 100))}% of agent spend goes to '
                f'context-gathering before the first file edit — descriptive, '
                f'not all waste. The avoidable portion is in the findings below.')
        stat = f'<em>{share:.0%}</em>'
        unit = 'pre-edit context share'
    else:
        stat = '<em>n/a</em>'
        unit = 'pre-edit share not measurable'
        prelim = ''
        desc = ('No edit sessions with token usage in this window — the '
                'pre-edit share needs measured edit sessions. Estimates and '
                'counts below are still valid.')

    tags = [
        f'<span class="tag">{total} session{"s" if total != 1 else ""} total</span>',
        f'<span class="tag">{data["edit_sessions"]} edit sessions</span>',
    ]
    if data['read_only_sessions']:
        tags.append(f'<span class="tag">{data["read_only_sessions"]} read-only excluded</span>')
    if data['pre_edit_spend_share'] is not None:
        tags.append(f'<span class="tag hi">{data["pre_edit_spend_share"]:.0%} pre-edit</span>')
    if prelim:
        tags.append(prelim)

    return f"""
    <section class="section" id="headline">
      <div class="section-label">Headline</div>
      <div class="headline-grid">
        <div>
          <div class="hl-stat">{stat}</div>
          <div class="hl-unit">{_esc(unit)}</div>
        </div>
        <div>
          <div class="hl-desc">{desc}</div>
          <div class="tags">{''.join(tags)}</div>
        </div>
      </div>
    </section>"""


def _wf_row(level: int, glyph: str, label: str, pct_of_total: float,
            eff: float, fill_var: str, fill_label: str) -> str:
    width = max(0.0, min(100.0, pct_of_total * 100))
    return f"""
        <div class="wf-row level-{level}">
          <div class="wf-lbl{' root' if level == 0 else ''}"><span class="wf-tree-icon">{_esc(glyph)}</span>{_esc(label)}</div>
          <div class="wf-track"><div class="wf-fill" style="width:{width:.2f}%; background:{fill_var};">{_esc(fill_label)}</div></div>
          <div class="wf-tok">{eff:,.0f}</div>
          <div class="wf-pct">{width:.0f}%</div>
        </div>"""


def _waterfall(data: dict) -> str:
    tree = data.get('spine_tree')
    spine = data.get('token_spine') or {}
    spine_total = spine.get('total', 0) or 0
    if not tree and spine_total <= 0:
        return ''

    rows = []
    if tree:
        total = tree['total']
        comps = sorted(tree['components'], key=lambda c: c['eff'], reverse=True)
        # total bar: gradient across components in display order
        stops, cum = [], 0.0
        for c in comps:
            w = (c['eff'] / total * 100) if total else 0
            var = _COMP_VAR.get(c['label'], 'var(--wf-cr)')
            stops.append(f'{var} {cum:.2f}%')
            cum += w
            stops.append(f'{var} {cum:.2f}%')
        grad = f'linear-gradient(90deg, {", ".join(stops)})'
        rows.append(f"""
        <div class="wf-row level-0">
          <div class="wf-lbl root"><span class="wf-tree-icon">●</span>eff input</div>
          <div class="wf-track"><div class="wf-fill" style="width:100%; background:{grad};"></div></div>
          <div class="wf-tok">{total:,.0f}</div>
          <div class="wf-pct">100%</div>
        </div>
        <div class="wf-sep"></div>""")

        for i, c in enumerate(comps):
            last_comp = i == len(comps) - 1
            cls = _COMP_CLASS.get(c['label'], ('wf-cr', 'wf-cr-pre', 'wf-cr-po'))
            pct = (c['eff'] / total) if total else 0
            glyph = '└─' if last_comp else '├─'
            rows.append(_wf_row(1, glyph, c['label'], pct, c['eff'],
                                f'var(--{cls[0]})', f'{pct * 100:.0f}%'))
            # pre/post-edit split — only when the component carries it (cache
            # write has no pre/post in the model, so skip the sub-rows there).
            if c['label'] != 'cache write' and c['eff'] > 0:
                pre, post = c['pre'], c['eff'] - c['pre']
                stem = '  ' if last_comp else '│ '
                rows.append(_wf_row(2, stem + '├─', 'pre-edit', pre / total, pre,
                                    f'var(--{cls[1]})', f'{pre / total * 100:.0f}%'))
                rows.append(_wf_row(2, stem + '└─', 'post-edit', post / total, post,
                                    f'var(--{cls[2]})', f'{post / total * 100:.0f}%'))
            if not last_comp:
                rows.append('<div class="wf-sep"></div>')
        note = (f'over {tree["pool_sessions"]} measured edit '
                f'session{"s" if tree["pool_sessions"] != 1 else ""} · children sum to their parent')
    else:
        total = spine_total
        comps = sorted(
            [('cache read', spine.get('cache_read', 0)),
             ('fresh input', spine.get('fresh_input', 0)),
             ('cache write', spine.get('cache_write', 0))],
            key=lambda kv: kv[1], reverse=True)
        stops, cum = [], 0.0
        for label, val in comps:
            w = (val / total * 100) if total else 0
            var = _COMP_VAR.get(label, 'var(--wf-cr)')
            stops.append(f'{var} {cum:.2f}%'); cum += w; stops.append(f'{var} {cum:.2f}%')
        grad = f'linear-gradient(90deg, {", ".join(stops)})'
        rows.append(f"""
        <div class="wf-row level-0">
          <div class="wf-lbl root"><span class="wf-tree-icon">●</span>eff input</div>
          <div class="wf-track"><div class="wf-fill" style="width:100%; background:{grad};"></div></div>
          <div class="wf-tok">{total:,.0f}</div>
          <div class="wf-pct">100%</div>
        </div>
        <div class="wf-sep"></div>""")
        for i, (label, val) in enumerate(comps):
            cls = _COMP_CLASS.get(label, ('wf-cr', '', ''))
            pct = (val / total) if total else 0
            glyph = '└─' if i == len(comps) - 1 else '├─'
            rows.append(_wf_row(1, glyph, label, pct, val,
                                f'var(--{cls[0]})', f'{pct * 100:.0f}%'))
        note = 'all sessions · composition only (no measured edit pool for pre/post)'

    # estimated overlays strip
    overlays = []
    overlays.append(f'orientation reads ~${data["orient_cost_per_session"]:.4f}/session')
    if data.get('sessions_with_big_results'):
        overlays.append(f'carried output ~${data["carried_cost_per_session"]:.4f}/session')
    if data.get('avg_redundant_reads', 0) >= 0.5:
        overlays.append(f'redundant reads {data["avg_redundant_reads"]:.1f}/session')

    return f"""
    <section class="section" id="waterfall">
      <div class="section-label">Token waterfall — measured spine</div>
      <div class="wf-note">{_esc(note)}</div>
      <div class="wf-tree">{''.join(rows)}</div>
      <div class="wf-overlays"><strong>Estimated overlays</strong> — modelled, not additive to the spine: &nbsp;{' &nbsp;·&nbsp; '.join(_esc(o) for o in overlays)}</div>
    </section>"""


def _findings(data: dict) -> str:
    findings = data.get('findings') or []
    if not findings:
        return ''
    cards = []
    for fd in findings:
        verify = _VERIFY.get(fd['id'])
        verify_html = (f"""
          <div class="finding-action"><span class="al verify">verify</span><span>{_code(verify)}</span></div>"""
                       if verify else '')
        cards.append(f"""
      <div class="finding">
        <div class="finding-head">
          <span class="finding-id">{_esc(fd['id'])}</span>
          <span class="finding-evidence">{_code(fd['evidence'])}</span>
        </div>
        <div class="finding-actions">
          <div class="finding-action"><span class="al fix">fix</span><span>{_code(fd['fix'])}</span></div>{verify_html}
        </div>
      </div>""")
    return f"""
    <section class="section" id="findings">
      <div class="section-label">Findings ({len(findings)})</div>
      <div class="findings">{''.join(cards)}</div>
    </section>"""


def _leaderboard(data: dict) -> str:
    board = data.get('leaderboard') or []
    if not board:
        return ''
    rows = []
    for s in board:
        sid = _esc(str(s.get('session_id', ''))[:8] or '—')
        src = s.get('source', 'claude')
        badge = 'codex' if src == 'codex' else ('cursor' if src == 'cursor' else 'claude')
        inp = s.get('input_tokens', 0)
        rbe = s.get('reads_before_edit', 0)
        growth = s.get('context_growth_factor')
        growth_s = f'{growth:.1f}×' if growth else '—'
        retries = s.get('error_results', 0)
        cr, cw = s.get('cache_reads', 0), s.get('cache_writes', 0)
        denom = inp + cr + cw
        hit = f'{cr / denom * 100:.0f}%' if denom else '—'
        rows.append(f"""
          <tr>
            <td class="mono">{sid}</td>
            <td><span class="badge {badge}">{_esc(src)}</span></td>
            <td class="num">{inp:,}</td>
            <td class="num {_heat(rbe, 8, 15)}">{rbe}</td>
            <td class="num">{hit}</td>
            <td class="num {_heat(growth or 0, 2, 4)}">{growth_s}</td>
            <td class="num {_heat(retries, 1, 3)}">{retries}</td>
          </tr>""")
    return f"""
    <section class="section" id="leaderboard">
      <div class="section-label">Session leaderboard</div>
      <table>
        <thead><tr>
          <th>Session</th><th>Source</th><th class="r">Input tok</th>
          <th class="r">Reads&rarr;edit</th><th class="r">Cache hit</th>
          <th class="r">Ctx growth</th><th class="r">Retries</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
      <div class="drill-hint">drill in &rarr; <code>cram audit --session &lt;id&gt;</code></div>
    </section>"""


def _layer_fill_and_value(layer: str, data: dict, rows: list) -> tuple[float, str]:
    """Bar fill fraction (0..1, bounded) and the human value label per layer."""
    if layer == 'orientation':
        v = data.get('avg_reads_before_edit', 0)
        return min(1.0, v / 10), f'{v:.1f} reads/session'
    if layer == 'repeated':
        n = len(rows)
        return min(1.0, n / 10), f'{n} file{"s" if n != 1 else ""} re-read'
    if layer == 'redundant':
        v = data.get('avg_redundant_reads', 0)
        return min(1.0, v / 5), f'{v:.1f} extra reads/session'
    if layer == 'churn':
        v = data.get('avg_edit_churn', 0)
        return min(1.0, v / 5), f'{v:.1f} re-edits/session'
    if layer == 'carried':
        n = data.get('sessions_with_big_results', 0)
        total = max(1, data.get('sessions', 1))
        return min(1.0, n / total), f'{n} session{"s" if n != 1 else ""} affected'
    if layer == 'retries':
        v = data.get('avg_error_results', 0)
        return min(1.0, v / 5), f'{v:.1f} failures/session'
    return 0.0, ''


def _layers(data: dict, layers: dict, repo_root: str) -> str:
    from cram.audit import format_layer_row  # local import avoids cycle at import time
    rows_html = []
    for name in _LAYER_ORDER:
        contributors = layers.get(name) or []
        fill, value = _layer_fill_and_value(name, data, contributors)
        drill = ''
        if contributors:
            items = ''.join(
                f'<div class="drill-row">{_esc(format_layer_row(name, r, repo_root))}</div>'
                for r in contributors[:5])
            drill = f"""
        <details class="layer-details">
          <summary>top contributors ({min(len(contributors), 5)} of {len(contributors)})</summary>
          <div class="drill-list">{items}</div>
        </details>"""
        rows_html.append(f"""
      <div class="layer-block">
        <div class="layer-row">
          <div class="l-name">{_esc(name)}</div>
          <div class="l-desc">{_esc(_LAYER_DESC[name])}</div>
          <div class="l-track"><div class="l-fill" style="width:{fill * 100:.0f}%"></div></div>
          <div class="l-val">{_esc(value)}</div>
        </div>{drill}
      </div>""")
    return f"""
    <section class="section" id="layers">
      <div class="section-label">Waste layers</div>
      <div class="layers">{''.join(rows_html)}</div>
    </section>"""


def _metric(val: str, label: str, basis: str, cls: str = '') -> str:
    return f"""
        <div class="metric">
          <div class="m-val {cls}">{_esc(val)}</div>
          <div class="m-label">{_esc(label)}</div>
          <div class="m-basis">{_esc(basis)}</div>
        </div>"""


def _metrics(data: dict) -> str:
    spine = data.get('token_spine') or {}
    spine_total = spine.get('total', 0) or 0
    total_spend = spine_total * _BASE_PRICE
    n = max(1, data['sessions'])
    per_session = total_spend / n
    # cache hit rate from raw tokens recovered out of the effective spine
    raw_cr = (spine.get('cache_read', 0) or 0) / _CR_MULT if _CR_MULT else 0
    raw_cw = (spine.get('cache_write', 0) or 0) / _CW_MULT if _CW_MULT else 0
    raw_in = spine.get('fresh_input', 0) or 0
    raw_denom = raw_cr + raw_cw + raw_in
    cache_hit = (raw_cr / raw_denom * 100) if raw_denom else 0

    cards = [
        _metric(f'${total_spend:,.2f}', f'Est. input-side spend, last {data["days"]}d',
                'measured tokens × price'),
        _metric(f'${per_session:.4f}', 'Avg cost per session', f'{data["sessions"]} sessions'),
        _metric(f'{cache_hit:.0f}%', 'Cache hit rate', 'cache read / total input', 'green'),
        _metric(f'{data["avg_reads_before_edit"]:.1f}', 'Reads before first edit', 'measured avg'),
        _metric(f'{data["avg_ratio"]:.1f}×', 'Read-to-edit ratio', f'measured · {data["ratio_band"]}'),
    ]
    if data.get('avg_context_growth') is not None:
        g = data['avg_context_growth']
        cards.append(_metric(f'{g:.1f}×', 'Context growth, peak/start', 'measured avg',
                             'red' if g > 3 else ''))
    if data.get('peak_context'):
        pk = data['peak_context']
        cards.append(_metric(f'{pk / 1000:.1f}k', 'Peak context (tokens)',
                             f'{pk / 200_000 * 100:.0f}% of 200k window'))
    if data.get('avg_requests'):
        cards.append(_metric(f'{data["avg_requests"]:.0f}', 'Requests per session', 'measured'))

    return f"""
    <section class="section" id="metrics">
      <div class="section-label">Key metrics</div>
      <div class="metrics">{''.join(cards)}</div>
    </section>"""


def render_report_html(data: dict, layers: dict, repo_root: str) -> str:
    """Return a standalone HTML report for a collect_audit() result.

    layers maps each waste-layer name to its ranked contributor rows
    (as produced by collect_layer / _layer_rows). Pass {} to omit drilldowns.
    """
    name = os.path.basename(repo_root.rstrip(os.sep)) or repo_root
    today = datetime.date.today().isoformat()
    total = data['sessions']

    body = ''.join([
        _headline(data),
        _waterfall(data),
        _findings(data),
        _leaderboard(data),
        _layers(data, layers, repo_root),
        _metrics(data),
    ])

    return _SHELL.format(
        css=_CSS, js=_JS,
        repo=_esc(name), days=data['days'], sessions=total, today=_esc(today),
        provider=_esc(data['provider']), body=body,
    )


# ── static assets ────────────────────────────────────────────────────────────

_CSS = """
[data-theme="dark"]{--bg:#09090b;--surface:#111113;--surface2:#18181b;--border:#27272a;--muted:#52525b;--text:#a1a1aa;--heading:#fafafa;--accent:#818cf8;--accent-lo:rgba(129,140,248,.1);--accent-bd:rgba(129,140,248,.25);--green:#34d399;--green-lo:rgba(52,211,153,.08);--yellow:#fbbf24;--red:#f87171;--shadow:0 1px 3px rgba(0,0,0,.4);--wf-cr:#3730a3;--wf-cr-pre:#4c1d95;--wf-cr-po:#2e1065;--wf-fr:#065f46;--wf-fr-pre:#064e3b;--wf-fr-po:#022c22;--wf-cw:#7c2d12}
[data-theme="light"]{--bg:#f8f8fa;--surface:#fff;--surface2:#f1f1f5;--border:#e4e4e7;--muted:#a1a1aa;--text:#52525b;--heading:#09090b;--accent:#4f46e5;--accent-lo:rgba(79,70,229,.07);--accent-bd:rgba(79,70,229,.2);--green:#059669;--green-lo:rgba(5,150,105,.07);--yellow:#d97706;--red:#dc2626;--shadow:0 1px 3px rgba(0,0,0,.08);--wf-cr:#818cf8;--wf-cr-pre:#6366f1;--wf-cr-po:#a5b4fc;--wf-fr:#34d399;--wf-fr-pre:#059669;--wf-fr-po:#6ee7b7;--wf-cw:#f97316}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Inter',system-ui,sans-serif;font-size:13.5px;line-height:1.6;transition:background .2s,color .2s}
:root{--mono:'JetBrains Mono','Fira Code',ui-monospace,monospace;--sidebar-w:200px;--radius:6px}
.shell{display:grid;grid-template-columns:var(--sidebar-w) 1fr;grid-template-rows:auto 1fr;min-height:100vh}
.header{grid-column:1/-1;padding:14px 28px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;background:var(--surface);position:sticky;top:0;z-index:100}
.logo{font-family:var(--mono);font-size:12px;color:var(--accent);background:var(--accent-lo);border:1px solid var(--accent-bd);padding:3px 9px;border-radius:4px;letter-spacing:.04em;flex-shrink:0}
.header-repo{font-size:14px;font-weight:600;color:var(--heading)}
.header-sep{color:var(--border)}.header-sub{font-size:13px;color:var(--muted)}
.header-right{margin-left:auto;display:flex;align-items:center;gap:16px}
.header-meta{font-family:var(--mono);font-size:11px;color:var(--muted);display:flex;gap:14px}
.toggle{background:var(--surface2);border:1px solid var(--border);border-radius:20px;padding:4px 10px;font-size:12px;color:var(--muted);cursor:pointer;display:flex;align-items:center;gap:6px;transition:border-color .15s,color .15s;flex-shrink:0}
.toggle:hover{border-color:var(--accent);color:var(--accent)}
.sidebar{grid-column:1;grid-row:2;border-right:1px solid var(--border);padding:24px 0;position:sticky;top:49px;height:calc(100vh - 49px);overflow-y:auto;background:var(--surface)}
.sidebar-label{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);padding:0 20px 8px}
.sidebar-link{display:block;padding:6px 20px;font-size:12.5px;color:var(--muted);text-decoration:none;border-left:2px solid transparent;transition:color .12s,border-color .12s,background .12s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sidebar-link:hover{color:var(--heading);background:var(--surface2)}
.sidebar-link.active{color:var(--accent);border-left-color:var(--accent);background:var(--accent-lo)}
.main{grid-column:2;grid-row:2;padding:40px 48px 80px;min-width:0}
.section{padding-top:52px}.section:first-child{padding-top:0}
.section-label{font-size:10.5px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:16px;display:flex;align-items:center;gap:10px}
.section-label::after{content:'';flex:1;height:1px;background:var(--border)}
.headline-grid{display:grid;grid-template-columns:auto 1fr;gap:28px;align-items:start}
.hl-stat{font-family:var(--mono);font-size:56px;font-weight:700;color:var(--heading);letter-spacing:-.03em;line-height:1}
.hl-stat em{color:var(--accent);font-style:normal}
.hl-unit{font-size:13px;color:var(--muted);margin-top:6px}
.hl-desc{font-size:13px;color:var(--text);padding-top:4px}.hl-desc strong{color:var(--heading)}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:14px}
.tag{font-family:var(--mono);font-size:11px;padding:2px 8px;border-radius:4px;border:1px solid var(--border);color:var(--muted);background:var(--surface2)}
.tag.hi{border-color:var(--accent-bd);color:var(--accent);background:var(--accent-lo)}
.wf-note{font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:14px}
.wf-tree{display:flex;flex-direction:column;gap:5px;font-family:var(--mono)}
.wf-row{display:grid;grid-template-columns:130px 1fr 90px 56px;align-items:center;gap:12px;font-size:12px}
.wf-row.level-1{padding-left:16px}.wf-row.level-2{padding-left:32px}
.wf-lbl{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center;gap:6px}
.wf-lbl.root{color:var(--heading);font-weight:600}
.wf-tree-icon{color:var(--border);flex-shrink:0;white-space:pre}
.wf-track{height:20px;background:var(--surface2);border-radius:3px;overflow:hidden;border:1px solid var(--border)}
.wf-row.level-0 .wf-track{height:26px;border-radius:4px}.wf-row.level-2 .wf-track{height:15px}
.wf-fill{height:100%;border-radius:2px;display:flex;align-items:center;padding:0 7px;font-size:10px;font-weight:700;color:rgba(255,255,255,.6);white-space:nowrap;overflow:hidden;min-width:0}
[data-theme="light"] .wf-fill{color:rgba(0,0,0,.45)}
.wf-tok{color:var(--text);text-align:right}.wf-pct{color:var(--muted);text-align:right}
.wf-row.level-0 .wf-tok{color:var(--heading);font-weight:600}
.wf-sep{height:8px}
.wf-overlays{margin-top:16px;padding:12px 16px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);font-size:12px;color:var(--muted)}
.wf-overlays strong{color:var(--text)}
.finding{border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);overflow:hidden;margin-bottom:8px;box-shadow:var(--shadow)}
.finding-head{padding:14px 18px 12px;display:flex;align-items:flex-start;gap:12px}
.finding-id{font-family:var(--mono);font-size:11px;color:var(--accent);background:var(--accent-lo);border:1px solid var(--accent-bd);padding:2px 8px;border-radius:4px;flex-shrink:0;margin-top:1px}
.finding-evidence{font-size:13px;color:var(--text)}
code{font-family:var(--mono);font-size:11px;background:var(--surface2);border:1px solid var(--border);padding:1px 5px;border-radius:3px;color:var(--heading)}
.finding-actions{border-top:1px solid var(--border);display:flex}
.finding-action{flex:1;padding:10px 18px;display:flex;align-items:flex-start;gap:8px;border-right:1px solid var(--border);font-size:12px;color:var(--muted)}
.finding-action:last-child{border-right:none}
.al{font-family:var(--mono);font-size:10px;padding:2px 7px;border-radius:4px;flex-shrink:0;margin-top:2px}
.al.fix{background:var(--green-lo);color:var(--green);border:1px solid rgba(52,211,153,.25)}
.al.verify{background:var(--accent-lo);color:var(--accent);border:1px solid var(--accent-bd)}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{padding:0 10px 10px;font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);text-align:left;border-bottom:1px solid var(--border)}
thead th:first-child{padding-left:0}thead th.r{text-align:right}
tbody tr{border-bottom:1px solid var(--border)}tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:var(--surface2)}
td{padding:9px 10px;vertical-align:middle}td:first-child{padding-left:0}
.mono{font-family:var(--mono);font-size:12px;color:var(--heading)}
.num{text-align:right;font-family:var(--mono);font-size:12px}
.badge{display:inline-block;font-family:var(--mono);font-size:10px;padding:2px 7px;border-radius:4px;border:1px solid var(--border);background:var(--surface2);color:var(--muted)}
.badge.claude{border-color:var(--accent-bd);color:var(--accent);background:var(--accent-lo)}
.badge.codex{border-color:rgba(52,211,153,.25);color:var(--green);background:var(--green-lo)}
.heat-hi{color:var(--red)}.heat-md{color:var(--yellow)}.heat-lo{color:var(--muted)}
.drill-hint{margin-top:10px;font-size:11px;color:var(--muted);font-family:var(--mono)}
.layer-block{margin-bottom:6px}
.layer-row{display:grid;grid-template-columns:100px 1fr 4fr 130px;align-items:center;gap:14px;padding:10px 14px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius)}
.l-name{font-family:var(--mono);font-size:11px;color:var(--heading)}
.l-desc{font-size:12px;color:var(--muted)}
.l-track{height:5px;background:var(--border);border-radius:3px;overflow:hidden}
.l-fill{height:100%;border-radius:3px;background:var(--accent);opacity:.75}
.l-val{font-family:var(--mono);font-size:11px;color:var(--muted);text-align:right}
.layer-details{margin:2px 0 0}
.layer-details summary{cursor:pointer;font-family:var(--mono);font-size:11px;color:var(--muted);padding:6px 14px;list-style:none;user-select:none}
.layer-details summary:hover{color:var(--accent)}
.layer-details summary::before{content:'▸ ';color:var(--border)}
.layer-details[open] summary::before{content:'▾ '}
.drill-list{padding:2px 14px 10px 28px;display:flex;flex-direction:column;gap:4px}
.drill-row{font-family:var(--mono);font-size:11.5px;color:var(--text)}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.metric{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;box-shadow:var(--shadow)}
.m-val{font-family:var(--mono);font-size:24px;font-weight:700;color:var(--heading);letter-spacing:-.02em;line-height:1.1}
.m-val.accent{color:var(--accent)}.m-val.green{color:var(--green)}.m-val.red{color:var(--red)}
.m-label{font-size:11px;color:var(--muted);margin-top:5px;line-height:1.35}
.m-basis{font-family:var(--mono);font-size:10px;color:var(--border);margin-top:5px}
.footer{margin-top:56px;padding-top:20px;border-top:1px solid var(--border);display:flex;justify-content:space-between;font-family:var(--mono);font-size:11px;color:var(--muted)}
.footer a{color:var(--muted);text-decoration:none}.footer a:hover{color:var(--accent)}
@media(max-width:900px){
.shell{grid-template-columns:1fr;grid-template-rows:auto auto 1fr}
.header{grid-column:1}
.sidebar{grid-column:1;grid-row:2;position:static;height:auto;border-right:none;border-bottom:1px solid var(--border);padding:12px 0;display:flex;flex-wrap:wrap}
.sidebar-label{display:none}
.sidebar-link{padding:6px 14px;border-left:none;border-bottom:2px solid transparent}
.sidebar-link.active{border-bottom-color:var(--accent);border-left-color:transparent;background:none}
.main{grid-column:1;grid-row:3;padding:28px 20px 60px}
.metrics{grid-template-columns:repeat(2,1fr)}
.headline-grid{grid-template-columns:1fr;gap:16px}.hl-stat{font-size:40px}
.wf-row{grid-template-columns:110px 1fr 80px 48px;gap:8px;font-size:11px}
.layer-row{grid-template-columns:90px 1fr 3fr 110px}
}
@media(max-width:600px){
.header-meta{display:none}
.wf-row{grid-template-columns:90px 1fr 70px}.wf-pct{display:none}
.layer-row{grid-template-columns:90px 1fr 90px}.l-desc{display:none}
.finding-actions{flex-direction:column}.finding-action{border-right:none;border-bottom:1px solid var(--border)}
}
"""

_JS = """
function toggleTheme(){
  var h=document.documentElement,b=document.getElementById('theme-btn');
  if(h.dataset.theme==='dark'){h.dataset.theme='light';b.innerHTML='☽ Dark';}
  else{h.dataset.theme='dark';b.innerHTML='☀ Light';}
}
var secs=document.querySelectorAll('section[id]'),lks=document.querySelectorAll('.sidebar-link');
var obs=new IntersectionObserver(function(es){es.forEach(function(e){
  if(e.isIntersecting){lks.forEach(function(l){l.classList.remove('active');});
  var a=document.querySelector('.sidebar-link[href="#'+e.target.id+'"]');if(a)a.classList.add('active');}});
},{rootMargin:'-30% 0px -60% 0px'});
secs.forEach(function(s){obs.observe(s);});
"""

_SHELL = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>cram audit — {repo}</title>
<style>{css}</style>
</head>
<body>
<div class="shell">
  <header class="header">
    <span class="logo">◆ cram</span>
    <span class="header-repo">{repo}</span>
    <span class="header-sep">—</span>
    <span class="header-sub">agent session audit</span>
    <div class="header-right">
      <div class="header-meta">
        <span>{sessions} sessions</span><span>last {days} days</span><span>{today}</span>
      </div>
      <button class="toggle" onclick="toggleTheme()" id="theme-btn">☀ Light</button>
    </div>
  </header>
  <nav class="sidebar">
    <div class="sidebar-label">Sections</div>
    <a href="#headline" class="sidebar-link active">Headline</a>
    <a href="#waterfall" class="sidebar-link">Token waterfall</a>
    <a href="#findings" class="sidebar-link">Findings</a>
    <a href="#leaderboard" class="sidebar-link">Leaderboard</a>
    <a href="#layers" class="sidebar-link">Waste layers</a>
    <a href="#metrics" class="sidebar-link">Key metrics</a>
  </nav>
  <main class="main">{body}
    <div class="footer">
      <span>generated by <a href="https://github.com/vishbay/cram-ai">cram-ai</a> · <code>cram audit --report-html</code></span>
      <span>{provider} pricing · conservative methodology</span>
    </div>
  </main>
</div>
<script>{js}</script>
</body>
</html>
"""
