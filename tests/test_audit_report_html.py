"""Tests for cram/audit_report_html.py and `cram audit --report-html`."""

from __future__ import annotations

from cram.audit import collect_audit, run_report_html, LAYERS, _layer_rows
from cram.audit_report_html import render_report_html

# Reuse the rich-repo fixture builder from the markdown report tests.
from tests.test_audit_report import _setup, _tool, _usage, _rich_repo


def _layers_for(repo, days=365):
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
        assert '<style>' in html and '<script>' in html
        import re
        assert not re.search(r'\{[a-z_]+\}', html)   # no unrendered placeholders

    def test_all_sections_present(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        for anchor in ('id="sum"', 'id="cov"', 'id="wf"', 'id="cost"',
                       'id="board"', 'id="layers"', 'id="find"', 'id="metrics"'):
            assert anchor in html
        assert 'class="strip"' in html   # stat strip

    def test_headline_share_rendered(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'hl-big' in html
        assert '62%' in html or '63%' in html
        assert 'pre-edit context share' in html

    def test_waterfall_tree_levels_and_cost(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'wf-row root' in html        # total
        assert 'wf-row s1' in html          # components
        assert 'wf-row s2' in html          # pre/post split
        assert 'wf-c' in html and '/s' in html   # $/session per component

    def test_waterfall_fallback_no_edit_sessions(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, [
            [_tool('Read', {'file_path': 'a.py'}), _usage(input_tokens=1000)],
        ])
        data = collect_audit(str(tmp_path), days=365)
        assert data['spine_tree'] is None
        html = render_report_html(data, {}, str(tmp_path))
        assert 'wf-row root' in html
        assert 'composition only' in html
        wf = html.split('id="wf"')[1].split('</div></div>')[0]
        assert 'wf-row s2' not in wf

    def test_coverage_block(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'Coverage' in html
        assert 'with tokens' in html and 'parse fails' in html
        assert 'b measured' in html and 'b estimated' in html and 'b count' in html
        assert 'Output-token spend' in html

    def test_thin_sample_warning(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, [
            [_tool('Read', {'file_path': 'a.py'}), _usage(input_tokens=1000),
             _tool('Edit', {'file_path': 'a.py'}), _usage(input_tokens=500)],
        ])
        repo = str(tmp_path)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'Small sample' in html

    def test_cost_by_layer_table(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'Cost by waste layer' in html
        assert 'do not sum to the spine' in html
        assert 'orientation' in html and 'carried' in html

    def test_layer_dropdowns_render(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert '<details>' in html
        assert 'top contributors' in html
        assert 'hot.py' in html

    def test_html_escapes_paths_and_text(self, tmp_path, monkeypatch):
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

    def test_metrics_and_strip(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'Cache hit rate' in html
        assert 'Avg cost / session' in html
        assert 'cache hit' in html         # stat strip label

    def test_theme_toggle_present(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'tt()' in html
        assert 'data-theme="dark"' in html
        assert '[data-theme="light"]' in html

    def test_retry_loops_section(self, tmp_path, monkeypatch):
        # two sessions where 'pytest -x' fails → repeated failure surfaces
        def fail(cmd, tid):
            return [{'type': 'tool_use', 'id': tid, 'name': 'Bash', 'input': {'command': cmd}},
                    {'type': 'tool_result', 'tool_use_id': tid, 'content': 'boom', 'is_error': True}]
        _setup(tmp_path, monkeypatch, [
            fail('pytest -x', 'a') + [_usage(input_tokens=500)],
            fail('pytest -x', 'b') + [_usage(input_tokens=500)],
        ])
        repo = str(tmp_path)
        data = collect_audit(repo, days=365)
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'id="retry"' in html
        assert 'Retry loops' in html
        assert 'pytest -x' in html

    def test_context_ab_section(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        # inject a context-mode segment (collect_audit only sets it when sessions
        # carry both modes; the renderer must surface it honestly when present)
        data['context_mode_segment'] = {
            'on':  {'sessions': 12, 'avg_reads_before_edit': 6.1,
                    'carried_cost_per_session': 0.09, 'sessions_with_big_results': 2,
                    'avg_peak_context': 24100, 'avg_context_growth': 2.1},
            'off': {'sessions': 35, 'avg_reads_before_edit': 9.0,
                    'carried_cost_per_session': 0.18, 'sessions_with_big_results': 9,
                    'avg_peak_context': 31800, 'avg_context_growth': 2.9},
        }
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'id="ab"' in html
        assert 'Context layer: on vs off' in html
        assert 'ctx on (12)' in html and 'ctx off (35)' in html
        assert '-32%' in html or '−32%' in html  # reads dropped (good)
        assert 'Observational, not causal' in html

    def test_no_ab_section_without_segment(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        assert data.get('context_mode_segment') is None
        html = render_report_html(data, _layers_for(repo), repo)
        assert 'id="ab"' not in html

    def test_embedded_drilldown(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        sid = data['leaderboard'][0]['session_id']
        timeline = {
            'rows': [
                {'turn': 1, 'input': 3000, 'cache_read': 0, 'context': 3000,
                 'delta': 0, 'notes': ['Read hot.py']},
                {'turn': 2, 'input': 900, 'cache_read': 14000, 'context': 18300,
                 'delta': 12400, 'notes': ['⚠ fixtures.py → 11k tok result']},
            ],
            'peak_context': 18300, 'carried_read_tokens': 11240,
            'redundant': [('hot.py', 3)],
            'failed_commands': [{'cmd': 'pytest -x', 'failures': 4}],
        }
        html = render_report_html(data, _layers_for(repo), repo,
                                  drilldowns={sid: timeline})
        assert 'drill-row' in html
        assert 'per-turn timeline' in html
        assert '+12,400' in html
        assert 'pytest -x' in html and '×4' in html


class TestRunReportHtml:
    def test_writes_file_no_open(self, tmp_path, monkeypatch, capsys):
        repo = _rich_repo(tmp_path, monkeypatch)
        out = str(tmp_path / 'report.html')
        run_report_html(repo, days=365, out_path=out, open_browser=False)
        assert 'Wrote' in capsys.readouterr().out
        with open(out) as f:
            content = f.read()
        assert content.startswith('<!DOCTYPE html>')
        assert 'id="wf"' in content

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
