# Demo assets

Reproducible scripts behind the README GIFs.

## Referee demo (`docs/img/referee-demo.gif`)

A deterministic demonstration of the `cram rig` referee: two arms run over one real fixture
with scripted agents (no live model), where a cheap "aggressive-trim" arm saves tokens by
**failing** the task. The referee reports tokens at fixed success, so the broken arm is not
credited.

```bash
# Just the output (deterministic):
python scripts/demo/referee_demo.py

# Re-record the GIF (requires vhs: https://github.com/charmbracelet/vhs):
vhs scripts/demo/referee.tape
```

`referee_demo.py` uses cram's own `rig` primitives (`run_rig`, `CommandOracle`, `summarize`,
`render_summary`) with `MockRunner`, so it stays in lockstep with the real referee and needs no
API key.
