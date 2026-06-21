"""Tests for per-file drilldown: read_file_counts / edit_file_counts per
session and the top_read_files aggregate ('which files do agents keep
re-reading?' — the evidence layer for findings)."""

from __future__ import annotations
import json

from cram.audit import (
    _analyze_transcript, _analyze_cursor_transcript, _analyze_codex_transcript,
    collect_audit,
)
from tests.test_audit import (
    _make_transcript, _write_codex_jsonl,
    _exec_cmd, _apply_patch,
)


class TestPerSessionFileCounts:
    def test_claude_counts_read_tool_paths_only(self, tmp_path):
        path = _make_transcript([
            ('Read', {'file_path': 'a.py'}),
            ('Read', {'file_path': 'a.py'}),
            ('Read', {'file_path': 'b.py'}),
            ('Bash', {'command': 'cat c.py'}),    # bash read: no structured path
            ('Read', {}),                          # no path
            ('Edit', {'file_path': 'a.py'}),
            ('Edit', {'file_path': 'a.py'}),
        ], tmp_path)
        r = _analyze_transcript(path)
        assert r['read_file_counts'] == {'a.py': 2, 'b.py': 1}
        assert r['edit_file_counts'] == {'a.py': 2}

    def test_cursor_counts_relevant_files(self, tmp_path):
        repo, other = '/repo/p', '/repo/other'
        path = str(tmp_path / 's.jsonl')
        with open(path, 'w') as f:
            f.write(json.dumps({'tool': 'read_file', 'vcs': {'root': repo},
                                'files': [repo + '/a.py', repo + '/b.py']}) + '\n')
            f.write(json.dumps({'tool': 'read_file', 'vcs': {'root': other},
                                'files': [other + '/x.py']}) + '\n')  # irrelevant
            f.write(json.dumps({'tool': 'edit_file', 'vcs': {'root': repo},
                                'files': [repo + '/a.py']}) + '\n')
        r = _analyze_cursor_transcript(path, repo)
        assert r['read_file_counts'] == {repo + '/a.py': 1, repo + '/b.py': 1}

    def test_codex_has_no_file_paths(self, tmp_path):
        repo = str(tmp_path / 'repo')
        path = str(tmp_path / 's.jsonl')
        _write_codex_jsonl(path, repo, [
            _exec_cmd(f'cat {repo}/a.py', repo),
            _apply_patch(f'*** Update File: {repo}/a.py\n--- a\n+++ b\n'),
        ])
        r = _analyze_codex_transcript(path, repo)
        assert r['read_file_counts'] == {}
        assert r['edit_file_counts'] == {repo + '/a.py': 1}


class TestTopReadFiles:
    def _setup(self, tmp_path, monkeypatch, sessions):
        import cram.audit as _audit_mod
        td = tmp_path / 'proj'
        td.mkdir()
        for i, calls in enumerate(sessions):
            p = str(td / f's{i}.jsonl')
            with open(p, 'w') as f:
                for name, inp in calls:
                    f.write(json.dumps({'type': 'tool_use', 'name': name,
                                        'input': inp}) + '\n')
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir',
                            lambda r, d=str(td): d)
        monkeypatch.setattr(_audit_mod, '_cursor_agent_transcripts_dir', lambda: None)
        monkeypatch.setattr(_audit_mod, '_cursor_storage_root', lambda: None)
        monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: None)

    def test_aggregates_across_sessions(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, [
            [('Read', {'file_path': 'hot.py'}), ('Read', {'file_path': 'hot.py'}),
             ('Read', {'file_path': 'warm.py'}), ('Edit', {'file_path': 'hot.py'})],
            [('Read', {'file_path': 'hot.py'}), ('Edit', {'file_path': 'x.py'})],
        ])
        data = collect_audit(str(tmp_path), days=365)
        top = data['top_read_files']
        # hot.py: 3 reads across 2 sessions; warm.py: 1 read in 1 session
        assert top[0] == ('hot.py', 3, 2)
        assert ('warm.py', 1, 1) in top

    def test_deterministic_order_and_cap(self, tmp_path, monkeypatch):
        calls = [('Read', {'file_path': f'f{i:02d}.py'}) for i in range(15)]
        self._setup(tmp_path, monkeypatch, [calls])
        data = collect_audit(str(tmp_path), days=365)
        top = data['top_read_files']
        assert len(top) == 10
        # all tied at 1 read / 1 session → sorted by path
        assert [t[0] for t in top] == sorted(t[0] for t in top)

    def test_report_lists_repeated_files_repo_relative(self, tmp_path, monkeypatch, capsys):
        from cram.audit import run_audit
        repo = str(tmp_path)
        self._setup(tmp_path, monkeypatch, [
            [('Read', {'file_path': repo + '/cram/audit.py'}),
             ('Read', {'file_path': repo + '/cram/audit.py'}),
             ('Edit', {'file_path': repo + '/cram/audit.py'})],
        ])
        run_audit(repo, days=365)
        out = capsys.readouterr().out
        assert 'Top repeated files' in out
        assert 'cram/audit.py' in out
        assert repo + '/cram/audit.py' not in out  # shortened to repo-relative

    def test_no_block_when_nothing_repeated(self, tmp_path, monkeypatch, capsys):
        from cram.audit import run_audit
        self._setup(tmp_path, monkeypatch, [
            [('Read', {'file_path': 'a.py'}), ('Edit', {'file_path': 'b.py'})],
        ])
        run_audit(str(tmp_path), days=365)
        assert 'Top repeated files' not in capsys.readouterr().out

    def test_json_includes_top_read_files(self, tmp_path, monkeypatch, capsys):
        from cram.audit import run_audit
        self._setup(tmp_path, monkeypatch, [
            [('Read', {'file_path': 'a.py'}), ('Read', {'file_path': 'a.py'}),
             ('Edit', {'file_path': 'a.py'})],
        ])
        run_audit(str(tmp_path), days=365, as_json=True)
        parsed = json.loads(capsys.readouterr().out)
        assert parsed['top_read_files'] == [['a.py', 2, 1]]
