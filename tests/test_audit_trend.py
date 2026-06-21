"""Trend-over-time: sparkline + recent-vs-prior direction for the primary metric."""

from __future__ import annotations
import json

from cram.audit import _sparkline, _weekly_trend, trend_line, collect_audit, AUDIT_SCHEMA_VERSION


# ── sparkline ──────────────────────────────────────────────────────────────────

def test_sparkline_length_and_flat():
    assert _sparkline([]) == ''
    assert _sparkline([5, 5, 5]) == '▁▁▁'          # flat → lowest block
    s = _sparkline([1, 2, 3, 4])
    assert len(s) == 4 and s[0] == '▁' and s[-1] == '█'


# ── direction ──────────────────────────────────────────────────────────────────

def _wk(values):
    # build a weekly series [(week, avg, n)] with n=2 each
    return [(f'2026-W{10 + i:02d}', v, 2) for i, v in enumerate(values)]


def test_trend_none_under_two_weeks():
    assert _weekly_trend([]) is None
    assert _weekly_trend(_wk([3.0])) is None


def test_trend_rising_is_worsening():
    t = _weekly_trend(_wk([2, 2, 6, 6]))      # reads-before-edit up → worse
    assert t['direction'] == 'worsening'
    assert t['change_pct'] > 0
    assert t['recent'] > t['prior']
    assert len(t['sparkline']) == 4


def test_trend_falling_is_improving():
    t = _weekly_trend(_wk([8, 8, 3, 3]))      # waste dropping → better
    assert t['direction'] == 'improving'
    assert t['change_pct'] < 0


def test_trend_flat_within_threshold():
    t = _weekly_trend(_wk([4.0, 4.1, 4.0, 4.05]))
    assert t['direction'] == 'flat'


def test_trend_line_formats():
    t = {'metric': 'reads_before_edit', 'weeks': 4, 'sparkline': '▁▃▆█',
         'prior': 2.0, 'recent': 6.0, 'change_pct': 2.0, 'direction': 'worsening'}
    line = trend_line({'trend': t})
    assert '↑' in line and 'worsening' in line and '2.0→6.0' in line
    assert trend_line({'trend': None}) is None


# ── integration ────────────────────────────────────────────────────────────────

def test_trend_key_present_in_audit(tmp_path, monkeypatch):
    import cram.audit as _audit_mod
    td = tmp_path / 'proj'
    td.mkdir()
    with open(td / 's0.jsonl', 'w') as f:
        f.write(json.dumps({'type': 'tool_use', 'name': 'Read',
                            'input': {'file_path': str(tmp_path) + '/a.py'}}) + '\n')
        f.write(json.dumps({'usage': {'input_tokens': 100}}) + '\n')
    monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r, d=str(td): d)
    monkeypatch.setattr(_audit_mod, '_cursor_agent_transcripts_dir', lambda: None)
    monkeypatch.setattr(_audit_mod, '_cursor_storage_root', lambda: None)
    monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: None)
    data = collect_audit(str(tmp_path), days=3650)
    assert 'trend' in data                    # present (None: one week of data)
    assert data['trend'] is None
    assert data['schema_version'] == AUDIT_SCHEMA_VERSION == 'audit/3'
