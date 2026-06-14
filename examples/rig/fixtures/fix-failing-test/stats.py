"""Small stats helpers. One function has a bug the test suite catches."""


def mean(values):
    return sum(values) / len(values)


def median(values):
    """Median of a list of numbers.

    BUG: the even-length branch returns the upper-middle element instead of
    averaging the two middle elements. test_stats.py::test_median_even fails
    until this is fixed.
    """
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return s[mid]
