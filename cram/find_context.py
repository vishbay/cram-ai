"""Pre-session file discovery: identifies relevant excerpts and populates CURRENT_TASK.md."""

from __future__ import annotations
import os
import re
import sys

from cram.utils import (
    call_context_model,
    call_model,  # noqa: F401 — re-exported so tests can assert it is NOT called
    get_model_recommendations,
    find_git_root as _find_git_root,
)
from cram.context_dir import CONTEXT_DIR, context_path, has_context_dir
from cram import targets as _targets

MAX_FILES         = int(os.environ.get('AICONTEXT_MAX_FILES',        '5'))
MAX_EXCERPT_LINES = int(os.environ.get('AICONTEXT_MAX_EXCERPT_LINES', '80'))
MAX_LINES         = MAX_EXCERPT_LINES  # alias kept for test compatibility

_STOP_WORDS = frozenset({
    'the', 'a', 'an', 'in', 'for', 'of', 'and', 'or', 'is', 'it', 'to',
    'fix', 'add', 'update', 'change', 'get', 'set', 'put', 'use', 'make',
    'with', 'by', 'at', 'from', 'on', 'up', 'out', 'new', 'old', 'this',
    'that', 'into', 'via', 'not', 'do', 'be', 'run', 'need',
})


# ── file helpers ──────────────────────────────────────────────────


def _read_context_file(filename: str) -> str:
    path = context_path('.', filename, warn=True)
    if not os.path.exists(path):
        return ''
    with open(path) as f:
        return f.read()


def _read_truncated(path: str) -> str:
    """Read a file, truncating to MAX_LINES lines if needed."""
    with open(path, errors='ignore') as f:
        lines = f.readlines()
    if len(lines) > MAX_LINES:
        omitted = len(lines) - MAX_LINES
        return ''.join(lines[:MAX_LINES]) + f'\n... [{omitted} lines omitted]\n'
    return ''.join(lines)


def _resolve_path(raw: str, root: str = '.') -> str:
    real_root = os.path.realpath(os.path.abspath(root))

    def _within_root(p: str) -> bool:
        rp = os.path.realpath(p)
        return rp == real_root or rp.startswith(real_root + os.sep)

    # Absolute paths must resolve inside the repo root
    if os.path.isabs(raw):
        if not _within_root(raw):
            return raw  # rejected — will fail os.path.exists() check downstream
        return os.path.relpath(os.path.realpath(raw), root)

    # Relative path: try resolving directly within root first
    candidate = os.path.join(root, raw)
    if os.path.exists(candidate) and _within_root(candidate):
        return os.path.relpath(os.path.realpath(candidate), root)

    # Walk to find by basename, verifying each match stays within root
    _skip = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'dist', 'build'}
    basename = os.path.basename(raw)
    if basename:
        matches: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _skip and not d.startswith('.')]
            if basename in filenames:
                candidate = os.path.join(dirpath, basename)
                if _within_root(candidate):
                    matches.append(os.path.relpath(os.path.realpath(candidate), root))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Prefer the match whose directory components overlap most with the
            # original raw path string (the model's hint about the path).
            raw_parts = set(raw.replace('\\', '/').split('/')[:-1])  # dir parts only
            def _overlap(m: str) -> int:
                m_parts = set(m.replace('\\', '/').split('/')[:-1])
                return len(raw_parts & m_parts)
            best_score = max(_overlap(m) for m in matches)
            best = [m for m in matches if _overlap(m) == best_score]
            if len(best) == 1:
                return best[0]
            # Still ambiguous — return raw so it surfaces as "not found" rather
            # than silently picking the wrong file
            return raw
    return raw


def _clean_path(line: str) -> str:
    line = line.strip()
    line = re.sub(r'^[-*\d.]+\s*', '', line)
    line = line.strip('`').strip()
    if ' ' in line:
        return ''
    if '/' not in line and '.' not in line:
        return ''
    return line


def _score_files(
    task: str, symbols_text: str
) -> list[tuple[str, float, list[str]]]:
    """Score files from SYMBOLS.md by keyword overlap with the task.

    Returns (filepath, score, [symbol, ...]) sorted descending by score.
    Scoring: +2.0 per keyword in filename stem, +1.5 per keyword in a directory
    component, +1.0 per keyword in a symbol name.
    """
    words = re.split(r'[^a-zA-Z0-9]+', task.lower())
    keywords = [w for w in words if len(w) >= 3 and w not in _STOP_WORDS]
    if not keywords:
        return []

    results: list[tuple[str, float, list[str]]] = []
    for line in symbols_text.splitlines():
        if ': ' not in line:
            continue
        filepath, sym_part = line.split(': ', 1)
        filepath = filepath.strip()
        if not filepath:
            continue
        syms     = [s.strip() for s in sym_part.split(',') if s.strip()]
        sym_lower = [s.lower() for s in syms]
        stem     = os.path.splitext(os.path.basename(filepath))[0].lower()
        parts    = filepath.lower().replace('\\', '/').split('/')

        score = 0.0
        for kw in keywords:
            if kw in stem:
                score += 2.0
            for part in parts[:-1]:      # directory components only
                if kw in part:
                    score += 1.5
                    break
            for sym in sym_lower:
                if kw in sym:
                    score += 1.0
                    break
        if score > 0:
            results.append((filepath, score, syms))

    return sorted(results, key=lambda x: x[1], reverse=True)


# ── excerpt extraction ────────────────────────────────────────────


def _extract_excerpt(fpath: str, identifiers: list[str], root: str = '.') -> str:
    """Return an identifier-focused excerpt of a file, or the full file if small.

    fpath is kept as a relative display path; the actual open uses
    os.path.join(root, fpath) so callers don't need to os.chdir first.
    """
    abs_path = fpath if os.path.isabs(fpath) else os.path.join(root, fpath)
    with open(abs_path, errors='ignore') as f:
        lines = f.readlines()

    total = len(lines)
    if total <= MAX_EXCERPT_LINES:
        return ''.join(lines)

    if not identifiers:
        omitted = total - MAX_EXCERPT_LINES
        return ''.join(lines[:MAX_EXCERPT_LINES]) + f'\n... [{omitted} lines omitted]\n'

    kw_lower = [k.lower() for k in identifiers]
    window   = 15

    matched: set[int] = set()
    for i, line in enumerate(lines):
        ll = line.lower()
        if any(kw in ll for kw in kw_lower):
            for j in range(max(0, i - window), min(total, i + window + 1)):
                matched.add(j)

    if not matched:
        omitted = total - MAX_EXCERPT_LINES
        return ''.join(lines[:MAX_EXCERPT_LINES]) + f'\n... [{omitted} lines omitted]\n'

    sorted_idx = sorted(matched)[:MAX_EXCERPT_LINES]

    parts: list[str] = [f'[lines {sorted_idx[0]+1}–{sorted_idx[-1]+1} of {total}]\n']
    prev = -2
    for i in sorted_idx:
        if i > prev + 1:
            if prev >= 0:
                parts.append(f'  ··· {i - prev - 1} lines omitted ···\n')
        parts.append(lines[i])
        prev = i
    if sorted_idx[-1] < total - 1:
        parts.append(f'  ··· {total - sorted_idx[-1] - 1} more lines\n')

    return ''.join(parts)


# ── file selection ────────────────────────────────────────────────


def _parse_file_line(raw: str) -> tuple[str, list[str]]:
    """Parse 'path/to/file.py | id1, id2' into (path, [id1, id2])."""
    if '|' in raw:
        path_part, id_part = raw.split('|', 1)
        path = _clean_path(path_part)
        ids  = [i.strip() for i in id_part.split(',') if i.strip()]
    else:
        path = _clean_path(raw)
        ids  = []
    return path, ids


def find_relevant_files(
    task: str, arch: str, decisions: str = '', symbols: str = '', gotchas: str = '',
    root: str = '.',
) -> list[tuple[str, list[str]]]:
    """Ask the context model to identify relevant files + their key identifiers.

    Returns a list of (resolved_path, [identifier, ...]) tuples.

    DECISIONS and GOTCHAS are accepted for back-compat but not included in the
    selection prompt — they don't help pick files and bloat the call.
    """
    if symbols:
        scored = _score_files(task, symbols)
        if scored:
            hint = '\n'.join(
                f'  {p} ({sc:.1f}) — {", ".join(ss[:4])}{"…" if len(ss) > 4 else ""}'
                for p, sc, ss in scored[:5]
            )
            symbols_section = (
                f"Top candidates by keyword match:\n{hint}\n\n"
                f"Full symbol index:\n{symbols}\n\n"
            )
        else:
            symbols_section = f"Symbol index (file → public identifiers):\n{symbols}\n\n"
    else:
        symbols_section = ''

    prompt = (
        f"Repo architecture:\n{arch}\n\n"
        f"{symbols_section}"
        f'Task: "{task}"\n\n'
        f"List ONLY the files DIRECTLY needed to complete this task.\n"
        f"For each file, include the specific identifiers (functions/classes) most relevant to the task.\n"
        f"Rules:\n"
        f"- UI/styling tasks → CSS and HTML files only\n"
        f"- Backend/API tasks → Python/Go/etc files only\n"
        f"- 1–3 files is almost always enough\n"
        f"- Max {MAX_FILES} files\n\n"
        f"Output format — one file per line:\n"
        f"  relative/path/to/file.ext | RelevantFunc, AnotherClass\n"
        f"If no specific identifiers match, output path only. No explanation."
    )
    raw_lines = call_context_model(prompt).strip().splitlines()

    results: list[tuple[str, list[str]]] = []
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        path, ids = _parse_file_line(raw)
        if not path:
            continue
        resolved = _resolve_path(path, root)
        results.append((resolved, ids))
        if len(results) >= MAX_FILES:
            break
    return results


# ── task history ─────────────────────────────────────────────────


def _archive_current_task_to_history() -> None:
    """Archive the current CURRENT_TASK.md entry to TASK_HISTORY.jsonl before it's replaced."""
    from cram.session import archive_task as _archive_task
    current_path = context_path('.', 'CURRENT_TASK.md', warn=False)
    if not current_path:
        return
    root = _find_git_root(os.getcwd())
    _archive_task(root, current_path)


# ── context assembly ──────────────────────────────────────────────


def _arch_summary(arch: str, max_lines: int = 25) -> str:
    """Extract the first max_lines non-blank lines of ARCHITECTURE.md."""
    collected = []
    for line in arch.splitlines():
        if line.strip():
            collected.append(line)
        if len(collected) >= max_lines:
            break
    return '\n'.join(collected)


def populate_current_task(
    task: str,
    file_entries,  # list[str] or list[tuple[str, list[str]]]
    ctx_model: str = '',
    coding_model: str = '',
    output_path: str | None = None,
    root: str = '.',
) -> list[str]:
    """Write CURRENT_TASK.md (or output_path) with identifier-focused excerpts. Returns files inlined.

    root is used for resolving relative file paths without os.chdir — callers
    can pass the repo root and keep cwd wherever they like.
    """
    # Normalize: accept both plain string paths and (path, identifiers) tuples
    normalized = [
        (e, []) if isinstance(e, str) else e
        for e in file_entries
    ]

    def _exists(f: str) -> bool:
        return os.path.exists(f if os.path.isabs(f) else os.path.join(root, f))

    found   = [(f, ids) for f, ids in normalized if _exists(f)]
    missing = [f for f, _ in normalized if not _exists(f)]

    target_path = output_path or context_path('.', 'CURRENT_TASK.md', warn=True)
    if output_path:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, 'w') as out:
        out.write(f"# Current Task\n\n## Task\n{task}\n\n")

        # Contract fields — Scope auto-derived, Out of Scope / DoD left for user
        scope_dirs = sorted({os.path.dirname(f) or '.' for f, _ in found})
        out.write('## Scope\n')
        for d in scope_dirs:
            label = (d + '/') if d and d != '.' else '.'
            out.write(f'- {label}\n')
        if not scope_dirs:
            out.write('<!-- Populated after file selection -->\n')
        out.write('\n')
        out.write('## Out of Scope\n')
        out.write('<!-- Add directories/files the agent should NOT touch -->\n\n')
        out.write('## Definition of Done\n')
        out.write('<!-- Add explicit acceptance criteria before closing this task -->\n\n')

        if ctx_model or coding_model:
            out.write("## Models\n")
            if ctx_model:
                out.write(f"- Context loaded by: `{ctx_model}`\n")
            if coding_model:
                out.write(f"- **Switch to `{coding_model}` for coding** ←\n")
            out.write('\n')

        if missing:
            out.write("## Notes\n")
            for m in missing:
                out.write(f"- `{m}` suggested but not found on disk\n")
            out.write('\n')

        out.write("## Relevant Files\n")
        for fpath, ids in found:
            ext     = os.path.splitext(fpath)[1].lstrip('.')
            excerpt = _extract_excerpt(fpath, ids, root=root)
            out.write(f"\n### {fpath}\n```{ext}\n{excerpt}\n```\n")

    return [f for f, _ in found]


# ── main entry ────────────────────────────────────────────────────


def find_context(task: str, target: str | None = None, inject: bool = False, root: str = '.') -> None:
    if inject:
        print(
            "Warning: --inject is deprecated; MCP delivery is the supported path. See README.",
            file=sys.stderr,
        )

    if not has_context_dir('.'):
        print(f"Error: {CONTEXT_DIR}/ not found. Run `cram init` first.", file=sys.stderr)
        sys.exit(1)

    arch      = _read_context_file('ARCHITECTURE.md')
    decisions = _read_context_file('DECISIONS.md')
    gotchas   = _read_context_file('GOTCHAS.md')
    symbols   = _read_context_file('SYMBOLS.md')

    if not arch:
        print(
            f"Warning: {CONTEXT_DIR}/ARCHITECTURE.md is empty. "
            "Run `cram sync` to rebuild it.",
            file=sys.stderr,
        )

    ctx_model, coding_model = get_model_recommendations()

    # ── Stage 1: symbol pre-filter ───────────────────────────────
    scored = _score_files(task, symbols) if symbols else []
    if scored:
        n = len(scored)
        print(f"[1/4] Symbol pre-filter → {n} candidate{'s' if n != 1 else ''}")
        for path, score, syms in scored[:5]:
            sym_note = f" ({', '.join(syms[:3])}{'…' if len(syms) > 3 else ''})" if syms else ''
            print(f"  → {path}  ({score:.1f}){sym_note}")
        if n > 5:
            print(f"  … {n - 5} more")
    elif symbols:
        sym_count = sum(
            len(line.split(': ', 1)[1].split(','))
            for line in symbols.splitlines() if ': ' in line
        )
        print(f"[1/4] Symbol index ready — {sym_count} identifiers (no keyword matches, using full index)")
    else:
        print("[1/4] Symbol index not found — run `cram sync` for better file selection")

    # ── Stage 2: file selection (LLM call) ───────────────────────
    print(f"[2/4] Identifying relevant files via {ctx_model} ...")
    sys.stdout.flush()

    file_entries = find_relevant_files(task, arch, decisions, symbols, gotchas, root=root)
    found_entries = [(f, ids) for f, ids in file_entries if os.path.exists(f)]
    missing       = [f for f, _ in file_entries if not os.path.exists(f)]

    if not file_entries:
        print("No files identified. Check that ARCHITECTURE.md describes the repo structure.")
        return

    for fpath, ids in found_entries:
        id_note = f" ({', '.join(ids[:3])}{'…' if len(ids) > 3 else ''})" if ids else ''
        print(f"  → {fpath}{id_note}")
    if missing:
        print(f"  (skipped {len(missing)} not found on disk)")

    # Warn about truncation (Gap 6)
    total_suggested = len(file_entries)
    if total_suggested >= MAX_FILES:
        print(f"  Note: capped at {MAX_FILES} files — set AICONTEXT_MAX_FILES to raise limit")

    # ── Stage 3: excerpt extraction ───────────────────────────────
    print(f"[3/4] Extracting focused excerpts from {len(found_entries)} file(s) ...")
    sys.stdout.flush()

    for fpath, ids in found_entries:
        excerpt = _extract_excerpt(fpath, ids)
        tok = len(excerpt) // 4
        id_note = f" ({', '.join(ids[:2])}{'…' if len(ids) > 2 else ''})" if ids else ''
        print(f"  → {fpath}{id_note}  ~{tok:,} tokens")

    # ── Stage 4: write context ───────────────────────────────────
    print("[4/4] Writing context ...")
    sys.stdout.flush()

    _archive_current_task_to_history()
    populate_current_task(task, file_entries, ctx_model, coding_model)

    task_path = context_path('.', 'CURRENT_TASK.md', warn=True)
    with open(task_path) as f:
        tokens = len(f.read()) // 4

    if target and (target != 'claude' or inject):
        arch_content = _read_context_file('ARCHITECTURE.md')
        with open(task_path) as fh:
            task_content = fh.read()
        root = _find_git_root(os.getcwd())
        effective = _targets.get_effective_targets(root)
        if target != 'all' and target not in effective:
            valid = ', '.join(sorted(effective)) + ', all'
            print(f"Error: unknown target '{target}'. Valid: {valid}", file=sys.stderr)
            sys.exit(1)
        if target == 'all':
            written = _targets.write_to_all_detected(root, task_content, arch_content)
            for p in written:
                print(f"  → {os.path.relpath(p)}")
            if not written:
                print("  (no known tool indicators found — try a specific --target)")
        else:
            path = _targets.write_to_target(root, target, task_content, arch_content,
                                             inject=(target == 'claude' and inject))
            print(f"  → {os.path.relpath(path)}")

    # ── Save session metadata ─────────────────────────────────────
    try:
        from cram.session import save_session
        root = _find_git_root(os.getcwd())
        expiry = save_session(root, task)
    except Exception:
        root = _find_git_root(os.getcwd())
        expiry = None

    print(f"\n✓ Context ready — ~{tokens:,} tokens")
    if not (target and target != 'claude') and not inject:
        print(f'  Call get_context("{task}") via the cram-ai MCP server to load it.')
    print("  Run `cram audit` to measure orientation tax on your real sessions.")
    if expiry:
        print(f"  Context resets on commit after {expiry} "
              f"(run `cram continue` to extend)")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog='cram task',
        description='Populate CURRENT_TASK.md before a coding session',
    )
    parser.add_argument('task', nargs='+', help='Task description')
    parser.add_argument(
        '--target',
        default=None,
        metavar='TARGET',
        help=(
            'Auto-load context into the tool\'s instruction file. '
            f"Builtins: {', '.join(_targets.TARGET_FILES)} | all. "
            'Custom targets from .ai-context/config.toml [targets.<name>] are also accepted. '
            'Falls back to default_target in .ai-context/config.toml.'
        ),
    )
    parser.add_argument(
        '--inject',
        action='store_true',
        help='[DEPRECATED] Write task content into CLAUDE.md. MCP delivery is the supported path. See README.',
    )
    parser.add_argument('--path', default=None, metavar='REPO_PATH')
    args = parser.parse_args()

    start = os.path.abspath(args.path) if args.path else os.getcwd()
    root  = _find_git_root(start)

    if not has_context_dir(root):
        print(
            f"Error: {CONTEXT_DIR}/ not found in {root}.\n"
            "  Run `cram init` first, or use --path to point at your repo:\n"
            f"  cram task \"{' '.join(args.task)}\" --path /path/to/repo",
            file=sys.stderr,
        )
        sys.exit(1)

    os.chdir(root)
    task = ' '.join(args.task)
    effective_target = args.target or _targets.load_default_target(root)
    find_context(task, effective_target, inject=args.inject, root=root)


if __name__ == '__main__':
    main()
