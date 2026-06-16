"""Tests for the per-session waterfall: derive_session_timeline (the data
behind `cram audit --session ID`) and its rendering. Where test_audit.py
covers the aggregate metrics, these cover the per-request timeline, the
consecutive-usage collapse, and the carried/redundant/retry attribution."""

from __future__ import annotations
import json

from cram.audit_events import parse_claude, derive_session_timeline
from cram.audit import run_session, _resolve_session_path


def _write_raw(path, messages):
    with open(path, 'w') as f:
        for msg in messages:
            f.write(json.dumps(msg) + '\n')


def _usage(input_tokens=0, cache_read=0, cache_write=0, output=0):
    return {'usage': {'input_tokens': input_tokens,
                      'cache_read_input_tokens': cache_read,
                      'cache_creation_input_tokens': cache_write,
                      'output_tokens': output}}


def _tool_use(name, **inp):
    return {'type': 'tool_use', 'name': name, 'input': inp}


def _timeline(path, big_result_bytes=20_000):
    meta, events = parse_claude(path)
    return derive_session_timeline(meta, events, big_result_bytes=big_result_bytes)


class TestTimelineRows:
    def test_rows_carry_token_classes_and_context(self, tmp_path):
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [_usage(input_tokens=100, cache_read=200, cache_write=300, output=40)])
        tl = _timeline(p)
        assert tl['requests'] == 1
        r = tl['rows'][0]
        assert (r['input'], r['cache_read'], r['cache_write'], r['output']) == (100, 200, 300, 40)
        assert r['context'] == 600          # input + cache_read + cache_write
        assert r['delta'] == 0              # first row has no prior context

    def test_delta_tracks_context_growth(self, tmp_path):
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [_usage(cache_read=10_000), _usage(cache_read=25_000)])
        tl = _timeline(p)
        assert [row['context'] for row in tl['rows']] == [10_000, 25_000]
        assert tl['rows'][1]['delta'] == 15_000
        assert tl['peak_context'] == 25_000
        assert tl['first_context'] == 10_000

    def test_empty_usage_rows_dropped(self, tmp_path):
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [_usage(), _usage(cache_read=5_000), _usage()])
        tl = _timeline(p)
        assert tl['requests'] == 1
        assert tl['rows'][0]['context'] == 5_000

    def test_no_usage_returns_none(self, tmp_path):
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [_tool_use('Read', file_path='a.py')])
        assert _timeline(p) is None

    def test_internal_usage_key_stripped(self, tmp_path):
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [_usage(cache_read=5_000)])
        tl = _timeline(p)
        assert '_usage' not in tl['rows'][0]


class TestConsecutiveUsageCollapse:
    def test_identical_consecutive_usage_collapses(self, tmp_path):
        # Claude re-logs one request's usage across lines; identical tuples merge.
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [
            _usage(input_tokens=5, cache_read=1_000, output=10),
            _usage(input_tokens=5, cache_read=1_000, output=10),
            _usage(input_tokens=5, cache_read=1_000, output=10),
        ])
        tl = _timeline(p)
        assert tl['requests'] == 1

    def test_collapse_folds_tool_notes_into_one_turn(self, tmp_path):
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [
            _usage(cache_read=1_000),
            _tool_use('Read', file_path='a.py'),
            _usage(cache_read=1_000),       # same tuple → collapses, note folds in
        ])
        tl = _timeline(p)
        assert tl['requests'] == 1
        assert any('a.py' in n for n in tl['rows'][0]['notes'])

    def test_distinct_usage_not_collapsed(self, tmp_path):
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [_usage(cache_read=1_000), _usage(cache_read=1_001)])
        assert _timeline(p)['requests'] == 2


class TestWasteAttribution:
    def test_big_result_attributed_to_last_read_and_carried(self, tmp_path):
        # read big.py → 40KB result → it rides every later request.
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [
            _usage(cache_read=10_000),
            _tool_use('Read', file_path='big.py'),
            {'type': 'tool_result', 'content': 'x' * 40_000},
            _usage(cache_read=20_000),
            _usage(cache_read=30_000),
        ])
        tl = _timeline(p)
        assert len(tl['carried']) == 1
        c = tl['carried'][0]
        assert c['file'] == 'big.py'
        assert c['tokens'] == 40_000 // 4       # size // 4
        # result appears after request 1 → carried by the 2 later requests
        assert c['carried_turns'] == 2
        assert c['carried_tokens'] == c['tokens'] * 2
        assert tl['carried_read_tokens'] == c['carried_tokens']

    def test_small_result_not_carried(self, tmp_path):
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [
            _usage(cache_read=10_000),
            {'type': 'tool_result', 'content': 'small'},
            _usage(cache_read=11_000),
        ])
        assert _timeline(p)['carried'] == []

    def test_redundant_reads_listed_descending(self, tmp_path):
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [
            _usage(cache_read=1_000),
            _tool_use('Read', file_path='hot.py'),
            _tool_use('Read', file_path='hot.py'),
            _tool_use('Read', file_path='warm.py'),
            _tool_use('Read', file_path='warm.py'),
            _tool_use('Read', file_path='warm.py'),
            _usage(cache_read=2_000),
        ])
        tl = _timeline(p)
        assert tl['redundant'] == [('warm.py', 3), ('hot.py', 2)]

    def test_failed_tool_calls_counted(self, tmp_path):
        p = str(tmp_path / 's.jsonl')
        _write_raw(p, [
            _usage(cache_read=1_000),
            {'type': 'tool_result', 'content': 'boom', 'is_error': True},
            {'type': 'tool_result', 'content': 'fine'},
            {'type': 'tool_result', 'content': 'boom', 'is_error': True},
            _usage(cache_read=2_000),
        ])
        assert _timeline(p)['retries'] == 2


class TestRunSessionCLI:
    def _setup(self, tmp_path, monkeypatch, messages, name='s-abc123.jsonl'):
        import cram.audit as _audit_mod
        td = tmp_path / 'proj'
        td.mkdir()
        _write_raw(str(td / name), messages)
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r, d=str(td): d)
        return str(td / name)

    def test_resolve_by_id_prefix(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, [_usage(cache_read=1_000)])
        assert _resolve_session_path('s-abc', str(tmp_path)).endswith('s-abc123.jsonl')

    def test_resolve_full_path(self, tmp_path, monkeypatch):
        p = self._setup(tmp_path, monkeypatch, [_usage(cache_read=1_000)])
        assert _resolve_session_path(p, str(tmp_path)) == p

    def test_resolve_miss_returns_none(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, [_usage(cache_read=1_000)])
        assert _resolve_session_path('nope', str(tmp_path)) is None

    def test_run_session_renders_waterfall(self, tmp_path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch, [
            _usage(cache_read=10_000),
            _tool_use('Read', file_path='big.py'),
            {'type': 'tool_result', 'content': 'x' * 40_000},
            _usage(cache_read=30_000),
        ])
        run_session('s-abc', str(tmp_path))
        out = capsys.readouterr().out
        assert 'Turn' in out and 'Context' in out
        assert 'Carried waste' in out
        assert 'big.py' in out

    def test_run_session_json(self, tmp_path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch, [_usage(cache_read=5_000)])
        run_session('s-abc', str(tmp_path), as_json=True)
        parsed = json.loads(capsys.readouterr().out)
        assert parsed['requests'] == 1
        assert parsed['rows'][0]['context'] == 5_000

    # ── Codex resolver ────────────────────────────────────────────────────────

    def _write_codex_session(self, sd, name, repo):
        """Write a minimal Codex JSONL file under a date-tree path."""
        dated = sd / '2026' / '06' / '16'
        dated.mkdir(parents=True, exist_ok=True)
        path = dated / name
        path.write_text(
            json.dumps({'type': 'session_meta', 'payload': {'cwd': repo}}) + '\n'
        )
        return path

    def test_resolve_codex_by_uuid_prefix(self, tmp_path, monkeypatch):
        import cram.audit as _audit_mod
        repo = str(tmp_path / 'repo')
        sd = tmp_path / 'codex_sessions'
        uuid = '019ece4f-5b3f-7e80-bd46-5cc7f20fac4f'
        fname = f'rollout-2026-06-16T21-00-00-{uuid}.jsonl'
        self._write_codex_session(sd, fname, repo)
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r: None)
        monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: str(sd))
        # prefix match on UUID
        result = _resolve_session_path('019ece4f', repo)
        assert result is not None and result.endswith(fname)

    def test_resolve_codex_full_uuid(self, tmp_path, monkeypatch):
        import cram.audit as _audit_mod
        repo = str(tmp_path / 'repo')
        sd = tmp_path / 'codex_sessions'
        uuid = '019ece4f-5b3f-7e80-bd46-5cc7f20fac4f'
        fname = f'rollout-2026-06-16T21-00-00-{uuid}.jsonl'
        self._write_codex_session(sd, fname, repo)
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r: None)
        monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: str(sd))
        result = _resolve_session_path(uuid, repo)
        assert result is not None and result.endswith(fname)

    def test_resolve_codex_miss_returns_none(self, tmp_path, monkeypatch):
        import cram.audit as _audit_mod
        sd = tmp_path / 'codex_sessions'
        sd.mkdir()
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r: None)
        monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: str(sd))
        assert _resolve_session_path('deadbeef', str(tmp_path)) is None

    def test_claude_takes_priority_over_codex(self, tmp_path, monkeypatch):
        """If both Claude and Codex have a match, Claude's per-repo dir wins."""
        import cram.audit as _audit_mod
        repo = str(tmp_path / 'repo')
        # Claude match
        td = tmp_path / 'claude_proj'
        td.mkdir()
        claude_path = td / 'abc-shared-prefix.jsonl'
        claude_path.write_text(json.dumps(_usage(cache_read=1)) + '\n')
        # Codex match with same prefix
        sd = tmp_path / 'codex_sessions'
        self._write_codex_session(sd, 'rollout-2026-abc-shared-00000000-0000-0000-0000-000000000000.jsonl', repo)
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r, d=str(td): d)
        monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: str(sd))
        result = _resolve_session_path('abc-shared', repo)
        assert result == str(claude_path)
