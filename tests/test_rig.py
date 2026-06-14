"""Tests for the controlled benchmark rig (cram/rig.py).

Covers the framework end-to-end with a MockRunner (no live agent): corpus
loading, provider availability + stubs, the run loop, the CommandOracle, the
effective-token measurement (reusing the audit timeline), and the
tokens-at-fixed-success summary."""

from __future__ import annotations
import json

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
    def test_blocks_without_api_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
        with pytest.raises(RuntimeError, match='ANTHROPIC_API_KEY'):
            rig.LiveRunner().run(rig.Task('a', 'x'), {}, str(tmp_path))
