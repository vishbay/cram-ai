"""Tests for the top-level `cram` dispatcher in cram/cli.py."""

import pytest

import cram
from cram.cli import main


def _run(monkeypatch, argv):
    monkeypatch.setattr('sys.argv', ['cram'] + argv)
    with pytest.raises(SystemExit) as exc:
        main()
    return exc.value.code


class TestVersionFlag:
    def test_long_flag_prints_version(self, monkeypatch, capsys):
        code = _run(monkeypatch, ['--version'])
        assert code == 0
        assert capsys.readouterr().out.strip() == f'cram-ai {cram.__version__}'

    def test_short_flag_prints_version(self, monkeypatch, capsys):
        code = _run(monkeypatch, ['-V'])
        assert code == 0
        assert capsys.readouterr().out.strip() == f'cram-ai {cram.__version__}'


class TestUsage:
    def test_no_args_prints_usage_exit_zero(self, monkeypatch, capsys):
        code = _run(monkeypatch, [])
        assert code == 0
        assert 'Usage: cram' in capsys.readouterr().out

    def test_help_flag_prints_usage_exit_zero(self, monkeypatch, capsys):
        code = _run(monkeypatch, ['--help'])
        assert code == 0
        assert 'Usage: cram' in capsys.readouterr().out

    def test_usage_groups_core_vs_optional_context(self, monkeypatch, capsys):
        _run(monkeypatch, ['--help'])
        out = capsys.readouterr().out
        assert 'Profile & referee' in out
        assert 'Optional — context layer' in out
        # audit/rig listed above the context-layer commands (task/mcp).
        assert out.index('rig') < out.index('Optional — context layer') < out.index('mcp')

    def test_unknown_command_errors(self, monkeypatch, capsys):
        code = _run(monkeypatch, ['bogus'])
        assert code == 1
        assert 'Unknown command' in capsys.readouterr().out
