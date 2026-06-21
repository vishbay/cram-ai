"""Generic CommandAdapter — lets cram rig referee any context-packer
(repomix, files-to-prompt, …) with no cram-specific integration."""

from __future__ import annotations
import sys

from cram import rig

PY = sys.executable


def test_presets_resolve_to_command_adapters():
    for name in ('repomix', 'files-to-prompt'):
        a = rig.get_provider(name)
        assert isinstance(a, rig.CommandAdapter)
        assert a.name == name


def test_availability_false_when_launcher_missing():
    a = rig.CommandAdapter('bogus', 'definitely-not-a-real-binary-xyz --go')
    assert a.availability().ok is False


def test_setup_writes_context_into_startup_file(tmp_path):
    a = rig.CommandAdapter('echo-ctx', [PY, '-c', "print('PACKED REPO CONTEXT')"],
                           target='claude', header='# ctx')
    out = a.setup(rig.Task('t', 'p'), str(tmp_path))
    assert out == {'CRAM_OPTIMIZER': 'echo-ctx'}
    body = (tmp_path / 'CLAUDE.md').read_text()
    assert '# ctx' in body and 'PACKED REPO CONTEXT' in body


def test_setup_targets_agents_md_for_codex(tmp_path):
    a = rig.CommandAdapter('x', [PY, '-c', "print('CTX')"], target='codex')
    a.setup(rig.Task('t', 'p'), str(tmp_path))
    assert (tmp_path / 'AGENTS.md').exists()
    assert not (tmp_path / 'CLAUDE.md').exists()


def test_setup_degrades_to_baseline_on_failure(tmp_path):
    a = rig.CommandAdapter('boom', [PY, '-c', "import sys; sys.exit(3)"])
    assert a.setup(rig.Task('t', 'p'), str(tmp_path)) == {}
    assert not (tmp_path / 'CLAUDE.md').exists()


def test_setup_degrades_when_output_empty(tmp_path):
    a = rig.CommandAdapter('empty', [PY, '-c', "pass"])
    assert a.setup(rig.Task('t', 'p'), str(tmp_path)) == {}


def test_configure_sets_target_from_runner():
    provs = rig._configure_providers_for_runner([rig.get_provider('repomix')], 'codex')
    assert provs[0].target == 'codex'
