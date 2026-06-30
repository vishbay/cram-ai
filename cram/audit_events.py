"""Normalized transcript events — adapters and metric derivation for cram audit.

Each source adapter (Claude Code, Cursor JSONL, Cursor workspace SQLite, Codex)
translates a raw transcript into a flat list of :class:`Event` records plus a
:class:`SessionMeta`. All metric math lives in :func:`derive_session`, which
replays the events and reproduces the exact per-session dict the legacy inline
analyzers returned (the pre-event-store implementation was pinned by a frozen
parity oracle through v0.4.x; removed in v0.5.0 once the numbers were stable).

Adapters never filter by repo: relevance is decided at derivation time, so an
ingested session can be evaluated against any repo root.
"""

from __future__ import annotations
import dataclasses
import json
import os
import re


READ_TOOLS  = frozenset({'Read', 'read_file'})
WRITE_TOOLS = frozenset({'Write', 'Edit', 'edit_file', 'write_file', 'NotebookEdit'})


def _is_context_tool(name: str | None) -> bool:
    """True for context-mode's ctx_* tools, bare or MCP-namespaced.

    context-mode (github.com/mksglu/context-mode) ships an MCP server whose
    tools are all ctx_*-prefixed. Detecting them lets the audit segment
    "context tool active" vs not — cram as neutral auditor, not a competing
    context layer.

    Claude Code surfaces MCP tools as 'mcp__<server>__<tool>', so we match the
    leaf after the last '__' as well as the bare name. NOTE: the exact surfaced
    name should be validated against a real context-mode transcript before this
    is relied on — it's the one assumption not verifiable from the source alone.
    """
    if not name:
        return False
    leaf = name.rsplit('__', 1)[-1]
    return leaf.startswith('ctx_') or 'context-mode' in name
BASH_READ_CMDS = ('cat ', 'head ', 'grep ', 'find ', 'ls ', 'tail ',
                  'nl ',   # number lines — Codex's preferred file viewer
                  'sed ',  # sed -n '..p' pattern used by Codex to read ranges
                  'rg ',   # ripgrep
                  'ag ',   # the_silver_searcher
                  'awk ',  # read/extract
                  )

# ── Cursor tool names ─────────────────────────────────────────────────────────
CURSOR_READ_TOOLS = frozenset({
    'read_file', 'view_file', 'view_code_item',
    'grep_search', 'file_search', 'codebase_search', 'list_directory',
})
CURSOR_WRITE_TOOLS = frozenset({
    'edit_file', 'write_file', 'create_file', 'delete_file', 'apply_changes',
})
CURSOR_BASH_TOOL = 'run_terminal_command'

# Patterns in exec_command.cmd that indicate a read operation.
# Codex routes all shell activity through exec_command, so we match on cmd text.
_CODEX_WRITE_PATCH_RE = re.compile(
    r'^\*{3}\s+(Add File|Update File|Delete File):\s*(.+)$',
    re.MULTILINE,
)


@dataclasses.dataclass(slots=True)
class Event:
    """One normalized transcript event, ordered by seq within its session."""
    seq: int
    kind: str                        # read | edit | tool_call | request_usage | tool_result
    tool: str | None = None
    file_path: str | None = None     # single-file reads/edits (Claude only)
    bytes: int | None = None         # serialized tool_result size
    is_error: bool = False
    tok_input: int = 0
    tok_output: int = 0
    tok_cache_read: int = 0
    tok_cache_write: int = 0
    extras: dict | None = None       # adapter-specific: files / vcs_root / workdir / cwd / cmd


@dataclasses.dataclass(slots=True)
class SessionMeta:
    adapter: str                     # claude | cursor-jsonl | cursor-db | codex
    source: str                      # display source: claude | cursor | codex
    path: str                        # transcript file or state.vscdb path
    mtime: float
    event_mtime: float | None = None  # cursor-db: max bubble createdAt (None if absent)
    external_id: str | None = None    # cursor-db composerId
    cwd: str | None = None            # codex session_meta.cwd (last seen)
    model: str | None = None          # most-frequent model id (claude/codex; None for cursor)


_UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def _session_ident(path: str) -> str:
    """A stable session id from a transcript path: the embedded UUID if present
    (Claude files are bare UUIDs; Codex files are rollout-<ts>-<uuid>), else the
    filename stem."""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _UUID_RE.search(stem)
    return m.group(0) if m else stem


def _find_all_tool_use(obj: object, depth: int = 0) -> list[dict]:
    if depth > 8:
        return []
    if isinstance(obj, dict):
        if obj.get('type') == 'tool_use':
            return [obj]
        results: list[dict] = []
        for v in obj.values():
            results.extend(_find_all_tool_use(v, depth + 1))
        return results
    if isinstance(obj, list):
        results = []
        for item in obj:
            results.extend(_find_all_tool_use(item, depth + 1))
        return results
    return []


def _find_tool_results(obj: object, depth: int = 0) -> list[dict]:
    if depth > 8:
        return []
    if isinstance(obj, dict):
        if obj.get('type') == 'tool_result':
            return [obj]
        results: list[dict] = []
        for v in obj.values():
            results.extend(_find_tool_results(v, depth + 1))
        return results
    if isinstance(obj, list):
        results = []
        for item in obj:
            results.extend(_find_tool_results(item, depth + 1))
        return results
    return []


def _find_usage(obj: object, depth: int = 0) -> list[dict]:
    if depth > 8:
        return []
    if isinstance(obj, dict):
        if 'cache_creation_input_tokens' in obj:
            return [obj]
        results: list[dict] = []
        for v in obj.values():
            results.extend(_find_usage(v, depth + 1))
        return results
    if isinstance(obj, list):
        results: list[dict] = []
        for item in obj:
            results.extend(_find_usage(item, depth + 1))
        return results
    return []


def repo_rel(path: str, repo_root: str) -> str:
    """Display helper: shorten a path under repo_root to repo-relative."""
    repo_sep = repo_root.rstrip(os.sep) + os.sep
    return path[len(repo_sep):] if path.startswith(repo_sep) else path


def _cmd_label(tool: str | None, cmd: str | None, file_path: str | None) -> str:
    """A stable, human label for a (failed) tool call — the retry-loop key.

    Bash/shell commands collapse to their normalized text (so the same command
    run repeatedly groups together); other tools fall back to tool + file.
    """
    if cmd:
        return ' '.join(cmd.split())[:120]
    if file_path:
        return f'{tool or "tool"} {os.path.basename(file_path)}'
    return tool or '(tool)'


def estimate_read_tokens_from_counts(read_file_counts: dict, repo_root: str | None,
                                     *, chars_per_token: int = 4) -> int:
    """Estimate input tokens from the files a session read.

    Cursor transcripts carry no token usage, so for those sessions every token
    metric is blank. This turns the file paths a session read (with their read
    counts) into an *estimate*: each read pulls the file's bytes into context, so
    we sum file size on disk and divide by `chars_per_token`. Strictly an
    estimate (the file on disk may differ from what was read at session time);
    callers must label it `estimated`, never `measured`. Files outside the repo
    or missing on disk are skipped.
    """
    if not read_file_counts:
        return 0
    sep = (repo_root.rstrip(os.sep) + os.sep) if repo_root else None
    total = 0
    for fp, count in read_file_counts.items():
        ap = fp if os.path.isabs(fp) else os.path.join(repo_root or '', fp)
        if sep and not (ap == repo_root or ap.startswith(sep)):
            continue
        try:
            size = os.path.getsize(ap)
        except OSError:
            continue
        total += (size // chars_per_token) * count
    return total


def _cursor_files_from_entry(entry: dict) -> list[str]:
    """Extract referenced file paths from a Cursor agent-transcript entry (deduplicated)."""
    seen: set[str] = set()
    files: list[str] = []
    for f in entry.get('files', []):
        if isinstance(f, str) and f and f not in seen:
            seen.add(f)
            files.append(f)
    inp = entry.get('input') or entry.get('params') or {}
    if isinstance(inp, dict):
        for key in ('target_file', 'file_path', 'path', 'filename'):
            v = inp.get(key)
            if isinstance(v, str) and v and v not in seen:
                seen.add(v)
                files.append(v)
    return files


# ── Adapters ──────────────────────────────────────────────────────────────────

def parse_claude(path: str) -> tuple[SessionMeta, list[Event]] | None:
    """Translate a Claude Code JSONL transcript into events. None on parse failure.

    Per line, events are emitted in the legacy analyzer's processing order —
    tool_use blocks, then usage blocks, then tool_result blocks — so that the
    usage/result interleaving (which carried_read_tokens depends on) survives.
    """
    events: list[Event] = []
    seq = 0
    model_counts: dict[str, int] = {}
    try:
        mtime = os.path.getmtime(path)
        with open(path, errors='ignore') as f:
            for line in f:
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                # A valid-JSON-but-non-dict line (e.g. a bare `null`, number, or
                # string) must be skipped, not walked: calling .get() on it would
                # raise and drop the *entire* session via the outer guard. Mirrors
                # parse_codex / parse_cursor_jsonl.
                if not isinstance(msg, dict):
                    continue

                m = msg.get('model') or (msg.get('message') or {}).get('model')
                if isinstance(m, str) and m:
                    model_counts[m] = model_counts.get(m, 0) + 1

                for block in _find_all_tool_use(msg):
                    name = block.get('name', '')
                    inp  = block.get('input') or {}
                    cmd  = inp.get('command', '') if isinstance(inp, dict) else ''

                    is_read = (
                        name in READ_TOOLS or
                        (name == 'Bash' and any(c in cmd for c in BASH_READ_CMDS))
                    )
                    is_write = name in WRITE_TOOLS

                    fpath = None
                    if isinstance(inp, dict):
                        fp = inp.get('file_path', '')
                        if fp:
                            fpath = fp
                    # Carry the tool_use id + command so an error tool_result can
                    # be linked back to the command that produced it (retry-loop
                    # drilldown). cmd is the Bash/shell command when present.
                    ex = {'id': block.get('id')}
                    if cmd:
                        ex['cmd'] = cmd
                    if is_read:
                        # Bash reads carry no file_path: the legacy analyzer only
                        # tracked redundant reads for READ_TOOLS calls.
                        events.append(Event(seq, 'read', tool=name,
                                            file_path=fpath if name in READ_TOOLS else None,
                                            extras=ex))
                    elif is_write:
                        events.append(Event(seq, 'edit', tool=name, file_path=fpath, extras=ex))
                    else:
                        events.append(Event(seq, 'tool_call', tool=name, extras=ex))
                    seq += 1

                for u in _find_usage(msg):
                    events.append(Event(
                        seq, 'request_usage',
                        tok_input=u.get('input_tokens', 0),
                        tok_output=u.get('output_tokens', 0),
                        tok_cache_read=u.get('cache_read_input_tokens', 0),
                        tok_cache_write=u.get('cache_creation_input_tokens', 0),
                    ))
                    seq += 1

                for tr in _find_tool_results(msg):
                    try:
                        size = len(json.dumps(tr.get('content', '')))
                    except Exception:
                        size = 0
                    events.append(Event(seq, 'tool_result', bytes=size,
                                        is_error=bool(tr.get('is_error')),
                                        extras={'tool_use_id': tr.get('tool_use_id')}))
                    seq += 1
    except Exception:
        return None
    model = max(model_counts, key=model_counts.get) if model_counts else None
    return SessionMeta('claude', 'claude', path, mtime, model=model), events


def parse_cursor_jsonl(path: str) -> tuple[SessionMeta, list[Event]] | None:
    """Translate a Cursor agent-transcript JSONL file into events.

    All entries are stored (no repo filtering); relevance inputs are kept in
    extras: {'files': [...], 'vcs_root': ...}. None on parse failure.
    """
    events: list[Event] = []
    seq = 0
    try:
        mtime = os.path.getmtime(path)
        with open(path, errors='ignore') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue

                tool = entry.get('tool') or entry.get('toolName') or ''
                vcs_root = (entry.get('vcs') or {}).get('root', '')
                files = _cursor_files_from_entry(entry)

                cmd = ''
                inp = entry.get('input')
                if isinstance(inp, dict):
                    cmd = inp.get('command', '')

                is_read = (
                    tool in CURSOR_READ_TOOLS
                    or (tool == CURSOR_BASH_TOOL
                        and any(c in cmd for c in BASH_READ_CMDS))
                )
                is_write = tool in CURSOR_WRITE_TOOLS
                kind = 'read' if is_read else ('edit' if is_write else 'tool_call')
                events.append(Event(seq, kind, tool=tool,
                                    extras={'files': files, 'vcs_root': vcs_root}))
                seq += 1
    except Exception:
        return None
    return SessionMeta('cursor-jsonl', 'cursor', path, mtime), events


def parse_cursor_db(db_path: str) -> list[tuple[SessionMeta, list[Event]]]:
    """Translate a Cursor workspace SQLite (state.vscdb) into per-composer sessions.

    Returns one (meta, events) pair per composerId. Empty list if sqlite3 is
    unavailable, the file can't be opened, or the schema differs.
    """
    try:
        import sqlite3
    except ImportError:
        return []

    try:
        con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True,
                              check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            con.close()
    except Exception:
        return []

    composer_bubbles: dict[str, list[dict]] = {}
    for row in rows:
        parts = row['key'].split(':', 2)
        if len(parts) != 3:
            continue
        composer_id = parts[1]
        try:
            blob = row['value']
            bubble = json.loads(blob if isinstance(blob, str) else blob.decode())
        except Exception:
            continue
        if not isinstance(bubble, dict):
            continue
        composer_bubbles.setdefault(composer_id, []).append(bubble)

    out: list[tuple[SessionMeta, list[Event]]] = []
    for composer_id, bubbles in composer_bubbles.items():
        events: list[Event] = []
        seq = 0
        mtime = 0.0

        for bubble in sorted(bubbles, key=lambda b: b.get('createdAt', 0)):
            ts_ms = bubble.get('createdAt', 0)
            ts = ts_ms / 1000 if ts_ms > 1e10 else float(ts_ms)
            if ts:
                mtime = max(mtime, ts)

            for tfd in bubble.get('toolFormerData') or []:
                if not isinstance(tfd, dict):
                    continue
                tool = tfd.get('toolName', '')
                params = tfd.get('params') or {}
                files: list[str] = []
                if isinstance(params, dict):
                    for key in ('target_file', 'file_path', 'path', 'filename'):
                        v = params.get(key)
                        if isinstance(v, str) and v:
                            files.append(v)

                is_read  = tool in CURSOR_READ_TOOLS
                is_write = tool in CURSOR_WRITE_TOOLS
                kind = 'read' if is_read else ('edit' if is_write else 'tool_call')
                events.append(Event(seq, kind, tool=tool, extras={'files': files}))
                seq += 1

        meta = SessionMeta('cursor-db', 'cursor', db_path,
                           mtime or os.path.getmtime(db_path),
                           event_mtime=mtime or None,
                           external_id=composer_id)
        out.append((meta, events))
    return out


def parse_codex(path: str) -> tuple[SessionMeta, list[Event]] | None:
    """Translate a Codex JSONL session file into events. None on parse failure.

    session_meta.cwd may change mid-file, so each event captures the cwd that
    was current when it occurred (extras['workdir'] / extras['cwd']).
    """
    events: list[Event] = []
    seq = 0
    session_cwd = ''
    model: str | None = None
    try:
        mtime = os.path.getmtime(path)
        with open(path, errors='ignore') as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue

                t = obj.get('type', '')
                p = obj.get('payload', {})
                if not isinstance(p, dict):
                    continue

                if not model:
                    m = p.get('model') or (p.get('info') or {}).get('model')
                    if isinstance(m, str) and m:
                        model = m

                if t == 'session_meta':
                    session_cwd = p.get('cwd', '') or ''

                if t == 'event_msg' and p.get('type') == 'token_count':
                    last = (p.get('info') or {}).get('last_token_usage') or {}
                    events.append(Event(
                        seq, 'request_usage',
                        tok_input=last.get('input_tokens', 0),
                        tok_output=last.get('output_tokens', 0),
                        tok_cache_read=last.get('cached_input_tokens', 0),
                    ))
                    seq += 1
                    continue

                if t != 'response_item':
                    continue

                pt = p.get('type', '')

                if pt == 'function_call' and p.get('name') == 'exec_command':
                    args_raw = p.get('arguments', '')
                    try:
                        args = (json.loads(args_raw)
                                if isinstance(args_raw, str) else args_raw) or {}
                    except Exception:
                        args = {}
                    cmd     = args.get('cmd', '')     if isinstance(args, dict) else ''
                    workdir = args.get('workdir', '') if isinstance(args, dict) else ''
                    wd      = workdir or session_cwd

                    is_read = any(c in cmd for c in BASH_READ_CMDS)
                    kind = 'read' if is_read else 'tool_call'
                    events.append(Event(seq, kind, tool='exec_command',
                                        extras={'workdir': wd, 'cmd': cmd,
                                                'id': p.get('call_id')}))
                    seq += 1

                elif pt == 'custom_tool_call' and p.get('name') == 'apply_patch':
                    patch_text = p.get('input', '')
                    files_in_patch: list[str] = []
                    for m in _CODEX_WRITE_PATCH_RE.finditer(str(patch_text)):
                        fp = m.group(2).strip()
                        if fp:
                            files_in_patch.append(fp)
                    events.append(Event(seq, 'edit', tool='apply_patch',
                                        extras={'files': files_in_patch,
                                                'cwd': session_cwd}))
                    seq += 1

                elif pt == 'function_call_output':
                    out = p.get('output', '')
                    # Exit codes > 1 in exec output indicate genuine tool failures
                    # (code 1 is common for grep-no-match, so we skip it)
                    if isinstance(out, str) and 'Process exited with code' in out:
                        for code in range(2, 128):
                            if f'code {code}' in out:
                                events.append(Event(seq, 'tool_result', is_error=True,
                                                    extras={'tool_use_id': p.get('call_id')}))
                                seq += 1
                                break
    except Exception:
        return None
    return SessionMeta('codex', 'codex', path, mtime, cwd=session_cwd, model=model), events


# ── Derivation ────────────────────────────────────────────────────────────────

def derive_session(meta: SessionMeta, events: list[Event],
                   repo_root: str | None = None, *,
                   big_result_bytes: int) -> dict | None:
    """Replay events and return the legacy per-session metrics dict.

    repo_root is required for cursor/codex relevance filtering; claude sessions
    ignore it. Returns None when the per-source "no relevant activity" rules of
    the legacy analyzers say so.
    """
    adapter = meta.adapter
    repo_sep = (repo_root.rstrip(os.sep) + os.sep) if repo_root else os.sep

    # Scan all events (not just read/edit) — ctx_* calls are 'tool_call' events,
    # which the metric loop below skips. Tool names survive the store roundtrip.
    context_mode = any(_is_context_tool(ev.tool) for ev in events)

    def _under(p: str) -> bool:
        return p == repo_root or p.startswith(repo_sep)

    reads = 0
    reads_before_edit = 0
    edits = 0
    first_edit_seen = False
    read_counts: dict[str, int] = {}
    edit_counts: dict[str, int] = {}

    cache_writes = 0
    cache_reads = 0
    input_tokens = 0
    ctx_per_req: list[int] = []
    output_per_req: list[int] = []
    big_results = 0
    big_result_positions: list[tuple[int, int]] = []
    error_results = 0
    any_relevant = False  # cursor-db session-level gate

    # Retry-loop drilldown: map tool_use id → command label, then attribute each
    # error tool_result back to the command that produced it and group by label.
    id_to_label: dict[str, str] = {}
    failed_cmd_counts: dict[str, int] = {}

    # Measured orientation: raw token sums for requests before the first
    # counted edit (the same first_edit_seen boundary reads_before_edit uses).
    requests_before_edit = 0
    pre_edit_input = 0
    pre_edit_cache_reads = 0
    pre_edit_cache_writes = 0

    for ev in events:
        kind = ev.kind

        # Register every tool_use's id → command label (any kind: a failing
        # command is usually a 'tool_call' like Bash/exec, not a read/edit).
        ev_extras = ev.extras or {}
        _bid = ev_extras.get('id')
        if _bid and kind in ('read', 'edit', 'tool_call'):
            id_to_label[_bid] = _cmd_label(ev.tool, ev_extras.get('cmd'), ev.file_path)

        if kind == 'request_usage':
            cache_writes += ev.tok_cache_write
            cache_reads  += ev.tok_cache_read
            input_tokens += ev.tok_input
            ctx_per_req.append(ev.tok_input + ev.tok_cache_read + ev.tok_cache_write)
            output_per_req.append(ev.tok_output)
            if not first_edit_seen:
                requests_before_edit += 1
                pre_edit_input        += ev.tok_input
                pre_edit_cache_reads  += ev.tok_cache_read
                pre_edit_cache_writes += ev.tok_cache_write
            continue

        if kind == 'tool_result':
            if ev.is_error:
                error_results += 1
                label = id_to_label.get(ev_extras.get('tool_use_id')) or '(unknown command)'
                failed_cmd_counts[label] = failed_cmd_counts.get(label, 0) + 1
            size = ev.bytes or 0
            if size > big_result_bytes:
                big_results += 1
                big_result_positions.append((len(ctx_per_req), size // 4))
            continue

        if kind not in ('read', 'edit'):
            continue

        extras = ev.extras or {}
        if adapter == 'claude':
            files = [ev.file_path] if ev.file_path else []
        elif adapter == 'cursor-jsonl':
            files = extras.get('files') or []
            relevant = (extras.get('vcs_root') == repo_root
                        or any(_under(f) for f in files))
            if not relevant:
                continue
        elif adapter == 'cursor-db':
            # Legacy behavior: every read/edit is counted; relevance is only a
            # session-level gate (any one event under the repo keeps the session).
            files = extras.get('files') or []
            if any(_under(f) for f in files):
                any_relevant = True
        else:  # codex
            if kind == 'read':
                files = []
                if not _under(extras.get('workdir') or ''):
                    continue
            else:
                # Codex apply_patch paths are usually repo-relative
                # ("cram/audit.py"); resolve against the session cwd before the
                # repo check. Deliberate fix over the legacy analyzer, which
                # compared raw paths and dropped relative-path edits.
                cwd = extras.get('cwd') or ''
                files = [f if os.path.isabs(f) else os.path.normpath(os.path.join(cwd, f))
                         for f in (extras.get('files') or [])]
                if files:
                    if not any(_under(f) for f in files):
                        continue
                elif not _under(cwd):
                    continue

        if kind == 'read':
            reads += 1
            if not first_edit_seen:
                reads_before_edit += 1
            for fp in files:
                if fp:
                    read_counts[fp] = read_counts.get(fp, 0) + 1
        else:
            edits += 1
            first_edit_seen = True
            for fp in files:
                if fp:
                    edit_counts[fp] = edit_counts.get(fp, 0) + 1

    if adapter == 'cursor-db':
        if not any_relevant or (reads == 0 and edits == 0):
            return None
    elif adapter in ('cursor-jsonl', 'codex'):
        if reads == 0 and edits == 0:
            return None

    requests  = len(ctx_per_req)
    total_ctx = sum(ctx_per_req)
    tail_share = (
        sum(ctx_per_req[2 * requests // 3:]) / total_ctx
        if requests >= 6 and total_ctx else None
    )
    carried_read_tokens = sum(
        tok * max(requests - k, 0) for k, tok in big_result_positions
    )
    first_ctx = ctx_per_req[0] if ctx_per_req else 0
    context_growth_factor = (
        max(ctx_per_req) / first_ctx if requests >= 2 and first_ctx else None
    )

    sess = {
        'reads':                   reads,
        'reads_before_edit':       reads_before_edit,
        'edits':                   edits,
        'ratio':                   reads_before_edit / max(edits, 1),
        'cache_writes':            cache_writes,
        'cache_reads':             cache_reads,
        'requests':                requests,
        'avg_context_per_request': total_ctx / requests if requests else 0.0,
        'peak_context':            max(ctx_per_req) if ctx_per_req else 0,
        'first_context':           first_ctx,
        'context_growth_factor':   context_growth_factor,
        'avg_output_tokens':       sum(output_per_req) / len(output_per_req) if output_per_req else 0.0,
        'tail_share':              tail_share,
        'big_results':             big_results,
        'carried_read_tokens':     carried_read_tokens,
        'redundant_reads':         sum(c - 1 for c in read_counts.values() if c > 1),
        'error_results':           error_results,
        # Failed commands grouped by normalized text, worst-first. The retry-loop
        # signal: the same command failing repeatedly (e.g. a wrong test command
        # run four times). [{cmd, failures}].
        'failed_commands':         [
            {'cmd': lbl, 'failures': n}
            for lbl, n in sorted(failed_cmd_counts.items(), key=lambda kv: -kv[1])
        ],
        'edit_churn':              sum(c - 1 for c in edit_counts.values() if c > 1),
        'mtime':                   meta.mtime,
        # Session identifier. Cursor-db composers share a path, so prefer their
        # external_id; otherwise pull the UUID out of the filename (Claude files
        # are bare UUIDs; Codex files are rollout-<ts>-<uuid>).
        'session_id':              meta.external_id or _session_ident(meta.path),
        # Measured-orientation inputs (raw; pricing applied at query time)
        'input_tokens':            input_tokens,
        'requests_before_edit':    requests_before_edit,
        'pre_edit_input_tokens':   pre_edit_input,
        'pre_edit_cache_reads':    pre_edit_cache_reads,
        'pre_edit_cache_writes':   pre_edit_cache_writes,
        # Per-file evidence (relevance rules already applied above).
        # Claude: READ_TOOLS reads with a file_path (Bash reads have none);
        # Cursor: every path on a relevant event; Codex: empty (no paths).
        'read_file_counts':        read_counts,
        'edit_file_counts':        edit_counts,
        # Additive: was a context tool (context-mode) active this session?
        'context_mode':            context_mode,
        # Model id (for per-session $ attribution); None for Cursor / unrecorded.
        'model':                   meta.model,
    }
    # Legacy dicts carry a 'source' key only for non-Claude sessions.
    if meta.source != 'claude':
        sess['source'] = meta.source
    return sess


def derive_session_timeline(meta: SessionMeta, events: list[Event],
                            repo_root: str | None = None, *,
                            big_result_bytes: int) -> dict | None:
    """Per-request waterfall for one session — the drill-down behind --session.

    Where :func:`derive_session` collapses a session to aggregate metrics, this
    keeps the timeline: one row per request_usage with its token classes, the
    context delta vs the previous request, and the tool activity that caused the
    jump (reads/edits issued, oversized results returned). It then attributes the
    session's wasted tokens to concrete causes:

      * carried   — oversized tool results re-read by every later request. Same
                    math as carried_read_tokens, but itemized and named (the
                    result is attributed to the most recent read's file_path).
      * redundant — files read more than once.
      * retries   — failed tool calls (error tool_results).

    Context math (input + cache_read + cache_write per request, size//4 tokens
    for results) mirrors derive_session exactly so the two never disagree.
    Returns None when the session has no request_usage rows (nothing to chart).
    """
    rows: list[dict] = []
    carried: list[dict] = []
    read_counts: dict[str, int] = {}
    retries = 0
    id_to_label: dict[str, str] = {}
    failed_cmd_counts: dict[str, int] = {}

    pending: list[str] = []   # tool activity since the previous request row
    last_read_file: str | None = None
    prev_ctx = 0

    for ev in events:
        kind = ev.kind

        _ex = ev.extras or {}
        _bid = _ex.get('id')
        if _bid and kind in ('read', 'edit', 'tool_call'):
            id_to_label[_bid] = _cmd_label(ev.tool, _ex.get('cmd'), ev.file_path)

        if kind == 'read':
            files = [ev.file_path] if ev.file_path else (ev.extras or {}).get('files') or []
            fp = files[0] if files else None
            if fp:
                read_counts[fp] = read_counts.get(fp, 0) + 1
                last_read_file = fp
            pending.append(f'Read {os.path.basename(fp)}' if fp else 'Read')
            continue

        if kind == 'edit':
            fp = ev.file_path
            pending.append(f'Edit {os.path.basename(fp)}' if fp else 'Edit')
            continue

        if kind == 'tool_call':
            if ev.tool:
                pending.append(ev.tool)
            continue

        if kind == 'tool_result':
            if ev.is_error:
                retries += 1
                label = id_to_label.get(_ex.get('tool_use_id'))
                failed_cmd_counts[label or '(unknown command)'] = (
                    failed_cmd_counts.get(label or '(unknown command)', 0) + 1)
                short = (label[:50] if label else None)
                pending.append(f'✗ failed: {short}' if short else '✗ tool error')
            size = ev.bytes or 0
            if size > big_result_bytes:
                tok = size // 4
                # The big result lands in the context of the NEXT request and
                # rides on every request after it. carried_turns is filled in
                # once we know the total request count.
                carried.append({
                    'file': last_read_file,
                    'tokens': tok,
                    'at_request': len(rows),
                })
                src = os.path.basename(last_read_file) if last_read_file else 'result'
                pending.append(f'⚠ {src} → {tok:,} tok result ({size // 1000} KB)')
            continue

        if kind == 'request_usage':
            ctx = ev.tok_input + ev.tok_cache_read + ev.tok_cache_write
            if ctx == 0 and ev.tok_output == 0:
                continue   # empty usage record — nothing to chart
            usage = (ev.tok_input, ev.tok_cache_read, ev.tok_cache_write, ev.tok_output)
            # Claude Code re-logs one request's usage across several transcript
            # lines; collapse consecutive identical tuples into one logical turn
            # and fold their tool activity into that row.
            if rows and rows[-1]['_usage'] == usage:
                rows[-1]['notes'].extend(pending)
                pending = []
                continue
            rows.append({
                'turn':        len(rows) + 1,
                '_usage':      usage,
                'input':       ev.tok_input,
                'cache_read':  ev.tok_cache_read,
                'cache_write': ev.tok_cache_write,
                'output':      ev.tok_output,
                'context':     ctx,
                'delta':       ctx - prev_ctx if rows else 0,
                'notes':       pending,
            })
            prev_ctx = ctx
            pending = []

    if not rows:
        return None

    requests = len(rows)
    for r in rows:
        del r['_usage']
    for c in carried:
        c['carried_turns']  = max(requests - c['at_request'], 0)
        c['carried_tokens'] = c['tokens'] * c['carried_turns']
    carried.sort(key=lambda c: -c['carried_tokens'])

    redundant = sorted(
        ((fp, n) for fp, n in read_counts.items() if n > 1),
        key=lambda t: (-t[1], t[0]),
    )

    contexts = [r['context'] for r in rows]
    return {
        'adapter':       meta.adapter,
        'source':        meta.source,
        'path':          meta.path,
        'mtime':         meta.mtime,
        'requests':      requests,
        'rows':          rows,
        'peak_context':  max(contexts),
        'first_context': contexts[0],
        'carried':       carried,
        'carried_read_tokens': sum(c['carried_tokens'] for c in carried),
        'redundant':     redundant,
        'retries':       retries,
        'failed_commands': [
            {'cmd': lbl, 'failures': n}
            for lbl, n in sorted(failed_cmd_counts.items(), key=lambda kv: -kv[1])
        ],
    }
