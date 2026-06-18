"""The referee demo script must keep working (it backs the README GIF)."""

from __future__ import annotations
import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'demo', 'referee_demo.py')


def test_referee_demo_runs_and_does_not_credit_the_failed_arm():
    out = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr
    text = out.stdout
    assert 'tokens at fixed success' in text
    # baseline passes; the cheap-but-broken arm fails and is not credited.
    assert 'baseline' in text and 'aggressive-trim' in text
    assert '0%' in text                       # the failing arm
    assert 'FAILED the task' in text
