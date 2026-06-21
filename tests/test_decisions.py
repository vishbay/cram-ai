"""Tests for cram/decisions.py — mine and show commands."""

from __future__ import annotations
import os
from unittest.mock import patch

import pytest

from cram.decisions import (
    _filter_commits,
    _parse_model_output,
    _append_with_reason,
    show_decisions,
    mine_decisions,
)

CONTEXT_DIR = '.ai-context'


@pytest.fixture()
def repo(tmp_path):
    ctx = tmp_path / CONTEXT_DIR
    ctx.mkdir()
    (ctx / 'DECISIONS.md').write_text('# Decisions\n')
    # Minimal git repo so git log works
    os.system(f'git -C {tmp_path} init -q')
    os.system(f'git -C {tmp_path} config user.email test@test.com')
    os.system(f'git -C {tmp_path} config user.name Test')
    return tmp_path


class TestFilterCommits:
    def test_keeps_decision_keywords(self):
        lines = [
            'abc1234 chose postgres over mysql for reliability',
            'def5678 fix typo in README',
            'ghi9012 decided to drop Redis because of latency',
            'jkl3456 bump version to 1.2.3',
        ]
        result = _filter_commits(lines)
        assert any('postgres' in l for l in result)
        assert any('Redis' in l for l in result)
        assert not any('typo' in l for l in result)
        assert not any('bump' in l for l in result)

    def test_case_insensitive(self):
        lines = ['abc1234 Decided to use SQLite for the event store']
        assert _filter_commits(lines)

    def test_empty_input(self):
        assert _filter_commits([]) == []

    def test_no_matches(self):
        lines = ['abc1234 fix null pointer', 'def5678 update deps']
        assert _filter_commits(lines) == []


class TestParseModelOutput:
    def test_parses_valid_lines(self):
        text = (
            'DECISION: use JWT over session cookies | REASON: reduces server-side state\n'
            'DECISION: drop Redis pub/sub | REASON: latency + ops cost\n'
        )
        results = _parse_model_output(text)
        assert len(results) == 2
        assert results[0] == ('use JWT over session cookies', 'reduces server-side state')
        assert results[1] == ('drop Redis pub/sub', 'latency + ops cost')

    def test_ignores_non_matching_lines(self):
        text = 'Some random text\nDECISION: use postgres | REASON: reliability\nMore noise\n'
        results = _parse_model_output(text)
        assert len(results) == 1

    def test_empty_output(self):
        assert _parse_model_output('') == []
        assert _parse_model_output('no decisions here') == []

    def test_strips_whitespace(self):
        text = 'DECISION:  use rust  | REASON:  performance  \n'
        results = _parse_model_output(text)
        assert results[0] == ('use rust', 'performance')


class TestAppendWithReason:
    def test_appends_entry_with_reason(self, repo):
        _append_with_reason(str(repo), 'use JWT', 'stateless auth')
        content = (repo / CONTEXT_DIR / 'DECISIONS.md').read_text()
        assert '[DECISION-001]' in content
        assert 'use JWT' in content
        assert 'stateless auth' in content

    def test_status_is_accepted(self, repo):
        _append_with_reason(str(repo), 'use postgres', 'reliability')
        content = (repo / CONTEXT_DIR / 'DECISIONS.md').read_text()
        assert 'Accepted' in content

    def test_increments_id(self, repo):
        decisions_path = repo / CONTEXT_DIR / 'DECISIONS.md'
        decisions_path.write_text(
            '# Decisions\n\n## [DECISION-003] existing\n- **Status:** Accepted\n'
        )
        _append_with_reason(str(repo), 'new decision', 'reason')
        content = decisions_path.read_text()
        assert '[DECISION-004]' in content


class TestShowDecisions:
    def test_prints_decisions_md(self, repo, capsys):
        (repo / CONTEXT_DIR / 'DECISIONS.md').write_text('# Decisions\n\n## [DECISION-001] test\n')
        show_decisions(str(repo))
        out = capsys.readouterr().out
        assert 'DECISION-001' in out

    def test_exits_when_file_missing(self, repo):
        (repo / CONTEXT_DIR / 'DECISIONS.md').unlink()
        with pytest.raises(SystemExit):
            show_decisions(str(repo))


class TestMineDecisions:
    def test_no_commits_prints_message(self, repo, capsys):
        mine_decisions(str(repo), days=1)
        out = capsys.readouterr().out
        assert 'No commits' in out or 'not found' in out.lower() or 'No ' in out

    def test_no_decision_commits_prints_message(self, repo, capsys):
        # Create a commit with no decision language
        (repo / 'file.txt').write_text('hello')
        os.system(f'git -C {repo} add file.txt')
        os.system(f'git -C {repo} commit -q -m "fix typo in README"')

        mine_decisions(str(repo), days=7)
        out = capsys.readouterr().out
        assert 'No decision-shaped' in out or 'No ' in out

    def test_model_no_output_prints_message(self, repo, capsys):
        (repo / 'file.txt').write_text('hello')
        os.system(f'git -C {repo} add file.txt')
        os.system(f'git -C {repo} commit -q -m "decided to use postgres over mysql"')

        with patch('cram.utils.call_context_model', return_value='no decisions here'):
            mine_decisions(str(repo), days=7)

        out = capsys.readouterr().out
        assert 'No decisions extracted' in out

    def test_interactive_accept(self, repo, capsys):
        (repo / 'file.txt').write_text('hello')
        os.system(f'git -C {repo} add file.txt')
        os.system(f'git -C {repo} commit -q -m "chose JWT over sessions because stateless"')

        model_out = 'DECISION: use JWT | REASON: stateless\n'
        with patch('cram.utils.call_context_model', return_value=model_out):
            with patch('builtins.input', return_value='a'):
                mine_decisions(str(repo), days=7)

        content = (repo / CONTEXT_DIR / 'DECISIONS.md').read_text()
        assert 'use JWT' in content

    def test_interactive_skip(self, repo, capsys):
        (repo / 'file.txt').write_text('hello')
        os.system(f'git -C {repo} add file.txt')
        os.system(f'git -C {repo} commit -q -m "chose JWT over sessions because stateless"')

        model_out = 'DECISION: use JWT | REASON: stateless\n'
        with patch('cram.utils.call_context_model', return_value=model_out):
            with patch('builtins.input', return_value='s'):
                mine_decisions(str(repo), days=7)

        content = (repo / CONTEXT_DIR / 'DECISIONS.md').read_text()
        assert 'use JWT' not in content

    def test_interactive_quit_stops_early(self, repo, capsys):
        (repo / 'file.txt').write_text('hello')
        os.system(f'git -C {repo} add file.txt')
        os.system(f'git -C {repo} commit -q -m "chose JWT over sessions"')

        model_out = (
            'DECISION: use JWT | REASON: stateless\n'
            'DECISION: drop Redis | REASON: latency\n'
        )
        call_count = 0
        def mock_input(prompt):
            nonlocal call_count
            call_count += 1
            return 'q'

        with patch('cram.utils.call_context_model', return_value=model_out):
            with patch('builtins.input', side_effect=mock_input):
                mine_decisions(str(repo), days=7)

        # Only one input prompt — quit stops the loop immediately
        assert call_count == 1
