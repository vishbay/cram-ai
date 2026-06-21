"""Tests for cram.usage."""
import json
import time
from pathlib import Path


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, 'w') as f:
        for entry in entries:
            f.write(json.dumps(entry) + '\n')


def test_missing_dir_returns_none(tmp_path, monkeypatch):
    from cram import usage
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    result = usage.measured_usage('/some/repo/path')
    assert result is None


def test_sums_match_fixture(tmp_path, monkeypatch):
    from cram import usage

    # Build the dashed path that usage.py will look for
    repo_root = '/Users/test/myrepo'
    dashed = repo_root.replace('/', '-')          # '-Users-test-myrepo'
    transcript_dir = tmp_path / '.claude' / 'projects' / dashed
    transcript_dir.mkdir(parents=True)

    monkeypatch.setattr(Path, 'home', lambda: tmp_path)

    entries = [
        {'message': {'usage': {
            'cache_creation_input_tokens': 100,
            'cache_read_input_tokens':     200,
            'input_tokens':                300,
            'output_tokens':               50,
        }}},
        {'message': {'usage': {
            'cache_creation_input_tokens': 50,
            'cache_read_input_tokens':     75,
            'input_tokens':                125,
            'output_tokens':               25,
        }}},
        # Entry with no usage field — should be skipped cleanly
        {'message': {}},
        # Top-level entry with no message key
        {'type': 'user'},
    ]
    _write_jsonl(transcript_dir / 'session1.jsonl', entries)

    result = usage.measured_usage(repo_root)
    assert result is not None
    assert result['available'] is True
    assert result['writes']  == 150
    assert result['reads']   == 275
    assert result['input']   == 425
    assert result['output']  == 75
    assert result['sessions'] == 1
    assert result['est_cost'] >= 0


def test_malformed_lines_skipped(tmp_path, monkeypatch):
    from cram import usage

    repo_root = '/Users/test/badrepo'
    dashed = repo_root.replace('/', '-')
    transcript_dir = tmp_path / '.claude' / 'projects' / dashed
    transcript_dir.mkdir(parents=True)

    monkeypatch.setattr(Path, 'home', lambda: tmp_path)

    # File with a mix of valid and malformed JSON lines
    path = transcript_dir / 'session.jsonl'
    with open(path, 'w') as f:
        f.write('not valid json\n')
        f.write('\n')  # blank line
        f.write(json.dumps({'message': {'usage': {'input_tokens': 10}}}) + '\n')
        f.write('{broken\n')

    result = usage.measured_usage(repo_root)
    assert result is not None
    assert result['input'] == 10


def test_old_files_excluded(tmp_path, monkeypatch):
    from cram import usage

    repo_root = '/Users/test/agerepo'
    dashed = repo_root.replace('/', '-')
    transcript_dir = tmp_path / '.claude' / 'projects' / dashed
    transcript_dir.mkdir(parents=True)

    monkeypatch.setattr(Path, 'home', lambda: tmp_path)

    # Write a file then backdate it to 30 days ago
    old_path = transcript_dir / 'old.jsonl'
    _write_jsonl(old_path, [
        {'message': {'usage': {'input_tokens': 999}}}
    ])
    old_time = time.time() - 31 * 86400
    import os
    os.utime(old_path, (old_time, old_time))

    result = usage.measured_usage(repo_root, days=7)
    assert result is None
