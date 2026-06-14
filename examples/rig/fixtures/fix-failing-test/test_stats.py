"""Spec for stats.py — this is the oracle. Do not edit these tests; make them
pass by fixing the source."""

from stats import mean, median


def test_mean():
    assert mean([2, 4, 6]) == 4


def test_median_odd():
    assert median([3, 1, 2]) == 2


def test_median_even():
    # The two middle values of [1, 2, 3, 4] are 2 and 3 → median 2.5.
    assert median([1, 2, 3, 4]) == 2.5
    assert median([10, 20]) == 15
