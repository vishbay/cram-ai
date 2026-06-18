"""Phase 1 benchmark harness: corpus tiers, --repeats variance, the
pycache-safe workdir copy, and the leaderboard renderer. No live agent."""

from __future__ import annotations
import json
import os

from cram import rig

BENCH = os.path.join(os.path.dirname(__file__), '..',
                     'examples', 'rig', 'bench', 'corpus.bench.json')

_MEDIAN_FIX = '''\
def mean(values):
    return sum(values) / len(values)


def median(values):
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2
'''


# ── corpus + tiers ────────────────────────────────────────────────────────────

def test_bench_corpus_loads_with_tiers_and_fixtures():
    corpus = rig.load_corpus(BENCH)
    by_tier = {}
    for t in corpus:
        assert t.tier in ('small', 'medium', 'large'), t.id
        assert os.path.isdir(t.fixture), f'missing fixture: {t.fixture}'
        by_tier.setdefault(t.tier, []).append(t.id)
    assert set(by_tier) == {'small', 'medium', 'large'}


def test_task_from_dict_preserves_tier_and_defaults_none():
    assert rig.Task.from_dict({'id': 'a', 'prompt': 'p', 'tier': 'medium'}).tier == 'medium'
    assert rig.Task.from_dict({'id': 'a', 'prompt': 'p'}).tier is None


# ── pycache-safe copy ─────────────────────────────────────────────────────────

def test_prepare_workdir_skips_bytecode(tmp_path):
    src = tmp_path / 'fixture'
    (src / '__pycache__').mkdir(parents=True)
    (src / '__pycache__' / 'mod.cpython-313.pyc').write_text('stale')
    (src / 'mod.py').write_text('x = 1\n')
    task = rig.Task(id='t', prompt='p', fixture=str(src))
    wd = rig._prepare_workdir(task, str(tmp_path / 'wd'))
    assert os.path.exists(os.path.join(wd, 'mod.py'))
    assert not os.path.exists(os.path.join(wd, '__pycache__'))


# ── repeats + variance ────────────────────────────────────────────────────────

def _varying_solver():
    """A MockRunner that fixes the median bug and reports a different token
    count on each call, so summarize() has real variance to compute."""
    calls = {'n': 0}

    def run(task, setup, workdir):
        with open(os.path.join(workdir, 'stats.py'), 'w') as f:
            f.write(_MEDIAN_FIX)
        calls['n'] += 1
        tp = os.path.join(workdir, 'transcript.jsonl')
        with open(tp, 'w') as f:
            f.write(json.dumps({'usage': {
                'input_tokens': 0,
                'cache_read_input_tokens': 1000 * calls['n'],
                'cache_creation_input_tokens': 0,
                'output_tokens': 0}}) + '\n')
        return tp
    return rig.MockRunner(run)


def test_repeats_runs_each_cell_n_times_without_collision(tmp_path):
    corpus = [t for t in rig.load_corpus(BENCH) if t.id == 'fix-failing-test']
    results = rig.run_rig(corpus, [rig.BaselineAdapter()], _varying_solver(),
                          rig.CommandOracle(), work_root=str(tmp_path), repeats=3)
    assert len(results) == 3
    assert all(r.success for r in results)
    assert {r.rep for r in results} == {0, 1, 2}


def test_repeat_workdirs_have_no_special_chars(tmp_path):
    """Repeat workdir names must stay [A-Za-z0-9/_.-] so the transcript-dir
    resolver (which only dashes '/') agrees with Claude Code (which dashes any
    other char too). A '#' suffix here made every repeat measure 0 tokens."""
    import re
    seen = []

    def run(task, setup, workdir):
        seen.append(workdir)
        return None
    corpus = [t for t in rig.load_corpus(BENCH) if t.id == 'fix-failing-test']
    rig.run_rig(corpus, [rig.BaselineAdapter()], rig.MockRunner(run),
                rig.CommandOracle(), work_root=str(tmp_path), repeats=3)
    assert len(seen) == 3
    for wd in seen:
        assert re.fullmatch(r'[A-Za-z0-9/_.\-]+', wd), wd
        assert '#' not in wd


def test_summarize_reports_n_runs_and_stdev(tmp_path):
    corpus = [t for t in rig.load_corpus(BENCH) if t.id == 'fix-failing-test']
    summary = rig.summarize(rig.run_rig(corpus, [rig.BaselineAdapter()],
                                        _varying_solver(), rig.CommandOracle(),
                                        work_root=str(tmp_path), repeats=3))
    base = summary['providers']['baseline']
    assert base['n_runs'] == 3
    assert base['eff_tokens_stdev'] > 0          # tokens varied across reps
    assert base['success_rate'] == 1.0


def test_stdev_zero_for_single_run(tmp_path):
    corpus = [t for t in rig.load_corpus(BENCH) if t.id == 'fix-failing-test']
    summary = rig.summarize(rig.run_rig(corpus, [rig.BaselineAdapter()],
                                        _varying_solver(), rig.CommandOracle(),
                                        work_root=str(tmp_path), repeats=1))
    assert summary['providers']['baseline']['eff_tokens_stdev'] == 0.0


# ── leaderboard ───────────────────────────────────────────────────────────────

def _write_result(path, providers, *, meta=None):
    doc = {'meta': meta or {}, 'summary': {'providers': providers, 'results': []}}
    with open(path, 'w') as f:
        json.dump(doc, f)
    return str(path)


def _prov(success, tok, *, ran=2, passed=2, n=2, stdev=0.0):
    return {'tasks': ran, 'ran': ran, 'skipped': 0, 'passed': passed,
            'success_rate': success, 'mean_eff_tokens_passed': tok,
            'n_runs': n, 'eff_tokens_stdev': stdev, 'skip_reason': ''}


def test_leaderboard_ranks_cheaper_at_equal_success_first(tmp_path):
    f = _write_result(tmp_path / 'r.json', {
        'baseline': _prov(1.0, 10_000),
        'cram':     _prov(1.0, 8_000),
    }, meta={'model': 'sonnet'})
    md = rig.render_leaderboard([f])
    # cram (cheaper, equal success) must appear before baseline
    assert md.index('| cram |') < md.index('| baseline |')
    assert '-20%' in md          # cram vs baseline within the run


def test_leaderboard_does_not_credit_low_success_for_cheap_tokens(tmp_path):
    f = _write_result(tmp_path / 'r.json', {
        'baseline': _prov(1.0, 10_000),
        'cheat':    _prov(0.5, 1_000),   # cheaper, but only by failing half
    })
    md = rig.render_leaderboard([f])
    # higher success ranks first despite the larger token count
    assert md.index('| baseline |') < md.index('| cheat |')


def test_leaderboard_handles_empty(tmp_path):
    f = _write_result(tmp_path / 'r.json', {})
    md = rig.render_leaderboard([f])
    assert 'no results' in md
