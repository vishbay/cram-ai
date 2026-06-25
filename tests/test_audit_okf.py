"""Tests for cram/audit_okf.py and `cram audit --okf` (OKF v0.1 bundle export)."""

from __future__ import annotations

import datetime
import os

from cram.audit import collect_audit, run_export_okf
from cram.audit_okf import render_okf_bundle, _yaml_str, _slug

# Reuse the rich-repo fixture builder from the markdown report tests.
from tests.test_audit_report import _rich_repo

_FIXED = datetime.datetime(2026, 6, 25, 12, 0, 0, tzinfo=datetime.timezone.utc)


class TestRenderOkfBundle:
    def test_root_index_declares_okf_version(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        files = render_okf_bundle(data, repo, now=_FIXED, version='0.10.0')
        assert 'index.md' in files
        root = files['index.md']
        assert root.startswith('---\n')                 # frontmatter present
        assert 'okf_version: "0.1"' in root             # bundle-root version
        assert 'type: "Knowledge Bundle"' in root
        assert 'generator: "cram-ai 0.10.0"' in root

    def test_one_doc_per_finding_with_frontmatter(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        assert data['findings']                          # fixture yields findings
        files = render_okf_bundle(data, repo, now=_FIXED)
        assert 'findings/index.md' in files
        for fd in data['findings']:
            path = f'findings/{_slug(fd["id"])}.md'
            assert path in files
            doc = files[path]
            assert 'type: "Finding"' in doc
            assert f'cram_finding_id: "{fd["id"]}"' in doc
            assert 'timestamp: "2026-06-25T12:00:00Z"' in doc
            assert '**Fix:**' in doc

    def test_okf_version_only_in_root_index(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        data = collect_audit(repo, days=365)
        files = render_okf_bundle(data, repo, now=_FIXED)
        for rel, contents in files.items():
            if rel != 'index.md':
                # key-precise: a repo path embedded in evidence may contain the
                # literal substring, so match the frontmatter key, not anywhere.
                assert not any(ln.startswith('okf_version:') for ln in contents.splitlines())

    def test_clean_bill_when_no_findings(self, tmp_path):
        # pure-function path: a no-findings audit still emits a valid root bundle
        data = {'findings': [], 'sessions': 3, 'days': 30,
                'provider': 'anthropic', 'schema_version': 'audit/3'}
        files = render_okf_bundle(data, str(tmp_path / 'myrepo'), now=_FIXED)
        assert set(files) == {'index.md'}                # no findings/ subtree
        assert 'clean bill of health' in files['index.md']

    def test_frontmatter_escapes_quotes_and_colons(self):
        # evidence strings carry colons, quotes, paths — must stay valid YAML scalars
        assert _yaml_str('a "quoted": value\nwith newline') == '"a \\"quoted\\": value with newline"'


class TestRunExportOkf:
    def test_writes_bundle_to_dir(self, tmp_path, monkeypatch, capsys):
        repo = _rich_repo(tmp_path, monkeypatch)
        out = str(tmp_path / 'okf')
        run_export_okf(repo, days=365, out_dir=out)
        assert 'Wrote OKF bundle' in capsys.readouterr().out
        assert os.path.exists(os.path.join(out, 'index.md'))
        with open(os.path.join(out, 'index.md')) as f:
            assert 'okf_version: "0.1"' in f.read()

    def test_default_dir(self, tmp_path, monkeypatch):
        repo = _rich_repo(tmp_path, monkeypatch)
        monkeypatch.chdir(tmp_path)
        run_export_okf(repo, days=365, out_dir=None)
        assert (tmp_path / 'cram-audit-okf' / 'index.md').exists()

    def test_no_sessions_writes_nothing(self, tmp_path, monkeypatch, capsys):
        import cram.audit as a
        monkeypatch.setattr(a, '_project_transcript_dir', lambda r: None)
        monkeypatch.setattr(a, '_cursor_agent_transcripts_dir', lambda: None)
        monkeypatch.setattr(a, '_cursor_storage_root', lambda: None)
        monkeypatch.setattr(a, '_codex_sessions_dir', lambda: None)
        run_export_okf(str(tmp_path), days=30, out_dir=str(tmp_path / 'okf'))
        assert 'No sessions found' in capsys.readouterr().out
        assert not (tmp_path / 'okf').exists()
