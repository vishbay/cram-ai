import math


class Circle:
    def __init__(self, radius):
        self.radius = radius

    def area(self):
        # BUG: this is the circumference (2*pi*r), not the area (pi*r**2).
        return 2 * math.pi * self.radius
