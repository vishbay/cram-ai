"""Tests for cram/mcp_server.py — deterministic output from each tool."""

import os
from unittest.mock import patch

import pytest


CONTEXT_DIR = '.ai-context'


@pytest.fixture()
def repo(tmp_path):
    """Minimal initialised repo for MCP tool tests."""
    ctx = tmp_path / CONTEXT_DIR
    ctx.mkdir()
    (ctx / 'ARCHITECTURE.md').write_text('# Arch\n\nKey files: main.py\n')
    (ctx / 'DECISIONS.md').write_text('# Decisions\n\n## [D-001] Use Python\n')
    (ctx / 'SYMBOLS.md').write_text('main.py: main, helper\nutils.py: parse, format\n')
    (tmp_path / 'main.py').write_text('def main(): pass\ndef helper(): pass\n')
    return tmp_path


# ---------------------------------------------------------------------------
# get_architecture determinism
# ---------------------------------------------------------------------------

class TestGetArchitectureDeterminism:
    def test_identical_on_repeat(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        r1 = srv.get_architecture()
        r2 = srv.get_architecture()
        assert r1 == r2

    def test_returns_file_content(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        result = srv.get_architecture()
        assert '# Arch' in result


# ---------------------------------------------------------------------------
# get_decisions determinism
# ---------------------------------------------------------------------------

class TestGetDecisionsDeterminism:
    def test_identical_on_repeat(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        r1 = srv.get_decisions()
        r2 = srv.get_decisions()
        assert r1 == r2


# ---------------------------------------------------------------------------
# get_symbols determinism
# ---------------------------------------------------------------------------

class TestGetSymbolsDeterminism:
    def test_full_index_identical_on_repeat(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        r1 = srv.get_symbols()
        r2 = srv.get_symbols()
        assert r1 == r2

    def test_filtered_results_sorted(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        result = srv.get_symbols('py')
        lines = result.split('\n')[2:]  # skip header line
        non_empty = [l for l in lines if l.strip()]
        assert non_empty == sorted(non_empty)

    def test_filtered_identical_on_repeat(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        r1 = srv.get_symbols('main')
        r2 = srv.get_symbols('main')
        assert r1 == r2


# ---------------------------------------------------------------------------
# get_context determinism
# ---------------------------------------------------------------------------

class TestGetContextDeterminism:
    def test_identical_on_repeat(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        monkeypatch.chdir(repo)

        mock_entries = [('main.py', ['main', 'helper'])]
        with patch('cram.find_context.find_relevant_files', return_value=mock_entries):
            r1 = srv.get_context('fix the helper function')
            r2 = srv.get_context('fix the helper function')

        assert r1 == r2

    def test_no_volatile_token_header(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        monkeypatch.chdir(repo)

        mock_entries = [('main.py', ['main'])]
        with patch('cram.find_context.find_relevant_files', return_value=mock_entries):
            result = srv.get_context('some task')

        assert '<!-- cram-ai context' not in result
        assert 'tokens -->' not in result

    def test_no_task_returns_current_task_md(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        (repo / CONTEXT_DIR / 'CURRENT_TASK.md').write_text('# Task: previous task\n\nsome context\n')
        result = srv.get_context()

        assert '# Task: previous task' in result
        assert 'some context' in result

    def test_no_task_no_file_returns_guidance(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        result = srv.get_context()

        assert 'No context loaded yet' in result

    def test_stale_header_prepended_when_stale(self, repo, monkeypatch):
        import cram.mcp_server as srv
        import cram.health as health_mod
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        (repo / CONTEXT_DIR / 'CURRENT_TASK.md').write_text('# Task: fix\n\nsome context\n')

        fake_health = {
            'staleness_band': 'stale', 'staleness_score': 6,
            'commits_since_sync': 6, 'state': 'stale', 'last_commit_age': '1h ago',
            'files': {},
        }
        monkeypatch.setattr(health_mod, 'context_health', lambda root: fake_health)

        result = srv.get_context()
        assert result.startswith('> staleness: stale')
        assert 'run `cram sync`' in result

    def test_no_stale_header_when_fresh(self, repo, monkeypatch):
        import cram.mcp_server as srv
        import cram.health as health_mod
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        (repo / CONTEXT_DIR / 'CURRENT_TASK.md').write_text('# Task: fix\n\nsome context\n')

        fake_health = {
            'staleness_band': 'fresh', 'staleness_score': 1,
            'commits_since_sync': 0, 'state': 'fresh', 'last_commit_age': None,
            'files': {},
        }
        monkeypatch.setattr(health_mod, 'context_health', lambda root: fake_health)

        result = srv.get_context()
        assert not result.startswith('> staleness:')


# ---------------------------------------------------------------------------
# get_health determinism + content
# ---------------------------------------------------------------------------

class TestGetHealthDeterminism:
    def test_identical_on_repeat(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        r1 = srv.get_health()
        r2 = srv.get_health()
        assert r1 == r2

    def test_contains_health_header(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        result = srv.get_health()
        assert '# Context health' in result
        assert 'staleness:' in result

    def test_no_wall_clock_in_output(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        result = srv.get_health()
        # Determinism check: no "ago" timestamps in the health body
        assert ' ago' not in result

    def test_over_budget_file_flagged(self, repo, monkeypatch):
        import cram.mcp_server as srv
        import cram.health as health_mod
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        fake_health = {
            'staleness_band': 'stale', 'staleness_score': 6,
            'commits_since_sync': 6, 'state': 'stale', 'last_commit_age': None,
            'files': {
                'GOTCHAS.md': {'tokens': 470, 'lines': 30, 'budget': 400, 'budget_status': 'over'},
            },
        }
        monkeypatch.setattr(health_mod, 'context_health', lambda root: fake_health)

        result = srv.get_health()
        assert 'over' in result
        assert 'trim before next sync' in result
        assert 'GOTCHAS.md' in result
        assert 'recommendation' in result


# ---------------------------------------------------------------------------
# Task slot namespacing
# ---------------------------------------------------------------------------

class TestTaskSlotNamespacing:
    def test_different_tasks_write_different_slot_files(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        monkeypatch.chdir(repo)

        entries_a = [('main.py', ['main'])]
        entries_b = [('main.py', ['helper'])]
        with patch('cram.find_context.find_relevant_files', side_effect=[entries_a, entries_b]):
            srv.get_context('add auth login')
            srv.get_context('fix database query')

        tasks_dir = repo / CONTEXT_DIR / 'tasks'
        slot_files = list(tasks_dir.glob('*.md'))
        assert len(slot_files) == 2

    def test_same_task_reuses_same_slot(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        monkeypatch.chdir(repo)

        entries = [('main.py', ['main'])]
        with patch('cram.find_context.find_relevant_files', return_value=entries):
            srv.get_context('fix the helper function')
            srv.get_context('fix the helper function')

        tasks_dir = repo / CONTEXT_DIR / 'tasks'
        slot_files = list(tasks_dir.glob('*.md'))
        assert len(slot_files) == 1

    def test_slot_content_matches_returned_content(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        monkeypatch.chdir(repo)

        entries = [('main.py', ['main'])]
        with patch('cram.find_context.find_relevant_files', return_value=entries):
            result = srv.get_context('add new feature')

        tasks_dir = repo / CONTEXT_DIR / 'tasks'
        slot_file = next(tasks_dir.glob('*.md'))
        assert slot_file.read_text() == result

    def test_stale_slots_cleaned_on_generate(self, repo, monkeypatch):
        import time
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        monkeypatch.chdir(repo)

        # Pre-create a stale slot file
        tasks_dir = repo / CONTEXT_DIR / 'tasks'
        tasks_dir.mkdir(parents=True)
        stale = tasks_dir / 'stale-task.md'
        stale.write_text('old context')
        # Back-date it by 25 hours
        old_time = time.time() - 25 * 3600
        os.utime(str(stale), (old_time, old_time))

        entries = [('main.py', ['main'])]
        with patch('cram.find_context.find_relevant_files', return_value=entries):
            srv.get_context('new task here')

        assert not stale.exists()


# ---------------------------------------------------------------------------
# Usage log
# ---------------------------------------------------------------------------

class TestUsageLog:
    def test_generate_appends_to_usage_jsonl(self, repo, monkeypatch):
        import json as _json
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        monkeypatch.chdir(repo)

        entries = [('main.py', ['main'])]
        with patch('cram.find_context.find_relevant_files', return_value=entries):
            srv.get_context('add login feature')

        log_path = repo / CONTEXT_DIR / 'usage.jsonl'
        assert log_path.exists()
        line = _json.loads(log_path.read_text().strip().splitlines()[-1])
        assert line['source'] == 'generate'
        assert line['task'] == 'add login feature'
        assert line['tokens'] > 0
        assert 'ts' in line

    def test_reload_appends_to_usage_jsonl(self, repo, monkeypatch):
        import json as _json
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))

        (repo / CONTEXT_DIR / 'CURRENT_TASK.md').write_text('# Task: reload test\n\nsome context\n')
        srv.get_context()

        log_path = repo / CONTEXT_DIR / 'usage.jsonl'
        assert log_path.exists()
        line = _json.loads(log_path.read_text().strip().splitlines()[-1])
        assert line['source'] == 'reload'

    def test_multiple_calls_append_multiple_lines(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        monkeypatch.chdir(repo)

        entries = [('main.py', ['main'])]
        with patch('cram.find_context.find_relevant_files', return_value=entries):
            srv.get_context('task one')
            srv.get_context('task two')

        log_path = repo / CONTEXT_DIR / 'usage.jsonl'
        lines = [l for l in log_path.read_text().strip().splitlines() if l]
        assert len(lines) == 2


class TestProposeDecision:
    def test_appends_pending_entry_to_decisions(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        (repo / CONTEXT_DIR / 'DECISIONS.md').write_text('# Decisions\n')

        result = srv.propose_decision('use JWT over sessions', reason='stateless')

        assert 'DECISION-' in result
        assert 'PENDING' in result.upper() or 'pending' in result.lower()
        content = (repo / CONTEXT_DIR / 'DECISIONS.md').read_text()
        assert '[PENDING]' in content
        assert 'use JWT over sessions' in content
        assert 'stateless' in content

    def test_pending_status_line_present(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        (repo / CONTEXT_DIR / 'DECISIONS.md').write_text('# Decisions\n')

        srv.propose_decision('drop Redis', reason='latency')

        content = (repo / CONTEXT_DIR / 'DECISIONS.md').read_text()
        assert 'Pending' in content

    def test_increments_decision_id(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        decisions_path = repo / CONTEXT_DIR / 'DECISIONS.md'
        decisions_path.write_text(
            '# Decisions\n\n## [DECISION-001] first\n- **Status:** Accepted\n'
        )

        srv.propose_decision('second decision')

        content = decisions_path.read_text()
        assert '[DECISION-002]' in content

    def test_writes_suggestions_jsonl(self, repo, monkeypatch):
        import json
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        (repo / CONTEXT_DIR / 'DECISIONS.md').write_text('# Decisions\n')

        srv.propose_decision('use postgres', reason='reliability')

        log = repo / CONTEXT_DIR / 'suggestions.jsonl'
        assert log.exists()
        entry = json.loads(log.read_text().strip())
        assert entry['type'] == 'decision'
        assert 'postgres' in entry['text']
        assert 'ts' in entry

    def test_returns_error_when_no_repo_root(self, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', '')
        result = srv.propose_decision('some decision')
        assert 'Error' in result

    def test_returns_error_when_decisions_missing(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        # Don't create DECISIONS.md
        (repo / CONTEXT_DIR / 'DECISIONS.md').unlink(missing_ok=True)
        result = srv.propose_decision('some decision')
        assert 'not found' in result.lower() or 'init' in result.lower()

    def test_alternatives_field_populated(self, repo, monkeypatch):
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        (repo / CONTEXT_DIR / 'DECISIONS.md').write_text('# Decisions\n')

        srv.propose_decision('use JWT', reason='stateless', alternatives='sessions, cookies')

        content = (repo / CONTEXT_DIR / 'DECISIONS.md').read_text()
        assert 'sessions, cookies' in content


# ---------------------------------------------------------------------------
# A1: Slot coherence — reload, add_file, and archive work against the slot
# ---------------------------------------------------------------------------

class TestSlotCoherence:
    def test_get_context_no_arg_returns_slot_content(self, repo, monkeypatch):
        """get_context() with no arg returns slot content written by get_context(task)."""
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        monkeypatch.chdir(repo)

        entries = [('main.py', ['main'])]
        with patch('cram.find_context.find_relevant_files', return_value=entries):
            result1 = srv.get_context('fix the rate limiter')

        # Now call with no arg — should return the same slot content
        result2 = srv.get_context()
        assert 'fix the rate limiter' in result2
        assert result2.strip() == result1.strip() or 'fix the rate limiter' in result2

    def test_add_file_appends_to_slot_not_current_task(self, repo, monkeypatch):
        """add_file() appends to the active slot, not CURRENT_TASK.md."""
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        monkeypatch.chdir(repo)
        # Write a helper file to add
        (repo / 'utils.py').write_text('def parse(): pass\ndef format(): pass\n')

        entries = [('main.py', ['main'])]
        with patch('cram.find_context.find_relevant_files', return_value=entries):
            srv.get_context('add logging support')

        result = srv.add_file('utils.py')
        assert 'utils.py' in result

        # The slot file should contain utils.py
        from cram.session import get_last_slot
        slug = get_last_slot(str(repo))
        assert slug is not None
        slot_path = repo / CONTEXT_DIR / 'tasks' / f'{slug}.md'
        assert slot_path.exists()
        assert 'utils.py' in slot_path.read_text()

    def test_two_get_context_calls_archive_first_task(self, repo, monkeypatch):
        """After two get_context(task) calls, the first task appears in TASK_HISTORY.jsonl."""
        import json as _json
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(repo))
        monkeypatch.chdir(repo)

        entries_a = [('main.py', ['main'])]
        entries_b = [('main.py', ['helper'])]
        with patch('cram.find_context.find_relevant_files', side_effect=[entries_a, entries_b]):
            srv.get_context('task alpha')
            srv.get_context('task beta')

        history_path = repo / CONTEXT_DIR / 'TASK_HISTORY.jsonl'
        assert history_path.exists()
        lines = [l for l in history_path.read_text().strip().splitlines() if l]
        assert len(lines) >= 1
        first_entry = _json.loads(lines[0])
        assert first_entry['task'] == 'task alpha'
        assert 'slug' in first_entry
        assert 'ts' in first_entry


# ---------------------------------------------------------------------------
# A4: Canonical archive_task from session.py
# ---------------------------------------------------------------------------

class TestArchiveTask:
    def test_archive_writes_jsonl_entry(self, tmp_path):
        """archive_task writes a well-formed JSONL entry with ts/task/slug."""
        import json as _json
        from cram.session import archive_task

        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        source = tmp_path / 'task.md'
        source.write_text('# Current Task\n\n## Task\nfix the parser\n\n## Relevant Files\n')

        archive_task(str(tmp_path), str(source))

        history = ctx / 'TASK_HISTORY.jsonl'
        assert history.exists()
        entry = _json.loads(history.read_text().strip())
        assert entry['task'] == 'fix the parser'
        assert 'slug' in entry
        assert 'ts' in entry
        assert entry['slug'] == 'fix-the-parser'

    def test_archive_skips_session_ended_placeholder(self, tmp_path):
        """archive_task skips files containing the session-ended marker."""
        from cram.session import archive_task

        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        source = tmp_path / 'task.md'
        source.write_text('# Current Task\n\n## Task\n<!-- Session ended on commit. -->\n')

        archive_task(str(tmp_path), str(source))

        history = ctx / 'TASK_HISTORY.jsonl'
        assert not history.exists()

    def test_archive_skips_missing_file(self, tmp_path):
        """archive_task silently does nothing if source_path doesn't exist."""
        from cram.session import archive_task

        ctx = tmp_path / '.ai-context'
        ctx.mkdir()

        archive_task(str(tmp_path), str(tmp_path / 'nonexistent.md'))
        # No TASK_HISTORY.jsonl should be created
        assert not (ctx / 'TASK_HISTORY.jsonl').exists()


# ---------------------------------------------------------------------------
# B3: MCP guard — tools return actionable message before cram init
# ---------------------------------------------------------------------------

class TestInitGuard:
    def test_get_context_before_init_returns_init_message(self, tmp_path, monkeypatch):
        """get_context() returns an actionable message when .ai-context/ doesn't exist."""
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(tmp_path))
        # No .ai-context/ directory

        result = srv.get_context()
        assert 'cram init' in result or 'init' in result.lower()

    def test_get_context_task_before_init_returns_init_message(self, tmp_path, monkeypatch):
        """get_context(task) returns an actionable message when .ai-context/ doesn't exist."""
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(tmp_path))

        result = srv.get_context('fix the parser')
        assert 'cram init' in result or 'init' in result.lower()

    def test_get_health_before_init_returns_init_message(self, tmp_path, monkeypatch):
        """get_health() returns an actionable message when .ai-context/ doesn't exist."""
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(tmp_path))

        result = srv.get_health()
        assert 'cram init' in result or 'init' in result.lower()

    def test_get_architecture_before_init_returns_guidance(self, tmp_path, monkeypatch):
        """get_architecture() returns actionable guidance when .ai-context/ doesn't exist."""
        import cram.mcp_server as srv
        monkeypatch.setattr(srv, '_repo_root', str(tmp_path))

        result = srv.get_architecture()
        assert 'cram init' in result or 'not found' in result.lower()

    def test_get_health_error_returns_diagnostic(self, tmp_path, monkeypatch):
        """get_health() returns a diagnostic string rather than silently failing."""
        import cram.mcp_server as srv
        import cram.health as health_mod
        monkeypatch.setattr(srv, '_repo_root', str(tmp_path))
        # Create .ai-context/ so the init guard passes
        (tmp_path / CONTEXT_DIR).mkdir()

        def _boom(root):
            raise RuntimeError('disk error')
        monkeypatch.setattr(health_mod, 'context_health', _boom)

        result = srv.get_health()
        assert 'Error' in result or 'error' in result.lower()
        assert 'disk error' in result or 'doctor' in result
