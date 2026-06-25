"""Single entry point: dispatches `cram <subcommand>` to the right module."""

import sys


USAGE = """\
Usage: cram <command> [args]

Profile & referee  (the core — `audit` is local and needs no API key):
  audit       [--days N] [--all] [--json] [--report [FILE]] [--report-html [FILE]] [--okf [DIR]] [--layer NAME] [--compare A B] [--session ID] [--reingest]  Audit agent sessions: where tokens go + findings; --session waterfall; --report / --report-html / --okf (OKF bundle)
  rig         <corpus.json> [--providers ...] [--repeats N] [--tier T] [--dry-run] | --observe <optimizer> [--days N] | --leaderboard <glob>  Referee context optimizers: tokens at fixed success (controlled), observational A/B, or a leaderboard
  benchmark   [path]                       Model the cache-write cost of delivering repo context

Setup:
  init        [path] [--team]              One-time repo setup (--team adds GitHub Actions workflows)
  doctor      [path]                       Check setup: models, hooks, git, context files
  hook        install|uninstall [path]     Manage git hooks (post-commit + commit-msg)
              global-install|global-uninstall   Manage the ~/.claude/CLAUDE.md block (separate from git hooks)

Optional — context layer  (the reference optimizer `cram rig` benchmarks; experimental, not the product):
  task        "<description>" [--target T] Populate CURRENT_TASK.md and auto-load into your tool
  add         <file> [file ...] [--replace] Append files to the current session context
  continue    [path]                       Extend grace period — keep context on next commit
  sync        [path]                       Update ARCHITECTURE.md after a commit
  decide      "<decision>" [path]          Append an architectural decision to DECISIONS.md
  decisions   [--mine] [--days N] [path]   Show decisions; --mine extracts drafts from git log
  gotcha      "<trap>" [path]              Append a non-obvious trap to GOTCHAS.md
  status      [path]                       Show .ai-context/ freshness
  mcp         [--repo PATH]                Start MCP server (stdio) delivering context to Claude Code / agents

--target choices: cursor | claude | copilot | codex | windsurf | gemini | all
  Set a default in .ai-context/config.toml:  [task] default_target = "cursor"

Flags: -V, --version   Print the cram-ai version and exit
"""


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ('-h', '--help'):
        print(USAGE)
        sys.exit(0)

    if args[0] in ('-V', '--version'):
        from cram import __version__
        print(f"cram-ai {__version__}")
        sys.exit(0)

    cmd, rest = args[0], args[1:]
    sys.argv = [f'cram {cmd}'] + rest  # rewrite for submodule arg parsers

    if cmd == 'init':
        from cram.init import main as _main
    elif cmd == 'task':
        from cram.find_context import main as _main
    elif cmd == 'add':
        from cram.add_context import main as _main
    elif cmd == 'continue':
        from cram.session import _continue_main as _main
    elif cmd == 'sync':
        from cram.sync_context import main as _main
    elif cmd == 'decide':
        from cram.decide import main as _main
    elif cmd == 'decisions':
        from cram.decisions import main as _main
    elif cmd == 'gotcha':
        from cram.gotcha import main as _main
    elif cmd == 'audit':
        from cram.audit import main as _main
    elif cmd == 'benchmark':
        from cram.benchmark import main as _main
    elif cmd == 'rig':
        from cram.rig import main as _main
    elif cmd == 'status':
        from cram.status import main as _main
    elif cmd == 'doctor':
        from cram.doctor import main as _main
    elif cmd == 'hook':
        from cram.hooks import main as _main
    elif cmd == 'mcp':
        from cram.mcp_server import main as _main
    else:
        print(f"Unknown command: {cmd!r}\n")
        print(USAGE)
        sys.exit(1)

    _main()


if __name__ == '__main__':
    main()
