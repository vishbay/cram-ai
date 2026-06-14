"""A tiny CLI. It prints a result but has no --verbose flag yet.

The task is to add a --verbose flag that also emits a debug line. The behavior
is pinned by test_app.py (the spec / oracle) — do not edit the tests.
"""

import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # TODO: support a --verbose flag that prints a "debug:" line in addition
    # to the result line. Without it, only the result line is printed.
    print("result: 42")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
