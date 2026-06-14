"""cram rig — controlled benchmark: tokens-at-fixed-success across context providers.

The honest way to compare a context tool (cram, Headroom, context-mode, …)
against a baseline is *not* raw token count — a tool that drops tokens by
breaking the task isn't saving anything. The metric is **effective tokens at a
fixed success rate**: run the same task corpus through each provider, score
whether the task actually succeeded (an oracle, e.g. the task's test suite),
and compare token cost only among the runs that passed.

Architecture (each piece is a small, swappable seam):

  Task        a unit of work + an oracle command that defines "done" (load_corpus)
  Provider    sets up a context tool for one run            (ProviderAdapter)
  Runner      executes the agent on a task → a transcript   (Runner)
  Oracle      scores success in the workdir                 (Oracle)
  measure     effective-token cost from the transcript      (reuses audit_events)

Token measurement reuses cram.audit_events.derive_session_timeline — the same
per-request accounting as `cram audit --session` — so the rig and the audit
never disagree about what a session cost.

Status: the framework, the baseline + cram adapters, and the MockRunner are
live and tested. The Headroom and context-mode adapters are stubs that report
themselves unavailable (those tools aren't installable from here yet). The
LiveRunner drives Claude Code headless (`claude -p`) and reuses your existing
login — no API key needed; it just requires the `claude` CLI on PATH.
`cram rig --dry-run` works today: it resolves the corpus × providers grid and
reports availability without running.
"""

from __future__ import annotations

import dataclasses
import glob
import json
import os
import shutil
import subprocess
import sys
from typing import Callable, Protocol, runtime_checkable

from cram import audit_events
from cram.cost_model import get_provider_pricing


# ── Corpus ──────────────────────────────────────────────────────────────────

@dataclasses.dataclass(slots=True)
class Task:
    """One benchmark task: a prompt, an optional fixture, and a success oracle.

    fixture: path to a directory copied into each run's workdir before the agent
             starts (the code to work on). None → an empty workdir.
    check:   argv list run in the workdir after the agent finishes; exit 0 means
             the task succeeded. Empty → the task always "succeeds" (token-only).
    """
    id: str
    prompt: str
    fixture: str | None = None
    check: list[str] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> 'Task':
        if 'id' not in d or 'prompt' not in d:
            raise ValueError("task needs 'id' and 'prompt'")
        check = d.get('check') or []
        if isinstance(check, str):
            check = check.split()
        return cls(id=str(d['id']), prompt=str(d['prompt']),
                   fixture=d.get('fixture'), check=list(check))


def load_corpus(path: str) -> list[Task]:
    """Load a corpus JSON file: a list of task dicts, or {"tasks": [...]}.

    fixture paths are resolved relative to the corpus file's directory.
    """
    with open(path) as f:
        raw = json.load(f)
    items = raw['tasks'] if isinstance(raw, dict) else raw
    base = os.path.dirname(os.path.abspath(path))
    tasks: list[Task] = []
    seen: set[str] = set()
    for item in items:
        t = Task.from_dict(item)
        if t.id in seen:
            raise ValueError(f'duplicate task id: {t.id}')
        seen.add(t.id)
        if t.fixture and not os.path.isabs(t.fixture):
            t.fixture = os.path.normpath(os.path.join(base, t.fixture))
        tasks.append(t)
    return tasks


# ── Providers ───────────────────────────────────────────────────────────────

@dataclasses.dataclass(slots=True)
class Availability:
    ok: bool
    reason: str = ''


@runtime_checkable
class ProviderAdapter(Protocol):
    name: str
    def availability(self) -> Availability: ...
    def setup(self, task: Task, workdir: str) -> dict: ...


class BaselineAdapter:
    """No context tool — the control arm. Always available."""
    name = 'baseline'

    def availability(self) -> Availability:
        return Availability(True)

    def setup(self, task: Task, workdir: str) -> dict:
        return {}


class CramAdapter:
    """cram as the context provider: `cram task "<prompt>"` populates context.

    Available whenever the cram CLI is importable/on PATH (it is, here). setup()
    is best-effort: a failure to pre-load context degrades to baseline rather
    than failing the run, so a broken context tool shows up as "no savings",
    not a crash.
    """
    name = 'cram'

    def availability(self) -> Availability:
        if shutil.which('cram') is None:
            return Availability(False, 'cram CLI not on PATH')
        return Availability(True)

    def setup(self, task: Task, workdir: str) -> dict:
        try:
            subprocess.run(['cram', 'task', task.prompt], cwd=workdir,
                           capture_output=True, timeout=120)
        except Exception:
            pass  # degrade to baseline context for this run
        return {'CRAM_REPO': workdir}


class _StubAdapter:
    """A provider whose backing tool isn't installable from here yet.

    Reports itself unavailable with an actionable install hint; setup() raises
    so a caller that bypasses the availability gate fails loudly rather than
    silently benchmarking nothing.
    """
    name = '<stub>'
    install_hint = ''

    def availability(self) -> Availability:
        return Availability(False, f'{self.name} not installed — {self.install_hint} '
                                   f'(adapter is a stub)')

    def setup(self, task: Task, workdir: str) -> dict:
        raise RuntimeError(f'{self.name} adapter is a stub — {self.install_hint}')


class HeadroomAdapter(_StubAdapter):
    """Headroom (context compression). Stub until the tool is wired in."""
    name = 'headroom'
    install_hint = 'install Headroom and point the adapter at its proxy'


class ContextModeAdapter(_StubAdapter):
    """context-mode (github.com/mksglu/context-mode). Stub until wired in.

    Note: the audit already detects context-mode's ctx_* tools in transcripts,
    so an observational A/B is possible today without this controlled adapter.
    """
    name = 'context-mode'
    install_hint = 'npm i -g context-mode and register its MCP server'


class ClaudeContextAdapter:
    """claude-context (github.com/zilliztech/claude-context) — semantic code
    search MCP server. The first concrete external optimizer cram benchmarks.

    Same persona + layer as cram's own context layer: it retrieves relevant code
    before the agent reads, via an MCP server exposing index_codebase /
    search_code / clear_index / get_indexing_status. Its headline claim — *~40%
    token reduction at equivalent retrieval quality* — is exactly what the rig
    measures (tokens at fixed success), which is why it's the natural first
    external adapter.

    `detector` is the transcript signature the observational A/B matches: Claude
    Code surfaces its tools as `mcp__claude-context__search_code`, so any tool
    name containing 'claude-context' (or the search_code leaf) flags a session
    as having used it.

    Availability is real, not a stub: claude-context needs `npx` to launch its
    MCP server AND a configured backend (embedding key + Milvus/Zilliz vector
    DB) — both of which are the *user's* setup, not cram's. Without them the
    adapter reports what's missing rather than benchmarking nothing.
    setup() writes a project-scoped `.mcp.json` so `claude -p` loads the server.

    Note: claude-context must `index_codebase` before `search_code` returns
    anything, and its benefit only shows on large codebases — see SETUP below.
    """
    name = 'claude-context'
    detector = {'kind': 'mcp_tool', 'match': 'claude-context'}
    SETUP = ('Configure claude-context per its README (embedding provider key + '
             'a Milvus/Zilliz endpoint), ensure `npx` is on PATH, and index the '
             'fixture once. Override the launch command with '
             'CRAM_CLAUDE_CONTEXT_CMD.')

    def __init__(self):
        self.mcp_cmd = os.environ.get(
            'CRAM_CLAUDE_CONTEXT_CMD',
            'npx -y @zilliz/claude-context-mcp@latest')

    def availability(self) -> Availability:
        launcher = self.mcp_cmd.split()[0]
        if shutil.which(launcher) is None:
            return Availability(False, f'{launcher} not on PATH — needed to launch '
                                       f'the claude-context MCP server. {self.SETUP}')
        # Backend opt-in: claude-context needs an embedding key + vector DB. Gate
        # on an explicit signal so it never falsely reports "available".
        if not (os.environ.get('OPENAI_API_KEY')
                or os.environ.get('CRAM_CLAUDE_CONTEXT_EMBED_KEY')):
            return Availability(False, f'no embedding key for claude-context '
                                       f'(set OPENAI_API_KEY or '
                                       f'CRAM_CLAUDE_CONTEXT_EMBED_KEY). {self.SETUP}')
        return Availability(True)

    def setup(self, task: Task, workdir: str) -> dict:
        # Project-scoped MCP config so `claude -p` in workdir loads the server.
        cmd, *cmd_args = self.mcp_cmd.split()
        config = {'mcpServers': {'claude-context': {'command': cmd, 'args': cmd_args}}}
        with open(os.path.join(workdir, '.mcp.json'), 'w') as f:
            json.dump(config, f, indent=2)
        # Indexing is the agent's first move (index_codebase) or a manual
        # pre-step; not driven here. Left as a documented setup nuance.
        return {}


_BUILTIN_PROVIDERS: dict[str, Callable[[], ProviderAdapter]] = {
    'baseline':       BaselineAdapter,
    'cram':           CramAdapter,
    'headroom':       HeadroomAdapter,
    'context-mode':   ContextModeAdapter,
    'claude-context': ClaudeContextAdapter,
}


def get_provider(name: str) -> ProviderAdapter:
    if name not in _BUILTIN_PROVIDERS:
        raise KeyError(f'unknown provider {name!r}; '
                       f'choices: {", ".join(_BUILTIN_PROVIDERS)}')
    return _BUILTIN_PROVIDERS[name]()


# ── Runner & Oracle ─────────────────────────────────────────────────────────

@runtime_checkable
class Runner(Protocol):
    def run(self, task: Task, setup: dict, workdir: str) -> str | None:
        """Execute the agent and return the path to its transcript (or None)."""
        ...


def _newest_transcript_for(workdir: str) -> str | None:
    """Newest Claude Code transcript for a working directory, or None.

    Claude Code writes session JSONL under ~/.claude/projects/<dashed-cwd>/;
    reuse the audit's resolver so the rig and `cram audit` agree on where
    transcripts live, then take the most recently modified one.
    """
    from cram.audit import _project_transcript_dir
    td = _project_transcript_dir(workdir)
    if not td:
        return None
    files = glob.glob(os.path.join(td, '*.jsonl'))
    return max(files, key=os.path.getmtime) if files else None


class LiveRunner:
    """Runs a coding agent on the task via Claude Code headless mode (`claude -p`).

    This targets the surface cram already benchmarks: Claude Code runs the task
    non-interactively and writes a JSONL transcript under ~/.claude/projects/,
    which run() locates and hands back for measurement with the same parser
    `cram audit` uses.

    No ANTHROPIC_API_KEY required — `claude -p` reuses your existing Claude Code
    login (a raw API key is only one of several auth paths it accepts, and is
    only needed for a login-less environment like CI). The one requirement is
    the `claude` CLI on PATH; available() reports it. Pass a different
    agent_cmd to drive another headless agent.
    """
    def __init__(self, agent_cmd: tuple[str, ...] = ('claude', '-p'),
                 timeout: int = 900):
        self.agent_cmd = tuple(agent_cmd)
        self.timeout = timeout

    def available(self) -> Availability:
        exe = self.agent_cmd[0]
        if shutil.which(exe) is None:
            return Availability(False, f'{exe} CLI not on PATH — install Claude '
                                       f'Code (or pass a different agent_cmd)')
        return Availability(True)

    def run(self, task: Task, setup: dict, workdir: str) -> str | None:
        av = self.available()
        if not av.ok:
            raise RuntimeError(av.reason)
        env = {**os.environ, **{k: str(v) for k, v in (setup or {}).items()}}
        subprocess.run([*self.agent_cmd, task.prompt], cwd=workdir, env=env,
                       capture_output=True, timeout=self.timeout)
        return _newest_transcript_for(workdir)


class MockRunner:
    """Deterministic runner for framework tests and --dry-run plumbing.

    transcript_for(task, setup, workdir) returns a transcript path (write the
    canned JSONL yourself). If it also mutates workdir so the oracle passes,
    the success path is exercised end-to-end without a live agent.
    """
    def __init__(self, transcript_for: Callable[[Task, dict, str], str | None]):
        self._fn = transcript_for

    def run(self, task: Task, setup: dict, workdir: str) -> str | None:
        return self._fn(task, setup, workdir)


@runtime_checkable
class Oracle(Protocol):
    def score(self, task: Task, workdir: str) -> bool: ...


class CommandOracle:
    """Success = the task's check command exits 0 in the workdir."""
    def __init__(self, timeout: int = 300):
        self.timeout = timeout

    def score(self, task: Task, workdir: str) -> bool:
        if not task.check:
            return True
        try:
            r = subprocess.run(task.check, cwd=workdir,
                               capture_output=True, timeout=self.timeout)
        except Exception:
            return False
        return r.returncode == 0


# ── Measurement (reuses the audit timeline) ─────────────────────────────────

def effective_tokens(transcript_path: str, *, provider: str | None = None,
                     big_result_bytes: int = 20_000) -> float:
    """Effective input-token cost of a transcript, cache traffic weighted.

    eff = Σ_requests (input + cache_write·write_mult + cache_read·read_mult),
    using the pricing multipliers for `provider` (default: $CRAM_PROVIDER).
    Reuses derive_session_timeline so this matches `cram audit --session`.
    Returns 0.0 for an unparseable or usage-free transcript.
    """
    parsed = audit_events.parse_claude(transcript_path)
    if parsed is None:
        return 0.0
    meta, events = parsed
    tl = audit_events.derive_session_timeline(meta, events,
                                              big_result_bytes=big_result_bytes)
    if tl is None:
        return 0.0
    p = get_provider_pricing(provider)
    wr, rd = p['cache_write_mult'], p['cache_read_mult']
    return sum(r['input'] + r['cache_write'] * wr + r['cache_read'] * rd
               for r in tl['rows'])


# ── Detection + observational A/B ────────────────────────────────────────────
# The verify-loop seam: decide whether an optimizer was active in a session, so
# real transcripts can be split optimizer-on vs optimizer-off without a
# controlled rig run. Generalizes audit_events._is_context_tool (which only
# knows context-mode's ctx_*) to any optimizer that declares a `detector`.

def _match_detector(tool_name: str | None, detector: dict) -> bool:
    """True if a tool name matches a detector signature.

    detector = {'kind': 'mcp_tool', 'match': 'claude-context'} matches Claude
    Code's MCP tool names ('mcp__claude-context__search_code') by substring on
    the full name or exact/prefix on the leaf after the last '__'. kind 'tool'
    is a plain substring match on the bare name.
    """
    if not tool_name:
        return False
    match = detector.get('match', '')
    if not match:
        return False
    if detector.get('kind') == 'mcp_tool':
        leaf = tool_name.rsplit('__', 1)[-1]
        return match in tool_name or leaf == match or leaf.startswith(match)
    return match in tool_name


def optimizer_active(events: list, detector: dict) -> bool:
    """Did any tool call in this session match the optimizer's detector?"""
    return any(_match_detector(getattr(ev, 'tool', None), detector) for ev in events)


def _detector_of(optimizer) -> dict:
    """Pull a detector dict off an adapter instance, class, or name."""
    if isinstance(optimizer, dict):
        return optimizer
    if isinstance(optimizer, str):
        det = getattr(get_provider(optimizer), 'detector', None)
        if det is None:
            raise ValueError(f'optimizer {optimizer!r} has no detector signature')
        return det
    det = getattr(optimizer, 'detector', None)
    if det is None:
        raise ValueError('optimizer has no detector signature')
    return det


def observe_optimizer(repo_root: str, optimizer, *, days: int = 30,
                      provider: str | None = None) -> dict | None:
    """Observational A/B over this repo's real transcripts: optimizer on vs off.

    For a developer already using an optimizer (e.g. claude-context), this asks
    "did your sessions that used it actually cost fewer tokens / read fewer
    files than the ones that didn't?" — without a controlled run. It re-parses
    the repo's Claude Code transcripts, tags each session by whether the
    optimizer's detector fired, and compares effective tokens and
    reads-before-edit across the two groups.

    Self-contained: it does not touch derive_session's pinned output (the parity
    suite stays green) — it derives its own metrics from the event stream.

    Returns None when there are no transcripts. Note it's *observational* — the
    groups aren't matched on task difficulty, so treat it as a signal, not proof
    (the controlled rig is the proof).
    """
    import glob as _glob
    import datetime as _dt
    from cram.audit import _project_transcript_dir

    td = _project_transcript_dir(repo_root)
    if not td:
        return None
    detector = _detector_of(optimizer)
    cutoff = _dt.datetime.now() - _dt.timedelta(days=days)

    on: list[dict] = []
    off: list[dict] = []
    for path in _glob.glob(os.path.join(td, '*.jsonl')):
        try:
            if _dt.datetime.fromtimestamp(os.path.getmtime(path)) < cutoff:
                continue
        except OSError:
            continue
        parsed = audit_events.parse_claude(path)
        if parsed is None:
            continue
        meta, events = parsed
        sess = audit_events.derive_session(meta, events, big_result_bytes=20_000)
        if sess is None:
            continue
        rec = {
            'reads_before_edit': sess['reads_before_edit'],
            'eff_tokens':        effective_tokens(path, provider=provider),
        }
        (on if optimizer_active(events, detector) else off).append(rec)

    if not on and not off:
        return None

    def _agg(group: list[dict]) -> dict:
        n = len(group)
        return {
            'sessions':                n,
            'avg_reads_before_edit':   (sum(g['reads_before_edit'] for g in group) / n) if n else None,
            'avg_eff_tokens':          (sum(g['eff_tokens'] for g in group) / n) if n else None,
        }

    return {'days': days, 'detector': detector,
            'on': _agg(on), 'off': _agg(off)}


def render_observation(obs: dict, name: str) -> str:
    """Human-readable optimizer-on vs -off table from observe_optimizer()."""
    on, off = obs['on'], obs['off']
    lines = ['', f'Observational A/B — {name}  (last {obs["days"]} days)', '']
    lines.append(f"  {'Metric':<28}{'with ' + name:>16}{'without':>14}{'Δ%':>9}")
    lines.append(f"  {'-' * 67}")

    def _row(label, key, lower_is_better=True, fmt='{:,.0f}'):
        a, b = on[key], off[key]
        if a is None or b is None:
            return f"  {label:<28}{(fmt.format(a) if a is not None else '—'):>16}" \
                   f"{(fmt.format(b) if b is not None else '—'):>14}{'—':>9}"
        delta = (a - b) / b * 100 if b else 0.0
        return f"  {label:<28}{fmt.format(a):>16}{fmt.format(b):>14}{delta:>+8.0f}%"

    lines.append(_row('Sessions', 'sessions', fmt='{:.0f}'))
    lines.append(_row('Avg reads before edit', 'avg_reads_before_edit'))
    lines.append(_row('Avg effective tokens', 'avg_eff_tokens'))
    lines.append('')
    if not on['sessions'] or not off['sessions']:
        lines.append(f"  ⚠ only one side has sessions — need both {name}-on and "
                     f"-off sessions to compare.")
    else:
        lines.append('  Negative Δ% on tokens/reads = the optimizer looks like it '
                     'helped. Observational, not controlled —')
        lines.append('  groups are not matched on task difficulty. Use `cram rig '
                     '<corpus>` for a controlled, quality-gated test.')
    lines.append('')
    return '\n'.join(lines)


# ── Run loop ────────────────────────────────────────────────────────────────

@dataclasses.dataclass(slots=True)
class RunResult:
    task_id: str
    provider: str
    success: bool = False
    eff_tokens: float = 0.0
    skipped: bool = False
    reason: str = ''


def _prepare_workdir(task: Task, root: str) -> str:
    wd = os.path.join(root, task.id)
    if task.fixture:
        shutil.copytree(task.fixture, wd)
    else:
        os.makedirs(wd, exist_ok=True)
    return wd


def run_rig(corpus: list[Task], providers: list[ProviderAdapter],
            runner: Runner, oracle: Oracle, *, work_root: str,
            measure: Callable[[str], float] = effective_tokens) -> list[RunResult]:
    """Run every (task × provider) and return one RunResult each.

    Unavailable providers are recorded as skipped (with the availability reason)
    rather than dropped, so the grid in the report is always complete.
    """
    results: list[RunResult] = []
    for task in corpus:
        for prov in providers:
            av = prov.availability()
            if not av.ok:
                results.append(RunResult(task.id, prov.name, skipped=True,
                                         reason=av.reason))
                continue
            wd = _prepare_workdir(task, os.path.join(work_root, prov.name))
            try:
                setup = prov.setup(task, wd)
                transcript = runner.run(task, setup, wd)
                success = oracle.score(task, wd)
                eff = measure(transcript) if transcript else 0.0
                results.append(RunResult(task.id, prov.name, success=success,
                                         eff_tokens=eff))
            except Exception as e:  # a provider/runner failure is a data point
                results.append(RunResult(task.id, prov.name, skipped=True,
                                         reason=f'run error: {e}'))
    return results


# ── Reporting ───────────────────────────────────────────────────────────────

def summarize(results: list[RunResult]) -> dict:
    """Per-provider: success rate and mean effective tokens over *passed* runs.

    Token cost is averaged only over successful runs — that's the
    tokens-at-fixed-success comparison; a provider that fails the task can't
    claim its (smaller) token count as a saving.
    """
    by_provider: dict[str, list[RunResult]] = {}
    for r in results:
        by_provider.setdefault(r.provider, []).append(r)

    out: dict[str, dict] = {}
    for prov, rs in by_provider.items():
        ran = [r for r in rs if not r.skipped]
        passed = [r for r in ran if r.success]
        out[prov] = {
            'tasks':         len(rs),
            'ran':           len(ran),
            'skipped':       len(rs) - len(ran),
            'passed':        len(passed),
            'success_rate':  (len(passed) / len(ran)) if ran else None,
            'mean_eff_tokens_passed': (sum(r.eff_tokens for r in passed) / len(passed))
                                      if passed else None,
            'skip_reason':   next((r.reason for r in rs if r.skipped), ''),
        }
    return {'providers': out, 'results': [dataclasses.asdict(r) for r in results]}


def render_summary(summary: dict, *, baseline: str = 'baseline') -> str:
    """Human-readable grid: success rate + tokens-at-fixed-success vs baseline."""
    provs = summary['providers']
    base = provs.get(baseline, {})
    base_tok = base.get('mean_eff_tokens_passed')

    lines = ['', 'cram rig — tokens at fixed success', '']
    lines.append(f"  {'Provider':<14}{'Pass':>10}{'Success':>10}"
                 f"{'Eff tokens':>14}{'vs base':>10}")
    lines.append(f"  {'-' * 58}")
    for name, s in provs.items():
        if s['ran'] == 0:
            lines.append(f"  {name:<14}{'—':>10}{'skipped':>10}{'':>14}{'':>10}"
                         f"   {s['skip_reason']}")
            continue
        rate = f"{s['success_rate']:.0%}" if s['success_rate'] is not None else '—'
        tok = s['mean_eff_tokens_passed']
        tokstr = f"{tok:,.0f}" if tok is not None else '—'
        if tok is not None and base_tok:
            delta = (tok - base_tok) / base_tok
            vs = f"{delta:+.0%}"
        else:
            vs = '—'
        lines.append(f"  {name:<14}{s['passed']:>4}/{s['ran']:<5}{rate:>10}"
                     f"{tokstr:>14}{vs:>10}")
    lines.append('')
    lines.append('  Eff tokens = input + cache_write·1.25 + cache_read·0.10, '
                 'averaged over passed runs only.')
    lines.append('  Negative vs-base = cheaper at equal success. Compare only '
                 'providers that actually ran.')
    lines.append('')
    return '\n'.join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────

def _dry_run(corpus: list[Task], providers: list[ProviderAdapter]) -> None:
    print(f"\ncram rig — dry run  ({len(corpus)} tasks × {len(providers)} providers)\n")
    print("  Providers:")
    for prov in providers:
        av = prov.availability()
        mark = '✓' if av.ok else '✗'
        note = '' if av.ok else f'  — {av.reason}'
        print(f"    {mark} {prov.name}{note}")
    print("\n  Tasks:")
    for t in corpus:
        chk = ' '.join(t.check) if t.check else '(none)'
        print(f"    {t.id:<24} check: {chk}")
    runnable = [p.name for p in providers if p.availability().ok]
    print(f"\n  Would run {len(corpus) * len(runnable)} (task × available-provider) "
          f"cells: {', '.join(runnable) or 'none'}.")
    rav = LiveRunner().available()
    runner_note = ('the Claude Code CLI (`claude -p`, reuses your existing login '
                   '— no API key)' if not rav.ok else
                   '`claude -p` (Claude Code login already detected)')
    print(f"  Live runs use {runner_note}.\n")


def _run_observe(optimizer: str, repo_root: str, days: int, as_json: bool) -> None:
    """`cram rig --observe <optimizer>` — observational A/B over real sessions."""
    try:
        obs = observe_optimizer(repo_root, optimizer, days=days)
    except ValueError as e:
        print(f'Error: {e}', file=sys.stderr)
        raise SystemExit(2)
    if obs is None:
        print(f'No transcripts found for this repo in the last {days} days.')
        return
    if as_json:
        print(json.dumps(obs, indent=2))
    else:
        print(render_observation(obs, optimizer))


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog='cram rig',
        description='Verify context optimizers two ways: a controlled benchmark '
                    '(tokens at fixed success over a corpus) or --observe '
                    '(A/B an optimizer over your real sessions).')
    parser.add_argument('corpus', nargs='?',
                        help='path to a corpus JSON file (controlled mode)')
    parser.add_argument('--observe', metavar='OPTIMIZER', default=None,
                        help='observational mode: A/B this optimizer '
                             '(e.g. claude-context) over real transcripts')
    parser.add_argument('--days', type=int, default=30,
                        help='observational look-back window (default: 30)')
    parser.add_argument('--path', default=None, metavar='REPO_PATH',
                        help='repo root for observational mode (default: cwd)')
    parser.add_argument('--providers', default='baseline,cram',
                        help='comma-separated provider names '
                             '(default: baseline,cram)')
    parser.add_argument('--dry-run', action='store_true',
                        help='resolve the grid + availability without running')
    parser.add_argument('--work-root', default=None,
                        help='directory for per-run workdirs (default: a tempdir)')
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args()

    # ── Observational mode ──────────────────────────────────────────────────
    if args.observe:
        from cram.utils import find_git_root
        start = os.path.abspath(args.path) if args.path else os.getcwd()
        try:
            repo_root = find_git_root(start)
        except Exception:
            repo_root = start
        _run_observe(args.observe, repo_root, args.days, args.as_json)
        return

    # ── Controlled mode ─────────────────────────────────────────────────────
    if not args.corpus:
        parser.error('a corpus path is required for controlled mode '
                     '(or use --observe <optimizer>)')

    corpus = load_corpus(args.corpus)
    try:
        providers = [get_provider(n.strip()) for n in args.providers.split(',') if n.strip()]
    except KeyError as e:
        parser.error(str(e))

    if args.dry_run:
        _dry_run(corpus, providers)
        return

    runner = LiveRunner()
    rav = runner.available()
    if not rav.ok:
        parser.error(f'{rav.reason}. Run with --dry-run to resolve the grid '
                     f'without executing.')

    import tempfile
    work_root = args.work_root or tempfile.mkdtemp(prefix='cram-rig-')
    results = run_rig(corpus, providers, runner, CommandOracle(),
                      work_root=work_root)
    summary = summarize(results)
    if args.as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(render_summary(summary))


if __name__ == '__main__':
    main()
