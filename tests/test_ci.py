"""Phase 2: the CI engine behind the cram audit GitHub Action (cram/ci.py).
Pure rendering + gating over JSON — no network, no model, no live agent."""

from __future__ import annotations
import json

from cram import ci


# ── compare ───────────────────────────────────────────────────────────────────

def _compare_doc():
    return {
        'days': 30,
        'a': {'path': '/x/baseline', 'data': {
            'sessions': 10, 'avg_reads_before_edit': 6.0, 'avg_requests': 20}},
        'b': {'path': '/x/cram', 'data': {
            'sessions': 10, 'avg_reads_before_edit': 3.0, 'avg_requests': 12}},
    }


def test_compare_comment_has_marker_rows_and_delta():
    md = ci.render_compare_comment(_compare_doc())
    assert ci.STICKY_MARKER in md
    assert 'Reads before first edit' in md
    assert 'baseline' in md and 'cram' in md
    # B oriented faster: 3 - 6 = -3.0
    assert '-3.0' in md
    assert '-50%' in md          # 6 → 3


def test_compare_comment_handles_no_sessions():
    doc = {'days': 30, 'a': {'path': 'a', 'data': None}, 'b': {'path': 'b', 'data': {}}}
    md = ci.render_compare_comment(doc)
    assert ci.STICKY_MARKER in md
    assert 'No sessions' in md


# ── report ────────────────────────────────────────────────────────────────────

def _setup_repo(tmp_path, monkeypatch):
    import cram.audit as _audit_mod
    td = tmp_path / 'proj'
    td.mkdir()
    sessions = [
        [{'type': 'tool_use', 'name': 'Read', 'input': {'file_path': str(tmp_path) + '/hot.py'}},
         {'usage': {'input_tokens': 3000, 'cache_read_input_tokens': 0,
                    'cache_creation_input_tokens': 0}},
         {'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': str(tmp_path) + '/hot.py'}},
         {'usage': {'input_tokens': 1000, 'cache_read_input_tokens': 0,
                    'cache_creation_input_tokens': 0}}],
    ]
    for i, msgs in enumerate(sessions):
        with open(str(td / f's{i}.jsonl'), 'w') as f:
            for m in msgs:
                f.write(json.dumps(m) + '\n')
    monkeypatch.setattr(_audit_mod, '_project_transcript_dir', lambda r, d=str(td): d)
    monkeypatch.setattr(_audit_mod, '_cursor_agent_transcripts_dir', lambda: None)
    monkeypatch.setattr(_audit_mod, '_cursor_storage_root', lambda: None)
    monkeypatch.setattr(_audit_mod, '_codex_sessions_dir', lambda: None)
    return str(tmp_path)


def test_report_comment_renders_with_marker_and_details(tmp_path, monkeypatch):
    from cram.audit import collect_audit
    repo = _setup_repo(tmp_path, monkeypatch)
    data = collect_audit(repo, days=365)
    md = ci.render_report_comment(data, repo)
    assert ci.STICKY_MARKER in md
    assert 'sessions analysed' in md
    assert '<details>' in md


def test_report_comment_handles_empty():
    md = ci.render_report_comment(None)
    assert ci.STICKY_MARKER in md
    assert 'No sessions' in md


# ── rig gate ──────────────────────────────────────────────────────────────────

def _rig(success_by_provider):
    return {'providers': {n: {'success_rate': s, 'ran': 2, 'passed': int(2 * s)}
                          for n, s in success_by_provider.items()}}


def test_rig_gate_fails_on_success_drop():
    base = _rig({'baseline': 1.0, 'cram': 1.0})
    cand = _rig({'baseline': 1.0, 'cram': 0.5})
    passed, body = ci.evaluate_rig_gate(base, cand, tolerance=0.0)
    assert passed is False
    assert 'FAIL' in body and '❌' in body


def test_rig_gate_passes_within_tolerance():
    base = _rig({'cram': 1.0})
    cand = _rig({'cram': 0.8})
    passed, _ = ci.evaluate_rig_gate(base, cand, tolerance=0.25)
    assert passed is True


def test_rig_gate_accepts_wrapped_summary():
    base = {'meta': {}, 'summary': _rig({'cram': 1.0})}
    cand = {'meta': {}, 'summary': _rig({'cram': 1.0})}
    passed, _ = ci.evaluate_rig_gate(base, cand)
    assert passed is True


# ── main / $GITHUB_OUTPUT ──────────────────────────────────────────────────────

def test_main_compare_writes_github_output(tmp_path, monkeypatch):
    src = tmp_path / 'cmp.json'
    src.write_text(json.dumps(_compare_doc()))
    gh_out = tmp_path / 'gh_out'
    gh_out.write_text('')
    out_md = tmp_path / 'comment.md'
    monkeypatch.setenv('GITHUB_OUTPUT', str(gh_out))

    code = ci.main(['--mode', 'compare', '--file-a', str(src), '--out', str(out_md)])
    assert code == 0
    gh = gh_out.read_text()
    assert 'passed=true' in gh
    assert 'comment-body<<CRAM_EOF' in gh
    assert ci.STICKY_MARKER in out_md.read_text()


def test_main_rig_gate_failure_exits_nonzero(tmp_path, monkeypatch):
    base = tmp_path / 'base.json'
    cand = tmp_path / 'cand.json'
    base.write_text(json.dumps(_rig({'cram': 1.0})))
    cand.write_text(json.dumps(_rig({'cram': 0.0})))
    gh_out = tmp_path / 'gh_out'
    gh_out.write_text('')
    monkeypatch.setenv('GITHUB_OUTPUT', str(gh_out))

    code = ci.main(['--mode', 'rig', '--file-a', str(base), '--file-b', str(cand)])
    assert code == 1
    assert 'passed=false' in gh_out.read_text()
