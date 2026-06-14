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


_BUILTIN_PROVIDERS: dict[str, Callable[[], ProviderAdapter]] = {
    'baseline':     BaselineAdapter,
    'cram':         CramAdapter,
    'headroom':     HeadroomAdapter,
    'context-mode': ContextModeAdapter,
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


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog='cram rig',
        description='Controlled benchmark: tokens at fixed success across '
                    'context providers (baseline, cram, headroom, context-mode)')
    parser.add_argument('corpus', help='path to a corpus JSON file')
    parser.add_argument('--providers', default='baseline,cram',
                        help='comma-separated provider names '
                             '(default: baseline,cram)')
    parser.add_argument('--dry-run', action='store_true',
                        help='resolve the grid + availability without running')
    parser.add_argument('--work-root', default=None,
                        help='directory for per-run workdirs (default: a tempdir)')
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args()

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
