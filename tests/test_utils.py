"""Tests for cram/utils.py — strip_code_fence and call_model routing."""

import json
from unittest.mock import MagicMock, patch

import pytest

from cram.utils import strip_code_fence, call_model, cache_min_tokens


# ---------------------------------------------------------------------------
# cache_min_tokens — per-model prompt-cache floor (Anthropic docs)
# ---------------------------------------------------------------------------

class TestCacheMinTokens:
    def test_opus_floor_is_4096(self):
        assert cache_min_tokens('Opus 4.8') == 4096
        assert cache_min_tokens('anthropic/claude-opus-4-8') == 4096

    def test_haiku_45_floor_is_4096(self):
        # the bug: 'haiku' lacks 'opus', so the old code returned 1024 (4x too low)
        assert cache_min_tokens('Haiku 4.5') == 4096
        assert cache_min_tokens('claude-haiku-4-5-20251001') == 4096

    def test_older_haiku_floor_is_2048(self):
        assert cache_min_tokens('Claude Haiku 3.5') == 2048

    def test_sonnet_46_floor_is_2048(self):
        # the bug: old code returned 1024 here, claiming sub-2048 prefixes cache
        assert cache_min_tokens('Sonnet 4.6') == 2048
        assert cache_min_tokens('anthropic/claude-sonnet-4-6') == 2048

    def test_older_sonnet_floor_is_1024(self):
        assert cache_min_tokens('claude-sonnet-4-5') == 1024
        assert cache_min_tokens('Sonnet 3.7') == 1024

    def test_unknown_defaults_to_2048_not_lowest(self):
        # an unknown model must not under-report the floor (the dangerous direction)
        assert cache_min_tokens('some-future-model') == 2048


# ---------------------------------------------------------------------------
# strip_code_fence
# ---------------------------------------------------------------------------

class TestStripCodeFence:
    def test_removes_plain_fence(self):
        assert strip_code_fence("```\nfoo\n```") == "foo"

    def test_removes_language_fence(self):
        assert strip_code_fence("```markdown\n# Arch\n```") == "# Arch"

    def test_removes_prose_preamble_before_heading(self):
        assert strip_code_fence("Here's the doc:\n# Arch") == "# Arch"

    def test_leaves_clean_markdown_unchanged(self):
        md = "# Arch\n\n## Section\nContent"
        assert strip_code_fence(md) == md

    def test_handles_empty_string(self):
        assert strip_code_fence("") == ""

    def test_strips_surrounding_whitespace(self):
        assert strip_code_fence("  \n# Heading\n  ") == "# Heading"

    def test_does_not_strip_mid_document_fences(self):
        md = "# Arch\n```python\ncode\n```\nmore"
        result = strip_code_fence(md)
        assert "```python" in result

    def test_preamble_not_stripped_if_no_colon(self):
        # Only strip preamble when it ends with ':'
        md = "Some intro line\n# Heading"
        result = strip_code_fence(md)
        assert "Some intro line" in result


# ---------------------------------------------------------------------------
# call_model routing
# ---------------------------------------------------------------------------

class TestCallModelRouting:
    def test_routes_to_litellm_when_model_has_slash(self):
        with patch.dict('os.environ', {'AICONTEXT_MODEL': 'openai/gpt-4o-mini'}, clear=False):
            with patch('cram.utils._call_via_litellm', return_value='ok') as mock_litellm:
                result = call_model("hello")
        mock_litellm.assert_called_once_with("hello", "openai/gpt-4o-mini")
        assert result == "ok"

    def test_routes_to_anthropic_sdk_when_key_set(self):
        env = {'AICONTEXT_MODEL': '', 'ANTHROPIC_API_KEY': 'sk-test'}
        with patch.dict('os.environ', env, clear=False):
            with patch('cram.utils._call_via_anthropic_sdk', return_value='ok') as mock_sdk:
                result = call_model("hello")
        mock_sdk.assert_called_once_with("hello", "")
        assert result == "ok"

    def test_routes_to_cli_when_no_key(self):
        env = {'AICONTEXT_MODEL': ''}
        with patch.dict('os.environ', env, clear=False):
            # Remove ANTHROPIC_API_KEY if present
            with patch('os.environ.get') as mock_get:
                mock_get.side_effect = lambda k, d='': {
                    'AICONTEXT_MODEL': '',
                    'ANTHROPIC_API_KEY': '',
                }.get(k, d)
                with patch('cram.utils._call_via_cli', return_value='ok') as mock_cli:
                    call_model("hello")
        mock_cli.assert_called_once()

    def test_litellm_takes_priority_over_api_key(self):
        env = {'AICONTEXT_MODEL': 'google/gemini-2.5-flash', 'ANTHROPIC_API_KEY': 'sk-test'}
        with patch.dict('os.environ', env, clear=False):
            with patch('cram.utils._call_via_litellm', return_value='ok') as mock_litellm:
                with patch('cram.utils._call_via_anthropic_sdk') as mock_sdk:
                    call_model("hello")
        mock_litellm.assert_called_once()
        mock_sdk.assert_not_called()

    def test_bare_model_alias_goes_to_cli_without_api_key(self):
        with patch.dict('os.environ', {'AICONTEXT_MODEL': 'haiku'}, clear=False):
            with patch('os.environ.get') as mock_get:
                mock_get.side_effect = lambda k, d='': {
                    'AICONTEXT_MODEL': 'haiku',
                    'ANTHROPIC_API_KEY': '',
                }.get(k, d)
                with patch('cram.utils._call_via_cli', return_value='ok') as mock_cli:
                    call_model("hello")
        mock_cli.assert_called_once()


class TestCallViaLitellmMissing:
    def test_exits_when_litellm_not_installed(self):
        with patch.dict('sys.modules', {'litellm': None}):
            with pytest.raises(SystemExit):
                from cram.utils import _call_via_litellm
                _call_via_litellm("hi", "openai/gpt-4o-mini")


class TestProxyHeaders:
    """proxy.base_url and proxy.headers thread through to litellm.completion."""

    def _make_litellm_mock(self):
        mock_litellm = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = 'response text'
        mock_litellm.completion.return_value = mock_response
        return mock_litellm

    def test_proxy_base_url_sets_api_base(self, tmp_path):
        from cram.utils import _call_via_litellm
        mock_litellm = self._make_litellm_mock()
        settings = {'proxy': {'base_url': 'https://gateway.corp/v1'}}
        with patch('cram.utils.load_settings', return_value=settings):
            with patch.dict('sys.modules', {'litellm': mock_litellm}):
                _call_via_litellm('hello', 'openai/gpt-4o')
        call_kwargs = mock_litellm.completion.call_args[1]
        assert call_kwargs['api_base'] == 'https://gateway.corp/v1'

    def test_proxy_base_url_uses_dummy_key_when_no_api_key(self, tmp_path):
        from cram.utils import _call_via_litellm
        mock_litellm = self._make_litellm_mock()
        settings = {'proxy': {'base_url': 'https://gateway.corp/v1'}}
        with patch('cram.utils.load_settings', return_value=settings):
            with patch.dict('sys.modules', {'litellm': mock_litellm}):
                _call_via_litellm('hello', 'openai/gpt-4o')
        call_kwargs = mock_litellm.completion.call_args[1]
        assert call_kwargs['api_key'] == 'no-key'

    def test_proxy_api_key_used_when_provided(self, tmp_path):
        from cram.utils import _call_via_litellm
        mock_litellm = self._make_litellm_mock()
        settings = {'proxy': {'base_url': 'https://gateway.corp/v1', 'api_key': 'mytoken'}}
        with patch('cram.utils.load_settings', return_value=settings):
            with patch.dict('sys.modules', {'litellm': mock_litellm}):
                _call_via_litellm('hello', 'openai/gpt-4o')
        call_kwargs = mock_litellm.completion.call_args[1]
        assert call_kwargs['api_key'] == 'mytoken'

    def test_proxy_headers_set_as_extra_headers(self, tmp_path):
        from cram.utils import _call_via_litellm
        mock_litellm = self._make_litellm_mock()
        settings = {'proxy': {
            'base_url': 'https://gateway.corp/v1',
            'headers': {'X-Corp-Token': 'abc123', 'X-Tenant': 'acme'},
        }}
        with patch('cram.utils.load_settings', return_value=settings):
            with patch.dict('sys.modules', {'litellm': mock_litellm}):
                _call_via_litellm('hello', 'openai/gpt-4o')
        call_kwargs = mock_litellm.completion.call_args[1]
        assert call_kwargs['extra_headers'] == {'X-Corp-Token': 'abc123', 'X-Tenant': 'acme'}

    def test_no_proxy_config_no_extra_kwargs(self, tmp_path):
        from cram.utils import _call_via_litellm
        mock_litellm = self._make_litellm_mock()
        with patch('cram.utils.load_settings', return_value={}):
            with patch.dict('sys.modules', {'litellm': mock_litellm}):
                _call_via_litellm('hello', 'openai/gpt-4o')
        call_kwargs = mock_litellm.completion.call_args[1]
        assert 'api_base' not in call_kwargs
        assert 'extra_headers' not in call_kwargs
        assert 'api_key' not in call_kwargs

    def test_headers_without_base_url_still_applied(self, tmp_path):
        from cram.utils import _call_via_litellm
        mock_litellm = self._make_litellm_mock()
        settings = {'proxy': {'headers': {'Authorization': 'Bearer tok'}}}
        with patch('cram.utils.load_settings', return_value=settings):
            with patch.dict('sys.modules', {'litellm': mock_litellm}):
                _call_via_litellm('hello', 'openai/gpt-4o')
        call_kwargs = mock_litellm.completion.call_args[1]
        assert call_kwargs['extra_headers'] == {'Authorization': 'Bearer tok'}
        assert 'api_base' not in call_kwargs


# ---------------------------------------------------------------------------
# _probe_lmstudio
# ---------------------------------------------------------------------------

class TestProbeLmStudio:
    def _make_response(self, model_ids: list[str]) -> bytes:
        return json.dumps({'data': [{'id': mid} for mid in model_ids]}).encode()

    def test_returns_models_on_success(self):
        from cram.utils import _probe_lmstudio
        from unittest.mock import patch, MagicMock
        cm = MagicMock()
        cm.__enter__ = lambda s: s
        cm.__exit__ = MagicMock(return_value=False)
        cm.read.return_value = self._make_response(['mistral-7b-instruct', 'llama3-8b'])
        with patch('urllib.request.urlopen', return_value=cm):
            result = _probe_lmstudio('http://localhost:1234')
        assert len(result) == 2
        ids = {m['id'] for m in result}
        assert 'lmstudio/mistral-7b-instruct' in ids
        assert 'lmstudio/llama3-8b' in ids

    def test_all_models_are_free(self):
        from cram.utils import _probe_lmstudio
        cm = MagicMock()
        cm.__enter__ = lambda s: s
        cm.__exit__ = MagicMock(return_value=False)
        cm.read.return_value = self._make_response(['some-model'])
        with patch('urllib.request.urlopen', return_value=cm):
            result = _probe_lmstudio()
        assert all(m['cost'] == 0 for m in result)
        assert all(m['provider'] == 'lmstudio' for m in result)

    def test_returns_empty_on_connection_refused(self):
        from cram.utils import _probe_lmstudio
        with patch('urllib.request.urlopen', side_effect=OSError('refused')):
            assert _probe_lmstudio() == []

    def test_size_tier_small_is_context(self):
        from cram.utils import _probe_lmstudio
        cm = MagicMock()
        cm.__enter__ = lambda s: s
        cm.__exit__ = MagicMock(return_value=False)
        cm.read.return_value = self._make_response(['llama-3b', 'llama-70b'])
        with patch('urllib.request.urlopen', return_value=cm):
            result = _probe_lmstudio()
        by_id = {m['id']: m for m in result}
        assert by_id['lmstudio/llama-3b']['tier'] == 'context'
        assert by_id['lmstudio/llama-70b']['tier'] == 'coding'


# ---------------------------------------------------------------------------
# _call_via_openai_compat
# ---------------------------------------------------------------------------

class TestCallViaOpenaiCompat:
    def _mock_urlopen(self, response_text: str):
        cm = MagicMock()
        cm.__enter__ = lambda s: s
        cm.__exit__ = MagicMock(return_value=False)
        cm.read.return_value = json.dumps({
            'choices': [{'message': {'content': response_text}}]
        }).encode()
        return cm

    def test_posts_to_chat_completions(self):
        from cram.utils import _call_via_openai_compat
        cm = self._mock_urlopen('hello back')
        with patch('urllib.request.urlopen', return_value=cm) as mock_open:
            result = _call_via_openai_compat('hello', 'my-model', 'http://localhost:1234')
        assert result == 'hello back'
        req = mock_open.call_args[0][0]
        assert req.full_url == 'http://localhost:1234/v1/chat/completions'

    def test_sets_authorization_header_when_key_given(self):
        from cram.utils import _call_via_openai_compat
        cm = self._mock_urlopen('hi')
        with patch('urllib.request.urlopen', return_value=cm) as mock_open:
            _call_via_openai_compat('hi', 'model', 'http://host', api_key='mykey')
        req = mock_open.call_args[0][0]
        assert req.get_header('Authorization') == 'Bearer mykey'

    def test_no_auth_header_when_no_key(self):
        from cram.utils import _call_via_openai_compat
        cm = self._mock_urlopen('ok')
        with patch('urllib.request.urlopen', return_value=cm) as mock_open:
            _call_via_openai_compat('ok', 'model', 'http://host')
        req = mock_open.call_args[0][0]
        assert req.get_header('Authorization') is None

    def test_trailing_slash_stripped_from_base_url(self):
        from cram.utils import _call_via_openai_compat
        cm = self._mock_urlopen('ok')
        with patch('urllib.request.urlopen', return_value=cm) as mock_open:
            _call_via_openai_compat('ok', 'model', 'http://host:1234/')
        req = mock_open.call_args[0][0]
        assert req.full_url == 'http://host:1234/v1/chat/completions'


# ---------------------------------------------------------------------------
# _call_via_gemini
# ---------------------------------------------------------------------------

class TestCallViaGemini:
    def test_uses_sdk_when_available(self, monkeypatch):
        from cram.utils import _call_via_gemini
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')

        mock_genai = MagicMock()
        mock_model_inst = MagicMock()
        mock_model_inst.generate_content.return_value.text = '  sdk response  '
        mock_genai.GenerativeModel.return_value = mock_model_inst

        # For `import google.generativeai as genai`, Python binds `genai` to
        # sys.modules['google'].generativeai — so the google mock must carry the attribute.
        mock_google = MagicMock()
        mock_google.generativeai = mock_genai
        with patch.dict('sys.modules', {'google': mock_google,
                                         'google.generativeai': mock_genai}):
            result = _call_via_gemini('hello', 'gemini/gemini-2.0-flash')

        assert result == 'sdk response'
        mock_genai.configure.assert_called_once_with(api_key='test-key')
        mock_genai.GenerativeModel.assert_called_once_with('gemini-2.0-flash')

    def test_http_fallback_when_sdk_missing(self, monkeypatch):
        from cram.utils import _call_via_gemini
        monkeypatch.setenv('GEMINI_API_KEY', 'test-key')

        response_body = json.dumps({
            'candidates': [{'content': {'parts': [{'text': 'http response'}]}}]
        }).encode()
        cm = MagicMock()
        cm.__enter__ = lambda s: s
        cm.__exit__ = MagicMock(return_value=False)
        cm.read.return_value = response_body

        # Setting entries to None causes ImportError on `import google.generativeai`
        with patch.dict('sys.modules', {'google': None, 'google.generativeai': None}):
            with patch('urllib.request.urlopen', return_value=cm):
                result = _call_via_gemini('hello', 'gemini/gemini-2.0-flash')

        assert result == 'http response'

    def test_bare_model_name_accepted(self, monkeypatch):
        """Model passed without 'gemini/' prefix still works."""
        from cram.utils import _call_via_gemini
        monkeypatch.setenv('GEMINI_API_KEY', 'key')

        mock_genai = MagicMock()
        mock_model_inst = MagicMock()
        mock_model_inst.generate_content.return_value.text = 'ok'
        mock_genai.GenerativeModel.return_value = mock_model_inst

        mock_google = MagicMock()
        mock_google.generativeai = mock_genai
        with patch.dict('sys.modules', {'google': mock_google,
                                         'google.generativeai': mock_genai}):
            _call_via_gemini('hi', 'gemini-2.0-flash')  # no prefix

        mock_genai.GenerativeModel.assert_called_once_with('gemini-2.0-flash')

    def test_vertex_ai_prefix_delegates_to_litellm(self, monkeypatch):
        from cram.utils import _call_via_gemini
        with patch('cram.utils._call_via_litellm', return_value='litellm-resp') as mock_ll:
            result = _call_via_gemini('hi', 'vertex_ai/gemini-2.5-pro')
        assert result == 'litellm-resp'
        mock_ll.assert_called_once_with('hi', 'vertex_ai/gemini-2.5-pro')


# ---------------------------------------------------------------------------
# call_context_model routing — gemini and lmstudio
# ---------------------------------------------------------------------------

class TestCallContextModelRouting:
    def test_codex_cli_prefix_routes_to_codex_caller(self):
        from cram.utils import call_context_model
        with patch('cram.utils.load_settings', return_value={'context_model': 'codex-cli/default'}):
            with patch('cram.utils._call_via_codex_cli', return_value='codex') as mock_codex:
                result = call_context_model('prompt')
        assert result == 'codex'
        mock_codex.assert_called_once_with('prompt', 'default')

    def test_gemini_prefix_routes_to_call_via_gemini(self):
        from cram.utils import call_context_model
        with patch('cram.utils.load_settings', return_value={'context_model': 'gemini/gemini-2.0-flash'}):
            with patch('cram.utils._call_via_gemini', return_value='gem') as mock_g:
                result = call_context_model('prompt')
        assert result == 'gem'
        mock_g.assert_called_once_with('prompt', 'gemini/gemini-2.0-flash')

    def test_lmstudio_prefix_routes_to_openai_compat(self):
        from cram.utils import call_context_model
        settings = {'context_model': 'lmstudio/my-model', 'lmstudio_url': 'http://localhost:1234'}
        with patch('cram.utils.load_settings', return_value=settings):
            with patch('cram.utils._call_via_openai_compat', return_value='lms') as mock_lms:
                result = call_context_model('prompt')
        assert result == 'lms'
        mock_lms.assert_called_once_with('prompt', 'my-model', 'http://localhost:1234')

    def test_lmstudio_default_url_used_when_not_configured(self):
        from cram.utils import call_context_model
        with patch('cram.utils.load_settings', return_value={'context_model': 'lmstudio/m'}):
            with patch('cram.utils._call_via_openai_compat', return_value='ok') as mock_lms:
                call_context_model('hi')
        assert mock_lms.call_args[0][2] == 'http://localhost:1234'

    def test_vertex_ai_prefix_routes_to_call_via_gemini(self):
        from cram.utils import call_context_model
        with patch('cram.utils.load_settings', return_value={'context_model': 'vertex_ai/gemini-2.5-pro'}):
            with patch('cram.utils._call_via_gemini', return_value='vx'):
                result = call_context_model('hi')
        assert result == 'vx'

    def test_auto_context_model_can_choose_codex_cli(self):
        from cram.utils import call_context_model
        codex = {'id': 'codex-cli/default', 'provider': 'codex-cli', 'tier': 'context',
                 'name': 'Codex CLI (codex exec)', 'cost': 0, 'quality': 4}
        claude = {'id': 'cli/haiku', 'provider': 'claude-cli', 'tier': 'context',
                  'name': 'Claude Haiku (claude CLI)', 'cost': 0, 'quality': 2}
        with patch('cram.utils.load_settings', return_value={'context_model': 'auto'}), \
             patch('cram.utils.discover_models', return_value=[claude, codex]), \
             patch('cram.utils._prefer_codex_context', return_value=True), \
             patch('cram.utils._call_via_codex_cli', return_value='ok') as mock_codex:
            result = call_context_model('hi')
        assert result == 'ok'
        mock_codex.assert_called_once_with('hi', 'default')


# ---------------------------------------------------------------------------
# discover_models includes LM Studio
# ---------------------------------------------------------------------------

class TestDiscoverModelsLmStudio:
    def test_codex_cli_included_when_available(self):
        from cram.utils import discover_models
        with patch('cram.utils._check_claude_cli', return_value=False), \
             patch('cram.utils._check_codex_cli', return_value=True), \
             patch('cram.utils._probe_ollama', return_value=[]), \
             patch('cram.utils._probe_lmstudio', return_value=[]), \
             patch('cram.utils._check_aws_credentials', return_value=False), \
             patch('cram.utils._check_gcp_credentials', return_value=False), \
             patch('cram.utils._check_azure_credentials', return_value=False), \
             patch('cram.utils.load_settings', return_value={}), \
             patch.dict('os.environ', {}, clear=True):
            result = discover_models()
        assert any(m['id'] == 'codex-cli/default' for m in result)

    def test_lmstudio_included_when_running(self):
        from cram.utils import discover_models
        lmstudio_model = {
            'id': 'lmstudio/llama3-8b', 'name': 'llama3-8b (local LM Studio)',
            'provider': 'lmstudio', 'tier': 'context', 'cost': 0, 'quality': 4,
            'base_url': 'http://localhost:1234',
        }
        with patch('cram.utils._check_claude_cli', return_value=False), \
             patch('cram.utils._check_codex_cli', return_value=False), \
             patch('cram.utils._probe_ollama', return_value=[]), \
             patch('cram.utils._probe_lmstudio', return_value=[lmstudio_model]), \
             patch('cram.utils._check_aws_credentials', return_value=False), \
             patch('cram.utils._check_gcp_credentials', return_value=False), \
             patch('cram.utils._check_azure_credentials', return_value=False), \
             patch('cram.utils.load_settings', return_value={}), \
             patch.dict('os.environ', {}, clear=True):
            result = discover_models()
        assert any(m['provider'] == 'lmstudio' for m in result)

    def test_lmstudio_not_included_when_offline(self):
        from cram.utils import discover_models
        with patch('cram.utils._check_claude_cli', return_value=False), \
             patch('cram.utils._check_codex_cli', return_value=False), \
             patch('cram.utils._probe_ollama', return_value=[]), \
             patch('cram.utils._probe_lmstudio', return_value=[]), \
             patch('cram.utils._check_aws_credentials', return_value=False), \
             patch('cram.utils._check_gcp_credentials', return_value=False), \
             patch('cram.utils._check_azure_credentials', return_value=False), \
             patch('cram.utils.load_settings', return_value={}), \
             patch.dict('os.environ', {}, clear=True):
            result = discover_models()
        assert not any(m['provider'] == 'lmstudio' for m in result)


class TestPickContextModel:
    def test_prefers_codex_cli_when_codex_context_preferred(self):
        from cram.utils import pick_context_model
        codex = {'id': 'codex-cli/default', 'provider': 'codex-cli', 'tier': 'context',
                 'name': 'Codex CLI (codex exec)', 'cost': 0, 'quality': 4}
        claude = {'id': 'cli/haiku', 'provider': 'claude-cli', 'tier': 'context',
                  'name': 'Claude Haiku (claude CLI)', 'cost': 0, 'quality': 2}
        with patch('cram.utils._prefer_codex_context', return_value=True):
            assert pick_context_model([claude, codex]) == codex

    def test_keeps_claude_cli_default_when_codex_not_preferred(self):
        from cram.utils import pick_context_model
        codex = {'id': 'codex-cli/default', 'provider': 'codex-cli', 'tier': 'context',
                 'name': 'Codex CLI (codex exec)', 'cost': 0, 'quality': 4}
        claude = {'id': 'cli/haiku', 'provider': 'claude-cli', 'tier': 'context',
                  'name': 'Claude Haiku (claude CLI)', 'cost': 0, 'quality': 2}
        with patch('cram.utils._prefer_codex_context', return_value=False):
            assert pick_context_model([codex, claude]) == claude
