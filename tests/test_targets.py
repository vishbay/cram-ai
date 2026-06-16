"""Tests for cram/targets.py — save/load default_target, detect, write."""

import os
import pytest

from cram.targets import (
    load_default_target,
    save_default_target,
    detect_targets,
    write_to_target,
    load_custom_targets,
    get_effective_targets,
    get_effective_indicators,
    TARGET_FILES,
    CRAM_SECTION_START,
    CRAM_SECTION_END,
)
from cram.targets import _upsert_cram_section

CONTEXT_DIR = '.ai-context'


# ---------------------------------------------------------------------------
# _upsert_cram_section — regression: injected code excerpts may contain
# regex-special sequences (backslashes, \1, \g<0>) that must not be
# interpreted as re.sub replacement escapes. (Found via the click case study,
# where `cram task --target claude --inject` crashed on core.py excerpts.)
# ---------------------------------------------------------------------------

class TestUpsertCramSectionRegexSafe:
    def test_inject_with_backslashes_and_group_refs(self, tmp_path):
        p = tmp_path / 'CLAUDE.md'
        p.write_text(f'{CRAM_SECTION_START}\nold\n{CRAM_SECTION_END}\n')
        nasty = r'def f(): return re.sub(r"\1\g<0>", x)  # \\ and \1'
        _upsert_cram_section(str(p), nasty)
        out = p.read_text()
        assert nasty.rstrip() in out          # written literally, not interpreted
        assert out.count(CRAM_SECTION_START) == 1   # replaced, not duplicated

    def test_creates_file_when_absent(self, tmp_path):
        p = tmp_path / 'CLAUDE.md'
        _upsert_cram_section(str(p), 'hello')
        assert 'hello' in p.read_text()


# ---------------------------------------------------------------------------
# save_default_target / load_default_target round-trip
# ---------------------------------------------------------------------------

class TestSaveLoadDefaultTarget:
    def test_round_trip(self, tmp_path):
        os.makedirs(tmp_path / CONTEXT_DIR)
        save_default_target(str(tmp_path), 'cursor')
        assert load_default_target(str(tmp_path)) == 'cursor'

    def test_overwrites_previous_value(self, tmp_path):
        os.makedirs(tmp_path / CONTEXT_DIR)
        save_default_target(str(tmp_path), 'claude')
        save_default_target(str(tmp_path), 'windsurf')
        assert load_default_target(str(tmp_path)) == 'windsurf'

    def test_all_is_valid(self, tmp_path):
        os.makedirs(tmp_path / CONTEXT_DIR)
        save_default_target(str(tmp_path), 'all')
        assert load_default_target(str(tmp_path)) == 'all'

    def test_all_known_targets_round_trip(self, tmp_path):
        os.makedirs(tmp_path / CONTEXT_DIR)
        for t in TARGET_FILES:
            save_default_target(str(tmp_path), t)
            assert load_default_target(str(tmp_path)) == t

    def test_invalid_target_not_saved(self, tmp_path):
        os.makedirs(tmp_path / CONTEXT_DIR)
        save_default_target(str(tmp_path), 'unknown-tool')
        assert load_default_target(str(tmp_path)) is None

    def test_preserves_existing_toml_content(self, tmp_path):
        config = tmp_path / CONTEXT_DIR / 'config.toml'
        os.makedirs(tmp_path / CONTEXT_DIR)
        config.write_text('[model]\nname = "claude"\n')
        save_default_target(str(tmp_path), 'cursor')
        content = config.read_text()
        assert 'name = "claude"' in content
        assert 'default_target = "cursor"' in content

    def test_appends_to_existing_task_section(self, tmp_path):
        config = tmp_path / CONTEXT_DIR / 'config.toml'
        os.makedirs(tmp_path / CONTEXT_DIR)
        config.write_text('[task]\nsome_other = "value"\n')
        save_default_target(str(tmp_path), 'copilot')
        content = config.read_text()
        assert 'some_other = "value"' in content
        assert 'default_target = "copilot"' in content

    def test_no_context_dir_returns_none(self, tmp_path):
        assert load_default_target(str(tmp_path)) is None

    def test_empty_config_dir_returns_none(self, tmp_path):
        os.makedirs(tmp_path / CONTEXT_DIR)
        assert load_default_target(str(tmp_path)) is None

    def test_loads_default_target_from_legacy_context_dir(self, tmp_path):
        legacy = tmp_path / '.cram-ai-context'
        legacy.mkdir()
        (legacy / 'config.toml').write_text('[task]\ndefault_target = "cursor"\n')

        assert load_default_target(str(tmp_path)) == 'cursor'


# ---------------------------------------------------------------------------
# detect_targets
# ---------------------------------------------------------------------------

class TestDetectTargets:
    def test_detects_cursor(self, tmp_path):
        (tmp_path / '.cursor').mkdir()
        assert 'cursor' in detect_targets(str(tmp_path))

    def test_detects_github(self, tmp_path):
        (tmp_path / '.github').mkdir()
        assert 'copilot' in detect_targets(str(tmp_path))

    def test_detects_none_when_empty(self, tmp_path):
        assert detect_targets(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# write_to_target
# ---------------------------------------------------------------------------

class TestWriteToTarget:
    def test_creates_file_for_cursor(self, tmp_path):
        path = write_to_target(str(tmp_path), 'cursor', '# Task\n')
        assert os.path.exists(path)
        content = open(path).read()
        assert '# Task\n' in content
        assert 'Command Output Protection' in content
        assert 'head -c 6000' in content

    def test_all_targets_include_byte_cap_section(self, tmp_path):
        non_claude = [t for t in TARGET_FILES if t != 'claude']
        for target in non_claude:
            path = write_to_target(str(tmp_path), target, '# Task\n')
            content = open(path).read()
            assert 'Command Output Protection' in content, f'{target} missing byte-cap section'
            assert 'head -c 6000' in content, f'{target} missing head -c 6000'

    def test_byte_cap_respects_config_override(self, tmp_path):
        cfg_dir = tmp_path / '.ai-context'
        cfg_dir.mkdir()
        (cfg_dir / 'config.toml').write_text('[output]\nbyte_cap = 3000\n')
        path = write_to_target(str(tmp_path), 'cursor', '# Task\n')
        content = open(path).read()
        assert 'head -c 3000' in content
        assert 'head -c 6000' not in content

    def test_creates_parent_dirs(self, tmp_path):
        path = write_to_target(str(tmp_path), 'windsurf', 'content')
        assert os.path.isfile(path)

    def test_raises_on_unknown_target(self, tmp_path):
        with pytest.raises(ValueError, match='Unknown target'):
            write_to_target(str(tmp_path), 'nonexistent', '')

    def test_gemini_target_upserts_markers(self, tmp_path):
        path = write_to_target(str(tmp_path), 'gemini', 'task text')
        assert path.endswith('GEMINI.md')
        content = open(path).read()
        assert CRAM_SECTION_START in content
        assert CRAM_SECTION_END in content
        assert 'task text' in content

    def test_gemini_preserves_user_content(self, tmp_path):
        gemini_md = tmp_path / 'GEMINI.md'
        gemini_md.write_text('# My config\nuser content here\n')
        write_to_target(str(tmp_path), 'gemini', 'new task')
        content = gemini_md.read_text()
        assert 'user content here' in content
        assert 'new task' in content


class TestCustomTargets:
    def _write_config(self, tmp_path, toml_content: str):
        ctx = tmp_path / '.ai-context'
        ctx.mkdir()
        (ctx / 'config.toml').write_text(toml_content)

    def test_load_custom_target_basic(self, tmp_path):
        self._write_config(tmp_path, '[targets.acme]\nfile = "ACME.md"\n')
        custom = load_custom_targets(str(tmp_path))
        assert 'acme' in custom
        assert custom['acme']['file'] == 'ACME.md'
        assert custom['acme']['indicator'] is None
        assert custom['acme']['upsert'] is False

    def test_load_custom_target_with_all_fields(self, tmp_path):
        self._write_config(tmp_path, (
            '[targets.acme]\n'
            'file = "ACME.md"\n'
            'indicator = "acme.config.json"\n'
            'upsert = true\n'
        ))
        custom = load_custom_targets(str(tmp_path))
        assert custom['acme']['indicator'] == 'acme.config.json'
        assert custom['acme']['upsert'] is True

    def test_load_custom_target_no_file_skipped(self, tmp_path):
        self._write_config(tmp_path, '[targets.bad]\nindicator = "x.json"\n')
        custom = load_custom_targets(str(tmp_path))
        assert 'bad' not in custom

    def test_get_effective_targets_merges(self, tmp_path):
        self._write_config(tmp_path, '[targets.acme]\nfile = "ACME.md"\n')
        effective = get_effective_targets(str(tmp_path))
        assert 'acme' in effective
        assert effective['acme'] == 'ACME.md'
        assert 'cursor' in effective  # builtin still present

    def test_get_effective_indicators_custom_with_indicator(self, tmp_path):
        self._write_config(tmp_path, (
            '[targets.acme]\n'
            'file = "ACME.md"\n'
            'indicator = "acme.config.json"\n'
        ))
        indicators = get_effective_indicators(str(tmp_path))
        assert indicators.get('acme') == 'acme.config.json'

    def test_get_effective_indicators_custom_without_indicator(self, tmp_path):
        self._write_config(tmp_path, '[targets.acme]\nfile = "ACME.md"\n')
        indicators = get_effective_indicators(str(tmp_path))
        assert 'acme' not in indicators

    def test_detect_custom_target_via_indicator(self, tmp_path):
        self._write_config(tmp_path, (
            '[targets.acme]\n'
            'file = "ACME.md"\n'
            'indicator = "acme.config.json"\n'
        ))
        (tmp_path / 'acme.config.json').write_text('{}')
        detected = detect_targets(str(tmp_path))
        assert 'acme' in detected

    def test_write_to_custom_target_overwrite_by_default(self, tmp_path):
        self._write_config(tmp_path, '[targets.acme]\nfile = "ACME.md"\n')
        path = write_to_target(str(tmp_path), 'acme', 'my task')
        assert path.endswith('ACME.md')
        content = open(path).read()
        assert 'my task' in content
        assert CRAM_SECTION_START not in content

    def test_write_to_custom_target_upsert_when_flagged(self, tmp_path):
        self._write_config(tmp_path, (
            '[targets.acme]\n'
            'file = "ACME.md"\n'
            'upsert = true\n'
        ))
        (tmp_path / 'ACME.md').write_text('# User content\n')
        path = write_to_target(str(tmp_path), 'acme', 'injected task')
        content = open(path).read()
        assert '# User content' in content
        assert CRAM_SECTION_START in content
        assert 'injected task' in content

    def test_write_to_custom_target_unknown_raises(self, tmp_path):
        self._write_config(tmp_path, '[targets.acme]\nfile = "ACME.md"\n')
        with pytest.raises(ValueError, match='Unknown target'):
            write_to_target(str(tmp_path), 'nonexistent', '')

    def test_save_load_custom_target_as_default(self, tmp_path):
        self._write_config(tmp_path, '[targets.acme]\nfile = "ACME.md"\n')
        save_default_target(str(tmp_path), 'acme')
        assert load_default_target(str(tmp_path)) == 'acme'
