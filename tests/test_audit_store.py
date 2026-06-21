"""Tests for cram/audit_store.py — the SQLite event-store cache."""

from __future__ import annotations
import json
import os
import sqlite3

from cram import audit_events
from cram.audit_store import AuditStore, resolve_db_path
from tests.test_audit import _make_transcript


def _parse(path):
    parsed = audit_events.parse_claude(path)
    assert parsed is not None
    return parsed


def _ingest_claude(store, path):
    meta, events = _parse(path)
    st = os.stat(path)
    store.replace_file(path, 'claude', st.st_mtime, st.st_size, [(meta, events)])


class TestResolveDbPath:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv('CRAM_AUDIT_DB', '/tmp/custom.db')
        assert resolve_db_path() == '/tmp/custom.db'

    def test_memory_accepted(self, monkeypatch):
        monkeypatch.setenv('CRAM_AUDIT_DB', ':memory:')
        assert resolve_db_path() == ':memory:'

    def test_xdg_data_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv('CRAM_AUDIT_DB', raising=False)
        monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
        assert resolve_db_path() == str(tmp_path / 'cram-ai' / 'audit.db')

    def test_default_under_local_share(self, monkeypatch):
        monkeypatch.delenv('CRAM_AUDIT_DB', raising=False)
        monkeypatch.delenv('XDG_DATA_HOME', raising=False)
        path = resolve_db_path()
        assert path.endswith(os.path.join('cram-ai', 'audit.db'))
        assert '.local' in path


class TestRoundtrip:
    def test_events_survive_storage(self, tmp_path):
        transcript = _make_transcript([
            ('Read', {'file_path': 'a.py'}),
            ('Read', {'file_path': 'a.py'}),
            ('Bash', {'command': 'grep -n def a.py'}),
            ('Edit', {'file_path': 'a.py'}),
        ], tmp_path)
        meta, events = _parse(transcript)
        direct = audit_events.derive_session(meta, events, big_result_bytes=20_000)

        store = AuditStore.open(str(tmp_path / 'cache.db'))
        _ingest_claude(store, transcript)
        sessions = store.sessions_for_file(transcript)
        assert len(sessions) == 1
        sid, stored_meta = sessions[0]
        replayed = audit_events.derive_session(
            stored_meta, store.events_for_session(sid), big_result_bytes=20_000)
        assert replayed == direct
        store.close()

    def test_extras_and_token_fields_roundtrip(self, tmp_path):
        path = str(tmp_path / 's.jsonl')
        with open(path, 'w') as f:
            f.write(json.dumps({'usage': {'cache_creation_input_tokens': 5,
                                          'cache_read_input_tokens': 7,
                                          'input_tokens': 3,
                                          'output_tokens': 11}}) + '\n')
            f.write(json.dumps({'type': 'tool_result', 'content': 'x' * 30_000,
                                'is_error': True}) + '\n')
        meta, events = _parse(path)
        store = AuditStore.open(str(tmp_path / 'cache.db'))
        _ingest_claude(store, path)
        sid, _ = store.sessions_for_file(path)[0]
        assert store.events_for_session(sid) == events
        store.close()

    def test_replace_is_idempotent(self, tmp_path):
        transcript = _make_transcript([('Read', {})], tmp_path)
        store = AuditStore.open(str(tmp_path / 'cache.db'))
        _ingest_claude(store, transcript)
        _ingest_claude(store, transcript)
        assert len(store.sessions_for_file(transcript)) == 1
        store.close()


class TestLedger:
    def test_new_file_needs_ingest(self, tmp_path):
        store = AuditStore.open(str(tmp_path / 'cache.db'))
        assert store.needs_ingest('/nope.jsonl', 1.0, 10)
        store.close()

    def test_ingested_file_is_cached(self, tmp_path):
        transcript = _make_transcript([('Read', {})], tmp_path)
        store = AuditStore.open(str(tmp_path / 'cache.db'))
        _ingest_claude(store, transcript)
        st = os.stat(transcript)
        assert not store.needs_ingest(transcript, st.st_mtime, st.st_size)
        store.close()

    def test_changed_file_needs_reingest(self, tmp_path):
        transcript = _make_transcript([('Read', {})], tmp_path)
        store = AuditStore.open(str(tmp_path / 'cache.db'))
        _ingest_claude(store, transcript)
        st = os.stat(transcript)
        assert store.needs_ingest(transcript, st.st_mtime + 5, st.st_size)
        assert store.needs_ingest(transcript, st.st_mtime, st.st_size + 100)
        store.close()

    def test_mark_failed_records_run_failure(self, tmp_path):
        store = AuditStore.open(str(tmp_path / 'cache.db'))
        assert store.run_failures == []
        store.mark_failed('/t.jsonl', 'claude', 1.0, 10)
        assert store.run_failures == ['/t.jsonl']
        store.close()

    def test_failed_parse_retried_and_keeps_old_snapshot(self, tmp_path):
        transcript = _make_transcript([('Read', {})], tmp_path)
        store = AuditStore.open(str(tmp_path / 'cache.db'))
        _ingest_claude(store, transcript)
        st = os.stat(transcript)
        store.mark_failed(transcript, 'claude', st.st_mtime, st.st_size)
        # previous good sessions remain queryable…
        assert len(store.sessions_for_file(transcript)) == 1
        # …and the file is retried on the next run
        assert store.needs_ingest(transcript, st.st_mtime, st.st_size)
        store.close()

    def test_persists_across_reopen(self, tmp_path):
        transcript = _make_transcript([('Read', {})], tmp_path)
        db = str(tmp_path / 'cache.db')
        store = AuditStore.open(db)
        _ingest_claude(store, transcript)
        store.close()
        store2 = AuditStore.open(db)
        st = os.stat(transcript)
        assert not store2.needs_ingest(transcript, st.st_mtime, st.st_size)
        assert len(store2.sessions_for_file(transcript)) == 1
        store2.close()


class TestInvalidation:
    def test_corrupt_db_rebuilt_never_raises(self, tmp_path):
        db = str(tmp_path / 'cache.db')
        with open(db, 'w') as f:
            f.write('this is not a sqlite database, not even close — padding ' * 50)
        store = AuditStore.open(db)
        assert not store.ephemeral  # corrupt file was deleted and rebuilt
        transcript = _make_transcript([('Read', {})], tmp_path)
        _ingest_claude(store, transcript)
        assert len(store.sessions_for_file(transcript)) == 1
        store.close()

    def test_unopenable_path_falls_back_to_memory(self, tmp_path, capsys):
        store = AuditStore.open(str(tmp_path))  # a directory, not a file
        assert store.ephemeral
        transcript = _make_transcript([('Read', {})], tmp_path)
        _ingest_claude(store, transcript)
        assert len(store.sessions_for_file(transcript)) == 1
        assert 'cache unavailable' in capsys.readouterr().err
        store.close()

    def test_schema_version_bump_rebuilds(self, tmp_path):
        db = str(tmp_path / 'cache.db')
        transcript = _make_transcript([('Read', {})], tmp_path)
        store = AuditStore.open(db)
        _ingest_claude(store, transcript)
        store.close()
        con = sqlite3.connect(db)
        con.execute('PRAGMA user_version = 999')
        con.commit()
        con.close()
        store2 = AuditStore.open(db)
        assert store2.sessions_for_file(transcript) == []
        st = os.stat(transcript)
        assert store2.needs_ingest(transcript, st.st_mtime, st.st_size)
        store2.close()

    def test_parser_fingerprint_change_rebuilds(self, tmp_path):
        db = str(tmp_path / 'cache.db')
        transcript = _make_transcript([('Read', {})], tmp_path)
        store = AuditStore.open(db)
        _ingest_claude(store, transcript)
        store.close()
        con = sqlite3.connect(db)
        con.execute("UPDATE meta SET value = 'stale' WHERE key = 'parser_fingerprint'")
        con.commit()
        con.close()
        store2 = AuditStore.open(db)
        assert store2.sessions_for_file(transcript) == []
        store2.close()


class TestCursorDbSessions:
    def test_multiple_sessions_per_file_no_duplicates(self, tmp_path):
        from cram.audit_events import SessionMeta
        store = AuditStore.open(str(tmp_path / 'cache.db'))
        vscdb = str(tmp_path / 'state.vscdb')
        open(vscdb, 'w').close()
        metas = [
            (SessionMeta('cursor-db', 'cursor', vscdb, 100.0,
                         event_mtime=100.0, external_id='c1'), []),
            (SessionMeta('cursor-db', 'cursor', vscdb, 200.0,
                         event_mtime=200.0, external_id='c2'), []),
        ]
        store.replace_file(vscdb, 'cursor-db', 1.0, 0, metas)
        store.replace_file(vscdb, 'cursor-db', 2.0, 0, metas)  # wholesale replace
        sessions = store.sessions_for_file(vscdb)
        assert len(sessions) == 2
        assert {m.external_id for _, m in sessions} == {'c1', 'c2'}
        store.close()


class TestIncrementalCollect:
    """collect_audit ingestion behavior: parse once, re-parse only on change."""

    def _setup(self, tmp_path, monkeypatch, n=2):
        import cram.audit as audit_mod
        td = tmp_path / 'proj'
        td.mkdir()
        paths = []
        for i in range(n):
            p = str(td / f's{i}.jsonl')
            with open(p, 'w') as f:
                f.write(json.dumps({'type': 'tool_use', 'name': 'Read',
                                    'input': {'file_path': f'{i}.py'}}) + '\n')
                f.write(json.dumps({'type': 'tool_use', 'name': 'Edit',
                                    'input': {'file_path': f'{i}.py'}}) + '\n')
            paths.append(p)
        monkeypatch.setattr(audit_mod, '_project_transcript_dir',
                            lambda r, d=str(td): d)
        monkeypatch.setattr(audit_mod, '_cursor_agent_transcripts_dir', lambda: None)
        monkeypatch.setattr(audit_mod, '_cursor_storage_root', lambda: None)
        monkeypatch.setattr(audit_mod, '_codex_sessions_dir', lambda: None)

        calls = []
        real = audit_events.parse_claude
        monkeypatch.setattr(audit_events, 'parse_claude',
                            lambda p: (calls.append(p), real(p))[1])
        return audit_mod, paths, calls

    def test_warm_run_parses_nothing(self, tmp_path, monkeypatch):
        audit_mod, paths, calls = self._setup(tmp_path, monkeypatch)
        d1 = audit_mod.collect_audit(str(tmp_path), days=365)
        assert sorted(calls) == sorted(paths)
        calls.clear()
        d2 = audit_mod.collect_audit(str(tmp_path), days=365)
        assert calls == []
        assert d1 == d2

    def test_changed_file_reparsed_alone(self, tmp_path, monkeypatch):
        audit_mod, paths, calls = self._setup(tmp_path, monkeypatch)
        audit_mod.collect_audit(str(tmp_path), days=365)
        calls.clear()
        with open(paths[0], 'a') as f:
            f.write(json.dumps({'type': 'tool_use', 'name': 'Read',
                                'input': {'file_path': 'extra.py'}}) + '\n')
        os.utime(paths[0], (os.path.getmtime(paths[0]) + 10,) * 2)
        data = audit_mod.collect_audit(str(tmp_path), days=365)
        assert calls == [paths[0]]
        assert data['sessions'] == 2
        # the appended read is reflected: 2 reads in s0 + 1 in s1 → avg 1.5
        assert abs(data['avg_reads'] - 1.5) < 0.01

    def test_reingest_flag_reparses_all(self, tmp_path, monkeypatch):
        audit_mod, paths, calls = self._setup(tmp_path, monkeypatch)
        audit_mod.collect_audit(str(tmp_path), days=365)
        calls.clear()
        audit_mod.collect_audit(str(tmp_path), days=365, reingest=True)
        assert sorted(calls) == sorted(paths)

    def test_corrupt_cache_recovers_at_collect_level(self, tmp_path, monkeypatch):
        audit_mod, paths, calls = self._setup(tmp_path, monkeypatch)
        d1 = audit_mod.collect_audit(str(tmp_path), days=365)
        db_path = os.environ['CRAM_AUDIT_DB']
        with open(db_path, 'w') as f:
            f.write('garbage that is definitely not sqlite ' * 100)
        d2 = audit_mod.collect_audit(str(tmp_path), days=365)
        assert d1 == d2

    def test_deleted_transcript_drops_out(self, tmp_path, monkeypatch):
        # Stale ledger/session rows for files no longer on disk must be inert:
        # queries are anchored to the live glob, not the ledger.
        audit_mod, paths, calls = self._setup(tmp_path, monkeypatch)
        assert audit_mod.collect_audit(str(tmp_path), days=365)['sessions'] == 2
        os.remove(paths[0])
        assert audit_mod.collect_audit(str(tmp_path), days=365)['sessions'] == 1


class TestParseFailureSurfacing:
    """Live transcripts that fail to parse must be visible, not silent."""

    def _setup(self, tmp_path, monkeypatch, corrupt=False, good=True):
        import cram.audit as audit_mod
        td = tmp_path / 'proj'
        td.mkdir()
        if good:
            with open(td / 's0.jsonl', 'w') as f:
                f.write(json.dumps({'type': 'tool_use', 'name': 'Read',
                                    'input': {'file_path': 'a.py'}}) + '\n')
                f.write(json.dumps({'type': 'tool_use', 'name': 'Edit',
                                    'input': {'file_path': 'a.py'}}) + '\n')
        bad = None
        if corrupt:
            # A directory named *.jsonl: stat succeeds, open() raises, so
            # parse_claude returns None — same path as an unreadable file.
            bad = td / 'bad.jsonl'
            bad.mkdir()
        monkeypatch.setattr(audit_mod, '_project_transcript_dir',
                            lambda r, d=str(td): d)
        monkeypatch.setattr(audit_mod, '_cursor_agent_transcripts_dir', lambda: None)
        monkeypatch.setattr(audit_mod, '_cursor_storage_root', lambda: None)
        monkeypatch.setattr(audit_mod, '_codex_sessions_dir', lambda: None)
        return audit_mod, str(bad) if bad else None

    def test_warns_and_audit_still_succeeds(self, tmp_path, monkeypatch, capsys):
        audit_mod, bad = self._setup(tmp_path, monkeypatch, corrupt=True)
        audit_mod.run_audit(str(tmp_path), days=365)
        out = capsys.readouterr()
        assert 'Sessions analysed' in out.out
        assert '1 transcript failed to parse' in out.err
        assert bad not in out.err  # paths only with CRAM_DEBUG

    def test_debug_lists_paths(self, tmp_path, monkeypatch, capsys):
        audit_mod, bad = self._setup(tmp_path, monkeypatch, corrupt=True)
        monkeypatch.setenv('CRAM_DEBUG', '1')
        audit_mod.run_audit(str(tmp_path), days=365)
        assert bad in capsys.readouterr().err

    def test_json_includes_parse_failures_count(self, tmp_path, monkeypatch, capsys):
        audit_mod, _ = self._setup(tmp_path, monkeypatch, corrupt=True)
        audit_mod.run_audit(str(tmp_path), days=365, as_json=True)
        out = capsys.readouterr()
        assert json.loads(out.out)['parse_failures'] == 1
        assert 'failed to parse' in out.err

    def test_clean_run_no_warning(self, tmp_path, monkeypatch, capsys):
        audit_mod, _ = self._setup(tmp_path, monkeypatch)
        audit_mod.run_audit(str(tmp_path), days=365, as_json=True)
        out = capsys.readouterr()
        assert 'failed to parse' not in out.err
        assert json.loads(out.out)['parse_failures'] == 0

    def test_all_failed_still_warns(self, tmp_path, monkeypatch, capsys):
        # Every transcript failing → data is None, but the warning must still
        # appear so "no sessions" isn't mistaken for a quiet month.
        audit_mod, _ = self._setup(tmp_path, monkeypatch, corrupt=True, good=False)
        audit_mod.run_audit(str(tmp_path), days=365)
        out = capsys.readouterr()
        assert 'No sessions found' in out.out
        assert 'failed to parse' in out.err

    def test_warm_run_still_warns(self, tmp_path, monkeypatch, capsys):
        # parse_ok=0 files are retried every run, so the warning persists
        # until the transcript is fixed or ages out of the window.
        audit_mod, _ = self._setup(tmp_path, monkeypatch, corrupt=True)
        audit_mod.run_audit(str(tmp_path), days=365)
        capsys.readouterr()
        audit_mod.run_audit(str(tmp_path), days=365)
        assert 'failed to parse' in capsys.readouterr().err


class TestContextModeDetection:
    """context-mode (ctx_* tools) detection: predicate, derive flag, roundtrip."""

    # The full, real context-mode tool surface, validated against authoritative
    # sources (no live transcript existed at the time):
    #   - tool names: literal ctx_* registrations in context-mode's src/server.ts
    #   - server key: official .mcp.json.example registers as "context-mode"
    #   - surfaced form: Claude Code renders MCP tools as mcp__<key>__<tool> with
    #     the hyphen preserved (proven by real on-disk mcp__cram-ai__* calls)
    # Combining those gives the exact surfaced names below. Update only if
    # context-mode renames a tool or changes its recommended server key.
    CONTEXT_MODE_TOOLS = (
        'ctx_batch_execute', 'ctx_doctor', 'ctx_execute', 'ctx_execute_file',
        'ctx_fetch_and_index', 'ctx_index', 'ctx_insight', 'ctx_purge',
        'ctx_search', 'ctx_stats', 'ctx_upgrade',
    )

    def test_predicate_matches_real_surface_bare(self):
        f = audit_events._is_context_tool
        for t in self.CONTEXT_MODE_TOOLS:
            assert f(t), t

    def test_predicate_matches_real_surface_mcp_namespaced(self):
        # The actual form Claude Code emits: mcp__context-mode__<tool>.
        f = audit_events._is_context_tool
        for t in self.CONTEXT_MODE_TOOLS:
            assert f(f'mcp__context-mode__{t}'), t

    def test_predicate_rejects_other_tools_and_mcp_servers(self):
        f = audit_events._is_context_tool
        # Must not over-match neighboring tools or other MCP servers.
        for n in ('Read', 'Edit', 'Write', 'Bash',
                  'mcp__cram-ai__get_context', 'mcp__github__create_issue',
                  'mcp__other__do_thing', 'context', '', None):
            assert not f(n), n

    def test_derive_flag_true_when_ctx_tool_present(self, tmp_path):
        path = _make_transcript([
            ('Read', {'file_path': 'a.py'}),
            ('ctx_search', {'query': 'foo'}),
            ('Edit', {'file_path': 'a.py'}),
        ], tmp_path)
        meta, events = _parse(path)
        sess = audit_events.derive_session(meta, events, big_result_bytes=20_000)
        assert sess['context_mode'] is True

    def test_derive_flag_false_without_ctx_tool(self, tmp_path):
        path = _make_transcript([
            ('Read', {'file_path': 'a.py'}),
            ('Edit', {'file_path': 'a.py'}),
        ], tmp_path)
        meta, events = _parse(path)
        sess = audit_events.derive_session(meta, events, big_result_bytes=20_000)
        assert sess['context_mode'] is False

    def test_flag_survives_store_roundtrip(self, tmp_path):
        # ctx_* calls are 'tool_call' events; their tool name must survive the
        # SQLite cache so detection works on a warm (cache-replayed) read.
        path = _make_transcript([
            ('ctx_execute', {'code': 'print(1)'}),
            ('Edit', {'file_path': 'a.py'}),
        ], tmp_path)
        store = AuditStore.open(str(tmp_path / 'cache.db'))
        _ingest_claude(store, path)
        sid, stored_meta = store.sessions_for_file(path)[0]
        replayed = audit_events.derive_session(
            stored_meta, store.events_for_session(sid), big_result_bytes=20_000)
        assert replayed['context_mode'] is True
        store.close()


class TestContextModeSegment:
    """collect_audit segments the pool only when both on/off sessions exist."""

    def _setup(self, tmp_path, monkeypatch, with_ctx=True, with_plain=True):
        import cram.audit as audit_mod
        td = tmp_path / 'proj'
        td.mkdir()
        if with_ctx:
            with open(td / 'ctx.jsonl', 'w') as f:
                f.write(json.dumps({'type': 'tool_use', 'name': 'ctx_search',
                                    'input': {'query': 'x'}}) + '\n')
                f.write(json.dumps({'type': 'tool_use', 'name': 'Edit',
                                    'input': {'file_path': 'a.py'}}) + '\n')
        if with_plain:
            with open(td / 'plain.jsonl', 'w') as f:
                f.write(json.dumps({'type': 'tool_use', 'name': 'Read',
                                    'input': {'file_path': 'b.py'}}) + '\n')
                f.write(json.dumps({'type': 'tool_use', 'name': 'Edit',
                                    'input': {'file_path': 'b.py'}}) + '\n')
        monkeypatch.setattr(audit_mod, '_project_transcript_dir',
                            lambda r, d=str(td): d)
        monkeypatch.setattr(audit_mod, '_cursor_agent_transcripts_dir', lambda: None)
        monkeypatch.setattr(audit_mod, '_cursor_storage_root', lambda: None)
        monkeypatch.setattr(audit_mod, '_codex_sessions_dir', lambda: None)
        return audit_mod

    def test_segment_present_when_mixed(self, tmp_path, monkeypatch):
        audit_mod = self._setup(tmp_path, monkeypatch)
        data = audit_mod.collect_audit(str(tmp_path), days=365)
        seg = data['context_mode_segment']
        assert seg is not None
        assert seg['on']['sessions'] == 1
        assert seg['off']['sessions'] == 1

    def test_segment_none_when_no_ctx_sessions(self, tmp_path, monkeypatch):
        audit_mod = self._setup(tmp_path, monkeypatch, with_ctx=False)
        data = audit_mod.collect_audit(str(tmp_path), days=365)
        assert data['context_mode_segment'] is None

    def test_segment_none_when_all_ctx_sessions(self, tmp_path, monkeypatch):
        audit_mod = self._setup(tmp_path, monkeypatch, with_plain=False)
        data = audit_mod.collect_audit(str(tmp_path), days=365)
        assert data['context_mode_segment'] is None

    def test_run_audit_prints_segment(self, tmp_path, monkeypatch, capsys):
        audit_mod = self._setup(tmp_path, monkeypatch)
        audit_mod.run_audit(str(tmp_path), days=365)
        out = capsys.readouterr().out
        assert 'Context tool segment' in out
        assert 'Carried result cost/session' in out


class TestIngestProgress:
    """A large cold ingest announces itself on stderr; warm runs stay quiet."""

    def _setup(self, tmp_path, monkeypatch, n):
        import cram.audit as audit_mod
        td = tmp_path / 'proj'
        td.mkdir()
        for i in range(n):
            with open(td / f's{i}.jsonl', 'w') as f:
                f.write(json.dumps({'type': 'tool_use', 'name': 'Read',
                                    'input': {'file_path': f'{i}.py'}}) + '\n')
        monkeypatch.setattr(audit_mod, '_project_transcript_dir',
                            lambda r, d=str(td): d)
        monkeypatch.setattr(audit_mod, '_cursor_agent_transcripts_dir', lambda: None)
        monkeypatch.setattr(audit_mod, '_cursor_storage_root', lambda: None)
        monkeypatch.setattr(audit_mod, '_codex_sessions_dir', lambda: None)
        return audit_mod

    def test_cold_run_over_threshold_announces(self, tmp_path, monkeypatch, capsys):
        import cram.audit as audit_mod
        n = audit_mod.INGEST_PROGRESS_MIN + 1
        audit_mod = self._setup(tmp_path, monkeypatch, n)
        audit_mod.collect_audit(str(tmp_path), days=365)
        assert f'ingesting {n} transcripts' in capsys.readouterr().err
        audit_mod.collect_audit(str(tmp_path), days=365)  # warm: cached
        assert 'ingesting' not in capsys.readouterr().err

    def test_small_cold_run_is_silent(self, tmp_path, monkeypatch, capsys):
        audit_mod = self._setup(tmp_path, monkeypatch, 3)
        audit_mod.collect_audit(str(tmp_path), days=365)
        assert 'ingesting' not in capsys.readouterr().err
