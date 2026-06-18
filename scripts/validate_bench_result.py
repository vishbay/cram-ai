#!/usr/bin/env python3
"""Validate a cram-bench result file before it joins the leaderboard.

A submission is a JSON file under examples/rig/bench/results/ — either a raw
`cram rig --json` summary or a wrapped {meta, summary} document. This enforces
the rules that keep the leaderboard honest and comparable:

  * a `baseline` arm must be present (so `vs base` is computable and within-run
    comparable — absolute token counts are not comparable across machines);
  * `meta.model` and `meta.cram_version` must be declared (you can only compare
    rows from the same model + version);
  * every provider must report `success_rate`, and any arm with passed runs must
    report `mean_eff_tokens_passed` — so nobody can win by failing the task
    cheaply.

Usage:  python scripts/validate_bench_result.py <file.json> [<file.json> ...]
Exit 0 if all valid, 1 otherwise.
"""

from __future__ import annotations

import json
import sys


def _summary(doc: dict) -> dict:
    if 'summary' in doc and 'providers' not in doc:
        return doc['summary']
    return doc


def validate(path: str) -> list[str]:
    """Return a list of problems (empty = valid)."""
    problems: list[str] = []
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        return [f'cannot read JSON: {e}']

    meta = doc.get('meta', {}) if isinstance(doc, dict) else {}
    for key in ('model', 'cram_version'):
        if not meta.get(key):
            problems.append(f'meta.{key} is required (results are only comparable '
                            f'within the same {key})')

    providers = _summary(doc).get('providers', {}) if isinstance(doc, dict) else {}
    if not providers:
        problems.append('no providers in summary')
        return problems
    if 'baseline' not in providers:
        problems.append("a 'baseline' arm is required (vs-base is the comparable metric)")

    for name, s in providers.items():
        if s.get('success_rate') is None and s.get('ran'):
            problems.append(f'{name}: missing success_rate')
        if s.get('passed') and s.get('mean_eff_tokens_passed') is None:
            problems.append(f'{name}: has passed runs but no mean_eff_tokens_passed')
    return problems


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print('usage: validate_bench_result.py <file.json> ...', file=sys.stderr)
        return 2
    ok = True
    for path in args:
        problems = validate(path)
        if problems:
            ok = False
            print(f'✗ {path}')
            for p in problems:
                print(f'    - {p}')
        else:
            print(f'✓ {path}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
