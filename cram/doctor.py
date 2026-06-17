"""cram doctor — check your setup and print a health report."""

from __future__ import annotations
import os
import subprocess
import sys

from cram.context_dir import CONTEXT_DIR, LEGACY_CONTEXT_DIR, has_context_dir, resolve_context_dir


def _row(ok: bool | None, label: str, detail: str = '') -> None:
    """Print one check row.  True=✓  False=✗ (error)  None=! (advisory)"""
    icon = '✓' if ok is True else ('!' if ok is None else '✗')
    tail = f'  — {detail}' if detail else ''
    print(f'  {icon}  {label}{tail}')


def _detail(text: str) -> None:
    """Print an indented detail line under a check row."""
    print(f'       {text}')


def main() -> None:
    from cram.utils import find_git_root, discover_models, pick_context_model, pick_coding_model

    print('\ncram doctor')
    print('─' * 44)

    errors = 0

    # ── Python ────────────────────────────────────────────────────
    v = sys.version_info
    ok = v >= (3, 10)
    _row(ok, f'Python {v.major}.{v.minor}.{v.micro}',
         'required ≥ 3.10' if not ok else f'at {sys.executable}')
    if not ok:
        errors += 1

    # ── git ───────────────────────────────────────────────────────
    try:
        result = subprocess.run(['git', '--version'], capture_output=True, check=True, text=True)
        git_ver = result.stdout.strip().replace('git version ', '')
        _row(True, 'git', git_ver)
    except Exception:
        _row(False, 'git not found', 'install from https://git-scm.com')
        errors += 1

    # ── repo ──────────────────────────────────────────────────────
    try:
        repo = find_git_root()
        _row(True, 'git repo', repo)
    except Exception:
        _row(False, 'not in a git repo', 'run from inside a git repository')
        errors += 1
        repo = None

    # ── context dir ───────────────────────────────────────────────
    if repo:
        ctx = resolve_context_dir(repo)
        if has_context_dir(repo):
            label = f'{os.path.basename(ctx)}/'
            if os.path.basename(ctx) == LEGACY_CONTEXT_DIR:
                _row(None, label, f'legacy name — migrate to {CONTEXT_DIR}/')
            else:
                _row(True, label, ctx)

            for fname in ('ARCHITECTURE.md', 'DECISIONS.md', 'GOTCHAS.md', 'SYMBOLS.md'):
                p = os.path.join(ctx, fname)
                if os.path.exists(p) and os.path.getsize(p) > 0:
                    size_kb = os.path.getsize(p) / 1024
                    _row(True, f'  {fname}', f'{size_kb:.1f} KB')
                else:
                    _row(None, f'  {fname} missing', 'run `cram sync` to rebuild')

            task_p = os.path.join(ctx, 'CURRENT_TASK.md')
            if os.path.exists(task_p) and os.path.getsize(task_p) > 100:
                # Extract task name for display
                task_name = ''
                try:
                    for line in open(task_p, errors='ignore'):
                        s = line.strip()
                        if s.startswith('# Task:'):
                            task_name = s[len('# Task:'):].strip()
                            break
                except Exception:
                    pass
                _row(True, '  CURRENT_TASK.md', task_name or 'task set')
            else:
                _row(None, '  CURRENT_TASK.md not set', 'run `cram task "..."` before your session')

            # committed to git?
            try:
                r = subprocess.run(
                    ['git', 'ls-files', '--error-unmatch',
                     os.path.relpath(os.path.join(ctx, 'ARCHITECTURE.md'), repo)],
                    cwd=repo, capture_output=True,
                )
                if r.returncode == 0:
                    _row(True, 'context committed to git', 'teammates get context automatically')
                else:
                    _row(None, 'context not committed to git',
                         f'git add {CONTEXT_DIR}/ && git commit -m "chore: init cram-ai"')
            except Exception:
                pass
        else:
            _row(False, f'{CONTEXT_DIR}/ not found', 'run `cram init` first')
            errors += 1

    # ── git hooks ─────────────────────────────────────────────────
    if repo:
        hooks = os.path.join(repo, '.git', 'hooks')
        pc  = os.path.exists(os.path.join(hooks, 'post-commit'))
        cm  = os.path.exists(os.path.join(hooks, 'commit-msg'))
        if pc and cm:
            _row(True, 'git hooks', 'post-commit + commit-msg installed')
            _detail('ARCHITECTURE.md and SYMBOLS.md auto-update on every commit')
        elif pc:
            _row(None, 'git hooks', 'post-commit only — re-run `cram hook install` to add commit-msg')
            _detail('decision-language prompts on commit are disabled')
        else:
            _row(None, 'git hooks not installed', 'run `cram hook install` to enable auto-sync on commit')
            _detail('without hooks, run `cram sync` manually after making changes')

    # ── models ────────────────────────────────────────────────────
    print()
    print('  Models:')
    try:
        available = discover_models()
        if not available:
            _row(False, 'no models found')
            errors += 1
            print()
            print('  Set one of these environment variables:')
            print('    export ANTHROPIC_API_KEY=sk-ant-...    # Claude Haiku + Sonnet')
            print('    export OPENAI_API_KEY=sk-...           # GPT-4o Mini + GPT-4o')
            print('    export GEMINI_API_KEY=...              # Gemini 2.0 Flash + 2.5 Pro')
            print('    # or install Ollama for free local inference (auto-detected)')
            print('    # or install the `claude` CLI (auto-detected, no key needed)')
        else:
            ctx_m  = pick_context_model(available)
            code_m = pick_coding_model(available)
            _row(True, 'context model (best available)', ctx_m['name']  if ctx_m  else '—')
            _detail('used for: cram task / get_context() — lightweight, fast')
            _row(True, 'coding model (best available)',  code_m['name'] if code_m else '—')
            _detail('recommendation only — cram does not control which model you use in your editor')
            all_models = sorted({m['name'] for m in available} - {
                ctx_m['name'] if ctx_m else '', code_m['name'] if code_m else ''
            } - {''})
            if all_models:
                _row(True, 'other detected models', ', '.join(all_models))
    except Exception as exc:
        _row(None, f'model discovery error: {exc}')

    # ── summary ───────────────────────────────────────────────────
    print()
    if errors == 0:
        print('  All checks passed.\n')
    else:
        print(f'  {errors} error(s) found — see above for fixes.\n')

    sys.exit(0 if errors == 0 else 1)
