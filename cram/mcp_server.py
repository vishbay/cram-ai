"""cram-ai MCP server — exposes repo context as tools for Claude Code and other agents.

Start with: cram mcp [--repo /path/to/repo]

Configure in .claude/settings.json:
  {
    "mcpServers": {
      "cram-ai": {
        "command": "cram",
        "args": ["mcp", "--repo", "/absolute/path/to/your/repo"]
      }
    }
  }
"""

from __future__ import annotations
import datetime
import json
import os
import re
import sys
import time

from cram.context_dir import CONTEXT_DIR, context_path, has_context_dir

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "mcp package required. Install with: pip install 'cram-ai[mcp]'",
        file=sys.stderr,
    )
    sys.exit(1)

# Resolved at startup — see main()
_repo_root: str = ''


def _task_slug(task: str) -> str:
    from cram.session import _task_slug as _slug
    return _slug(task)


def _active_slot_path() -> str | None:
    """Return the absolute path of the most recently active task slot, or None."""
    if not _repo_root:
        return None
    from cram.session import get_last_slot
    slug = get_last_slot(_repo_root)
    if not slug:
        return None
    tasks_dir = os.path.join(_repo_root, CONTEXT_DIR, 'tasks')
    slot = os.path.join(tasks_dir, f'{slug}.md')
    return slot if os.path.exists(slot) else None


def _archive_current_task() -> None:
    """Archive the active slot (or CURRENT_TASK.md fallback) to TASK_HISTORY.jsonl."""
    from cram.session import archive_task
    source = _active_slot_path() or _ctx_path('CURRENT_TASK.md')
    archive_task(_repo_root, source)


def _cleanup_stale_slots(tasks_dir: str, max_age: int = 86400) -> None:
    try:
        cutoff = time.time() - max_age
        for fname in os.listdir(tasks_dir):
            fpath = os.path.join(tasks_dir, fname)
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
    except Exception:
        pass


def _log_usage(task: str, tokens: int, source: str) -> None:
    try:
        log_path = _ctx_path('usage.jsonl')
        entry = {
            'ts':     datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'task':   task,
            'tokens': tokens,
            'source': source,
        }
        with open(log_path, 'a') as lf:
            lf.write(json.dumps(entry) + '\n')
    except Exception:
        pass


def _ctx_path(filename: str) -> str:
    return context_path(_repo_root, filename, warn=True)


def _read(filename: str) -> str:
    p = _ctx_path(filename)
    if not os.path.exists(p):
        return ''
    with open(p, errors='ignore') as f:
        return f.read()


mcp = FastMCP(
    'cram-ai',
    instructions=(
        "IMPORTANT: Call get_context() as your FIRST action in every session, before "
        "answering any question or writing any code. Pass the task description as the "
        "argument (e.g. get_context('fix the rate limiter')), or call with no arguments "
        "to reload the last task's context. Skipping this means you have no focused "
        "context and will miss key files."
    ),
)


@mcp.tool()
def get_context(task: str = '') -> str:
    """Load focused file excerpts for a coding task.

    Call this as your FIRST action in every session before writing any code.

    With a task argument: runs the full pipeline — reads the symbol index, asks
    the context model to identify relevant files, extracts identifier-focused
    excerpts, and returns everything assembled as a markdown document.

    With no argument: returns the last context loaded by `cram task`, skipping
    the LLM pipeline. Use this at session start when the developer already ran
    `cram task "..."` from the CLI before opening the editor.

    Args:
        task: What you're about to work on, e.g. "add OAuth login to the API".
              Omit to reload the existing context from the last `cram task` run.
    """
    if not _repo_root:
        return 'Error: repo root not configured.'

    if not has_context_dir(_repo_root):
        return f'Error: {CONTEXT_DIR}/ not found in {_repo_root}. Run `cram init` first.'

    if not task:
        # Try the active slot first; fall back to CURRENT_TASK.md for CLI-only users
        slot = _active_slot_path()
        if slot:
            with open(slot, errors='ignore') as f:
                content = f.read()
        else:
            content = _read('CURRENT_TASK.md')
        if not content:
            return (
                'No context loaded yet. Call get_context("your task description") '
                'to generate context for a specific task, or run `cram task "..."` '
                'from the terminal first.'
            )
        # Prepend a staleness warning when context may be out of date.
        try:
            from cram.health import context_health
            h = context_health(_repo_root)
            band = h['staleness_band']
            if band in ('stale', 'critical'):
                score   = h['staleness_score']
                commits = h['commits_since_sync']
                commit_note = f' — {commits} commit{"s" if commits != 1 else ""} since last sync' if commits else ''
                header = f'> staleness: {band} ({score}/10){commit_note} — run `cram sync` before relying on this context\n\n'
                content = header + content
        except Exception:
            pass
        _log_usage('', len(content) // 4, 'reload')
        return content

    # Each task gets its own slot file — concurrent agents don't stomp each other
    slug       = _task_slug(task)
    tasks_dir  = os.path.join(_repo_root, CONTEXT_DIR, 'tasks')
    slot_path  = os.path.join(tasks_dir, f'{slug}.md')

    # No os.chdir — files are opened via os.path.join(root, relpath) in populate_current_task
    from cram.find_context import find_relevant_files, populate_current_task
    from cram.utils import get_model_recommendations

    arch      = _read('ARCHITECTURE.md')
    decisions = _read('DECISIONS.md')
    gotchas   = _read('GOTCHAS.md')
    symbols   = _read('SYMBOLS.md')

    ctx_model, coding_model = get_model_recommendations()
    file_entries = find_relevant_files(task, arch, decisions, symbols, gotchas,
                                       root=_repo_root)

    if not file_entries:
        return (
            f"# Task: {task}\n\n"
            "No relevant files identified. "
            "Check that ARCHITECTURE.md and SYMBOLS.md describe the repo structure.\n\n"
            f"## Architecture\n{arch}"
        )

    os.makedirs(tasks_dir, exist_ok=True)
    _archive_current_task()
    populate_current_task(task, file_entries, ctx_model, coding_model,
                          output_path=slot_path, root=_repo_root)
    # Record the active slot so no-arg reload and add_file can find it
    from cram.session import set_last_slot
    set_last_slot(_repo_root, slug)
    _cleanup_stale_slots(tasks_dir)

    with open(slot_path) as f:
        content = f.read()

    _log_usage(task, len(content) // 4, 'generate')
    return content


@mcp.tool()
def get_architecture() -> str:
    """Get this repo's ARCHITECTURE.md — structure, tech stack, and key files.

    Use this for general orientation questions about the codebase before diving
    into a specific task.
    """
    content = _read('ARCHITECTURE.md')
    if not content:
        return 'ARCHITECTURE.md not found. Run `cram init` to generate it.'
    return content


@mcp.tool()
def get_symbols(query: str = '') -> str:
    """Get the repo's public symbol index — files mapped to their top-level functions and classes.

    Use this to find which file defines a specific function or class without
    reading every source file.

    Args:
        query: Optional filter — returns only lines containing this string (case-insensitive).
               Leave empty to get the full index.
    """
    content = _read('SYMBOLS.md')
    if not content:
        return 'SYMBOLS.md not found. Run `cram init` or `cram sync` to generate it.'

    if not query:
        return content

    q = query.lower()
    matching = sorted(line for line in content.splitlines() if q in line.lower())
    if not matching:
        return f'No symbols matching "{query}" found.\n\nFull index:\n{content}'
    return f'Symbols matching "{query}":\n\n' + '\n'.join(matching)


@mcp.tool()
def get_decisions() -> str:
    """Get architectural decisions recorded for this repo.

    These are choices the team has committed to — use them to ensure your
    implementation aligns with existing conventions and constraints.
    """
    content = _read('DECISIONS.md')
    if not content:
        return 'DECISIONS.md not found. Run `cram init` to create it.'
    return content


@mcp.tool()
def propose_decision(text: str, reason: str = '', alternatives: str = '') -> str:
    """Propose a new architectural decision discovered during this session.

    Appends a [PENDING] entry to DECISIONS.md for owner review. Use this when
    you discover an invariant, design constraint, or make a significant
    architectural choice the team should record.

    Args:
        text: The decision in one sentence ("use JWT over session cookies")
        reason: Why this decision was made (optional but encouraged)
        alternatives: What alternatives were considered (optional)
    """
    import datetime as _dt
    from cram.decide import _next_decision_id, DECISIONS_FILE
    from cram.context_dir import context_path

    if not _repo_root:
        return 'Error: repo root not configured.'

    path = context_path(_repo_root, DECISIONS_FILE, warn=True)
    if not os.path.exists(path):
        return 'DECISIONS.md not found. Run `cram init` first.'

    with open(path) as f:
        content = f.read()

    decision_id = _next_decision_id(content)
    today = _dt.date.today().isoformat()

    entry = (
        f"\n## [{decision_id}] [PENDING] {text}\n"
        f"- **Date:** {today}\n"
        f"- **Status:** Pending — proposed by agent, awaiting owner review\n"
        f"- **Decision:** {text}\n"
        f"- **Reason:** {reason}\n"
        f"- **Alternatives considered:** {alternatives}\n"
    )

    with open(path, 'a') as f:
        f.write(entry)

    # Log to suggestions.jsonl for later review
    suggestions_path = os.path.join(_repo_root, '.ai-context', 'suggestions.jsonl')
    try:
        with open(suggestions_path, 'a') as f:
            import json as _json
            f.write(_json.dumps({
                'ts':   _dt.datetime.now(_dt.timezone.utc).isoformat(),
                'type': 'decision',
                'id':   decision_id,
                'text': text,
            }) + '\n')
    except OSError:
        pass

    return f'Added [{decision_id}] to DECISIONS.md with [PENDING] status. Review with `cram decisions` or edit the file directly.'


@mcp.tool()
def get_gotchas() -> str:
    """Get known non-obvious traps and foot-guns in this repo.

    These are things that aren't visible from the code alone: silent side
    effects, endpoints that bypass middleware, columns with surprising nullability,
    patterns that look correct but break in production. Read before touching
    unfamiliar areas.
    """
    content = _read('GOTCHAS.md')
    if not content:
        return 'GOTCHAS.md not found. Run `cram init` to create it, then add entries as you find them.'
    return content


@mcp.tool()
def get_health() -> str:
    """Report context staleness + per-file token budgets.

    Use this to decide whether to trust the loaded context or run `cram sync`
    first. Returns a deterministic markdown block — safe to cache.
    """
    if not _repo_root:
        return 'Error: repo root not configured.'

    if not has_context_dir(_repo_root):
        return f'Error: {CONTEXT_DIR}/ not found in {_repo_root}. Run `cram init` first.'

    try:
        from cram.health import context_health
        h = context_health(_repo_root)
    except Exception as ex:
        return f'Error reading health data: {ex}. Run `cram doctor` to diagnose.'

    band    = h['staleness_band']
    score   = h['staleness_score']
    commits = h['commits_since_sync']

    commit_note = (
        f' — {commits} commit{"s" if commits != 1 else ""} since last sync'
        if commits is not None else ''
    )

    lines = ['# Context health', f'- staleness: {band} ({score}/10){commit_note}']

    for fname, info in h['files'].items():
        tok    = info['tokens']
        budget = info['budget']
        bstat  = info['budget_status']
        if budget:
            suffix = ' — trim before next sync' if bstat == 'over' else ''
            lines.append(f'- {fname}  {tok:,} tok (budget {budget:,}) {bstat}{suffix}')
        else:
            lines.append(f'- {fname}  {tok:,} tok')

    if band in ('stale', 'critical'):
        lines.append('- recommendation: run `cram sync` before relying on this context')

    return '\n'.join(lines)


@mcp.tool()
def add_file(path: str, identifiers: str = '') -> str:
    """Add a file's excerpts to the current session context.

    Use this when you discover mid-task that a file not in the initial context
    is needed. The file is appended to CURRENT_TASK.md with excerpts focused on
    the current task's keywords, or on the identifiers you specify.

    Args:
        path: Relative path to the file, e.g. "auth/middleware.py"
        identifiers: Optional comma-separated function/class names to focus on,
                     e.g. "handle_request, check_token". Leave empty to use the
                     current task's keywords automatically.
    """
    if not _repo_root:
        return 'Error: repo root not configured.'

    # No os.chdir — add_files receives explicit task_path_override and resolves
    # relative file paths itself (via _resolve_path which accepts root).
    import io
    from contextlib import redirect_stdout
    from cram.add_context import add_files

    # Use the active slot if one exists, otherwise fall back to CURRENT_TASK.md
    active_slot = _active_slot_path()
    task_file = active_slot or _ctx_path('CURRENT_TASK.md')

    spec = f'{path} | {identifiers}' if identifiers.strip() else path
    buf  = io.StringIO()
    with redirect_stdout(buf):
        ok = add_files([spec], replace=False, task_path_override=task_file,
                       root=_repo_root)

    output = buf.getvalue()
    if ok:
        with open(task_file) as f:
            content = f.read()
        return output.rstrip() + '\n\n' + content
    return output or f'Could not add {path} — check the file exists and a session is active.'


@mcp.tool()
def run_benchmark() -> str:
    """Model the cache-write cost of delivering repo context — full repo vs cram context.

    A delivery-cost model (cache-write tokens at Sonnet/Opus rates), not a proof of
    savings — whether the context layer actually helps is what `cram rig` measures.
    """
    if not _repo_root:
        return 'Error: repo root not configured.'
    import io
    from contextlib import redirect_stdout
    from cram.benchmark import run_benchmark as _bench

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            _bench(_repo_root)
        return buf.getvalue()
    except SystemExit:
        return buf.getvalue() or 'Benchmark failed — run `cram init` first.'


@mcp.tool()
def get_task_history(limit: int = 20) -> str:
    """Return recent task history as a markdown list (newest first).

    Args:
        limit: Maximum number of entries to return (default 20).
    """
    if not _repo_root:
        return 'Error: repo root not configured.'
    history_path = _ctx_path('TASK_HISTORY.jsonl')
    if not os.path.exists(history_path):
        return 'No task history yet.'
    try:
        entries = []
        with open(history_path, errors='ignore') as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        if not entries:
            return 'No task history yet.'
        entries = entries[-limit:][::-1]
        lines = ['## Task History\n']
        for e in entries:
            ts = e.get('ts', '')[:16].replace('T', ' ')
            lines.append(f'- `{ts}` — {e.get("task", "")}')
        return '\n'.join(lines)
    except Exception as ex:
        return f'Error reading task history: {ex}'


def main() -> None:
    global _repo_root

    import argparse
    from cram.utils import find_git_root

    parser = argparse.ArgumentParser(
        prog='cram mcp',
        description='Start the cram-ai MCP server on stdio',
    )
    parser.add_argument(
        '--repo', default=None,
        help='Path to the git repo (defaults to cwd)',
    )
    args = parser.parse_args()

    start = os.path.abspath(args.repo) if args.repo else os.getcwd()
    _repo_root = find_git_root(start)

    # Validate the repo has been initialised
    if not has_context_dir(_repo_root):
        print(
            f"Warning: {CONTEXT_DIR}/ not found in {_repo_root}. "
            "Run `cram init` before using the MCP server.",
            file=sys.stderr,
        )

    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
