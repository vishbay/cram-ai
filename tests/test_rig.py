"""Tests for the controlled benchmark rig (cram/rig.py).

Covers the framework end-to-end with a MockRunner (no live agent): corpus
loading, provider availability + stubs, the run loop, the CommandOracle, the
effective-token measurement (reusing the audit timeline), and the
tokens-at-fixed-success summary."""

from __future__ import annotations
import json
import os

import pytest

from cram import rig


def _usage(input_tokens=0, cache_read=0, cache_write=0, output=0):
    return {'usage': {'input_tokens': input_tokens,
                      'cache_read_input_tokens': cache_read,
                      'cache_creation_input_tokens': cache_write,
                      'output_tokens': output}}


def _write_transcript(path, messages):
    with open(path, 'w') as f:
        for m in messages:
            f.write(json.dumps(m) + '\n')


# ── Corpus ──────────────────────────────────────────────────────────────────

class TestCorpus:
    def test_load_list_and_dict_forms(self, tmp_path):
        p = tmp_path / 'c.json'
        p.write_text(json.dumps([{'id': 'a', 'prompt': 'do a'}]))
        assert [t.id for t in rig.load_corpus(str(p))] == ['a']
        p.write_text(json.dumps({'tasks': [{'id': 'b', 'prompt': 'do b'}]}))
        assert [t.id for t in rig.load_corpus(str(p))] == ['b']

    def test_fixture_resolved_relative_to_corpus(self, tmp_path):
        (tmp_path / 'fix').mkdir()
        p = tmp_path / 'c.json'
        p.write_text(json.dumps([{'id': 'a', 'prompt': 'x', 'fixture': 'fix'}]))
        t = rig.load_corpus(str(p))[0]
        assert t.fixture == str(tmp_path / 'fix')

    def test_string_check_splits(self, tmp_path):
        p = tmp_path / 'c.json'
        p.write_text(json.dumps([{'id': 'a', 'prompt': 'x', 'check': 'pytest -q'}]))
        assert rig.load_corpus(str(p))[0].check == ['pytest', '-q']

    def test_duplicate_id_rejected(self, tmp_path):
        p = tmp_path / 'c.json'
        p.write_text(json.dumps([{'id': 'a', 'prompt': 'x'},
                                 {'id': 'a', 'prompt': 'y'}]))
        with pytest.raises(ValueError):
            rig.load_corpus(str(p))

    def test_missing_required_field(self):
        with pytest.raises(ValueError):
            rig.Task.from_dict({'id': 'a'})


# ── Providers ───────────────────────────────────────────────────────────────

class TestProviders:
    def test_baseline_always_available(self):
        assert rig.BaselineAdapter().availability().ok

    def test_stub_providers_unavailable_with_hint(self):
        for adapter in (rig.HeadroomAdapter(), rig.ContextModeAdapter()):
            av = adapter.availability()
            assert not av.ok
            assert adapter.name in av.reason and 'stub' in av.reason

    def test_stub_setup_raises(self, tmp_path):
        with pytest.raises(RuntimeError):
            rig.HeadroomAdapter().setup(rig.Task('a', 'x'), str(tmp_path))

    def test_get_provider_unknown(self):
        with pytest.raises(KeyError):
            rig.get_provider('nope')

    def test_get_provider_known(self):
        assert rig.get_provider('baseline').name == 'baseline'

    def test_cram_provider_targets_codex_for_codex_runner(self):
        providers = rig._configure_providers_for_runner([rig.CramAdapter()], 'codex')
        assert providers[0].target == 'codex'

    def test_cram_provider_targets_claude_for_claude_runner(self):
        providers = rig._configure_providers_for_runner([rig.CramAdapter()], 'claude')
        assert providers[0].target == 'claude'

    def test_cram_setup_passes_target_when_configured(self, tmp_path, monkeypatch):
        calls = {}

        def fake_run(argv, **kw):
            calls['argv'] = argv
            calls['cwd'] = kw.get('cwd')

            class Result:
                returncode = 0
            return Result()

        monkeypatch.setattr(rig.subprocess, 'run', fake_run)
        rig.CramAdapter(target='codex').setup(rig.Task('a', 'do it'), str(tmp_path))
        assert calls['argv'] == ['cram', 'task', 'do it', '--target', 'codex']
        assert calls['cwd'] == str(tmp_path)

    def test_cram_setup_falls_back_to_existing_context(self, tmp_path, monkeypatch):
        def fake_run(*args, **kwargs):
            class Result:
                returncode = 1
            return Result()

        monkeypatch.setattr(rig.subprocess, 'run', fake_run)
        monkeypatch.setattr(rig.CramAdapter, '_write_existing_context',
                            lambda self, workdir: True)
        setup = rig.CramAdapter(target='codex').setup(rig.Task('a', 'do it'), str(tmp_path))
        assert setup['CRAM_CONTEXT_FALLBACK'] == 'existing'

    def test_cram_setup_runs_init_when_uninitialized(self, tmp_path, monkeypatch):
        import cram.context_dir as _cd
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)

            class Result:
                returncode = 0
            return Result()
        monkeypatch.setattr(_cd, 'has_context_dir', lambda p: False)
        monkeypatch.setattr(rig.subprocess, 'run', fake_run)
        setup = rig.CramAdapter().setup(rig.Task('a', 'do it'), str(tmp_path))
        assert ['cram', 'init'] in calls          # bootstrapped the context layer
        assert setup.get('CRAM_INITIALIZED') == '1'

    def test_cram_setup_skips_init_when_already_initialized(self, tmp_path, monkeypatch):
        import cram.context_dir as _cd
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)

            class Result:
                returncode = 0
            return Result()
        monkeypatch.setattr(_cd, 'has_context_dir', lambda p: True)
        monkeypatch.setattr(rig.subprocess, 'run', fake_run)
        rig.CramAdapter().setup(rig.Task('a', 'do it'), str(tmp_path))
        assert ['cram', 'init'] not in calls      # already initialized → no re-init

    def test_prepare_workdir_git_inits(self, tmp_path):
        fx = tmp_path / 'fx'
        fx.mkdir()
        (fx / 'a.py').write_text('x = 1\n')
        wd = rig._prepare_workdir(rig.Task('t', 'p', fixture=str(fx)), str(tmp_path / 'wd'))
        assert os.path.isdir(os.path.join(wd, '.git'))


# ── Oracle ──────────────────────────────────────────────────────────────────

class TestCommandOracle:
    def test_no_check_is_success(self, tmp_path):
        assert rig.CommandOracle().score(rig.Task('a', 'x'), str(tmp_path))

    def test_exit_zero_passes(self, tmp_path):
        t = rig.Task('a', 'x', check=['true'])
        assert rig.CommandOracle().score(t, str(tmp_path))

    def test_nonzero_fails(self, tmp_path):
        t = rig.Task('a', 'x', check=['false'])
        assert not rig.CommandOracle().score(t, str(tmp_path))


# ── Measurement ─────────────────────────────────────────────────────────────

class TestEffectiveTokens:
    def test_weights_cache_traffic(self, tmp_path, monkeypatch):
        monkeypatch.setenv('CRAM_PROVIDER', 'anthropic')
        p = str(tmp_path / 's.jsonl')
        _write_transcript(p, [_usage(input_tokens=100, cache_read=1_000, cache_write=200)])
        # 100 + 200*1.25 + 1000*0.10 = 450
        assert rig.effective_tokens(p) == pytest.approx(450.0)

    def test_unparseable_and_empty_are_zero(self, tmp_path):
        p = str(tmp_path / 's.jsonl')
        _write_transcript(p, [{'type': 'tool_use', 'name': 'Read', 'input': {}}])
        assert rig.effective_tokens(p) == 0.0

    def test_codex_transcript_weights_cache_traffic(self, tmp_path, monkeypatch):
        monkeypatch.setenv('CRAM_PROVIDER', 'anthropic')
        d = tmp_path / '.codex' / 'sessions'
        d.mkdir(parents=True)
        p = str(d / 's.jsonl')
        _write_transcript(p, [
            {'type': 'session_meta', 'payload': {'cwd': str(tmp_path)}},
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                'last_token_usage': {
                    'input_tokens': 100,
                    'cached_input_tokens': 1_000,
                    'output_tokens': 50,
                },
            }}},
        ])
        # Codex reports cached input as cache-read traffic: 100 + 1000*0.10
        assert rig.effective_tokens(p) == pytest.approx(200.0)


# ── Run loop + summary ──────────────────────────────────────────────────────

class TestRunRig:
    def _runner(self, tmp_path, succeed_for=None, tokens=10_000):
        """MockRunner that writes a transcript and creates a marker the oracle
        checks. succeed_for: set of (task_id) that should pass; None → all pass."""
        def transcript_for(task, setup, workdir):
            tp = f'{workdir}/transcript.jsonl'
            _write_transcript(tp, [_usage(cache_read=tokens)])
            ok = succeed_for is None or task.id in succeed_for
            if ok:
                open(f'{workdir}/PASS', 'w').close()
            return tp
        return rig.MockRunner(transcript_for)

    def _oracle(self):
        # success = a PASS marker exists in the workdir
        return rig.CommandOracle()  # uses task.check = ['test', '-f', 'PASS']

    def test_grid_includes_skipped_providers(self, tmp_path):
        corpus = [rig.Task('t1', 'x', check=['test', '-f', 'PASS'])]
        providers = [rig.BaselineAdapter(), rig.HeadroomAdapter()]
        results = rig.run_rig(corpus, providers, self._runner(tmp_path),
                              rig.CommandOracle(), work_root=str(tmp_path / 'w'))
        by = {(r.task_id, r.provider): r for r in results}
        assert by[('t1', 'baseline')].success
        assert by[('t1', 'headroom')].skipped
        assert 'stub' in by[('t1', 'headroom')].reason

    def test_success_and_tokens_recorded(self, tmp_path):
        corpus = [rig.Task('t1', 'x', check=['test', '-f', 'PASS'])]
        results = rig.run_rig(corpus, [rig.BaselineAdapter()],
                              self._runner(tmp_path, tokens=5_000),
                              rig.CommandOracle(), work_root=str(tmp_path / 'w'))
        r = results[0]
        assert r.success and r.eff_tokens == pytest.approx(500.0)  # 5000*0.10

    def test_failed_task_recorded_not_skipped(self, tmp_path):
        corpus = [rig.Task('t1', 'x', check=['test', '-f', 'PASS'])]
        results = rig.run_rig(corpus, [rig.BaselineAdapter()],
                              self._runner(tmp_path, succeed_for=set()),
                              rig.CommandOracle(), work_root=str(tmp_path / 'w'))
        assert not results[0].success and not results[0].skipped

    def test_summary_tokens_at_fixed_success(self, tmp_path):
        corpus = [rig.Task('t1', 'x', check=['test', '-f', 'PASS']),
                  rig.Task('t2', 'x', check=['test', '-f', 'PASS'])]
        results = rig.run_rig(corpus, [rig.BaselineAdapter()],
                              self._runner(tmp_path, succeed_for={'t1'}, tokens=10_000),
                              rig.CommandOracle(), work_root=str(tmp_path / 'w'))
        s = rig.summarize(results)['providers']['baseline']
        assert s['ran'] == 2 and s['passed'] == 1
        assert s['success_rate'] == pytest.approx(0.5)
        # mean over passed runs only: 10000*0.10 = 1000
        assert s['mean_eff_tokens_passed'] == pytest.approx(1000.0)

    def test_render_summary_runs(self, tmp_path):
        corpus = [rig.Task('t1', 'x', check=['test', '-f', 'PASS'])]
        results = rig.run_rig(corpus, [rig.BaselineAdapter(), rig.HeadroomAdapter()],
                              self._runner(tmp_path), rig.CommandOracle(),
                              work_root=str(tmp_path / 'w'))
        out = rig.render_summary(rig.summarize(results))
        assert 'tokens at fixed success' in out
        assert 'baseline' in out and 'headroom' in out


class TestLiveRunner:
    def test_available_requires_claude_cli(self, monkeypatch):
        monkeypatch.setattr(rig.shutil, 'which', lambda exe: None)
        av = rig.LiveRunner().available()
        assert not av.ok and 'claude' in av.reason.lower()

    def test_available_when_cli_present(self, monkeypatch):
        monkeypatch.setattr(rig.shutil, 'which', lambda exe: '/usr/local/bin/claude')
        assert rig.LiveRunner().available().ok

    def test_run_blocks_when_cli_missing_no_api_key_needed(self, tmp_path, monkeypatch):
        monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
        monkeypatch.setattr(rig.shutil, 'which', lambda exe: None)
        with pytest.raises(RuntimeError, match='claude'):
            rig.LiveRunner().run(rig.Task('a', 'x'), {}, str(tmp_path))

    def test_run_invokes_claude_p_and_returns_transcript(self, tmp_path, monkeypatch):
        # claude on PATH; subprocess stubbed; transcript resolver returns a path.
        calls = {}
        monkeypatch.setattr(rig.shutil, 'which', lambda exe: '/bin/claude')
        def fake_run(argv, **kw):
            calls['argv'] = argv
            calls['cwd'] = kw.get('cwd')
            return None
        monkeypatch.setattr(rig.subprocess, 'run', fake_run)
        monkeypatch.setattr(rig, '_newest_transcript_for',
                            lambda wd: str(tmp_path / 't.jsonl'))
        out = rig.LiveRunner().run(rig.Task('a', 'do the thing'), {}, str(tmp_path))
        assert out == str(tmp_path / 't.jsonl')
        # --dangerously-skip-permissions is required for headless edits/oracle.
        assert calls['argv'] == ['claude', '-p', '--dangerously-skip-permissions',
                                 'do the thing']
        assert calls['cwd'] == str(tmp_path)

    def test_custom_agent_cmd(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rig.shutil, 'which', lambda exe: '/bin/' + exe)
        monkeypatch.setattr(rig.subprocess, 'run', lambda *a, **k: None)
        monkeypatch.setattr(rig, '_newest_transcript_for', lambda wd: None)
        r = rig.LiveRunner(agent_cmd=('myagent', '--headless'))
        assert r.available().ok
        r.run(rig.Task('a', 'x'), {}, str(tmp_path))  # no raise


class TestCodexRunner:
    def test_available_requires_codex_cli(self, monkeypatch):
        monkeypatch.setattr(rig.shutil, 'which', lambda exe: None)
        av = rig.CodexRunner().available()
        assert not av.ok and 'codex' in av.reason.lower()

    def test_available_when_cli_present(self, monkeypatch):
        monkeypatch.setattr(rig.shutil, 'which', lambda exe: '/usr/local/bin/codex')
        assert rig.CodexRunner().available().ok

    def test_run_invokes_codex_exec_and_returns_transcript(self, tmp_path, monkeypatch):
        calls = {}
        monkeypatch.setattr(rig.shutil, 'which', lambda exe: '/bin/codex')
        monkeypatch.setattr(rig.time, 'time', lambda: 123.0)

        def fake_run(argv, **kw):
            calls['argv'] = argv
            calls['cwd'] = kw.get('cwd')
            return None

        monkeypatch.setattr(rig.subprocess, 'run', fake_run)
        monkeypatch.setattr(rig, '_newest_codex_transcript_for',
                            lambda wd, since=0.0: str(tmp_path / 'c.jsonl'))
        out = rig.CodexRunner().run(rig.Task('a', 'do the thing'), {}, str(tmp_path))
        assert out == str(tmp_path / 'c.jsonl')
        assert calls['argv'] == [
            'codex', 'exec',
            '--sandbox', 'workspace-write',
            '--skip-git-repo-check',
            '--cd', str(tmp_path),
            'do the thing',
        ]
        assert calls['cwd'] == str(tmp_path)

    def test_make_runner_selects_codex(self):
        assert isinstance(rig._make_runner('codex'), rig.CodexRunner)


# ── claude-context adapter ───────────────────────────────────────────────────

class TestClaudeContextAdapter:
    def test_registered_as_provider(self):
        assert rig.get_provider('claude-context').name == 'claude-context'

    def test_has_detector_signature(self):
        assert rig.ClaudeContextAdapter.detector == {
            'kind': 'mcp_tool', 'match': 'claude-context'}

    def test_unavailable_without_launcher(self, monkeypatch):
        monkeypatch.setattr(rig.shutil, 'which', lambda exe: None)
        av = rig.ClaudeContextAdapter().availability()
        assert not av.ok and 'npx' in av.reason

    def test_unavailable_without_embedding_key(self, monkeypatch):
        monkeypatch.setattr(rig.shutil, 'which', lambda exe: '/bin/npx')
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
        monkeypatch.delenv('CRAM_CLAUDE_CONTEXT_EMBED_KEY', raising=False)
        av = rig.ClaudeContextAdapter().availability()
        assert not av.ok and 'embedding key' in av.reason

    def test_available_when_configured(self, monkeypatch):
        monkeypatch.setattr(rig.shutil, 'which', lambda exe: '/bin/npx')
        monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
        assert rig.ClaudeContextAdapter().availability().ok

    def test_setup_writes_project_mcp_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv('CRAM_CLAUDE_CONTEXT_CMD', 'npx -y @zilliz/claude-context-mcp@latest')
        rig.ClaudeContextAdapter().setup(rig.Task('a', 'x'), str(tmp_path))
        cfg = json.loads((tmp_path / '.mcp.json').read_text())
        srv = cfg['mcpServers']['claude-context']
        assert srv['command'] == 'npx'
        assert srv['args'] == ['-y', '@zilliz/claude-context-mcp@latest']


# ── Detector + observational A/B ─────────────────────────────────────────────

class TestDetector:
    def test_mcp_tool_substring_on_full_name(self):
        det = {'kind': 'mcp_tool', 'match': 'claude-context'}
        assert rig._match_detector('mcp__claude-context__search_code', det)

    def test_mcp_tool_leaf_match(self):
        det = {'kind': 'mcp_tool', 'match': 'search_code'}
        assert rig._match_detector('mcp__claude-context__search_code', det)

    def test_no_match_on_unrelated_tool(self):
        det = {'kind': 'mcp_tool', 'match': 'claude-context'}
        assert not rig._match_detector('Read', det)
        assert not rig._match_detector(None, det)

    def test_plain_tool_kind(self):
        assert rig._match_detector('my_search', {'kind': 'tool', 'match': 'search'})

    def test_optimizer_active_scans_events(self):
        E = rig.audit_events.Event
        events = [E(0, 'read', tool='Read'),
                  E(1, 'tool_call', tool='mcp__claude-context__search_code')]
        det = {'kind': 'mcp_tool', 'match': 'claude-context'}
        assert rig.optimizer_active(events, det)
        assert not rig.optimizer_active([E(0, 'read', tool='Read')], det)

    def test_detector_of_resolves_named_provider(self):
        assert rig._detector_of('claude-context')['match'] == 'claude-context'

    def test_detector_of_rejects_provider_without_detector(self):
        with pytest.raises(ValueError):
            rig._detector_of('baseline')

    def test_cram_layer_has_get_context_detector(self):
        # Enables `cram rig --observe cram` — A/B a user's own context-layer use.
        assert rig._detector_of('cram') == {'kind': 'mcp_tool', 'match': 'get_context'}

    def test_observe_cram_splits_on_get_context_calls(self, tmp_path, monkeypatch):
        td = tmp_path / 'proj'
        td.mkdir()
        monkeypatch.setattr('cram.audit._project_transcript_dir',
                            lambda r, d=str(td): d)
        def write(name, used_layer):
            msgs = [{'type': 'tool_use', 'name': 'Read', 'input': {'file_path': 'a.py'}}]
            if used_layer:
                msgs.append({'type': 'tool_use',
                             'name': 'mcp__cram-ai__get_context', 'input': {}})
            msgs.append({'usage': {'input_tokens': 0, 'cache_read_input_tokens': 1000,
                                   'cache_creation_input_tokens': 0, 'output_tokens': 0}})
            with open(td / name, 'w') as f:
                for m in msgs:
                    f.write(json.dumps(m) + '\n')
        write('with.jsonl', True)
        write('without.jsonl', False)
        obs = rig.observe_optimizer(str(tmp_path), 'cram', days=3650)
        assert obs['on']['sessions'] == 1 and obs['off']['sessions'] == 1


class TestObserve:
    def _proj(self, tmp_path, monkeypatch):
        td = tmp_path / 'proj'
        td.mkdir()
        monkeypatch.setattr('cram.audit._project_transcript_dir',
                            lambda r, d=str(td): d)
        return td

    def _session(self, td, name, *, with_tool, reads, cache_read):
        msgs = []
        for _ in range(reads):
            msgs.append({'type': 'tool_use', 'name': 'Read', 'input': {'file_path': 'a.py'}})
        if with_tool:
            msgs.append({'type': 'tool_use',
                         'name': 'mcp__claude-context__search_code', 'input': {}})
        msgs.append({'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': 'a.py'}})
        msgs.append({'usage': {'input_tokens': 0, 'cache_read_input_tokens': cache_read,
                               'cache_creation_input_tokens': 0, 'output_tokens': 0}})
        with open(td / name, 'w') as f:
            for m in msgs:
                f.write(json.dumps(m) + '\n')

    def test_splits_on_off_and_aggregates(self, tmp_path, monkeypatch):
        td = self._proj(tmp_path, monkeypatch)
        # optimizer-on: fewer reads; optimizer-off: more reads
        self._session(td, 's_on.jsonl', with_tool=True, reads=1, cache_read=5_000)
        self._session(td, 's_off.jsonl', with_tool=False, reads=6, cache_read=20_000)
        obs = rig.observe_optimizer(str(tmp_path), 'claude-context', days=3650)
        assert obs['on']['sessions'] == 1 and obs['off']['sessions'] == 1
        assert obs['on']['avg_reads_before_edit'] == 1
        assert obs['off']['avg_reads_before_edit'] == 6
        # eff tokens = cache_read * 0.10 (anthropic)
        assert obs['on']['avg_eff_tokens'] == pytest.approx(500.0)

    def test_none_when_no_transcripts(self, tmp_path, monkeypatch):
        self._proj(tmp_path, monkeypatch)
        assert rig.observe_optimizer(str(tmp_path), 'claude-context') is None

    def test_render_observation_runs(self, tmp_path, monkeypatch):
        td = self._proj(tmp_path, monkeypatch)
        self._session(td, 's_on.jsonl', with_tool=True, reads=1, cache_read=5_000)
        self._session(td, 's_off.jsonl', with_tool=False, reads=6, cache_read=20_000)
        obs = rig.observe_optimizer(str(tmp_path), 'claude-context', days=3650)
        out = rig.render_observation(obs, 'claude-context')
        assert 'Observational A/B' in out and 'claude-context' in out
