"""Tests for cram/audit_report_html.py and `cram audit --report-html`."""

from __future__ import annotations
import json

from cram.audit import collect_audit, run_report_html, LAYERS, _layer_rows
from cram.audit_report_html import render_report_html

# Reuse the rich-repo fixture builder from the markdown report tests.
from tests.test_audit_report import _setup, _tool, _usage, _rich_repo


def _layers_for(repo, days=365):
    """Build the {layer: rows} map render_report_html expects."""
    import cram.audit as a
    import cram.audit_store as store_mod
    store = store_mod.AuditStore.open()
    try:
        sessions, _ = a._gather_sessions(store, repo, days, False, False)
    finally:
        store.close()
    return {name: _layer_rows(name, sessions or [], repo) for name in LAYERS}


class TestRenderReportHtml:
    def test_self_contained_document(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert html.startswith('<!DOCTYPE html>')
        assert html.rstrip().endswith('</html>')
        # everything inline — no external fetches
        assert '<style>' in html and '<script>' in html
        assert 'http://' not in html.replace('http://www.w3', '')  # no external links except none
        # no unrendered format placeholders
        import re
        assert not re.search(r'\{[a-z_]+\}', html)

    def test_all_sections_present(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        for anchor in ('id="headline"', 'id="waterfall"', 'id="findings"',
                       'id="leaderboard"', 'id="layers"', 'id="metrics"'):
            assert anchor in html

    def test_headline_share_rendered(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        # pre-edit 5000/8000 = 62.5% → 62%
        assert '<em>62%</em>' in html or '<em>63%</em>' in html
        assert 'pre-edit context share' in html

    def test_waterfall_tree_levels(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'wf-row level-0' in html        # total
        assert 'wf-row level-1' in html        # components
        assert 'wf-row level-2' in html        # pre/post split
        assert 'Estimated overlays' in html

    def test_waterfall_fallback_no_edit_sessions(self, tmp_path, monkeypatch):
        # read-only only → spine_tree is None → composition-only waterfall
        _setup(tmp_path, monkeypatch, [
            [_tool('Read', {'file_path': 'a.py'}), _usage(input_tokens=1000)],
        ])
        data = collect_audit(str(tmp_path), days=365)
        assert data['spine_tree'] is None
        html = render_report_html(data, {}, str(tmp_path))
        assert 'wf-row level-0' in html
        assert 'composition only' in html
        # no pre/post rows in the waterfall section
        wf = html.split('id="waterfall"')[1].split('</section>')[0]
        assert 'wf-row level-2' not in wf

    def test_layer_dropdowns_render(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        layers = _layers_for(repo)
        html = render_report_html(data, layers, repo)
        # repeated layer has hot.py read across 2 sessions → a contributor row
        assert '<details' in html
        assert 'top contributors' in html
        assert 'hot.py' in html

    def test_html_escapes_paths_and_text(self, tmp_path, monkeypatch):
        # a file path with HTML-significant chars must be escaped, not injected
        _setup(tmp_path, monkeypatch, [
            [_tool('Read', {'file_path': '<script>x</script>.py'}),
             _usage(input_tokens=1000),
             _tool('Read', {'file_path': '<script>x</script>.py'}),
             _usage(input_tokens=1000),
             _tool('Edit', {'file_path': 'safe.py'}), _usage(input_tokens=500)],
            [_tool('Read', {'file_path': '<script>x</script>.py'}),
             _usage(input_tokens=1000),
             _tool('Edit', {'file_path': 'safe.py'}), _usage(input_tokens=500)],
        ])
        repo = str(tmp_path)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert '<script>x</script>.py' not in html
        assert '&lt;script&gt;' in html

    def test_metrics_include_cost_and_cache_hit(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'Cache hit rate' in html
        assert 'Avg cost per session' in html
        assert 'Reads before first edit' in html

    def test_theme_toggle_present(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'toggleTheme' in html
        assert 'data-theme="dark"' in html
        assert '[data-theme="light"]' in html


class TestRunReportHtml:
    def test_writes_file_no_open(self, tmp_path, monkeypatch, capsys):
        repo = _rich_repo(tmp_path, monkeypatch)
        out = str(tmp_path / 'report.html')
        run_report_html(repo, days=365, out_path=out, open_browser=False)
        printed = capsys.readouterr().out
        assert 'Wrote' in printed
        with open(out) as f:
            content = f.read()
        assert content.startswith('<!DOCTYPE html>')
        assert 'id="waterfall"' in content

    def test_default_filename(self, tmp_path, monkeypatch, capsys):
        repo = _rich_repo(tmp_path, monkeypatch)
        monkeypatch.chdir(tmp_path)
        run_report_html(repo, days=365, out_path=None, open_browser=False)
        assert (tmp_path / 'cram-audit-report.html').exists()

    def test_no_sessions_message(self, tmp_path, monkeypatch, capsys):
        import cram.audit as a
        monkeypatch.setattr(a, '_project_transcript_dir', lambda r: None)
        monkeypatch.setattr(a, '_cursor_agent_transcripts_dir', lambda: None)
        monkeypatch.setattr(a, '_cursor_storage_root', lambda: None)
        monkeypatch.setattr(a, '_codex_sessions_dir', lambda: None)
        run_report_html(str(tmp_path), days=30, out_path=str(tmp_path / 'x.html'),
                        open_browser=False)
        assert 'No sessions found' in capsys.readouterr().out
        assert not (tmp_path / 'x.html').exists()

    def test_does_not_open_browser_when_false(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        called = []
        import webbrowser
        monkeypatch.setattr(webbrowser, 'open', lambda *a, **k: called.append(a))
        run_report_html(repo, days=365, out_path=str(tmp_path / 'r.html'),
                        open_browser=False)
        assert called == []
