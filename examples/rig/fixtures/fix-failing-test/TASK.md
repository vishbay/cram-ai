# fix-failing-test

`test_stats.py::test_median_even` fails. The bug is in `stats.py` — the
even-length branch of `median()` returns the upper-middle element instead of
averaging the two middle elements.

**Goal:** make `pytest -q` pass by fixing `stats.py`. Do not edit the tests.

This is the oracle for the `fix-failing-test` corpus task. The fixture ships
**red** (one failing test); a correct fix makes it **green**.
