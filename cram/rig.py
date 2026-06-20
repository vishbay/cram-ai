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
themselves unavailable (those tools aren't installable from here yet). The live
runners drive either Claude Code headless (`claude -p`) or Codex noninteractive
mode (`codex exec`) and reuse the user's existing login. `cram rig --dry-run`
works today: it resolves the corpus × providers grid and reports availability
without running.
"""

from __future__ import annotations

import dataclasses
import glob
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from typing import Callable, Protocol, runtime_checkable

from cram import audit_events
from cram.cost_model import get_provider_pricing


# ── Corpus ──────────────────────────────────────────────────────────────────

@dataclasses.dataclass(slots=True)
class Task:
    """One benchmark task: a prompt, a code source, and a success oracle.

    The workdir is populated from exactly one source:
      fixture: a local directory copied in (a self-contained fixture); or
      repo + ref: a git URL cloned and checked out at `ref` (a real repo at a
               pinned SHA — the "real-repo" tier, no vendoring needed).
    overlay: a local directory whose files are copied in *after* the source —
             e.g. a clean-room regression test that's the oracle (so we don't
             vendor the upstream repo's tests).
    check:   argv list run in the workdir after the agent finishes; exit 0 means
             success. Empty → the task always "succeeds" (token-only).
    env:     extra environment for the check (e.g. {"PYTHONPATH": "src"} so a
             cloned, not-installed library imports).
    """
    id: str
    prompt: str
    fixture: str | None = None
    check: list[str] = dataclasses.field(default_factory=list)
    tier: str | None = None
    repo: str | None = None
    ref: str | None = None
    overlay: str | None = None
    env: dict = dataclasses.field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> 'Task':
        if 'id' not in d or 'prompt' not in d:
            raise ValueError("task needs 'id' and 'prompt'")
        check = d.get('check') or []
        if isinstance(check, str):
            check = check.split()
        tier = d.get('tier')
        return cls(id=str(d['id']), prompt=str(d['prompt']),
                   fixture=d.get('fixture'), check=list(check),
                   tier=str(tier) if tier is not None else None,
                   repo=d.get('repo'), ref=d.get('ref'),
                   overlay=d.get('overlay'), env=dict(d.get('env') or {}))


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
        if t.overlay and not os.path.isabs(t.overlay):
            t.overlay = os.path.normpath(os.path.join(base, t.overlay))
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

    `detector` lets `cram rig --observe cram` A/B a user's own sessions by
    whether the context layer was used — it matches cram's MCP tool
    ('mcp__cram-ai__get_context'). Note this only sees the MCP delivery path;
    file-based delivery (CLAUDE.md injection) leaves no tool-call signature, so
    use `cram audit --compare` to A/B that.
    """
    name = 'cram'
    detector = {'kind': 'mcp_tool', 'match': 'get_context'}

    def __init__(self, target: str | None = None):
        self.target = target

    def availability(self) -> Availability:
        if shutil.which('cram') is None:
            return Availability(False, 'cram CLI not on PATH')
        return Availability(True)

    def _write_existing_context(self, workdir: str) -> bool:
        """Best-effort fallback: write already-generated context to a target file."""
        if not self.target:
            return False
        from cram import targets as _targets
        from cram.context_dir import context_path, has_context_dir

        if not has_context_dir(workdir):
            return False
        task_path = context_path(workdir, 'CURRENT_TASK.md', warn=True)
        arch_path = context_path(workdir, 'ARCHITECTURE.md', warn=True)
        if not os.path.exists(task_path):
            return False
        try:
            with open(task_path, errors='ignore') as f:
                task_content = f.read()
            with open(arch_path, errors='ignore') as f:
                arch_content = f.read()
            _targets.write_to_target(workdir, self.target, task_content, arch_content)
            return True
        except OSError:
            return False

    def setup(self, task: Task, workdir: str) -> dict:
        from cram.context_dir import has_context_dir

        # Bootstrap the context layer if the repo isn't already cram-initialized
        # (e.g. a fresh benchmark fixture). Without this `cram task` has no
        # .ai-context to populate and cram silently degrades to baseline — so
        # cram could never actually compete on the benchmark.
        initialized = has_context_dir(workdir)
        if not initialized:
            try:
                init = subprocess.run(['cram', 'init'], cwd=workdir,
                                      capture_output=True, timeout=300)
                initialized = init.returncode == 0
            except Exception:
                initialized = False

        cmd = ['cram', 'task', task.prompt]
        if self.target:
            cmd.extend(['--target', self.target])
        fallback = False
        try:
            result = subprocess.run(cmd, cwd=workdir,
                                    capture_output=True, timeout=180)
            if result.returncode != 0:
                fallback = self._write_existing_context(workdir)
        except Exception:
            fallback = self._write_existing_context(workdir)
        setup = {'CRAM_REPO': workdir}
        if initialized:
            setup['CRAM_INITIALIZED'] = '1'
        if fallback:
            setup['CRAM_CONTEXT_FALLBACK'] = 'existing'
        return setup


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


# Which startup file each runner reads context from, when an optimizer pre-packs
# context into the repo (CommandAdapter). Other targets need no special handling.
_RUNNER_STARTUP_FILE = {'claude': 'CLAUDE.md', 'codex': 'AGENTS.md'}


class CommandAdapter:
    """Generic optimizer: run a command that emits repo context on stdout, then
    write that into the agent's startup file before the run.

    This lets any context-packer be refereed with no cram-specific integration —
    e.g. repomix (`npx -y repomix --stdout`) or files-to-prompt. setup() is
    best-effort: if the tool is missing or fails, the arm degrades to baseline
    (no context written) rather than crashing the run, so a broken optimizer
    shows up as "no savings", not a benchmark hole.
    """

    def __init__(self, name: str, command, *, target: str = 'claude',
                 header: str | None = None):
        self.name = name
        self.command = command if isinstance(command, list) else command.split()
        self.target = target
        self.header = header

    @property
    def launcher(self) -> str:
        return self.command[0]

    def availability(self) -> Availability:
        if shutil.which(self.launcher) is None:
            return Availability(False, f'{self.launcher} not on PATH — needed to '
                                       f'run the {self.name} optimizer')
        return Availability(True)

    def setup(self, task: Task, workdir: str) -> dict:
        try:
            result = subprocess.run(self.command, cwd=workdir, capture_output=True,
                                    text=True, timeout=300)
        except Exception:
            return {}                      # degrade to baseline
        context = (result.stdout or '').strip()
        if result.returncode != 0 or not context:
            return {}
        startup = _RUNNER_STARTUP_FILE.get(self.target, 'CLAUDE.md')
        body = f'{self.header}\n\n{context}\n' if self.header else context + '\n'
        try:
            path = os.path.join(workdir, startup)
            with open(path, 'a' if os.path.exists(path) else 'w') as f:
                f.write(('\n' if os.path.exists(path) else '') + body)
        except OSError:
            return {}
        return {'CRAM_OPTIMIZER': self.name}


# Presets for low-setup, key-free third-party optimizers (require only their CLI
# / npx; no API keys). Construct via get_provider('repomix') etc.
def _repomix() -> CommandAdapter:
    return CommandAdapter('repomix', 'npx -y repomix --stdout',
                          header='# Repo context (packed by repomix)')


def _files_to_prompt() -> CommandAdapter:
    return CommandAdapter('files-to-prompt', 'files-to-prompt .',
                          header='# Repo context (files-to-prompt)')


_BUILTIN_PROVIDERS: dict[str, Callable[[], ProviderAdapter]] = {
    'baseline':        BaselineAdapter,
    'cram':            CramAdapter,
    'headroom':        HeadroomAdapter,
    'context-mode':    ContextModeAdapter,
    'claude-context':  ClaudeContextAdapter,
    'repomix':         _repomix,
    'files-to-prompt': _files_to_prompt,
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


def _newest_codex_transcript_for(workdir: str, *, since: float = 0.0) -> str | None:
    """Newest Codex transcript whose session cwd matches workdir, or None.

    Codex stores sessions globally under ~/.codex/sessions/YYYY/MM/DD. Unlike
    Claude Code there is no project-specific directory, so scan recent JSONL
    files and use parse_codex's SessionMeta.cwd to find this disposable workdir.
    """
    from cram.audit import _codex_sessions_dir
    sd = _codex_sessions_dir()
    if not sd:
        return None
    root = os.path.realpath(os.path.abspath(workdir))
    matches: list[str] = []
    for path in glob.glob(os.path.join(sd, '**', '*.jsonl'), recursive=True):
        try:
            if os.path.getmtime(path) < since:
                continue
        except OSError:
            continue
        parsed = audit_events.parse_codex(path)
        if parsed is None:
            continue
        meta, _events = parsed
        if meta.cwd and os.path.realpath(os.path.abspath(meta.cwd)) == root:
            matches.append(path)
    return max(matches, key=os.path.getmtime) if matches else None


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
    def __init__(self, agent_cmd: tuple[str, ...] = (
                     'claude', '-p', '--dangerously-skip-permissions'),
                 timeout: int = 900):
        # --dangerously-skip-permissions is required for a headless run to
        # actually edit files / run the oracle command: without it `claude -p`
        # makes no changes and every arm fails with zero work. The rig runs in
        # throwaway per-task workdirs (copied fixtures), so granting autonomy
        # there is safe — the agent can't touch anything outside the copy.
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


class CodexRunner:
    """Runs a task through Codex CLI noninteractive mode (`codex exec`)."""
    def __init__(
        self,
        agent_cmd: tuple[str, ...] = (
            'codex', 'exec',
            '--sandbox', 'workspace-write',
            '--skip-git-repo-check',
        ),
        timeout: int = 900,
    ):
        self.agent_cmd = tuple(agent_cmd)
        self.timeout = timeout

    def available(self) -> Availability:
        exe = self.agent_cmd[0]
        if shutil.which(exe) is None:
            return Availability(False, f'{exe} CLI not on PATH — install Codex '
                                       f'or add Codex.app/Contents/Resources/codex')
        return Availability(True)

    def run(self, task: Task, setup: dict, workdir: str) -> str | None:
        av = self.available()
        if not av.ok:
            raise RuntimeError(av.reason)
        env = {**os.environ, **{k: str(v) for k, v in (setup or {}).items()}}
        start = time.time()
        subprocess.run([*self.agent_cmd, '--cd', workdir, task.prompt],
                       cwd=workdir, env=env, capture_output=True,
                       timeout=self.timeout)
        return _newest_codex_transcript_for(workdir, since=start)


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


class OracleTimeout(Exception):
    """The success check exceeded its timeout — infra noise, not a task failure."""


class CommandOracle:
    """Success = the task's check command exits 0 in the workdir."""
    def __init__(self, timeout: int = 300):
        self.timeout = timeout

    def score(self, task: Task, workdir: str) -> bool:
        if not task.check:
            return True
        env = {**os.environ, **{k: str(v) for k, v in (task.env or {}).items()}}
        try:
            r = subprocess.run(task.check, cwd=workdir, env=env,
                               capture_output=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as e:
            # A timeout is not a task failure — surface it so the rig records it
            # as oracle_timeout (excluded from the success rate) rather than a loss.
            raise OracleTimeout(f'check timed out after {self.timeout}s') from e
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
    if f'{os.sep}.codex{os.sep}sessions{os.sep}' in transcript_path:
        parsed = audit_events.parse_codex(transcript_path)
    else:
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

# A cell's outcome. Distinguishing these is what stops the rig from silently
# miscounting: a run error, an oracle timeout, and a missing transcript used to
# all look like a skip or a 0-token "success".
#   ran           — executed, oracle gave a verdict, tokens measured
#   no_transcript — executed, oracle gave a verdict, but tokens unmeasurable
#   unavailable   — provider's availability gate failed (never ran)
#   setup_error   — workdir/clone or provider.setup() raised
#   run_error     — the agent runner raised
#   oracle_timeout— the success check timed out (infra noise, not a task failure)
_COUNTED_STATUSES = ('ran', 'no_transcript')   # produced a real success/fail verdict


@dataclasses.dataclass(slots=True)
class RunResult:
    task_id: str
    provider: str
    success: bool = False
    eff_tokens: float = 0.0
    status: str = 'ran'
    measured: bool = True          # False when the run produced no measurable tokens
    reason: str = ''
    rep: int = 0

    @property
    def skipped(self) -> bool:
        """Back-compat: a cell that did not produce a success/fail verdict."""
        return self.status not in _COUNTED_STATUSES


# Copytree must never carry stale bytecode into a run — a fixture's committed
# __pycache__ could shadow the agent's edit and make the oracle pass/fail on
# the wrong code.
_FIXTURE_IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache')


# Cache of full repo clones (URL → local path), so the many per-cell workdirs
# clone cheaply with `--shared` from a single fetch instead of hitting the
# network N times. Lives in the system temp dir; not measured/audited.
_REPO_CACHE: dict[str, str] = {}


def _repo_cache_dir() -> str:
    import tempfile
    return os.path.join(tempfile.gettempdir(), 'cram-rig-repocache')


def clean_repo_cache() -> str:
    """Remove the shared clone cache. Returns the path that was cleaned."""
    d = _repo_cache_dir()
    shutil.rmtree(d, ignore_errors=True)
    _REPO_CACHE.clear()
    return d


def _repo_cache(repo: str) -> str:
    cached = _REPO_CACHE.get(repo)
    if cached and os.path.isdir(cached):
        return cached
    import hashlib
    base = _repo_cache_dir()
    os.makedirs(base, exist_ok=True)
    dest = os.path.join(base, hashlib.sha1(repo.encode()).hexdigest())
    if not os.path.isdir(dest):
        # One retry on transient clone failure (network) before giving up, so a
        # blip fails a single cell (caught upstream), never the whole campaign.
        for attempt in range(2):
            try:
                subprocess.run(['git', 'clone', '--quiet', repo, dest],
                               check=True, timeout=600)
                break
            except Exception:
                shutil.rmtree(dest, ignore_errors=True)
                if attempt == 1:
                    raise
                time.sleep(2)
    _REPO_CACHE[repo] = dest
    return dest


def _git_init(wd: str) -> None:
    try:
        subprocess.run(['git', 'init', '-q'], cwd=wd, capture_output=True, timeout=30)
    except Exception:
        pass


def _prepare_workdir(task: Task, wd: str) -> str:
    """Create one run's working directory at `wd` from the task's code source.

    Source is a local fixture (copied) or a git repo (cloned at `ref`); an
    optional overlay (e.g. the clean-room oracle test) is copied in afterwards.
    The workdir is a git repo either way — cram (find_git_root) and many agents
    expect a git root, and all arms get the same realistic environment.
    """
    if task.repo:
        # Clone cheaply from the shared cache, then pin to ref.
        cache = _repo_cache(task.repo)
        subprocess.run(['git', 'clone', '--quiet', '--shared', cache, wd],
                       check=True, timeout=120)
        if task.ref:
            subprocess.run(['git', '-C', wd, 'checkout', '--quiet', task.ref],
                           check=True, timeout=60)
    elif task.fixture:
        shutil.copytree(task.fixture, wd, ignore=_FIXTURE_IGNORE)
        _git_init(wd)
    else:
        os.makedirs(wd, exist_ok=True)
        _git_init(wd)
    if task.overlay:
        shutil.copytree(task.overlay, wd, ignore=_FIXTURE_IGNORE, dirs_exist_ok=True)
    return wd


def _progress(on: bool, msg: str) -> None:
    if on:
        print(f'  · {msg}', file=sys.stderr, flush=True)


def run_rig(corpus: list[Task], providers: list[ProviderAdapter],
            runner: Runner, oracle: Oracle, *, work_root: str,
            measure: Callable[[str], float] = effective_tokens,
            repeats: int = 1, progress: bool = False) -> list[RunResult]:
    """Run every (task × provider), `repeats` times each, and return RunResults.

    Each stage (workdir prep, provider setup, runner, oracle, measurement) sets a
    precise `status` so a failure is never miscounted as a skip or a 0-token
    success. Unavailable providers are recorded once. `repeats > 1` runs each cell
    N times in isolated workdirs so `summarize` can report variance.
    """
    results: list[RunResult] = []

    def record(rr: RunResult) -> None:
        results.append(rr)
        tag = rr.status if rr.status in _COUNTED_STATUSES else f'!{rr.status}'
        tok = f'{rr.eff_tokens:,.0f}t' if rr.measured else '—'
        _progress(progress, f'{rr.task_id}/{rr.provider}'
                            f'{"" if repeats == 1 else f"#{rr.rep}"}: '
                            f'{"pass" if rr.success else "fail"} {tag} {tok}')

    for task in corpus:
        for prov in providers:
            av = prov.availability()
            if not av.ok:
                record(RunResult(task.id, prov.name, status='unavailable',
                                 measured=False, reason=av.reason))
                continue
            for rep in range(repeats):
                # Keep the leaf to [A-Za-z0-9-] (+ '/'): Claude Code dashes any
                # other char (e.g. '#') when naming its transcript dir, but
                # cram's resolver only dashes '/', so a '#rep' suffix made the
                # transcript unfindable and every repeat measured 0 tokens.
                leaf = task.id if repeats == 1 else f'{task.id}-rep{rep}'
                wd_path = os.path.join(work_root, prov.name, leaf)
                try:
                    wd = _prepare_workdir(task, wd_path)
                except Exception as e:
                    record(RunResult(task.id, prov.name, status='setup_error',
                                     measured=False, reason=f'workdir/clone: {e}', rep=rep))
                    continue
                try:
                    setup = prov.setup(task, wd)
                except Exception as e:
                    record(RunResult(task.id, prov.name, status='setup_error',
                                     measured=False, reason=f'provider setup: {e}', rep=rep))
                    continue
                try:
                    transcript = runner.run(task, setup, wd)
                except Exception as e:
                    record(RunResult(task.id, prov.name, status='run_error',
                                     measured=False, reason=f'runner: {e}', rep=rep))
                    continue
                try:
                    success = oracle.score(task, wd)
                except OracleTimeout as e:
                    record(RunResult(task.id, prov.name, status='oracle_timeout',
                                     measured=False, reason=str(e), rep=rep))
                    continue

                eff = measure(transcript) if transcript else 0.0
                if transcript and eff > 0:
                    record(RunResult(task.id, prov.name, success=success,
                                     eff_tokens=eff, status='ran', measured=True, rep=rep))
                else:
                    # Oracle gave a verdict, but we couldn't measure tokens (no
                    # transcript found, or it had no usage). Count the verdict;
                    # don't average a phantom 0 into the provider's token cost.
                    record(RunResult(task.id, prov.name, success=success,
                                     eff_tokens=0.0, status='no_transcript', measured=False,
                                     reason='no measurable transcript', rep=rep))
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
        ran = [r for r in rs if not r.skipped]            # produced a verdict
        passed = [r for r in ran if r.success]
        # Tokens are averaged only over passed AND measured runs — never fold a
        # phantom 0 (unmeasured/no-transcript) into a provider's token cost.
        toks = [r.eff_tokens for r in passed if r.measured]
        # Failures by kind, so a run error / timeout is never read as a task loss.
        failures: dict[str, int] = {}
        for r in rs:
            if r.skipped:
                failures[r.status] = failures.get(r.status, 0) + 1
        out[prov] = {
            'tasks':         len(rs),
            'ran':           len(ran),
            'skipped':       len(rs) - len(ran),
            'passed':        len(passed),
            'success_rate':  (len(passed) / len(ran)) if ran else None,
            'mean_eff_tokens_passed': (sum(toks) / len(toks)) if toks else None,
            # Variance over the measured passed runs; meaningful when repeats > 1.
            'n_runs':        len(toks),
            'unmeasured':    len(passed) - len(toks),
            'eff_tokens_stdev': statistics.pstdev(toks) if len(toks) > 1 else 0.0,
            'failures':      failures,
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
        note = ''
        if s.get('unmeasured'):
            note = f"   ⚠ {s['unmeasured']} passed run(s) unmeasured"
        lines.append(f"  {name:<14}{s['passed']:>4}/{s['ran']:<5}{rate:>10}"
                     f"{tokstr:>14}{vs:>10}{note}")
    # Failure breakdown — so run errors / timeouts aren't misread as task losses.
    fail_rows = []
    for name, s in provs.items():
        fails = s.get('failures') or {}
        if fails:
            fail_rows.append(f"  {name}: " + ', '.join(f'{k}×{v}' for k, v in fails.items()))
    if fail_rows:
        lines.append('')
        lines.append('  Excluded (did not produce a verdict):')
        lines.extend(fail_rows)
    lines.append('')
    lines.append('  Eff tokens = input + cache_write·1.25 + cache_read·0.10, '
                 'averaged over passed + measured runs only.')
    lines.append('  Negative vs-base = cheaper at equal success. Compare only '
                 'providers that actually ran.')
    lines.append('')
    return '\n'.join(lines)


def _load_result(path: str) -> dict:
    """Load a committed rig result file.

    Accepts either a raw `summarize()` output ({providers, results}) or a
    wrapped {meta, summary} document. Returns {meta, summary}.
    """
    with open(path) as f:
        doc = json.load(f)
    if 'summary' in doc and 'providers' not in doc:
        return {'meta': doc.get('meta', {}), 'summary': doc['summary']}
    return {'meta': doc.get('meta', {}), 'summary': doc}


def render_leaderboard(result_files: list[str], *, baseline: str = 'baseline') -> str:
    """Markdown leaderboard across committed rig result files.

    Results are grouped by benchmark (`meta.name`) — a separate ranked table per
    corpus, because absolute token counts from different corpora aren't
    comparable. Within each table, rows are ranked by tokens-at-fixed-success:
    highest success first, then cheapest effective tokens — so a provider is
    never credited for a low token count it only got by failing the task.
    `vs base` is computed against the baseline provider within the same file.
    """
    groups: dict[str, list[dict]] = {}
    for path in result_files:
        doc = _load_result(path)
        meta = doc['meta']
        provs = doc['summary'].get('providers', {})
        base_tok = provs.get(baseline, {}).get('mean_eff_tokens_passed')
        bench = meta.get('name') or os.path.basename(path)
        label = meta.get('model') or os.path.basename(path)
        for name, s in provs.items():
            if not s.get('ran'):
                continue
            tok = s.get('mean_eff_tokens_passed')
            vs = ((tok - base_tok) / base_tok) if (tok is not None and base_tok) else None
            groups.setdefault(bench, []).append({
                'provider': name, 'label': label,
                'success': s.get('success_rate'),
                'tok': tok, 'stdev': s.get('eff_tokens_stdev', 0.0),
                'n': s.get('n_runs', s.get('passed', 0)), 'vs': vs,
            })

    out = ['', '# cram rig leaderboard', '']
    if not groups:
        out += ['_(no results)_', '']
        return '\n'.join(out)

    for bench in sorted(groups):
        rows = groups[bench]
        # tokens-at-fixed-success ordering: success desc, then cheaper tokens asc.
        rows.sort(key=lambda r: (-(r['success'] or 0.0),
                                 r['tok'] if r['tok'] is not None else float('inf')))
        out += [f'## {bench}', '',
                '| # | Provider | Model | Success | Eff tokens (±σ) | vs base | N |',
                '|--:|---|---|--:|--:|--:|--:|']
        for i, r in enumerate(rows, 1):
            succ = f"{r['success']:.0%}" if r['success'] is not None else '—'
            if r['tok'] is not None:
                tok = f"{r['tok']:,.0f} ±{r['stdev']:,.0f}" if r['stdev'] else f"{r['tok']:,.0f}"
            else:
                tok = '—'
            vs = f"{r['vs']:+.0%}" if r['vs'] is not None else '—'
            out.append(f"| {i} | {r['provider']} | {r['label']} | {succ} | {tok} | {vs} | {r['n']} |")
        out.append('')
    out += ['_Ranked by tokens at fixed success: higher success first, then cheaper. '
            'Token counts are comparable only within a benchmark + model; `vs base` is the '
            'within-run delta._', '']
    return '\n'.join(out)


# ── CLI ─────────────────────────────────────────────────────────────────────

def _pkg_version() -> str:
    try:
        import cram
        return cram.__version__
    except Exception:
        return 'unknown'


def _make_runner(name: str) -> Runner:
    if name == 'claude':
        return LiveRunner()
    if name == 'codex':
        return CodexRunner()
    raise ValueError(f'unknown runner {name!r}')


def _configure_providers_for_runner(
    providers: list[ProviderAdapter],
    runner_name: str,
) -> list[ProviderAdapter]:
    for provider in providers:
        # Inject context into the file the active runner actually reads
        # (CLAUDE.md for claude, AGENTS.md for codex). Without an explicit
        # target cram relies on default detection and may not inject for the
        # claude runner, silently degrading to baseline.
        if isinstance(provider, (CramAdapter, CommandAdapter)):
            provider.target = runner_name
    return providers


def _runner_availability(name: str) -> Availability:
    runner = _make_runner(name)
    available = getattr(runner, 'available', None)
    return available() if callable(available) else Availability(True)


def _dry_run(corpus: list[Task], providers: list[ProviderAdapter],
             runner_name: str) -> None:
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
    rav = _runner_availability(runner_name)
    runner_label = {
        'claude': '`claude -p`',
        'codex':  '`codex exec`',
    }[runner_name]
    status = 'detected' if rav.ok else f'unavailable — {rav.reason}'
    print(f"  Live runs use {runner_label} ({status}).\n")


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
    parser.add_argument('--runner', choices=('claude', 'codex'), default='claude',
                        help='headless coding agent for controlled mode '
                             '(default: claude)')
    parser.add_argument('--repeats', type=int, default=1, metavar='N',
                        help='run each (task × provider) cell N times for variance '
                             '(default: 1)')
    parser.add_argument('--tier', default=None, metavar='TIER',
                        help='only run tasks whose corpus "tier" matches '
                             '(e.g. small|medium|large)')
    parser.add_argument('--leaderboard', metavar='GLOB', default=None,
                        help='render a markdown leaderboard from committed result '
                             'JSON files matching GLOB, then exit')
    parser.add_argument('--clean-cache', action='store_true',
                        help='delete the shared repo clone cache, then exit')
    parser.add_argument('--keep-workdirs', action='store_true',
                        help='keep per-run workdirs for debugging (default: clean '
                             'an auto-created tempdir on success)')
    parser.add_argument('--no-baseline', action='store_true',
                        help='allow running without a baseline arm (not recommended '
                             '— vs-baseline is the comparable metric)')
    parser.add_argument('--model', default=None,
                        help='model label recorded in result metadata '
                             '(default: $CRAM_MODEL or "unknown")')
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args()

    # ── Cache maintenance ────────────────────────────────────────────────────
    if args.clean_cache:
        print(f'Cleaned repo cache: {clean_repo_cache()}')
        return

    # ── Leaderboard mode ────────────────────────────────────────────────────
    if args.leaderboard:
        files = sorted(glob.glob(args.leaderboard))
        if not files:
            print(f'No result files match {args.leaderboard!r}.', file=sys.stderr)
            raise SystemExit(1)
        print(render_leaderboard(files))
        return

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
    if args.tier:
        corpus = [t for t in corpus if t.tier == args.tier]
        if not corpus:
            parser.error(f'no tasks with tier {args.tier!r} in the corpus')
    if args.repeats < 1:
        parser.error('--repeats must be >= 1')
    provider_names = [n.strip() for n in args.providers.split(',') if n.strip()]
    if 'baseline' not in provider_names and not args.no_baseline:
        parser.error("no 'baseline' arm in --providers; vs-baseline is the "
                     "comparable metric. Add baseline, or pass --no-baseline.")
    try:
        providers = [get_provider(n) for n in provider_names]
    except KeyError as e:
        parser.error(str(e))
    providers = _configure_providers_for_runner(providers, args.runner)

    if args.dry_run:
        _dry_run(corpus, providers, args.runner)
        return

    runner = _make_runner(args.runner)
    rav = _runner_availability(args.runner)
    if not rav.ok:
        parser.error(f'{rav.reason}. Run with --dry-run to resolve the grid '
                     f'without executing.')

    import tempfile
    created_tmp = args.work_root is None
    work_root = args.work_root or tempfile.mkdtemp(prefix='cram-rig-')
    results = run_rig(corpus, providers, runner, CommandOracle(),
                      work_root=work_root, repeats=args.repeats,
                      progress=not args.as_json)
    summary = summarize(results)

    # Self-describing metadata so committed results are comparable + leaderboard-ready.
    import platform
    import datetime as _dt
    summary = {
        'meta': {
            'name':         os.path.splitext(os.path.basename(args.corpus))[0],
            'model':        args.model or os.environ.get('CRAM_MODEL', 'unknown'),
            'runner':       args.runner,
            'cram_version': _pkg_version(),
            'repeats':      args.repeats,
            'providers':    provider_names,
            'date':         _dt.date.today().isoformat(),
            'machine':      platform.node(),
        },
        **summary,
    }
    if args.as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(render_summary(summary))

    # Workdir hygiene: keep on request or if any cell errored (debugging);
    # otherwise clean an auto-created tempdir so /tmp doesn't accumulate.
    errored = any(r.status not in _COUNTED_STATUSES for r in results)
    if created_tmp and not args.keep_workdirs and not errored:
        shutil.rmtree(work_root, ignore_errors=True)
    else:
        print(f'  workdirs: {work_root}', file=sys.stderr)


if __name__ == '__main__':
    main()
