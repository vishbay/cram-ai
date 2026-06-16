"""Tests for cram.status — staleness_score, staleness_band, get_status_dict."""

import os
import subprocess
import pytest

from cram.status import staleness_score, staleness_band, get_status_dict, CONTEXT_DIR


# ---------------------------------------------------------------------------
# staleness_score
# ---------------------------------------------------------------------------

class TestStalenessScore:
    def test_zero_commits_is_zero(self):
        assert staleness_score(0, False) == 0

    def test_five_commits_is_five(self):
        assert staleness_score(5, False) == 5

    def test_ten_commits_is_ten(self):
        assert staleness_score(10, False) == 10

    def test_capped_at_ten(self):
        assert staleness_score(99, False) == 10
        assert staleness_score(100, False) == 10

    def test_none_behind_returns_six(self):
        assert staleness_score(None, True) == 6

    def test_none_not_behind_returns_zero(self):
        assert staleness_score(None, False) == 0

    def test_env_override_critical_threshold(self, monkeypatch):
        import cram.status as s
        monkeypatch.setattr(s, 'STALE_CRITICAL_COMMITS', 20)
        # 10 commits out of 20 → score 5
        assert staleness_score(10, False) == 5

    def test_never_below_zero(self):
        assert staleness_score(0, False) == 0


# ---------------------------------------------------------------------------
# staleness_band
# ---------------------------------------------------------------------------

class TestStalenessBand:
    def test_boundary_2_fresh(self):
        assert staleness_band(2) == 'fresh'

    def test_boundary_3_acceptable(self):
        assert staleness_band(3) == 'acceptable'

    def test_boundary_5_acceptable(self):
        assert staleness_band(5) == 'acceptable'

    def test_boundary_6_stale(self):
        assert staleness_band(6) == 'stale'

    def test_boundary_7_stale(self):
        assert staleness_band(7) == 'stale'

    def test_boundary_8_critical(self):
        assert staleness_band(8) == 'critical'

    def test_boundary_10_critical(self):
        assert staleness_band(10) == 'critical'

    def test_zero_fresh(self):
        assert staleness_band(0) == 'fresh'


# ---------------------------------------------------------------------------
# get_status_dict — back-compat + new fields
# ---------------------------------------------------------------------------

class TestGetStatusDictBackCompat:
    def test_state_is_stale_or_fresh_string(self, tmp_path, monkeypatch):
        ctx = tmp_path / CONTEXT_DIR
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Arch\n')
        monkeypatch.chdir(tmp_path)

        result = get_status_dict(str(tmp_path))
        assert result['state'] in ('stale', 'fresh', 'not-init')

    def test_state_matches_band_mapping(self, tmp_path, monkeypatch):
        ctx = tmp_path / CONTEXT_DIR
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Arch\n')
        monkeypatch.chdir(tmp_path)

        result = get_status_dict(str(tmp_path))
        band  = result['staleness_band']
        state = result['state']
        if band in ('stale', 'critical'):
            assert state == 'stale'
        else:
            assert state == 'fresh'

    def test_new_fields_present(self, tmp_path, monkeypatch):
        ctx = tmp_path / CONTEXT_DIR
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Arch\n')
        monkeypatch.chdir(tmp_path)

        result = get_status_dict(str(tmp_path))
        assert 'staleness_score' in result
        assert 'staleness_band' in result
        assert 'commits_since_sync' in result

    def test_not_init_returns_not_init(self, tmp_path):
        result = get_status_dict(str(tmp_path))
        assert result['state'] == 'not-init'

    def test_legacy_context_dir_counts_as_initialized(self, tmp_path):
        ctx = tmp_path / '.cram-ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('# Arch\n')

        result = get_status_dict(str(tmp_path))
        assert result['state'] in ('stale', 'fresh')
        assert 'ARCHITECTURE.md' in result['files']


# ---------------------------------------------------------------------------
# Integration: real git repo with N commits after ARCHITECTURE.md
# ---------------------------------------------------------------------------

def _git(*args, cwd):
    subprocess.check_call(['git'] + list(args), cwd=str(cwd),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture()
def git_repo(tmp_path):
    """A real git repo with ARCHITECTURE.md committed."""
    _git('init', cwd=tmp_path)
    _git('config', 'user.email', 'test@test.com', cwd=tmp_path)
    _git('config', 'user.name', 'Test', cwd=tmp_path)

    ctx = tmp_path / CONTEXT_DIR
    ctx.mkdir()
    (ctx / 'ARCHITECTURE.md').write_text('# Arch\n')
    _git('add', '.', cwd=tmp_path)
    _git('commit', '-m', 'init', cwd=tmp_path)
    return tmp_path


def _add_empty_commit(repo, n=1):
    for i in range(n):
        (repo / f'dummy_{i}.txt').write_text('x')
        _git('add', '.', cwd=repo)
        _git('commit', '-m', f'empty {i}', cwd=repo)


@pytest.mark.skipif(
    not bool(subprocess.run(['git', '--version'], capture_output=True).returncode == 0),
    reason='git not available',
)
class TestGetStatusDictIntegration:
    def test_zero_commits_since_sync(self, git_repo):
        result = get_status_dict(str(git_repo))
        assert result['commits_since_sync'] == 0
        assert result['staleness_band'] == 'fresh'

    def test_n_commits_since_sync(self, git_repo):
        _add_empty_commit(git_repo, 3)
        result = get_status_dict(str(git_repo))
        assert result['commits_since_sync'] == 3

    def test_band_matches_commit_count(self, git_repo):
        _add_empty_commit(git_repo, 7)
        result = get_status_dict(str(git_repo))
        band = result['staleness_band']
        score = result['staleness_score']
        assert band == staleness_band(score)
        assert result['state'] == ('stale' if band in ('stale', 'critical') else 'fresh')


# ---------------------------------------------------------------------------
# Structure-fingerprint freshness (#24 interaction): a matching embedded
# structure hash means sync would skip the regen, so the file is fresh by
# design even when non-structural commits have landed since.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not bool(subprocess.run(['git', '--version'], capture_output=True).returncode == 0),
    reason='git not available',
)
class TestStructureHashFreshness:
    def _sync_arch_with_hash(self, repo):
        """Embed the current structure fingerprint into ARCHITECTURE.md and commit."""
        from cram.init import scan_structure
        from cram.sync_context import _structure_hash, _with_structure_hash
        arch = repo / CONTEXT_DIR / 'ARCHITECTURE.md'
        h = _structure_hash(scan_structure(str(repo)))
        arch.write_text(_with_structure_hash('# Arch\n', h))
        _git('add', '.', cwd=repo)
        _git('commit', '-m', 'sync arch', cwd=repo)

    def test_matching_hash_suppresses_staleness_after_content_commits(self, git_repo):
        # A source file whose *content* we'll churn without changing the tree.
        (git_repo / 'mod.py').write_text('x = 0\n')
        _git('add', '.', cwd=git_repo)
        _git('commit', '-m', 'add mod', cwd=git_repo)
        self._sync_arch_with_hash(git_repo)

        # Content-only commits — repo structure is unchanged.
        for i in range(1, 4):
            (git_repo / 'mod.py').write_text(f'x = {i}\n')
            _git('add', '.', cwd=git_repo)
            _git('commit', '-m', f'edit {i}', cwd=git_repo)

        result = get_status_dict(str(git_repo))
        assert result['commits_since_sync'] == 0
        assert result['staleness_band'] == 'fresh'

    def test_structure_change_still_counts(self, git_repo):
        self._sync_arch_with_hash(git_repo)
        # New files change the tree → embedded hash no longer matches → counts.
        _add_empty_commit(git_repo, 2)
        result = get_status_dict(str(git_repo))
        assert result['commits_since_sync'] >= 1

    def test_no_embedded_hash_falls_back_to_commit_count(self, git_repo):
        # git_repo's ARCHITECTURE.md has no structure marker (pre-#24 file).
        _add_empty_commit(git_repo, 3)
        result = get_status_dict(str(git_repo))
        assert result['commits_since_sync'] == 3

    def test_status_line_labels_staleness_not_health(self, git_repo, capsys):
        # 0 = fresh; the line must read "Staleness : 0/10 (fresh)" so a low
        # score isn't misread as bad "health".
        from cram.status import show_status
        show_status(str(git_repo))
        out = capsys.readouterr().out
        assert 'Staleness : 0/10 (fresh)' in out
        assert 'Context health' not in out
