"""cram decisions — show or mine architectural decisions from git history."""

from __future__ import annotations
import os
import re
import subprocess
import sys

from cram.context_dir import context_path, context_basename
from cram.decide import DECISIONS_FILE, _next_decision_id

DECISION_KEYWORDS = re.compile(
    r'\b(chose|instead of|decided|switched? to|not using|because|moved? from|'
    r'replaced|dropped|avoided|prefer|use \w+ over|avoid \w+)\b',
    re.IGNORECASE,
)

_MINE_PROMPT = """\
You are extracting architectural decisions from git commit messages.

For each commit that records a REAL design decision (a deliberate architectural
or technology choice), output exactly ONE line in this format:
  DECISION: <decision in one sentence> | REASON: <why, from the commit message>

Rules:
- Skip pure bug fixes, version bumps, typo fixes, dependency updates, CI changes.
- Only output lines that match the format above. No explanations, no headers.
- If a commit has no real decision, output nothing for it.

Commits:
{commits}
"""


def _git_log(root: str, days: int) -> list[str]:
    """Return commit subject lines from the last N days."""
    try:
        result = subprocess.run(
            ['git', 'log', f'--since={days}.days.ago', '--oneline', '--no-merges'],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [l.strip() for l in result.stdout.splitlines() if l.strip()]
    except subprocess.CalledProcessError:
        return []


def _filter_commits(lines: list[str]) -> list[str]:
    return [l for l in lines if DECISION_KEYWORDS.search(l)]


def _parse_model_output(text: str) -> list[tuple[str, str]]:
    """Parse 'DECISION: X | REASON: Y' lines → [(decision, reason)]."""
    results = []
    for line in text.splitlines():
        m = re.match(r'DECISION:\s*(.+?)\s*\|\s*REASON:\s*(.+)', line.strip())
        if m:
            results.append((m.group(1).strip(), m.group(2).strip()))
    return results


def _interactive_review(drafts: list[tuple[str, str]], root: str) -> int:
    """Per-entry git-add-p style review. Returns count accepted."""
    accepted = 0
    total = len(drafts)
    for i, (decision, reason) in enumerate(drafts, 1):
        print(f"\n── Draft {i}/{total} {'─' * 50}")
        print(f"  Decision: {decision}")
        print(f"  Reason:   {reason}")
        while True:
            try:
                choice = input("  [a]ccept  [s]kip  [e]dit  [q]uit > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return accepted
            if choice in ('a', ''):
                # Reuse append_decision but inject reason into the file
                _append_with_reason(root, decision, reason)
                print("  → Added to DECISIONS.md")
                accepted += 1
                break
            elif choice == 's':
                break
            elif choice == 'e':
                try:
                    decision = input(f"  Decision [{decision}]: ").strip() or decision
                    reason   = input(f"  Reason   [{reason}]: ").strip() or reason
                except (EOFError, KeyboardInterrupt):
                    print()
                    return accepted
            elif choice == 'q':
                return accepted
    return accepted


def _append_with_reason(root: str, decision_text: str, reason: str) -> None:
    """Like append_decision() but fills in the Reason field."""
    from datetime import date
    path = context_path(root, DECISIONS_FILE, warn=True)
    with open(path) as f:
        content = f.read()
    decision_id = _next_decision_id(content)
    today = date.today().isoformat()
    entry = (
        f"\n## [{decision_id}] {decision_text}\n"
        f"- **Date:** {today}\n"
        f"- **Status:** Accepted\n"
        f"- **Decision:** {decision_text}\n"
        f"- **Reason:** {reason}\n"
        f"- **Alternatives considered:** \n"
    )
    with open(path, 'a') as f:
        f.write(entry)


def show_decisions(root: str) -> None:
    path = context_path(root, DECISIONS_FILE, warn=True)
    if not os.path.exists(path):
        print(f"DECISIONS.md not found at {path}. Run `cram init` first.", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        print(f.read(), end='')


def mine_decisions(root: str, days: int) -> None:
    path = context_path(root, DECISIONS_FILE, warn=True)
    if not os.path.exists(path):
        print("DECISIONS.md not found. Run `cram init` first.", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning git log (last {days} days)…")
    all_commits = _git_log(root, days)
    if not all_commits:
        print("No commits found.")
        return

    filtered = _filter_commits(all_commits)
    if not filtered:
        print(f"No decision-shaped commits found in {len(all_commits)} commits.")
        print("Keywords scanned: chose, decided, switched to, replaced, dropped, …")
        return

    print(f"Found {len(filtered)} candidate commits (of {len(all_commits)} total). Extracting…\n")

    from cram.utils import call_context_model
    response = call_context_model(_MINE_PROMPT.format(commits='\n'.join(filtered)))
    drafts = _parse_model_output(response)

    if not drafts:
        print("No decisions extracted. The model found no clear design choices in those commits.")
        print("Try `cram decide \"<decision>\"` to add one manually.")
        return

    print(f"Extracted {len(drafts)} decision draft(s). Review each one:\n")
    accepted = _interactive_review(drafts, root)
    print(f"\n{accepted}/{len(drafts)} decisions added to {context_basename(root)}/{DECISIONS_FILE}")
    if accepted < len(drafts):
        skipped = len(drafts) - accepted
        print(f"{skipped} skipped. Re-run or use `cram decide` to add manually.")


def main() -> None:
    import argparse
    from cram.utils import find_git_root

    parser = argparse.ArgumentParser(
        prog='cram decisions',
        description='Show or mine architectural decisions',
    )
    parser.add_argument('--mine', action='store_true',
                        help='Mine git history for decision-shaped commits')
    parser.add_argument('--days', type=int, default=90,
                        help='How many days of git history to scan (default: 90)')
    parser.add_argument('--path', default=None, metavar='REPO_PATH')
    args = parser.parse_args()

    start = os.path.abspath(args.path) if args.path else os.getcwd()
    try:
        root = find_git_root(start)
    except Exception:
        root = start

    if args.mine:
        mine_decisions(root, args.days)
    else:
        show_decisions(root)


if __name__ == '__main__':
    main()
