# add-cli-flag

`app.py` is a CLI that prints `result: 42`. It has no `--verbose` flag.

**Goal:** add a `--verbose` flag so that, when passed, the program *also* prints
a line containing `debug` (any debug detail), while still printing the result.
Without `--verbose`, no debug line is printed. Make `pytest -q` pass by editing
`app.py`. Do not edit the tests.

This is the oracle for the `add-cli-flag` corpus task. The fixture ships
**red** (`test_verbose_emits_debug` fails); a correct implementation makes it
**green**.
