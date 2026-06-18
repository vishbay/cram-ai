"""Phase 3: opt-in Cursor token estimation.

Cursor transcripts carry no token usage, so every token metric is blank for
them. `--estimate-cursor` turns the files a session read into a clearly-labelled
*estimate*, while leaving the measured aggregates untouched."""

from __future__ import annotations
import json
import os

import pytest

from cram import audit_events
from cram.audit import collect_audit


# ── pure helper ────────────────────────────────────────────────────────────────

def test_estimate_from_counts_sums_size_over_token_budget(tmp_path):
    f = tmp_path / 'a.py'
    f.write_text('x' * 4000)               # 4000 bytes → 1000 tok at cpt=4
    counts = {str(f): 2}                    # read twice
    est = audit_events.estimate_read_tokens_from_counts(counts, str(tmp_path))
    assert est == 2000


def test_estimate_skips_missing_and_outside_repo(tmp_path):
    inside = tmp_path / 'in.py'
    inside.write_text('y' * 400)           # 100 tok
    outside = tmp_path.parent / 'out.py'
    outside.write_text('z' * 4000)
    counts = {str(inside): 1, str(outside): 1, str(tmp_path / 'gone.py'): 5}
    est = audit_events.estimate_read_tokens_from_counts(counts, str(tmp_path))
    assert est == 100                       # only the in-repo, existing file


def test_estimate_empty_counts():
    assert audit_events.estimate_read_tokens_from_counts({}, '/repo') == 0


def test_estimate_respects_chars_per_token(tmp_path):
    f = tmp_path / 'a.py'
    f.write_text('x' * 1000)
    counts = {str(f): 1}
    assert audit_events.estimate_read_tokens_from_counts(
        counts, str(tmp_path), chars_per_token=10) == 100


# ── integration through collect_audit ──────────────────────────────────────────

def _cursor_only_repo(tmp_path, monkeypatch):
    """A repo with one Cursor session that reads a known-size in-repo file."""
    import cram.audit as _audit_mod
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'hot.py').write_text('h' * 8000)        # 2000 tok at cpt=4

    at_dir = tmp_path / 'agent-transcripts'
    at_dir.mkdir()
    with open(at_dir / 's.jsonl', 'w') as f:
        for tool, files in [('read_file', [str(repo / 'hot.py')]),
                            ('edit_file', [str(repo / 'hot.py')])]:
            f.write(json.dumps({'version': 1, 'tool': tool,
                                'vcs': {'root': str(repo)}, 'files': files,
                                'input': {'target_file': files[0]}}) + '\n')

    monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r: None)
    monkeypatch.setattr(_audit_mod, '_cursor_agent_transcripts_dir', lambda: str(at_dir))
    monkeypatch.setattr(_audit_mod, '_cursor_storage_root', lambda: None)
    monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: None)
    return str(repo)


def test_off_by_default_no_estimate(tmp_path, monkeypatch):
    repo = _cursor_only_repo(tmp_path, monkeypatch)
    data = collect_audit(repo, days=3650)
    assert data is not None
    assert data['cursor_estimated'] is False
    assert data['est_cursor_read_tokens'] == 0
    assert not any(l['layer'] == 'cursor-reads' for l in data['layer_costs'])


def test_estimate_cursor_adds_labelled_estimate(tmp_path, monkeypatch):
    repo = _cursor_only_repo(tmp_path, monkeypatch)
    data = collect_audit(repo, days=3650, estimate_cursor=True)
    assert data['cursor_estimated'] is True
    assert data['est_cursor_read_tokens'] == 2000          # 8000 bytes / 4
    assert data['cursor_estimated_sessions'] == 1
    row = next(l for l in data['layer_costs'] if l['layer'] == 'cursor-reads')
    assert row['basis'] == 'estimated'


def test_estimate_does_not_pollute_measured_aggregates(tmp_path, monkeypatch):
    repo = _cursor_only_repo(tmp_path, monkeypatch)
    off = collect_audit(repo, days=3650)
    on = collect_audit(repo, days=3650, estimate_cursor=True)
    # The estimate must never leak into measured token sums — Cursor has none.
    for key in ('avg_cache_reads', 'avg_cache_writes', 'token_spine'):
        assert off[key] == on[key], key
    assert on['token_spine']['total'] == 0


def test_estimated_row_renders_in_markdown(tmp_path, monkeypatch):
    from cram.audit_report import render_report
    repo = _cursor_only_repo(tmp_path, monkeypatch)
    on = collect_audit(repo, days=3650, estimate_cursor=True)
    md = render_report(on, repo)
    assert 'Estimated Cursor read tokens' in md
    off = collect_audit(repo, days=3650)
    assert 'Estimated Cursor read tokens' not in render_report(off, repo)
