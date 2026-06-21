"""Tests for cram/audit.py — transcript analysis and efficiency metrics."""

from __future__ import annotations
import datetime
import io
import json


from cram.audit import (
    _analyze_transcript, _find_all_tool_use, collect_audit, ratio_band,
    AUDIT_TOK_PER_FILE, AUDIT_BASE_PRICE,
    _analyze_cursor_transcript, _analyze_cursor_workspace_db,
    _analyze_codex_transcript,
)


def _make_transcript(tool_calls: list[tuple[str, dict]], tmp_path) -> str:
    """Write a fake JSONL transcript with the given tool_use blocks."""
    path = str(tmp_path / 'session.jsonl')
    with open(path, 'w') as f:
        for name, inp in tool_calls:
            msg = {'type': 'tool_use', 'name': name, 'input': inp}
            f.write(json.dumps(msg) + '\n')
    return path


class TestAnalyzeTranscript:
    def test_counts_reads(self, tmp_path):
        path = _make_transcript([
            ('Read', {'file_path': 'foo.py'}),
            ('Read', {'file_path': 'bar.py'}),
            ('Edit', {'file_path': 'foo.py'}),
        ], tmp_path)
        r = _analyze_transcript(path)
        assert r is not None
        assert r['reads'] == 2

    def test_reads_before_edit(self, tmp_path):
        path = _make_transcript([
            ('Read', {}),
            ('Read', {}),
            ('Edit', {}),
            ('Read', {}),  # after edit — not counted in reads_before_edit
        ], tmp_path)
        r = _analyze_transcript(path)
        assert r['reads_before_edit'] == 2
        assert r['reads'] == 3

    def test_edits_counted(self, tmp_path):
        path = _make_transcript([
            ('Read', {}),
            ('Edit', {}),
            ('Write', {}),
            ('Edit', {}),
        ], tmp_path)
        r = _analyze_transcript(path)
        assert r['edits'] == 3

    def test_ratio_reads_before_edit_over_edits(self, tmp_path):
        path = _make_transcript([
            ('Read', {}),
            ('Read', {}),
            ('Read', {}),
            ('Read', {}),
            ('Edit', {}),
            ('Edit', {}),
        ], tmp_path)
        r = _analyze_transcript(path)
        assert r['reads_before_edit'] == 4
        assert r['edits'] == 2
        assert abs(r['ratio'] - 2.0) < 0.01

    def test_ratio_with_no_edits_uses_one(self, tmp_path):
        # reads_before_edit / max(edits, 1) — denominator floored at 1
        path = _make_transcript([
            ('Read', {}),
            ('Read', {}),
        ], tmp_path)
        r = _analyze_transcript(path)
        assert r['edits'] == 0
        assert r['ratio'] == 2.0

    def test_bash_read_commands_counted(self, tmp_path):
        path = _make_transcript([
            ('Bash', {'command': 'cat foo.py'}),
            ('Bash', {'command': 'grep -n def foo.py'}),
            ('Edit', {}),
        ], tmp_path)
        r = _analyze_transcript(path)
        assert r['reads_before_edit'] == 2

    def test_bash_write_not_counted_as_read(self, tmp_path):
        path = _make_transcript([
            ('Bash', {'command': 'python run_tests.py'}),
            ('Edit', {}),
        ], tmp_path)
        r = _analyze_transcript(path)
        assert r['reads_before_edit'] == 0

    def test_empty_file_returns_zero_metrics(self, tmp_path):
        path = str(tmp_path / 'empty.jsonl')
        open(path, 'w').close()
        r = _analyze_transcript(path)
        assert r is not None
        assert r['reads'] == 0
        assert r['edits'] == 0
        assert r['ratio'] == 0.0

    def test_returns_none_on_missing_file(self, tmp_path):
        r = _analyze_transcript(str(tmp_path / 'nonexistent.jsonl'))
        assert r is None

    def test_ratio_field_present_in_return(self, tmp_path):
        path = _make_transcript([('Read', {}), ('Edit', {})], tmp_path)
        r = _analyze_transcript(path)
        assert 'ratio' in r
        assert 'edits' in r

    def test_high_ratio_is_flagged(self, tmp_path):
        # 10 reads before 1 edit → ratio 10.0 (above 5× threshold)
        calls = [('Read', {})] * 10 + [('Edit', {})]
        path = _make_transcript(calls, tmp_path)
        r = _analyze_transcript(path)
        assert r['ratio'] > 5.0


class TestRatioBand:
    def test_bands(self):
        assert ratio_band(0.5) == 'good'
        assert ratio_band(1.99) == 'good'
        assert ratio_band(2.0) == 'normal'
        assert ratio_band(4.99) == 'normal'
        assert ratio_band(5.0) == 'high'
        assert ratio_band(12.0) == 'high'


def _write_transcript(path, tool_calls, usage=None):
    """Write a JSONL transcript with tool_use blocks and an optional usage block."""
    with open(path, 'w') as f:
        for name, inp in tool_calls:
            f.write(json.dumps({'type': 'tool_use', 'name': name, 'input': inp}) + '\n')
        if usage is not None:
            f.write(json.dumps({'usage': usage}) + '\n')


class TestCollectAudit:
    def _setup_transcripts(self, tmp_path, monkeypatch, sessions):
        import cram.audit as _audit_mod
        td = tmp_path / 'transcripts' / 'proj'
        td.mkdir(parents=True)
        for i, (calls, usage) in enumerate(sessions):
            _write_transcript(str(td / f's{i}.jsonl'), calls, usage)
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir',
                            lambda repo_root: str(td))
        return td

    def test_returns_none_without_transcripts(self, tmp_path, monkeypatch):
        import cram.audit as _audit_mod
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r: None)
        assert collect_audit(str(tmp_path)) is None

    def test_summary_fields(self, tmp_path, monkeypatch):
        self._setup_transcripts(tmp_path, monkeypatch, [
            ([('Read', {}), ('Read', {}), ('Edit', {})], None),
            ([('Read', {}), ('Edit', {}), ('Edit', {})], None),
        ])
        data = collect_audit(str(tmp_path), days=365)
        assert data is not None
        assert data['sessions'] == 2
        assert abs(data['avg_reads_before_edit'] - 1.5) < 0.01
        assert data['ratio_band'] in ('good', 'normal', 'high')
        assert len(data['recent']) == 2
        assert data['weekly']  # at least one weekly bucket

    def test_cache_blind_session_detected(self, tmp_path, monkeypatch):
        # One session reads from cache, one writes but never reads.
        self._setup_transcripts(tmp_path, monkeypatch, [
            ([('Read', {}), ('Edit', {})],
             {'cache_creation_input_tokens': 5000, 'cache_read_input_tokens': 90000}),
            ([('Read', {}), ('Edit', {})],
             {'cache_creation_input_tokens': 5000, 'cache_read_input_tokens': 0}),
        ])
        data = collect_audit(str(tmp_path), days=365)
        assert data['cache_engaged_sessions'] == 1
        assert data['cache_blind_sessions'] == 1

    def test_monthly_cost_not_inflated(self, tmp_path, monkeypatch):
        """Monthly orientation tax = cost/session × sessions/month — no extra ×30."""
        self._setup_transcripts(tmp_path, monkeypatch, [
            ([('Read', {}), ('Read', {}), ('Edit', {})], None),
        ])
        data = collect_audit(str(tmp_path), days=30)
        expected = data['orient_cost_per_session'] * data['sessions_per_month']
        assert abs(data['monthly_orient_cost'] - expected) < 1e-9

    def test_json_output_is_valid(self, tmp_path, monkeypatch, capsys):
        from cram.audit import run_audit
        self._setup_transcripts(tmp_path, monkeypatch, [
            ([('Read', {}), ('Edit', {})], None),
        ])
        run_audit(str(tmp_path), days=365, as_json=True)
        parsed = json.loads(capsys.readouterr().out)
        assert parsed['sessions'] == 1
        assert 'ratio_band' in parsed


class TestContextBloat:
    """Bucket-2 metrics: context growth, oversized tool results, redundant reads."""

    def _write_raw(self, path, messages):
        with open(path, 'w') as f:
            for msg in messages:
                f.write(json.dumps(msg) + '\n')

    def _usage(self, cache_read, input_tokens=0):
        return {'usage': {'cache_creation_input_tokens': 0,
                          'cache_read_input_tokens': cache_read,
                          'input_tokens': input_tokens}}

    def test_no_usage_blocks_yields_zero_bloat_fields(self, tmp_path):
        path = _make_transcript([('Read', {}), ('Edit', {})], tmp_path)
        r = _analyze_transcript(path)
        assert r['requests'] == 0
        assert r['avg_context_per_request'] == 0.0
        assert r['peak_context'] == 0
        assert r['tail_share'] is None
        assert r['carried_read_tokens'] == 0

    def test_context_growth_and_tail_share(self, tmp_path):
        # 6 requests with linearly growing context: 10k..60k.
        # Last third = requests 5,6 → (50+60)/210 ≈ 0.524
        path = str(tmp_path / 's.jsonl')
        self._write_raw(path, [self._usage(k * 10_000) for k in range(1, 7)])
        r = _analyze_transcript(path)
        assert r['requests'] == 6
        assert abs(r['avg_context_per_request'] - 35_000) < 1
        assert r['peak_context'] == 60_000
        assert abs(r['tail_share'] - 110 / 210) < 0.01

    def test_tail_share_none_for_short_sessions(self, tmp_path):
        path = str(tmp_path / 's.jsonl')
        self._write_raw(path, [self._usage(10_000) for _ in range(5)])
        r = _analyze_transcript(path)
        assert r['tail_share'] is None

    def test_oversized_result_and_carried_tokens(self, tmp_path):
        # Big result lands after 2 requests; 4 more requests follow → carried
        # tokens = est_tokens × 4.
        path = str(tmp_path / 's.jsonl')
        big = {'type': 'tool_result', 'content': 'x' * 30_000}
        msgs = ([self._usage(10_000)] * 2 + [big] + [self._usage(10_000)] * 4)
        self._write_raw(path, msgs)
        r = _analyze_transcript(path)
        assert r['big_results'] == 1
        est_tokens = len(json.dumps('x' * 30_000)) // 4
        assert r['carried_read_tokens'] == est_tokens * 4

    def test_small_result_not_counted(self, tmp_path):
        path = str(tmp_path / 's.jsonl')
        self._write_raw(path, [{'type': 'tool_result', 'content': 'small'}])
        r = _analyze_transcript(path)
        assert r['big_results'] == 0

    def test_redundant_reads_counted(self, tmp_path):
        path = _make_transcript([
            ('Read', {'file_path': 'a.py'}),
            ('Read', {'file_path': 'a.py'}),
            ('Read', {'file_path': 'a.py'}),
            ('Read', {'file_path': 'b.py'}),
        ], tmp_path)
        r = _analyze_transcript(path)
        assert r['redundant_reads'] == 2

    def test_collect_audit_aggregates_bloat(self, tmp_path, monkeypatch):
        import cram.audit as _audit_mod
        td = tmp_path / 'transcripts' / 'proj'
        td.mkdir(parents=True)
        msgs = ([self._usage(10_000)] * 2 +
                [{'type': 'tool_result', 'content': 'x' * 30_000}] +
                [self._usage(20_000)] * 4)
        self._write_raw(str(td / 's0.jsonl'), msgs)
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir',
                            lambda repo_root: str(td))
        data = collect_audit(str(tmp_path), days=365)
        assert data['avg_requests'] == 6
        assert data['peak_context'] == 20_000
        assert data['sessions_with_big_results'] == 1
        assert data['carried_cost_per_session'] > 0
        assert data['bloat_sessions_measured'] == 1
        assert 'big_result_bytes' in data


class TestRetryLoops:
    """Bucket-3 metrics: failed tool calls and same-file edit churn."""

    def _write_raw(self, path, messages):
        with open(path, 'w') as f:
            for msg in messages:
                f.write(json.dumps(msg) + '\n')

    def test_error_results_counted(self, tmp_path):
        path = str(tmp_path / 's.jsonl')
        self._write_raw(path, [
            {'type': 'tool_result', 'content': 'boom', 'is_error': True},
            {'type': 'tool_result', 'content': 'fine'},
            {'type': 'tool_result', 'content': 'fine', 'is_error': False},
            {'type': 'tool_result', 'content': 'boom again', 'is_error': True},
        ])
        r = _analyze_transcript(path)
        assert r['error_results'] == 2

    def test_no_errors_yields_zero(self, tmp_path):
        path = _make_transcript([('Read', {}), ('Edit', {})], tmp_path)
        r = _analyze_transcript(path)
        assert r['error_results'] == 0
        assert r['edit_churn'] == 0

    def test_edit_churn_counts_repeat_edits(self, tmp_path):
        # 3 edits to a.py (churn 2) + 1 to b.py (churn 0) → 2
        path = _make_transcript([
            ('Edit', {'file_path': 'a.py'}),
            ('Edit', {'file_path': 'a.py'}),
            ('Write', {'file_path': 'a.py'}),
            ('Edit', {'file_path': 'b.py'}),
        ], tmp_path)
        r = _analyze_transcript(path)
        assert r['edit_churn'] == 2

    def test_edits_without_file_path_ignored(self, tmp_path):
        path = _make_transcript([
            ('Edit', {}),
            ('Edit', {}),
            ('Write', {'command': 'no path here'}),
        ], tmp_path)
        r = _analyze_transcript(path)
        assert r['edits'] == 3
        assert r['edit_churn'] == 0

    def test_collect_audit_aggregates_retry_loops(self, tmp_path, monkeypatch):
        import cram.audit as _audit_mod
        td = tmp_path / 'transcripts' / 'proj'
        td.mkdir(parents=True)
        # Session 0: 2 failures + churn 1; session 1: clean.
        self._write_raw(str(td / 's0.jsonl'), [
            {'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': 'a.py'}},
            {'type': 'tool_result', 'content': 'boom', 'is_error': True},
            {'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': 'a.py'}},
            {'type': 'tool_result', 'content': 'boom', 'is_error': True},
        ])
        self._write_raw(str(td / 's1.jsonl'), [
            {'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': 'b.py'}},
            {'type': 'tool_result', 'content': 'fine'},
        ])
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir',
                            lambda repo_root: str(td))
        data = collect_audit(str(tmp_path), days=365)
        assert abs(data['avg_error_results'] - 1.0) < 0.01
        assert abs(data['avg_edit_churn'] - 0.5) < 0.01
        assert data['sessions_with_errors'] == 1


class TestRunCompare:
    """cram audit --compare — side-by-side A/B view for the P0 experiment."""

    def _setup_two_repos(self, tmp_path, monkeypatch):
        import cram.audit as _audit_mod
        td_a = tmp_path / 'transcripts' / 'repo-a'
        td_b = tmp_path / 'transcripts' / 'repo-b'
        td_a.mkdir(parents=True)
        td_b.mkdir(parents=True)
        # A: heavy orientation (4 reads before edit). B: light (1 read).
        _write_transcript(str(td_a / 's.jsonl'),
                          [('Read', {})] * 4 + [('Edit', {})])
        _write_transcript(str(td_b / 's.jsonl'),
                          [('Read', {}), ('Edit', {})])
        repo_a = str(tmp_path / 'repo-a')
        repo_b = str(tmp_path / 'repo-b')
        mapping = {repo_a: str(td_a), repo_b: str(td_b)}
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir',
                            lambda root: mapping.get(root))
        return repo_a, repo_b

    def test_compare_prints_both_columns_and_delta(self, tmp_path, monkeypatch, capsys):
        from cram.audit import run_compare
        repo_a, repo_b = self._setup_two_repos(tmp_path, monkeypatch)
        run_compare(repo_a, repo_b, days=365)
        out = capsys.readouterr().out
        assert 'repo-a' in out and 'repo-b' in out
        assert 'Reads before first edit' in out
        # A=4.0, B=1.0 → Δ=-3.0, -75%
        assert '-3.0' in out
        assert '-75%' in out

    def test_compare_json_contains_both_arms(self, tmp_path, monkeypatch, capsys):
        from cram.audit import run_compare
        repo_a, repo_b = self._setup_two_repos(tmp_path, monkeypatch)
        run_compare(repo_a, repo_b, days=365, as_json=True)
        parsed = json.loads(capsys.readouterr().out)
        assert parsed['a']['data']['avg_reads_before_edit'] == 4.0
        assert parsed['b']['data']['avg_reads_before_edit'] == 1.0

    def test_compare_missing_arm_reports_cleanly(self, tmp_path, monkeypatch, capsys):
        import cram.audit as _audit_mod
        from cram.audit import run_compare
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda root: None)
        run_compare(str(tmp_path / 'x'), str(tmp_path / 'y'), days=30)
        out = capsys.readouterr().out
        assert 'No sessions found' in out


class TestFindAllToolUse:
    def test_finds_top_level(self):
        obj = {'type': 'tool_use', 'name': 'Read', 'input': {}}
        assert _find_all_tool_use(obj) == [obj]

    def test_finds_nested(self):
        obj = {'content': [{'type': 'tool_use', 'name': 'Edit', 'input': {}}]}
        result = _find_all_tool_use(obj)
        assert len(result) == 1
        assert result[0]['name'] == 'Edit'

    def test_empty_object(self):
        assert _find_all_tool_use({}) == []

    def test_depth_limit(self):
        # Build deeply nested structure that exceeds depth limit
        obj = {}
        current = obj
        for _ in range(10):
            current['child'] = {}
            current = current['child']
        current['type'] = 'tool_use'
        # Should not crash or loop; depth limit returns empty
        result = _find_all_tool_use(obj)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# B5: Module constants (AUDIT_TOK_PER_FILE, AUDIT_BASE_PRICE) and caveat output
# ---------------------------------------------------------------------------

class TestAuditConstants:
    def test_constants_have_expected_defaults(self):
        """AUDIT_TOK_PER_FILE defaults to 2500 and AUDIT_BASE_PRICE to Sonnet base."""
        assert AUDIT_TOK_PER_FILE == 2500
        assert abs(AUDIT_BASE_PRICE - 3.0 / 1_000_000) < 1e-12

    def test_constants_are_overridable_via_env(self, monkeypatch):
        """Env vars CRAM_AUDIT_TOK_PER_FILE and CRAM_AUDIT_BASE_PRICE override defaults."""
        monkeypatch.setenv('CRAM_AUDIT_TOK_PER_FILE', '1000')
        monkeypatch.setenv('CRAM_AUDIT_BASE_PRICE', '0.000005')
        # Re-import to pick up env overrides
        import importlib
        import cram.audit as _audit_mod
        importlib.reload(_audit_mod)
        try:
            assert _audit_mod.AUDIT_TOK_PER_FILE == 1000
            assert abs(_audit_mod.AUDIT_BASE_PRICE - 0.000005) < 1e-12
        finally:
            importlib.reload(_audit_mod)  # restore defaults

    def test_provider_wiring_local_zeroes_prices(self, monkeypatch):
        """CRAM_PROVIDER=local zeroes AUDIT_BASE_PRICE and CACHE_READ_MULT on reload."""
        monkeypatch.setenv('CRAM_PROVIDER', 'local')
        monkeypatch.delenv('CRAM_AUDIT_BASE_PRICE', raising=False)
        import importlib
        import cram.audit as _audit_mod
        importlib.reload(_audit_mod)
        try:
            assert _audit_mod.AUDIT_PROVIDER == 'local'
            assert _audit_mod.AUDIT_BASE_PRICE == 0.0
            assert _audit_mod.CACHE_READ_MULT == 0.0
        finally:
            monkeypatch.delenv('CRAM_PROVIDER', raising=False)
            importlib.reload(_audit_mod)  # restore defaults

    def test_run_audit_prints_modelled_caveat(self, tmp_path, monkeypatch):
        """run_audit output contains the modelled-cost caveat line."""
        from cram.audit import run_audit
        import cram.audit as _audit_mod

        # Create a minimal transcript with reads+edits
        td = tmp_path / 'transcripts' / 'cram-ai'
        td.mkdir(parents=True)
        transcript = td / 'session.jsonl'
        calls = [
            ('Read', {'file_path': 'a.py'}),
            ('Read', {'file_path': 'b.py'}),
            ('Edit', {'file_path': 'a.py'}),
        ]
        with open(transcript, 'w') as f:
            for name, inp in calls:
                f.write(json.dumps({'type': 'tool_use', 'name': name, 'input': inp}) + '\n')

        # Patch _project_transcript_dir to return our tmp transcript dir
        monkeypatch.setattr(_audit_mod, '_project_transcript_dir',
                            lambda repo_root: str(td))

        buf = io.StringIO()
        import sys
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            run_audit(str(tmp_path), days=365)
        finally:
            sys.stdout = old_stdout

        output = buf.getvalue()
        assert 'modelled' in output or 'Modelled' in output or 'reads_before_edit' in output
        # The caveat note should mention tok/file assumption
        assert 'tok/file' in output or 'AUDIT_TOK_PER_FILE' in output or '2,500' in output

    def test_analyze_transcript_returns_required_fields(self, tmp_path):
        """_analyze_transcript returns reads, reads_before_edit, edits, and ratio."""
        path = str(tmp_path / 'session.jsonl')
        with open(path, 'w') as f:
            for name, inp in [('Read', {}), ('Read', {}), ('Edit', {})]:
                f.write(json.dumps({'type': 'tool_use', 'name': name, 'input': inp}) + '\n')

        r = _analyze_transcript(path)
        assert r is not None
        assert r['reads'] == 2
        assert r['reads_before_edit'] == 2
        assert r['edits'] == 1
        assert abs(r['ratio'] - 2.0) < 0.01


# ---------------------------------------------------------------------------
# Cursor support
# ---------------------------------------------------------------------------

def _write_cursor_jsonl(path: str, repo_root: str, events: list[tuple[str, list[str]]]) -> None:
    """Write a Cursor agent-transcript JSONL file.

    events: list of (tool_name, [file_path, ...]) tuples.
    """
    with open(path, 'w') as f:
        for tool, files in events:
            entry = {
                'version': 1,
                'tool': tool,
                'vcs': {'root': repo_root},
                'files': files,
                'input': {'target_file': files[0]} if files else {},
                'timestamp': 1_700_000_000_000,
            }
            f.write(json.dumps(entry) + '\n')


class TestCursorTranscript:
    """_analyze_cursor_transcript — Cursor JSONL agent-transcript parser."""

    def test_counts_reads_and_edits(self, tmp_path):
        repo = '/repo/myproject'
        path = str(tmp_path / 'session.jsonl')
        _write_cursor_jsonl(path, repo, [
            ('read_file',  [repo + '/a.py']),
            ('grep_search', [repo + '/a.py']),
            ('edit_file',  [repo + '/a.py']),
        ])
        r = _analyze_cursor_transcript(path, repo)
        assert r is not None
        assert r['reads'] == 2
        assert r['reads_before_edit'] == 2
        assert r['edits'] == 1
        assert abs(r['ratio'] - 2.0) < 0.01

    def test_reads_after_first_edit_not_counted(self, tmp_path):
        repo = '/repo/proj'
        path = str(tmp_path / 's.jsonl')
        _write_cursor_jsonl(path, repo, [
            ('read_file',  [repo + '/x.py']),
            ('edit_file',  [repo + '/x.py']),
            ('read_file',  [repo + '/y.py']),  # after edit — not in reads_before_edit
        ])
        r = _analyze_cursor_transcript(path, repo)
        assert r['reads'] == 2
        assert r['reads_before_edit'] == 1

    def test_filters_out_other_repo_events(self, tmp_path):
        repo = '/repo/mine'
        other = '/repo/other'
        path = str(tmp_path / 's.jsonl')
        with open(path, 'w') as f:
            # other repo read
            f.write(json.dumps({'version': 1, 'tool': 'read_file',
                                'vcs': {'root': other},
                                'files': [other + '/foo.py']}) + '\n')
            # our repo edit
            f.write(json.dumps({'version': 1, 'tool': 'edit_file',
                                'vcs': {'root': repo},
                                'files': [repo + '/bar.py']}) + '\n')
        r = _analyze_cursor_transcript(path, repo)
        assert r is not None
        assert r['reads'] == 0
        assert r['edits'] == 1

    def test_returns_none_when_no_relevant_activity(self, tmp_path):
        repo = '/repo/mine'
        other = '/repo/other'
        path = str(tmp_path / 's.jsonl')
        _write_cursor_jsonl(path, other, [
            ('read_file', [other + '/foo.py']),
        ])
        r = _analyze_cursor_transcript(path, repo)
        assert r is None

    def test_redundant_reads_counted(self, tmp_path):
        repo = '/repo/p'
        path = str(tmp_path / 's.jsonl')
        _write_cursor_jsonl(path, repo, [
            ('read_file', [repo + '/a.py']),
            ('read_file', [repo + '/a.py']),
            ('read_file', [repo + '/a.py']),
            ('read_file', [repo + '/b.py']),
        ])
        r = _analyze_cursor_transcript(path, repo)
        assert r['redundant_reads'] == 2

    def test_edit_churn_counted(self, tmp_path):
        repo = '/repo/p'
        path = str(tmp_path / 's.jsonl')
        _write_cursor_jsonl(path, repo, [
            ('edit_file', [repo + '/a.py']),
            ('edit_file', [repo + '/a.py']),
            ('edit_file', [repo + '/b.py']),
        ])
        r = _analyze_cursor_transcript(path, repo)
        assert r['edit_churn'] == 1  # a.py edited twice → churn 1

    def test_token_fields_zeroed(self, tmp_path):
        repo = '/repo/p'
        path = str(tmp_path / 's.jsonl')
        _write_cursor_jsonl(path, repo, [
            ('read_file', [repo + '/a.py']),
            ('edit_file', [repo + '/a.py']),
        ])
        r = _analyze_cursor_transcript(path, repo)
        assert r is not None
        assert r['cache_writes'] == 0
        assert r['cache_reads'] == 0
        assert r['requests'] == 0
        assert r['peak_context'] == 0
        assert r['tail_share'] is None
        assert r['source'] == 'cursor'

    def test_codebase_search_counted_as_read(self, tmp_path):
        repo = '/repo/p'
        path = str(tmp_path / 's.jsonl')
        _write_cursor_jsonl(path, repo, [
            ('codebase_search', [repo + '/a.py']),
            ('edit_file',       [repo + '/a.py']),
        ])
        r = _analyze_cursor_transcript(path, repo)
        assert r['reads_before_edit'] == 1

    def test_run_terminal_command_with_grep_counted_as_read(self, tmp_path):
        repo = '/repo/p'
        path = str(tmp_path / 's.jsonl')
        with open(path, 'w') as f:
            f.write(json.dumps({
                'tool': 'run_terminal_command',
                'vcs': {'root': repo},
                'files': [repo + '/a.py'],
                'input': {'command': 'grep -n def a.py'},
            }) + '\n')
            f.write(json.dumps({
                'tool': 'edit_file',
                'vcs': {'root': repo},
                'files': [repo + '/a.py'],
            }) + '\n')
        r = _analyze_cursor_transcript(path, repo)
        assert r['reads_before_edit'] == 1

    def test_empty_file_returns_none(self, tmp_path):
        path = str(tmp_path / 'empty.jsonl')
        open(path, 'w').close()
        r = _analyze_cursor_transcript(path, '/repo/p')
        assert r is None

    def test_missing_file_returns_none(self, tmp_path):
        r = _analyze_cursor_transcript(str(tmp_path / 'nope.jsonl'), '/repo/p')
        assert r is None


class TestCursorWorkspaceDb:
    """_analyze_cursor_workspace_db — SQLite workspace parser."""

    def _make_db(self, path: str, bubbles: list[dict]) -> None:
        import sqlite3
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE cursorDiskKV (key TEXT, value TEXT)')
        for bubble in bubbles:
            composer_id = bubble.pop('_composerId', 'comp1')
            bubble_id   = bubble.pop('_bubbleId',   f'bub{id(bubble)}')
            key = f'bubbleId:{composer_id}:{bubble_id}'
            con.execute('INSERT INTO cursorDiskKV VALUES (?, ?)',
                        (key, json.dumps(bubble)))
        con.commit()
        con.close()

    def test_reads_and_edits_from_sqlite(self, tmp_path):
        repo = '/repo/p'
        db = str(tmp_path / 'state.vscdb')
        self._make_db(db, [
            {'_composerId': 'c1', '_bubbleId': 'b1', 'type': 2, 'createdAt': 1_700_000_001_000,
             'toolFormerData': [
                 {'toolName': 'read_file', 'params': {'target_file': repo + '/a.py'}},
                 {'toolName': 'read_file', 'params': {'target_file': repo + '/b.py'}},
             ]},
            {'_composerId': 'c1', '_bubbleId': 'b2', 'type': 2, 'createdAt': 1_700_000_002_000,
             'toolFormerData': [
                 {'toolName': 'edit_file', 'params': {'target_file': repo + '/a.py'}},
             ]},
        ])
        cutoff = datetime.datetime(2020, 1, 1)
        result = _analyze_cursor_workspace_db(db, repo, cutoff)
        assert len(result) == 1
        sess = result[0]
        assert sess['reads'] == 2
        assert sess['reads_before_edit'] == 2
        assert sess['edits'] == 1
        assert sess['source'] == 'cursor'

    def test_filters_other_repo(self, tmp_path):
        repo = '/repo/mine'
        other = '/repo/other'
        db = str(tmp_path / 'state.vscdb')
        self._make_db(db, [
            {'_composerId': 'c1', '_bubbleId': 'b1', 'type': 2, 'createdAt': 1_700_000_001_000,
             'toolFormerData': [
                 {'toolName': 'read_file', 'params': {'target_file': other + '/x.py'}},
             ]},
        ])
        cutoff = datetime.datetime(2020, 1, 1)
        result = _analyze_cursor_workspace_db(db, repo, cutoff)
        assert result == []

    def test_groups_by_composer_id(self, tmp_path):
        repo = '/repo/p'
        db = str(tmp_path / 'state.vscdb')
        self._make_db(db, [
            {'_composerId': 'c1', '_bubbleId': 'b1', 'type': 2, 'createdAt': 1_700_000_001_000,
             'toolFormerData': [{'toolName': 'read_file',
                                 'params': {'target_file': repo + '/a.py'}}]},
            {'_composerId': 'c2', '_bubbleId': 'b2', 'type': 2, 'createdAt': 1_700_000_001_000,
             'toolFormerData': [{'toolName': 'edit_file',
                                 'params': {'target_file': repo + '/b.py'}}]},
        ])
        cutoff = datetime.datetime(2020, 1, 1)
        result = _analyze_cursor_workspace_db(db, repo, cutoff)
        # Two separate composers → two sessions
        assert len(result) == 2

    def test_missing_table_returns_empty(self, tmp_path):
        import sqlite3
        db = str(tmp_path / 'state.vscdb')
        con = sqlite3.connect(db)
        con.execute('CREATE TABLE unrelated (k TEXT, v TEXT)')
        con.commit()
        con.close()
        result = _analyze_cursor_workspace_db(db, '/repo/p', datetime.datetime(2020, 1, 1))
        assert result == []

    def test_returns_empty_for_nonexistent_file(self, tmp_path):
        result = _analyze_cursor_workspace_db(
            str(tmp_path / 'nope.vscdb'), '/repo/p', datetime.datetime(2020, 1, 1)
        )
        assert result == []


class TestCollectAuditWithCursor:
    """collect_audit merges Cursor sessions when Claude transcripts absent or present."""

    def test_cursor_sessions_included_when_no_claude_transcripts(
        self, tmp_path, monkeypatch
    ):
        import cram.audit as _audit_mod

        repo = str(tmp_path / 'repo')
        at_dir = tmp_path / 'agent-transcripts'
        at_dir.mkdir()
        path = str(at_dir / 'session.jsonl')
        _write_cursor_jsonl(path, repo, [
            ('read_file',  [repo + '/a.py']),
            ('read_file',  [repo + '/b.py']),
            ('edit_file',  [repo + '/a.py']),
        ])

        monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r: None)
        monkeypatch.setattr(_audit_mod, '_cursor_agent_transcripts_dir',
                            lambda: str(at_dir))

        data = _audit_mod.collect_audit(repo, days=365)
        assert data is not None
        assert data['sessions'] == 1
        assert data['avg_reads_before_edit'] == 2.0

    def test_cursor_sessions_merged_with_claude_sessions(self, tmp_path, monkeypatch):
        import cram.audit as _audit_mod

        repo = str(tmp_path / 'repo')

        # Claude Code transcript
        claude_dir = tmp_path / 'claude'
        claude_dir.mkdir()
        with open(str(claude_dir / 'claude.jsonl'), 'w') as f:
            for name, inp in [('Read', {'file_path': repo + '/c.py'}), ('Edit', {})]:
                f.write(json.dumps({'type': 'tool_use', 'name': name, 'input': inp}) + '\n')

        # Cursor transcript
        at_dir = tmp_path / 'agent-transcripts'
        at_dir.mkdir()
        cursor_path = str(at_dir / 'cursor.jsonl')
        _write_cursor_jsonl(cursor_path, repo, [
            ('read_file',  [repo + '/a.py']),
            ('read_file',  [repo + '/b.py']),
            ('edit_file',  [repo + '/a.py']),
        ])

        monkeypatch.setattr(_audit_mod, '_project_transcript_dir',
                            lambda r: str(claude_dir))
        monkeypatch.setattr(_audit_mod, '_cursor_agent_transcripts_dir',
                            lambda: str(at_dir))

        data = _audit_mod.collect_audit(repo, days=365)
        assert data is not None
        assert data['sessions'] == 2
        # claude: rbe=1, cursor: rbe=2 → avg=1.5
        assert abs(data['avg_reads_before_edit'] - 1.5) < 0.01


# ---------------------------------------------------------------------------
# Codex transcript tests
# ---------------------------------------------------------------------------

def _write_codex_jsonl(path: str, session_cwd: str, events: list[dict]) -> None:
    """Write a minimal Codex JSONL session file."""
    with open(path, 'w') as f:
        # session_meta header
        f.write(json.dumps({
            'type': 'session_meta',
            'payload': {'cwd': session_cwd},
        }) + '\n')
        for ev in events:
            f.write(json.dumps(ev) + '\n')


def _exec_cmd(cmd: str, workdir: str = '') -> dict:
    return {
        'type': 'response_item',
        'payload': {
            'type': 'function_call',
            'name': 'exec_command',
            'arguments': json.dumps({'cmd': cmd, 'workdir': workdir}),
        },
    }


def _apply_patch(patch_text: str) -> dict:
    return {
        'type': 'response_item',
        'payload': {
            'type': 'custom_tool_call',
            'name': 'apply_patch',
            'input': patch_text,
        },
    }


def _token_count(input_tokens: int, cached_input_tokens: int = 0, output_tokens: int = 0) -> dict:
    return {
        'type': 'event_msg',
        'payload': {
            'type': 'token_count',
            'info': {
                'last_token_usage': {
                    'input_tokens': input_tokens,
                    'cached_input_tokens': cached_input_tokens,
                    'output_tokens': output_tokens,
                },
            },
        },
    }


class TestCodexTranscript:
    def test_counts_reads_and_edits(self, tmp_path):
        repo = str(tmp_path / 'repo')
        path = str(tmp_path / 'session.jsonl')
        _write_codex_jsonl(path, repo, [
            _exec_cmd(f'cat {repo}/a.py', repo),
            _exec_cmd(f'cat {repo}/b.py', repo),
            _apply_patch(f'*** Update File: {repo}/a.py\n--- old\n+++ new\n'),
        ])
        r = _analyze_codex_transcript(path, repo)
        assert r is not None
        assert r['reads'] == 2
        assert r['edits'] == 1
        assert r['reads_before_edit'] == 2

    def test_reads_after_first_edit_not_in_reads_before_edit(self, tmp_path):
        repo = str(tmp_path / 'repo')
        path = str(tmp_path / 'session.jsonl')
        _write_codex_jsonl(path, repo, [
            _exec_cmd(f'cat {repo}/a.py', repo),
            _apply_patch(f'*** Update File: {repo}/a.py\n--- old\n+++ new\n'),
            _exec_cmd(f'cat {repo}/b.py', repo),  # post-edit read
        ])
        r = _analyze_codex_transcript(path, repo)
        assert r is not None
        assert r['reads'] == 2
        assert r['reads_before_edit'] == 1

    def test_filters_other_repo_events(self, tmp_path):
        repo = str(tmp_path / 'repo')
        other = str(tmp_path / 'other')
        path = str(tmp_path / 'session.jsonl')
        _write_codex_jsonl(path, repo, [
            _exec_cmd(f'cat {other}/a.py', other),   # different workdir
            _apply_patch(f'*** Update File: {other}/a.py\n--- old\n+++ new\n'),
        ])
        # session_cwd is repo but all activity is in other
        r = _analyze_codex_transcript(path, repo)
        # reads=0, edits=0 → returns None
        assert r is None

    def test_returns_none_when_no_relevant_activity(self, tmp_path):
        repo = str(tmp_path / 'repo')
        path = str(tmp_path / 'session.jsonl')
        _write_codex_jsonl(path, repo, [])
        assert _analyze_codex_transcript(path, repo) is None

    def test_token_fields_populated_from_token_count(self, tmp_path):
        repo = str(tmp_path / 'repo')
        path = str(tmp_path / 'session.jsonl')
        _write_codex_jsonl(path, repo, [
            _exec_cmd(f'cat {repo}/a.py', repo),
            _token_count(input_tokens=1000, cached_input_tokens=200, output_tokens=50),
            _apply_patch(f'*** Update File: {repo}/a.py\n--- old\n+++ new\n'),
        ])
        r = _analyze_codex_transcript(path, repo)
        assert r is not None
        assert r['cache_reads'] == 200
        assert r['requests'] == 1
        assert r['avg_context_per_request'] == 1200  # 1000 + 200
        assert r['avg_output_tokens'] == 50.0

    def test_context_growth_factor(self, tmp_path):
        repo = str(tmp_path / 'repo')
        path = str(tmp_path / 'session.jsonl')
        _write_codex_jsonl(path, repo, [
            _exec_cmd(f'cat {repo}/a.py', repo),
            _token_count(input_tokens=500),
            _apply_patch(f'*** Update File: {repo}/a.py\n--- old\n+++ new\n'),
            _token_count(input_tokens=2000),
        ])
        r = _analyze_codex_transcript(path, repo)
        assert r is not None
        # growth = 2000 / 500 = 4.0
        assert abs(r['context_growth_factor'] - 4.0) < 0.01

    def test_edit_churn_counted(self, tmp_path):
        repo = str(tmp_path / 'repo')
        path = str(tmp_path / 'session.jsonl')
        file_a = f'{repo}/a.py'
        _write_codex_jsonl(path, repo, [
            _apply_patch(f'*** Update File: {file_a}\n--- old\n+++ new1\n'),
            _apply_patch(f'*** Update File: {file_a}\n--- old\n+++ new2\n'),
        ])
        r = _analyze_codex_transcript(path, repo)
        assert r is not None
        assert r['edit_churn'] == 1  # file edited twice: churn = 2 - 1 = 1

    def test_add_file_patch_counted_as_edit(self, tmp_path):
        repo = str(tmp_path / 'repo')
        path = str(tmp_path / 'session.jsonl')
        _write_codex_jsonl(path, repo, [
            _apply_patch(f'*** Add File: {repo}/new.py\n+++ content\n'),
        ])
        r = _analyze_codex_transcript(path, repo)
        assert r is not None
        assert r['edits'] == 1

    def test_arguments_as_dict_not_string(self, tmp_path):
        """Verify parser doesn't crash when arguments is already a dict (defensive)."""
        repo = str(tmp_path / 'repo')
        path = str(tmp_path / 'session.jsonl')
        with open(path, 'w') as f:
            f.write(json.dumps({'type': 'session_meta', 'payload': {'cwd': repo}}) + '\n')
            f.write(json.dumps({
                'type': 'response_item',
                'payload': {
                    'type': 'function_call',
                    'name': 'exec_command',
                    'arguments': {'cmd': f'cat {repo}/a.py', 'workdir': repo},
                },
            }) + '\n')
            f.write(json.dumps({
                'type': 'response_item',
                'payload': {
                    'type': 'custom_tool_call',
                    'name': 'apply_patch',
                    'input': f'*** Update File: {repo}/a.py\n--- old\n+++ new\n',
                },
            }) + '\n')
        r = _analyze_codex_transcript(path, repo)
        assert r is not None
        assert r['reads'] == 1

    def test_missing_file_returns_none(self, tmp_path):
        repo = str(tmp_path / 'repo')
        assert _analyze_codex_transcript(str(tmp_path / 'nonexistent.jsonl'), repo) is None

    def test_source_field_is_codex(self, tmp_path):
        repo = str(tmp_path / 'repo')
        path = str(tmp_path / 'session.jsonl')
        _write_codex_jsonl(path, repo, [
            _exec_cmd(f'cat {repo}/a.py', repo),
            _apply_patch(f'*** Update File: {repo}/a.py\n--- old\n+++ new\n'),
        ])
        r = _analyze_codex_transcript(path, repo)
        assert r is not None
        assert r['source'] == 'codex'


class TestCollectAuditWithCodex:
    def test_codex_sessions_included_when_no_claude_transcripts(self, tmp_path, monkeypatch):
        import cram.audit as _audit_mod

        repo = str(tmp_path / 'repo')
        sessions_dir = tmp_path / 'codex-sessions'
        sessions_dir.mkdir()
        path = str(sessions_dir / 'session.jsonl')
        _write_codex_jsonl(path, repo, [
            _exec_cmd(f'cat {repo}/a.py', repo),
            _exec_cmd(f'cat {repo}/b.py', repo),
            _apply_patch(f'*** Update File: {repo}/a.py\n--- old\n+++ new\n'),
        ])

        monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r: None)
        monkeypatch.setattr(_audit_mod, '_cursor_agent_transcripts_dir', lambda: None)
        monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: str(sessions_dir))

        data = _audit_mod.collect_audit(repo, days=365)
        assert data is not None
        assert data['sessions'] == 1
        assert data['avg_reads_before_edit'] == 2.0

    def test_codex_sessions_merged_with_claude_sessions(self, tmp_path, monkeypatch):
        import cram.audit as _audit_mod

        repo = str(tmp_path / 'repo')

        claude_dir = tmp_path / 'claude'
        claude_dir.mkdir()
        with open(str(claude_dir / 'claude.jsonl'), 'w') as f:
            for name, inp in [('Read', {'file_path': repo + '/c.py'}), ('Edit', {})]:
                f.write(json.dumps({'type': 'tool_use', 'name': name, 'input': inp}) + '\n')

        sessions_dir = tmp_path / 'codex-sessions'
        sessions_dir.mkdir()
        codex_path = str(sessions_dir / 'session.jsonl')
        _write_codex_jsonl(codex_path, repo, [
            _exec_cmd(f'cat {repo}/a.py', repo),
            _exec_cmd(f'cat {repo}/b.py', repo),
            _apply_patch(f'*** Update File: {repo}/a.py\n--- old\n+++ new\n'),
        ])

        monkeypatch.setattr(_audit_mod, '_project_transcript_dir',
                            lambda r: str(claude_dir))
        monkeypatch.setattr(_audit_mod, '_cursor_agent_transcripts_dir', lambda: None)
        monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: str(sessions_dir))

        data = _audit_mod.collect_audit(repo, days=365)
        assert data is not None
        assert data['sessions'] == 2
        # claude: rbe=1, codex: rbe=2 → avg=1.5
        assert abs(data['avg_reads_before_edit'] - 1.5) < 0.01
