"""cram ui — Textual TUI dashboard for decisions, session efficiency, and context health."""

from __future__ import annotations
import glob
import os
import re
import subprocess
import sys
from datetime import datetime

_TEXTUAL_MISSING = """\
textual is not installed. Run:
  pip install "cram-ai[tui]"
or:
  pip install textual
"""


def _require_textual() -> None:
    try:
        import textual  # noqa: F401
    except ImportError:
        print(_TEXTUAL_MISSING, file=sys.stderr)
        sys.exit(1)


# ── Decision parsing ────────────────────────────────────────────────────────

_ENTRY_RE = re.compile(
    r'^## \[(DECISION-\d+)\](.*?)$',
    re.MULTILINE,
)
_STATUS_RE = re.compile(r'\*\*Status:\*\*\s*(.+)', re.IGNORECASE)
_DATE_RE   = re.compile(r'\*\*Date:\*\*\s*(.+)')
_REASON_RE = re.compile(r'\*\*Reason:\*\*\s*(.+)')


def _parse_decisions(content: str) -> list[dict]:
    """Return list of decision dicts parsed from DECISIONS.md."""
    entries = []
    splits = list(_ENTRY_RE.finditer(content))
    for i, m in enumerate(splits):
        end = splits[i + 1].start() if i + 1 < len(splits) else len(content)
        block = content[m.start():end]
        decision_id  = m.group(1)
        heading_rest = m.group(2).strip()
        pending      = '[PENDING]' in heading_rest
        title = heading_rest.replace('[PENDING]', '').strip()

        status_m = _STATUS_RE.search(block)
        date_m   = _DATE_RE.search(block)
        reason_m = _REASON_RE.search(block)

        entries.append({
            'id':      decision_id,
            'title':   title,
            'pending': pending,
            'status':  status_m.group(1).strip() if status_m else '',
            'date':    date_m.group(1).strip()   if date_m   else '',
            'reason':  reason_m.group(1).strip() if reason_m else '',
            'block':   block,
        })
    return entries


def _approve_decision(content: str, decision_id: str) -> str:
    """Remove [PENDING] tag and set Status to Accepted in DECISIONS.md content."""
    content = re.sub(
        rf'(## \[{re.escape(decision_id)}\]) \[PENDING\] ',
        r'\1 ',
        content,
    )
    entry_start = content.find(f'## [{decision_id}]')
    if entry_start == -1:
        return content
    next_entry = content.find('\n## [', entry_start + 1)
    block_end  = next_entry if next_entry != -1 else len(content)
    block = content[entry_start:block_end]
    new_block = _STATUS_RE.sub('**Status:** Accepted', block, count=1)
    return content[:entry_start] + new_block + content[block_end:]


def _delete_decision(content: str, decision_id: str) -> str:
    """Remove a decision entry block from DECISIONS.md content."""
    start = content.find(f'## [{decision_id}]')
    if start == -1:
        return content
    next_entry = content.find('\n## [', start + 1)
    block_end  = next_entry if next_entry != -1 else len(content)
    return content[:start].rstrip('\n') + '\n' + content[block_end:]


# ── Textual app ─────────────────────────────────────────────────────────────

def _build_app(root: str):  # noqa: ANN202
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import ScrollableContainer, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import (
        Button, DataTable, Footer, Header, Input, Label,
        ListItem, ListView, Markdown, ProgressBar, RichLog, Static,
        TabbedContent, TabPane,
    )
    from textual.reactive import reactive
    from textual import work
    from textual.worker import WorkerState

    from cram.context_dir import context_path
    from cram.audit import (_analyze_transcript_cached, _project_transcript_dir,
                            collect_audit, collect_layer, format_layer_row, LAYERS)
    from cram.audit_report import render_report
    from cram.audit_store import AuditStore
    from cram.health import context_health

    DECISIONS_FILE = 'DECISIONS.md'

    # ── Task input modal ─────────────────────────────────────────

    class TaskInputModal(ModalScreen):
        CSS = """
        TaskInputModal {
            align: center middle;
        }
        #modal-dialog {
            background: $surface;
            border: thick $accent;
            padding: 1 2;
            width: 64;
            height: auto;
        }
        #modal-dialog Label {
            margin-bottom: 1;
        }
        #modal-input {
            margin-bottom: 1;
        }
        """
        BINDINGS = [Binding('escape', 'cancel', 'Cancel')]

        def compose(self) -> ComposeResult:
            with Vertical(id='modal-dialog'):
                yield Label('[b]cram task[/b] — enter task description')
                yield Input(placeholder='e.g. fix the auth middleware', id='modal-input')
                yield Button('Run', variant='primary', id='modal-confirm')

        def on_mount(self) -> None:
            self.query_one('#modal-input', Input).focus()

        def on_input_submitted(self) -> None:
            self._submit()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == 'modal-confirm':
                self._submit()

        def _submit(self) -> None:
            value = self.query_one('#modal-input', Input).value.strip()
            if not value:
                self.notify('Task description cannot be blank', severity='warning')
                return
            self.dismiss(value)

        def action_cancel(self) -> None:
            self.dismiss(None)

    # ── Decisions pane ───────────────────────────────────────────

    class DecisionsPane(VerticalScroll):
        focused_id: reactive[str | None] = reactive(None)

        def _decisions_path(self) -> str:
            return context_path(root, DECISIONS_FILE, warn=True)

        def _load(self) -> tuple[str, list[dict]]:
            path = self._decisions_path()
            if not os.path.exists(path):
                return '', []
            with open(path) as f:
                content = f.read()
            return content, _parse_decisions(content)

        def compose(self) -> ComposeResult:
            yield Label('[b]Pending review[/b]', id='pending-header')
            yield ListView(id='pending-list')
            yield Label('[b]Accepted[/b]', id='accepted-header')
            yield Static('', id='accepted-list')

        def on_mount(self) -> None:
            self.refresh_decisions()

        def refresh_decisions(self) -> None:
            try:
                self._refresh_decisions_inner()
            except Exception as ex:
                try:
                    self.query_one('#accepted-list', Static).update(
                        f'[red]Error loading decisions: {ex}[/red]'
                    )
                except Exception:
                    pass

        def _refresh_decisions_inner(self) -> None:
            _, entries = self._load()
            pending  = [e for e in entries if e['pending']]
            accepted = [e for e in entries if not e['pending']]

            lv = self.query_one('#pending-list', ListView)
            lv.clear()
            if pending:
                for e in pending:
                    label = f"[yellow]{e['id']}[/yellow]  {e['title']}"
                    if e['reason']:
                        label += f"\n  [dim]{e['reason']}[/dim]"
                    lv.append(ListItem(Label(label), id=f'pending-{e["id"]}'))
                self.focused_id = pending[0]['id']
            else:
                lv.append(ListItem(Label('[dim]No pending decisions.[/dim]')))
                self.focused_id = None

            accepted_text = ''
            for e in accepted:
                date = f'  [dim]{e["date"]}[/dim]' if e['date'] else ''
                accepted_text += f'  {e["id"]}  {e["title"]}{date}\n'
            self.query_one('#accepted-list', Static).update(
                accepted_text or '  [dim]None yet.[/dim]\n'
            )

        def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
            if event.item is not None and event.item.id:
                # id is "pending-DECISION-NNN"
                self.focused_id = event.item.id[len('pending-'):]

        def approve_focused(self) -> str | None:
            path = self._decisions_path()
            if not self.focused_id or not os.path.exists(path):
                return None
            with open(path) as f:
                content = f.read()
            with open(path, 'w') as f:
                f.write(_approve_decision(content, self.focused_id))
            approved_id = self.focused_id
            self.refresh_decisions()
            return approved_id

        def delete_focused(self) -> str | None:
            path = self._decisions_path()
            if not self.focused_id or not os.path.exists(path):
                return None
            with open(path) as f:
                content = f.read()
            with open(path, 'w') as f:
                f.write(_delete_decision(content, self.focused_id))
            deleted_id = self.focused_id
            self.refresh_decisions()
            return deleted_id

        def pending_count(self) -> int:
            _, entries = self._load()
            return sum(1 for e in entries if e['pending'])

    # ── Audit pane ───────────────────────────────────────────────

    _BAND_STYLE = {
        'good':   ('green',  '✓ good'),
        'normal': ('yellow', '~ normal'),
        'high':   ('red',    "⚠ high — context isn't landing"),
    }

    class AuditPane(VerticalScroll):
        """The landing tab: the orientation-tax numbers, not the knobs."""

        def compose(self) -> ComposeResult:
            yield Static('', id='audit-body')

        def on_mount(self) -> None:
            self.refresh_audit()

        def refresh_audit(self) -> None:
            try:
                self._refresh_audit_inner()
            except Exception as ex:
                try:
                    self.query_one('#audit-body', Static).update(
                        f'[red]Error loading audit data: {ex}[/red]'
                    )
                except Exception:
                    pass

        def _refresh_audit_inner(self) -> None:
            data = collect_audit(root, days=30)
            body = self.query_one('#audit-body', Static)
            if data is None:
                body.update(
                    '[dim]No sessions found for this repo in the last 30 days.\n'
                    'Checks: ~/.claude/projects/ (Claude Code), '
                    '~/.cursor/agent-transcripts/ (Cursor), '
                    '~/.codex/sessions/ (Codex)[/dim]'
                )
                return

            color, label = _BAND_STYLE.get(data['ratio_band'],
                                           ('white', data['ratio_band']))
            lines = [
                f'[b]Agent sessions — last {data["days"]} days[/b]'
                f'  [dim]({data["provider"]} pricing · set CRAM_PROVIDER to change)[/dim]\n',
                f'  Sessions analysed          {data["sessions"]}',
                f'  Reads before first edit    {data["avg_reads_before_edit"]:.1f}   [dim]← primary metric[/dim]',
                f'  Read-to-edit ratio         {data["avg_ratio"]:.1f}×  [{color}]{label}[/{color}]',
                f'  Cache writes / session     {data["avg_cache_writes"]:,.0f} tok',
                f'  Cache reads / session      {data["avg_cache_reads"]:,.0f} tok',
                '',
                '[b]Cache engagement[/b]',
            ]
            extra_lines = []
            if data.get('read_only_sessions'):
                extra_lines.append(
                    f'  No-edit sessions           {data["read_only_sessions"]}'
                    f'   [dim]excluded from pre-edit share[/dim]')
            if data.get('pre_edit_spend_share') is not None:
                extra_lines.append(
                    f'  Pre-edit context share     {data["pre_edit_spend_share"]:.0%}'
                    f'   [dim]measured · {data.get("pre_edit_measured_sessions", 0)} '
                    f'edit session(s)[/dim]')
            lines[4:4] = extra_lines  # after the ratio line, before cache lines
            engaged = data['cache_engaged_sessions']
            blind   = data['cache_blind_sessions']
            total   = data['sessions']
            if engaged:
                lines.append(
                    f'  [green]✓ {engaged}/{total} sessions read from the prompt cache[/green]'
                )
            if blind:
                lines.append(
                    f'  [red]⚠ {blind} session(s) wrote cache but never read it — '
                    'caching may not be engaging[/red]'
                )
            if not engaged and not blind:
                lines.append('  [dim]No cache usage data in these transcripts.[/dim]')

            if data['avg_requests']:
                lines += ['', '[b]Context bloat[/b]']
                lines.append(f'  Requests / session         {data["avg_requests"]:.0f}')
                lines.append(
                    f'  Context per request        {data["avg_context_per_request"]:,.0f} tok'
                    f'  [dim](peak {data["peak_context"]:,})[/dim]'
                )
                if data.get('avg_first_context'):
                    lines.append(
                        f'  Context at session start   {data["avg_first_context"]:,.0f} tok'
                        f'  [dim](system prompt + initial msg)[/dim]'
                    )
                growth = data.get('avg_context_growth')
                if growth is not None:
                    n_g = data.get('context_growth_measured', 0)
                    if growth > 5:
                        gcolor, glabel = 'red', '⚠ heavy bloat'
                    elif growth > 2:
                        gcolor, glabel = 'yellow', '↑ growing'
                    else:
                        gcolor, glabel = 'green', 'stable'
                    lines.append(
                        f'  Context growth/session     [{gcolor}]{growth:.1f}×  {glabel}[/{gcolor}]'
                        f'  [dim]({n_g} session{"s" if n_g != 1 else ""})[/dim]'
                    )
                if data.get('avg_output_tokens'):
                    lines.append(
                        f'  Avg output tokens/request  {data["avg_output_tokens"]:,.0f}'
                    )
                tail = data['bloat_tail_share']
                if tail is not None:
                    if tail <= 0.36:
                        tcolor, tlabel = 'green', 'flat'
                    elif tail <= 0.5:
                        tcolor, tlabel = 'yellow', 'growing'
                    else:
                        tcolor, tlabel = 'red', 'compounding'
                    lines.append(
                        f'  Read-cost in last ⅓        {tail * 100:.0f}%  '
                        f'[{tcolor}]{tlabel}[/{tcolor}]  [dim](33% = flat)[/dim]'
                    )
                if data['sessions_with_big_results']:
                    kb = data['big_result_bytes'] // 1000
                    lines.append(
                        f'  Oversized tool results     [yellow]{data["sessions_with_big_results"]}'
                        f'/{total} sessions carried a result > {kb} KB[/yellow]'
                    )
                    lines.append(
                        f'  Carried read cost          ~${data["carried_cost_per_session"]:.4f}/session'
                        f'  [dim](re-read every turn)[/dim]'
                    )
                if data['avg_redundant_reads'] >= 0.5:
                    lines.append(
                        f'  Redundant re-reads         {data["avg_redundant_reads"]:.1f}/session'
                    )

            if data['avg_error_results'] > 0 or data['avg_edit_churn'] > 0:
                lines += ['', '[b]Retry loops[/b]']
                err = data['avg_error_results']
                if err > 8:
                    err_str = f'[red]{err:.1f}[/red]'
                elif err > 3:
                    err_str = f'[yellow]{err:.1f}[/yellow]'
                else:
                    err_str = f'{err:.1f}'
                lines.append(
                    f'  Failed tool calls/session  {err_str}'
                    f'  [dim]({data["sessions_with_errors"]}/{total} sessions had failures)[/dim]'
                )
                lines.append(
                    f'  Same-file re-edits/session {data["avg_edit_churn"]:.1f}'
                )

            top_files = [t for t in data.get('top_read_files', []) if t[1] > 1][:5]
            if top_files:
                lines += ['', '[b]Top repeated files[/b]']
                for fp, r, n in top_files:
                    lines.append(
                        f'  {r}× in {n} session{"s" if n != 1 else ""}  [dim]{fp}[/dim]'
                    )

            projects = data.get('projects') or []
            if projects:
                lines += ['', '[b]By source[/b]']
                for src, n, avg_r, avg_rbe, avg_cw in projects:
                    # Claude project entries use a directory path; shorten to 'claude'
                    if src in ('cursor', 'codex'):
                        src_label = src
                    else:
                        src_label = 'claude'
                    lines.append(
                        f'  {src_label:<12}  {n} session{"s" if n != 1 else ""}  '
                        f'reads/session {avg_r:.1f}  rbe {avg_rbe:.1f}'
                        + (f'  cache {avg_cw:,.0f} tok' if avg_cw else '')
                    )

            if data['weekly']:
                lines += ['', '[b]Trend — reads before first edit (weekly avg)[/b]']
                max_rbe = max(avg for _, avg, _ in data['weekly']) or 1.0
                for wk, avg, n in data['weekly']:
                    filled = int(round((avg / max_rbe) * 10))
                    bar = '█' * filled + '░' * (10 - filled)
                    plural = 's' if n != 1 else ''
                    lines.append(
                        f'  {wk}  {bar} {avg:5.1f}  [dim]({n} session{plural})[/dim]'
                    )

            lines += [
                '',
                f'[dim]Modeled orientation cost ({data["provider"]} pricing): '
                f'~${data["orient_cost_per_session"]:.4f}/session · '
                f'~${data["monthly_orient_cost"]:.2f}/month — the ratio is the measured signal.[/dim]',
                "[dim]Ratio guide: < 2× good · 2–5× normal · > 5× context isn't landing[/dim]",
            ]
            body.update('\n'.join(lines))

    # ── Sessions pane ────────────────────────────────────────────

    def _build_task_intervals(repo_root: str) -> list[tuple[float, float, str]]:
        """Return list of (start_ts, end_ts, task) sorted newest-first.

        Intervals are reconstructed from TASK_HISTORY.jsonl + session.json.
        Each history entry's `ts` is when that task was *archived* (= when the
        next task started), so consecutive archive times give us start/end bounds.
        """
        import json as _json
        from cram.session import load_session

        intervals: list[tuple[float, float, str]] = []
        history_path = os.path.join(repo_root, '.ai-context', 'TASK_HISTORY.jsonl')

        entries: list[dict] = []
        try:
            if os.path.exists(history_path):
                with open(history_path, errors='ignore') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entries.append(_json.loads(line))
                            except Exception:
                                pass
        except Exception:
            pass

        entries = [e for e in entries if not e.get('task', '').startswith('<!--')]

        def _parse_ts(s: str) -> float:
            try:
                return datetime.fromisoformat(s).timestamp()
            except Exception:
                return 0.0

        # Build intervals: entry[i] ended at ts[i], started at ts[i-1] (or 0)
        for i, e in enumerate(entries):
            end   = _parse_ts(e.get('ts', ''))
            start = _parse_ts(entries[i - 1].get('ts', '')) if i > 0 else 0.0
            intervals.append((start, end, e.get('task', '')))

        # Current active task: started at session.set_at, ends at infinity
        session = load_session(repo_root)
        if session and session.get('task') and not session['task'].startswith('<!--'):
            start = session.get('set_at', 0.0)
            intervals.append((start, float('inf'), session['task']))

        intervals.sort(key=lambda x: x[0], reverse=True)
        return intervals

    def _task_for_mtime(mtime: float, intervals: list[tuple[float, float, str]]) -> str:
        """Return the task description active at `mtime`, truncated to 32 chars."""
        for start, end, task in intervals:
            if start <= mtime <= end:
                return task[:32] + '…' if len(task) > 32 else task
        return ''

    class SessionsPane(ScrollableContainer):
        def compose(self) -> ComposeResult:
            yield Static('', id='sessions-legend')
            yield DataTable(id='sessions-table')

        def on_mount(self) -> None:
            table = self.query_one('#sessions-table', DataTable)
            table.add_columns('Date', 'Task', 'Reads', 'Writes', 'Explore ratio', 'Cache tok', 'Efficiency')
            self.query_one('#sessions-legend', Static).update(
                '[dim]Explore ratio = reads before first write ÷ writes.  '
                'Low (< 2×) = good context.  High (> 5×) = agent explored a lot.[/dim]\n'
            )
            self.refresh_sessions()

        def refresh_sessions(self) -> None:
            try:
                self._refresh_sessions_inner()
            except Exception as ex:
                try:
                    table = self.query_one('#sessions-table', DataTable)
                    table.clear()
                    table.add_row(f'Error loading sessions: {ex}', '', '', '', '', '', '')
                except Exception:
                    pass

        def _refresh_sessions_inner(self) -> None:
            import glob as _glob
            table = self.query_one('#sessions-table', DataTable)
            table.clear()

            td = _project_transcript_dir(root)
            if not td:
                table.add_row('No transcripts found', '', '', '', '', '', '')
                return

            files = sorted(_glob.glob(td + '/*.jsonl'), key=os.path.getmtime, reverse=True)[:20]
            if not files:
                table.add_row('No sessions found', '', '', '', '', '', '')
                return

            intervals = _build_task_intervals(root)

            store = AuditStore.open()
            try:
                rows = [_analyze_transcript_cached(store, f) for f in files]
            finally:
                store.close()

            for r in rows:
                if not r:
                    continue
                mtime    = datetime.fromtimestamp(r['mtime'])
                date_str = mtime.strftime('%m-%d %H:%M')
                task_str = _task_for_mtime(r['mtime'], intervals)
                ratio    = r['ratio']
                cache_k  = f'{r["cache_reads"] // 1000}k' if r.get('cache_reads', 0) >= 1000 else str(r.get('cache_reads', 0))
                if ratio < 2:
                    eff = '[green]good[/green]'
                elif ratio > 5:
                    eff = '[red]high[/red]'
                else:
                    eff = '[yellow]normal[/yellow]'
                table.add_row(
                    date_str,
                    task_str,
                    str(r['reads']),
                    str(r['edits']),
                    f'{ratio:.1f}×',
                    cache_k,
                    eff,
                )

    # ── Health pane ──────────────────────────────────────────────

    class HealthPane(ScrollableContainer):
        def compose(self) -> ComposeResult:
            yield Static('', id='health-body')

        def on_mount(self) -> None:
            self.refresh_health()

        def refresh_health(self) -> None:
            try:
                self._refresh_health_inner()
            except Exception as ex:
                try:
                    self.query_one('#health-body', Static).update(
                        f'[red]Error loading health data: {ex}[/red]\n'
                        'Run `cram doctor` from the terminal to diagnose.'
                    )
                except Exception:
                    pass

        def _refresh_health_inner(self) -> None:
            h = context_health(root)
            score     = h['staleness_score']
            band      = h['staleness_band']
            freshness = 10 - score  # 10 = perfectly synced, 0 = critical
            color = {'fresh': 'green', 'acceptable': 'yellow',
                     'stale': 'orange1', 'critical': 'red'}.get(band, 'white')

            band_label = {
                'fresh':      'up to date',
                'acceptable': 'mostly current',
                'stale':      'stale — run cram sync',
                'critical':   'critical — run cram sync now',
            }.get(band, band)

            lines = [
                f'[b]Freshness:[/b] [{color}]{freshness}/10[/{color}]  [{color}]{band_label}[/{color}]',
            ]
            commits = h.get('commits_since_sync')
            if commits is not None:
                noun = 'commit' if commits == 1 else 'commits'
                lines.append(f'[b]Commits since last sync:[/b] {commits} {noun}')
            if h.get('last_commit_age'):
                lines.append(f'[b]Last commit:[/b] {h["last_commit_age"]}')

            lines.append('')
            lines.append('[b]Context files:[/b]')
            for fname, info in h.get('files', {}).items():
                bs     = info.get('budget_status', 'ok')
                budget = info.get('budget')
                fc     = 'green' if bs == 'ok' else ('yellow' if bs == 'near' else 'red')
                budget_str = f'/ {budget:,}' if budget else ''
                note = ''
                if bs == 'near':
                    note = '  [yellow]approaching soft target[/yellow]'
                elif bs == 'over':
                    note = '  [yellow]over soft target (informational only)[/yellow]'
                lines.append(
                    f'  [{fc}]{fname:<22}[/{fc}]  {info["tokens"]:>5} tok {budget_str}{note}'
                )

            if not h.get('files'):
                lines.append('  [dim]No context files found. Run `cram init` first.[/dim]')

            self.query_one('#health-body', Static).update('\n'.join(lines))

    # ── History pane ─────────────────────────────────────────────

    class HistoryPane(VerticalScroll):
        def compose(self) -> ComposeResult:
            yield Static('', id='history-body')

        def on_mount(self) -> None:
            self.refresh_history()

        def refresh_history(self) -> None:
            try:
                self._refresh_history_inner()
            except Exception as ex:
                try:
                    self.query_one('#history-body', Static).update(
                        f'[red]Error loading history: {ex}[/red]'
                    )
                except Exception:
                    pass

        def _refresh_history_inner(self) -> None:
            import json as _json
            from cram.session import load_session

            lines = ['[b]Recent Tasks[/b]\n']

            # Show the active session task at the top (it hasn't been archived yet)
            session = load_session(root)
            if session and session.get('task') and not session['task'].startswith('<!--'):
                set_at = session.get('set_at', 0)
                ts = datetime.fromtimestamp(set_at).strftime('%Y-%m-%d %H:%M') if set_at else ''
                lines.append(f'  [dim]{ts}[/dim]  [green]{session["task"]}[/green]  [dim](active)[/dim]')

            history_path = os.path.join(root, '.ai-context', 'TASK_HISTORY.jsonl')
            entries = []
            if os.path.exists(history_path):
                with open(history_path, errors='ignore') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entries.append(_json.loads(line))
            entries = [e for e in entries if not e.get('task', '').startswith('<!--')]
            entries = entries[::-1]
            for e in entries:
                raw_ts = e.get('ts', '')
                try:
                    dt = datetime.fromisoformat(raw_ts).astimezone()
                    ts = dt.strftime('%Y-%m-%d %H:%M')
                except Exception:
                    ts = raw_ts[:16].replace('T', ' ')
                task = e.get('task', '')
                lines.append(f'  [dim]{ts}[/dim]  {task}')

            if len(lines) == 1:
                self.query_one('#history-body', Static).update('[dim]No task history yet.[/dim]')
                return
            self.query_one('#history-body', Static).update('\n'.join(lines))

    # ── Actions pane ─────────────────────────────────────────────

    _ACTIONS_MENU = (
        '[b]Commands[/b]\n\n'
        '  [yellow bold]s[/yellow bold]       cram sync       re-generate context from codebase\n'
        '  [yellow bold]t[/yellow bold]       cram task …     set a new task (opens input)\n'
        '  [yellow bold]b[/yellow bold]       cram benchmark  show token savings table\n'
        '  [yellow bold]ctrl+k[/yellow bold]  cram doctor     check setup health\n'
    )

    class ActionsPane(VerticalScroll):
        def compose(self) -> ComposeResult:
            yield Static(_ACTIONS_MENU, id='actions-menu')
            yield Label('[b]Output[/b]', id='output-header')
            yield ProgressBar(id='output-spinner', total=None, show_eta=False)
            yield RichLog(id='output-log', highlight=True, markup=True, wrap=True)

        def start_command(self, cmd_str: str) -> None:
            log = self.query_one('#output-log', RichLog)
            log.clear()
            log.write(f'[dim]$ {cmd_str}[/dim]\n')
            self.query_one('#output-spinner', ProgressBar).display = True

        def append_output(self, text: str) -> None:
            self.query_one('#output-spinner', ProgressBar).display = False
            self.query_one('#output-log', RichLog).write(text)

    # ── Main app ─────────────────────────────────────────────────

    # ── Report pane ──────────────────────────────────────────────

    class ReportPane(VerticalScroll):
        """Full profiler report (token waterfall, findings → fix → verify,
        session leaderboard) — the shareable markdown, rendered in the TUI from
        the same render_report() the CLI uses. A thin viewer, no duplicated logic."""

        def compose(self) -> ComposeResult:
            yield Markdown('', id='report-body')

        def on_mount(self) -> None:
            self.refresh_report()

        def refresh_report(self) -> None:
            md = self.query_one('#report-body', Markdown)
            try:
                data = collect_audit(root, days=30)
                if data is None:
                    md.update('_No sessions found for this repo in the last 30 days._')
                    return
                md.update(render_report(data, root))
            except Exception as ex:
                md.update(f'**Error loading report:** {ex}')

    # ── Layers pane (drilldown) ──────────────────────────────────

    class LayersPane(VerticalScroll):
        """Drill into one waste class — highlight a layer, see its contributors."""

        _DESC = {
            'orientation': 'reads before first edit (by session)',
            'repeated':    'files re-read across sessions',
            'redundant':   'files re-read within a session',
            'carried':     'sessions carrying oversized tool output',
            'retries':     'sessions with failed tool calls',
            'churn':       'files re-edited within a session',
        }

        def compose(self) -> ComposeResult:
            yield Label('[b]Waste layers[/b] — highlight to drill in', id='layers-header')
            yield ListView(id='layers-list')
            yield Static('', id='layers-body')

        def on_mount(self) -> None:
            lv = self.query_one('#layers-list', ListView)
            if not len(lv):
                for name in LAYERS:
                    lv.append(ListItem(
                        Label(f'{name}  [dim]{self._DESC[name]}[/dim]'),
                        id=f'layer-{name}'))
            self._show('orientation')

        def refresh_layers(self) -> None:
            lv = self.query_one('#layers-list', ListView)
            item = lv.highlighted_child
            name = item.id[len('layer-'):] if item and item.id else 'orientation'
            self._show(name)

        def on_list_view_highlighted(self, event: 'ListView.Highlighted') -> None:
            if event.item is not None and event.item.id:
                self._show(event.item.id[len('layer-'):])

        def _show(self, name: str) -> None:
            body = self.query_one('#layers-body', Static)
            try:
                rows = collect_layer(root, name, days=30)
            except Exception as ex:
                body.update(f'[red]Error: {ex}[/red]')
                return
            if not rows:
                body.update(f'[dim]No {name} evidence in the last 30 days.[/dim]')
                return
            lines = [f'[b]{name}[/b]  [dim]{self._DESC[name]}[/dim]\n']
            for r in rows[:15]:
                lines.append('  ' + format_layer_row(name, r, root))
            body.update('\n'.join(lines))

    class CramApp(App):
        TITLE = 'cram-ai'
        CSS = """
        AuditPane, ReportPane, LayersPane, DecisionsPane, SessionsPane,
        HealthPane, ActionsPane {
            padding: 1 2;
        }
        Label#pending-header, Label#accepted-header, Label#slots-header,
        Label#output-header {
            color: $accent;
            padding: 1 0 0 0;
        }
        DataTable {
            height: auto;
        }
        Footer {
            background: $surface;
        }
        ListView {
            height: auto;
            max-height: 14;
            border: solid $surface-lighten-2;
            margin-bottom: 1;
        }
        ListView > ListItem {
            padding: 0 1;
        }
        ListView > ListItem.--highlight {
            background: $accent 20%;
        }
        RichLog {
            height: 1fr;
            min-height: 8;
            border: solid $surface-lighten-2;
            padding: 0 1;
        }
        #actions-menu {
            margin-bottom: 1;
        }
        #output-spinner {
            display: none;
            height: 1;
            margin: 1 0;
        }
        TaskInputModal {
            align: center middle;
        }
        #modal-dialog {
            background: $surface;
            border: thick $accent;
            padding: 1 2;
            width: 64;
            height: auto;
        }
        #modal-input {
            margin-bottom: 1;
        }
        """
        BINDINGS = [
            Binding('a',      'approve',   'Approve',   show=True),
            Binding('d',      'delete',    'Delete',    show=True),
            Binding('r',      'refresh',   'Refresh',   show=True),
            Binding('s',      'sync',      'Sync',      show=True),
            Binding('t',      'task',      'Task',      show=True),
            Binding('b',      'benchmark', 'Benchmark', show=True),
            Binding('ctrl+k', 'doctor',    'Doctor',    show=True),
            Binding('q',      'quit',      'Quit',      show=True),
        ]

        def compose(self) -> ComposeResult:
            yield Header()
            with TabbedContent(initial='audit'):
                with TabPane('Audit', id='audit'):
                    yield AuditPane(id='audit-pane')
                with TabPane('Report', id='report'):
                    yield ReportPane(id='report-pane')
                with TabPane('Layers', id='layers'):
                    yield LayersPane(id='layers-pane')
                with TabPane('Sessions', id='sessions'):
                    yield SessionsPane(id='sessions-pane')
                with TabPane('Decisions', id='decisions'):
                    yield DecisionsPane(id='decisions-pane')
                with TabPane('Health', id='health'):
                    yield HealthPane(id='health-pane')
                with TabPane('History', id='history'):
                    yield HistoryPane(id='history-pane')
                with TabPane('Actions', id='actions'):
                    yield ActionsPane(id='actions-pane')
            yield Footer()

        def on_mount(self) -> None:
            self._update_title()
            self.set_interval(30, self._auto_refresh)

        def on_tabbed_content_tab_activated(self, event) -> None:
            pane_id = event.pane.id if event.pane else None
            refresh_map = {
                'audit':     ('#audit-pane',     'refresh_audit'),
                'report':    ('#report-pane',    'refresh_report'),
                'layers':    ('#layers-pane',    'refresh_layers'),
                'decisions': ('#decisions-pane', 'refresh_decisions'),
                'sessions':  ('#sessions-pane',  'refresh_sessions'),
                'health':    ('#health-pane',    'refresh_health'),
                'history':   ('#history-pane',   'refresh_history'),
            }
            pair = refresh_map.get(pane_id)
            if pair:
                try:
                    w = self.query_one(pair[0])
                    getattr(w, pair[1])()
                except Exception:
                    pass

        def _update_title(self) -> None:
            pane = self.query_one('#decisions-pane', DecisionsPane)
            n = pane.pending_count()
            repo_name = os.path.basename(root)
            self.sub_title = f'{repo_name}  •  {n} pending' if n else repo_name

        def _auto_refresh(self) -> None:
            self.action_refresh()

        # ── Decision actions ──────────────────────────────────────

        def action_approve(self) -> None:
            pane = self.query_one('#decisions-pane', DecisionsPane)
            approved = pane.approve_focused()
            if approved:
                self.notify(f'Approved {approved}', severity='information')
                self._update_title()
            else:
                self.notify('No pending decision selected', severity='warning')

        def action_delete(self) -> None:
            pane = self.query_one('#decisions-pane', DecisionsPane)
            deleted = pane.delete_focused()
            if deleted:
                self.notify(f'Deleted {deleted}', severity='warning')
                self._update_title()
            else:
                self.notify('No pending decision selected', severity='warning')

        def action_refresh(self) -> None:
            for widget_id, method in [
                ('#audit-pane',     'refresh_audit'),
                ('#decisions-pane', 'refresh_decisions'),
                ('#sessions-pane',  'refresh_sessions'),
                ('#health-pane',    'refresh_health'),
                ('#history-pane',   'refresh_history'),
            ]:
                try:
                    w = self.query_one(widget_id)
                    getattr(w, method)()
                except Exception:
                    pass
            self._update_title()

        # ── CLI actions ───────────────────────────────────────────

        def _run_cli(self, cmd: list[str]) -> None:
            """Switch to Actions tab, show command, run it in a worker thread."""
            try:
                self.query_one(TabbedContent).active = 'actions'
            except Exception:
                pass
            actions = self.query_one('#actions-pane', ActionsPane)
            actions.start_command(' '.join(cmd))

            def _task() -> str:
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, cwd=root)
                    return (r.stdout or '') + (r.stderr or '')
                except Exception as exc:
                    return f'Error: {exc}\n'

            self.run_worker(_task, thread=True, exclusive=True, name='cli-cmd')

        def on_worker_state_changed(self, event) -> None:
            if event.worker.name == 'cli-cmd' and event.state == WorkerState.SUCCESS:
                try:
                    actions = self.query_one('#actions-pane', ActionsPane)
                    actions.append_output(event.worker.result or '(no output)\n')
                except Exception:
                    pass
                try:
                    self.query_one('#history-pane').refresh_history()
                except Exception:
                    pass

        def action_sync(self) -> None:
            self._run_cli(['cram', 'sync'])
            self.notify('Running cram sync…')

        def action_benchmark(self) -> None:
            self._run_cli(['cram', 'benchmark'])
            self.notify('Running cram benchmark…')

        def action_doctor(self) -> None:
            self._run_cli(['cram', 'doctor'])
            self.notify('Running cram doctor…')

        @work
        async def action_task(self) -> None:
            description = await self.push_screen_wait(TaskInputModal())
            if description:
                self._run_cli(['cram', 'task', description])
                self.notify(f'Running cram task "{description}"…')

    return CramApp


# ── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    from cram.utils import find_git_root

    _require_textual()

    parser = argparse.ArgumentParser(
        prog='cram ui',
        description='TUI dashboard — orientation-tax audit, session efficiency, '
                    'decisions, context health',
        epilog=(
            'Cost attribution: the audit prices tokens using $CRAM_PROVIDER '
            '(anthropic, openai, gemini, vertex_ai, bedrock, azure, local; '
            'default anthropic). For open-source / local models use '
            'CRAM_PROVIDER=local to zero out dollars. Set it before launching — '
            'it is read at startup, not from within the TUI:\n'
            '  CRAM_PROVIDER=local cram ui'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--path', default=None, metavar='REPO_PATH')
    args = parser.parse_args()

    start = os.path.abspath(args.path) if args.path else os.getcwd()
    try:
        root = find_git_root(start)
    except Exception:
        root = start

    AppClass = _build_app(root)
    AppClass().run()


if __name__ == '__main__':
    main()
