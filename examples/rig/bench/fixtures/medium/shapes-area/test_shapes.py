import math

import pytest

from shapes import Circle, Rectangle


def test_rectangle_area():
    assert Rectangle(3, 4).area() == 12


def test_circle_area():
    assert Circle(2).area() == pytest.approx(math.pi * 4)


def test_circle_unit():
    assert Circle(1).area() == pytest.approx(math.pi)
