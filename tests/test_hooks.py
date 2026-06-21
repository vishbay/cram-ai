"""Tests for cram/hooks.py — git hook installation and commit-msg pattern detection."""

from __future__ import annotations
import os
import subprocess


from cram.hooks import (
    COMMIT_MSG_HOOK_SCRIPT,
    install_hook,
    install_commit_msg_hook,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_git_repo(tmp_path):
    """Return a minimal git repo at tmp_path."""
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    return tmp_path


# ---------------------------------------------------------------------------
# install_commit_msg_hook
# ---------------------------------------------------------------------------

class TestInstallCommitMsgHook:
    def test_creates_commit_msg_hook(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        result = install_commit_msg_hook(str(repo))
        hook_path = repo / '.git' / 'hooks' / 'commit-msg'
        assert hook_path.exists()
        assert result is True

    def test_hook_is_executable(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        install_commit_msg_hook(str(repo))
        hook_path = repo / '.git' / 'hooks' / 'commit-msg'
        assert os.access(str(hook_path), os.X_OK)

    def test_hook_contains_cram_marker(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        install_commit_msg_hook(str(repo))
        hook_path = repo / '.git' / 'hooks' / 'commit-msg'
        content = hook_path.read_text()
        assert 'cram-ai' in content

    def test_skips_if_already_installed(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        first = install_commit_msg_hook(str(repo))
        second = install_commit_msg_hook(str(repo))
        assert first is True
        assert second is False

    def test_no_git_dir_returns_false(self, tmp_path):
        result = install_commit_msg_hook(str(tmp_path))
        assert result is False


# ---------------------------------------------------------------------------
# install_hook installs both hooks
# ---------------------------------------------------------------------------

class TestInstallHookInstallsBoth:
    def test_installs_post_commit(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        install_hook(str(repo))
        assert (repo / '.git' / 'hooks' / 'post-commit').exists()

    def test_installs_commit_msg(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        install_hook(str(repo))
        assert (repo / '.git' / 'hooks' / 'commit-msg').exists()

    def test_returns_true_when_any_installed(self, tmp_path):
        repo = _make_git_repo(tmp_path)
        result = install_hook(str(repo))
        assert result is True


# ---------------------------------------------------------------------------
# commit-msg hook pattern detection
# ---------------------------------------------------------------------------

class TestCommitMsgPatternDetection:
    """Run the actual shell script against various commit message bodies."""

    def _run_hook(self, tmp_path, msg: str) -> str:
        """Write a commit-msg hook, run it with the given message, return stdout."""
        hook = tmp_path / 'commit-msg'
        hook.write_text(COMMIT_MSG_HOOK_SCRIPT)
        hook.chmod(0o755)
        msg_file = tmp_path / 'COMMIT_EDITMSG'
        msg_file.write_text(msg)
        result = subprocess.run(
            ['sh', str(hook), str(msg_file)],
            capture_output=True, text=True,
        )
        return result.stdout + result.stderr

    def test_chose_triggers_suggestion(self, tmp_path):
        out = self._run_hook(tmp_path, 'refactor: chose sqlalchemy instead of raw SQL')
        assert 'cram' in out

    def test_instead_of_triggers_suggestion(self, tmp_path):
        out = self._run_hook(tmp_path, 'perf: use batch inserts instead of row-by-row')
        assert 'cram' in out

    def test_decided_triggers_suggestion(self, tmp_path):
        out = self._run_hook(tmp_path, 'chore: decided to drop Redis and use Postgres queues')
        assert 'cram' in out

    def test_rationale_triggers_suggestion(self, tmp_path):
        out = self._run_hook(tmp_path, 'docs: rationale for picking gRPC over REST')
        assert 'cram' in out

    def test_tradeoff_triggers_suggestion(self, tmp_path):
        out = self._run_hook(tmp_path, 'fix: trade-off between accuracy and speed here')
        assert 'cram' in out

    def test_plain_bugfix_does_not_trigger(self, tmp_path):
        out = self._run_hook(tmp_path, 'fix: correct off-by-one error in pagination')
        assert 'cram' not in out

    def test_feature_addition_does_not_trigger(self, tmp_path):
        out = self._run_hook(tmp_path, 'feat: add OAuth login endpoint')
        assert 'cram' not in out

    def test_hook_always_exits_zero(self, tmp_path):
        """Commit-msg hook must never block a commit."""
        hook = tmp_path / 'commit-msg'
        hook.write_text(COMMIT_MSG_HOOK_SCRIPT)
        hook.chmod(0o755)
        msg_file = tmp_path / 'COMMIT_EDITMSG'
        msg_file.write_text('chose X over Y')
        result = subprocess.run(
            ['sh', str(hook), str(msg_file)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
