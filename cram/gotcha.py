"""Quick CLI to append a known trap to GOTCHAS.md."""

from __future__ import annotations
import os
import sys
from datetime import date

from cram.context_dir import context_path, context_basename

GOTCHAS_FILE = 'GOTCHAS.md'


def _next_gotcha_id(content: str) -> str:
    import re
    ids = re.findall(r'\[GOTCHA-(\d+)\]', content)
    next_n = max((int(n) for n in ids), default=0) + 1
    return f"GOTCHA-{next_n:03d}"


def append_gotcha(root: str, trap_text: str) -> None:
    path = context_path(root, GOTCHAS_FILE, warn=True)
    if not os.path.exists(path):
        print(f"Error: {path} not found. Run `cram init` first.", file=sys.stderr)
        sys.exit(1)

    with open(path) as f:
        content = f.read()

    gotcha_id = _next_gotcha_id(content)
    today = date.today().isoformat()

    entry = (
        f"\n## [{gotcha_id}] {trap_text}\n"
        f"- **Date:** {today}\n"
        f"- **File / area:** \n"
        f"- **Trap:** {trap_text}\n"
        f"- **Why it exists:** \n"
        f"- **Safe pattern:** \n"
    )

    with open(path, 'a') as f:
        f.write(entry)

    print(f"Added [{gotcha_id}] to {context_basename(root)}/{GOTCHAS_FILE}")
    print("  Edit the file to fill in File/area, Why it exists, and Safe pattern.")


def main() -> None:
    from cram.utils import find_git_root

    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print("Usage: cram gotcha \"<trap description>\" [--path /repo]")
        sys.exit(0)

    path_idx = next((i for i, a in enumerate(args) if a == '--path'), None)
    if path_idx is not None and path_idx + 1 < len(args):
        repo_path = args[path_idx + 1]
        trap_parts = args[:path_idx] + args[path_idx + 2:]
    else:
        repo_path = '.'
        trap_parts = args

    trap_text = ' '.join(trap_parts).strip()
    if not trap_text:
        print("Error: trap description is required.", file=sys.stderr)
        sys.exit(1)

    root = find_git_root(os.path.abspath(repo_path))
    append_gotcha(root, trap_text)


if __name__ == '__main__':
    main()
