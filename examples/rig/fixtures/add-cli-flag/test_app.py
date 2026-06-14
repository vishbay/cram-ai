"""Spec for app.py — this is the oracle. Do not edit these tests; make them
pass by implementing the --verbose flag in app.py."""

import contextlib
import io

from app import main


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


def test_result_always_printed():
    rc, out = _run([])
    assert rc == 0
    assert "result: 42" in out


def test_quiet_has_no_debug():
    _, out = _run([])
    assert "debug" not in out.lower()


def test_verbose_emits_debug():
    rc, out = _run(["--verbose"])
    assert rc == 0
    assert "result: 42" in out          # result still printed
    assert "debug" in out.lower()        # --verbose adds a debug line
