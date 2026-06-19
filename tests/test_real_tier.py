"""Real-repo tier: git-sourced fixtures (clone @ ref) + overlay oracle + env.
Uses a local git repo as the 'remote' so there's no network in tests."""

from __future__ import annotations
import os
import subprocess
import sys

from cram import rig


def _git(*args, cwd):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(path) -> str:
    """A tiny local git repo with one commit; returns its SHA."""
    path.mkdir()
    (path / 'src').mkdir()
    (path / 'src' / 'mod.py').write_text('VALUE = "from-repo"\n')
    _git('init', '-q', cwd=path)
    _git('add', '-A', cwd=path)
    subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t',
                    'commit', '-q', '-m', 'init'], cwd=path, check=True,
                   capture_output=True)
    out = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=path, check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def test_from_dict_parses_repo_fields():
    t = rig.Task.from_dict({'id': 'a', 'prompt': 'p', 'repo': 'u', 'ref': 'sha',
                            'overlay': 'o', 'env': {'PYTHONPATH': 'src'}})
    assert (t.repo, t.ref, t.overlay) == ('u', 'sha', 'o')
    assert t.env == {'PYTHONPATH': 'src'}


def test_prepare_workdir_clones_repo_and_applies_overlay(tmp_path, monkeypatch):
    monkeypatch.setattr(rig, '_REPO_CACHE', {})        # isolate the module cache
    remote = tmp_path / 'remote'
    sha = _make_repo(remote)
    overlay = tmp_path / 'overlay'
    overlay.mkdir()
    (overlay / 'oracle_test.py').write_text('def test_ok():\n    assert True\n')

    task = rig.Task(id='t', prompt='p', repo=str(remote), ref=sha,
                    overlay=str(overlay))
    wd = rig._prepare_workdir(task, str(tmp_path / 'wd'))

    assert os.path.isdir(os.path.join(wd, '.git'))            # cloned repo
    assert os.path.exists(os.path.join(wd, 'src', 'mod.py'))  # repo content
    assert os.path.exists(os.path.join(wd, 'oracle_test.py')) # overlay applied


def test_command_oracle_applies_task_env(tmp_path):
    # check passes only when the task's env var is visible to the check process.
    task = rig.Task(id='t', prompt='p',
                    check=[sys.executable, '-c',
                           'import os,sys; sys.exit(0 if os.environ.get("CRAM_X")=="1" else 1)'],
                    env={'CRAM_X': '1'})
    assert rig.CommandOracle().score(task, str(tmp_path)) is True
    task.env = {}
    assert rig.CommandOracle().score(task, str(tmp_path)) is False


def test_real_corpus_loads_and_resolves_overlay():
    corpus = os.path.join(os.path.dirname(__file__), '..', 'examples', 'rig',
                          'bench', 'corpus.real.json')
    tasks = rig.load_corpus(corpus)
    t = tasks[0]
    assert t.repo and t.ref and t.tier == 'real'
    assert os.path.isdir(t.overlay)                          # resolved abs path
    assert os.path.exists(os.path.join(t.overlay, 'oracle_test.py'))
    assert t.env.get('PYTHONPATH') == 'src'
