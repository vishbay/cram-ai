"""Single entry point: dispatches `cram <subcommand>` to the right module."""

import sys


USAGE = """\
Usage: cram <command> [args]

Commands:
  init        [path] [--team]              One-time repo setup (--team adds GitHub Actions CI)
  task        "<description>" [--target T] Populate CURRENT_TASK.md and auto-load into tool
  add         <file> [file ...] [--replace] Append files to the current session context
  continue    [path]                       Extend grace period — keep context on next commit
  sync        [path]                       Update ARCHITECTURE.md after a commit
  decide      "<decision>" [path]          Append an architectural decision to DECISIONS.md
  decisions   [--mine] [--days N] [path]   Show decisions; --mine extracts drafts from git log
  gotcha      "<trap>" [path]              Append a non-obvious trap to GOTCHAS.md
  audit       [--days N] [--all] [--json] [--report [FILE]] [--compare A B] [--session ID] [--reingest]  Audit agent sessions: where tokens go + findings; --session for a per-request waterfall; --report for shareable markdown
  benchmark   [path]                       Show token savings vs full-repo auto-indexing
  rig         <corpus.json> [--providers ...] [--dry-run] | --observe <optimizer> [--days N]  Verify context optimizers: controlled (tokens at fixed success) or observational A/B over real sessions
  status      [path]                       Show .ai-context/ freshness
  doctor      [path]                       Check setup: models, hooks, git, context files
  hook        install|uninstall [path]     Manage the git post-commit hook
  mcp         [--repo PATH]                Start MCP server (stdio) for Claude Code / agents
  ui          [path]                       Launch TUI dashboard, Audit tab first (requires cram-ai[tui])

--target choices: cursor | claude | copilot | codex | windsurf | all
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
    elif cmd == 'ui':
        from cram.ui import main as _main
    else:
        print(f"Unknown command: {cmd!r}\n")
        print(USAGE)
        sys.exit(1)

    _main()


if __name__ == '__main__':
    main()
