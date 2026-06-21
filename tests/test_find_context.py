"""Tests for cram/find_context.py — path cleaning, file inlining, CLI flow."""

from unittest.mock import patch

import pytest

from cram.find_context import (
    _clean_path,
    _read_truncated,
    _resolve_path,
    _score_files,
    find_relevant_files,
    populate_current_task,
    find_context,
)


# ---------------------------------------------------------------------------
# _clean_path
# ---------------------------------------------------------------------------

class TestCleanPath:
    def test_strips_dash_bullet(self):
        assert _clean_path('- src/main.py') == 'src/main.py'

    def test_strips_asterisk_bullet(self):
        assert _clean_path('* src/main.py') == 'src/main.py'

    def test_strips_numbered_list(self):
        assert _clean_path('1. src/main.py') == 'src/main.py'

    def test_strips_backticks(self):
        assert _clean_path('`src/main.py`') == 'src/main.py'

    def test_strips_whitespace(self):
        assert _clean_path('  src/main.py  ') == 'src/main.py'

    def test_rejects_prose_with_spaces(self):
        assert _clean_path('Based on the architecture:') == ''
        assert _clean_path('Here are the relevant files') == ''

    def test_rejects_bare_word_without_separator(self):
        # 'README' has no '/' or '.' — ambiguous, rejected
        assert _clean_path('README') == ''

    def test_accepts_path_with_dot_only(self):
        assert _clean_path('README.md') == 'README.md'

    def test_accepts_path_with_slash_only(self):
        assert _clean_path('src/file') == 'src/file'

    def test_accepts_nested_path(self):
        assert _clean_path('cram/utils.py') == 'cram/utils.py'


# ---------------------------------------------------------------------------
# _read_truncated
# ---------------------------------------------------------------------------

class TestReadTruncated:
    def test_returns_full_content_under_limit(self, tmp_path):
        f = tmp_path / 'small.py'
        f.write_text('line1\nline2\nline3\n')
        result = _read_truncated(str(f))
        assert 'line1' in result
        assert 'line3' in result
        assert 'omitted' not in result

    def test_truncates_and_adds_marker(self, tmp_path, monkeypatch):
        import cram.find_context as fc
        monkeypatch.setattr(fc, 'MAX_LINES', 2)
        f = tmp_path / 'big.py'
        f.write_text('\n'.join(f'line{i}' for i in range(10)))
        result = _read_truncated(str(f))
        assert 'line0' in result
        assert 'line1' in result
        assert 'line9' not in result
        assert 'omitted' in result


# ---------------------------------------------------------------------------
# find_relevant_files
# ---------------------------------------------------------------------------

class TestFindRelevantFiles:
    def test_returns_cleaned_file_paths(self):
        mock_response = 'src/main.py\ncram/utils.py\n'
        with patch('cram.find_context.call_context_model', return_value=mock_response):
            result = find_relevant_files('add feature', '# Arch', '# Decisions')
        paths = [f for f, _ in result]
        assert 'src/main.py' in paths
        assert 'cram/utils.py' in paths

    def test_strips_prose_from_response(self):
        mock_response = (
            'Here are the relevant files:\n'
            'src/main.py\n'
            '- cram/utils.py\n'
        )
        with patch('cram.find_context.call_context_model', return_value=mock_response):
            result = find_relevant_files('add feature', '', '')
        paths = [f for f, _ in result]
        assert 'src/main.py' in paths
        assert 'cram/utils.py' in paths
        assert any('Here' in p for p in paths) is False

    def test_respects_max_files_limit(self, monkeypatch):
        import cram.find_context as fc
        monkeypatch.setattr(fc, 'MAX_FILES', 2)
        mock_response = 'a/b.py\nc/d.py\ne/f.py\ng/h.py'
        with patch('cram.find_context.call_context_model', return_value=mock_response):
            result = find_relevant_files('task', '', '')
        assert len(result) <= 2

    def test_passes_task_and_arch_to_model(self):
        with patch('cram.find_context.call_context_model', return_value='src/a.py') as mock_call:
            find_relevant_files('my task', '# Arch content', '# Dec content')
        prompt = mock_call.call_args[0][0]
        assert 'my task' in prompt
        assert '# Arch content' in prompt

    def test_decisions_excluded_from_selection_prompt(self):
        # DECISIONS don't help pick files — keep them out of the selection prompt
        decisions = 'use postgres for all persistence'
        with patch('cram.find_context.call_context_model', return_value='src/a.py') as mock_call:
            find_relevant_files('add auth', '# Arch', decisions, symbols='auth.py: login, logout')
        prompt = mock_call.call_args[0][0]
        assert decisions not in prompt

    def test_scored_candidates_appear_as_hint_in_prompt(self):
        symbols = 'cram/find_context.py: find_relevant_files, populate_current_task\n'
        with patch('cram.find_context.call_context_model', return_value='cram/find_context.py') as mock_call:
            find_relevant_files('fix find context pipeline', '# Arch', '', symbols=symbols)
        prompt = mock_call.call_args[0][0]
        assert 'Top candidates' in prompt
        assert 'cram/find_context.py' in prompt

    def test_full_index_used_when_no_keyword_matches(self):
        symbols = 'utils.py: parse, format\nmodels.py: User, Post\n'
        with patch('cram.find_context.call_context_model', return_value='utils.py') as mock_call:
            # Task keywords ('zzz') won't match anything
            find_relevant_files('zzz yyy xxx', '# Arch', '', symbols=symbols)
        prompt = mock_call.call_args[0][0]
        assert 'Symbol index' in prompt
        assert 'utils.py' in prompt

    def test_uses_call_context_model_not_call_model(self):
        """A2: find_relevant_files must invoke call_context_model, not call_model."""
        with patch('cram.find_context.call_context_model', return_value='src/a.py') as ctx_mock, \
             patch('cram.find_context.call_model') as plain_mock:
            find_relevant_files('any task', '# Arch', '')
        assert ctx_mock.called, 'call_context_model should have been called'
        assert not plain_mock.called, 'call_model should NOT be called by find_relevant_files'


# ---------------------------------------------------------------------------
# populate_current_task
# ---------------------------------------------------------------------------

class TestPopulateCurrentTask:
    def test_inlines_existing_file_content(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        src = tmp_path / 'utils.py'
        src.write_text('def helper(): pass\n')

        found = populate_current_task('fix bug', ['utils.py'])

        assert found == ['utils.py']
        content = (ctx / 'CURRENT_TASK.md').read_text()
        assert 'fix bug' in content
        assert 'def helper(): pass' in content

    def test_output_path_writes_to_custom_location(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        src = tmp_path / 'utils.py'
        src.write_text('def helper(): pass\n')
        custom = tmp_path / '.ai-context' / 'tasks' / 'my-slot.md'

        populate_current_task('fix bug', ['utils.py'], output_path=str(custom))

        assert custom.exists()
        assert 'fix bug' in custom.read_text()
        assert 'def helper' in custom.read_text()
        # Default CURRENT_TASK.md should NOT be written
        assert not (ctx / 'CURRENT_TASK.md').exists()

    def test_output_path_creates_parent_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / '.ai-context').mkdir()
        (tmp_path / 'main.py').write_text('# main\n')
        deep = tmp_path / 'deep' / 'nested' / 'slot.md'

        populate_current_task('task', ['main.py'], output_path=str(deep))

        assert deep.exists()

    def test_notes_missing_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / '.ai-context').mkdir()

        found = populate_current_task('task', ['ghost.py'])

        assert found == []
        content = (tmp_path / '.ai-context' / 'CURRENT_TASK.md').read_text()
        assert 'ghost.py' in content
        assert 'not found' in content

    def test_uses_correct_code_fence_for_extension(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / '.ai-context').mkdir()
        (tmp_path / 'app.ts').write_text('const x = 1;\n')

        populate_current_task('task', ['app.ts'])

        content = (tmp_path / '.ai-context' / 'CURRENT_TASK.md').read_text()
        assert '```ts' in content

    def test_handles_mixed_existing_and_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / '.ai-context').mkdir()
        (tmp_path / 'real.py').write_text('# real\n')

        found = populate_current_task('task', ['real.py', 'fake.py'])

        assert 'real.py' in found
        assert 'fake.py' not in found


# ---------------------------------------------------------------------------
# _score_files
# ---------------------------------------------------------------------------

class TestScoreFiles:
    SYMBOLS = (
        'cram/find_context.py: find_relevant_files, populate_current_task, find_context\n'
        'cram/mcp_server.py: get_context, get_symbols, get_decisions\n'
        'cram/utils.py: call_model, find_git_root\n'
        'tests/test_find_context.py: TestScoreFiles, TestCleanPath\n'
    )

    def test_filename_match_scores_higher_than_symbol_only(self):
        scored = _score_files('fix find context pipeline', self.SYMBOLS)
        paths = [p for p, *_ in scored]
        # 'find' and 'context' are in the filename stem → highest score
        assert paths[0] == 'cram/find_context.py'

    def test_symbol_match_returns_nonzero_score(self):
        scored = _score_files('call the model', self.SYMBOLS)
        paths = [p for p, *_ in scored]
        assert 'cram/utils.py' in paths

    def test_no_matching_keywords_returns_empty(self):
        result = _score_files('zzz yyy xxx', self.SYMBOLS)
        assert result == []

    def test_stop_words_ignored(self):
        # 'fix', 'add', 'the' are all stop words — no match on those alone
        result = _score_files('fix the add', self.SYMBOLS)
        assert result == []

    def test_short_words_ignored(self):
        result = _score_files('do it', self.SYMBOLS)
        assert result == []

    def test_returns_sorted_descending_by_score(self):
        scored = _score_files('find context symbols', self.SYMBOLS)
        scores = [sc for _, sc, _ in scored]
        assert scores == sorted(scores, reverse=True)

    def test_symbols_included_in_result(self):
        scored = _score_files('get context', self.SYMBOLS)
        by_path = {p: ss for p, _, ss in scored}
        assert 'get_context' in by_path.get('cram/mcp_server.py', [])

    def test_directory_component_match(self):
        symbols = 'auth/middleware.py: check_token, validate\n'
        scored = _score_files('auth token validation', symbols)
        # 'auth' matches directory component
        assert len(scored) == 1
        assert scored[0][0] == 'auth/middleware.py'
        assert scored[0][1] >= 1.5


# ---------------------------------------------------------------------------
# populate_current_task — contract fields
# ---------------------------------------------------------------------------

class TestContractFields:
    def test_scope_derived_from_found_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / '.ai-context').mkdir()
        (tmp_path / 'cram').mkdir()
        (tmp_path / 'cram' / 'utils.py').write_text('def helper(): pass\n')

        populate_current_task('refactor helpers', ['cram/utils.py'])

        content = (tmp_path / '.ai-context' / 'CURRENT_TASK.md').read_text()
        assert '## Scope' in content
        assert '- cram/' in content

    def test_out_of_scope_placeholder_present(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / '.ai-context').mkdir()
        (tmp_path / 'main.py').write_text('# main\n')

        populate_current_task('fix main', ['main.py'])

        content = (tmp_path / '.ai-context' / 'CURRENT_TASK.md').read_text()
        assert '## Out of Scope' in content
        assert '<!--' in content

    def test_definition_of_done_placeholder_present(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / '.ai-context').mkdir()
        (tmp_path / 'main.py').write_text('# main\n')

        populate_current_task('fix main', ['main.py'])

        content = (tmp_path / '.ai-context' / 'CURRENT_TASK.md').read_text()
        assert '## Definition of Done' in content

    def test_scope_repo_root_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / '.ai-context').mkdir()
        (tmp_path / 'main.py').write_text('# main\n')

        populate_current_task('fix main', ['main.py'])

        content = (tmp_path / '.ai-context' / 'CURRENT_TASK.md').read_text()
        assert '## Scope' in content
        assert '- .' in content  # repo root shown as '.'

    def test_scope_empty_when_no_files_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / '.ai-context').mkdir()

        populate_current_task('task', ['ghost.py'])  # file doesn't exist

        content = (tmp_path / '.ai-context' / 'CURRENT_TASK.md').read_text()
        assert '## Scope' in content
        # No dirs from missing files — shows placeholder
        assert 'Populated after' in content or '## Out of Scope' in content

    def test_contract_sections_before_relevant_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / '.ai-context').mkdir()
        (tmp_path / 'app.py').write_text('# app\n')

        populate_current_task('fix app', ['app.py'])

        content = (tmp_path / '.ai-context' / 'CURRENT_TASK.md').read_text()
        scope_pos = content.index('## Scope')
        files_pos = content.index('## Relevant Files')
        assert scope_pos < files_pos


# ---------------------------------------------------------------------------
# find_context (integration)
# ---------------------------------------------------------------------------

class TestFindContext:
    def test_exits_if_no_context_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            find_context('some task')

    def test_warns_if_architecture_missing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / '.ai-context').mkdir()
        # No ARCHITECTURE.md created — should warn but not crash

        with patch('cram.find_context.call_context_model', return_value=''):
            find_context('some task')

        assert 'Warning' in capsys.readouterr().err

    def test_full_flow_with_mocked_model(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Arch')
        (ctx / 'DECISIONS.md').write_text('# Dec')
        (tmp_path / 'main.py').write_text('print("hello")\n')

        with patch('cram.find_context.call_context_model', return_value='main.py'):
            find_context('fix the print')

        content = (ctx / 'CURRENT_TASK.md').read_text()
        assert 'fix the print' in content
        assert 'print("hello")' in content


# ---------------------------------------------------------------------------
# A5: chdir-free extraction — excerpts work from any cwd
# ---------------------------------------------------------------------------

class TestChdirFreeExtraction:
    def test_get_context_works_from_different_cwd(self, tmp_path, monkeypatch):
        """A5: populate_current_task resolves files via root, not cwd."""
        import cram.mcp_server as srv
        from unittest.mock import patch as _patch

        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Arch\n')
        (ctx / 'DECISIONS.md').write_text('# Dec\n')
        (ctx / 'GOTCHAS.md').write_text('# Got\n')
        (ctx / 'SYMBOLS.md').write_text('main.py: main\n')
        (tmp_path / 'main.py').write_text('def main(): pass\n')

        monkeypatch.setattr(srv, '_repo_root', str(tmp_path))
        # Deliberately change to a completely different directory
        monkeypatch.chdir('/')

        entries = [('main.py', ['main'])]
        with _patch('cram.find_context.find_relevant_files', return_value=entries):
            result = srv.get_context('test task from root cwd')

        assert 'test task from root cwd' in result
        assert 'main' in result  # excerpt content made it in

    def test_populate_current_task_uses_root_kwarg(self, tmp_path):
        """populate_current_task(root=X) finds files without os.chdir."""
        import cram.find_context as fc

        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (tmp_path / 'utils.py').write_text('def parse(): pass\ndef fmt(): pass\n')
        out = tmp_path / '.ai-context' / 'CURRENT_TASK.md'

        fc.populate_current_task(
            'test task',
            [('utils.py', ['parse'])],
            output_path=str(out),
            root=str(tmp_path),
        )

        content = out.read_text()
        assert 'test task' in content
        assert 'utils.py' in content
        assert 'parse' in content


# ---------------------------------------------------------------------------
# A6: _resolve_path basename disambiguation
# ---------------------------------------------------------------------------

class TestResolvePathDisambiguation:
    def test_single_basename_match_wins(self, tmp_path):
        """A6: single match by basename is returned even if directory differs."""
        sub = tmp_path / 'sub'
        sub.mkdir()
        (sub / 'utils.py').write_text('pass')

        result = _resolve_path('utils.py', str(tmp_path))
        assert result == 'sub/utils.py'

    def test_closer_dir_match_preferred(self, tmp_path):
        """A6: with multiple basename matches, prefer the one whose dir parts overlap the raw path."""
        # Create two utils.py — one in auth/, one in payments/
        (tmp_path / 'auth').mkdir()
        (tmp_path / 'auth' / 'utils.py').write_text('pass')
        (tmp_path / 'payments').mkdir()
        (tmp_path / 'payments' / 'utils.py').write_text('pass')

        # raw path hint contains 'auth' — should prefer auth/utils.py
        result = _resolve_path('auth/utils.py', str(tmp_path))
        assert result == 'auth/utils.py'

    def test_fully_ambiguous_returns_raw(self, tmp_path):
        """A6: if still ambiguous after dir-overlap scoring, return raw path unresolved."""
        # Two utils.py with NO directory hint in the raw path
        (tmp_path / 'a').mkdir()
        (tmp_path / 'a' / 'utils.py').write_text('pass')
        (tmp_path / 'b').mkdir()
        (tmp_path / 'b' / 'utils.py').write_text('pass')

        result = _resolve_path('utils.py', str(tmp_path))
        # Both a/utils.py and b/utils.py have equal overlap (zero) with bare 'utils.py'
        # — should return the raw string rather than silently picking one
        assert result == 'utils.py'


    def test_root_param_scopes_path_resolution(self, tmp_path, monkeypatch):
        # Files only exist inside tmp_path; resolving with root=str(tmp_path) should find them
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Arch')
        (tmp_path / 'util.py').write_text('def helper(): pass\n')

        # Without chdir — use explicit root to resolve
        with patch('cram.find_context.call_context_model', return_value='util.py'):
            result = find_relevant_files('fix helper', '# Arch', root=str(tmp_path))

        paths = [f for f, _ in result]
        assert 'util.py' in paths

    def test_legacy_context_dir_still_works(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ctx = tmp_path / '.cram-ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Arch')
        (ctx / 'DECISIONS.md').write_text('# Dec')
        (tmp_path / 'main.py').write_text('print("hello")\n')

        with patch('cram.find_context.call_context_model', return_value='main.py'):
            find_context('fix the print')

        content = (ctx / 'CURRENT_TASK.md').read_text()
        assert 'fix the print' in content
        assert 'print("hello")' in content
