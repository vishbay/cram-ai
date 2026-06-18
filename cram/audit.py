"""cram audit — diagnose where AI coding-agent sessions spend tokens and context.

Parsing and metric derivation live in cram.audit_events (adapters produce
normalized Event streams; derive_session replays them). This module keeps the
public surface: discovery helpers, the legacy _analyze_* entry points (now thin
parse→derive wrappers), collect_audit, and the CLI report/compare commands.
"""

from __future__ import annotations
import json
import os
import sys
import glob
import datetime

from cram import audit_events
from cram import audit_findings
from cram import audit_store
# Back-compat re-exports: these names lived here before the event-store split
# and are imported by tests and external callers.
from cram.audit_events import (  # noqa: F401
    READ_TOOLS, WRITE_TOOLS, BASH_READ_CMDS,
    CURSOR_READ_TOOLS, CURSOR_WRITE_TOOLS, CURSOR_BASH_TOOL,
    _CODEX_WRITE_PATCH_RE,
    _find_all_tool_use, _find_tool_results, _find_usage,
    _cursor_files_from_entry,
)

CONTEXT_DIR = '.ai-context'

# Cost-model assumptions (overridable via env vars)
# Rough average tokens per file excerpt read during orientation.
# Override with: CRAM_AUDIT_TOK_PER_FILE=2500
AUDIT_TOK_PER_FILE: int = int(os.environ.get('CRAM_AUDIT_TOK_PER_FILE', '2500'))
# Dollar attribution is provider-pluggable: select with CRAM_PROVIDER
# (anthropic | openai | gemini | local), override individual fields via
# CRAM_PRICE_INPUT_PER_MTOK / CRAM_CACHE_WRITE_MULT / CRAM_CACHE_READ_MULT.
from cram.cost_model import get_provider_pricing, resolve_provider

AUDIT_PROVIDER: str = resolve_provider()
_PRICING = get_provider_pricing(AUDIT_PROVIDER)
# Base input price per token (USD). CRAM_AUDIT_BASE_PRICE wins over the
# provider table for backward compatibility.
AUDIT_BASE_PRICE: float = float(os.environ.get(
    'CRAM_AUDIT_BASE_PRICE', str(_PRICING['input_per_mtok'] / 1_000_000)))
# A tool result above this serialized size counts as oversized — it gets
# carried (re-read) by every subsequent request in the session.
# Override with: CRAM_AUDIT_BIG_RESULT_BYTES=20000
BIG_RESULT_BYTES: int = int(os.environ.get('CRAM_AUDIT_BIG_RESULT_BYTES', '20000'))
# A cold run ingesting more than this many transcripts announces itself on
# stderr — otherwise a large first run reads as a hang.
INGEST_PROGRESS_MIN: int = 50
# Cache multipliers vs base input price (0.1x read / 1.25x write on Anthropic).
CACHE_READ_MULT: float = _PRICING['cache_read_mult']
CACHE_WRITE_MULT: float = _PRICING['cache_write_mult']


def _analyze_transcript(path: str) -> dict | None:
    """Parse a Claude Code transcript and return its session metrics dict."""
    parsed = audit_events.parse_claude(path)
    if parsed is None:
        return None
    meta, events = parsed
    try:
        return audit_events.derive_session(meta, events,
                                           big_result_bytes=BIG_RESULT_BYTES)
    except Exception:
        return None


# ── Cursor support ────────────────────────────────────────────────────────────

def _cursor_agent_transcripts_dir() -> str | None:
    """Return ~/.cursor/agent-transcripts/ if it exists."""
    path = os.path.join(os.path.expanduser('~'), '.cursor', 'agent-transcripts')
    return path if os.path.isdir(path) else None


def _cursor_storage_root() -> str | None:
    """Return the Cursor User storage root for the current platform, or None."""
    candidates = [
        # macOS
        os.path.join(os.path.expanduser('~'), 'Library', 'Application Support',
                     'Cursor', 'User'),
        # Linux / XDG
        os.path.join(os.path.expanduser('~'), '.config', 'Cursor', 'User'),
    ]
    appdata = os.environ.get('APPDATA', '')
    if appdata:
        candidates.append(os.path.join(appdata, 'Cursor', 'User'))
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def _analyze_cursor_transcript(path: str, repo_root: str) -> dict | None:
    """Parse a Cursor agent-transcript JSONL file for tool-call metrics.

    Only events associated with repo_root (via vcs.root or file paths under
    repo_root) are counted. Returns None if no relevant activity found.
    Token-based metrics are unavailable for Cursor sessions and are zeroed.
    """
    parsed = audit_events.parse_cursor_jsonl(path)
    if parsed is None:
        return None
    meta, events = parsed
    try:
        return audit_events.derive_session(meta, events, repo_root,
                                           big_result_bytes=BIG_RESULT_BYTES)
    except Exception:
        return None


def _analyze_cursor_workspace_db(db_path: str, repo_root: str,
                                  cutoff: datetime.datetime) -> list[dict]:
    """Parse Cursor workspace SQLite (state.vscdb) for tool-call sessions.

    Returns one session dict per composer session with activity under
    repo_root since cutoff. Empty list if sqlite3 is unavailable or the
    schema differs.
    """
    sessions: list[dict] = []
    for meta, events in audit_events.parse_cursor_db(db_path):
        if meta.event_mtime and datetime.datetime.fromtimestamp(meta.event_mtime) < cutoff:
            continue
        try:
            r = audit_events.derive_session(meta, events, repo_root,
                                            big_result_bytes=BIG_RESULT_BYTES)
        except Exception:
            r = None
        if r:
            sessions.append(r)
    return sessions


def _collect_cursor_sessions(store, repo_root: str, cutoff: datetime.datetime,
                             reingest: bool = False) -> list[dict]:
    """Return Cursor session dicts for repo_root since cutoff, via the store.

    Tries agent-transcripts (JSONL) first, then workspace SQLite databases.
    """
    sessions: list[dict] = []

    # ── Path 1: ~/.cursor/agent-transcripts/ ──────────────────────────────────
    at_dir = _cursor_agent_transcripts_dir()
    if at_dir:
        for path in glob.glob(os.path.join(at_dir, '*.jsonl')):
            st = os.stat(path)
            if datetime.datetime.fromtimestamp(st.st_mtime) < cutoff:
                continue
            for meta, events in _sessions_from_file(
                    store, path, 'cursor-jsonl',
                    audit_events.parse_cursor_jsonl, reingest, st):
                r = _derive(meta, events, repo_root)
                if r:
                    sessions.append(r)
        # If we found any sessions via agent-transcripts, skip SQLite.
        if sessions:
            return sessions

    # ── Path 2: workspaceStorage/<hash>/state.vscdb ───────────────────────────
    storage_root = _cursor_storage_root()
    if not storage_root:
        return []
    ws_root = os.path.join(storage_root, 'workspaceStorage')
    for db_path in glob.glob(os.path.join(ws_root, '*', 'state.vscdb')):
        st = os.stat(db_path)
        if datetime.datetime.fromtimestamp(st.st_mtime) < cutoff:
            continue
        for meta, events in _cursor_db_sessions(store, db_path, reingest, st):
            # Per-composer cutoff on bubble timestamps, as in the legacy parser.
            if meta.event_mtime and datetime.datetime.fromtimestamp(meta.event_mtime) < cutoff:
                continue
            r = _derive(meta, events, repo_root)
            if r:
                sessions.append(r)

    return sessions


# ── Codex support ─────────────────────────────────────────────────────────────

def _codex_sessions_dir() -> str | None:
    """Return ~/.codex/sessions/ if it exists."""
    path = os.path.join(os.path.expanduser('~'), '.codex', 'sessions')
    return path if os.path.isdir(path) else None


def _analyze_codex_transcript(path: str, repo_root: str) -> dict | None:
    """Parse a Codex JSONL session file and return a session dict.

    The session is associated with repo_root via session_meta.cwd or the
    workdir field of individual exec_command calls. Returns None if no
    relevant activity is found.
    """
    parsed = audit_events.parse_codex(path)
    if parsed is None:
        return None
    meta, events = parsed
    try:
        return audit_events.derive_session(meta, events, repo_root,
                                           big_result_bytes=BIG_RESULT_BYTES)
    except Exception:
        return None


def _collect_codex_sessions(store, repo_root: str, cutoff: datetime.datetime,
                            reingest: bool = False) -> list[dict]:
    """Return Codex session dicts for repo_root since cutoff, via the store."""
    sd = _codex_sessions_dir()
    if not sd:
        return []
    sessions: list[dict] = []
    for path in glob.glob(os.path.join(sd, '**', '*.jsonl'), recursive=True):
        st = os.stat(path)
        if datetime.datetime.fromtimestamp(st.st_mtime) < cutoff:
            continue
        for meta, events in _sessions_from_file(
                store, path, 'codex', audit_events.parse_codex, reingest, st):
            r = _derive(meta, events, repo_root)
            if r:
                sessions.append(r)
    return sessions


def _project_transcript_dir(repo_root: str) -> str | None:
    """Return the ~/.claude/projects/ subdirectory for this repo, or None."""
    dashed = repo_root.replace(os.sep, '-').lstrip('-')
    candidate = os.path.join(os.path.expanduser('~'), '.claude', 'projects', '-' + dashed)
    if os.path.isdir(candidate):
        return candidate
    # Also try without leading slash conversion quirk on Windows-style paths
    candidate2 = os.path.join(os.path.expanduser('~'), '.claude', 'projects', dashed)
    if os.path.isdir(candidate2):
        return candidate2
    return None


def ratio_band(ratio: float) -> str:
    """Map a read-to-edit ratio onto the documented bands: good / normal / high."""
    if ratio < 2.0:
        return 'good'
    if ratio < 5.0:
        return 'normal'
    return 'high'


# ── Cached ingestion (event store) ────────────────────────────────────────────

def _sessions_from_file(store, path: str, adapter: str, parse_fn,
                        reingest: bool, st: os.stat_result | None = None) -> list:
    """Return [(meta, events)] for a one-session-per-file transcript, cached.

    A failed parse yields nothing for this run (matching the cacheless
    behavior) but is recorded so the file is retried on the next run.
    Callers that already stat'ed the file pass `st` to avoid a second syscall.
    """
    if st is None:
        try:
            st = os.stat(path)
        except OSError:
            return []
    if reingest or store.needs_ingest(path, st.st_mtime, st.st_size):
        parsed = parse_fn(path)
        if parsed is None:
            store.mark_failed(path, adapter, st.st_mtime, st.st_size)
            return []
        store.replace_file(path, adapter, st.st_mtime, st.st_size, [parsed])
        return [parsed]
    return [(meta, store.events_for_session(sid))
            for sid, meta in store.sessions_for_file(path)]


def _cursor_db_sessions(store, db_path: str, reingest: bool,
                        st: os.stat_result | None = None) -> list:
    """Return [(meta, events)] per composer session in a state.vscdb, cached.

    parse_cursor_db can't distinguish a locked/unreadable db from a genuinely
    empty one, so an empty result is treated as a failed parse: nothing this
    run (legacy behavior either way) and a retry next run.
    """
    if st is None:
        try:
            st = os.stat(db_path)
        except OSError:
            return []
    if reingest or store.needs_ingest(db_path, st.st_mtime, st.st_size):
        parsed = audit_events.parse_cursor_db(db_path)
        if not parsed:
            store.mark_failed(db_path, 'cursor-db', st.st_mtime, st.st_size)
            return []
        store.replace_file(db_path, 'cursor-db', st.st_mtime, st.st_size, parsed)
        return parsed
    return [(meta, store.events_for_session(sid))
            for sid, meta in store.sessions_for_file(db_path)]


def _derive(meta, events, repo_root: str | None = None) -> dict | None:
    try:
        return audit_events.derive_session(meta, events, repo_root,
                                           big_result_bytes=BIG_RESULT_BYTES)
    except Exception:
        return None


def _segment_metrics(sessions: list[dict]) -> dict:
    """Context-bloat headline metrics for one segment (the buckets context-mode
    claims to fix: carried oversized results, context growth, peak context)."""
    n = len(sessions)
    if not n:
        return {'sessions': 0}
    growths = [s['context_growth_factor'] for s in sessions
               if s['context_growth_factor'] is not None]
    return {
        'sessions':                  n,
        'avg_reads_before_edit':     sum(s['reads_before_edit'] for s in sessions) / n,
        'carried_cost_per_session':  sum(s['carried_read_tokens'] for s in sessions) / n
                                     * CACHE_READ_MULT * AUDIT_BASE_PRICE,
        'sessions_with_big_results': sum(1 for s in sessions if s['big_results']),
        'avg_context_growth':        sum(growths) / len(growths) if growths else None,
        'avg_peak_context':          sum(s['peak_context'] for s in sessions) / n,
    }


def collect_audit(repo_root: str, days: int = 30, all_projects: bool = False,
                  *, reingest: bool = False,
                  failures_out: list[str] | None = None) -> dict | None:
    """Analyse transcripts and return structured audit data, or None if none found.

    This is the data core shared by the CLI report, `cram audit --json`, and the
    HTML report. Includes Cursor sessions when available (single-repo mode only).
    Transcripts are ingested into the local event store on first sight and
    re-parsed only when they change; reingest=True bypasses the cache.

    failures_out, if given, receives the paths of transcripts that failed to
    parse this run — populated even when the result is None, so callers can
    warn that "no sessions" may really mean "nothing parsed".
    """
    store = audit_store.AuditStore.open()
    try:
        return _collect_audit_inner(store, repo_root, days, all_projects, reingest)
    finally:
        if failures_out is not None:
            failures_out.extend(store.run_failures)
        store.close()


def _gather_sessions(store, repo_root: str, days: int,
                     all_projects: bool, reingest: bool) -> tuple[list[dict], list]:
    """Collect per-session dicts (Claude + Cursor + Codex) for the window.

    Shared by the aggregate audit and the per-layer drilldown so both see the
    exact same session pool.
    """
    if all_projects:
        projects_root = os.path.join(os.path.expanduser('~'), '.claude', 'projects')
        dirs = sorted(glob.glob(projects_root + '/*/'))
    else:
        td = _project_transcript_dir(repo_root)
        dirs = [td + '/'] if td else []

    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)

    all_sessions = []
    project_summaries = []

    # Pre-scan Claude transcripts so a large cold ingest can announce itself.
    claude_files: list[tuple[str, list[tuple[str, os.stat_result]]]] = []
    for proj_dir in dirs:
        name = os.path.basename(proj_dir.rstrip('/'))
        # Skip test/tmp dirs
        if 'pytest' in name or 'private-tmp' in name or 'private-var' in name:
            continue
        entries = []
        for f in glob.glob(proj_dir + '*.jsonl'):
            st = os.stat(f)
            if datetime.datetime.fromtimestamp(st.st_mtime) >= cutoff:
                entries.append((f, st))
        claude_files.append((name, entries))

    pending = sum(
        1 for _, entries in claude_files for f, st in entries
        if reingest or store.needs_ingest(f, st.st_mtime, st.st_size))
    if pending > INGEST_PROGRESS_MIN:
        print(f'cram audit: ingesting {pending} transcripts into the local '
              f'event store …', file=sys.stderr)

    for name, entries in claude_files:
        sessions = []
        for f, st in entries:
            for meta, events in _sessions_from_file(
                    store, f, 'claude', audit_events.parse_claude, reingest, st):
                r = _derive(meta, events)
                if r:
                    sessions.append(r)
                    all_sessions.append(r)

        if not sessions:
            continue

        avg_reads = sum(s['reads'] for s in sessions) / len(sessions)
        avg_rbe   = sum(s['reads_before_edit'] for s in sessions) / len(sessions)
        avg_cw    = sum(s['cache_writes'] for s in sessions) / len(sessions)
        project_summaries.append((name, len(sessions), avg_reads, avg_rbe, avg_cw))

    # Append Cursor sessions for single-repo mode.
    # --all skips Cursor/Codex (no per-project grouping available yet).
    if not all_projects:
        cursor_sessions = _collect_cursor_sessions(store, repo_root, cutoff, reingest)
        if cursor_sessions:
            all_sessions.extend(cursor_sessions)
            avg_reads = sum(s['reads'] for s in cursor_sessions) / len(cursor_sessions)
            avg_rbe   = sum(s['reads_before_edit'] for s in cursor_sessions) / len(cursor_sessions)
            project_summaries.append(
                ('cursor', len(cursor_sessions), avg_reads, avg_rbe, 0.0)
            )

        codex_sessions = _collect_codex_sessions(store, repo_root, cutoff, reingest)
        if codex_sessions:
            all_sessions.extend(codex_sessions)
            avg_reads = sum(s['reads'] for s in codex_sessions) / len(codex_sessions)
            avg_rbe   = sum(s['reads_before_edit'] for s in codex_sessions) / len(codex_sessions)
            project_summaries.append(
                ('codex', len(codex_sessions), avg_reads, avg_rbe, 0.0)
            )

    return all_sessions, project_summaries


def _collect_audit_inner(store, repo_root: str, days: int,
                         all_projects: bool, reingest: bool) -> dict | None:
    all_sessions, project_summaries = _gather_sessions(
        store, repo_root, days, all_projects, reingest)
    if not all_sessions:
        return None

    total     = len(all_sessions)
    avg_reads = sum(s['reads'] for s in all_sessions) / total
    avg_rbe   = sum(s['reads_before_edit'] for s in all_sessions) / total
    avg_edits = sum(s['edits'] for s in all_sessions) / total
    avg_ratio = sum(s['ratio'] for s in all_sessions) / total
    avg_cw    = sum(s['cache_writes'] for s in all_sessions) / total
    avg_cr    = sum(s['cache_reads'] for s in all_sessions) / total

    # Cache-engagement signal: a session that wrote cache but never read it paid
    # the 1.25× write price and got nothing back — caching may not be engaging.
    cache_engaged = sum(1 for s in all_sessions if s['cache_reads'] > 0)
    cache_blind   = sum(1 for s in all_sessions
                        if s['cache_writes'] > 0 and s['cache_reads'] == 0)

    # Measured spine for the token waterfall: effective input-side composition.
    # fresh input (×1) + cache read (×read mult) + cache write (×write mult).
    # Every token counted once → these three sum to total effective input.
    spine_fresh = float(sum(s['input_tokens'] for s in all_sessions))
    spine_read  = sum(s['cache_reads']  for s in all_sessions) * CACHE_READ_MULT
    spine_write = sum(s['cache_writes'] for s in all_sessions) * CACHE_WRITE_MULT
    token_spine = {
        'cache_read':  spine_read,
        'fresh_input': spine_fresh,
        'cache_write': spine_write,
        'total':       spine_fresh + spine_read + spine_write,
    }

    # Bucket 2: context bloat
    with_reqs        = [s for s in all_sessions if s['requests']]
    avg_requests     = sum(s['requests'] for s in all_sessions) / total
    avg_ctx_per_req  = (sum(s['avg_context_per_request'] for s in with_reqs) / len(with_reqs)
                        if with_reqs else 0.0)
    peak_context     = max((s['peak_context'] for s in all_sessions), default=0)
    tails            = [s['tail_share'] for s in all_sessions if s['tail_share'] is not None]
    bloat_tail_share = sum(tails) / len(tails) if tails else None
    sessions_with_big_results = sum(1 for s in all_sessions if s['big_results'])
    carried_cost_per_session  = (
        sum(s['carried_read_tokens'] for s in all_sessions) / total
        * CACHE_READ_MULT * AUDIT_BASE_PRICE
    )
    avg_redundant_reads = sum(s['redundant_reads'] for s in all_sessions) / total
    growths = [s['context_growth_factor'] for s in all_sessions
               if s['context_growth_factor'] is not None]
    avg_context_growth  = sum(growths) / len(growths) if growths else None
    avg_first_context   = (sum(s['first_context'] for s in with_reqs) / len(with_reqs)
                           if with_reqs else 0.0)
    avg_output_tokens   = (sum(s['avg_output_tokens'] for s in with_reqs) / len(with_reqs)
                           if with_reqs else 0.0)

    # Bucket 3: retry loops — failed tool calls and same-file edit churn
    avg_error_results   = sum(s['error_results'] for s in all_sessions) / total
    avg_edit_churn      = sum(s['edit_churn'] for s in all_sessions) / total
    sessions_with_errors = sum(1 for s in all_sessions if s['error_results'] > 0)

    # Measured orientation: input-side spend before the first edit, as a share
    # of total input-side spend. Edit sessions only (no-edit sessions are
    # excluded — reading may have been the job) and only sessions with usage.
    # Effective tokens weight cache traffic by the provider multipliers, applied
    # here at query time so CRAM_PROVIDER changes never require a re-parse.
    edit_session_list  = [s for s in all_sessions if s['edits'] > 0]
    read_only_sessions = total - len(edit_session_list)
    measured           = [s for s in edit_session_list if s['requests'] > 0]

    def _eff(inp: float, cw: float, cr: float) -> float:
        return inp + cw * CACHE_WRITE_MULT + cr * CACHE_READ_MULT

    eff_pre = sum(_eff(s['pre_edit_input_tokens'], s['pre_edit_cache_writes'],
                       s['pre_edit_cache_reads']) for s in measured)
    eff_tot = sum(_eff(s['input_tokens'], s['cache_writes'], s['cache_reads'])
                  for s in measured)
    pre_edit_spend_share      = eff_pre / eff_tot if measured and eff_tot else None
    pre_edit_spend_eff_tokens = eff_pre / len(measured) if measured else None
    pre_edit_spend_cost       = (pre_edit_spend_eff_tokens * AUDIT_BASE_PRICE
                                 if pre_edit_spend_eff_tokens is not None else None)
    # Spine tree for the waterfall: effective input by composition × pre/post-edit,
    # over the same measured pool as the pre-edit share. Both axes are measured and
    # exhaustive, so children reconcile to their parent (and to eff_tot).
    spine_tree = None
    if measured and eff_tot:
        def _csum(field: str) -> float:
            return float(sum(s[field] for s in measured))
        comps = [
            ('cache read',  _csum('cache_reads')  * CACHE_READ_MULT,
                            _csum('pre_edit_cache_reads')  * CACHE_READ_MULT),
            ('fresh input', _csum('input_tokens'), _csum('pre_edit_input_tokens')),
            ('cache write', _csum('cache_writes') * CACHE_WRITE_MULT,
                            _csum('pre_edit_cache_writes') * CACHE_WRITE_MULT),
        ]
        spine_tree = {
            'pool_sessions': len(measured),
            'total':         eff_tot,
            'components':    [{'label': l, 'eff': e, 'pre': p} for l, e, p in comps],
        }

    # Below this many measured sessions the share is reported as preliminary.
    PRELIMINARY_MIN_MEASURED = 5

    # Per-file evidence: total reads per file across sessions, and in how many
    # sessions each file was read. Cross-session repetition is the orientation
    # signal ("every session re-reads audit.py" → belongs in a repo briefing).
    file_reads: dict[str, list[int]] = {}
    for s in all_sessions:
        for fp, c in s['read_file_counts'].items():
            agg = file_reads.setdefault(os.path.normpath(fp), [0, 0])
            agg[0] += c
            agg[1] += 1
    top_read_files = sorted(
        ((fp, r, n) for fp, (r, n) in file_reads.items()),
        key=lambda t: (-t[1], -t[2], t[0]))[:10]

    # Orientation cost estimate: reads_before_edit × avg file size × Sonnet price
    # Assumptions: AUDIT_TOK_PER_FILE tokens per file read, AUDIT_BASE_PRICE per token.
    orient_tok_per_session  = avg_rbe * AUDIT_TOK_PER_FILE
    orient_cost_per_session = orient_tok_per_session * AUDIT_BASE_PRICE
    sessions_per_month      = total / (days / 30)
    monthly_orient_cost     = orient_cost_per_session * sessions_per_month

    # Weekly trend of the primary metric, oldest → newest, last 8 ISO weeks
    weekly_map: dict[str, list[float]] = {}
    for s in all_sessions:
        wk = datetime.datetime.fromtimestamp(s['mtime']).strftime('%G-W%V')
        weekly_map.setdefault(wk, []).append(s['reads_before_edit'])
    weekly = [(wk, sum(v) / len(v), len(v)) for wk, v in sorted(weekly_map.items())][-8:]

    recent = sorted(all_sessions, key=lambda s: s['mtime'], reverse=True)[:20]

    # Waste leaderboard: the heaviest *measured* sessions (those with token
    # usage — Claude/Codex), ranked by fresh input-side spend. All columns the
    # report shows from these are measured; reader drills in with --session.
    measured_pool = [s for s in all_sessions if s.get('input_tokens', 0) > 0]
    leaderboard = sorted(measured_pool, key=lambda s: s['input_tokens'], reverse=True)[:10]

    # Retry-loop evidence: failed commands grouped across sessions, worst-first.
    # The signal is the SAME command failing repeatedly (the "ran the wrong test
    # command four times" case), so rank by total failures then session spread.
    from collections import Counter as _Counter
    _fc_fail: _Counter = _Counter()
    _fc_sess: _Counter = _Counter()
    for s in all_sessions:
        for fc in (s.get('failed_commands') or []):
            _fc_fail[fc['cmd']] += fc['failures']
            _fc_sess[fc['cmd']] += 1
    top_failed_commands = sorted(
        ({'cmd': c, 'failures': _fc_fail[c], 'sessions': _fc_sess[c]} for c in _fc_fail),
        key=lambda r: (-r['failures'], -r['sessions'], r['cmd']))[:10]

    # Cost by waste layer — the overlapping diagnostics (NOT the spine partition).
    # These do not sum to effective input: orientation pre-edit cache reads are
    # already inside the spine's "cache read", etc. Each row carries its own
    # basis so the report never implies fake precision:
    #   measured   — derived from measured tokens × price
    #   estimated  — modelled with the assumed tokens/file read
    #   count      — a frequency we trust but do not dollar-cost
    carried_eff_per_session = (
        sum(s['carried_read_tokens'] for s in all_sessions) / total * CACHE_READ_MULT)
    redundant_eff_per_session = avg_redundant_reads * AUDIT_TOK_PER_FILE
    layer_costs = [
        {'layer': 'orientation', 'basis': 'measured',
         'eff_tokens_per_session': pre_edit_spend_eff_tokens,
         'cost_per_session': pre_edit_spend_cost,
         'note': 'pre-edit input-side spend (measured edit sessions)'},
        {'layer': 'carried', 'basis': 'measured',
         'eff_tokens_per_session': carried_eff_per_session,
         'cost_per_session': carried_cost_per_session,
         'note': 'oversized results re-read every later turn'},
        {'layer': 'redundant', 'basis': 'estimated',
         'eff_tokens_per_session': redundant_eff_per_session,
         'cost_per_session': redundant_eff_per_session * AUDIT_BASE_PRICE,
         'note': 'same-file re-reads × assumed tokens/file'},
        {'layer': 'retries', 'basis': 'count',
         'count_per_session': avg_error_results,
         'note': 'failed tool calls — not dollar-costed'},
        {'layer': 'churn', 'basis': 'count',
         'count_per_session': avg_edit_churn,
         'note': 'same-file re-edits — not dollar-costed'},
    ]
    # Rank dollar-costed layers first (by $/session desc), count-only last.
    layer_costs.sort(key=lambda r: (r['basis'] == 'count',
                                    -(r.get('cost_per_session') or 0)))

    # Neutral-auditor view: split on whether a context tool was active. Only
    # meaningful when the pool actually contains both — otherwise it's noise.
    cm_on  = [s for s in all_sessions if s.get('context_mode')]
    cm_off = [s for s in all_sessions if not s.get('context_mode')]
    context_mode_segment = (
        {'on': _segment_metrics(cm_on), 'off': _segment_metrics(cm_off)}
        if cm_on and cm_off else None
    )

    data = {
        'days':                      days,
        'sessions':                  total,
        'avg_reads':                 avg_reads,
        'avg_reads_before_edit':     avg_rbe,
        'avg_edits':                 avg_edits,
        'avg_ratio':                 avg_ratio,
        'ratio_band':                ratio_band(avg_ratio),
        'avg_cache_writes':          avg_cw,
        'avg_cache_reads':           avg_cr,
        'cache_engaged_sessions':    cache_engaged,
        'cache_blind_sessions':      cache_blind,
        'avg_requests':              avg_requests,
        'avg_context_per_request':   avg_ctx_per_req,
        'avg_first_context':         avg_first_context,
        'peak_context':              peak_context,
        'avg_context_growth':        avg_context_growth,
        'context_growth_measured':   len(growths),
        'avg_output_tokens':         avg_output_tokens,
        'bloat_tail_share':          bloat_tail_share,
        'bloat_sessions_measured':   len(tails),
        'sessions_with_big_results': sessions_with_big_results,
        'carried_cost_per_session':  carried_cost_per_session,
        'avg_redundant_reads':       avg_redundant_reads,
        'avg_error_results':         avg_error_results,
        'avg_edit_churn':            avg_edit_churn,
        'sessions_with_errors':      sessions_with_errors,
        'big_result_bytes':          BIG_RESULT_BYTES,
        # Measured orientation (new, additive; None when unmeasurable)
        'edit_sessions':                   len(edit_session_list),
        'read_only_sessions':              read_only_sessions,
        'pre_edit_measured_sessions':        len(measured),
        'pre_edit_unmeasured_sessions': len(edit_session_list) - len(measured),
        'pre_edit_spend_share':            pre_edit_spend_share,
        'pre_edit_spend_eff_tokens':       pre_edit_spend_eff_tokens,
        'pre_edit_spend_cost':             pre_edit_spend_cost,
        'pre_edit_eff_total_tokens':       eff_tot if measured else None,
        'pre_edit_preliminary':            bool(measured) and len(measured) < PRELIMINARY_MIN_MEASURED,
        'top_read_files':                  top_read_files,
        # Estimated orientation (legacy model: assumed tokens/file)
        'orient_tokens_per_session': orient_tok_per_session,
        'orient_cost_per_session':   orient_cost_per_session,
        'sessions_per_month':        sessions_per_month,
        'monthly_orient_cost':       monthly_orient_cost,
        'provider':                  AUDIT_PROVIDER,
        'projects':                  project_summaries,
        'weekly':                    weekly,
        'recent':                    recent,
        'leaderboard':               leaderboard,
        'top_failed_commands':       top_failed_commands,
        'measured_pool_sessions':    len(measured_pool),
        'layer_costs':               layer_costs,
        'token_spine':               token_spine,
        'spine_tree':                spine_tree,
        'base_price':                AUDIT_BASE_PRICE,
        # Live transcripts whose parse failed this run; their sessions are
        # missing from every number above.
        'parse_failures':            len(store.run_failures),
        # None unless the window contains both context-tool-on and -off sessions.
        'context_mode_segment':      context_mode_segment,
    }
    data['findings'] = audit_findings.derive_findings(data)
    return data


def run_audit(repo_root: str, days: int = 30, all_projects: bool = False,
              as_json: bool = False, reingest: bool = False) -> None:
    """Print an orientation-tax audit for the repo (or all projects)."""

    failures: list[str] = []
    data = collect_audit(repo_root, days=days, all_projects=all_projects,
                         reingest=reingest, failures_out=failures)

    if failures:
        n = len(failures)
        print(f"⚠ {n} transcript{'s' if n != 1 else ''} failed to parse — "
              f"numbers may be incomplete (CRAM_DEBUG=1 for paths)",
              file=sys.stderr)
        if os.environ.get('CRAM_DEBUG'):
            for p in sorted(failures):
                print(f"  {p}", file=sys.stderr)

    if data is None:
        if not all_projects and _project_transcript_dir(repo_root) is None:
            print("No Claude Code transcripts found for this repo.")
            print("  (Expected: ~/.claude/projects/" +
                  repo_root.replace(os.sep, '-').lstrip('-') + "/)")
        else:
            print(f"No sessions found in the last {days} days.")
        return

    if as_json:
        print(json.dumps(data, indent=2))
        return

    band_label = {
        'good':   '✓ good',
        'normal': '~ normal',
        'high':   '⚠ high — context may not be landing',
    }[data['ratio_band']]

    total = data['sessions']

    print(f"\nAgent session audit — last {days} days\n")
    print(f"  Sessions analysed:              {total}")
    print(f"  Avg reads/session:              {data['avg_reads']:.1f}")
    print(f"  Avg reads before first edit:    {data['avg_reads_before_edit']:.1f}  ← primary metric")
    print(f"  Avg edits/session:              {data['avg_edits']:.1f}")
    print(f"  Avg read-to-edit ratio:         {data['avg_ratio']:.1f}×  {band_label}")
    print(f"  Avg cache writes/session:       {data['avg_cache_writes']:,.0f} tokens")
    print(f"  Cache engagement:               {data['cache_engaged_sessions']}/{total} sessions read from cache")
    if data['cache_blind_sessions']:
        print(f"    ⚠ {data['cache_blind_sessions']} session(s) wrote cache but never read it — "
              f"check that prompt caching is engaging")

    print()
    print(f"  Pre-edit context share (measured):")
    excl = (f"  ({data['read_only_sessions']} no-edit sessions excluded)"
            if data['read_only_sessions'] else '')
    print(f"    Edit sessions:                {data['edit_sessions']}/{total}{excl}")
    if data['pre_edit_measured_sessions']:
        if data['pre_edit_unmeasured_sessions']:
            print(f"    With token usage:             {data['pre_edit_measured_sessions']}"
                  f"/{data['edit_sessions']} measured"
                  f"  ({data['pre_edit_unmeasured_sessions']} lack usage data)")
        prelim = (f"  ⚠ preliminary — only {data['pre_edit_measured_sessions']} "
                  f"measured session{'s' if data['pre_edit_measured_sessions'] != 1 else ''}"
                  if data['pre_edit_preliminary'] else '')
        if data['pre_edit_spend_share'] is not None:
            print(f"    Pre-edit context share:       {data['pre_edit_spend_share']:.0%}"
                  f"  of {data['pre_edit_eff_total_tokens']:,.0f} eff. input tokens{prelim}")
        if data['pre_edit_spend_eff_tokens'] is not None:
            print(f"    Pre-edit spend/session:       ~{data['pre_edit_spend_eff_tokens']:,.0f} eff. tokens"
                  f"  (~${data['pre_edit_spend_cost']:.4f}, {data['provider']} pricing)")
    elif data['edit_sessions']:
        print(f"    No token usage in these sessions — measured share "
              f"unavailable (estimates below)")

    if data['avg_requests']:
        print()
        print(f"  Context bloat:")
        print(f"    Avg requests/session:         {data['avg_requests']:.0f}")
        print(f"    Avg context per request:      {data['avg_context_per_request']:,.0f} tokens"
              f"  (peak {data['peak_context']:,})")
        if data['avg_first_context']:
            print(f"    Avg context at session start: {data['avg_first_context']:,.0f} tokens"
                  f"  (system prompt + initial message)")
        if data['avg_context_growth'] is not None:
            n_g = data['context_growth_measured']
            growth = data['avg_context_growth']
            flag = '  ⚠ heavy bloat' if growth > 5 else ('  ↑ growing' if growth > 2 else '')
            print(f"    Avg context growth/session:   {growth:.1f}×"
                  f"  ({n_g} session{'s' if n_g != 1 else ''} measured){flag}")
        if data['avg_output_tokens']:
            print(f"    Avg output tokens/request:    {data['avg_output_tokens']:,.0f}"
                  f"  (large outputs expand next-turn context)")
        if data['bloat_tail_share'] is not None:
            n_measured = data['bloat_sessions_measured']
            print(f"    Read-cost in last 1/3 turns:  {data['bloat_tail_share'] * 100:.0f}%"
                  f"  ({n_measured} session{'s' if n_measured != 1 else ''} measured; 33% = flat)")
        if data['sessions_with_big_results']:
            kb = data['big_result_bytes'] // 1000
            print(f"    Oversized tool results:       {data['sessions_with_big_results']}/{total} "
                  f"sessions carried a result > {kb} KB")
            print(f"    Est. carried read cost:       ~${data['carried_cost_per_session']:.4f}/session"
                  f"  (oversized results re-read every turn)")
        if data['avg_redundant_reads'] >= 0.5:
            print(f"    Redundant same-file reads:    {data['avg_redundant_reads']:.1f}/session")

    if data['avg_error_results'] > 0 or data['avg_edit_churn'] > 0:
        print()
        print(f"  Retry loops:")
        print(f"    Failed tool calls/session:    {data['avg_error_results']:.1f}"
              f"  ({data['sessions_with_errors']}/{total} sessions had failures)")
        print(f"    Same-file re-edits/session:   {data['avg_edit_churn']:.1f}")
        top_fc = [c for c in data.get('top_failed_commands', []) if c['failures'] > 1]
        if top_fc:
            print(f"    Most-retried failed commands (same command failing repeatedly):")
            for c in top_fc[:5]:
                scope = f"{c['sessions']} session{'s' if c['sessions'] != 1 else ''}"
                print(f"      {c['failures']:>3}× in {scope}   {c['cmd'][:70]}")

    repeated_files = [t for t in data['top_read_files'] if t[1] > 1]
    if repeated_files:
        print()
        print(f"  Top repeated files (most-read; candidates for a repo briefing):")
        for fp, r, n in repeated_files[:5]:
            disp = audit_events.repo_rel(fp, repo_root)
            print(f"    {r:>3}× in {n} session{'s' if n != 1 else ''}   {disp}")

    seg = data.get('context_mode_segment')
    if seg:
        on, off = seg['on'], seg['off']
        print()
        print(f"  Context tool segment (ctx_* active vs not — A/B without a second checkout):")
        print(f"    {'Metric':<30} {'ctx on':>12} {'ctx off':>12}")
        print(f"    {'Sessions':<30} {on['sessions']:>12} {off['sessions']:>12}")
        print(f"    {'Reads before first edit':<30} {on['avg_reads_before_edit']:>12.1f} "
              f"{off['avg_reads_before_edit']:>12.1f}")
        print(f"    {'Carried result cost/session':<30} {'$' + format(on['carried_cost_per_session'], '.4f'):>12} "
              f"{'$' + format(off['carried_cost_per_session'], '.4f'):>12}")
        print(f"    {'Sessions w/ oversized result':<30} {on['sessions_with_big_results']:>12} "
              f"{off['sessions_with_big_results']:>12}")
        print(f"    {'Avg peak context (tokens)':<30} {on['avg_peak_context']:>12,.0f} "
              f"{off['avg_peak_context']:>12,.0f}")
        print(f"    → context-mode targets carried-result cost + peak context; "
              f"this is whether it landed.")
        print(f"      (ctx_* reads run in the sandbox, off-transcript — read the "
              f"carried-cost/peak columns, not reads-before-edit.)")

    if data['findings']:
        print()
        print(f"  Findings ({len(data['findings'])}):")
        for fd in data['findings']:
            print(f"    ⚠ {fd['id']:<18} {fd['evidence']}")
            print(f"      → {fd['fix']}")
            rec = fd.get('recommended')
            if rec:
                alt = (f"  (alt: {', '.join(rec['alternatives'])})"
                       if rec.get('alternatives') else '')
                print(f"      ⌁ recommend [{rec['kind']}] {rec['title']}{alt}")
    print()
    print(f"  Est. orientation tokens/session: ~{data['orient_tokens_per_session']:,.0f}")
    print(f"  Est. orientation cost/session:   ~${data['orient_cost_per_session']:.4f}  "
          f"({data['provider']} pricing, base input)")
    print(f"  Est. monthly orientation tax:    ~${data['monthly_orient_cost']:.2f}  "
          f"({data['sessions_per_month']:.0f} sessions/month)")
    print(f"  Note: cost is modelled from reads_before_edit ({AUDIT_TOK_PER_FILE:,} tok/file assumed); "
          f"the ratio is the measured signal.")
    print()
    print(f"  Ratio guide: < 2× good · 2–5× normal · > 5× context isn't landing")

    if all_projects and len(data['projects']) > 1:
        print(f"\n  Per-project breakdown:")
        print(f"  {'Project':<45} {'Sessions':>8} {'Reads/s':>8} {'RBE':>6} {'CW/s':>12}")
        print(f"  {'-'*45} {'-'*8} {'-'*8} {'-'*6} {'-'*12}")
        for name, n, reads, rbe, cw in sorted(data['projects'], key=lambda x: -x[3]):
            short = name[-43:] if len(name) > 43 else name
            print(f"  {short:<45} {n:>8} {reads:>8.1f} {rbe:>6.1f} {cw:>12,.0f}")

    print()
    print("  High orientation share? Give the agent a repo briefing (e.g. cram task)")
    print("  and re-audit to verify the share actually drops.")


def run_report(repo_root: str, days: int = 30, all_projects: bool = False,
               out_path: str = '-', reingest: bool = False) -> None:
    """Render the markdown report to stdout (out_path '-') or a file."""
    from cram.audit_report import render_report
    data = collect_audit(repo_root, days=days, all_projects=all_projects,
                         reingest=reingest)
    if data is None:
        print(f"No sessions found in the last {days} days.")
        return
    md = render_report(data, repo_root)
    if out_path == '-':
        print(md)
    else:
        with open(out_path, 'w') as f:
            f.write(md)
        print(f"Wrote {out_path}")


def _build_drilldowns(leaderboard: list[dict], repo_root: str, top: int = 3) -> dict:
    """Per-turn timelines for the heaviest sessions, keyed by session_id.

    Best-effort: resolves each session's transcript and builds its timeline;
    silently skips any that can't be resolved or parsed (e.g. Cursor sessions,
    or Codex sessions outside the date-tree). Used to embed expandable
    drilldowns under the HTML leaderboard.
    """
    out: dict = {}
    for s in leaderboard[:top]:
        sid = s.get('session_id')
        if not sid:
            continue
        try:
            path = _resolve_session_path(sid, repo_root)
            if not path:
                continue
            sd = _codex_sessions_dir()
            is_codex = sd and os.path.abspath(path).startswith(os.path.abspath(sd))
            parsed = (audit_events.parse_codex if is_codex
                      else audit_events.parse_claude)(path)
            if not parsed:
                continue
            tl = audit_events.derive_session_timeline(
                *parsed, big_result_bytes=BIG_RESULT_BYTES)
            if tl:
                out[sid] = tl
        except Exception:
            continue
    return out


def run_report_html(repo_root: str, days: int = 30, all_projects: bool = False,
                    out_path: str | None = None, reingest: bool = False,
                    open_browser: bool | None = None) -> None:
    """Render the standalone HTML report to a file and (optionally) open it.

    Builds the same collect_audit() data as the markdown report, plus the
    per-layer drilldown rows, and writes one self-contained HTML file.
    out_path defaults to 'cram-audit-report.html' in the cwd. open_browser
    defaults to True when stdout is a TTY (so it pops open interactively but
    stays quiet in CI / pipes).
    """
    from cram.audit_report_html import render_report_html

    store = audit_store.AuditStore.open()
    try:
        data = _collect_audit_inner(store, repo_root, days, all_projects, reingest)
        if data is None:
            print(f"No sessions found in the last {days} days.")
            return
        all_sessions, _ = _gather_sessions(store, repo_root, days, all_projects, False)
        layers = {name: _layer_rows(name, all_sessions or [], repo_root)
                  for name in LAYERS}
    finally:
        store.close()

    # Embedded drilldowns: per-turn timelines for the top leaderboard sessions
    # (best-effort — skip any that can't be resolved or parsed).
    drilldowns = _build_drilldowns(data.get('leaderboard') or [], repo_root, top=3)

    out_path = out_path or 'cram-audit-report.html'
    html = render_report_html(data, layers, repo_root, drilldowns=drilldowns)
    with open(out_path, 'w') as f:
        f.write(html)
    abs_path = os.path.abspath(out_path)
    print(f"Wrote {abs_path}")

    if open_browser is None:
        open_browser = sys.stdout.isatty()
    if open_browser:
        import webbrowser
        try:
            webbrowser.open(f'file://{abs_path}')
        except Exception:
            pass


def _resolve_root(path: str) -> str:
    from cram.utils import find_git_root
    start = os.path.abspath(path)
    try:
        return find_git_root(start)
    except Exception:
        return start


# Rows for the side-by-side comparison: (label, summary key, format).
_COMPARE_ROWS = [
    ('Sessions analysed',          'sessions',                '{:.0f}'),
    ('Pre-edit spend share (meas.)', 'pre_edit_spend_share',        '{:.1%}'),
    ('Reads before first edit ←',  'avg_reads_before_edit',   '{:.1f}'),
    ('Read-to-edit ratio',         'avg_ratio',               '{:.1f}'),
    ('Edits/session',              'avg_edits',               '{:.1f}'),
    ('Cache writes/session',       'avg_cache_writes',        '{:,.0f}'),
    ('Cache reads/session',        'avg_cache_reads',         '{:,.0f}'),
    ('Requests/session',           'avg_requests',            '{:.0f}'),
    ('Context/request',            'avg_context_per_request', '{:,.0f}'),
    ('Context at session start',   'avg_first_context',       '{:,.0f}'),
    ('Context growth (peak/start)','avg_context_growth',      '{:.1f}'),
    ('Output tokens/request',      'avg_output_tokens',       '{:,.0f}'),
    ('Redundant re-reads',         'avg_redundant_reads',     '{:.1f}'),
    ('Failed tool calls/session',  'avg_error_results',       '{:.1f}'),
    ('Same-file re-edits/session', 'avg_edit_churn',          '{:.1f}'),
]


def compare_rows(data_a: dict, data_b: dict) -> list[dict]:
    """Per-metric A/B comparison rows, shared by the stdout table and the CI
    PR-comment renderer (cram.ci). Each row carries raw values plus
    preformatted strings so both renderers agree on formatting and the Δ sign.
    """
    rows: list[dict] = []
    for label, key, fmt in _COMPARE_ROWS:
        va, vb = data_a.get(key), data_b.get(key)
        if va is None or vb is None:
            rows.append({'label': label, 'key': key, 'a': va, 'b': vb,
                         'a_str': '—', 'b_str': '—', 'delta_str': '—', 'pct': '—'})
            continue
        delta = vb - va
        pct = f'{delta / va * 100:+.0f}%' if va else '—'
        delta_str = fmt.format(delta) if delta >= 0 else '-' + fmt.format(-delta)
        rows.append({'label': label, 'key': key, 'a': va, 'b': vb,
                     'a_str': fmt.format(va), 'b_str': fmt.format(vb),
                     'delta_str': delta_str, 'pct': pct})
    return rows


def run_compare(path_a: str, path_b: str, days: int = 30,
                as_json: bool = False, reingest: bool = False) -> None:
    """Side-by-side audit of two checkouts — the P0 attribution experiment view.

    A is the treatment arm (context wiring on), B the control, by convention;
    the output is symmetric so the order only affects the delta sign.
    """
    root_a, root_b = _resolve_root(path_a), _resolve_root(path_b)
    data_a = collect_audit(root_a, days=days, reingest=reingest)
    data_b = collect_audit(root_b, days=days, reingest=reingest)

    if as_json:
        print(json.dumps({
            'days': days,
            'a': {'path': root_a, 'data': data_a},
            'b': {'path': root_b, 'data': data_b},
        }, indent=2))
        return

    for root, data in ((root_a, data_a), (root_b, data_b)):
        if data is None:
            print(f"No sessions found for {root} in the last {days} days.")
            return

    name_a = os.path.basename(root_a.rstrip(os.sep))[:18] or root_a
    name_b = os.path.basename(root_b.rstrip(os.sep))[:18] or root_b

    print(f"\nAudit comparison — last {days} days  (Δ = B − A)\n")
    print(f"  {'Metric':<28} {name_a:>18} {name_b:>18} {'Δ':>12} {'Δ%':>8}")
    print(f"  {'-' * 28} {'-' * 18} {'-' * 18} {'-' * 12} {'-' * 8}")
    for row in compare_rows(data_a, data_b):
        print(f"  {row['label']:<28} {row['a_str']:>18} {row['b_str']:>18} "
              f"{row['delta_str']:>12} {row['pct']:>8}")
    print()
    print("  ← primary metric. Negative Δ on reads-before-first-edit means B")
    print("    (second path) oriented faster. Compare distributions, not just")
    print("    means, before drawing conclusions — a few long sessions dominate.")


def _resolve_session_path(ident: str, repo_root: str) -> str | None:
    """Resolve a --session identifier to a transcript path.

    Accepts a full path, a bare UUID / UUID-prefix, or a partial session id.
    Searches Claude's per-repo project dir first, then Codex's global date-
    tree (~/.codex/sessions/YYYY/MM/DD/*.jsonl), matching by UUID embedded in
    the filename. Newest match wins on a prefix tie.
    """
    if os.path.isfile(ident):
        return ident

    # ── Claude: per-repo project dir ─────────────────────────────────────────
    td = _project_transcript_dir(repo_root)
    if td:
        exact = os.path.join(td, ident if ident.endswith('.jsonl') else ident + '.jsonl')
        if os.path.isfile(exact):
            return exact
        matches = [f for f in glob.glob(os.path.join(td, '*.jsonl'))
                   if os.path.basename(f).startswith(ident)]
        if matches:
            return max(matches, key=os.path.getmtime)

    # ── Codex: global date-tree, match by embedded UUID ──────────────────────
    sd = _codex_sessions_dir()
    if sd:
        codex_matches = [
            f for f in glob.glob(os.path.join(sd, '**', '*.jsonl'), recursive=True)
            if audit_events._session_ident(f).startswith(ident)
        ]
        if codex_matches:
            return max(codex_matches, key=os.path.getmtime)

    return None


def run_session(ident: str, repo_root: str, as_json: bool = False) -> None:
    """Per-request waterfall for one session — `cram audit --session ID`."""
    path = _resolve_session_path(ident, repo_root)
    if path is None:
        print(f"No transcript found for session {ident!r}.", file=sys.stderr)
        hints = []
        if _project_transcript_dir(repo_root) is None:
            hints.append('no ~/.claude/projects/ directory for this repo')
        if _codex_sessions_dir() is None:
            hints.append('no ~/.codex/sessions/ directory')
        if hints:
            print(f"  ({'; '.join(hints)})", file=sys.stderr)
        sys.exit(1)

    sd = _codex_sessions_dir()
    is_codex = sd and os.path.abspath(path).startswith(os.path.abspath(sd))
    parse_fn = audit_events.parse_codex if is_codex else audit_events.parse_claude
    parsed = parse_fn(path)
    if parsed is None:
        print(f"Could not parse transcript: {path}", file=sys.stderr)
        sys.exit(1)
    meta, events = parsed
    tl = audit_events.derive_session_timeline(meta, events,
                                              big_result_bytes=BIG_RESULT_BYTES)
    if tl is None:
        print("No per-request token usage in this session — nothing to chart.")
        return

    if as_json:
        print(json.dumps(tl, indent=2))
        return

    sid = os.path.splitext(os.path.basename(path))[0]
    when = datetime.datetime.fromtimestamp(tl['mtime']).strftime('%Y-%m-%d %H:%M')
    carried_cost = tl['carried_read_tokens'] * CACHE_READ_MULT * AUDIT_BASE_PRICE

    print(f"\nSession {sid[:8]} · {os.path.basename(repo_root.rstrip(os.sep))} · "
          f"{when} · {tl['requests']} requests\n")
    print(f"  {'Turn':>4} {'Input':>9} {'CacheR':>10} {'CacheW':>10} "
          f"{'Output':>8} {'Context':>10} {'Δ':>9}  Note")
    print(f"  {'-' * 74}")
    for r in tl['rows']:
        d = r['delta']
        dstr = f'+{d:,}' if d > 0 else (f'{d:,}' if d < 0 else '—')
        flag = ' ⚠' if d > 10_000 else ''
        note = '; '.join(r['notes'][:3])
        if len(r['notes']) > 3:
            note += f' (+{len(r["notes"]) - 3})'
        print(f"  {r['turn']:>4} {r['input']:>9,} {r['cache_read']:>10,} "
              f"{r['cache_write']:>10,} {r['output']:>8,} {r['context']:>10,} "
              f"{dstr:>9}{flag}  {note}")

    print()
    print(f"  Peak context: {tl['peak_context']:,} tok  ·  "
          f"first request: {tl['first_context']:,} tok")

    if tl['carried']:
        print(f"\n  Carried waste (oversized results re-read every later turn):")
        for c in tl['carried'][:5]:
            src = audit_events.repo_rel(c['file'], repo_root) if c['file'] else '(result)'
            print(f"    {src}: {c['tokens']:,} tok × {c['carried_turns']} later turns "
                  f"= {c['carried_tokens']:,} carried tok")
        print(f"    → est. carried read cost: ~${carried_cost:.4f} "
              f"({AUDIT_PROVIDER} pricing)")

    if tl['redundant']:
        print(f"\n  Redundant re-reads (same file read >1×):")
        for fp, n in tl['redundant'][:5]:
            print(f"    {n}× {audit_events.repo_rel(fp, repo_root)}")

    if tl['retries']:
        print(f"\n  Failed tool calls (retry loops): {tl['retries']}")
        for fc in tl.get('failed_commands', [])[:5]:
            scope = f"{fc['failures']}× failed" if fc['failures'] > 1 else "failed"
            print(f"    {scope}: {fc['cmd'][:70]}")
    print()


LAYERS = ('orientation', 'repeated', 'redundant', 'carried', 'retries', 'churn')


def _layer_rows(layer: str, sessions: list[dict], repo_root: str) -> list[dict]:
    """Concrete contributors for one waste layer, ranked worst-first."""
    from collections import Counter, defaultdict

    if layer in ('repeated', 'redundant'):
        reads = Counter()
        sess_seen: dict[str, set] = defaultdict(set)
        for i, s in enumerate(sessions):
            for fp, c in (s.get('read_file_counts') or {}).items():
                if layer == 'redundant':
                    if c > 1:
                        reads[fp] += c - 1
                else:
                    reads[fp] += c
                    sess_seen[fp].add(i)
        if layer == 'redundant':
            rows = [{'file': fp, 'extra_reads': n} for fp, n in reads.items() if n > 0]
            rows.sort(key=lambda r: -r['extra_reads'])
        else:
            rows = [{'file': fp, 'reads': reads[fp], 'sessions': len(sess_seen[fp])}
                    for fp in reads if len(sess_seen[fp]) >= 2]
            rows.sort(key=lambda r: (-r['reads'], -r['sessions']))
        return rows

    if layer == 'churn':
        extra = Counter()
        for s in sessions:
            for fp, c in (s.get('edit_file_counts') or {}).items():
                if c > 1:
                    extra[fp] += c - 1
        rows = [{'file': fp, 're_edits': n} for fp, n in extra.items() if n > 0]
        rows.sort(key=lambda r: -r['re_edits'])
        return rows

    if layer == 'carried':
        rows = [{'session_id': s.get('session_id', ''), 'source': s.get('source', 'claude'),
                 'big_results': s['big_results'], 'carried_tokens': s['carried_read_tokens']}
                for s in sessions if s.get('big_results')]
        rows.sort(key=lambda r: -r['carried_tokens'])
        return rows

    if layer == 'retries':
        # Group failed tool calls by command across sessions — the retry-loop
        # signal is the SAME command failing repeatedly (e.g. a wrong test
        # command run four times), not just a per-session error count.
        fails: Counter = Counter()
        sess: Counter = Counter()
        for s in sessions:
            for fc in (s.get('failed_commands') or []):
                fails[fc['cmd']] += fc['failures']
                sess[fc['cmd']] += 1
        rows = [{'cmd': c, 'failures': fails[c], 'sessions': sess[c]} for c in fails]
        rows.sort(key=lambda r: (-r['failures'], -r['sessions'], r['cmd']))
        return rows

    if layer == 'orientation':
        rows = [{'session_id': s.get('session_id', ''), 'source': s.get('source', 'claude'),
                 'reads_before_edit': s['reads_before_edit']}
                for s in sessions if s['edits'] > 0 and s['reads_before_edit'] > 0]
        rows.sort(key=lambda r: -r['reads_before_edit'])
        return rows

    raise ValueError(f'unknown layer {layer!r}; choose from {", ".join(LAYERS)}')


def format_layer_row(layer: str, r: dict, repo_root: str) -> str:
    """One-line rendering of a drilldown row, shared by the CLI and the HTML report."""
    if layer == 'repeated':
        return f"{r['reads']}× in {r['sessions']} sessions  {audit_events.repo_rel(r['file'], repo_root)}"
    if layer == 'redundant':
        return f"+{r['extra_reads']} re-reads  {audit_events.repo_rel(r['file'], repo_root)}"
    if layer == 'churn':
        return f"+{r['re_edits']} re-edits  {audit_events.repo_rel(r['file'], repo_root)}"
    if layer == 'carried':
        return (f"{r['carried_tokens']:,} carried tok ({r['big_results']} big result"
                f"{'s' if r['big_results'] != 1 else ''})  {r['session_id'][:8]} · {r['source']}")
    if layer == 'retries':
        scope = f"{r['sessions']} session{'s' if r['sessions'] != 1 else ''}"
        return f"{r['failures']}× failed in {scope}  {r['cmd']}"
    if layer == 'orientation':
        return f"{r['reads_before_edit']} reads before 1st edit  {r['session_id'][:8]} · {r['source']}"
    return str(r)


def collect_layer(repo_root: str, layer: str, days: int = 30,
                  all_projects: bool = False, *, reingest: bool = False) -> list[dict]:
    """Drill into one waste layer; return its ranked contributor rows."""
    store = audit_store.AuditStore.open()
    try:
        all_sessions, _ = _gather_sessions(store, repo_root, days, all_projects, reingest)
    finally:
        store.close()
    return _layer_rows(layer, all_sessions or [], repo_root)


def run_layer(layer: str, repo_root: str, days: int = 30, all_projects: bool = False,
              *, as_json: bool = False, reingest: bool = False) -> None:
    """`cram audit --layer <name>` — print the evidence under one waste class."""
    rows = collect_layer(repo_root, layer, days, all_projects, reingest=reingest)
    if as_json:
        print(json.dumps({'layer': layer, 'rows': rows}, indent=2))
        return
    if not rows:
        print(f"No {layer} evidence in the last {days} days.")
        return

    print(f"\nLayer drilldown — {layer}  (last {days} days, top {min(len(rows), 15)})\n")
    file_layers = {'repeated', 'redundant', 'churn'}
    for r in rows[:15]:
        print('  ' + format_layer_row(layer, r, repo_root))
    if layer not in file_layers:
        print("\n  Drill into a Claude session with `cram audit --session <id>`.")
    print()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog='cram audit',
        description='Audit AI coding-agent sessions: where tokens and context '
                    'go, with evidence-backed findings',
    )
    parser.add_argument('--days', type=int, default=30,
                        help='Look back N days (default: 30)')
    parser.add_argument('--all', action='store_true', dest='all_projects',
                        help='Show all projects, not just this repo')
    parser.add_argument('--json', action='store_true', dest='as_json',
                        help='Emit structured JSON instead of the text report')
    parser.add_argument('--compare', nargs=2, metavar=('PATH_A', 'PATH_B'),
                        default=None,
                        help='Compare two checkouts side by side '
                             '(P0 attribution experiment)')
    parser.add_argument('--reingest', '--no-cache', action='store_true',
                        dest='reingest',
                        help='Ignore the audit cache and re-parse all transcripts')
    parser.add_argument('--report', nargs='?', const='-', default=None,
                        metavar='FILE',
                        help='Emit a shareable markdown report '
                             '(to FILE, or stdout if omitted)')
    parser.add_argument('--report-html', nargs='?', const='', default=None,
                        dest='report_html', metavar='FILE',
                        help='Emit a standalone HTML report '
                             '(to FILE, or cram-audit-report.html if omitted)')
    parser.add_argument('--no-open', action='store_true', dest='no_open',
                        help='With --report-html, do not open the file in a browser')
    parser.add_argument('--session', default=None, metavar='ID',
                        help='Per-request waterfall for one session '
                             '(transcript path or session-id prefix)')
    parser.add_argument('--layer', default=None, choices=LAYERS,
                        help='Drill into one waste class and list its contributors')
    parser.add_argument('--path', default=None, metavar='REPO_PATH')
    args = parser.parse_args()

    if args.session:
        root = _resolve_root(args.path) if args.path else _resolve_root(os.getcwd())
        run_session(args.session, root, as_json=args.as_json)
        return

    if args.layer:
        root = _resolve_root(args.path) if args.path else _resolve_root(os.getcwd())
        run_layer(args.layer, root, days=args.days, all_projects=args.all_projects,
                  as_json=args.as_json, reingest=args.reingest)
        return

    if args.compare:
        run_compare(args.compare[0], args.compare[1],
                    days=args.days, as_json=args.as_json, reingest=args.reingest)
        return

    root = _resolve_root(args.path) if args.path else _resolve_root(os.getcwd())

    if args.report_html is not None:
        run_report_html(root, days=args.days, all_projects=args.all_projects,
                        out_path=args.report_html or None, reingest=args.reingest,
                        open_browser=False if args.no_open else None)
        return

    if args.report is not None:
        run_report(root, days=args.days, all_projects=args.all_projects,
                   out_path=args.report, reingest=args.reingest)
        return

    run_audit(root, days=args.days, all_projects=args.all_projects,
              as_json=args.as_json, reingest=args.reingest)


if __name__ == '__main__':
    main()
