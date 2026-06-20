"""Workstream B: the audit JSON contract (schema_version + bases) and findings
sample-size gating (preliminary off small N)."""

from __future__ import annotations
import json

from cram.audit import collect_audit, AUDIT_SCHEMA_VERSION


def _setup(tmp_path, monkeypatch, sessions):
    import cram.audit as _audit_mod
    td = tmp_path / 'proj'
    td.mkdir(parents=True)
    for i, msgs in enumerate(sessions):
        with open(td / f's{i}.jsonl', 'w') as f:
            for m in msgs:
                f.write(json.dumps(m) + '\n')
    monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r, d=str(td): d)
    monkeypatch.setattr(_audit_mod, '_cursor_agent_transcripts_dir', lambda: None)
    monkeypatch.setattr(_audit_mod, '_cursor_storage_root', lambda: None)
    monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: None)
    return str(tmp_path)


def _read(fp):
    return {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': fp}}


def _err():
    return {'type': 'tool_result', 'is_error': True, 'content': 'boom'}


def _usage(inp=0, cr=0, cw=0):
    return {'usage': {'input_tokens': inp, 'cache_read_input_tokens': cr,
                      'cache_creation_input_tokens': cw}}


# ── contract ──────────────────────────────────────────────────────────────────

def test_schema_version_and_bases_present(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch, [[_read(str(tmp_path) + '/a.py'), _usage(inp=100)]])
    data = collect_audit(repo, days=3650)
    assert data['schema_version'] == AUDIT_SCHEMA_VERSION
    assert data['bases']['orient_cost_per_session'] == 'estimated'
    assert data['bases']['carried_cost_per_session'] == 'measured'


def test_key_set_stable_empty_vs_rich(tmp_path, monkeypatch):
    # A minimal session and a richer one must yield the same top-level key set.
    repo1 = _setup(tmp_path / 'r1', monkeypatch, [[_read(str(tmp_path) + '/a.py'), _usage(inp=50)]])
    keys1 = set(collect_audit(repo1, days=3650).keys())
    repo2 = _setup(tmp_path / 'r2', monkeypatch, [
        [_read(str(tmp_path) + '/h.py'), _usage(inp=3000),
         {'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': str(tmp_path) + '/h.py'}},
         _usage(inp=1000)],
    ])
    keys2 = set(collect_audit(repo2, days=3650).keys())
    assert keys1 == keys2


# ── findings sample-size gating ───────────────────────────────────────────────

def test_findings_preliminary_on_small_n(tmp_path, monkeypatch):
    # One session with repeated failed tool calls → retry-loops fires, N=1.
    repo = _setup(tmp_path, monkeypatch, [
        [_read(str(tmp_path) + '/a.py'), _usage(inp=100), _err(), _err()],
    ])
    data = collect_audit(repo, days=3650)
    retry = next((f for f in data['findings'] if f['id'] == 'retry-loops'), None)
    assert retry is not None
    assert retry['preliminary'] is True
    assert retry['sample_n'] == 1
    assert 'preliminary' in retry['evidence']


def test_findings_carry_sample_n(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch, [[_read(str(tmp_path) + '/a.py'), _usage(inp=100), _err(), _err()]])
    data = collect_audit(repo, days=3650)
    assert all('sample_n' in f and 'preliminary' in f for f in data['findings'])
