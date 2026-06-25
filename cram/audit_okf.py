"""Export `cram audit` findings as an Open Knowledge Format (OKF) bundle.

OKF v0.1 (Google Cloud, Apache-2.0) is a vendor-neutral spec for curated agent
context: a directory of markdown files with YAML frontmatter. cram is a *producer*
here — the audit measures where a repo's coding-agent tokens go, and this turns
those durable findings into portable concepts any OKF-aware agent can read next
session to avoid the same waste. cram stays the meter; OKF is just the wire format.

render_okf_bundle() is a pure function over the same collect_audit() dict the
text/markdown/HTML reports use, returning {relative_path: file_contents}. The
caller writes them to disk. Frontmatter is hand-emitted (no PyYAML dependency)
and deterministic apart from the injectable timestamp, so the bundle diffs
cleanly under version control — which is the whole point of OKF.

Spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf
"""

from __future__ import annotations
import datetime
import os
import re

OKF_VERSION = '0.1'


# ── frontmatter helpers (minimal, dependency-free YAML) ──────────────────────

def _yaml_str(s) -> str:
    """A double-quoted YAML scalar, safe for colons/quotes/newlines."""
    text = str(s).replace('\\', '\\\\').replace('"', '\\"')
    text = re.sub(r'\s+', ' ', text).strip()
    return f'"{text}"'


def _yaml_list(items) -> str:
    return '[' + ', '.join(_yaml_str(i) for i in items) + ']'


def _frontmatter(fields: list[tuple[str, str | None]]) -> str:
    """Render an ordered list of (key, already-encoded-value) into a block.
    None values are dropped so optional fields stay absent rather than empty."""
    lines = [f'{k}: {v}' for k, v in fields if v is not None]
    return '---\n' + '\n'.join(lines) + '\n---\n'


def _slug(s: str) -> str:
    s = re.sub(r'[^a-z0-9._-]+', '-', str(s).lower()).strip('-')
    return s or 'item'


def _humanize(finding_id: str) -> str:
    return finding_id.replace('-', ' ').replace('_', ' ').strip().capitalize()


# ── documents ────────────────────────────────────────────────────────────────

def _finding_doc(fd: dict, ts: str) -> str:
    fid = fd.get('id', 'finding')
    evidence = fd.get('evidence', '')
    tags = ['cram-audit', _slug(fid)]
    if fd.get('severity'):
        tags.append(_slug(fd['severity']))
    extras: list[tuple[str, str | None]] = [
        ('cram_finding_id', _yaml_str(fid)),
        ('cram_severity', _yaml_str(fd['severity']) if fd.get('severity') else None),
        ('cram_sample_n', str(fd['sample_n']) if fd.get('sample_n') is not None else None),
    ]
    if fd.get('preliminary'):
        extras.append(('cram_preliminary', 'true'))
    fm = _frontmatter([
        ('type', _yaml_str('Finding')),
        ('title', _yaml_str(_humanize(fid))),
        ('description', _yaml_str(evidence)),
        ('tags', _yaml_list(tags)),
        ('timestamp', _yaml_str(ts)),
        *extras,
    ])
    body = [f'# {_humanize(fid)}', '', f'**Evidence:** {evidence}', '',
            f'**Fix:** {fd.get("fix", "")}']
    verify = fd.get('verify')
    if verify and verify.get('command'):
        body += ['', f'**Verify:** `{verify["command"]}` → {verify.get("expect", "")}']
    if fd.get('preliminary'):
        n = fd.get('sample_n')
        body += ['', f'> Preliminary — only {n} measured session(s); treat as directional.']
    return fm + '\n' + '\n'.join(body) + '\n'


def _findings_index(findings: list[dict]) -> str:
    fm = _frontmatter([
        ('type', _yaml_str('Index')),
        ('title', _yaml_str('Findings')),
        ('description', _yaml_str(f'{len(findings)} agent-token finding(s) from cram audit.')),
    ])
    lines = ['# Findings', '']
    for fd in findings:
        fid = fd.get('id', 'finding')
        lines.append(f'- [{_humanize(fid)}]({_slug(fid)}.md) — {fd.get("evidence", "")}')
    return fm + '\n' + '\n'.join(lines) + '\n'


def _headline(data: dict) -> str:
    cost = data.get('total_eff_cost') or 0.0
    if not cost:
        return ''
    parts = [f'💸 ~${cost:,.2f} effective input over '
             f'{data.get("cost_measured_sessions", 0)} measured session(s) '
             f'(~${data.get("monthly_cost") or 0:,.2f}/mo).']
    big = data.get('biggest_avoidable')
    if big and big.get('monthly_cost'):
        parts.append(f'Biggest avoidable: **{big["layer"]}** ~${big["monthly_cost"]:,.2f}/mo.')
    return ' '.join(parts)


def _root_index(data: dict, repo: str, findings: list[dict], ts: str,
                version: str | None) -> str:
    sessions, days = data.get('sessions', 0), data.get('days', 0)
    provider = data.get('provider', 'unknown')
    desc = (f"Where {repo}'s coding-agent tokens go and what to fix — "
            f"{sessions} session(s) over {days}d, measured by cram audit.")
    fm = _frontmatter([
        ('okf_version', _yaml_str(OKF_VERSION)),
        ('type', _yaml_str('Knowledge Bundle')),
        ('title', _yaml_str(f'cram audit — {repo}')),
        ('description', _yaml_str(desc)),
        ('tags', _yaml_list(['cram-audit', 'agent-context', 'token-efficiency'])),
        ('timestamp', _yaml_str(ts)),
        ('cram_schema_version', _yaml_str(data.get('schema_version', ''))),
        ('generator', _yaml_str(f'cram-ai {version}' if version else 'cram-ai')),
    ])
    body = [f'# cram audit — {repo}', '',
            f'Measured from **{sessions} session(s)** over **{days}d** '
            f'({provider} pricing).']
    headline = _headline(data)
    if headline:
        body += ['', headline]
    body += ['', f'## Findings ({len(findings)})', '']
    if findings:
        for fd in findings:
            fid = fd.get('id', 'finding')
            body.append(f'- [{_humanize(fid)}](findings/{_slug(fid)}.md) — {fd.get("evidence", "")}')
    else:
        body.append('No findings — clean bill of health for this window.')
    return fm + '\n' + '\n'.join(body) + '\n'


# ── public API ───────────────────────────────────────────────────────────────

def render_okf_bundle(data: dict, repo_root: str, *,
                      now: datetime.datetime | None = None,
                      version: str | None = None) -> dict[str, str]:
    """Return {relative_path: contents} for an OKF v0.1 bundle of audit findings.

    The caller writes each path under the bundle root. `now` is injectable so
    the output is deterministic in tests; `version` stamps the generator field.
    """
    repo = os.path.basename(repo_root.rstrip(os.sep)) or repo_root
    now = now or datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    findings = data.get('findings') or []

    files = {'index.md': _root_index(data, repo, findings, ts, version)}
    if findings:
        files['findings/index.md'] = _findings_index(findings)
        for fd in findings:
            files[f'findings/{_slug(fd.get("id", "finding"))}.md'] = _finding_doc(fd, ts)
    return files
