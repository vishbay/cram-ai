"""cram ci — render audit/rig results as a sticky PR comment, and gate on them.

This module is the engine behind the `cram audit` GitHub Action. It is
deliberately **key-free and offline**: it consumes JSON that a developer (or a
self-hosted job) produced with `cram audit --json` / `cram audit --compare ...
--json` / `cram rig --json`, and turns it into Markdown for a pull-request
comment, plus a pass/fail verdict for the rig gate.

Why JSON-in rather than running the audit here: agent transcripts live on a
developer's disk (~/.claude/projects/…), never in a stock CI runner, so a fresh
checkout has nothing to audit. Committing/uploading the JSON is the realistic
input in CI.

Modes (see `main`):
  compare  — two audit JSONs (or one `--compare ... --json`): a delta table.
  report   — one `cram audit --json`: the markdown audit report.
  rig      — baseline + candidate `cram rig --json`: a success-rate gate.
"""

from __future__ import annotations

import argparse
import json
import os

STICKY_MARKER = "<!-- cram-audit-bot -->"


# ── loading ──────────────────────────────────────────────────────────────────

def _load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _rig_summary(doc: dict) -> dict:
    """Accept a raw `summarize()` output or a wrapped {meta, summary} doc."""
    if 'summary' in doc and 'providers' not in doc:
        return doc['summary']
    return doc


# ── compare ──────────────────────────────────────────────────────────────────

def render_compare_comment(compare_json: dict, *, marker: str = STICKY_MARKER) -> str:
    """Markdown delta table from a `cram audit --compare A B --json` document.

    Shape: {days, a:{path,data}, b:{path,data}}. Δ = B − A, matching the CLI.
    """
    from cram.audit import compare_rows

    days = compare_json.get('days', '?')
    a, b = compare_json.get('a', {}), compare_json.get('b', {})
    data_a, data_b = a.get('data'), b.get('data')
    name_a = os.path.basename((a.get('path') or 'A').rstrip('/')) or 'A'
    name_b = os.path.basename((b.get('path') or 'B').rstrip('/')) or 'B'

    out = [marker, '## cram audit — comparison',
           f'_Last {days} days · Δ = B − A · negative reads-before-edit means B oriented faster._',
           '']
    if not data_a or not data_b:
        out.append('> No sessions found in one or both inputs — nothing to compare.')
        return '\n'.join(out)

    out += [f'| Metric | {name_a} | {name_b} | Δ | Δ% |',
            '|---|--:|--:|--:|--:|']
    for r in compare_rows(data_a, data_b):
        out.append(f"| {r['label']} | {r['a_str']} | {r['b_str']} | {r['delta_str']} | {r['pct']} |")
    out.append('')
    return '\n'.join(out)


# ── report ───────────────────────────────────────────────────────────────────

def render_report_comment(audit_data: dict, repo_root: str = '.', *,
                          marker: str = STICKY_MARKER) -> str:
    """Markdown audit report (from `collect_audit()` data) wrapped for a PR comment.

    HTML can't render in a PR comment, so the markdown report is used; the body
    is collapsed under <details> to keep the thread tidy.
    """
    from cram.audit_report import render_report

    if not audit_data:
        return f"{marker}\n## cram audit\n\n> No sessions found — nothing to report."

    sessions = audit_data.get('sessions', '?')
    rbe = audit_data.get('avg_reads_before_edit')
    headline = f"{sessions} sessions analysed"
    if rbe is not None:
        headline += f" · {rbe:.1f} reads before first edit"
    body = render_report(audit_data, repo_root)
    return (f"{marker}\n## cram audit\n\n**{headline}**\n\n"
            f"<details><summary>Full report</summary>\n\n{body}\n\n</details>\n")


# ── rig gate ─────────────────────────────────────────────────────────────────

def evaluate_rig_gate(baseline: dict, candidate: dict, *,
                      tolerance: float = 0.0,
                      marker: str = STICKY_MARKER) -> tuple[bool, str]:
    """Compare candidate vs baseline `cram rig` summaries per provider.

    Fails (passed=False) if any provider's candidate success rate drops more
    than `tolerance` below baseline — i.e. the "optimization" made the agent
    worse. Token savings never override a success regression.
    """
    base = _rig_summary(baseline).get('providers', {})
    cand = _rig_summary(candidate).get('providers', {})

    rows, passed = [], True
    for name in sorted(set(base) | set(cand)):
        b = base.get(name, {}).get('success_rate')
        c = cand.get(name, {}).get('success_rate')
        if b is None or c is None:
            rows.append((name, b, c, None, '—'))
            continue
        drop = b - c
        ok = drop <= tolerance + 1e-9
        passed = passed and ok
        rows.append((name, b, c, drop, '✅' if ok else '❌'))

    out = [marker, '## cram rig — success gate',
           f"_Fails if candidate success drops more than {tolerance:.0%} below baseline._",
           '',
           '| Provider | Baseline | Candidate | Δ success | |',
           '|---|--:|--:|--:|:-:|']
    for name, b, c, drop, mark in rows:
        bs = f'{b:.0%}' if b is not None else '—'
        cs = f'{c:.0%}' if c is not None else '—'
        ds = f'{-drop:+.0%}' if drop is not None else '—'
        out.append(f'| {name} | {bs} | {cs} | {ds} | {mark} |')
    out += ['', f"**Gate: {'PASS ✅' if passed else 'FAIL ❌'}**", '']
    return passed, '\n'.join(out)


# ── CLI ──────────────────────────────────────────────────────────────────────

def _emit(body: str, passed: bool, out_path: str | None) -> None:
    """Write the rendered body + verdict to GITHUB_OUTPUT and an optional file."""
    gh_out = os.environ.get('GITHUB_OUTPUT')
    if gh_out:
        with open(gh_out, 'a') as f:
            f.write(f'passed={"true" if passed else "false"}\n')
            f.write('comment-body<<CRAM_EOF\n')
            f.write(body.rstrip('\n') + '\n')
            f.write('CRAM_EOF\n')
            if out_path:
                f.write(f'summary-path={out_path}\n')
    if out_path:
        with open(out_path, 'w') as f:
            f.write(body)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog='cram-ci',
                                description='Render cram audit/rig JSON as a PR comment and gate.')
    p.add_argument('--mode', choices=('compare', 'report', 'rig'), default='compare')
    p.add_argument('--file-a', help='baseline JSON (compare/rig)')
    p.add_argument('--file-b', help='candidate JSON (compare/rig)')
    p.add_argument('--compare-json', help='a single `--compare ... --json` document')
    p.add_argument('--report-json', help='a single `cram audit --json` document')
    p.add_argument('--repo', default='.', help='repo root for report rendering')
    p.add_argument('--tolerance', type=float, default=0.0,
                   help='max allowed success-rate drop for the rig gate (default 0)')
    p.add_argument('--out', help='also write the rendered markdown to this file')
    args = p.parse_args(argv)

    passed = True
    if args.mode == 'compare':
        src = args.compare_json or args.file_a
        if not src:
            p.error('compare mode needs --compare-json or --file-a')
        body = render_compare_comment(_load(src))
    elif args.mode == 'report':
        if not args.report_json:
            p.error('report mode needs --report-json')
        body = render_report_comment(_load(args.report_json), args.repo)
    else:  # rig
        if not (args.file_a and args.file_b):
            p.error('rig mode needs --file-a (baseline) and --file-b (candidate)')
        passed, body = evaluate_rig_gate(_load(args.file_a), _load(args.file_b),
                                         tolerance=args.tolerance)

    _emit(body, passed, args.out)
    if not os.environ.get('GITHUB_OUTPUT'):
        print(body)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
