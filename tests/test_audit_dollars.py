"""Real, model-aware $ + actionable headline."""

from __future__ import annotations
import json

import pytest

from cram.cost_model import resolve_model_price
from cram.audit import collect_audit, cost_headline


# ── model price resolution ────────────────────────────────────────────────────

def test_resolve_model_price_by_substring():
    assert resolve_model_price('claude-opus-4-8') == pytest.approx(5.00 / 1e6)
    assert resolve_model_price('claude-sonnet-4-6') == pytest.approx(3.00 / 1e6)
    assert resolve_model_price('gpt-5') == pytest.approx(1.25 / 1e6)


def test_resolve_model_price_falls_back_to_provider(monkeypatch):
    monkeypatch.setenv('CRAM_PROVIDER', 'anthropic')
    assert resolve_model_price(None) == pytest.approx(3.00 / 1e6)
    assert resolve_model_price('totally-unknown-model') == pytest.approx(3.00 / 1e6)


def test_resolve_model_price_env_override(monkeypatch):
    monkeypatch.setenv('CRAM_MODEL_PRICES', json.dumps({'opus': 99.0}))
    assert resolve_model_price('claude-opus-4-8') == pytest.approx(99.0 / 1e6)


# ── integration ───────────────────────────────────────────────────────────────

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


def _edit_session(repo, model, input_tokens):
    return [
        {'message': {'model': model}},
        {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': repo + '/h.py'}},
        {'usage': {'input_tokens': input_tokens, 'cache_read_input_tokens': 0,
                   'cache_creation_input_tokens': 0}},
        {'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': repo + '/h.py'}},
        {'usage': {'input_tokens': 0, 'cache_read_input_tokens': 0,
                   'cache_creation_input_tokens': 0}},
    ]


def test_real_dollars_are_model_aware(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch,
                  [_edit_session(str(tmp_path), 'claude-opus-4-8', 1_000_000)])
    data = collect_audit(repo, days=30)
    # 1M effective input tokens × $5/M = $5.00, priced by the session's own model.
    assert data['total_eff_cost'] == pytest.approx(5.00, rel=1e-3)
    assert data['model_mix'] == {'claude-opus-4-8': 1}
    assert data['cost_measured_sessions'] == 1
    assert data['monthly_cost'] == pytest.approx(5.00, rel=1e-3)   # 30-day window
    assert data['schema_version'] == 'audit/2'
    assert data['bases']['total_eff_cost'] == 'measured'


def test_biggest_avoidable_and_headline(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch,
                  [_edit_session(str(tmp_path), 'claude-sonnet-4-6', 500_000)])
    data = collect_audit(repo, days=30)
    big = data['biggest_avoidable']
    assert big is not None and big['cost_per_session'] > 0
    assert big['monthly_cost'] > 0
    hl = cost_headline(data)
    assert hl and '$' in hl and 'biggest avoidable' in hl


def test_headline_none_without_measured_cost():
    assert cost_headline({'cost_measured_sessions': 0, 'total_eff_cost': 0}) is None
