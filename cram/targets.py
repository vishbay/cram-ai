"""Write task context to each AI tool's auto-loaded instruction file."""

from __future__ import annotations
import os
import re

from cram.context_dir import resolve_context_dir

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

CRAM_SECTION_START = "<!-- cram-ai: start -->"
CRAM_SECTION_END   = "<!-- cram-ai: end -->"

# Written to CLAUDE.md instead of injecting task content.
# Keeps the prefix tiny — real context comes from get_context() via MCP.
CLAUDE_MCP_POINTER = (
    "cram-ai context is served via the MCP server — not this file.\n\n"
    "IMPORTANT: Call get_context() as your FIRST action in every session,\n"
    "before answering any question or writing any code. Pass the task description\n"
    'as the argument (e.g. get_context("fix the rate limiter")), or call with no\n'
    "arguments to reload the last task's context.\n\n"
    "Add cram-ai to your .claude/settings.json:\n"
    "  {\n"
    '    "mcpServers": {\n'
    '      "cram-ai": {\n'
    '        "command": "cram",\n'
    '        "args": ["mcp", "--repo", "/absolute/path/to/this/repo"]\n'
    "      }\n"
    "    }\n"
    "  }\n"
)

# Each entry is a cram-owned file the tool reads automatically.
# cram overwrites it on every `cram task` run — no shared config is ever touched.
TARGET_FILES: dict[str, str] = {
    "cursor":   ".cursor/rules/cram-task.md",      # Cursor reads all files in .cursor/rules/
    "claude":   "CLAUDE.md",                        # Claude Code reads CLAUDE.md from the project root
    "copilot":  ".github/cram-task.md",             # Requires one-time include in copilot-instructions.md
    "codex":    "AGENTS.md",                        # Codex reads AGENTS.md from the repo root
    "windsurf": ".windsurf/rules/cram-task.md",    # Windsurf reads all files in .windsurf/rules/
    "gemini":   "GEMINI.md",                       # Gemini CLI reads GEMINI.md from the project root
}

# File or directory whose presence indicates the tool is active in the repo
TARGET_INDICATORS: dict[str, str] = {
    "cursor":   ".cursor",
    "claude":   "CLAUDE.md",
    "copilot":  ".github",
    "codex":    "AGENTS.md",
    "windsurf": ".windsurfrules",
    "gemini":   "GEMINI.md",
}

# Targets that share a user-owned file — use cram markers to preserve user content
_UPSERT_TARGETS = frozenset({"claude", "codex", "gemini"})


def load_custom_targets(root: str) -> dict[str, dict]:
    """Load [targets.<name>] sections from config.toml.

    Each section may contain:
      file      = "ACME.md"           (required) path relative to repo root
      indicator = "acme.config.json"  (optional) file/dir that signals tool is active
      upsert    = true                (optional) use cram markers instead of overwriting

    Returns {name: {file, indicator, upsert}}.
    """
    config_path = os.path.join(resolve_context_dir(root), 'config.toml')
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, 'rb') as f:
            cfg = tomllib.load(f)
        custom: dict[str, dict] = {}
        for name, val in cfg.get('targets', {}).items():
            if isinstance(val, dict) and val.get('file'):
                custom[name] = {
                    'file':      str(val['file']),
                    'indicator': str(val['indicator']) if val.get('indicator') else None,
                    'upsert':    bool(val.get('upsert', False)),
                }
        return custom
    except Exception:
        return {}


def get_effective_targets(root: str) -> dict[str, str]:
    """Return merged {name: file_path} for builtins + custom targets."""
    merged = dict(TARGET_FILES)
    merged.update({n: d['file'] for n, d in load_custom_targets(root).items()})
    return merged


def get_effective_indicators(root: str) -> dict[str, str]:
    """Return merged {name: indicator} for builtins + custom targets that declare one."""
    merged = dict(TARGET_INDICATORS)
    for name, d in load_custom_targets(root).items():
        if d.get('indicator'):
            merged[name] = d['indicator']
    return merged


def load_output_config(root: str) -> dict:
    """Read [output] section from config.toml. Returns defaults if missing."""
    defaults = {'byte_cap': 6000, 'line_cap': 50, 'temp_file': '.cram-temp-output.txt'}
    config_path = os.path.join(resolve_context_dir(root), 'config.toml')
    if not os.path.exists(config_path):
        return defaults
    try:
        with open(config_path, 'rb') as f:
            cfg = tomllib.load(f)
        out = cfg.get('output', {})
        return {
            'byte_cap':  int(out.get('byte_cap',  defaults['byte_cap'])),
            'line_cap':  int(out.get('line_cap',   defaults['line_cap'])),
            'temp_file': str(out.get('temp_file',  defaults['temp_file'])),
        }
    except Exception:
        return defaults


def _byte_cap_block(byte_cap: int = 6000, line_cap: int = 50,
                    temp_file: str = '.cram-temp-output.txt') -> str:
    return (
        "\n## Command Output Protection\n\n"
        "Every command with unknown or potentially large output MUST be byte-capped.\n\n"
        f"Default rule: COMMAND 2>&1 | head -c {byte_cap}\n\n"
        "Safe patterns:\n"
        f"  head -n {line_cap} file.py | cat\n"
        "  git status --porcelain | head -n 30\n"
        "  git log --oneline -15\n"
        "  grep -n \"KEYWORD\" file.py | head -n 40\n\n"
        "Write-then-inspect for large outputs:\n"
        f"  COMMAND > {temp_file} 2>&1\n"
        f"  head -c {byte_cap} {temp_file}\n\n"
        "Never cat a file over 200 lines without head/tail.\n"
        f"Never run a script with unknown output without | head -c {byte_cap}.\n"
    )


def load_default_target(root: str) -> str | None:
    """Read default_target from .ai-context/config.toml, if present."""
    config_path = os.path.join(resolve_context_dir(root), 'config.toml')
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, 'rb') as f:
            cfg = tomllib.load(f)
        val = cfg.get('task', {}).get('default_target')
        return val if val in {*get_effective_targets(root), 'all'} else None
    except Exception:
        return None


def save_default_target(root: str, target: str) -> None:
    """Persist default_target to .ai-context/config.toml.

    Silently no-ops if target is invalid or the file cannot be written.
    Preserves all other content already in config.toml.
    """
    import re
    if target not in {*get_effective_targets(root), 'all'}:
        return
    config_path = os.path.join(resolve_context_dir(root), 'config.toml')
    content = ''
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                content = f.read()
        except OSError:
            return

    line = f'default_target = "{target}"'
    if re.search(r'^\s*default_target\s*=', content, re.MULTILINE):
        content = re.sub(
            r'^\s*default_target\s*=.*',
            line,
            content,
            flags=re.MULTILINE,
        )
    elif re.search(r'^\[task\]', content, re.MULTILINE):
        content = re.sub(
            r'^(\[task\])',
            lambda m: m.group(0) + f'\n{line}',
            content,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        sep = '\n\n' if content.strip() else ''
        content = content.rstrip('\n') + f'{sep}[task]\n{line}\n'

    try:
        with open(config_path, 'w') as f:
            f.write(content)
    except OSError:
        pass


def detect_targets(root: str) -> list[str]:
    """Return names of tools whose indicator file/dir exists in the repo."""
    return [t for t, ind in get_effective_indicators(root).items()
            if os.path.exists(os.path.join(root, ind))]


def _arch_header(arch_content: str, max_lines: int = 25) -> str:
    """Compact architecture preamble for tools that don't do recursive file loading."""
    lines = []
    for line in arch_content.splitlines():
        if line.strip():
            lines.append(line)
        if len(lines) >= max_lines:
            break
    if not lines:
        return ''
    return '<!-- Architecture summary (auto-generated by cram-ai) -->\n' + \
           '\n'.join(lines) + '\n\n---\n\n'


def _render(target: str, task_content: str, arch_content: str,
            output_cfg: dict | None = None) -> str:
    """Return the content to write for this target.

    For claude, content goes inside a cram-managed marker section in root CLAUDE.md
    so any existing user content is preserved. Other tools only see the single
    injected file, so we prepend a compact architecture header and append byte-cap rules.
    """
    cfg = output_cfg or {}
    cap_block = _byte_cap_block(
        byte_cap=cfg.get('byte_cap', 6000),
        line_cap=cfg.get('line_cap', 50),
        temp_file=cfg.get('temp_file', '.cram-temp-output.txt'),
    )
    if target == 'claude':
        return task_content + cap_block
    return _arch_header(arch_content) + task_content + cap_block


def _upsert_cram_section(path: str, inner_content: str) -> None:
    """Insert or replace the cram-managed section in a CLAUDE.md file.

    Preserves all content outside the cram markers. Creates the file if absent.
    """
    block = f"{CRAM_SECTION_START}\n{inner_content.rstrip()}\n{CRAM_SECTION_END}\n"
    if os.path.exists(path):
        existing = open(path).read()
        if CRAM_SECTION_START in existing:
            # Replacement passed as a function so backslashes / group-like
            # sequences in injected code excerpts are written literally instead
            # of being interpreted as re.sub escapes (which raised on inject).
            replacement = block.rstrip('\n')
            updated = re.sub(
                rf'{re.escape(CRAM_SECTION_START)}.*?{re.escape(CRAM_SECTION_END)}',
                lambda _m: replacement,
                existing,
                flags=re.DOTALL,
            )
            with open(path, 'w') as f:
                f.write(updated)
            return
        sep = '\n\n' if existing.strip() else ''
        with open(path, 'a') as f:
            f.write(sep + block)
    else:
        with open(path, 'w') as f:
            f.write(block)


def write_to_target(root: str, target: str, task_content: str, arch_content: str = '',
                    inject: bool = False) -> str:
    """Write task context to the cram-owned section for this target. Returns the absolute path.

    For the claude target, writes a pointer-only CLAUDE.md by default (inject=False).
    Pass inject=True to write task_content instead (backward compat for --inject flag).
    """
    custom_targets = load_custom_targets(root)
    effective_files = get_effective_targets(root)

    rel = effective_files.get(target)
    if not rel:
        raise ValueError(f"Unknown target '{target}'. Valid: {', '.join(effective_files)}")

    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    output_cfg = load_output_config(root)

    # Determine whether to use marker-based upsert (preserves user content outside markers)
    use_upsert = (target in _UPSERT_TARGETS) or \
                 (target in custom_targets and custom_targets[target].get('upsert', False))

    if target == 'claude' and not inject:
        pointer = CLAUDE_MCP_POINTER.replace('/absolute/path/to/this/repo', root)
        _upsert_cram_section(path, pointer)
    elif use_upsert:
        content = _render(target, task_content, arch_content, output_cfg)
        _upsert_cram_section(path, content)
    else:
        content = _render(target, task_content, arch_content, output_cfg)
        with open(path, 'w') as f:
            f.write(content)
    return path


def write_to_all_detected(root: str, task_content: str, arch_content: str = '') -> list[str]:
    """Write to every tool whose indicator is present. Returns paths written."""
    written = []
    for target in detect_targets(root):
        try:
            written.append(write_to_target(root, target, task_content, arch_content))
        except Exception:
            pass
    return written
