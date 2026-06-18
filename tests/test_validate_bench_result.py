"""Validator for leaderboard submissions (scripts/validate_bench_result.py)."""

from __future__ import annotations
import importlib.util
import json
import os

_SPEC = importlib.util.spec_from_file_location(
    'validate_bench_result',
    os.path.join(os.path.dirname(__file__), '..', 'scripts', 'validate_bench_result.py'))
vbr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vbr)


def _prov(success=1.0, tok=10000, ran=2, passed=2):
    return {'ran': ran, 'passed': passed, 'success_rate': success,
            'mean_eff_tokens_passed': tok}


def _write(tmp_path, providers, meta=None):
    doc = {'meta': meta if meta is not None else {'model': 'm', 'cram_version': '0.8.1'},
           'summary': {'providers': providers, 'results': []}}
    p = tmp_path / 'r.json'
    p.write_text(json.dumps(doc))
    return str(p)


def test_valid_submission_passes(tmp_path):
    p = _write(tmp_path, {'baseline': _prov(), 'cram': _prov(tok=9000)})
    assert vbr.validate(p) == []


def test_missing_baseline_fails(tmp_path):
    p = _write(tmp_path, {'cram': _prov()})
    assert any('baseline' in x for x in vbr.validate(p))


def test_missing_meta_fails(tmp_path):
    p = _write(tmp_path, {'baseline': _prov()}, meta={})
    probs = vbr.validate(p)
    assert any('model' in x for x in probs)
    assert any('cram_version' in x for x in probs)


def test_passed_without_tokens_fails(tmp_path):
    bad = {'ran': 2, 'passed': 2, 'success_rate': 1.0, 'mean_eff_tokens_passed': None}
    p = _write(tmp_path, {'baseline': bad})
    assert any('mean_eff_tokens_passed' in x for x in vbr.validate(p))


def test_main_returns_nonzero_on_bad(tmp_path):
    p = _write(tmp_path, {'cram': _prov()})        # no baseline
    assert vbr.main([p]) == 1


def test_committed_seed_result_is_valid():
    seed = os.path.join(os.path.dirname(__file__), '..', 'examples', 'rig', 'bench',
                        'results', 'cram-bench-v1-opus4.8-2026-06-18.json')
    assert vbr.validate(seed) == []
