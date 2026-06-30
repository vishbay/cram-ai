"""Direct unit coverage for the transcript adapters in `cram.audit_events`.

`audit_events` is the parsing core: it turns untrusted JSONL/SQLite transcripts
into normalized Event streams. The aggregate (`test_audit*`) and timeline
(`test_audit_timeline`) suites exercise it end-to-end, but neither targets the
adapters directly — especially their behavior on malformed or hostile input,
which is the security-relevant surface (`SECURITY.md` calls out "parsing of
untrusted transcript/JSONL files").

These tests pin: per-adapter classification (read / edit / tool_call / usage),
robustness (bad JSON lines skipped not raised, unreadable files → None, the
recursion depth cap), and the small pure helpers.
"""

from __future__ import annotations
import json

import pytest

from cram.audit_events import (
    Event,
    SessionMeta,
    parse_claude,
    parse_codex,
    parse_cursor_jsonl,
    parse_cursor_db,
    derive_session,
    repo_rel,
    estimate_read_tokens_from_counts,
    _is_context_tool,
    _session_ident,
    _cmd_label,
    _find_all_tool_use,
)


def _write_lines(path, objs):
    with open(path, 'w') as f:
        for o in objs:
            f.write((o if isinstance(o, str) else json.dumps(o)) + '\n')
    return str(path)


def _kinds(events):
    return [e.kind for e in events]


# ── parse_claude ──────────────────────────────────────────────────────────────

class TestParseClaude:
    def test_missing_file_returns_none(self, tmp_path):
        assert parse_claude(str(tmp_path / 'nope.jsonl')) is None

    def test_empty_file_yields_no_events(self, tmp_path):
        p = _write_lines(tmp_path / 's.jsonl', [])
        meta, events = parse_claude(p)
        assert meta.adapter == 'claude' and meta.source == 'claude'
        assert events == []

    def test_malformed_lines_skipped_not_raised(self, tmp_path):
        p = _write_lines(tmp_path / 's.jsonl', [
            '{not json at all',
            '',
            json.dumps({'type': 'tool_use', 'name': 'Read',
                        'input': {'file_path': '/r/a.py'}}),
            'null',                       # valid JSON, but not a dict
            '12345',                      # valid JSON scalar
        ])
        meta, events = parse_claude(p)
        # The single well-formed Read survives; the garbage is silently dropped.
        assert _kinds(events) == ['read']
        assert events[0].file_path == '/r/a.py'

    def test_read_edit_and_bash_read_classification(self, tmp_path):
        p = _write_lines(tmp_path / 's.jsonl', [
            {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/r/a.py'}},
            {'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': '/r/b.py'}},
            {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'grep -n foo /r/a.py'}},
            {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'rm -rf build'}},
        ])
        _meta, events = parse_claude(p)
        assert _kinds(events) == ['read', 'edit', 'read', 'tool_call']
        # Bash reads carry no file_path (legacy behavior the metric loop relies on).
        assert events[2].file_path is None

    def test_usage_block_extracted(self, tmp_path):
        p = _write_lines(tmp_path / 's.jsonl', [
            {'message': {'usage': {
                'input_tokens': 100, 'output_tokens': 40,
                'cache_read_input_tokens': 200, 'cache_creation_input_tokens': 300}}},
        ])
        _meta, events = parse_claude(p)
        assert _kinds(events) == ['request_usage']
        u = events[0]
        assert (u.tok_input, u.tok_output, u.tok_cache_read, u.tok_cache_write) == (100, 40, 200, 300)

    def test_tool_result_size_and_error(self, tmp_path):
        big = 'x' * 5000
        p = _write_lines(tmp_path / 's.jsonl', [
            {'type': 'tool_result', 'content': big, 'is_error': True,
             'tool_use_id': 'tid-1'},
        ])
        _meta, events = parse_claude(p)
        assert _kinds(events) == ['tool_result']
        tr = events[0]
        assert tr.is_error is True
        assert tr.bytes >= 5000
        assert (tr.extras or {}).get('tool_use_id') == 'tid-1'

    def test_model_is_most_frequent(self, tmp_path):
        p = _write_lines(tmp_path / 's.jsonl', [
            {'model': 'claude-opus-4-8', 'message': {}},
            {'message': {'model': 'claude-sonnet-4-6'}},
            {'message': {'model': 'claude-sonnet-4-6'}},
        ])
        meta, _events = parse_claude(p)
        assert meta.model == 'claude-sonnet-4-6'

    def test_event_ordering_tooluse_then_usage_then_result(self, tmp_path):
        # A single line carrying all three block types must emit in the order the
        # carried-token math depends on: tool_use, then usage, then tool_result.
        p = _write_lines(tmp_path / 's.jsonl', [{
            'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/r/a.py'},
            'message': {'usage': {'input_tokens': 1, 'cache_creation_input_tokens': 0}},
            'extra': {'type': 'tool_result', 'content': 'hi'},
        }])
        _meta, events = parse_claude(p)
        assert _kinds(events) == ['read', 'request_usage', 'tool_result']
        assert [e.seq for e in events] == [0, 1, 2]

    def test_non_utf8_bytes_do_not_crash(self, tmp_path):
        p = tmp_path / 's.jsonl'
        line = json.dumps({'type': 'tool_use', 'name': 'Read',
                           'input': {'file_path': '/r/a.py'}})
        with open(p, 'wb') as f:
            f.write(b'\xff\xfe garbage bytes\n')         # invalid UTF-8
            f.write((line + '\n').encode())
        meta, events = parse_claude(str(p))             # errors='ignore' in the open
        assert meta is not None
        assert _kinds(events) == ['read']


# ── parse_codex ───────────────────────────────────────────────────────────────

class TestParseCodex:
    def test_exec_command_read_vs_tool_call(self, tmp_path):
        p = _write_lines(tmp_path / 's.jsonl', [
            {'type': 'session_meta', 'payload': {'cwd': '/repo'}},
            {'type': 'response_item', 'payload': {
                'type': 'function_call', 'name': 'exec_command', 'call_id': 'c1',
                'arguments': json.dumps({'cmd': 'cat /repo/a.py', 'workdir': '/repo'})}},
            {'type': 'response_item', 'payload': {
                'type': 'function_call', 'name': 'exec_command', 'call_id': 'c2',
                'arguments': json.dumps({'cmd': 'python build.py', 'workdir': '/repo'})}},
        ])
        meta, events = parse_codex(p)
        assert meta.adapter == 'codex' and meta.cwd == '/repo'
        assert _kinds(events) == ['read', 'tool_call']

    def test_apply_patch_extracts_files(self, tmp_path):
        patch = '*** Update File: cram/audit.py\n@@\n-old\n+new\n'
        p = _write_lines(tmp_path / 's.jsonl', [
            {'type': 'session_meta', 'payload': {'cwd': '/repo'}},
            {'type': 'response_item', 'payload': {
                'type': 'custom_tool_call', 'name': 'apply_patch', 'input': patch}},
        ])
        _meta, events = parse_codex(p)
        assert _kinds(events) == ['edit']
        assert (events[0].extras or {}).get('files') == ['cram/audit.py']

    def test_token_count_usage(self, tmp_path):
        p = _write_lines(tmp_path / 's.jsonl', [
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                'last_token_usage': {'input_tokens': 500, 'output_tokens': 60,
                                     'cached_input_tokens': 1200}}}},
        ])
        _meta, events = parse_codex(p)
        assert _kinds(events) == ['request_usage']
        u = events[0]
        assert (u.tok_input, u.tok_output, u.tok_cache_read) == (500, 60, 1200)

    def test_function_output_error_codes(self, tmp_path):
        # Exit code 1 is treated as success (grep-no-match); >=2 is a real failure.
        p = _write_lines(tmp_path / 's.jsonl', [
            {'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'call_id': 'c1',
                'output': 'Process exited with code 1'}},
            {'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'call_id': 'c2',
                'output': 'Process exited with code 2'}},
        ])
        _meta, events = parse_codex(p)
        # Only the code-2 output becomes an error tool_result.
        assert _kinds(events) == ['tool_result']
        assert events[0].is_error is True

    def test_malformed_and_non_dict_lines_skipped(self, tmp_path):
        p = _write_lines(tmp_path / 's.jsonl', [
            '{broken',
            '[]',                       # list payload, not a dict at top level
            {'type': 'response_item', 'payload': 'not-a-dict'},
            {'type': 'response_item', 'payload': {
                'type': 'function_call', 'name': 'exec_command', 'call_id': 'c1',
                'arguments': '{bad json'}},   # unparseable arguments → args={}
        ])
        meta, events = parse_codex(p)
        # The last line still yields one event; bad arguments degrade to empty cmd
        # (no BASH_READ match) → classified as a tool_call, never raising.
        assert meta is not None
        assert _kinds(events) == ['tool_call']

    def test_missing_file_returns_none(self, tmp_path):
        assert parse_codex(str(tmp_path / 'nope.jsonl')) is None


# ── parse_cursor_jsonl ────────────────────────────────────────────────────────

class TestParseCursorJsonl:
    def test_classification_and_file_extraction(self, tmp_path):
        p = _write_lines(tmp_path / 's.jsonl', [
            {'tool': 'read_file', 'input': {'target_file': '/r/a.py'},
             'vcs': {'root': '/r'}},
            {'toolName': 'edit_file', 'files': ['/r/b.py']},
            {'tool': 'run_terminal_command', 'input': {'command': 'ls /r'}},
            {'tool': 'run_terminal_command', 'input': {'command': 'npm test'}},
        ])
        meta, events = parse_cursor_jsonl(p)
        assert meta.adapter == 'cursor-jsonl' and meta.source == 'cursor'
        assert _kinds(events) == ['read', 'edit', 'read', 'tool_call']
        assert (events[0].extras or {})['files'] == ['/r/a.py']
        assert (events[0].extras or {})['vcs_root'] == '/r'

    def test_non_dict_and_bad_lines_skipped(self, tmp_path):
        p = _write_lines(tmp_path / 's.jsonl', [
            '"just a string"',
            '{oops',
            {'tool': 'read_file', 'files': ['/r/a.py']},
        ])
        _meta, events = parse_cursor_jsonl(p)
        assert _kinds(events) == ['read']

    def test_missing_file_returns_none(self, tmp_path):
        assert parse_cursor_jsonl(str(tmp_path / 'nope.jsonl')) is None


# ── parse_cursor_db ───────────────────────────────────────────────────────────

class TestParseCursorDb:
    def test_nonexistent_db_returns_empty_list(self, tmp_path):
        assert parse_cursor_db(str(tmp_path / 'nope.vscdb')) == []

    def test_non_sqlite_file_returns_empty_list(self, tmp_path):
        p = tmp_path / 'state.vscdb'
        p.write_text('this is not a sqlite database')
        assert parse_cursor_db(str(p)) == []

    def test_groups_bubbles_per_composer(self, tmp_path):
        sqlite3 = pytest.importorskip('sqlite3')
        db = str(tmp_path / 'state.vscdb')
        con = sqlite3.connect(db)
        con.execute('CREATE TABLE cursorDiskKV (key TEXT, value TEXT)')
        bubble = {'createdAt': 1700000000000,
                  'toolFormerData': [{'toolName': 'read_file',
                                      'params': {'target_file': '/r/a.py'}}]}
        con.execute('INSERT INTO cursorDiskKV VALUES (?, ?)',
                    ('bubbleId:comp-1:b1', json.dumps(bubble)))
        con.commit()
        con.close()
        sessions = parse_cursor_db(db)
        assert len(sessions) == 1
        meta, events = sessions[0]
        assert meta.adapter == 'cursor-db'
        assert meta.external_id == 'comp-1'
        assert _kinds(events) == ['read']


# ── recursion-depth cap (hostile nesting) ─────────────────────────────────────

class TestDepthCap:
    def test_find_helpers_bail_below_target_depth(self):
        # A tool_use buried deeper than the depth cap (8) must NOT be found —
        # the cap is what stops a maliciously nested payload from being walked
        # unboundedly. Build a chain of 12 nested dicts with the tool_use at the
        # bottom and assert it's invisible.
        node = {'type': 'tool_use', 'name': 'Read', 'input': {}}
        for _ in range(12):
            node = {'wrap': node}
        assert _find_all_tool_use(node) == []

    def test_shallow_nesting_is_found(self):
        node = {'a': {'b': {'type': 'tool_use', 'name': 'Read', 'input': {}}}}
        found = _find_all_tool_use(node)
        assert len(found) == 1 and found[0]['name'] == 'Read'

    def test_deeply_nested_transcript_line_does_not_crash(self, tmp_path):
        # End-to-end: a pathologically nested line is parsed without raising and
        # simply yields nothing from below the cap.
        node = {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/r/a.py'}}
        for _ in range(50):
            node = {'nest': node}
        p = _write_lines(tmp_path / 's.jsonl', [node])
        meta, events = parse_claude(p)
        assert meta is not None
        assert events == []


# ── pure helpers ──────────────────────────────────────────────────────────────

class TestHelpers:
    def test_is_context_tool(self):
        assert _is_context_tool('ctx_search') is True
        assert _is_context_tool('mcp__context-mode__ctx_load') is True
        assert _is_context_tool('mcp__server__ctx_anything') is True
        assert _is_context_tool('Read') is False
        assert _is_context_tool(None) is False

    def test_session_ident_prefers_embedded_uuid(self):
        uuid = '12345678-1234-1234-1234-123456789abc'
        assert _session_ident(f'/x/{uuid}.jsonl') == uuid
        assert _session_ident(f'/x/rollout-2026-{uuid}.jsonl') == uuid
        assert _session_ident('/x/no-uuid-here.jsonl') == 'no-uuid-here'

    def test_repo_rel(self):
        assert repo_rel('/repo/cram/a.py', '/repo') == 'cram/a.py'
        assert repo_rel('/other/a.py', '/repo') == '/other/a.py'

    def test_cmd_label(self):
        # Shell command collapses whitespace and is the retry-loop key.
        assert _cmd_label('Bash', 'grep   -n   foo', None) == 'grep -n foo'
        # No cmd → tool + basename.
        assert _cmd_label('Read', None, '/r/sub/a.py') == 'Read a.py'
        # Nothing → tool name, then a placeholder.
        assert _cmd_label('Edit', None, None) == 'Edit'
        assert _cmd_label(None, None, None) == '(tool)'

    def test_estimate_read_tokens_filters_outside_repo_and_missing(self, tmp_path):
        repo = tmp_path / 'repo'
        repo.mkdir()
        f = repo / 'a.py'
        f.write_text('y' * 4000)                       # 4000 bytes / 4 = 1000 tok
        counts = {
            str(f): 2,                                 # 1000 * 2 = 2000
            str(tmp_path / 'outside.py'): 5,           # outside repo → skipped
            str(repo / 'gone.py'): 3,                  # missing on disk → skipped
        }
        est = estimate_read_tokens_from_counts(counts, str(repo), chars_per_token=4)
        assert est == 2000

    def test_estimate_read_tokens_empty(self):
        assert estimate_read_tokens_from_counts({}, '/repo') == 0


# ── derivation gate (per-adapter "no relevant activity") ──────────────────────

class TestDeriveGate:
    def test_claude_session_with_only_usage_is_kept(self):
        # Claude sessions are never gated on read/edit presence (unlike cursor/codex).
        meta = SessionMeta('claude', 'claude', '/x/s.jsonl', 0.0)
        events = [Event(0, 'request_usage', tok_input=100)]
        sess = derive_session(meta, events, big_result_bytes=20_000)
        assert sess is not None
        assert sess['requests'] == 1 and sess['reads'] == 0

    def test_codex_session_with_no_reads_or_edits_is_dropped(self):
        meta = SessionMeta('codex', 'codex', '/x/s.jsonl', 0.0, cwd='/repo')
        events = [Event(0, 'request_usage', tok_input=100)]
        assert derive_session(meta, events, repo_root='/repo',
                              big_result_bytes=20_000) is None
