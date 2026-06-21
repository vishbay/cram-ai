"""Git hook installer — wires cram sync into the post-commit and commit-msg hooks."""

from __future__ import annotations
import os
import re
import stat
import sys

GLOBAL_CLAUDE_MD    = os.path.expanduser('~/.claude/CLAUDE.md')
_CRAM_SECTION_START = "<!-- cram-ai: start -->"
_CRAM_SECTION_END   = "<!-- cram-ai: end -->"
_GLOBAL_CLAUDE_MD_BLOCK = """\
> **cram-ai** — context is auto-loaded at session start via the SessionStart hook.
> When you see a systemMessage "cram context loaded: <task>", acknowledge it to
> the user in one line and proceed — do not call get_context() again.
> If no context was auto-loaded, call get_context() before
> answering any question or writing any code. Pass the task description as the
> argument (e.g. get_context("fix the rate limiter")), or call with no arguments
> to reload the last task's context.
> You do NOT need a new session per task. When the task changes mid-session,
> call get_context("the next task") again — it reloads focused context inline,
> no restart needed. Keep working in a single session.
> Run `cram doctor` if tools are missing.
"""


def _upsert_global_section(path: str, inner_content: str) -> None:
    block = f"{_CRAM_SECTION_START}\n{inner_content.rstrip()}\n{_CRAM_SECTION_END}\n"
    if os.path.exists(path):
        existing = open(path).read()
        if _CRAM_SECTION_START in existing:
            updated = re.sub(
                rf'{re.escape(_CRAM_SECTION_START)}.*?{re.escape(_CRAM_SECTION_END)}',
                block.rstrip('\n'),
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
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(block)


def install_global_claude_md() -> bool:
    """Upsert cram's warning block into ~/.claude/CLAUDE.md. Returns True if written."""
    try:
        _upsert_global_section(GLOBAL_CLAUDE_MD, _GLOBAL_CLAUDE_MD_BLOCK)
        print(f"cram-ai session warning installed at {GLOBAL_CLAUDE_MD}")
        return True
    except OSError as e:
        print(f"Warning: could not write {GLOBAL_CLAUDE_MD}: {e}", file=sys.stderr)
        return False


def uninstall_global_claude_md() -> None:
    """Remove cram's block from ~/.claude/CLAUDE.md."""
    if not os.path.exists(GLOBAL_CLAUDE_MD):
        return
    content = open(GLOBAL_CLAUDE_MD).read()
    cleaned = re.sub(
        rf'\n*{re.escape(_CRAM_SECTION_START)}.*?{re.escape(_CRAM_SECTION_END)}\n*',
        '\n',
        content,
        flags=re.DOTALL,
    ).strip()
    if cleaned:
        with open(GLOBAL_CLAUDE_MD, 'w') as f:
            f.write(cleaned + '\n')
    else:
        os.remove(GLOBAL_CLAUDE_MD)
    print(f"Removed cram-ai block from {GLOBAL_CLAUDE_MD}")

HOOK_SCRIPT = """\
#!/bin/sh
# Installed by cram-ai — keeps .ai-context/ARCHITECTURE.md fresh
if command -v cram >/dev/null 2>&1; then
    cram sync
elif command -v python3 >/dev/null 2>&1; then
    python3 -m cram.sync_context
fi
"""

COMMIT_MSG_HOOK_SCRIPT = """\
#!/bin/sh
# Installed by cram-ai — prompts to record architectural decisions
MSG_FILE="$1"
if [ -z "$MSG_FILE" ] || [ ! -f "$MSG_FILE" ]; then
  exit 0
fi
MSG=$(cat "$MSG_FILE")
if echo "$MSG" | grep -qiE '(chose|instead of|decided|rationale|trade.?off|switched? (from|to)|moved? (from|to|away))'; then
  printf "\\ncram: decision language detected — consider recording it:\\n  cram decide \\\"...\\\"\\n\\n"
fi
exit 0
"""


def _git_dir(repo_root: str) -> str | None:
    git_dir = os.path.join(repo_root, '.git')
    return git_dir if os.path.isdir(git_dir) else None


def _write_hook(hook_path: str, script: str, marker: str) -> bool:
    """Write or append a hook script. Returns True if installed, False if skipped."""
    if os.path.exists(hook_path):
        existing = open(hook_path).read()
        if marker in existing:
            print(f"{os.path.basename(hook_path)} hook already contains cram. Skipping.")
            return False
        with open(hook_path, 'a') as f:
            f.write('\n' + script)
        print(f"Appended cram block to existing {hook_path}")
    else:
        with open(hook_path, 'w') as f:
            f.write(script)
        print(f"Installed {os.path.basename(hook_path)} hook at {hook_path}")

    current = os.stat(hook_path).st_mode
    os.chmod(hook_path, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return True


def install_commit_msg_hook(repo_root: str = '.') -> bool:
    """Write commit-msg hook that suggests cram decide for decision language. Returns True if installed."""
    root = os.path.abspath(repo_root)
    git_dir = _git_dir(root)
    if not git_dir:
        print(f"No .git/ directory found in {root}. Skipping hook install.")
        return False
    hooks_dir = os.path.join(git_dir, 'hooks')
    os.makedirs(hooks_dir, exist_ok=True)
    return _write_hook(os.path.join(hooks_dir, 'commit-msg'), COMMIT_MSG_HOOK_SCRIPT, 'cram-ai')


def install_hook(repo_root: str = '.') -> bool:
    """Write post-commit and commit-msg hooks. Returns True if any installed."""
    root = os.path.abspath(repo_root)
    git_dir = _git_dir(root)
    if not git_dir:
        print(f"No .git/ directory found in {root}. Skipping hook install.")
        return False
    hooks_dir = os.path.join(git_dir, 'hooks')
    os.makedirs(hooks_dir, exist_ok=True)
    a = _write_hook(os.path.join(hooks_dir, 'post-commit'), HOOK_SCRIPT, 'cram-ai')
    b = _write_hook(os.path.join(hooks_dir, 'commit-msg'), COMMIT_MSG_HOOK_SCRIPT, 'cram-ai')
    return a or b


SESSION_START_HOOK_SCRIPT = '''\
#!/usr/bin/env python3
"""SessionStart hook: auto-inject cram-ai context and notify the user."""
import json
import sys
from pathlib import Path


def main():
    task_file = None
    for dirname in (\'.ai-context\', \'.cram-ai-context\'):
        candidate = Path.cwd() / dirname / \'CURRENT_TASK.md\'
        if candidate.exists():
            task_file = candidate
            break
    if task_file is None:
        sys.exit(0)

    try:
        content = task_file.read_text()
    except Exception:
        sys.exit(0)

    task = \'\'
    lines = content.split(\'\\n\')
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(\'# Task:\'):
            task = s[len(\'# Task:\'):].strip()
            break
        if s == \'## Task\':
            for next_line in lines[i + 1:]:
                ns = next_line.strip()
                if ns.startswith(\'#\'):
                    break
                if ns and not ns.startswith(\'<!--\'):
                    task = ns
                    break
            break

    if not task:
        sys.exit(0)

    print(json.dumps({
        \'additionalContext\': content,
        \'systemMessage\': f\'cram context loaded: {task}\',
    }))


main()
'''

POST_CONTEXT_HOOK_SCRIPT = '''\
#!/usr/bin/env python3
"""PostToolUse hook: show a user note after get_context() is called via MCP."""
import json
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    result = \'\'
    tool_response = data.get(\'tool_response\', data.get(\'tool_result\', \'\'))
    if isinstance(tool_response, dict):
        for block in tool_response.get(\'content\', []):
            if isinstance(block, dict) and block.get(\'type\') == \'text\':
                result = block.get(\'text\', \'\')
                break
    elif isinstance(tool_response, str):
        result = tool_response

    task = \'\'
    for line in result.split(\'\\n\'):
        s = line.strip()
        if s.startswith(\'# Task:\'):
            task = s[len(\'# Task:\'):].strip()
            break

    note = \'cram context loaded\'
    if task:
        note += f\': {task}\'

    print(json.dumps({\'systemMessage\': note}))


main()
'''


def install_claude_code_hooks(repo_root: str = '.') -> bool:
    """Write Claude Code hook scripts and wire them into .claude/settings.json.

    Also registers the cram-ai MCP server so get_context() works out of the box.
    Returns True if anything was written, False if already configured.
    """
    import json as _json

    root = os.path.abspath(repo_root)
    hooks_dir = os.path.join(root, '.claude', 'hooks')
    os.makedirs(hooks_dir, exist_ok=True)

    wrote_any = False
    for fname, script in [
        ('cram_session_start.py', SESSION_START_HOOK_SCRIPT),
        ('cram_post_context.py',  POST_CONTEXT_HOOK_SCRIPT),
    ]:
        path = os.path.join(hooks_dir, fname)
        if os.path.exists(path):
            print(f"  .claude/hooks/{fname} already exists — skipping.")
            continue
        with open(path, 'w') as f:
            f.write(script)
        current = os.stat(path).st_mode
        os.chmod(path, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  .claude/hooks/{fname}")
        wrote_any = True

    settings_path = os.path.join(root, '.claude', 'settings.json')
    settings: dict = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                settings = _json.load(f)
        except Exception:
            pass

    changed = False

    mcp_servers = settings.setdefault('mcpServers', {})
    if 'cram-ai' not in mcp_servers:
        mcp_servers['cram-ai'] = {'command': 'cram', 'args': ['mcp', '--repo', root]}
        changed = True

    hooks_cfg = settings.setdefault('hooks', {})

    ss_cmd = 'python3 .claude/hooks/cram_session_start.py'
    session_start = hooks_cfg.setdefault('SessionStart', [])
    if not any(
        any(h.get('command') == ss_cmd for h in entry.get('hooks', []))
        for entry in session_start
    ):
        session_start.append({
            'matcher': '*',
            'hooks': [{'type': 'command', 'command': ss_cmd}],
        })
        changed = True

    pt_cmd = 'python3 .claude/hooks/cram_post_context.py'
    post_tool = hooks_cfg.setdefault('PostToolUse', [])
    if not any(
        any(h.get('command') == pt_cmd for h in entry.get('hooks', []))
        for entry in post_tool
    ):
        post_tool.append({
            'matcher': 'mcp__cram-ai__get_context',
            'hooks': [{'type': 'command', 'command': pt_cmd}],
        })
        changed = True

    if changed:
        with open(settings_path, 'w') as f:
            _json.dump(settings, f, indent=2)
            f.write('\n')
        print("  .claude/settings.json")
        return True

    if not wrote_any:
        print("  .claude/settings.json already configured — skipping.")
    return wrote_any


def uninstall_hook(repo_root: str = '.') -> None:
    root = os.path.abspath(repo_root)
    git_dir = _git_dir(root)
    if not git_dir:
        print("No .git/ directory found.")
        return

    hook_path = os.path.join(git_dir, 'hooks', 'post-commit')
    if not os.path.exists(hook_path):
        print("No post-commit hook found.")
        return

    content = open(hook_path).read()
    # Remove only the block we added
    cleaned = content.replace(HOOK_SCRIPT, '').replace('\n' + HOOK_SCRIPT, '')
    if cleaned.strip():
        with open(hook_path, 'w') as f:
            f.write(cleaned)
        print("Removed cram block from post-commit hook.")
    else:
        os.remove(hook_path)
        print("Removed post-commit hook.")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Manage cram git hooks')
    parser.add_argument('action', choices=['install', 'uninstall'], default='install', nargs='?')
    parser.add_argument('path', nargs='?', default='.')
    args = parser.parse_args()

    from cram.utils import find_git_root
    path = find_git_root(args.path)
    if args.action == 'uninstall':
        uninstall_hook(path)
        uninstall_global_claude_md()
    else:
        install_hook(path)
        install_global_claude_md()


if __name__ == '__main__':
    main()
