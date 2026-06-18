# Task: fix the ledger balance

`pytest -q` fails. This package splits a small ledger across `ledger/store.py`
(data), `ledger/commands.py` (operations), and `ledger/cli.py` (routing). The
balance is computed wrong: debits and credits should net against each other, but
the reported balance treats every entry as positive. Find and fix the bug so all
tests pass. Do not edit the tests.
