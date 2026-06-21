"""One-time setup: scans a repo, generates initial .ai-context/ files via Haiku."""

import os
import fnmatch

from cram.utils import call_model, strip_code_fence
from cram.hooks import install_hook, install_global_claude_md, install_claude_code_hooks
from cram.symbols import write_symbols_md
from cram.targets import write_to_target, _byte_cap_block
from cram.context_dir import CONTEXT_DIR, LEGACY_CONTEXT_DIR, canonical_context_dir, legacy_context_dir

EXCLUDE_DIRS = {
    'node_modules', 'dist', 'build', '__pycache__',
    '.git', '.venv', 'venv', 'coverage', '.next',
    CONTEXT_DIR, LEGACY_CONTEXT_DIR,
}

EXCLUDE_FILES = {
    'package-lock.json', 'yarn.lock', 'poetry.lock',
}

EXCLUDE_PATTERNS = {'*.min.js', '*.min.css'}

MAX_LINES = int(os.environ.get('AICONTEXT_MAX_LINES', '300'))


def _is_excluded_file(filename: str) -> bool:
    if filename in EXCLUDE_FILES:
        return True
    return any(fnmatch.fnmatch(filename, pat) for pat in EXCLUDE_PATTERNS)


def scan_structure(root: str) -> str:
    """Return a compact directory tree as a string, excluding noise dirs."""
    lines = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded dirs in-place so os.walk skips them
        dirnames[:] = [d for d in sorted(dirnames) if d not in EXCLUDE_DIRS]

        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == '.' else rel.count(os.sep) + 1
        indent = '  ' * depth
        folder = os.path.basename(dirpath) if rel != '.' else os.path.basename(root)
        lines.append(f"{indent}{folder}/")

        file_indent = '  ' * (depth + 1)
        for fname in sorted(filenames):
            if not _is_excluded_file(fname):
                lines.append(f"{file_indent}{fname}")

    return '\n'.join(lines)


def generate_architecture_md(structure: str) -> str:
    """Call Haiku to produce an initial ARCHITECTURE.md from the repo tree."""
    prompt = (
        f"You are a technical writer creating a concise ARCHITECTURE.md "
        f"for an AI coding assistant.\n\n"
        f"Repo structure:\n```\n{structure}\n```\n\n"
        f"Write a markdown document under {MAX_LINES} lines that covers:\n"
        f"1. What this repo does (inferred from file names)\n"
        f"2. Key directories and their purpose\n"
        f"3. Important files an AI should know about\n"
        f"4. Tech stack (inferred from file extensions and config files)\n\n"
        f"Be concise. No filler. Return only the markdown, no explanation."
    )
    return strip_code_fence(call_model(prompt))


def write_gitignore(context_dir: str) -> None:
    path = os.path.join(context_dir, '.gitignore')
    with open(path, 'w') as f:
        f.write("CURRENT_TASK.md\nsession.json\n")


_CI_WORKFLOW = """\
name: cram sync

on:
  push:
    branches: [main, master]

jobs:
  sync:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install cram-ai
        run: pip install cram-ai

      - name: Sync context
        run: cram sync
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          OPENAI_API_KEY:    ${{ secrets.OPENAI_API_KEY }}
          GEMINI_API_KEY:    ${{ secrets.GEMINI_API_KEY }}

      - name: Commit updated context
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add .ai-context/
          git diff --staged --quiet || git commit -m "chore: sync cram-ai context [skip ci]"
          git push
"""


# A starter workflow that comments a cram audit comparison on every PR using the
# published `cram audit` action. It expects the two audit JSONs to be committed
# (transcripts never exist in CI), so it's a template teams adapt — see the
# action's README for how to produce baseline.json / candidate.json.
_AUDIT_PR_WORKFLOW = """\
name: cram audit

on:
  pull_request:

jobs:
  audit:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
      # Adapt: point file-a/file-b at committed `cram audit --json` outputs, or
      # switch mode to `rig` with baseline/candidate `cram rig --json` results.
      - uses: vishbay/cram-ai@v1
        with:
          mode: compare
          file-a: .cram/baseline.json
          file-b: .cram/candidate.json
"""


def _write_workflow(workflows_dir: str, name: str, content: str) -> None:
    dest = os.path.join(workflows_dir, name)
    if os.path.exists(dest):
        print(f"  .github/workflows/{name} already exists — skipping.")
        return
    with open(dest, 'w') as f:
        f.write(content)
    print(f"  .github/workflows/{name}")


def write_ci_action(repo_root: str) -> None:
    workflows_dir = os.path.join(repo_root, '.github', 'workflows')
    os.makedirs(workflows_dir, exist_ok=True)
    _write_workflow(workflows_dir, 'cram-sync.yml', _CI_WORKFLOW)
    _write_workflow(workflows_dir, 'cram-audit.yml', _AUDIT_PR_WORKFLOW)


DECISIONS_TEMPLATE = """\
# Architectural Decisions

## [DECISION-001] Example decision
- **Date:** YYYY-MM-DD
- **Status:** Accepted
- **Decision:** What was decided
- **Reason:** Why
- **Alternatives considered:** What else was weighed
"""

GOTCHAS_TEMPLATE = """\
# Gotchas

Non-obvious traps in this codebase that grep can't tell you. Add entries when something burns you.

## [GOTCHA-001] Example
- **File / area:** path/to/file.py or module name
- **Trap:** What the surprising behaviour is
- **Why it exists:** Root cause or history
- **Safe pattern:** What to do instead
"""

CURRENT_TASK_TEMPLATE = """\
# Current Task

## Task
<!-- Replace with your task description -->

## Relevant Files
<!-- Populated by `cram task "..."` -->
"""


def init_repo(target: str = '.', team: bool = False) -> None:
    root = os.path.abspath(target)
    context_dir = canonical_context_dir(root)

    if os.path.exists(context_dir):
        print(f".ai-context/ already exists at {context_dir}. Skipping.")
        return
    if os.path.isdir(legacy_context_dir(root)):
        print(
            f"Legacy {LEGACY_CONTEXT_DIR}/ exists; creating canonical {CONTEXT_DIR}/. "
            "Move any manual entries across when you are ready."
        )

    print(f"Scanning {root} ...")
    structure = scan_structure(root)

    print("Generating ARCHITECTURE.md via Haiku ...")
    architecture = generate_architecture_md(structure)

    os.makedirs(context_dir, exist_ok=True)

    with open(os.path.join(context_dir, 'ARCHITECTURE.md'), 'w') as f:
        f.write(architecture + "\n## Agent Rules\n" + _byte_cap_block())

    print("Building symbol index ...")
    _, sym_count = write_symbols_md(root)
    print(f"  {sym_count} identifiers indexed")

    with open(os.path.join(context_dir, 'DECISIONS.md'), 'w') as f:
        f.write(DECISIONS_TEMPLATE)

    with open(os.path.join(context_dir, 'GOTCHAS.md'), 'w') as f:
        f.write(GOTCHAS_TEMPLATE)

    with open(os.path.join(context_dir, 'CURRENT_TASK.md'), 'w') as f:
        f.write(CURRENT_TASK_TEMPLATE)

    write_gitignore(context_dir)

    install_hook(root)
    install_global_claude_md()
    install_claude_code_hooks(root)

    # Write pointer-only CLAUDE.md to repo root (MCP config snippet, not injected content)
    write_to_target(root, 'claude', '')
    print("  CLAUDE.md  (MCP config pointer)")

    print("\nDone. Created .ai-context/ with:")
    for fname in ['ARCHITECTURE.md', 'DECISIONS.md', 'GOTCHAS.md', 'CURRENT_TASK.md', 'SYMBOLS.md', '.gitignore']:
        print(f"  .ai-context/{fname}")

    if team:
        print("\nCreating CI workflow:")
        write_ci_action(root)

    print("\nNext steps:")
    print("  1. Review .ai-context/ARCHITECTURE.md (edit if the summary is off)")
    print("  2. Edit .ai-context/DECISIONS.md — add your team's invariants")
    print("  3. Edit .ai-context/GOTCHAS.md — add non-obvious traps (add more over time)")
    print("  4. Commit context so teammates get it automatically:")
    print("       git add .ai-context/ .claude/ CLAUDE.md && git commit -m \"chore: init cram-ai\"")
    print("  5. Run `cram task \"your task\"` to set the active task — context auto-loads next session")
    if not team:
        print("\nTip: run `cram init --team` to also generate a GitHub Actions workflow")
        print("     that keeps ARCHITECTURE.md fresh on every push.")


def main() -> None:
    import argparse
    from cram.utils import find_git_root

    parser = argparse.ArgumentParser(prog='cram init',
                                     description='One-time repo setup for cram-ai')
    parser.add_argument('path', nargs='?', default=None,
                        help='Repo path (defaults to git root of current directory)')
    parser.add_argument('--team', action='store_true',
                        help='Also create .github/workflows/cram-sync.yml for CI sync')
    args = parser.parse_args()

    target = find_git_root(args.path or '.')
    init_repo(target, team=args.team)


if __name__ == '__main__':
    main()
