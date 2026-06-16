"""cram status — show .ai-context/ file freshness and repo sync state."""

from __future__ import annotations
import os
import subprocess
import sys
from datetime import datetime, timezone

from cram.context_dir import (
    CONTEXT_DIR,
    LEGACY_CONTEXT_DIR,
    context_basename,
    has_context_dir,
    resolve_context_dir,
)

CONTEXT_FILES = ['ARCHITECTURE.md', 'DECISIONS.md', 'GOTCHAS.md', 'CURRENT_TASK.md', '.gitignore']

# Commits-since-update that maps to score 10 (critical). Override via env.
STALE_CRITICAL_COMMITS = int(os.environ.get('CRAM_STALE_CRITICAL_COMMITS', '10'))


def _mtime(path: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    except OSError:
        return None


def _last_commit_time(root: str = '.') -> datetime | None:
    try:
        ts = subprocess.check_output(
            ['git', 'log', '-1', '--format=%ct'],
            cwd=root,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
    except (subprocess.CalledProcessError, ValueError):
        return None


def _age_label(dt: datetime) -> str:
    now = datetime.now(tz=timezone.utc)
    secs = int((now - dt).total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _line_count(path: str) -> int:
    try:
        with open(path) as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _arch_structure_current(root: str) -> bool | None:
    """True if ARCHITECTURE.md's embedded structure fingerprint matches the
    current repo structure.

    Since #24, `cram sync` only rewrites ARCHITECTURE.md when the repo structure
    changes — a matching fingerprint is exactly the condition under which sync
    skips the regen, so the file is fresh *by design* even when commits have
    landed since it was last written. Returns None when there's no embedded hash
    (pre-#24 file) so callers fall back to commit-count staleness.
    """
    context_dir = resolve_context_dir(root)
    arch_path = os.path.join(context_dir, 'ARCHITECTURE.md')
    try:
        with open(arch_path, errors='ignore') as f:
            content = f.read()
    except OSError:
        return None

    from cram.sync_context import _stored_structure_hash, _structure_hash
    stored = _stored_structure_hash(content)
    if stored is None:
        return None

    from cram.init import scan_structure
    try:
        return stored == _structure_hash(scan_structure(root))
    except Exception:
        return None


def _commits_since_context_update(root: str) -> int | None:
    """Commits on HEAD since ARCHITECTURE.md was last synced.

    If the embedded structure fingerprint still matches the repo, the file is
    fresh by design (#24) — return 0 without counting intervening non-structural
    commits, which sync intentionally does not regenerate for.

    Otherwise: cram sync writes ARCHITECTURE.md to the working tree but doesn't
    commit it, so we check mtime first — if the file is newer than the last
    commit it's already up to date (0) — then fall back to counting commits
    since it was last committed.
    """
    if _arch_structure_current(root):
        return 0

    rel_dir = CONTEXT_DIR if os.path.isdir(os.path.join(root, CONTEXT_DIR)) else LEGACY_CONTEXT_DIR
    rel = os.path.join(rel_dir, 'ARCHITECTURE.md')
    arch_path = os.path.join(root, rel)
    try:
        sha = subprocess.check_output(
            ['git', 'log', '-1', '--format=%H', '--', rel],
            cwd=root, stderr=subprocess.DEVNULL,
        ).decode().strip()
        if not sha:
            return None
        # cram sync writes ARCHITECTURE.md but doesn't commit it. If the
        # working-tree file differs from HEAD it was already updated this sync.
        dirty = subprocess.run(
            ['git', 'diff', '--quiet', 'HEAD', '--', rel],
            cwd=root, stderr=subprocess.DEVNULL,
        ).returncode != 0
        if dirty:
            return 0
        count = subprocess.check_output(
            ['git', 'rev-list', '--count', f'{sha}..HEAD'],
            cwd=root, stderr=subprocess.DEVNULL,
        ).decode().strip()
        return int(count)
    except (subprocess.CalledProcessError, ValueError):
        return None


def staleness_score(commits_since: int | None, arch_behind_commit: bool) -> int:
    """0–10. Primary: commits since context last changed; fallback: mtime signal.

    Falls back to the legacy mtime signal (arch_behind_commit) when the commit
    count is unknown: behind → 6 (stale), else 0 (fresh).
    """
    if commits_since is None:
        return 6 if arch_behind_commit else 0
    scaled = round(commits_since / STALE_CRITICAL_COMMITS * 10)
    return max(0, min(10, scaled))


def staleness_band(score: int) -> str:
    if score <= 2: return 'fresh'
    if score <= 5: return 'acceptable'
    if score <= 7: return 'stale'
    return 'critical'


def get_status_dict(root: str = '.') -> dict:
    """Return structured status data for programmatic use (MCP, CLI)."""
    root = os.path.abspath(root)
    context_dir = resolve_context_dir(root)

    if not has_context_dir(root):
        return {'state': 'not-init', 'files': {}, 'last_commit_age': None}

    last_commit = _last_commit_time(root)
    now = datetime.now(tz=timezone.utc)
    files: dict = {}
    arch_behind_commit = False

    for fname in ('ARCHITECTURE.md', 'DECISIONS.md', 'GOTCHAS.md', 'CURRENT_TASK.md'):
        fpath = os.path.join(context_dir, fname)
        if not os.path.exists(fpath):
            continue
        mtime = _mtime(fpath)
        if mtime:
            age_secs = int((now - mtime).total_seconds())
            files[fname] = {
                'age_secs':  age_secs,
                'age_label': _age_label(mtime),
                'lines':     _line_count(fpath),
            }
            if fname == 'ARCHITECTURE.md' and last_commit and last_commit > mtime:
                arch_behind_commit = True

    commits_since = _commits_since_context_update(root)
    score = staleness_score(commits_since, arch_behind_commit)
    band  = staleness_band(score)

    return {
        'state':              'stale' if band in ('stale', 'critical') else 'fresh',
        'staleness_score':    score,
        'staleness_band':     band,
        'commits_since_sync': commits_since,
        'files':              files,
        'last_commit_age':    _age_label(last_commit) if last_commit else None,
    }


def show_status(root: str = '.') -> None:
    root = os.path.abspath(root)
    context_dir = resolve_context_dir(root, warn=True)

    if not has_context_dir(root):
        print(f"No .ai-context/ found in {root}.")
        print("Run `cram init` to set it up.")
        sys.exit(1)

    from cram.cost_model import budget_status as _budget_status

    last_commit = _last_commit_time(root)

    print(f"{context_basename(root)}/  ({context_dir})")
    print()

    for fname in CONTEXT_FILES:
        fpath = os.path.join(context_dir, fname)
        if not os.path.exists(fpath):
            print(f"  {'MISSING':10s}  {fname}")
            continue

        mtime = _mtime(fpath)
        age = _age_label(mtime) if mtime else '?'
        lines = _line_count(fpath)

        flag = ''
        if (fname == 'ARCHITECTURE.md' and last_commit and mtime
                and last_commit > mtime and _arch_structure_current(root) is not True):
            flag = '  ← stale (commit after last sync)'

        try:
            with open(fpath, errors='ignore') as f:
                tokens = len(f.read()) // 4
            bstatus = _budget_status(fname, tokens)
            if bstatus == 'over':
                flag += f'  (over budget: {tokens} tok)'
            elif bstatus == 'near':
                flag += f'  (near budget: {tokens} tok)'
        except OSError:
            pass

        print(f"  {age:12s}  {fname}  ({lines} lines){flag}")

    print()
    status = get_status_dict(root)
    score   = status['staleness_score']
    band    = status['staleness_band']
    commits = status['commits_since_sync']

    if last_commit:
        print(f"Last commit : {_age_label(last_commit)}")

    msg = f"Staleness : {score}/10 ({band})"
    if commits is not None:
        plural = 's' if commits != 1 else ''
        msg += f" — {commits} commit{plural} since last sync."
        if band in ('stale', 'critical'):
            msg += " Run `cram sync`."
    print(msg)

    try:
        from cram.targets import load_output_config
        byte_cap = load_output_config(root)['byte_cap']
    except Exception:
        byte_cap = 6000
    print(f"Output protection: active ({byte_cap:,} byte cap)   ✓")


def main() -> None:
    from cram.utils import find_git_root
    target = find_git_root(sys.argv[1] if len(sys.argv) > 1 else '.')
    show_status(target)


if __name__ == '__main__':
    main()
