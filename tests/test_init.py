"""Tests for cram/init.py — file exclusions, tree scanning, repo setup."""

from unittest.mock import patch


from cram.init import (
    _is_excluded_file,
    scan_structure,
    write_gitignore,
    init_repo,
    DECISIONS_TEMPLATE,
    CURRENT_TASK_TEMPLATE,
)


# ---------------------------------------------------------------------------
# _is_excluded_file
# ---------------------------------------------------------------------------

class TestIsExcludedFile:
    def test_excludes_package_lock(self):
        assert _is_excluded_file('package-lock.json') is True

    def test_excludes_yarn_lock(self):
        assert _is_excluded_file('yarn.lock') is True

    def test_excludes_poetry_lock(self):
        assert _is_excluded_file('poetry.lock') is True

    def test_excludes_min_js(self):
        assert _is_excluded_file('bundle.min.js') is True

    def test_excludes_min_css(self):
        assert _is_excluded_file('styles.min.css') is True

    def test_allows_regular_js(self):
        assert _is_excluded_file('app.js') is False

    def test_allows_python_file(self):
        assert _is_excluded_file('main.py') is False

    def test_allows_readme(self):
        assert _is_excluded_file('README.md') is False


# ---------------------------------------------------------------------------
# scan_structure
# ---------------------------------------------------------------------------

class TestScanStructure:
    def test_includes_regular_files(self, tmp_path):
        (tmp_path / 'main.py').write_text('')
        result = scan_structure(str(tmp_path))
        assert 'main.py' in result

    def test_excludes_node_modules(self, tmp_path):
        nm = tmp_path / 'node_modules' / 'lodash'
        nm.mkdir(parents=True)
        (nm / 'index.js').write_text('')
        result = scan_structure(str(tmp_path))
        # index.js must not appear — node_modules was not traversed
        assert 'index.js' not in result
        # node_modules must not appear as a directory entry (with trailing slash)
        assert 'node_modules/' not in result

    def test_excludes_git_dir(self, tmp_path):
        git = tmp_path / '.git'
        git.mkdir()
        (git / 'config').write_text('')
        result = scan_structure(str(tmp_path))
        assert '.git' not in result

    def test_excludes_pycache(self, tmp_path):
        pc = tmp_path / '__pycache__'
        pc.mkdir()
        (pc / 'foo.pyc').write_text('')
        result = scan_structure(str(tmp_path))
        assert '__pycache__' not in result

    def test_excludes_lock_files(self, tmp_path):
        (tmp_path / 'package-lock.json').write_text('')
        (tmp_path / 'src.py').write_text('')
        result = scan_structure(str(tmp_path))
        assert 'package-lock.json' not in result
        assert 'src.py' in result

    def test_excludes_min_files(self, tmp_path):
        (tmp_path / 'bundle.min.js').write_text('')
        (tmp_path / 'app.js').write_text('')
        result = scan_structure(str(tmp_path))
        assert 'bundle.min.js' not in result
        assert 'app.js' in result

    def test_nested_structure_indented(self, tmp_path):
        src = tmp_path / 'src'
        src.mkdir()
        (src / 'utils.py').write_text('')
        result = scan_structure(str(tmp_path))
        lines = result.splitlines()
        utils_line = next(l for l in lines if 'utils.py' in l)
        src_line = next(l for l in lines if 'src/' in l)
        # utils.py should be indented more than src/
        assert len(utils_line) - len(utils_line.lstrip()) > \
               len(src_line) - len(src_line.lstrip())

    def test_excludes_cram_dir(self, tmp_path):
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (ctx / 'ARCHITECTURE.md').write_text('')
        result = scan_structure(str(tmp_path))
        assert '.ai-context' not in result


# ---------------------------------------------------------------------------
# write_gitignore
# ---------------------------------------------------------------------------

class TestWriteGitignore:
    def test_creates_gitignore_with_current_task(self, tmp_path):
        write_gitignore(str(tmp_path))
        content = (tmp_path / '.gitignore').read_text()
        assert 'CURRENT_TASK.md' in content


# ---------------------------------------------------------------------------
# init_repo
# ---------------------------------------------------------------------------

class TestInitRepo:
    def test_creates_context_dir(self, tmp_path):
        with patch('cram.init.generate_architecture_md', return_value='# Arch'):
            init_repo(str(tmp_path))
        assert (tmp_path / '.ai-context').is_dir()

    def test_creates_all_required_files(self, tmp_path):
        with patch('cram.init.generate_architecture_md', return_value='# Arch'):
            init_repo(str(tmp_path))
        ctx = tmp_path / '.ai-context'
        assert (ctx / 'ARCHITECTURE.md').exists()
        assert (ctx / 'DECISIONS.md').exists()
        assert (ctx / 'CURRENT_TASK.md').exists()
        assert (ctx / '.gitignore').exists()

    def test_architecture_md_uses_generated_content(self, tmp_path):
        with patch('cram.init.generate_architecture_md', return_value='# Generated'):
            init_repo(str(tmp_path))
        content = (tmp_path / '.ai-context' / 'ARCHITECTURE.md').read_text()
        assert '# Generated' in content

    def test_decisions_md_uses_template(self, tmp_path):
        with patch('cram.init.generate_architecture_md', return_value='# Arch'):
            init_repo(str(tmp_path))
        content = (tmp_path / '.ai-context' / 'DECISIONS.md').read_text()
        assert content == DECISIONS_TEMPLATE

    def test_current_task_md_uses_template(self, tmp_path):
        with patch('cram.init.generate_architecture_md', return_value='# Arch'):
            init_repo(str(tmp_path))
        content = (tmp_path / '.ai-context' / 'CURRENT_TASK.md').read_text()
        assert content == CURRENT_TASK_TEMPLATE

    def test_skips_if_context_dir_exists(self, tmp_path, capsys):
        (tmp_path / '.ai-context').mkdir()
        with patch('cram.init.generate_architecture_md') as mock_gen:
            init_repo(str(tmp_path))
        mock_gen.assert_not_called()
        assert 'Skipping' in capsys.readouterr().out

    def test_does_not_call_model_when_skipping(self, tmp_path):
        (tmp_path / '.ai-context').mkdir()
        with patch('cram.utils.call_model') as mock_call:
            init_repo(str(tmp_path))
        mock_call.assert_not_called()
