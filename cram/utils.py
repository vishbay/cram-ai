"""Shared model backend — provider-agnostic routing with auto-discovery."""

from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

CLAUDE_BIN = os.environ.get('CLAUDE_CODE_EXECPATH', 'claude')

_SETTINGS_FILE = Path.home() / '.config' / 'cram-ai' / 'settings.json'

# (id, display_name, tier, cost_per_mtok_input, quality_rank)
# tier: 'context' = cheap/fast retrieval; 'coding' = capable implementation
_CATALOGUE: dict[str, list[tuple]] = {
    'claude-cli': [
        ('cli/haiku',  'Claude Haiku (claude CLI)',  'context', 0,    2),
        ('cli/sonnet', 'Claude Sonnet (claude CLI)', 'coding',  0,    6),
        ('cli/opus',   'Claude Opus (claude CLI)',   'coding',  0,    9),
    ],
    'anthropic': [
        ('anthropic/claude-haiku-4-5-20251001', 'Claude Haiku 4.5',  'context', 0.80,  2),
        ('anthropic/claude-sonnet-4-6',         'Claude Sonnet 4.6', 'coding',  3.00,  6),
        ('anthropic/claude-opus-4-8',           'Claude Opus 4.8',   'coding',  15.00, 9),
    ],
    'openai': [
        ('openai/gpt-4o-mini', 'GPT-4o Mini', 'context', 0.15, 2),
        ('openai/gpt-4o',      'GPT-4o',      'coding',  5.00, 6),
    ],
    'gemini': [
        ('gemini/gemini-2.0-flash-lite', 'Gemini 2.0 Flash Lite', 'context', 0.08, 2),
        ('gemini/gemini-2.0-flash',      'Gemini 2.0 Flash',      'context', 0.40, 3),
        ('gemini/gemini-2.5-pro',        'Gemini 2.5 Pro',        'coding',  3.50, 7),
    ],
    'bedrock': [
        ('bedrock/anthropic.claude-3-haiku-20240307-v1:0',           'Claude Haiku (Bedrock)',  'context', 0.25, 2),
        ('bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0',        'Claude Sonnet (Bedrock)', 'coding',  3.00, 6),
    ],
    'vertex_ai': [
        ('vertex_ai/gemini-2.0-flash',            'Gemini Flash (Vertex)',    'context', 0.40, 3),
        ('vertex_ai/gemini-2.5-pro-preview-05-06','Gemini 2.5 Pro (Vertex)', 'coding',  3.50, 7),
    ],
    'azure': [
        ('azure/gpt-4o-mini', 'GPT-4o Mini (Azure)', 'context', 0.15, 2),
        ('azure/gpt-4o',      'GPT-4o (Azure)',       'coding',  5.00, 6),
    ],
}


# ── settings I/O ──────────────────────────────────────────────────

def load_settings() -> dict:
    try:
        with open(_SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(updates: dict) -> None:
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = load_settings()
    existing.update(updates)
    with open(_SETTINGS_FILE, 'w') as f:
        json.dump(existing, f, indent=2)


# ── credential probes ─────────────────────────────────────────────

def _check_claude_cli() -> bool:
    try:
        r = subprocess.run([CLAUDE_BIN, '--version'], capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


_OLLAMA_EMBED_PATTERNS = ('embed', 'clip', 'rerank', 'nomic-embed', 'bge-', 'e5-')


def _probe_ollama(base_url: str = 'http://localhost:11434') -> list[dict]:
    try:
        with urllib.request.urlopen(f'{base_url}/api/tags', timeout=2) as r:
            data = json.loads(r.read())
        result = []
        for m in data.get('models', []):
            name = m['name']
            name_lower = name.lower()
            # Skip embedding / reranking models — not text generators
            if any(p in name_lower for p in _OLLAMA_EMBED_PATTERNS):
                continue
            size_match = re.search(r'(\d+)b', name_lower)
            size = int(size_match.group(1)) if size_match else 7
            result.append({
                'id':       f'ollama/{name}',
                'name':     f'{name} (local Ollama)',
                'provider': 'ollama',
                'tier':     'context' if size <= 8 else 'coding',
                'cost':     0,
                'quality':  min(2 + size // 4, 8),
                'base_url': base_url,
            })
        return result
    except Exception:
        return []


def _probe_lmstudio(base_url: str = 'http://localhost:1234') -> list[dict]:
    """Probe LM Studio for loaded models via its OpenAI-compatible /v1/models endpoint."""
    try:
        with urllib.request.urlopen(f'{base_url}/v1/models', timeout=2) as r:
            data = json.loads(r.read())
        result = []
        for m in data.get('data', []):
            mid = m.get('id', '')
            if not mid:
                continue
            name_lower = mid.lower()
            size_match = re.search(r'(\d+)b', name_lower)
            size = int(size_match.group(1)) if size_match else 7
            result.append({
                'id':       f'lmstudio/{mid}',
                'name':     f'{mid} (local LM Studio)',
                'provider': 'lmstudio',
                'tier':     'context' if size <= 8 else 'coding',
                'cost':     0,
                'quality':  min(2 + size // 4, 8),
                'base_url': base_url,
            })
        return result
    except Exception:
        return []


def _check_aws_credentials() -> bool:
    if os.environ.get('AWS_ACCESS_KEY_ID') or os.environ.get('AWS_PROFILE'):
        return True
    return (Path.home() / '.aws' / 'credentials').exists() or \
           (Path.home() / '.aws' / 'config').exists()


def _check_gcp_credentials() -> bool:
    if os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        return True
    return (Path.home() / '.config' / 'gcloud' / 'application_default_credentials.json').exists()


def _check_azure_credentials() -> bool:
    return bool(os.environ.get('AZURE_OPENAI_ENDPOINT'))


# ── discovery ─────────────────────────────────────────────────────

def discover_models() -> list[dict]:
    """Discover available models across all providers, sorted cheapest first."""
    available: list[dict] = []

    def _add(provider: str) -> None:
        for mid, name, tier, cost, quality in _CATALOGUE[provider]:
            available.append({'id': mid, 'name': name, 'provider': provider,
                               'tier': tier, 'cost': cost, 'quality': quality})

    # 1. Claude CLI — free, works inside Claude Code with no API key
    if _check_claude_cli():
        _add('claude-cli')

    # 2. Ollama — local, free
    settings = load_settings()
    available.extend(_probe_ollama(settings.get('ollama_url', 'http://localhost:11434')))

    # 3. LM Studio — local, free, OpenAI-compatible (default port 1234)
    available.extend(_probe_lmstudio(settings.get('lmstudio_url', 'http://localhost:1234')))

    # 4. Enterprise: AWS Bedrock (IAM / instance role, no API key needed)
    if _check_aws_credentials():
        _add('bedrock')

    # 5. Enterprise: GCP Vertex AI (ADC / service account, no API key needed)
    if _check_gcp_credentials():
        _add('vertex_ai')

    # 6. Enterprise: Azure OpenAI (managed identity / AZURE_OPENAI_ENDPOINT)
    if _check_azure_credentials():
        _add('azure')

    # 7. Direct API keys
    if os.environ.get('ANTHROPIC_API_KEY'):
        _add('anthropic')
    if os.environ.get('OPENAI_API_KEY'):
        _add('openai')
    if os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY'):
        _add('gemini')

    # 8. Custom proxy (corporate gateway, LiteLLM proxy, etc.)
    proxy = settings.get('proxy', {})
    if proxy.get('base_url'):
        available.append({
            'id':       'proxy/custom',
            'name':     f"Custom proxy ({proxy['base_url']})",
            'provider': 'proxy',
            'tier':     'both',
            'cost':     0,
            'quality':  5,
        })

    # Sort: free first, then cheapest, then highest quality within same cost
    return sorted(available, key=lambda m: (m['cost'], -m['quality']))


def pick_context_model(available: list[dict]) -> dict | None:
    """Cheapest model suitable for context/retrieval tasks.

    The Claude CLI is free inside Claude Code and produces far more reliable
    doc/context regens than a small local model, so prefer it when present —
    local Ollama/LM Studio models (slow, low-quality on large prompts like an
    ARCHITECTURE.md rewrite) are kept only as the fallback.
    """
    candidates = [m for m in available if m['tier'] in ('context', 'both')]
    if not candidates:
        return available[0] if available else None
    cli = [m for m in candidates if m['provider'] == 'claude-cli']
    return cli[0] if cli else candidates[0]


def pick_coding_model(available: list[dict]) -> dict | None:
    """Highest quality model for coding tasks."""
    candidates = [m for m in available if m['tier'] in ('coding', 'both')]
    if not candidates:
        candidates = available
    return max(candidates, key=lambda m: m['quality']) if candidates else None


def cache_min_tokens(model_name: str) -> int:
    """Minimum prefix tokens required for prompt caching to activate for this model."""
    return 4096 if 'opus' in model_name.lower() else 1024


def get_model_recommendations() -> tuple[str, str]:
    """Return (context_model_name, coding_model_name) for display."""
    settings = load_settings()
    available = discover_models()

    ctx_id = settings.get('context_model', 'auto')
    cod_id = settings.get('coding_model',  'auto')

    def _name(model_id: str, picker) -> str:
        if model_id == 'auto':
            m = picker(available)
            return m['name'] if m else 'none found'
        m = next((x for x in available if x['id'] == model_id), None)
        return m['name'] if m else model_id

    return _name(ctx_id, pick_context_model), _name(cod_id, pick_coding_model)


# ── call_context_model ────────────────────────────────────────────

def call_context_model(prompt: str) -> str:
    """Route to the cheapest available model for context/retrieval tasks.

    Priority:
    1. settings.json context_model (explicit user choice)
    2. Auto-discover cheapest available provider
    3. Fall back to call_model()
    """
    settings = load_settings()
    model_id = settings.get('context_model', 'auto')

    if model_id == 'auto':
        available = discover_models()
        best = pick_context_model(available)
        model_id = best['id'] if best else ''

    if not model_id or model_id == 'auto':
        return call_model(prompt)

    if model_id.startswith('cli/'):
        return _call_via_cli(prompt, model_id.split('/', 1)[1])
    if model_id.startswith('ollama/'):
        settings = load_settings()
        base_url = settings.get('ollama_url', 'http://localhost:11434')
        return _call_via_ollama(prompt, model_id[len('ollama/'):], base_url)
    if model_id.startswith('lmstudio/'):
        settings = load_settings()
        base_url = settings.get('lmstudio_url', 'http://localhost:1234')
        return _call_via_openai_compat(prompt, model_id[len('lmstudio/'):], base_url)
    if model_id.startswith('gemini/') or model_id.startswith('vertex_ai/'):
        return _call_via_gemini(prompt, model_id)
    if '/' in model_id:
        return _call_via_litellm(prompt, model_id)
    return _call_via_cli(prompt, model_id)


# ── existing call_model (kept for backwards compat) ───────────────

def call_model(prompt: str) -> str:
    """Send a prompt and return the response text.

    Routing priority:
    1. AICONTEXT_MODEL contains '/'  → litellm (any provider)
    2. ANTHROPIC_API_KEY is set      → Anthropic SDK directly
    3. Fallback                      → claude -p (Claude Code session, no key needed)
    """
    model = os.environ.get('AICONTEXT_MODEL', '')

    if '/' in model:
        return _call_via_litellm(prompt, model)

    if os.environ.get('ANTHROPIC_API_KEY'):
        return _call_via_anthropic_sdk(prompt, model)

    return _call_via_cli(prompt, model)


# ── shared helpers ────────────────────────────────────────────────

def find_git_root(path: str = '.') -> str:
    current = os.path.abspath(path)
    while True:
        if os.path.isdir(os.path.join(current, '.git')):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(path)
        current = parent


def strip_code_fence(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip() == '```':
        lines = lines[:-1]
    if lines and not lines[0].startswith('#') and lines[0].endswith(':'):
        lines = lines[1:]
    return '\n'.join(lines).strip()


def _ollama_timeout() -> int:
    """Per-request timeout for Ollama, in seconds. Local models — especially
    reasoning models on large prompts — can be slow, so the default is
    generous and overridable via CRAM_OLLAMA_TIMEOUT or settings.ollama_timeout."""
    env = os.environ.get('CRAM_OLLAMA_TIMEOUT')
    if env and env.isdigit():
        return int(env)
    try:
        return int(load_settings().get('ollama_timeout', 300))
    except (TypeError, ValueError):
        return 300


def _strip_think_blocks(text: str) -> str:
    """Drop <think>...</think> spans emitted by reasoning models (qwen3 etc.)."""
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()


def _call_via_ollama(prompt: str, model: str, base_url: str = 'http://localhost:11434') -> str:
    # think=False keeps reasoning models (qwen3, ...) from spending the whole
    # budget on <think> traces; ignored by non-reasoning models.
    payload = json.dumps(
        {'model': model, 'prompt': prompt, 'stream': False, 'think': False}
    ).encode()
    req = urllib.request.Request(
        f'{base_url}/api/generate',
        data=payload,
        headers={'Content-Type': 'application/json'},
    )
    timeout = _ollama_timeout()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            response = json.loads(r.read())['response']
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        raise RuntimeError(
            f"Ollama model '{model}' did not respond within {timeout}s "
            f"({base_url}): {e}. Local reasoning models can be slow on large "
            f"prompts — raise CRAM_OLLAMA_TIMEOUT, pick a faster context_model "
            f"in settings, or check that Ollama is running."
        ) from e
    return _strip_think_blocks(response)


def _call_via_openai_compat(prompt: str, model: str, base_url: str,
                            api_key: str = '') -> str:
    """Call any OpenAI-compatible API (LM Studio, vLLM, Jan, etc.) via raw HTTP."""
    url = base_url.rstrip('/') + '/v1/chat/completions'
    payload = json.dumps({
        'model':      model,
        'messages':   [{'role': 'user', 'content': prompt}],
        'max_tokens': 1500,
        'stream':     False,
    }).encode()
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return data['choices'][0]['message']['content'].strip()


def _call_via_gemini(prompt: str, model: str) -> str:
    """Call Google Gemini API directly — no litellm required.

    Tries the google-generativeai SDK first; falls back to raw HTTPS if
    the package is not installed. API key read from GEMINI_API_KEY or
    GOOGLE_API_KEY. Routes through litellm for vertex_ai/ prefix (GCP ADC).
    """
    # vertex_ai models require GCP credentials + litellm; delegate
    if model.startswith('vertex_ai/'):
        return _call_via_litellm(prompt, model)

    bare = model.split('/', 1)[1] if '/' in model else model
    api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY', '')

    if not api_key:
        # No key — may work via litellm if ADC is configured
        return _call_via_litellm(prompt, f'gemini/{bare}')

    # ── SDK path ──────────────────────────────────────────────────
    try:
        import google.generativeai as genai  # type: ignore[import]
        genai.configure(api_key=api_key)
        gen_model = genai.GenerativeModel(bare)
        response = gen_model.generate_content(prompt)
        return response.text.strip()
    except ImportError:
        pass  # SDK not installed — fall through to HTTP

    # ── Raw HTTPS fallback ────────────────────────────────────────
    url = (
        'https://generativelanguage.googleapis.com/v1beta/models/'
        f'{bare}:generateContent?key={api_key}'
    )
    payload = json.dumps({
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'maxOutputTokens': 1500},
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return data['candidates'][0]['content']['parts'][0]['text'].strip()


def _call_via_litellm(prompt: str, model: str) -> str:
    try:
        import litellm
    except ImportError:
        print(
            "litellm not installed. Run: pip install litellm\n"
            "Or set AICONTEXT_MODEL to a bare alias (haiku) to use the claude CLI.",
            file=sys.stderr,
        )
        sys.exit(1)

    proxy = load_settings().get('proxy', {})
    kwargs: dict = {
        'model':      model,
        'messages':   [{"role": "user", "content": prompt}],
        'max_tokens': 1500,
    }
    if proxy.get('base_url'):
        kwargs['api_base'] = proxy['base_url']
        # Keyless gateways (SSO/corp token) need a dummy key so litellm doesn't abort
        kwargs['api_key'] = proxy.get('api_key') or 'no-key'
    if proxy.get('headers'):
        kwargs['extra_headers'] = dict(proxy['headers'])

    response = litellm.completion(**kwargs)
    return response.choices[0].message.content.strip()


def _call_via_anthropic_sdk(prompt: str, model: str) -> str:
    import anthropic
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model or 'claude-haiku-4-5-20251001',
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _call_via_cli(prompt: str, model: str) -> str:
    system = (
        "You are a text generator. Output ONLY the exact content requested. "
        "Never use tools. Never ask for permission. Never describe what you will do. "
        "Never write file paths. Just return the raw text content directly."
    )
    try:
        result = subprocess.run(
            [CLAUDE_BIN, '-p', '-', '--model', model or 'haiku',
             '--output-format', 'text', '--input-format', 'text',
             '--allowedTools', '',
             '--append-system-prompt', system],
            input=prompt,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error calling claude CLI: {e.stderr}", file=sys.stderr)
        raise
    except FileNotFoundError:
        raise RuntimeError(
            "No ANTHROPIC_API_KEY set and `claude` CLI not found. "
            "Set ANTHROPIC_API_KEY or AICONTEXT_MODEL=provider/model and the matching API key."
        )
