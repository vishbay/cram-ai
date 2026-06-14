"""End-to-end executability of the controlled rig over the real example
fixtures — no live agent. A MockRunner stands in for the coding agent: one that
applies the correct solution (oracle passes) and one that does nothing (oracle
fails, because the fixtures ship red). This proves the
corpus → fixture → agent → oracle pipeline actually runs."""

from __future__ import annotations
import json
import os

from cram import rig

CORPUS = os.path.join(os.path.dirname(__file__), '..',
                      'examples', 'rig', 'corpus.example.json')

# Known-correct solutions, written verbatim by the "solver" MockRunner.
_SOLUTIONS = {
    'fix-failing-test': ('stats.py', '''\
def mean(values):
    return sum(values) / len(values)


def median(values):
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2
'''),
    'add-cli-flag': ('app.py', '''\
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--verbose" in argv:
        print("debug: verbose mode on")
    print("result: 42")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''),
}


def _transcript(workdir: str) -> str:
    """Minimal valid transcript so effective_tokens has something to read."""
    tp = os.path.join(workdir, 'transcript.jsonl')
    with open(tp, 'w') as f:
        f.write(json.dumps({'usage': {'input_tokens': 0,
                                      'cache_read_input_tokens': 1000,
                                      'cache_creation_input_tokens': 0,
                                      'output_tokens': 0}}) + '\n')
    return tp


def _solver_runner():
    def run(task, setup, workdir):
        fname, content = _SOLUTIONS[task.id]
        with open(os.path.join(workdir, fname), 'w') as f:
            f.write(content)
        return _transcript(workdir)
    return rig.MockRunner(run)


def _noop_runner():
    def run(task, setup, workdir):
        return _transcript(workdir)   # change nothing → fixtures stay red
    return rig.MockRunner(run)


def test_corpus_loads_and_fixtures_exist():
    corpus = rig.load_corpus(CORPUS)
    assert {t.id for t in corpus} == {'fix-failing-test', 'add-cli-flag'}
    for t in corpus:
        assert os.path.isdir(t.fixture), f'missing fixture: {t.fixture}'


def test_solver_passes_the_oracle(tmp_path):
    corpus = rig.load_corpus(CORPUS)
    results = rig.run_rig(corpus, [rig.BaselineAdapter()], _solver_runner(),
                          rig.CommandOracle(), work_root=str(tmp_path))
    assert len(results) == 2
    assert all(r.success for r in results), [(r.task_id, r.reason) for r in results]
    assert all(r.eff_tokens > 0 for r in results)   # measured from the transcript


def test_noop_fails_the_oracle_because_fixtures_ship_red(tmp_path):
    corpus = rig.load_corpus(CORPUS)
    results = rig.run_rig(corpus, [rig.BaselineAdapter()], _noop_runner(),
                          rig.CommandOracle(), work_root=str(tmp_path))
    assert results and all(not r.success and not r.skipped for r in results)


def test_summary_separates_solver_from_noop(tmp_path):
    corpus = rig.load_corpus(CORPUS)
    solved = rig.summarize(rig.run_rig(corpus, [rig.BaselineAdapter()],
                                       _solver_runner(), rig.CommandOracle(),
                                       work_root=str(tmp_path / 'a')))
    noop = rig.summarize(rig.run_rig(corpus, [rig.BaselineAdapter()],
                                     _noop_runner(), rig.CommandOracle(),
                                     work_root=str(tmp_path / 'b')))
    assert solved['providers']['baseline']['success_rate'] == 1.0
    assert noop['providers']['baseline']['success_rate'] == 0.0
