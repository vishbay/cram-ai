"""Quick CLI to append an architectural decision to DECISIONS.md."""

from __future__ import annotations
import os
import sys
from datetime import date

from cram.context_dir import context_path, context_basename

DECISIONS_FILE = 'DECISIONS.md'


def _next_decision_id(content: str) -> str:
    import re
    ids = re.findall(r'\[DECISION-(\d+)\]', content)
    next_n = max((int(n) for n in ids), default=0) + 1
    return f"DECISION-{next_n:03d}"


def append_decision(root: str, decision_text: str) -> None:
    path = context_path(root, DECISIONS_FILE, warn=True)
    if not os.path.exists(path):
        print(f"Error: {path} not found. Run `cram init` first.", file=sys.stderr)
        sys.exit(1)

    with open(path) as f:
        content = f.read()

    decision_id = _next_decision_id(content)
    today = date.today().isoformat()

    entry = (
        f"\n## [{decision_id}] {decision_text}\n"
        f"- **Date:** {today}\n"
        f"- **Status:** Accepted\n"
        f"- **Decision:** {decision_text}\n"
        f"- **Reason:** \n"
        f"- **Alternatives considered:** \n"
    )

    with open(path, 'a') as f:
        f.write(entry)

    print(f"Added [{decision_id}] to {context_basename(root)}/{DECISIONS_FILE}")
    print("  Edit the file to fill in Reason and Alternatives.")


def main() -> None:
    from cram.utils import find_git_root

    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print("Usage: cram decide \"<decision text>\" [--path /repo]")
        sys.exit(0)

    path_idx = next((i for i, a in enumerate(args) if a == '--path'), None)
    if path_idx is not None and path_idx + 1 < len(args):
        repo_path = args[path_idx + 1]
        decision_parts = args[:path_idx] + args[path_idx + 2:]
    else:
        repo_path = '.'
        decision_parts = args

    decision_text = ' '.join(decision_parts).strip()
    if not decision_text:
        print("Error: decision text is required.", file=sys.stderr)
        sys.exit(1)

    root = find_git_root(os.path.abspath(repo_path))
    append_decision(root, decision_text)


if __name__ == '__main__':
    main()
