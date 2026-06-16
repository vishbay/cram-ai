"""Tests for `cram audit --layer <name>` drilldown."""

from __future__ import annotations
import json

import pytest

from cram.audit import _layer_rows, LAYERS, run_layer


def _sess(**kw):
    base = dict(read_file_counts={}, edit_file_counts={}, big_results=0,
                carried_read_tokens=0, error_results=0, reads_before_edit=0,
                edits=0, session_id='', source='claude')
    base.update(kw)
    return base


class TestLayerRows:
    def test_repeated_needs_two_sessions(self):
        sessions = [_sess(read_file_counts={'a.py': 3}),
                    _sess(read_file_counts={'a.py': 2, 'b.py': 1})]
        rows = _layer_rows('repeated', sessions, '/r')
        assert rows[0] == {'file': 'a.py', 'reads': 5, 'sessions': 2}
        assert all(r['file'] != 'b.py' for r in rows)   # only 1 session → excluded

    def test_redundant_is_within_session_extra(self):
        rows = _layer_rows('redundant', [_sess(read_file_counts={'a.py': 3, 'b.py': 1})], '/r')
        assert rows == [{'file': 'a.py', 'extra_reads': 2}]

    def test_churn(self):
        rows = _layer_rows('churn', [_sess(edit_file_counts={'a.py': 3})], '/r')
        assert rows[0]['re_edits'] == 2

    def test_carried_ranked_by_tokens(self):
        rows = _layer_rows('carried', [
            _sess(big_results=1, carried_read_tokens=100, session_id='x'),
            _sess(big_results=2, carried_read_tokens=500, session_id='y'),
        ], '/r')
        assert [r['session_id'] for r in rows] == ['y', 'x']

    def test_retries_excludes_clean_sessions(self):
        rows = _layer_rows('retries', [
            _sess(error_results=2, session_id='x'), _sess(error_results=0),
        ], '/r')
        assert rows == [{'session_id': 'x', 'source': 'claude', 'failed': 2}]

    def test_orientation_edit_sessions_only(self):
        rows = _layer_rows('orientation', [
            _sess(edits=1, reads_before_edit=5, session_id='x'),
            _sess(edits=0, reads_before_edit=9),   # no edit → excluded
        ], '/r')
        assert rows == [{'session_id': 'x', 'source': 'claude', 'reads_before_edit': 5}]

    def test_unknown_layer_raises(self):
        with pytest.raises(ValueError):
            _layer_rows('bogus', [], '/r')

    def test_all_layers_handle_empty(self):
        for layer in LAYERS:
            assert _layer_rows(layer, [], '/r') == []


class TestRunLayer:
    def test_json_output(self, monkeypatch, capsys):
        import cram.audit as a
        monkeypatch.setattr(a, 'collect_layer',
                            lambda *args, **kw: [{'file': 'a.py', 'extra_reads': 2}])
        run_layer('redundant', '/r', as_json=True)
        out = json.loads(capsys.readouterr().out)
        assert out['layer'] == 'redundant'
        assert out['rows'][0]['file'] == 'a.py'

    def test_empty_message(self, monkeypatch, capsys):
        import cram.audit as a
        monkeypatch.setattr(a, 'collect_layer', lambda *args, **kw: [])
        run_layer('carried', '/r')
        assert 'No carried evidence' in capsys.readouterr().out

    def test_renders_carried_table(self, monkeypatch, capsys):
        import cram.audit as a
        monkeypatch.setattr(a, 'collect_layer', lambda *args, **kw: [
            {'session_id': 'abcd1234ef', 'source': 'claude',
             'big_results': 2, 'carried_tokens': 5000}])
        run_layer('carried', '/r')
        out = capsys.readouterr().out
        assert '5,000 carried tok' in out and 'abcd1234' in out
