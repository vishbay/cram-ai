"""Post-session context update: refreshes ARCHITECTURE.md after a commit."""

import hashlib
import os
import re
import subprocess
import sys
import time

from cram.init import scan_structure
from cram.utils import call_context_model, strip_code_fence
from cram.symbols import write_symbols_md
from cram.context_dir import CONTEXT_DIR, has_context_dir, resolve_context_dir

MAX_LINES = int(os.environ.get('AICONTEXT_MAX_LINES', '300'))
# A commit within this window of setting a task is treated as mid-session.
TASK_GRACE_SECONDS = int(os.environ.get('CRAM_TASK_GRACE_SECONDS', str(10 * 60)))

SESSION_ENDED_TEMPLATE = """\
# Current Task

## Task
<!-- Session ended on commit. Run `cram task "..."` to begin a new task. -->

## Relevant Files
<!-- Populated by `cram task "..."` -->
"""


def get_git_diff() -> str:
    try:
        return subprocess.check_output(
            ['git', 'diff', 'HEAD~1', '--stat', '--unified=2'],
            stderr=subprocess.DEVNULL,
        ).decode()
    except subprocess.CalledProcessError:
        # Only one commit — diff the initial commit itself
        return subprocess.check_output(
            ['git', 'show', '--stat', '--unified=2', 'HEAD'],
            stderr=subprocess.DEVNULL,
        ).decode()


# Fingerprint of the repo structure ARCHITECTURE.md was last generated from.
# Stored inside the doc so it travels with the committed file — after a pull,
# a teammate's structure matches and the LLM rewrite is skipped (no churn).
_STRUCT_HASH_RE = re.compile(r'<!--\s*cram:structure-hash\s+([0-9a-f]{64})\s*-->')


def _structure_hash(structure: str) -> str:
    return hashlib.sha256(structure.encode()).hexdigest()


def _stored_structure_hash(content: str) -> str | None:
    m = _STRUCT_HASH_RE.search(content)
    return m.group(1) if m else None


def _with_structure_hash(content: str, struct_hash: str) -> str:
    """Append (or replace) the structure-hash marker at the end of the doc."""
    body = _STRUCT_HASH_RE.sub('', content).rstrip()
    return f"{body}\n\n<!-- cram:structure-hash {struct_hash} -->\n"


def update_architecture_md(structure: str, diff: str, current: str) -> str:
    prompt = (
        f"Update this ARCHITECTURE.md based on recent changes.\n"
        f"Keep it under {MAX_LINES} lines. Only update what changed.\n"
        f"The repo structure below is the ground truth: do not mention files "
        f"that are absent from it. Remove any existing entry whose file no "
        f"longer appears in the structure.\n\n"
        f"Current ARCHITECTURE.md:\n{current}\n\n"
        f"Repo structure:\n{structure}\n\n"
        f"Recent git diff:\n{diff}\n\n"
        f"Return only the updated markdown, no explanation."
    )
    return strip_code_fence(call_context_model(prompt))


def reset_task(root: str) -> None:
    """Unconditionally reset CURRENT_TASK.md and all target files to the session-ended placeholder."""
    from cram import targets as _targets
    root = os.path.abspath(root)
    context_dir = resolve_context_dir(root, warn=True)
    task_path = os.path.join(context_dir, 'CURRENT_TASK.md')
    if not os.path.isdir(context_dir):
        return
    with open(task_path, 'w') as f:
        f.write(SESSION_ENDED_TEMPLATE)
    _targets.write_to_all_detected(root, SESSION_ENDED_TEMPLATE)


def _task_has_real_content(task_path: str) -> bool:
    """Return True if CURRENT_TASK.md has an actual task set (not the blank template)."""
    if not os.path.exists(task_path):
        return False
    with open(task_path) as f:
        content = f.read()
    return '<!-- Replace with your task description -->' not in content \
        and '<!-- Session ended on commit.' not in content


def _reset_task_if_session_ended(root: str, context_dir: str) -> None:
    """Reset CURRENT_TASK.md and all target files after a commit, unless the
    task was set within the grace period (treat as mid-session commit)."""
    from cram import targets as _targets
    from cram.session import load_session, clear_session

    task_path = os.path.join(context_dir, 'CURRENT_TASK.md')

    if not _task_has_real_content(task_path):
        return  # nothing to reset

    session = load_session(root)
    if session is not None:
        age   = time.time() - session.get('set_at', 0.0)
        grace = session.get('grace_seconds', TASK_GRACE_SECONDS)
    else:
        # Legacy: no session.json yet — use file mtime
        age   = time.time() - os.path.getmtime(task_path)
        grace = TASK_GRACE_SECONDS

    if age < grace:
        print(f"Task set {int(age)}s ago — keeping context (within {int(grace)}s grace period).")
        return

    from cram.find_context import _archive_current_task_to_history
    _archive_current_task_to_history()

    with open(task_path, 'w') as f:
        f.write(SESSION_ENDED_TEMPLATE)

    written = _targets.write_to_all_detected(root, SESSION_ENDED_TEMPLATE)
    for path in written:
        print(f"Task context reset in {os.path.relpath(path, root)} (your instructions are untouched).")
    clear_session(root)
    print("Ready for next task — run `cram task \"...\"`.")


def sync(root: str = '.') -> None:
    root = os.path.abspath(root)
    context_dir = resolve_context_dir(root, warn=True)
    arch_path = os.path.join(context_dir, 'ARCHITECTURE.md')

    if not has_context_dir(root):
        print(
            f"Error: {CONTEXT_DIR}/ not found. Run `cram init` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    current = ''
    if os.path.exists(arch_path):
        with open(arch_path) as f:
            current = f.read()

    print("Getting git diff ...")
    diff = get_git_diff()

    print("Scanning repo structure ...")
    structure = scan_structure(root)

    struct_hash = _structure_hash(structure)
    if current and _stored_structure_hash(current) == struct_hash:
        # Repo structure is identical to the last regen — rewriting would only
        # produce non-deterministic prose churn. Skip the LLM call entirely.
        print("Repo structure unchanged — skipping ARCHITECTURE.md regen.")
        updated = None
    else:
        from cram.utils import get_model_recommendations
        ctx_model, _ = get_model_recommendations()
        print(f"Updating ARCHITECTURE.md via {ctx_model} ...")
        try:
            updated = _with_structure_hash(
                update_architecture_md(structure, diff, current), struct_hash
            )
        except RuntimeError as e:
            print(f"Error: could not update ARCHITECTURE.md — {e}", file=sys.stderr)
            print(
                "Existing ARCHITECTURE.md left unchanged. Symbol index will still refresh below.",
                file=sys.stderr,
            )
            updated = None

    if updated:
        with open(arch_path, 'w') as f:
            f.write(updated)
        print(f"Done. {CONTEXT_DIR}/ARCHITECTURE.md updated.")

    print("Refreshing symbol index ...")
    _, sym_count = write_symbols_md(root)
    print(f"  {sym_count} identifiers indexed")

    # Warn on over-budget frozen files (soft warning — never truncates).
    from cram.cost_model import FILE_BUDGETS, budget_status as _budget_status
    for fname, limit in FILE_BUDGETS.items():
        fpath = os.path.join(context_dir, fname)
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, errors='ignore') as _f:
                tokens = len(_f.read()) // 4
            if _budget_status(fname, tokens) == 'over':
                print(
                    f"  Warning: {fname} is {tokens} tok (budget {limit}) — "
                    "trim before next session.",
                    file=sys.stderr,
                )
        except OSError:
            pass

    _reset_task_if_session_ended(root, context_dir)


def main() -> None:
    from cram.utils import find_git_root
    target = find_git_root(sys.argv[1] if len(sys.argv) > 1 else '.')
    sync(target)


if __name__ == '__main__':
    main()
