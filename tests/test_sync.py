"""Tests for cram/sync_context.py — git diff, architecture update, sync flow."""

import subprocess
from unittest.mock import patch

import pytest

from cram.sync_context import get_git_diff, update_architecture_md, sync


# ---------------------------------------------------------------------------
# get_git_diff
# ---------------------------------------------------------------------------

class TestGetGitDiff:
    def test_returns_diff_from_head_minus_one(self):
        with patch('subprocess.check_output', return_value=b'diff output') as mock:
            result = get_git_diff()
        assert result == 'diff output'
        args = mock.call_args[0][0]
        assert 'HEAD~1' in args

    def test_falls_back_to_show_on_single_commit(self):
        responses = [
            subprocess.CalledProcessError(128, 'git'),
            b'initial commit',
        ]
        with patch('subprocess.check_output', side_effect=responses) as mock:
            result = get_git_diff()
        assert result == 'initial commit'
        # Second call should use 'show HEAD'
        second_call_args = mock.call_args_list[1][0][0]
        assert 'show' in second_call_args
        assert 'HEAD' in second_call_args

    def test_diff_decoded_to_string(self):
        with patch('subprocess.check_output', return_value=b'utf-8 diff \xe2\x80\x94'):
            result = get_git_diff()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# update_architecture_md
# ---------------------------------------------------------------------------

class TestUpdateArchitectureMd:
    def test_calls_model_with_all_three_inputs(self):
        with patch('cram.sync_context.call_context_model', return_value='# Updated') as mock:
            with patch('cram.sync_context.strip_code_fence', side_effect=lambda x: x):
                update_architecture_md('structure', 'diff', 'current')
        prompt = mock.call_args[0][0]
        assert 'structure' in prompt
        assert 'diff' in prompt
        assert 'current' in prompt

    def test_strips_code_fence_from_response(self):
        with patch('cram.sync_context.call_context_model', return_value='```\n# Arch\n```'):
            result = update_architecture_md('s', 'd', 'c')
        assert result == '# Arch'

    def test_returns_model_output(self):
        with patch('cram.sync_context.call_context_model', return_value='# New Arch'):
            with patch('cram.sync_context.strip_code_fence', side_effect=lambda x: x):
                result = update_architecture_md('s', 'd', 'c')
        assert result == '# New Arch'


# ---------------------------------------------------------------------------
# sync (integration)
# ---------------------------------------------------------------------------

class TestSync:
    def test_exits_if_no_context_dir(self, tmp_path):
        with pytest.raises(SystemExit):
            sync(str(tmp_path))

    def test_writes_updated_architecture(self, tmp_path):
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Old Arch')

        with patch('cram.sync_context.get_git_diff', return_value='diff text'):
            with patch('cram.sync_context.scan_structure', return_value='tree'):
                with patch('cram.sync_context.call_context_model', return_value='# New Arch'):
                    sync(str(tmp_path))

        content = (ctx / 'ARCHITECTURE.md').read_text()
        assert '# New Arch' in content

    def test_reads_existing_architecture_as_context(self, tmp_path):
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Existing Context')

        with patch('cram.sync_context.get_git_diff', return_value='diff'):
            with patch('cram.sync_context.scan_structure', return_value='tree'):
                with patch('cram.sync_context.call_context_model', return_value='# Updated') as mock:
                    sync(str(tmp_path))

        prompt = mock.call_args[0][0]
        assert '# Existing Context' in prompt

    def test_handles_missing_architecture_gracefully(self, tmp_path):
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        # No ARCHITECTURE.md — should not crash

        with patch('cram.sync_context.get_git_diff', return_value='diff'):
            with patch('cram.sync_context.scan_structure', return_value='tree'):
                with patch('cram.sync_context.call_context_model', return_value='# Fresh'):
                    sync(str(tmp_path))

        assert '# Fresh' in (ctx / 'ARCHITECTURE.md').read_text()

    def test_passes_structure_and_diff_to_model(self, tmp_path):
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('')

        with patch('cram.sync_context.get_git_diff', return_value='my diff'):
            with patch('cram.sync_context.scan_structure', return_value='my tree'):
                with patch('cram.sync_context.call_context_model', return_value='# ok') as mock:
                    sync(str(tmp_path))

        prompt = mock.call_args[0][0]
        assert 'my diff' in prompt
        assert 'my tree' in prompt

    def test_embeds_structure_hash_after_regen(self, tmp_path):
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Old Arch')

        with patch('cram.sync_context.get_git_diff', return_value='diff'):
            with patch('cram.sync_context.scan_structure', return_value='tree'):
                with patch('cram.sync_context.call_context_model', return_value='# New Arch'):
                    sync(str(tmp_path))

        content = (ctx / 'ARCHITECTURE.md').read_text()
        assert '# New Arch' in content
        assert 'cram:structure-hash' in content

    def test_skips_regen_when_structure_unchanged(self, tmp_path):
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Old Arch')

        # First sync regens and embeds the hash for structure 'tree'.
        with patch('cram.sync_context.get_git_diff', return_value='diff'):
            with patch('cram.sync_context.scan_structure', return_value='tree'):
                with patch('cram.sync_context.call_context_model', return_value='# Regen One'):
                    sync(str(tmp_path))
        first = (ctx / 'ARCHITECTURE.md').read_text()

        # Second sync with identical structure must NOT call the model, and the
        # file must be left byte-for-byte unchanged (no churn).
        with patch('cram.sync_context.get_git_diff', return_value='diff'):
            with patch('cram.sync_context.scan_structure', return_value='tree'):
                with patch('cram.sync_context.call_context_model',
                           return_value='# Regen Two') as mock:
                    sync(str(tmp_path))

        mock.assert_not_called()
        assert (ctx / 'ARCHITECTURE.md').read_text() == first

    def test_regens_when_structure_changes(self, tmp_path):
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Old Arch')

        with patch('cram.sync_context.get_git_diff', return_value='diff'):
            with patch('cram.sync_context.scan_structure', return_value='tree-v1'):
                with patch('cram.sync_context.call_context_model', return_value='# V1'):
                    sync(str(tmp_path))

        with patch('cram.sync_context.get_git_diff', return_value='diff'):
            with patch('cram.sync_context.scan_structure', return_value='tree-v2'):
                with patch('cram.sync_context.call_context_model',
                           return_value='# V2') as mock:
                    sync(str(tmp_path))

        mock.assert_called_once()
        assert '# V2' in (ctx / 'ARCHITECTURE.md').read_text()
