"""Tests for cram/audit_findings.py — deterministic findings rules."""

from __future__ import annotations
import json

from cram.audit_findings import derive_findings


def _data(**overrides):
    """A healthy-repo aggregate baseline: no rule should fire on it."""
    base = {
        'sessions':                  10,
        'top_read_files':            [('a.py', 1, 1)],
        'pre_edit_spend_share':            0.10,
        'pre_edit_measured_sessions':  8,
        'sessions_with_big_results': 0,
        'big_result_bytes':          20_000,
        'carried_cost_per_session':  0.0,
        'cache_blind_sessions':      0,
        'avg_error_results':         0.2,
        'sessions_with_errors':      1,
        'avg_edit_churn':            0.5,
        'avg_context_growth':        2.0,
        'context_growth_measured':   8,
    }
    base.update(overrides)
    return base


class TestRules:
    def test_healthy_repo_has_no_findings(self):
        assert derive_findings(_data()) == []

    def test_repeated_reads_fires_on_cross_session_repeats(self):
        f = derive_findings(_data(top_read_files=[
            ('hot.py', 9, 4), ('warm.py', 3, 2), ('once.py', 5, 1)]))
        assert [x['id'] for x in f] == ['repeated-reads']
        assert 'hot.py read 9× across 4 sessions' in f[0]['evidence']
        assert '(+1 more)' in f[0]['evidence']  # once.py (1 session) not counted

    def test_within_session_repeats_do_not_fire(self):
        # 7 reads but all in one session: redundant, not cross-session evidence
        assert derive_findings(_data(top_read_files=[('x.py', 7, 1)])) == []

    def test_high_orientation_threshold(self):
        assert derive_findings(_data(pre_edit_spend_share=0.24)) == []
        f = derive_findings(_data(pre_edit_spend_share=0.25))
        assert [x['id'] for x in f] == ['high-orientation']
        assert '25%' in f[0]['evidence']

    def test_unmeasured_orientation_never_fires(self):
        assert derive_findings(_data(pre_edit_spend_share=None)) == []

    def test_oversized_results(self):
        f = derive_findings(_data(sessions_with_big_results=3,
                                  carried_cost_per_session=0.0421))
        assert [x['id'] for x in f] == ['oversized-results']
        assert '3/10 sessions' in f[0]['evidence']
        assert '$0.0421' in f[0]['evidence']

    def test_cache_blind(self):
        f = derive_findings(_data(cache_blind_sessions=2))
        assert [x['id'] for x in f] == ['cache-blind']

    def test_retry_loops_threshold(self):
        assert derive_findings(_data(avg_error_results=0.9)) == []
        f = derive_findings(_data(avg_error_results=1.5, sessions_with_errors=4))
        assert [x['id'] for x in f] == ['retry-loops']

    def test_edit_churn_threshold(self):
        assert derive_findings(_data(avg_edit_churn=1.9)) == []
        f = derive_findings(_data(avg_edit_churn=2.5))
        assert [x['id'] for x in f] == ['edit-churn']

    def test_context_bloat_threshold(self):
        assert derive_findings(_data(avg_context_growth=5.0)) == []
        f = derive_findings(_data(avg_context_growth=6.2))
        assert [x['id'] for x in f] == ['context-bloat']
        assert derive_findings(_data(avg_context_growth=None)) == []

    def test_multiple_findings_stable_order(self):
        f = derive_findings(_data(
            top_read_files=[('hot.py', 4, 3)],
            pre_edit_spend_share=0.40,
            cache_blind_sessions=1,
            avg_edit_churn=3.0,
        ))
        assert [x['id'] for x in f] == [
            'repeated-reads', 'high-orientation', 'cache-blind', 'edit-churn']
        for x in f:
            assert x['severity'] == 'warn' and x['evidence'] and x['fix']


class TestEndToEnd:
    def _setup(self, tmp_path, monkeypatch, sessions):
        import cram.audit as _audit_mod
        td = tmp_path / 'proj'
        td.mkdir()
        for i, calls in enumerate(sessions):
            with open(str(td / f's{i}.jsonl'), 'w') as f:
                for name, inp in calls:
                    f.write(json.dumps({'type': 'tool_use', 'name': name,
                                        'input': inp}) + '\n')
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir',
                            lambda r, d=str(td): d)
        monkeypatch.setattr(_audit_mod, '_cursor_agent_transcripts_dir', lambda: None)
        monkeypatch.setattr(_audit_mod, '_cursor_storage_root', lambda: None)
        monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: None)
        return _audit_mod

    def test_findings_in_collect_and_report(self, tmp_path, monkeypatch, capsys):
        audit_mod = self._setup(tmp_path, monkeypatch, [
            [('Read', {'file_path': 'hot.py'}), ('Edit', {'file_path': 'a.py'})],
            [('Read', {'file_path': 'hot.py'}), ('Edit', {'file_path': 'b.py'})],
        ])
        data = audit_mod.collect_audit(str(tmp_path), days=365)
        assert [f['id'] for f in data['findings']] == ['repeated-reads']

        audit_mod.run_audit(str(tmp_path), days=365)
        out = capsys.readouterr().out
        assert 'Findings (1):' in out
        assert 'repeated-reads' in out
        assert 'hot.py' in out

    def test_no_findings_block_when_clean(self, tmp_path, monkeypatch, capsys):
        audit_mod = self._setup(tmp_path, monkeypatch, [
            [('Read', {'file_path': 'a.py'}), ('Edit', {'file_path': 'a.py'})],
        ])
        audit_mod.run_audit(str(tmp_path), days=365)
        assert 'Findings' not in capsys.readouterr().out

    def test_findings_serializable_in_json(self, tmp_path, monkeypatch, capsys):
        audit_mod = self._setup(tmp_path, monkeypatch, [
            [('Read', {'file_path': 'hot.py'}), ('Edit', {'file_path': 'a.py'})],
            [('Read', {'file_path': 'hot.py'}), ('Edit', {'file_path': 'b.py'})],
        ])
        audit_mod.run_audit(str(tmp_path), days=365, as_json=True)
        parsed = json.loads(capsys.readouterr().out)
        assert parsed['findings'][0]['id'] == 'repeated-reads'


# ── verify recipe (profiler → referee loop) ───────────────────────────────────

class TestVerifyLoop:
    def test_every_finding_carries_a_verify_recipe(self):
        data = _data(avg_error_results=2.0, avg_edit_churn=3.0,
                     avg_context_growth=9.0, cache_blind_sessions=3,
                     sessions_with_big_results=2,
                     top_read_files=[('hot.py', 9, 5)])
        findings = derive_findings(data)
        assert findings
        for fd in findings:
            v = fd['verify']
            assert v and 'command' in v and 'expect' in v

    def test_orientation_findings_point_at_the_referee(self):
        data = _data(pre_edit_spend_share=0.40, pre_edit_measured_sessions=8,
                     top_read_files=[('hot.py', 9, 5)])
        by_id = {f['id']: f for f in derive_findings(data)}
        assert 'cram rig' in by_id['high-orientation']['verify']['command']
        assert 'cram rig' in by_id['repeated-reads']['verify']['command']

    def test_config_finding_points_at_compare(self):
        data = _data(avg_edit_churn=3.0)
        churn = next(f for f in derive_findings(data) if f['id'] == 'edit-churn')
        assert 'cram audit --compare' in churn['verify']['command']
