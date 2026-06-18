#!/usr/bin/env python3
"""Deterministic demo of the cram rig *referee*.

The point cram makes that raw token tools miss: a "saving" that comes from the
agent failing the task is not a saving. This runs two arms over one real fixture
with scripted (MockRunner) agents — no live model, fully reproducible:

  baseline          fixes the bug  → passes, normal token cost
  aggressive-trim   trims so hard the agent gives up → cheaper, but FAILS

The referee reports tokens **at fixed success**, so the cheap-but-broken arm is
not credited for its smaller token count. Run:  python scripts/demo/referee_demo.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from cram import rig  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), '..', '..',
                       'examples', 'rig', 'fixtures', 'fix-failing-test')

_FIX = '''\
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


class _DemoAdapter:
    """A trivial provider that just tags its arm name into setup."""
    def __init__(self, name: str):
        self.name = name

    def availability(self):
        return rig.Availability(True)

    def setup(self, task, workdir):
        return {'arm': self.name}


def _transcript(workdir: str, cache_read: int) -> str:
    tp = os.path.join(workdir, 'transcript.jsonl')
    with open(tp, 'w') as f:
        f.write(json.dumps({'usage': {
            'input_tokens': 0, 'cache_read_input_tokens': cache_read,
            'cache_creation_input_tokens': 0, 'output_tokens': 0}}) + '\n')
    return tp


def _runner():
    def run(task, setup, workdir):
        if setup.get('arm') == 'baseline':
            # Real fix → oracle passes. Honest token cost.
            with open(os.path.join(workdir, 'stats.py'), 'w') as f:
                f.write(_FIX)
            return _transcript(workdir, cache_read=120_000)
        # "aggressive-trim": cheaper context, but the agent never lands the fix.
        return _transcript(workdir, cache_read=40_000)
    return rig.MockRunner(run)


def main() -> None:
    corpus = [t for t in rig.load_corpus(
        os.path.join(os.path.dirname(FIXTURE), '..', 'corpus.example.json'))
        if t.id == 'fix-failing-test']
    providers = [_DemoAdapter('baseline'), _DemoAdapter('aggressive-trim')]
    with tempfile.TemporaryDirectory(prefix='cram-referee-demo-') as wd:
        results = rig.run_rig(corpus, providers, _runner(),
                              rig.CommandOracle(), work_root=wd)
    print(rig.render_summary(rig.summarize(results)))
    print("  aggressive-trim used FAR fewer tokens — but it FAILED the task,")
    print("  so the referee does not credit it. That's the whole point.\n")


if __name__ == '__main__':
    main()
